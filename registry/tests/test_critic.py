"""Regression tests for the advisory critic (engine/lib/critic.py, openhands-review §3.2).

The feature is only worth having if "advisory" is structurally true, so most of these
tests are about what the critic must NOT be able to do: change a commit decision, edit
the tests it grades, move a review status, or fail a run by malfunctioning. The
remainder pin the totality of the parser — a phase that emitted junk must degrade to
"no signal", never to a traceback in the middle of a pipeline run.
"""
import json, os, pathlib, subprocess, sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import critic
import review_state


@pytest.fixture
def contract(tmp_path, monkeypatch):
    """Point critic at a throwaway contract file."""
    p = tmp_path / "critic.contract.json"
    monkeypatch.setattr(critic, "CONTRACT", p)
    return p


def _write(p, obj):
    p.write_text(json.dumps(obj), encoding="utf-8")


# ------------------------------------------------------- advisory, structurally

def test_the_gate_never_reads_the_critic():
    """The whole design rests on this: the gate is deterministic and not an LLM."""
    for f in (ROOT / "engine/gate").rglob("*"):
        if f.is_file():
            assert "critic" not in f.read_text(encoding="utf-8", errors="replace").lower(), \
                f"{f} references the critic — the gate must not consume advisory signal"


def test_critic_phase_has_no_write_tools():
    """A critic that can Write/Edit is an unreviewed repair loop, not a second opinion."""
    cfg = yaml.safe_load(open(ROOT / "registry/org-config.yaml", encoding="utf-8"))
    tools = cfg["phases"]["critic"]["allowed_tools"]
    for forbidden in ("Write", "Edit", "Bash"):
        assert forbidden not in tools, f"critic must not get {forbidden}: {tools}"
    assert "Read" in tools


def test_pipeline_runs_the_critic_non_fatally_and_after_the_gate_is_decided():
    src = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    # a failing critic phase must not abort the run under `set -e`
    assert "PHASE critic critic.md" in src
    idx = src.index("PHASE critic critic.md")
    assert "||" in src[idx:idx + 400], "critic phase is not guarded against failure"
    # stale signal from a previous run must never be attributed to this one
    assert src.index("rm -f out/critic.contract.json") < idx
    # the score is appended to the summary only after the gate loop has run
    assert src.index("out/gate_results.tsv") < src.index("critic.py record")


def test_a_terrible_score_does_not_change_the_run_outcome(tmp_path):
    """The one that matters: overall is computed from gate results alone."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "gate_results.tsv").write_text("e2e-api-tests-1\tcommitted\t0\tdeadbee\n",
                                          encoding="utf-8")
    _write(out / "critic.contract.json",
           {"score": 0.0, "verdict": "accept", "noise_count": 9,
            "specs_reviewed": 9, "findings": [], "rationale": "everything is wrong"})
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/run_record.py"),
                        "RID", "pr", "PR-x-1"], cwd=tmp_path, capture_output=True,
                       text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    rec = json.loads(r.stdout)
    assert rec["overall"] == "committed", "a 0.0 critic score changed the run outcome"
    assert rec["critic"]["score"] == 0.0
    assert rec["critic"]["verdict"] == "weak"      # recomputed from thresholds


def test_no_critic_signal_leaves_the_record_unchanged(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "gate_results.tsv").write_text("e2e-api-tests-1\tcommitted\t0\tdeadbee\n",
                                          encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/run_record.py"),
                        "RID", "pr", "PR-x-1"], cwd=tmp_path, capture_output=True,
                       text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    rec = json.loads(r.stdout)
    assert "critic" not in rec and rec["overall"] == "committed"


def test_recording_a_score_never_moves_a_review_status(tmp_path, monkeypatch):
    """A low score must not un-approve work a human already approved."""
    store = tmp_path / "reviews.json"
    monkeypatch.setattr(review_state, "FILE", store)
    review_state.set_status("PROJ-9", "approved", reviewer="ana")
    sig = {"score": 0.05, "verdict": "weak", "noise_count": 3,
           "specs_reviewed": 3, "findings": [], "rationale": "n/a"}
    entry = review_state.set_critic("PROJ-9", sig)
    assert entry["status"] == "approved", "critic score changed the review status"
    assert entry["critic"]["score"] == 0.05
    # and it is durable
    assert json.load(open(store, encoding="utf-8"))["PROJ-9"]["critic"]["verdict"] == "weak"


def test_record_survives_an_unwritable_store(contract, monkeypatch):
    """Advisory means a storage failure cannot take the run down with it."""
    _write(contract, {"score": 0.9, "verdict": "accept", "noise_count": 0,
                      "findings": []})

    def boom(*a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr(review_state, "set_critic", boom)
    assert critic.record("PROJ-1", contract)["score"] == 0.9   # returned, not raised


# ------------------------------------------------------------------ enable/disable

def test_env_overrides_config_both_ways(monkeypatch):
    monkeypatch.setenv("AIQE_CRITIC", "0")
    assert critic.enabled({"enabled": True}) is False
    monkeypatch.setenv("AIQE_CRITIC", "1")
    assert critic.enabled({"enabled": False}) is True
    monkeypatch.delenv("AIQE_CRITIC")
    assert critic.enabled({"enabled": False}) is False
    assert critic.enabled({"enabled": True}) is True


def test_enabled_cli_exit_code():
    for val, expected in (("0", 1), ("1", 0)):
        r = subprocess.run([sys.executable, str(ROOT / "engine/lib/critic.py"), "enabled"],
                           cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                           stdin=subprocess.DEVNULL, env={**os.environ, "AIQE_CRITIC": val})
        assert r.returncode == expected, r.stdout


# ------------------------------------------------------------- parser totality

@pytest.mark.parametrize("payload", [
    None,                                          # no file at all
    "not json at all",
    '"a string"',
    "[1, 2, 3]",
    '{"verdict":"accept"}',                        # no score
    '{"score":"good"}',                            # unparseable score
    '{"score":null}',
])
def test_bad_contracts_degrade_to_no_signal(contract, payload):
    if payload is not None:
        contract.write_text(payload, encoding="utf-8")
    assert critic.load(contract) is None
    assert critic.summary_line(None) == "critic: no signal"


def test_scores_are_clamped_and_verdict_is_ours_not_the_models(contract):
    _write(contract, {"score": 1.4, "verdict": "weak", "noise_count": 0, "findings": []})
    sig = critic.load(contract)
    assert sig["score"] == 1.0 and sig["verdict"] == "accept"
    _write(contract, {"score": -3, "verdict": "accept", "noise_count": 0, "findings": []})
    assert critic.load(contract)["verdict"] == "weak"


def test_noise_cannot_exceed_the_specs_reviewed(contract):
    """Otherwise the escaped-noise metric reports more than 100%."""
    _write(contract, {"score": 0.5, "verdict": "review", "noise_count": 99,
                      "specs_reviewed": 4, "findings": []})
    assert critic.load(contract)["noise_count"] == 4


def test_noise_falls_back_to_counting_findings(contract):
    _write(contract, {"score": 0.5, "verdict": "review", "noise_count": "oops",
                      "specs_reviewed": 5,
                      "findings": [{"kind": "vacuous"}, {"kind": "duplicate"},
                                   {"kind": "missing"}, {"kind": "brittle"}]})
    # missing/brittle are real findings but are gaps, not noise
    assert critic.load(contract)["noise_count"] == 2


def test_malformed_findings_are_survived(contract):
    _write(contract, {"score": 0.7, "verdict": "review", "noise_count": 0,
                      "findings": ["a string", None, {"kind": "NOT_A_KIND"}, {}]})
    findings = critic.load(contract)["findings"]
    assert len(findings) == 2                      # the two dicts
    assert all(f["kind"] in critic.KINDS for f in findings)


def test_thresholds_come_from_config():
    cfg = {"enabled": True, "accept_threshold": 0.9, "review_threshold": 0.4}
    assert critic.verdict_for(0.85, cfg) == "review"
    assert critic.verdict_for(0.95, cfg) == "accept"
    assert critic.verdict_for(0.3, cfg) == "weak"


def test_config_defaults_when_org_config_is_unreadable(monkeypatch):
    monkeypatch.setattr(critic, "ROOT", pathlib.Path("/nonexistent-dir-xyz"))
    assert critic.config() == critic.DEFAULTS


def test_summary_line_is_informative(contract):
    _write(contract, {"score": 0.42, "verdict": "accept", "noise_count": 2,
                      "specs_reviewed": 5,
                      "findings": [{"kind": "vacuous", "severity": "high",
                                    "file": "a.spec.js", "note": "no assertion"}]})
    line = critic.summary_line(critic.load(contract))
    assert "0.42" in line and "weak" in line and "2/5" in line and "high-severity" in line


# ------------------------------------------------------------------ mock + wiring

def test_mock_phase_emits_a_valid_contract(tmp_path):
    import work_queue                       # plain "bash" hits WSL's System32 stub
    r = subprocess.run([work_queue.bash_exe(), str(ROOT / "engine/phases/mock_phase.sh"),
                        "critic", "PROJ-1", "workspace"], cwd=tmp_path,
                       capture_output=True, text=True, encoding="utf-8",
                       stdin=subprocess.DEVNULL,
                       env={**os.environ, "AIQE_MOCK_CRITIC_SCORE": "0.31"})
    assert r.returncode == 0, r.stderr
    sig = critic.load(tmp_path / "out/critic.contract.json")
    assert sig["score"] == 0.31 and sig["verdict"] == "weak"
    schema = json.load(open(ROOT / "engine/phases/contracts/critic.schema.json",
                            encoding="utf-8"))
    raw = json.load(open(tmp_path / "out/critic.contract.json", encoding="utf-8"))
    assert all(k in raw for k in schema["required"])


def test_prompt_forbids_authoring_and_is_data_framed():
    text = (ROOT / "prompts/critic.md").read_text(encoding="utf-8")
    assert "DATA" in text, "prompt is missing the data-not-instructions framing"
    assert "read-only" in text.lower()
    for kind in critic.KINDS:
        assert kind in text, f"prompt does not define the '{kind}' finding kind"
