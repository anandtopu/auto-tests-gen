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


# ------------------------------------------- SDD adoption S2: requirements UI
def test_approval_is_refused_over_a_blocking_ambiguity():
    """The whole point of the state is that the ticket does not yet say what
    should happen. Approving anyway launders a guess into a signed artifact."""
    src = (ROOT / "bin" / "dashboard_server.py").read_text(encoding="utf-8")
    body = src.split('self.path == "/api/requirements/status"', 1)[1].split("if self.path ==", 1)[0]
    assert "blocking" in body and "409" in body, \
        "approval must refuse while a blocking ambiguity is unanswered"


def test_staleness_compares_like_with_like():
    """The first version compared requirements_sha (sha256 of requirements.yaml)
    against spec_store.sha() — a DIFFERENT file hashed with a DIFFERENT,
    truncated function. Every approved requirement reported as stale."""
    src = (ROOT / "bin" / "dashboard_server.py").read_text(encoding="utf-8")
    body = src.split('url.path == "/api/requirements"', 1)[1].split("elif url.path", 1)[0]
    # Strip comments first. The previous version tripped on the COMMENT that
    # explains why spec_store.sha is wrong — a pin that fails on prose while the
    # code is correct teaches people to delete pins. (Second time in this
    # codebase; the app_paths pin had the same shape.)
    code = "\n".join(l for l in body.splitlines()
                     if not l.strip().startswith("#"))
    assert "requirements_path" in code and "sha256" in code, \
        "current sha must be sha256 of requirements.yaml, matching what plan_state signs"
    assert "spec_store.sha(" not in code, \
        "that hashes the testplan, not the requirements"


def test_the_requirements_state_field_is_the_flat_one():
    """plan_state stores `requirements_status` at the top level of the entry;
    there is no nested `requirements` dict and no `plans` wrapper. Reading the
    wrong shape made approval never register — it shipped in S1 and was found
    while building S2."""
    for f in ("engine/lib/spec_workflow.py", "bin/dashboard_server.py"):
        src = (ROOT / f).read_text(encoding="utf-8")
        assert 'get("requirements_status")' in src or "requirements_status" in src, f
    wf = (ROOT / "engine/lib/spec_workflow.py").read_text(encoding="utf-8")
    assert 'load().get("plans"' not in wf, "plan_state has no 'plans' wrapper"


# ---------------------------------------- SDD adoption S3: governance settings
def test_the_workflow_view_cannot_contradict_the_engine(monkeypatch):
    """The first version read only org-config, so with AIQE_SPEC_ENFORCE set the
    view reported 'off' while the gate was actually refusing commits. A workflow
    view that contradicts the enforcement it describes is worse than none."""
    sys.path.insert(0, str(ROOT / "engine" / "lib"))
    import importlib
    import spec_workflow
    monkeypatch.setenv("AIQE_SPEC_ENFORCE", "strict")
    monkeypatch.setenv("AIQE_REQUIREMENTS_GATE", "1")
    importlib.reload(spec_workflow)
    g = spec_workflow.governance()
    assert g["spec_enforce"] == "strict", "the view must see the env override"
    assert g["requirements_gate"] is True
    # ...and the ENGINE must agree, or the view is describing a fiction.
    import plan_state
    assert plan_state._requirements_gate_on() is True
    spec = importlib.util.spec_from_file_location(
        "sc_pin", ROOT / "engine" / "gate" / "spec_check.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.mode() == "strict"


def test_both_governance_knobs_are_env_overridable():
    """They were asymmetric: the gate honoured AIQE_SPEC_ENFORCE, the
    requirements gate honoured nothing. org-config.yaml ships INSIDE the image
    and cannot be written under readOnlyRootFilesystem, so a deployed estate had
    no way to turn spec governance on at all."""
    ps = (ROOT / "engine/lib/plan_state.py").read_text(encoding="utf-8")
    sc = (ROOT / "engine/gate/spec_check.py").read_text(encoding="utf-8")
    assert "AIQE_REQUIREMENTS_GATE" in ps
    assert "AIQE_SPEC_ENFORCE" in sc


def test_governance_settings_state_their_consequence():
    """Someone deciding whether to enable this needs to know what starts
    FAILING, not which YAML key moves."""
    sys.path.insert(0, str(ROOT / "engine" / "lib"))
    import settings_store as ss
    sec = [s for s in ss.SPEC if s["section"] == "Spec-driven governance"]
    assert sec, "the governance section must exist in the Settings SPEC"
    blob = (sec[0]["hint"] + " ".join(
        f.get("help", "") + " ".join(o[1] for o in f.get("options", []))
        for f in sec[0]["fields"])).lower()
    for consequence in ("refuse", "exit 65", "exit 8", "warn"):
        assert consequence in blob, f"the settings must say {consequence!r}"


# ------------------------------------------------- SDD adoption S4: waivers
def _ws():
    sys.path.insert(0, str(ROOT / "engine" / "lib"))
    import waiver_store
    return waiver_store


def test_a_waiver_needs_a_reason_an_owner_and_an_expiry():
    """A blank-reason bypass is indistinguishable from an accident six months
    later, when the person who added it has forgotten."""
    ws = _ws()
    import datetime
    ok = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    assert ws.validate("S1", "the upstream API is not on staging", "anand", ok)[1] == []
    assert ws.validate("S1", "", "anand", ok)[1], "blank reason must be refused"
    assert ws.validate("S1", "later", "anand", ok)[1], "a token reason must be refused"
    assert ws.validate("S1", "a good long reason here", "", ok)[1], "owner required"
    assert ws.validate("S1", "a good long reason here", "anand", "")[1], "expiry required"


def test_an_unbounded_expiry_is_refused():
    """`expires: 2099-01-01` passes every check the gate makes while meaning
    'never'. The cap makes that lie impossible to tell in one step."""
    ws = _ws()
    import datetime
    far = (datetime.date.today() + datetime.timedelta(days=ws.MAX_DAYS + 1)).isoformat()
    _, problems = ws.validate("S1", "a perfectly good reason", "anand", far)
    assert problems and str(ws.MAX_DAYS) in problems[0]
    past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    assert ws.validate("S1", "a perfectly good reason", "anand", past)[1]


def test_nothing_is_written_when_a_waiver_is_refused(tmp_path, monkeypatch):
    """A refused waiver that half-wrote would be worse than one that saved."""
    ws = _ws()
    monkeypatch.setenv("AIQE_SPEC_DIR", str(tmp_path / "specs"))
    import importlib, spec_store
    importlib.reload(spec_store); importlib.reload(ws)
    _, problems = ws.save("K-1", "S1", "", "anand", "2099-01-01")
    assert problems
    assert not ws.path("K-1").exists(), "a refused waiver must write nothing"


def test_expired_waivers_stay_listed(tmp_path, monkeypatch):
    """Hiding a lapsed exception makes it look like it never existed, when it is
    the most interesting row on the page."""
    ws = _ws()
    monkeypatch.setenv("AIQE_SPEC_DIR", str(tmp_path / "specs"))
    import importlib, spec_store
    importlib.reload(spec_store); importlib.reload(ws)
    p = ws.path("K-1"); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("waivers:\n- scenario: S1\n  reason: old\n  by: a\n"
                 "  expires: '2020-01-01'\n", encoding="utf-8")
    rows = ws.list_for("K-1")
    assert len(rows) == 1 and rows[0]["expired"] is True


def test_the_waiver_ui_refusal_returns_the_problems():
    """A refused waiver must say what would make it acceptable — 'invalid' is
    not a reason."""
    src = (ROOT / "bin" / "dashboard_server.py").read_text(encoding="utf-8")
    body = src.split('self.path == "/api/waivers/save"', 1)[1].split("if self.path ==", 1)[0]
    assert "422" in body and "problems" in body


# ------------------------------------- SDD adoption S6: generated governance
def _gp():
    sys.path.insert(0, str(ROOT / "engine" / "lib"))
    import governance_page
    return governance_page


def test_the_governance_page_is_generated_from_the_constitution():
    """A hand-written governance doc is correct the day it is written and
    slowly becomes fiction. This one is wrong only if the code is."""
    gp = _gp()
    d = gp.page()
    assert d["clause_count"] > 0
    assert "constitution.yaml" in d["source"]
    md = gp.markdown()
    for c in d["clauses"]:
        assert c["id"] in md, f"{c['id']} missing from the rendered page"


def test_a_clause_whose_pin_vanished_is_reported_as_undefended(monkeypatch, tmp_path):
    """A clause is only as true as the test that holds it. A deleted pin turns
    a rule into a hope, and the page must say so rather than print it as if it
    still held."""
    gp = _gp()
    fake = tmp_path / "constitution.yaml"
    fake.write_text(
        "clauses:\n"
        "- id: CX\n  statement: something important\n  category: safety\n"
        "  pins:\n  - file: tests/this-was-deleted.sh\n", encoding="utf-8")
    monkeypatch.setattr(gp, "CONSTITUTION", fake)
    cls = gp.clauses()
    assert cls[0]["pin_missing"] == ["tests/this-was-deleted.sh"]
    assert "CX" in gp.page()["unpinned"]
    assert "MISSING" in gp.markdown()


def test_the_page_leads_with_whether_anything_is_enforced(monkeypatch):
    """A governance page describing an aspiration while the gate is off is
    worse than none: it teaches a rule nobody applies."""
    gp = _gp()
    import importlib, spec_workflow
    monkeypatch.delenv("AIQE_SPEC_ENFORCE", raising=False)
    monkeypatch.delenv("AIQE_REQUIREMENTS_GATE", raising=False)
    importlib.reload(spec_workflow); importlib.reload(gp)
    md = gp.markdown()
    head = md.split("## Is any of this enforced right now?", 1)[1][:400]
    assert "**No.**" in head or "**Yes.**" in head, "the answer must be stated plainly"
    assert md.index("enforced right now") < md.index("## The rules"), \
        "enforcement status must come BEFORE the rules it qualifies"


# ------------------------------- SDD adoption S5: coverage subtraction (counts)
def _ss():
    sys.path.insert(0, str(ROOT / "engine" / "lib"))
    import spec_savings
    return spec_savings


def test_savings_refuses_to_invent_money():
    """A savings figure is exactly the kind of number people repeat in a status
    update. Pricing a skipped scenario needs a measured per-scenario cost, and
    every run on this estate is simulated. 0 would read as 'no saving'; an
    estimate would read as a measurement. Say which one it is."""
    ss = _ss()
    s = ss.savings(7)
    assert s["avoided_scenarios"] == 7, "the COUNT is measured and must be reported"
    assert s["usd"] is None, "money must not be invented"
    assert s["basis"] == "unmeasured"
    assert "parity" in s["why"], "the refusal must name what would fix it"


def test_only_scenario_linked_tests_count_as_coverage():
    """A test with no scenario_id may well cover the behaviour, but we cannot
    prove which scenario. Counting it would silently drop coverage — the one
    failure this platform cannot see."""
    ss = _ss()
    src = (ROOT / "engine/lib/spec_savings.py").read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "unlinked_tests" in code
    assert "if sid and f:" in code, "coverage requires BOTH a scenario id and a file"


def test_subtraction_is_advisory_not_automatic():
    """Skipping authoring on a wrong join would silently drop coverage. The
    join gets proven against real runs before it is allowed to remove work."""
    ss = _ss()
    plan = ss.authoring_plan("PROJ-301")
    assert plan["advisory"] is True
    # Nothing in the pipeline consumes to_author yet — assert that stays true.
    for f in ("engine/pipeline.sh", "engine/phases/run_phase.sh"):
        src = (ROOT / f).read_text(encoding="utf-8")
        assert "spec_savings" not in src, \
            f"{f} acts on coverage subtraction — it must be proven first"


def test_counts_add_up():
    ss = _ss()
    p = ss.authoring_plan("PROJ-301")
    assert p["already_covered"] + p["to_author"] == p["scenarios"]
    assert len(p["already_covered_ids"]) == p["already_covered"]


def test_savings_is_reachable_from_the_ui():
    """A library nobody can see is not a feature (S5).

    The whole point of this adoption thread is that the guidance is usable FROM
    the interface, so the endpoint and its card are pinned together — wiring one
    without the other ships a card that renders nothing, or an endpoint nobody
    calls.
    """
    srv = (ROOT / "bin/dashboard_server.py").read_text(encoding="utf-8")
    assert "/api/spec-savings" in srv
    assert "import spec_savings" in srv
    ui = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    assert "/api/spec-savings" in ui and 'id="sv-body"' in ui


def test_ui_never_prints_a_zero_for_an_unmeasured_saving():
    """The renderer must distinguish absent from zero.

    `s.usd === null` and `=== undefined` are BOTH checked in the UI: a JSON
    null arriving as undefined would fall through to the money branch and print
    "~$undefined", or worse, a coerced 0 that reads as "no saving".
    """
    ui = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    i = ui.index("loadSavings")
    block = ui[i:i + 2500]
    assert "s.usd === null" in block and "s.usd === undefined" in block
    assert "not measured" in block


# ----- journey review: the board read two facts from the wrong place ---------
def test_generated_is_read_from_the_key_plan_state_actually_writes():
    """`mark_generated` writes `generated_run`; the board read `generated`.

    Nothing ever set that key, so a ticket whose tests had been generated AND
    committed sat at "plan approved; tests not generated" forever — the board's
    single most visible claim, permanently wrong. Found by walking the journey
    end to end rather than reading the code.
    """
    sys.path.insert(0, str(ROOT / "engine" / "lib"))
    import plan_state
    import spec_workflow
    src = (ROOT / "engine/lib/spec_workflow.py").read_text(encoding="utf-8")
    assert 'entry.get("generated_run")' in src
    assert 'entry.get("generated")' not in src, \
        "reading a key mark_generated never writes"
    # And the producer still writes it — a rename on either side breaks this.
    import inspect
    assert 'e["generated_run"]' in inspect.getsource(plan_state.mark_generated)
    assert spec_workflow.STATES.index("tests") < spec_workflow.STATES.index("committed")


def test_committed_means_the_gate_committed_not_that_a_pdf_was_attached():
    """`linked` means "the plan is attached to the ticket". Reading it as a
    commit made attaching a PDF advance the board to done, while a genuinely
    committed run with no attachment reported "no gate commit recorded". The
    gate's own result is the only thing that answers this question."""
    sys.path.insert(0, str(ROOT / "engine" / "lib"))
    import spec_workflow
    src = (ROOT / "engine/lib/spec_workflow.py").read_text(encoding="utf-8")
    assert "_gate_committed(key)" in src
    assert 'entry.get("linked") or entry.get("committed")' not in src
    # A COMMITTED gate in a run record for the key is what makes it true.
    assert spec_workflow._gate_committed("no-such-key-ever") is False


def test_the_board_survives_a_torn_run_record(tmp_path, monkeypatch):
    """One unreadable file must not take out the other twenty tickets' rows."""
    sys.path.insert(0, str(ROOT / "engine" / "lib"))
    import spec_workflow
    runs = tmp_path / "reports" / "runs"
    runs.mkdir(parents=True)
    # Named so the TORN file is globbed FIRST. With the good record sorting
    # first the scan short-circuits on it and never opens the bad one — the
    # test passed against a version that let a torn record raise, which is
    # exactly the decorative pin this suite is not allowed to contain.
    (runs / "aaa-torn.json").write_text('{"trigger": {"key": "K-1"', encoding="utf-8")
    (runs / "zzz-good.json").write_text(
        '{"trigger": {"key": "K-1"}, "gates": [{"status": "COMMITTED"}]}',
        encoding="utf-8")
    monkeypatch.setattr(spec_workflow, "ROOT", tmp_path)
    assert spec_workflow._gate_committed("K-1") is True
    # And a record whose gate did NOT commit must not read as committed.
    (runs / "zzz-good.json").write_text(
        '{"trigger": {"key": "K-2"}, "gates": [{"status": "NO_CHANGES"}]}',
        encoding="utf-8")
    assert spec_workflow._gate_committed("K-2") is False


def test_an_email_alert_can_actually_be_addressed_from_the_ui():
    """`alCollect` hard-coded `recipients: []` and the row had no field for it.

    The backend has always honoured per-rule recipients — they set SMTP_TO for
    the delivery — so the effect was an email rule built in the UI that could
    never deliver, whose only feedback was its own "nothing will be delivered"
    warning with nothing the user could do about it. A configured alert that
    silently reaches no one is worse than no alert: it is believed.
    """
    ui = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    assert 'data-f="recipients"' in ui, "no recipients field in the rule row"
    assert "recipients: []" not in ui, "recipients are hard-coded empty again"
    assert "recipients: g('recipients')" in ui, "the field is not collected"
    # The table's three column counts must agree, or rows misalign under the
    # header the moment a column is added.
    import re
    head = ui[ui.index('table id="al-table"'):]
    head = head[:head.index("</thead>")]
    cols = len(re.findall(r"<th[ >]", head))
    row = ui[ui.index("function alRow"):]
    row = row[:row.index("\nfunction ")]
    assert row.count("'<td") == cols, \
        f"row renders {row.count(chr(39) + '<td')} cells for {cols} headers"
    m = re.search(r'colspan="(\d+)" class="muted">No rules yet', ui)
    assert m and int(m.group(1)) == cols, "empty-state colspan does not span the table"


# ---- the UI guide is checked against the UI, not written from memory --------
def test_the_ui_guide_covers_every_view_the_dashboard_has():
    """A view-by-view guide that silently misses a view is worse than none —
    the reader concludes the view does not exist, or that the guide is stale
    and stops trusting the rest of it."""
    import re
    ui = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    views = set(re.findall(r'data-view="([a-z-]+)"', ui))
    guide = (ROOT / "docs/ui-guide.md").read_text(encoding="utf-8").lower()
    # Headings are prose ("Runs & team reviews"), so match on the words the
    # dashboard's own nav uses for each view.
    NAMES = {"overview": "overview", "wizard": "guided run", "queue": "queue",
             "plans": "test plans", "specflow": "spec workflow", "runs": "runs",
             "activity": "activity", "alerts": "alerts", "trace": "trace",
             "cost": "cost", "artifacts": "artifacts", "catalog": "catalog",
             "repos": "repositories", "settings": "settings"}
    assert views == set(NAMES), f"views changed: {views ^ set(NAMES)}"
    for view, heading in NAMES.items():
        assert f"## {heading}" in guide, f"docs/ui-guide.md does not document '{view}'"


def test_the_ui_guide_states_the_rules_it_claims_the_ui_follows():
    """Each of these is a promise the guide makes to a reader who will act on
    it, and each is enforced somewhere in the code."""
    guide = (ROOT / "docs/ui-guide.md").read_text(encoding="utf-8")
    # Waiver cap, quoted as a number — it must match the enforced constant.
    sys.path.insert(0, str(ROOT / "engine" / "lib"))
    import waiver_store
    assert f"{waiver_store.MAX_DAYS} days" in guide, "the stated waiver cap is not the enforced one"
    # The strict-mode consequence, quoted as an exit code.
    assert "exit 8" in guide
    src = (ROOT / "engine/gate/spec_check.py").read_text(encoding="utf-8")
    assert "8" in src, "spec_check no longer exits 8; the guide says it does"
    # The alert-recipients warning the guide tells people to act on.
    assert "delivers nowhere" in guide
    import alert_rules
    assert "nothing will be delivered" in (ROOT / "engine/lib/alert_rules.py").read_text(
        encoding="utf-8")


def test_the_board_scans_the_run_records_once_not_once_per_ticket(tmp_path, monkeypatch):
    """`board()` asked "did this commit?" per ticket, and each answer re-globbed
    and re-parsed EVERY run record — O(keys x records).

    Measured at 200 records: 1 key took 1 ms, 20 keys took 250 ms. The dashboard
    makes that worse than it sounds, because entering the Spec workflow view
    re-runs its loaders, so the cost lands on every navigation rather than once
    per page load.

    Pinned by COUNTING the scans, not by timing: a timing assertion is flaky on
    a loaded machine and says nothing about why it got slow.
    """
    import glob as glob_mod
    import json
    sys.path.insert(0, str(ROOT / "engine" / "lib"))
    import spec_workflow

    runs = tmp_path / "reports" / "runs"
    runs.mkdir(parents=True)
    for i in range(12):
        (runs / f"r{i}.json").write_text(json.dumps({
            "trigger": {"key": f"K-{i}"},
            "gates": [{"test_repo": "e2e-api-tests-1", "status": "committed"}],
        }), encoding="utf-8")
    monkeypatch.setattr(spec_workflow, "ROOT", tmp_path)
    monkeypatch.setattr(spec_workflow, "_keys", lambda: [f"K-{i}" for i in range(12)])

    scans = []
    real_glob = glob_mod.glob
    monkeypatch.setattr(glob_mod, "glob",
                        lambda p, *a, **k: (scans.append(p), real_glob(p, *a, **k))[1])
    board = spec_workflow.board()
    run_scans = [p for p in scans if "reports" in p and "runs" in p]
    assert len(run_scans) == 1, \
        f"{len(run_scans)} scans of the run records for 12 tickets"
    assert len(board["rows"]) == 12


def test_committed_keys_and_the_single_key_lookup_agree(tmp_path, monkeypatch):
    """Two ways to ask the same question must not drift; the single-key helper
    is now defined in terms of the set so they cannot."""
    import json
    sys.path.insert(0, str(ROOT / "engine" / "lib"))
    import spec_workflow
    runs = tmp_path / "reports" / "runs"
    runs.mkdir(parents=True)
    (runs / "yes.json").write_text(json.dumps({
        "trigger": {"key": "K-1"}, "gates": [{"status": "committed"}]}), encoding="utf-8")
    (runs / "no.json").write_text(json.dumps({
        "trigger": {"key": "K-2"}, "gates": [{"status": "no_changes"}]}), encoding="utf-8")
    monkeypatch.setattr(spec_workflow, "ROOT", tmp_path)

    ck = spec_workflow.committed_keys()
    assert ck == {"K-1"}
    for k in ("K-1", "K-2", "never-ran"):
        assert spec_workflow._gate_committed(k) == (k in ck), k
    # And a caller passing the set must get the same answer as one that doesn't.
    assert spec_workflow.status("K-1", committed=ck)["state"] == \
        spec_workflow.status("K-1")["state"]
