"""Pins standalone operation: the platform must run with NO OpenHands at all
(docs/integrations/standalone-operation.md).

The claim being protected is narrow and load-bearing: OpenHands is one of four
interchangeable trigger paths, so its absence or outage must never change whether a
run happens, whether tests get committed, or whether a CI gate goes red. It is easy to
regress by adding an import, a health precondition, or a check that fails the build.
"""
import os, pathlib, subprocess, sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import integration_check
import openhands_mode
import work_queue

DEAD = "http://127.0.0.1:1"          # closed port: connect fails fast, no network wait


# ------------------------------------------------------- the pipeline never calls it

def test_pipeline_does_not_invoke_openhands():
    """pipeline.sh may MENTION OpenHands in prose, but must not call it."""
    src = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    code = [l for l in src.splitlines()
            if not l.lstrip().startswith("#") and "openhands" in l.lower()]
    # The only tolerable hit is the clarification message text ("@openhands use ...")
    for line in code:
        assert "MSG=" in line or "@openhands use" in line, \
            f"pipeline.sh appears to call OpenHands: {line.strip()}"


def test_no_engine_module_imports_the_openhands_client_at_run_time():
    """Only the dashboard (a UI convenience) and the checker may import it."""
    offenders = []
    for f in (ROOT / "engine").rglob("*.py"):
        if f.name in ("openhands_client.py", "openhands_events.py", "integration_check.py"):
            continue
        if "openhands_client" in f.read_text(encoding="utf-8", errors="replace"):
            offenders.append(f.relative_to(ROOT).as_posix())
    assert not offenders, f"engine modules must not depend on OpenHands: {offenders}"


def test_every_trigger_path_calls_the_pipeline_directly():
    """CI paths must not route through OpenHands — that is what makes them a fallback."""
    for rel in ("triggers/github-actions/ai-qe-pr.yml",
                "triggers/jenkins/Jenkinsfile",
                "triggers/bitbucket-pipelines/bitbucket-pipelines.yml"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "engine/pipeline.sh" in text, f"{rel} does not call the pipeline"
        assert "openhands" not in text.lower(), f"{rel} depends on OpenHands"


# --------------------------------------------------- an outage is degraded, not fatal

def test_unreachable_openhands_is_degraded_and_not_fatal(monkeypatch):
    monkeypatch.setenv("OPENHANDS_URL", DEAD)
    monkeypatch.setenv("OPENHANDS_API_KEY", "x")
    out = integration_check.run(["openhands"])
    r = out["results"][0]
    assert r["status"] == "degraded", f"expected degraded, got {r['status']}"
    assert out["summary"]["fail"] == 0, "an OpenHands outage must not count as a failure"
    assert out["summary"]["degraded"] == 1
    assert "pipeline does not call it" in r["hint"], "hint must name the alternative"


def test_check_integrations_exits_zero_when_only_openhands_is_down():
    """A red CI gate over an uncalled system was the original bug."""
    env = {**os.environ, "OPENHANDS_URL": DEAD, "OPENHANDS_API_KEY": "x"}
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/integration_check.py"),
                        "openhands"], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
                       env=env)
    assert r.returncode == 0, f"exit {r.returncode} — an optional outage went fatal\n{r.stdout}"
    assert "[warn]" in r.stdout and "degraded" in r.stdout


def test_unset_openhands_is_skipped_not_degraded(monkeypatch):
    """The recommended standalone posture: simply leave OPENHANDS_URL unset."""
    monkeypatch.delenv("OPENHANDS_URL", raising=False)
    monkeypatch.setattr(integration_check, "_load_env", lambda: None)  # ignore .env
    out = integration_check.run(["openhands"])
    assert out["results"][0]["status"] == "skipped"
    assert out["summary"]["fail"] == 0 and out["summary"]["degraded"] == 0


def test_only_openhands_is_optional():
    """Slack/Splunk/Jenkins failing IS worth a red build — don't widen this set."""
    assert integration_check.OPTIONAL_CHECKS == {"openhands"}


# ------------------------------------------------- the hybrid switch (AIQE_OPENHANDS)

def test_mode_defaults_to_hybrid(monkeypatch):
    """`auto` is the only default under which an outage cannot stop a team shipping."""
    monkeypatch.delenv("AIQE_OPENHANDS", raising=False)
    assert openhands_mode.mode() == "auto"
    assert openhands_mode.enabled() and not openhands_mode.required()


@pytest.mark.parametrize("raw,expected", [
    ("off", "off"), ("0", "off"), ("false", "off"), ("none", "off"), ("disabled", "off"),
    ("auto", "auto"), ("1", "auto"), ("hybrid", "auto"), ("optional", "auto"),
    ("required", "required"), ("strict", "required"),
    ("  REQUIRED  ", "required"),                 # case + whitespace
    ("nonsense", "auto"), ("", "auto"),           # unrecognised -> safe default
])
def test_mode_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("AIQE_OPENHANDS", raw)
    assert openhands_mode.mode() == expected


def test_env_overrides_org_config(monkeypatch):
    """An operator overriding for one run must not have to edit committed config."""
    monkeypatch.setattr(openhands_mode, "_from_config", lambda: "required")
    monkeypatch.setenv("AIQE_OPENHANDS", "off")
    assert openhands_mode.mode() == "off"
    monkeypatch.delenv("AIQE_OPENHANDS")
    assert openhands_mode.mode() == "required"


def test_unreadable_config_falls_back_instead_of_raising(monkeypatch):
    monkeypatch.delenv("AIQE_OPENHANDS", raising=False)
    monkeypatch.setattr(openhands_mode, "ROOT", pathlib.Path("/nonexistent-xyz"))
    assert openhands_mode.mode() == "auto"


@pytest.mark.parametrize("mode,expect_status,expect_exit", [
    ("off", "skipped", 0),        # never contacted
    ("auto", "degraded", 0),      # hybrid: reported, not fatal
    ("required", "fail", 1),      # hard dependency: fatal
])
def test_each_mode_classifies_an_outage(monkeypatch, mode, expect_status, expect_exit):
    monkeypatch.setenv("AIQE_OPENHANDS", mode)
    monkeypatch.setenv("OPENHANDS_URL", DEAD)
    monkeypatch.setenv("OPENHANDS_API_KEY", "x")
    out = integration_check.run(["openhands"])
    assert out["results"][0]["status"] == expect_status

    env = {**os.environ, "AIQE_OPENHANDS": mode, "OPENHANDS_URL": DEAD,
           "OPENHANDS_API_KEY": "x"}
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/integration_check.py"),
                        "openhands"], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
                       env=env)
    assert r.returncode == expect_exit, f"{mode}: exit {r.returncode}\n{r.stdout}"


def test_off_mode_skips_even_when_a_url_is_configured(monkeypatch):
    """Turning it off must win over leftover credentials in .env."""
    monkeypatch.setenv("AIQE_OPENHANDS", "off")
    monkeypatch.setenv("OPENHANDS_URL", "https://openhands.example.com")
    r = integration_check.check_openhands()
    assert r["status"] == "skipped" and "standalone" in r["detail"]


def test_dashboard_refuses_to_delegate_when_off():
    """Starting a conversation with OpenHands off would contradict the posture."""
    src = (ROOT / "bin/dashboard_server.py").read_text(encoding="utf-8")
    assert "openhands_mode.enabled()" in src, "conversation start is not mode-gated"
    idx = src.index("openhands_mode.enabled()")
    assert "409" in src[idx:idx + 400], "expected a 409 with an explanatory hint"


def test_mode_is_configurable_from_the_settings_ui():
    spec = (ROOT / "engine/lib/settings_store.py").read_text(encoding="utf-8")
    assert "AIQE_OPENHANDS" in spec, "the hybrid switch must be settable in Settings"


# ------------------------------------------------------------ end-to-end, no OpenHands

def test_full_run_commits_with_openhands_unreachable(tmp_path):
    """The headline claim: routed -> generated -> gated -> COMMITTED, exit 0."""
    env = {**os.environ, "AIQE_MOCK": "1", "OPENHANDS_URL": DEAD,
           "OPENHANDS_API_KEY": "x"}
    r = subprocess.run([work_queue.bash_exe(), "engine/pipeline.sh", "jira", "PROJ-301"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", stdin=subprocess.DEVNULL, env=env, timeout=900)
    assert r.returncode == 0, f"pipeline failed without OpenHands:\n{r.stdout[-3000:]}"
    assert "GATE_STATUS=COMMITTED" in r.stdout, \
        f"no commit without OpenHands:\n{r.stdout[-3000:]}"


# ------------------------------------------------------------------------- the doc

def test_decision_record_exists_and_documents_the_isolation_requirement():
    doc = ROOT / "docs/integrations/standalone-operation.md"
    assert doc.exists(), "the decision record is the deliverable — keep it"
    text = doc.read_text(encoding="utf-8")
    # The one genuinely load-bearing caveat must not be quietly dropped
    assert "dangerously-skip-permissions" in text, \
        "standalone operation must document the isolation requirement"
    assert "AIQE_GATE_CHECK_ONLY=1" in text, "document the standalone gate check"
    for path in ("bin/taskevent_receiver.py", "deploy/openshift/"):
        assert path in text, f"decision record should point at {path}"
