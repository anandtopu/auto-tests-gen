"""A spend statement that drops line items must never look complete.

`/api/cost-statement` returned EVERY row a key has ever accumulated -- one per
phase per run, with no upper bound. Measured against this estate, PROJ-301
carries 1800 rows / 808 KB for a SINGLE ticket, and nothing reads them:
`bin/dashboard.py` renders `totals` only, and the row-level surfaces are the md
and csv exports the same endpoint serves. So every request shipped close to a
megabyte no consumer displays, growing with run history forever.

Bounding it is only safe under one condition, which is what these pins are
about: the short list must be unreadable as the whole list. A spend record that
silently omits line items under-reports what a task cost, and the reader has no
way to tell -- the C13 shape this repo keeps finding. So the view carries the
TRUE counts, says `truncated`, and the endpoint names the URL that undoes it;
the md/csv exports stay complete, because a downloaded audit is exactly where a
missing row does the most damage.

Found by measuring, not by reading: a host-level quirk truncated a large HTTP
response mid-body and the adversarial API suite started failing there. The
truncation itself is NOT ours (a stdlib http.server serving a same-sized body
dies at the same offset on this machine) and is not what these pins defend --
they defend the payload being unbounded in the first place.
"""
import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import cost_statement as cs                                    # noqa: E402


def _server_source():
    return (ROOT / "bin/dashboard_server.py").read_text(encoding="utf-8")


def _code(text):
    """Source with comments stripped. The first version of the export pin
    matched the word `bounded` inside the COMMENT explaining why the export
    must not be bounded -- a pin reading its author's prose instead of the
    code, which this repo has now caught itself doing four times."""
    return "\n".join(line.split("#")[0] for line in text.splitlines())


def _route():
    src = _code(_server_source())
    return src.split('elif url.path == "/api/cost-statement"')[1].split("elif url.path")[0]


def _doc(user=5, non_user=3):
    def row(i, attribution):
        return {"run_id": f"r{i}", "mode": "jira", "phase": "analyze",
                "provider": "mock", "model": "m", "basis": "simulated",
                "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0,
                "cache_read_tokens": 0, "cache_creation_tokens": 0,
                "turns": 1, "attempts": 1, "attribution": attribution,
                "ts": i, "key": "K-1"}
    return {"schema": 1, "key": "K-1",
            "rows": [row(i, "user") for i in range(user)],
            "totals": {"reported_usd": 1.5},
            "non_user_rows": [row(i, "probe") for i in range(non_user)],
            "non_user_totals": {"reported_usd": 0.25}}


# --------------------------------------------------------------- the library

def test_a_truncated_statement_says_it_is_truncated():
    view = cs.bounded(_doc(user=50), limit=10)
    assert len(view["rows"]) == 10
    assert view["truncated"] is True, \
        "a short spend list that does not say so reads as the whole record"
    assert view["rows_total"] == 50


def test_the_true_count_survives_truncation():
    """The count is the only thing telling a reader how much is missing."""
    view = cs.bounded(_doc(user=50, non_user=40), limit=10)
    assert view["rows_total"] == 50 and view["non_user_rows_total"] == 40


def test_non_user_rows_are_bounded_too():
    """Probe/embedding spend accumulates on the same key and is a list of the
    same unbounded shape -- bounding only one half leaves the payload
    unbounded."""
    view = cs.bounded(_doc(user=2, non_user=50), limit=10)
    assert len(view["non_user_rows"]) == 10 and view["truncated"] is True


def test_bounding_is_inert_below_the_limit():
    """A statement that fits must come back untouched and NOT flagged, or
    every small statement grows a warning nobody needs and readers learn to
    ignore the flag that matters."""
    doc = _doc(user=5, non_user=3)
    view = cs.bounded(doc, limit=200)
    assert view["rows"] == doc["rows"] and view["non_user_rows"] == doc["non_user_rows"]
    assert view["truncated"] is False
    assert view["rows_total"] == 5 and view["non_user_rows_total"] == 3


def test_limit_none_returns_everything():
    view = cs.bounded(_doc(user=50), limit=None)
    assert len(view["rows"]) == 50 and view["truncated"] is False


def test_totals_are_never_recomputed_from_the_short_list():
    """The whole point: `totals` is the spend answer, and it is computed over
    every row by `statement()`. If bounding recomputed or dropped it, the
    endpoint would report a fraction of a bill as the bill."""
    doc = _doc(user=50)
    view = cs.bounded(doc, limit=1)
    assert view["totals"] == doc["totals"]
    assert view["non_user_totals"] == doc["non_user_totals"]


def test_bounding_does_not_mutate_the_statement_it_was_given():
    """The dashboard and the exports read the same doc; a bounding call that
    edited it in place would truncate the csv download as a side effect."""
    doc = _doc(user=50)
    cs.bounded(doc, limit=2)
    assert len(doc["rows"]) == 50


# ---------------------------------------------------------------- the server

def test_the_endpoint_bounds_the_json_view():
    route = _route()
    assert "cost_statement.bounded(" in route, \
        "the JSON view is unbounded again"


def test_the_exports_are_rendered_from_the_complete_document():
    """A downloaded statement missing line items is a spend audit that
    under-reports -- the format branch must never see the bounded view."""
    route = _route()
    fmt_branch = route.split("if fmt:")[1].split("else:")[0]
    assert "cost_statement.render(doc" in fmt_branch
    assert "bounded" not in fmt_branch, \
        "the md/csv export was rendered from a truncated document"


def test_the_truncation_names_the_way_to_get_everything():
    route = _route()
    assert "complete_via" in route and "rows=all" in route, \
        "a reader told the record is incomplete needs to be told how to get it"


def test_an_unparseable_rows_value_is_refused_not_defaulted():
    """Silently ignoring an option a caller passed is how a bounded answer
    reads as the complete one."""
    route = _route()
    assert "rows must be" in route and "self._send(400" in route


def test_the_usage_banner_documents_the_bound():
    src = _server_source()
    banner = src.split("GET  /api/cost-statement")[1].split("\n")[0] + \
        src.split("GET  /api/cost-statement")[1].split("\n")[1]
    assert "rows=all" in banner and "bounded" in banner.lower()


# ------------------------------------------------------- driving the endpoint
#
# Every assertion above this line reads SOURCE TEXT, and source text cannot see
# a route that parses `rows` and then ignores it -- the defect class this repo
# has hit twice (the selection CLI dropped every flag it documented). These
# three boot the real server and ask it.

@pytest.fixture(scope="module")
def server(tmp_path_factory):
    import os, shutil, socket, subprocess, time
    d = tmp_path_factory.mktemp("cost-bounded")
    (d / "runs").mkdir()
    registry = d / "registry.yaml"
    shutil.copy2(ROOT / "registry/repo-registry.yaml", registry)
    sk = socket.socket(); sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]; sk.close()
    token = "bounded-probe-token"
    env = dict(os.environ, AIQE_MOCK="1", AIQE_UI_PORT=str(port),
               AIQE_UI_TOKEN=token, AIQE_ENV_FILE=str(d / ".env"),
               AIQE_REGISTRY_FILE=str(registry),
               AIQE_PLAN_DIR=str(d / "plans"),
               AIQE_REVIEWS_FILE=str(d / "runs/reviews.json"),
               AIQE_QUEUE_FILE=str(d / "runs/queue.json"),
               AIQE_OPENHANDS_DIR=str(d / "openhands"))
    log = open(d / "server.log", "w", encoding="utf-8")   # noqa: SIM115
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "bin/dashboard_server.py")], cwd=ROOT,
        env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(200):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            break
        except OSError:
            time.sleep(0.25)
    yield base, token
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()


def _get(server, query):
    import urllib.error, urllib.request
    base, token = server
    req = urllib.request.Request(f"{base}/api/cost-statement?{query}",
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def test_the_served_json_carries_the_bound(server):
    status, body = _get(server, "key=PROJ-301")
    assert status == 200, body
    doc = json.loads(body)
    assert "truncated" in doc and "rows_total" in doc
    assert len(doc["rows"]) <= cs.DEFAULT_ROW_LIMIT
    if doc["truncated"]:
        assert doc["rows_total"] > len(doc["rows"])
        assert "rows=all" in doc.get("complete_via", "")


def test_an_explicit_row_count_reaches_the_bound(server):
    """The option must ACTUALLY reach the bound, not merely parse. 250 is
    deliberately not the default, so a route that parsed `rows` and then
    passed DEFAULT_ROW_LIMIT anyway fails here.

    `rows=all` is not fetched over HTTP: the full statement for either key in
    this estate is 0.8-1.5 MB, and this host truncates any HTTP response past
    ~765 KiB mid-body (reproduced with a stdlib http.server serving a
    same-sized body, so it is the machine, not this server). Asserting it here
    would pin a host quirk instead of the product. The `all` branch is one
    line, covered by the library pins above and by the route text."""
    doc = json.loads(_get(server, "key=PROJ-301&rows=250")[1])
    assert len(doc["rows"]) == min(250, doc["rows_total"])
    assert len(doc["rows"]) != cs.DEFAULT_ROW_LIMIT, \
        "the route ignored the count it was given and used the default"
    assert doc["truncated"] is (doc["rows_total"] > 250)


def test_the_route_maps_all_to_an_unbounded_view():
    route = _route()
    assert 'rows_q == "all"' in route and "limit = None" in route


def test_a_bad_rows_value_is_refused_by_the_server(server):
    status, body = _get(server, "key=PROJ-301&rows=0")
    assert status == 400 and "rows must be" in body
    status, body = _get(server, "key=PROJ-301&rows=lots")
    assert status == 400, body
