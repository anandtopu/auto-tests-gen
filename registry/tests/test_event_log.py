"""Pins for the transaction log (observability slice 1).

Three of these are the reason the feature is safe to ship rather than a new
liability: it must never break a caller, never record a secret, and never make
the whole history unreadable because of one bad line.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine" / "lib"))
import event_log as el  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AIQE_EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setattr(el, "_degraded_reported", False)
    monkeypatch.setattr(el, "_dropped", 0)


def test_an_event_round_trips_with_the_documented_shape():
    eid = el.emit("plan.approved", actor="anand", source="ui",
                  target="PROJ-301", run_id="r1", detail={"scenarios": 7})
    assert eid and eid.startswith("evt_")
    rows, corrupt = el.read()
    assert corrupt == 0 and len(rows) == 1
    r = rows[0]
    for field in ("id", "ts", "kind", "actor", "actor_source", "source",
                  "target", "run_id", "outcome", "detail"):
        assert field in r, f"{field} missing from the record"
    assert r["kind"] == "plan.approved" and r["target"] == "PROJ-301"


def test_ids_sort_in_time_order():
    """The UI shows newest-first and slice 2 pages on id; ids that do not sort
    by time would silently reorder history.

    The guarantee is PER-PROCESS monotonicity: a millisecond prefix plus a
    monotonic sequence. Ordering between concurrent processes inside the same
    millisecond is arbitrary and cannot be otherwise without coordination, so
    consumers order by (ts, id)."""
    ids = [el.emit("run.started", target=f"t{i}") for i in range(25)]
    assert ids == sorted(ids)


# --------------------------------------------------------------- redaction
def test_secret_values_never_reach_the_log():
    """The Settings UI writes .env. The event records WHICH keys changed, never
    their values — a transaction log that leaks credentials is worse than none."""
    el.emit("settings.changed", source="ui",
            detail={"AIQE_UI_TOKEN": "s3cr3t-value", "ANTHROPIC_API_KEY": "sk-abc",
                    "SMTP_PASSWORD": "hunter2", "changed_keys": 3})
    body = (pathlib.Path(os.environ["AIQE_EVENTS_DIR"])).glob("*.jsonl")
    text = "\n".join(p.read_text(encoding="utf-8") for p in body)
    for leaked in ("s3cr3t-value", "sk-abc", "hunter2"):
        assert leaked not in text, f"{leaked!r} reached the transaction log"
    assert "AIQE_UI_TOKEN" in text, "the KEY NAME must survive — that is the audit value"


def test_redaction_reaches_one_level_of_nesting():
    out = el.redact({"outer": {"api_key": "abc", "count": 2}})
    assert out["outer"]["api_key"] == "<redacted>" and out["outer"]["count"] == 2


def test_a_long_value_is_clipped_even_when_its_key_looks_innocent():
    """The denylist cannot know the next secret's name, so length is the
    backstop — and it also stops a ticket body being pasted into the log."""
    out = el.redact({"description": "A" * 5000})
    assert len(out["description"]) <= el.MAX_VALUE_CHARS + 1


def test_detail_key_count_is_bounded():
    out = el.redact({f"k{i}": i for i in range(200)})
    assert len(out) <= el.MAX_DETAIL_KEYS + 1
    assert out["_truncated_keys"] > 0, "truncation must be visible, not silent"


# ------------------------------------------------------- never break a caller
def test_an_unwritable_log_does_not_raise(monkeypatch, tmp_path):
    """A run that cost real money must not fail because a log line could not be
    written. Emission returns None and the caller carries on."""
    blocked = tmp_path / "afile"
    blocked.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("AIQE_EVENTS_DIR", str(blocked / "nested"))
    assert el.emit("run.started", target="x") is None
    assert el.health()["degraded"] is True
    assert el.health()["dropped"] == 1


def test_degradation_is_reported_once_not_per_event(monkeypatch, tmp_path, capsys):
    """A failing log that narrates its failure on every call turns one problem
    into an outage of its own."""
    blocked = tmp_path / "afile2"
    blocked.write_text("x", encoding="utf-8")
    monkeypatch.setenv("AIQE_EVENTS_DIR", str(blocked / "nested"))
    for _ in range(20):
        el.emit("run.started", target="x")
    err = capsys.readouterr().err
    assert err.count("DEGRADED") == 1, "the warning must not repeat per event"
    assert el.health()["dropped"] == 20, "but every drop is still counted"


def test_a_corrupt_line_is_skipped_not_raised():
    """One bad write must not make the entire history unreadable."""
    el.emit("run.started", target="good-1")
    d = pathlib.Path(os.environ["AIQE_EVENTS_DIR"])
    f = next(d.glob("*.jsonl"))
    with open(f, "a", encoding="utf-8") as fh:
        fh.write("{not json at all\n")
    el.emit("run.completed", target="good-2")
    rows, corrupt = el.read()
    assert corrupt == 1
    assert {r["target"] for r in rows} == {"good-1", "good-2"}


# ------------------------------------------------------------------ filtering
def test_filters_narrow_the_stream():
    el.emit("gate.committed", actor="a", target="repo-1")
    el.emit("gate.refused", actor="b", target="repo-2", outcome="refused")
    el.emit("plan.approved", actor="a", target="PROJ-1")
    assert len(el.read(kinds=["gate.committed", "gate.refused"])[0]) == 2
    assert len(el.read(actor="a")[0]) == 2
    assert len(el.read(outcome="refused")[0]) == 1
    assert len(el.read(target="repo-2")[0]) == 1


def test_limit_is_honoured():
    for i in range(30):
        el.emit("run.started", target=f"t{i}")
    assert len(el.read(limit=5)[0]) == 5


# ------------------------------------------------------------------ retention
def test_prune_drops_old_day_files_and_records_itself():
    d = pathlib.Path(os.environ["AIQE_EVENTS_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "2020-01-01.jsonl").write_text('{"kind":"run.started"}\n', encoding="utf-8")
    el.emit("run.started", target="today")
    removed = el.prune(30)
    assert len(removed) == 1 and "2020-01-01" in removed[0]
    assert not (d / "2020-01-01.jsonl").exists()
    kinds = [r["kind"] for r in el.read()[0]]
    assert "log.pruned" in kinds, "the prune must be auditable too"


def test_prune_of_zero_days_is_a_no_op():
    """A misconfigured retention of 0 must not delete the whole history."""
    el.emit("run.started", target="keep-me")
    assert el.prune(0) == []
    assert len(el.read()[0]) == 1


# ------------------------------------------------------------- vocabulary
def test_every_kind_used_in_the_codebase_is_declared():
    """The vocabulary is closed by TEST, not at runtime: emit() writes an
    unknown kind rather than dropping it, because losing an event to a typo is
    the one thing a transaction log may not do. This is what keeps it closed."""
    used = set()
    for p in list((ROOT / "engine").rglob("*.py")) + list((ROOT / "bin").rglob("*.py")):
        if p.name == "event_log.py":
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if "emit(" not in line:
                continue
            for quote in ('"', "'"):
                if f'emit({quote}' in line:
                    kind = line.split(f"emit({quote}", 1)[1].split(quote, 1)[0]
                    if "." in kind:
                        used.add(kind)
    undeclared = sorted(used - el.KINDS)
    assert not undeclared, f"kinds emitted but not in event_log.KINDS: {undeclared}"


def test_cli_lists_events():
    el.emit("run.completed", target="PROJ-9")
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/event_log.py"), "10"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       env={**os.environ})
    assert r.returncode == 0, r.stderr
    assert "run.completed" in r.stdout and "PROJ-9" in r.stdout


# ------------------------------------------- server wiring (observability 1.1/2.x)
def _server_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ds_under_test", ROOT / "bin" / "dashboard_server.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_status_classification_never_calls_a_rejection_ok():
    """The first live run recorded a 400 as `ok`, which would make "nothing is
    failing" true of a log full of rejected requests."""
    m = _server_module()
    assert m._classify_status(200) == ("request.received", "ok")
    assert m._classify_status(302) == ("request.received", "ok")
    for refused in (400, 401, 403, 404, 409, 422):
        assert m._classify_status(refused)[1] == "refused", refused
    for failed in (500, 503):
        assert m._classify_status(failed)[1] == "failed", failed
    assert m._classify_status(None)[1] == "failed", "no response is a failure, not unknown"


def test_csv_export_defuses_spreadsheet_formulas():
    """`actor` arrives from an SSO header we do not control. A cell starting
    with = + - @ executes on open in Excel and Sheets, so an audit export
    would attack the person doing the audit."""
    m = _server_module()
    assert m._csv_cell("=cmd|' /c calc'!A1").startswith("'=")
    assert m._csv_cell("+1").startswith("'+")
    assert m._csv_cell("@SUM(A1)").startswith("'@")
    assert m._csv_cell("a,b") == '"a,b"'
    assert m._csv_cell('say "hi"') == '"say ""hi"""'
    assert m._csv_cell(None) == ""


def test_post_is_wrapped_once_rather_than_edited_per_endpoint():
    """34 mutating endpoints share one wrapper. Per-branch emission would
    guarantee the next endpoint added is the one that goes unrecorded."""
    src = (ROOT / "bin" / "dashboard_server.py").read_text(encoding="utf-8")
    assert "def _handle_post(self):" in src, "the real handler must be wrapped"
    # Exactly one REQUEST-level emission. Endpoints may still emit their own
    # DOMAIN events (`/api/alerts/save` records `settings.changed`) — that is
    # the point of the vocabulary. What must never drift is per-endpoint
    # request logging, because then a new endpoint silently goes unrecorded.
    wrapper = src.split("def do_POST(self):", 1)[1].split("def _handle_post", 1)[0]
    assert wrapper.count("event_log.emit(") == 1, \
        "the wrapper must emit exactly once per request"
    handler = src.split("def _handle_post(self):", 1)[1]
    assert "_classify_status" not in handler, \
        "request classification belongs to the wrapper, not to any endpoint"
    for kind in ('"request.received"', '"request.refused"', '"request.failed"'):
        assert kind not in handler, \
            f"{kind} emitted inside an endpoint — request logging must stay in the wrapper"
    # GETs must not be logged: browsing is not a transaction.
    get_body = src.split("def do_GET(self):", 1)[1].split("def do_POST", 1)[0]
    assert "event_log.emit" not in get_body, "a GET must not write an event"


def test_the_request_body_is_never_stored():
    """The Settings endpoint receives .env values in its body."""
    src = (ROOT / "bin" / "dashboard_server.py").read_text(encoding="utf-8")
    wrapper = src.split("def do_POST(self):", 1)[1].split("def _handle_post", 1)[0]
    assert "body" not in wrapper, "the wrapper must not touch the request body"


# --------------------------------------------------- surfacing (E5, 5.1/5.2)
def test_cli_events_and_alerts_are_registered():
    """CLI parity matters because the people most likely to need the audit
    trail — mid-incident, over SSH — are the least likely to have a browser
    pointed at the dashboard."""
    src = (ROOT / "bin" / "qa.py").read_text(encoding="utf-8")
    for cmd in ('sub.add_parser("events")', 'sub.add_parser("alerts")'):
        assert cmd in src, f"{cmd} missing from qa.py"
    assert "def cmd_events" in src and "def cmd_alerts" in src


def test_listing_alerts_from_the_cli_never_notifies():
    """A read-only report that pages people would be its own outage."""
    src = (ROOT / "bin" / "qa.py").read_text(encoding="utf-8")
    body = src.split("def cmd_alerts", 1)[1].split("\ndef ", 1)[0]
    assert "notify=False" in body


def test_overview_tiles_only_exist_when_there_is_something_to_say():
    """A permanent '0 alerts' tile is furniture people stop reading, and this
    epic is about signals that still mean something when they appear."""
    src = (ROOT / "bin" / "dashboard.py").read_text(encoding="utf-8")
    block = src.split("# Observability 5.1", 1)[1].split("tiles_html =", 1)[0]
    assert "if _firing:" in block, "the firing tile must be conditional"
    assert "if _unevaluable:" in block, "unevaluable needs its OWN tile — it is not healthy"
    assert "notify=False" in block, "rendering the Overview must never notify"


def test_the_docs_describe_what_is_not_recorded():
    """The reassurance a reader actually needs: GETs, bodies and secret values
    stay out of the log."""
    uc = (ROOT / "docs" / "use-cases.md").read_text(encoding="utf-8")
    assert "deliberately NOT recorded" in uc
    for claim in ("GET request", "request bodies", "secret value"):
        assert claim.split()[0].lower() in uc.lower(), claim


# ------------------------------------------------- SDD adoption S1: workflow
def test_the_workflow_view_never_mutates():
    """Rendering a workflow must not advance it. Every transition stays behind
    the approve/edit commands, which sign and record an actor."""
    src = (ROOT / "engine" / "lib" / "spec_workflow.py").read_text(encoding="utf-8")
    for mutator in ("set_status(", "save_plan(", "approve(", "write_", ".unlink(",
                    "mkdir(", "os.replace"):
        assert mutator not in src, f"spec_workflow calls {mutator} — it must be read-only"


def test_every_row_reports_the_governance_that_produced_it():
    """'Blocked' means different things under different configuration. A view
    that hides which one it is teaches a rule the platform is not enforcing."""
    sys.path.insert(0, str(ROOT / "engine" / "lib"))
    import spec_workflow
    b = spec_workflow.board()
    assert set(b) >= {"states", "governance", "rows", "summary", "enforced"}
    g = b["governance"]
    assert "requirements_gate_effect" in g and "spec_enforce_effect" in g, \
        "the EFFECT must be stated, not just the flag"
    for r in b["rows"]:
        assert "governance" in r and "advisory" in r


def test_an_unenforced_step_is_labelled_advisory(monkeypatch):
    """With the gate off, 'requirements not approved' does not actually stop
    anything — saying otherwise would be a lie the UI repeats."""
    sys.path.insert(0, str(ROOT / "engine" / "lib"))
    import spec_workflow
    monkeypatch.setattr(spec_workflow, "governance",
                        lambda: {"requirements_gate": False,
                                 "requirements_gate_effect": "advisory",
                                 "spec_enforce": "off",
                                 "spec_enforce_effect": "ignored",
                                 "spec_mode": True})
    rows = [r for r in spec_workflow.board()["rows"]
            if r["state"] == "requirements" and "not approved" in r["blocker"]]
    for r in rows:
        assert r["advisory"] is True, "an unenforced step must say so"
