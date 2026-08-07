"""PRD v2 B5: reviewer quality tier, envelope headroom, and panel deferral."""

import copy
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import budget  # noqa: E402
import cost_report  # noqa: E402
import work_queue  # noqa: E402


def _config():
    return yaml.safe_load(
        (ROOT / "registry/org-config.yaml").read_text(encoding="utf-8")
    )


def test_reviewer_is_judgement_tier_and_never_degrades():
    cfg = _config()
    assert cfg["models"]["reviewer"] == "claude-sonnet-4-6"
    assert cfg["models"]["reviewrepair"] == "claude-sonnet-4-6"

    source = (ROOT / "engine/phases/run_phase.sh").read_text(encoding="utf-8")
    import re
    cheap = re.search(r'case "\$PHASE" in\s*\n\s*([^)]*)\)', source)
    assert cheap, "the degradation phase allow-list disappeared"
    for phase in ("reviewer", "reviewrepair"):
        assert phase not in cheap.group(1), (
            f"{phase} is judgement work and must never use the cheap tier"
        )


def test_effective_envelopes_add_review_headroom_only_when_review_runs(monkeypatch):
    cfg = _config()
    monkeypatch.delenv("AIQE_TEST_REVIEWER", raising=False)

    enabled = copy.deepcopy(cfg)
    enabled["review"]["enabled"] = True
    enabled["review"]["agent_gate"] = "warn"
    assert budget.workflow_envelope("pr", enabled) == (2.25, 1.50, 0.75)
    assert budget.workflow_envelope("jira", enabled) == (4.75, 4.00, 0.75)
    assert budget.workflow_envelope("tests", enabled) == (3.75, 3.00, 0.75)
    assert budget.workflow_envelope("plan", enabled) == (1.00, 1.00, 0.00)

    disabled = copy.deepcopy(enabled)
    disabled["review"]["enabled"] = False
    assert budget.workflow_envelope("pr", disabled) == (1.50, 1.50, 0.00)


def test_review_policy_precedence_also_governs_envelope_uplift(monkeypatch):
    cfg = _config()
    cfg["review"]["enabled"] = False

    cfg["review"]["agent_gate"] = "require"
    monkeypatch.setenv("AIQE_TEST_REVIEWER", "0")
    assert budget.workflow_envelope("pr", cfg) == (2.25, 1.50, 0.75), (
        "require forces the reviewer and must reserve its planning headroom"
    )

    cfg["review"]["agent_gate"] = "off"
    monkeypatch.setenv("AIQE_TEST_REVIEWER", "1")
    assert budget.workflow_envelope("pr", cfg) == (1.50, 1.50, 0.00), (
        "off suppresses both the reviewer call and its envelope uplift"
    )


def test_boolean_or_negative_money_values_never_become_dollar_limits(monkeypatch):
    cfg = _config()
    cfg["review"]["enabled"] = True
    monkeypatch.delenv("AIQE_TEST_REVIEWER", raising=False)

    cfg["budgets"]["review_uplift_usd"]["pr"] = True
    assert budget.workflow_envelope("pr", cfg) == (1.50, 1.50, 0.00)
    cfg["budgets"]["review_uplift_usd"]["pr"] = -2
    assert budget.workflow_envelope("pr", cfg) == (1.50, 1.50, 0.00)
    cfg["budgets"]["envelopes"]["pr"] = True
    assert budget.workflow_envelope("pr", cfg) == (0.00, 0.00, 0.00)


def test_explicit_cost_limit_still_beats_review_uplift(monkeypatch):
    monkeypatch.setenv("AIQE_RUN_MODE", "pr")
    monkeypatch.setenv("AIQE_TEST_REVIEWER", "1")
    monkeypatch.setenv("MAX_COST_USD_PER_RUN", "9.5")
    limit, _, source = budget.limits()
    assert (limit, source) == (9.5, "MAX_COST_USD_PER_RUN")


def test_queue_warning_uses_and_explains_the_effective_envelope(monkeypatch):
    monkeypatch.setenv("AIQE_TEST_REVIEWER", "1")
    monkeypatch.setattr(
        cost_report, "report",
        lambda days=None: {
            "by_key_top10": [{"key": "PR-orders-api-9", "runs": 2,
                              "cost_usd": 2.00}]
        },
    )
    assert work_queue._envelope_warning("pr", "orders-api", "9") == "", (
        "history below the review-adjusted cap must not warn against the old cap"
    )

    monkeypatch.setattr(
        cost_report, "report",
        lambda days=None: {
            "by_key_top10": [{"key": "PR-orders-api-9", "runs": 2,
                              "cost_usd": 2.50}]
        },
    )
    warning = work_queue._envelope_warning("pr", "orders-api", "9")
    assert "$2.25" in warning
    assert "$1.50 base + $0.75 agent-review uplift" in warning


def test_reviewer_panel_is_deferred_until_a_real_quarter_sets_the_threshold():
    cfg = _config()
    panel = cfg["review"]["panel"]
    assert panel["status"] == "deferred"
    assert panel["enabled"] is False
    assert panel["trigger"] == {
        "metric": "reviewer_escape_rate",
        "observation_days": 90,
        "threshold": None,
    }
    assert "reviewerpanel" not in cfg["models"]
    assert "reviewerpanel" not in cfg["phases"]
