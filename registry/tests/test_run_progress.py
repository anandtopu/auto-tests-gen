"""Per-run progress: the two questions a user asks while waiting.

    how far along is it, and which step is it on?
    it failed — which step, why, and where do I look?

`wizard_status` answers the JOURNEY question and deliberately collapses the run
into one step ("the agent is analyzing and writing tests"). That is the right
grain for a guided flow and the wrong one for tracing a failure.

The state model is what these pin. A step nobody can observe must not be
reported as pending or done — a progress view that says "running" about a
process that died twenty minutes ago is the C13 failure with a spinner on it.
"""
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import run_progress as rp  # noqa: E402


def _estate(tmp_path, ctx=None, lock=None, artifacts=(), gates=None, record=None,
            cost=(), skips=()):
    """A throwaway checkout shaped like the real one."""
    (tmp_path / "out").mkdir(exist_ok=True)
    (tmp_path / "reports/runs").mkdir(parents=True, exist_ok=True)
    if ctx:
        (tmp_path / "out/run-context.json").write_text(json.dumps(ctx), encoding="utf-8")
    if lock is not None:
        d = tmp_path / "out/.pipeline.lock"
        d.mkdir(exist_ok=True)
        import os
        os.utime(d, (time.time() - lock * 60, time.time() - lock * 60))
    for a in artifacts:
        p = tmp_path / a
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}", encoding="utf-8")
    if gates:
        (tmp_path / "out/gate_results.tsv").write_text(
            "\n".join("\t".join(g) for g in gates), encoding="utf-8")
    if cost:
        (tmp_path / "out/cost.tsv").write_text(
            "\n".join("\t".join(c) for c in cost), encoding="utf-8")
    if skips:
        (tmp_path / "out/phase-skips.tsv").write_text(
            "\n".join("\t".join(s) for s in skips), encoding="utf-8")
    if record:
        (tmp_path / f"reports/runs/{record['run_id']}.json").write_text(
            json.dumps(record), encoding="utf-8")
    return tmp_path


def _states(p):
    return {s["id"]: s["state"] for s in p["steps"]}


# ------------------------------------------------------------------ live

def test_the_first_incomplete_step_is_the_one_running(tmp_path):
    e = _estate(tmp_path, ctx={"run_id": "r1", "mode": "pr", "key": "PR-x-1"},
                lock=0, artifacts=["out/resolve.contract.json",
                                   "out/triage.contract.json"])
    p = rp.progress(key="PR-x-1", root=e)
    assert p["source"] == "live" and p["busy"] is True
    st = _states(p)
    assert st["resolve"] == "done" and st["triage"] == "done"
    assert st["generate"] == "running", "the first incomplete step must be the current one"
    assert st["validate"] == "pending" and st["gate"] == "pending"


def test_a_stale_lock_means_unknown_not_running(tmp_path):
    """pipeline.sh breaks locks older than 90 minutes, so past that the holder
    is gone. Reporting the step as `running` would leave a progress view
    spinning forever about a process that no longer exists — the exact shape
    C13 exists to forbid."""
    e = _estate(tmp_path, ctx={"run_id": "r1", "mode": "pr", "key": "PR-x-1"},
                lock=rp.STALE_LOCK_MINUTES + 5,
                artifacts=["out/resolve.contract.json"])
    p = rp.progress(key="PR-x-1", root=e)
    st = _states(p)
    assert st["triage"] == "unknown", f"expected unknown, got {st['triage']}"
    assert p["busy"] is False, "a dead holder must stop the caller polling"
    detail = [s["detail"] for s in p["steps"] if s["id"] == "triage"][0]
    assert "gone" in detail and "expired" in detail
    assert "record" in detail or "logs" in detail, "the message must say where to look"


def test_a_skipped_phase_is_skipped_not_missing(tmp_path):
    """A no-op phase (critic with zero generated tests) is a DECISION, not a
    gap. Rendering it as pending would make a finished run look stuck."""
    e = _estate(tmp_path, ctx={"run_id": "r1", "mode": "pr", "key": "PR-x-1"},
                lock=0, artifacts=["out/resolve.contract.json",
                                   "out/triage.contract.json",
                                   "out/generate.contract.json",
                                   "out/validate.contract.json"],
                skips=[("critic", "no generated tests to score")])
    st = _states(rp.progress(key="PR-x-1", root=e))
    assert st["critic"] == "skipped"
    assert [s for s in rp.progress(key="PR-x-1", root=e)["steps"]
            if s["id"] == "critic"][0]["detail"] == "no generated tests to score"


# ---------------------------------------------------------------- record

def _rec(gates, phases=(), run_id="r9", key="PR-x-1", overall="committed", mode="pr"):
    return {"run_id": run_id, "mode": mode, "overall": overall, "ts": 1,
            "trigger": {"type": "pr", "key": key},
            "phases": [{"name": n, "contract": c} for n, c in phases],
            "gates": gates}


def test_a_finished_run_reads_from_the_record_not_the_scratch_dir(tmp_path):
    """out/ belongs to whatever ran LAST in this checkout. Once a record
    exists it is authoritative — otherwise a user opening an old run would be
    shown the current one's artifacts."""
    e = _estate(tmp_path, artifacts=["out/resolve.contract.json"],
                record=_rec([{"test_repo": "e2e-api", "status": "committed",
                              "exit_code": "0", "commit": "abc"}],
                            phases=[("triage", {}), ("generate", {"tests": [
                                {"action": "created"}, {"action": "updated"}]})]))
    p = rp.progress(key="PR-x-1", root=e)
    assert p["source"] == "record" and p["busy"] is False
    st = _states(p)
    assert st["generate"] == "done" and st["gate"] == "done"
    gen = [s for s in p["steps"] if s["id"] == "generate"][0]
    assert "1 created" in gen["detail"] and "1 updated" in gen["detail"]


def test_a_failed_gate_carries_the_exit_codes_meaning_and_its_log(tmp_path):
    """The debugging half of the request. An exit code with no meaning attached
    is the failure message being withheld from the person who needs it."""
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
    e = _estate(tmp_path, record=_rec(
        [{"test_repo": "e2e-api", "status": "quarantined", "exit_code": 5}],
        overall="quarantined"))
    (e / "reports/PR-x-1-e2e-api.log").write_text(
        "\n".join(f"line {i}" for i in range(50)), encoding="utf-8")
    p = rp.progress(key="PR-x-1", root=e)
    gate = [s for s in p["steps"] if s["id"] == "gate"][0]
    assert gate["state"] == "failed"
    r = gate["repos"][0]
    assert r["meaning"] == "TESTS_FAILED"
    assert "did not pass" in r["why"]
    assert r["log"].endswith("PR-x-1-e2e-api.log")
    assert r["log_tail"] and "line 49" in r["log_tail"], "the tail is the debugging value"


def test_an_unreadable_log_is_none_not_an_empty_string(tmp_path):
    """"We could not read the log" and "the step said nothing" lead to
    different actions."""
    e = _estate(tmp_path, record=_rec(
        [{"test_repo": "gone", "status": "quarantined", "exit_code": 5}]))
    gate = [s for s in rp.progress(key="PR-x-1", root=e)["steps"]
            if s["id"] == "gate"][0]
    assert gate["repos"][0]["log_tail"] is None


def test_a_record_without_a_gate_block_does_not_claim_the_gate_passed(tmp_path):
    """A run aborted at exit 77 never reaches the gate. Reporting `done`
    would tell a user their tests were committed when nothing was."""
    e = _estate(tmp_path, record=_rec([], overall="aborted"))
    gate = [s for s in rp.progress(key="PR-x-1", root=e)["steps"]
            if s["id"] == "gate"][0]
    assert gate["state"] == "unknown"
    assert "ended before the gate" in gate["detail"]


# ------------------------------------------------------------------ none

def test_no_run_at_all_says_so_and_tells_the_user_what_to_do(tmp_path):
    """An empty step ladder would render as a row of pending steps — which
    reads as "queued and starting soon" for a run that does not exist."""
    p = rp.progress(key="PR-nothing-1", root=_estate(tmp_path))
    assert p["source"] == "none" and p["steps"] == []
    assert "No run has been recorded" in p["detail"]
    assert "PR-<repo>-<number>" in p["detail"], "spell out the key format"


# ------------------------------------------------------------- exit codes

def test_an_unrecognized_exit_code_is_not_guessed():
    name, why = rp.explain_exit(99)
    assert name == "UNRECOGNIZED" and "not one this pipeline documents" in why
    assert rp.explain_exit(None)[0] == "UNKNOWN"
    assert rp.explain_exit("")[0] == "UNKNOWN"
    assert rp.explain_exit(0)[0] == "OK"


def test_every_documented_exit_code_is_one_the_source_actually_emits():
    """The table is a RENDERING of the gate and pipeline, so it must not drift
    into describing codes nobody raises — a fictional meaning is worse than no
    meaning, because it sends the reader somewhere that is not the problem."""
    src = ((ROOT / "engine/gate/gate.sh").read_text(encoding="utf-8")
           + (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8"))
    missing = [c for c in rp.EXIT_MEANINGS if f"exit {c}" not in src]
    assert not missing, f"documented but never emitted: {missing}"


def test_every_stage_in_a_chain_has_a_why():
    """The view exists for people who did not build the pipeline. A step with a
    label and no explanation is a progress bar, not a trace."""
    for mode, chain in rp.CHAINS.items():
        for st in chain:
            assert st["why"] and len(st["why"]) > 20, f"{mode}/{st['id']} has no why"
            assert st["label"], f"{mode}/{st['id']} has no label"


def test_the_chains_match_the_phases_the_pipeline_actually_runs():
    """A step list that drifts from pipeline.sh shows users a stage that never
    runs, or hides one that does."""
    src = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    for mode in ("pr", "jira"):
        for st in rp.CHAINS[mode]:
            if st["id"] in ("resolve", "gate", "generate"):
                continue          # invoked via wrappers, not `PHASE <id>`
            assert f"PHASE {st['id']} " in src, \
                f"{mode}: no `PHASE {st['id']}` in pipeline.sh"


# ------------------------------------------------------------- API + view

def test_the_endpoint_refuses_a_bad_key_and_requires_one():
    """The route interpolates nothing into a path, but an unvalidated key still
    reaches glob and the record scan. Same word-character contract as the other
    key-taking routes."""
    src = (ROOT / "bin/dashboard_server.py").read_text(encoding="utf-8")
    block = src[src.index('elif url.path == "/api/run-progress":'):]
    block = block[:block.index('elif url.path == "/api/wizard/status":')]
    assert r're.fullmatch(r"[\w.-]+", key)' in block, "key is not validated"
    # The CONDITION, not just its message: asserting the string alone survived
    # mutating the guard to `if False:`, which is the pin proving nothing.
    assert "if not key and not run:" in block, "the empty-request guard is gone"
    assert "key or run required" in block


def test_the_view_polls_only_while_a_live_holder_owns_the_lock():
    """`busy` is False for a stale lock, so a dead run must stop the poll. A
    view that keeps polling a vanished run is the spinner-forever failure."""
    js = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    body = js[js.index("async function rpLoad"):]
    body = body[:body.index("onEnter('progress'")]
    assert "if (p.busy)" in body, "the poll is not gated on busy"
    assert "setTimeout" in body


def test_a_superseded_response_never_overwrites_a_newer_one():
    """Found by DRIVING the page: entering the view fires the loader for
    whatever key is already in the box, so a user who then types a new key has
    two requests in flight. Without a token the slower, earlier response wins
    and the page shows one key's steps under another key's name.
    """
    js = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    body = js[js.index("let rpSeq = 0;"):]
    body = body[:body.index("onEnter('progress'")]
    assert "const mine = ++rpSeq;" in body
    assert "if (mine !== rpSeq) return;" in body, "no stale-response guard"
    assert "mine === rpSeq" in body, "the error path can still clobber a newer render"


def test_the_view_reports_a_load_failure_in_its_own_words():
    """`loadFailed` renders a <tr> and this view has no table, so reusing it
    would write a row into nothing and leave the ladder looking merely
    unchanged — indistinguishable from a run that had not moved."""
    js = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    body = js[js.index("async function rpLoad"):]
    body = body[:body.index("onEnter('progress'")]
    assert "display failure, not an empty result" in body


def test_every_view_that_fetches_reports_a_load_failure():
    """Driving the served page with a failing `fetch` showed the QUEUE view
    silently keeping whatever it had. `runViewLoaders` swallows a rejected
    loader by design (one failing loader must not stop its neighbours), so an
    unguarded `await` leaves the view unchanged — and on the queue that reads as
    "nothing is queued, nothing is running". An operator queues a duplicate, or
    walks away believing their submission was never accepted.

    Asserted over the loaders that actually FETCH. `repos` and `catalog` are
    server-rendered and issue zero requests, so there is nothing there to fail —
    measured, not assumed.
    """
    js = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    body = js[js.index("async function refreshQueue"):]
    body = body[:body.index("\nasync function ", 10) if "\nasync function " in body[10:]
                else len(body)]
    assert "loadFailed('#queue-table tbody'" in body, \
        "the queue loader no longer reports a failed load"
    assert "not current" in body, \
        "the count must say the list is stale, not just leave a number standing"
