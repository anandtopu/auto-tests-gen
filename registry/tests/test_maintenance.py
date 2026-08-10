"""The nightly job must not report success for work it did not do.

`make maintain` is what cron and the OpenShift CronJob run unattended. Every
step was `-`-prefixed so make ignored failures, and the target ended with an
unconditional "== maintenance complete ==".

Measured before the fix, with two steps sabotaged: both failed, the last line
printed was `maintenance complete`, and the exit code was **0**. One of the two
was the state-bundle snapshot — the disaster-recovery backup — so it could have
failed every night for a year while the CronJob stayed green. Constitution C13
at the deployment layer.

The steps must STAY independent and best-effort (a network blip in guidance
sync must not skip the backup that follows it), so these pin the report and the
exit code rather than the abort-on-first-failure behaviour that would be worse.
"""
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import maintenance  # noqa: E402


def _fake(codes):
    """A runner that returns a scripted exit code per step index."""
    seq = iter(codes)
    return lambda argv: next(seq)


def test_a_failed_step_does_not_stop_the_ones_after_it():
    """The reason the `-` prefixes were right, and must not be 'fixed' into an
    abort: the backup runs last."""
    steps = [("a", ["x.py"], False), ("b", ["y.py"], False), ("c", ["z.py"], False)]
    res = maintenance.run_steps(steps, runner=_fake([1, 0, 0]))
    assert [r["step"] for r in res] == ["a", "b", "c"], "a failure skipped later steps"
    assert [r["state"] for r in res] == ["failed", "ok", "ok"]


def test_a_failed_local_step_is_named_and_fails_the_job():
    steps = [("prune", ["a.py"], False), ("state-bundle snapshot", ["b.py"], False)]
    res = maintenance.run_steps(steps, runner=_fake([0, 1]))
    out = maintenance.summarize(res)
    assert "MAINTENANCE INCOMPLETE" in out
    assert "state-bundle snapshot" in out, "the failing step is not named"
    assert "maintenance complete" not in out, \
        "the old unconditional success line survived a failure"


def test_an_external_step_degrades_instead_of_failing():
    """A job that goes red on somebody else's outage is a job whose red gets
    ignored — but it must still SAY the step did not complete."""
    steps = [("guidance sync", ["a.py"], True), ("prune", ["b.py"], False)]
    res = maintenance.run_steps(steps, runner=_fake([1, 0]))
    out = maintenance.summarize(res)
    assert res[0]["state"] == "degraded"
    assert "DEGRADED" in out and "guidance sync" in out
    assert "MAINTENANCE INCOMPLETE" not in out
    assert "did NOT complete successfully" in out, \
        "degraded must not read as 'fine'"


def test_a_local_failure_makes_the_job_exit_nonzero():
    """THE point of the change: a CronJob reads the exit code, and this used to
    be 0 no matter what happened. Pinned on main() end to end, because an
    earlier version of this file checked only the summary text and a mutation
    turning the whole verdict into `return 0` survived it."""
    steps = [("prune", ["a.py"], False), ("state-bundle snapshot", ["b.py"], False)]
    assert maintenance.main([], steps=steps, runner=_fake([0, 1])) == 1
    assert maintenance.main([], steps=steps, runner=_fake([0, 0])) == 0


def test_an_external_outage_alone_keeps_the_job_green():
    steps = [("guidance sync", ["a.py"], True), ("prune", ["b.py"], False)]
    assert maintenance.main([], steps=steps, runner=_fake([1, 0])) == 0


def test_cost_reconciliation_degrades_only_for_its_external_exit_code():
    steps = [("cost reconciliation", ["cost.py"], {75})]
    degraded = maintenance.run_steps(steps, runner=_fake([75]))
    failed = maintenance.run_steps(steps, runner=_fake([1]))
    assert degraded[0]["state"] == "degraded"
    assert failed[0]["state"] == "failed"
    assert maintenance.exit_code(degraded) == 0
    assert maintenance.exit_code(failed) == 1


def test_the_summary_is_printed_even_when_everything_worked():
    """A summary that only appears on failure trains people not to look."""
    steps = [("a", ["x.py"], False), ("b", ["y.py"], False)]
    out = maintenance.summarize(maintenance.run_steps(steps, runner=_fake([0, 0])))
    assert "maintenance summary" in out
    assert "all 2 step(s) ok" in out


def test_every_declared_step_points_at_a_script_that_exists():
    """A typo'd path would exit non-zero forever and read as a broken estate."""
    missing = [a[0] for _, a, _ in maintenance.STEPS if not (ROOT / a[0]).exists()]
    assert not missing, f"maintenance steps reference missing scripts: {missing}"


def test_only_externally_dependent_steps_are_tolerated():
    """Marking a local step tolerated restores exactly the silence this module
    removes, and would do it invisibly."""
    tolerated = {label for label, _, t in maintenance.STEPS if t}
    assert tolerated == {"guidance sync", "vector index refresh",
                         "cost reconciliation"}, (
        "the tolerated set changed — a local step marked tolerated stops failing "
        "the nightly job")


def test_the_makefile_delegates_instead_of_ignoring_failures():
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    target = mk.split("\nmaintain:", 1)[1].split("\nstate-export:", 1)[0]
    assert "maintenance.py" in target
    assert "-python3" not in target, \
        "a `-`-prefixed step is back: its failure is ignored and unreported"
    # Look for an ECHO that prints success, not the phrase anywhere: the target
    # carries a comment quoting the old behaviour, and a substring check turned
    # the explanation of the bug into a false report of the bug.
    echoed = [ln for ln in target.split("\n")
              if "echo" in ln and "maintenance complete" in ln
              and not ln.lstrip().startswith("@#")]
    assert not echoed, \
        f"the unconditional success echo is back in the Makefile: {echoed}"


# --- the manifest that makes the exit code observable -----------------------

def _manifests():
    yaml = pytest.importorskip("yaml")
    for f in sorted((ROOT / "deploy/openshift").glob("*.yaml")):
        for doc in yaml.safe_load_all(f.read_text(encoding="utf-8")):
            if isinstance(doc, dict):
                yield f.name, doc


def test_a_cronjob_actually_runs_the_nightly_job():
    """docs/deployment.md told operators to run `make maintain` nightly and no
    manifest did it, so a by-the-book deployment took no backups at all."""
    jobs = [d for _, d in _manifests() if d.get("kind") == "CronJob"]
    assert jobs, "no CronJob — nothing runs maintenance in a deployment"
    spec = jobs[0]["spec"]
    assert spec.get("schedule"), "a CronJob with no schedule never fires"
    assert spec.get("concurrencyPolicy") == "Forbid", \
        "maintenance takes the same locks a run does"
    # command OR args: the invariant is that the job RUNS maintenance, not which
    # field carries it. It moved from `command:` to `args:` so the image
    # ENTRYPOINT (first-boot state seeding) is not replaced — see
    # test_deploy_manifests.test_no_container_replaces_the_image_entrypoint.
    ct = spec["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
    cmd = " ".join((ct.get("command") or []) + (ct.get("args") or []))
    assert "maintain" in cmd, f"the CronJob does not run maintenance: {cmd!r}"


def test_no_manifest_references_a_volume_claim_nothing_defines():
    """Caught in review: the first draft of the CronJob invented an
    `ai-qe-state` claim. The job would never schedule — a silent no-backup with
    extra steps, which is the failure this whole change exists to prevent."""
    defined, referenced = set(), {}
    for name, doc in _manifests():
        if doc.get("kind") == "PersistentVolumeClaim":
            defined.add(doc["metadata"]["name"])
        for vol in (doc.get("spec", {}).get("jobTemplate", {}).get("spec", {})
                    .get("template", {}).get("spec", {}).get("volumes")
                    or doc.get("spec", {}).get("template", {}).get("spec", {})
                    .get("volumes") or []):
            claim = (vol.get("persistentVolumeClaim") or {}).get("claimName")
            if claim:
                referenced.setdefault(claim, set()).add(name)
    dangling = {k: sorted(v) for k, v in referenced.items() if k not in defined}
    assert not dangling, f"manifests reference undefined claims: {dangling}"


def test_the_cronjob_is_applied_by_kustomize():
    """A manifest not listed in kustomization.yaml is a file, not a deployment."""
    yaml = pytest.importorskip("yaml")
    kust = yaml.safe_load(
        (ROOT / "deploy/openshift/kustomization.yaml").read_text(encoding="utf-8"))
    # Parsed, not substring-matched: a COMMENTED-OUT `# - cronjob.yaml` still
    # contains the filename, and the first version of this assertion passed
    # against exactly that mutation.
    assert "cronjob.yaml" in (kust.get("resources") or []),         "the CronJob is not an applied resource"


def test_the_operator_guide_states_the_exit_contract():
    """The exit code is only useful if the person who crons it knows to read it.
    docs/user-guide.md tells operators to run this nightly; before the fix it
    could not fail, so there was nothing to document. Now there is, and a doc
    that still implies best-effort-means-always-green would send an operator
    past a failed backup."""
    guide = (ROOT / "docs/user-guide.md").read_text(encoding="utf-8")
    section = guide.split("`make maintain` (cron it", 1)[1][:2000]
    for token in ("DEGRADED", "FAILED", "exit"):
        assert token in section, f"the guide never mentions {token!r} for maintenance"
    assert "every" in section.lower(), \
        "the guide should say the summary lists every step, not only failures"


# --- a degraded step says WHY, not just which exit code ----------------------
#
# Found by running `make maintain`. The summary is what a CronJob log shows and
# what an operator reads, and it said only "exit 75:
# engine/lib/cost_reconcile.py" -- sending the reader to the source to learn
# that 75 means "no billing credential". The step had ALREADY printed the
# reason; the summary discarded it. Same shape as the batch-drain defect: the
# information existed and was thrown away at the last step.

def test_a_structured_reason_the_step_emitted_wins():
    """Precedence copied from work_queue.failure_reason, which solved this
    once already: an emitted signal beats prose."""
    tail = ['some chatter',
            '{"state": "unavailable", "reason": "ANTHROPIC_ADMIN_KEY is not '
            'configured", "reason_code": "credential-missing"}']
    got = maintenance.step_reason(tail)
    # Assert the EXTRACTED form, not substrings -- those appear in the raw JSON
    # line too, so a fallback that just echoed the line passed this test.
    # Verified by mutation.
    assert got == "ANTHROPIC_ADMIN_KEY is not configured [credential-missing]", got
    assert not got.startswith("{"), "the raw JSON line was echoed, not parsed"


def test_prose_is_used_when_there_is_no_structured_reason():
    assert maintenance.step_reason(["working", "could not reach the endpoint"]) \
        == "could not reach the endpoint"


def test_no_output_yields_no_invented_reason():
    """An absent reason must stay absent -- inventing one would be worse than
    the exit code alone."""
    assert maintenance.step_reason([]) == ""
    assert maintenance.step_reason(["== cost reconciliation =="]) == ""


def test_the_summary_renders_the_reason_for_a_degraded_step():
    out = maintenance.summarize([
        {"step": "cost reconciliation", "exit": 75, "state": "degraded",
         "command": "engine/lib/cost_reconcile.py",
         "reason": "ANTHROPIC_ADMIN_KEY is not configured"}])
    assert "why: ANTHROPIC_ADMIN_KEY is not configured" in out, (
        "the operator still has to open the source to learn what exit 75 meant")


def test_the_summary_omits_the_why_line_when_there_is_nothing_to_say():
    """An empty `why:` would imply the reason is known and blank."""
    out = maintenance.summarize([
        {"step": "x", "exit": 3, "state": "failed", "command": "c", "reason": ""}])
    assert "why:" not in out
    assert "exit 3" in out


def test_a_successful_step_carries_no_reason(tmp_path):
    """Control: reasons are for steps that did not succeed."""
    res = maintenance.run_steps(steps=[("ok step", ["-c", "pass"], False)],
                                runner=lambda argv: 0)
    assert res[0]["state"] == "ok" and res[0]["reason"] == ""
