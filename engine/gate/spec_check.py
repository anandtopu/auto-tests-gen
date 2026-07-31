#!/usr/bin/env python3
"""Spec-satisfaction gate check (SDD story 3.2).

Deterministic, read-only. For a run whose key carries an APPROVED structured
spec, verifies the signed contract is being HONOURED, not just reported:

  1. every changed spec file claiming a scenario_id (via the generate
     contract) resolves to a scenario in the approved spec — a forged or
     stale id is a violation;
  2. every approved scenario is covered by a generated/updated test in this
     run's contract, or carries a NON-EXPIRED waiver.

Modes (org-config `spec.enforce`): off = check absent; warn = findings print,
exit 0; strict = exit 8 (a NEW gate code — distinct from 2..7). Exemptions by
construction: no structured spec for the key (PR-path or free-form plans) or a
spec not APPROVED yet -> exit 0 silently. The check ADDS a verdict, never a
writer.

Usage (gate.sh): spec_check.py <KEY> <test_repo> <changed_files_file>
"""
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(os.environ.get("AIQE_ROOT") or
                    pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, str(ROOT / "engine/lib"))

EXIT_SPEC = 8


def mode():
    env = os.environ.get("AIQE_SPEC_ENFORCE", "").strip().lower()
    if env in ("off", "warn", "strict"):
        return env
    try:
        import yaml
        cfg = yaml.safe_load(open(ROOT / "registry/org-config.yaml",
                                  encoding="utf-8")) or {}
        v = str((cfg.get("spec") or {}).get("enforce", "off")).lower()
        return v if v in ("warn", "strict") else "off"
    except Exception:
        return "off"


def check(key, test_repo, changed):
    """(findings, exempt). Total: any failure to read state = exempt — the
    spec check must never break a gate on its own malfunction."""
    try:
        import plan_state
        import spec_store
        spec = spec_store.load(key)
        if not spec:
            return [], True                      # free-form / PR-path: exempt
        if plan_state.get(key).get("status") != "approved":
            return [], True                      # nothing signed to enforce yet
        approved_ids = {s["id"] for s in spec.get("scenarios", [])
                        if isinstance(s, dict) and s.get("id")}
        waivers = spec_store.load_waivers(key)
    except Exception:
        return [], True

    tests = []
    try:
        c = json.load(open(ROOT / "out/generate.contract.json",
                           encoding="utf-8"))
        tests = [t for t in c.get("tests") or [] if isinstance(t, dict)]
    except Exception:
        pass

    findings = []
    changed_set = set(changed)
    # 1. forged/stale scenario ids on files this gate is about to commit
    for t in tests:
        sid = t.get("scenario_id")
        if t.get("file") in changed_set and sid and sid not in approved_ids:
            findings.append(f"UNAPPROVED_SCENARIO: {t.get('file')} claims "
                            f"'{sid}' which is not in the approved spec")
    # 2. coverage-or-waiver for every approved scenario (this repo's only)
    covered = {t.get("scenario_id") for t in tests}
    this_repo = {s["id"] for s in spec.get("scenarios", [])
                 if isinstance(s, dict) and s.get("target_repo") == test_repo}
    for sid in sorted(this_repo):
        if sid in covered:
            continue
        w = waivers.get(sid)
        if w and not w.get("expired"):
            continue
        if w and w.get("expired"):
            findings.append(f"EXPIRED_WAIVER: {sid} ({w.get('reason', '')}, "
                            f"expired {w.get('expires')}) — renew or cover")
        else:
            findings.append(f"UNCOVERED_SCENARIO: {sid} is approved but no "
                            f"test in this run covers it (waive with a reason "
                            f"in specs/{key}/waivers.yaml, or cover it)")
    return findings, False


def main(argv):
    m = mode()
    if m == "off":
        return 0
    key, test_repo = argv[1], argv[2]
    changed = []
    if len(argv) > 3 and pathlib.Path(argv[3]).exists():
        changed = [l.strip() for l in open(argv[3], encoding="utf-8")
                   if l.strip()]
    findings, exempt = check(key, test_repo, changed)
    if exempt or not findings:
        return 0
    for f in findings:
        print(f"SPEC_{'VIOLATION' if m == 'strict' else 'WARNING'}: {f}")
    if m == "strict":
        print(f"SPEC_UNSATISFIED: {len(findings)} finding(s) — the approved "
              f"spec is a signed contract; cover, waive, or re-approve")
        return EXIT_SPEC
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
