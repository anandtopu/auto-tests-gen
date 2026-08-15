"""A queued run that will produce nothing must say so, and the warning must
reach a human.

TWO DEFECTS, and the second is what made the first worth fixing.

(1) MEASURED: queueing a PR run for an app repo NOTHING covers produced a
queue item byte-identical in shape to one for a fully covered repo. The run
resolves no test repo, generates nothing, and the operator sees `status:
queued` and then silence -- the runner is a background subprocess whose
console nobody reads. Intake already validates the repo and the PR number on
the stated grounds that it is better to fail "at INTAKE, not minutes later in
a background runner nobody watches", and it already WARNED about a
probabilistic thing (this key's spend history vs its envelope) while saying
nothing about one knowable with certainty at the moment of typing.

(2) THE WARNING FIELD WAS RENDERED BY NOTHING. `_envelope_warning` has always
set `item["warning"]`, `load()` preserves it and `GET /api/queue` serves it --
and the queue row template in bin/dashboard.py emits id, status, mode, key,
release, attrs, requested_by and actions, never the warning. So the whole
feature stopped at the last step. Same shape as the alert rule whose
`recipients` the backend honoured while the UI row had no field for them, and
the reason a coverage warning was worth adding only together with this fix.

The wording respects what is actually certain (C13): a contract change fans
out to `consumed_by` consumers, so a PR touching this repo's contract can
still generate into a COVERED consumer's test repo. Where that is possible it
is named; where no consumer is covered either, the sentence is unqualified.
"""
import importlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

LONELY = "zz-lonely-api"        # nothing covers it, no consumer covered either
FANOUT = "zz-fanout-api"        # nothing covers it, but a covered consumer exists
COVERED = "orders-api"          # the control


@pytest.fixture(scope="module")
def wq():
    """work_queue bound to an isolated registry + queue file."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="queue-warn-"))
    reg = tmp / "repo-registry.yaml"
    shutil.copy(ROOT / "registry/repo-registry.yaml", reg)
    d = yaml.safe_load(reg.read_text(encoding="utf-8"))
    d["source_repositories"] += [
        {"name": LONELY, "type": "backend", "scm": "bitbucket",
         "url": "PROJ/" + LONELY, "domains": ["billing"],
         "testable_paths": ["src/**"], "contract": "openapi/lonely.yaml"},
        {"name": FANOUT, "type": "backend", "scm": "bitbucket",
         "url": "PROJ/" + FANOUT, "domains": ["catalog"],
         "testable_paths": ["src/**"], "contract": "openapi/fan.yaml",
         "consumed_by": ["web-storefront-ui"]},
    ]
    reg.write_text(yaml.safe_dump(d, sort_keys=False), encoding="utf-8")

    # SET the knobs, never clear them: a cleared knob sends the store back to
    # the live estate, which is how an earlier test deleted real approvals.
    old = {k: os.environ.get(k) for k in ("AIQE_REGISTRY_FILE", "AIQE_QUEUE_FILE")}
    os.environ["AIQE_REGISTRY_FILE"] = str(reg)
    os.environ["AIQE_QUEUE_FILE"] = str(tmp / "queue.json")
    import registry
    import work_queue
    importlib.reload(registry)
    importlib.reload(work_queue)
    try:
        yield work_queue
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        importlib.reload(registry)
        importlib.reload(work_queue)
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------- the warning

def test_a_covered_repo_queues_silently(wq):
    """The over-fix guard, and the reason the rest is worth anything: a warning
    that fires on a healthy queue is one operators learn to scroll past."""
    assert wq._coverage_warning("pr", COVERED, pr="201") == ""


def test_an_uncovered_repo_says_the_run_will_generate_nothing(wq):
    w = wq._coverage_warning("pr", LONELY, pr="7")
    assert "generate nothing" in w, w
    assert LONELY in w
    # The fix that would actually work, not just the diagnosis.
    assert "scope" in w and "bin/repos.py" in w, w


def test_a_possible_fanout_is_named_rather_than_glossed(wq):
    """What is certain differs between the two, so the sentences differ."""
    w = wq._coverage_warning("pr", FANOUT, pr="7")
    assert "unless the PR changes its contract" in w, w
    assert "web-storefront-ui" in w, w
    assert "will generate nothing" not in w, \
        "a repo whose contract change can still fan out is not a certainty"


def test_the_two_uncovered_situations_do_not_read_alike(wq):
    assert wq._coverage_warning("pr", LONELY, pr="7") != \
        wq._coverage_warning("pr", FANOUT, pr="7")


def test_a_ticket_key_is_never_treated_as_an_app_repo(wq, monkeypatch):
    """jira/plan targets are ticket keys, not repos.

    Asserting only on the empty result proved nothing and a mutation showed
    it: with the mode guard removed, `PROJ-301` matches no source repo and the
    `src is None` branch returns "" anyway. The property that guard actually
    buys is that a ticket intake does not consult the registry AT ALL -- which
    is both the work it avoids on every jira submission and what would stop a
    ticket key colliding with a repo slug from drawing a nonsense warning. So
    pin the observable thing rather than a result two branches can produce.
    """
    import registry
    calls = []
    monkeypatch.setattr(registry, "load_registry",
                        lambda *a, **k: calls.append(1))
    assert wq._coverage_warning("jira", "PROJ-301") == ""
    assert wq._coverage_warning("tests", "PROJ-301") == ""
    assert wq._coverage_warning("plan", "PROJ-301") == ""
    assert not calls, "a ticket intake asked the registry about a ticket key"


def test_an_unreadable_registry_warns_nothing_rather_than_guessing(wq,
                                                                  monkeypatch):
    import registry
    monkeypatch.setattr(registry, "load_registry",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    assert wq._coverage_warning("pr", LONELY, pr="7") == ""


def test_both_warnings_survive_together(wq, monkeypatch):
    """They are about different things -- this will cost a lot, this will
    produce nothing -- so neither may swallow the other."""
    monkeypatch.setattr(wq, "_envelope_warning",
                        lambda *a, **k: "SPEND-SENTINEL")
    item, _ = wq.add("pr", LONELY, pr="11")
    assert "SPEND-SENTINEL" in item["warning"]
    assert "generate nothing" in item["warning"]


def test_the_queued_item_carries_the_warning(wq):
    item, _ = wq.add("pr", LONELY, pr="12")
    assert "generate nothing" in item.get("warning", "")
    plain, _ = wq.add("pr", COVERED, pr="203")
    assert "warning" not in plain, \
        "a healthy item gained a warning key that renderers will show"


# ------------------------------------------------------- it reaches a human

_TEMPLATE = re.compile(r"body\.innerHTML = q\.map\(i => \{(.*?)\n    \}\)\.join\(''\);",
                       re.S)

_HARNESS = """
const escHtml = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
  .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const keyOf = i => i.mode === 'pr' ? 'PR-' + i.target + '-' + i.pr : i.target;
const chipMap = { queued: ['queued','info'] };
const TICKET_SEARCH_ENABLED = false;
function row(i) {
%s
}
console.log(JSON.stringify(row(JSON.parse(process.argv[2]))));
"""


def _render_row(item, tmp):
    """Run the REAL queue-row template from bin/dashboard.py against one item.

    The queue table is built client-side, so a source-text assertion is all
    this repo could previously make about it -- and a source-text assertion
    cannot tell a field that is read from one that is read and thrown away.
    """
    src = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    m = _TEMPLATE.search(src)
    assert m, "the queue row template moved; this pin is measuring nothing"
    f = tmp / "row.js"
    f.write_text(_HARNESS % m.group(1), encoding="utf-8")
    r = subprocess.run(["node", str(f), json.dumps(item)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr[-1500:]
    return json.loads(r.stdout)


_ITEM = {"id": "q1", "mode": "pr", "target": LONELY, "pr": "7",
         "status": "queued", "release": "", "requested_by": ""}


def test_the_queue_row_renders_the_warning(tmp_path):
    """THE SECOND DEFECT. The store kept it and the API served it; the row
    template dropped it, so no human ever saw one."""
    html = _render_row(dict(_ITEM, warning="nothing covers this repo"), tmp_path)
    assert "nothing covers this repo" in html, \
        "the queue row still drops item.warning"


def test_a_row_without_a_warning_gains_no_empty_box(tmp_path):
    html = _render_row(_ITEM, tmp_path)
    assert "warning-fg" not in html


def test_the_warning_is_escaped_into_the_row(tmp_path):
    """It carries repo names out of the registry, which is operator input."""
    html = _render_row(dict(_ITEM, warning="<script>x</script>"), tmp_path)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_the_generated_dashboard_script_still_parses(tmp_path):
    """A syntax error in that template does not break one row, it breaks every
    view on the page."""
    out = tmp_path / "dash.html"
    r = subprocess.run([sys.executable, str(ROOT / "bin/dashboard.py")],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL,
                       env=dict(os.environ, AIQE_DASHBOARD_OUT=str(out)))
    assert r.returncode == 0, r.stderr[-1500:]
    assert out.exists(), "AIQE_DASHBOARD_OUT was ignored — this test would " \
                         "otherwise overwrite the operator's dashboard"
    blocks = re.findall(r"<script[^>]*>(.*?)</script>",
                        out.read_text(encoding="utf-8"), re.S)
    assert blocks, "no script block found — the pin is measuring nothing"
    js = tmp_path / "dash.js"
    js.write_text("\n;\n".join(blocks), encoding="utf-8")
    chk = subprocess.run(["node", "--check", str(js)], capture_output=True,
                         text=True, encoding="utf-8", errors="replace",
                         stdin=subprocess.DEVNULL)
    assert chk.returncode == 0, chk.stderr[-1500:]
