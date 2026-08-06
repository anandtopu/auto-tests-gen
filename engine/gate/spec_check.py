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


VALID_MODES = ("off", "warn", "strict")


def mode(warn=None):
    """The enforcement mode, and a COMPLAINT when the configured value was not
    one of the three.

    The fallback stays `off` — a gate must not refuse commits because its own
    setting is misspelled. But it can no longer be silent about it. `stict` used
    to resolve to `off` with no output anywhere, which is the worst shape this
    failure can take: the operator believes the gate is enforcing, the gate
    isn't, and both agree on the reported answer because both read the same
    unusable value.

    `warn` is the sink for the complaint (print, log) so this stays importable
    and testable without a side effect.
    """
    say = warn if warn is not None else (lambda m: print(m, file=sys.stderr))
    raw_env = os.environ.get("AIQE_SPEC_ENFORCE", "").strip()
    env = raw_env.lower()
    if env in VALID_MODES:
        return env
    if raw_env:
        say(f"[spec-check] AIQE_SPEC_ENFORCE={raw_env!r} is not one of "
            f"{list(VALID_MODES)} — IGNORING it and falling back to org-config. "
            f"If you meant to enforce, nothing here is enforcing.")
    try:
        import yaml
        cfg = yaml.safe_load(open(ROOT / "registry/org-config.yaml",
                                  encoding="utf-8")) or {}
        raw = (cfg.get("spec") or {}).get("enforce", "off")
        # YAML 1.1 turns a bare `off` into the BOOLEAN False (and `on` into
        # True), so the documented value `spec.enforce: off` never arrives as a
        # string. The old code fell through to "off" and was accidentally
        # right; anything that compared to the string would not have been.
        # False is the documented off. True is not a mode at all — `on` looks
        # like it should enable enforcement and silently would not.
        if raw is False:
            return "off"
        if raw is True:
            say("[spec-check] spec.enforce: on is not a mode (YAML reads it as "
                "true). Use 'warn' or 'strict' — QUOTED, or YAML will convert "
                "it. Treating as 'off': NOTHING is being enforced.")
            return "off"
        v = str(raw).lower()
        if v in VALID_MODES:
            return v
        say(f"[spec-check] spec.enforce={raw!r} in registry/org-config.yaml is "
            f"not one of {list(VALID_MODES)} — treating as 'off'. NOTHING is "
            f"being enforced.")
        return "off"
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
    # A run-level generate contract can contain tests from several fan-out
    # agents.  Repo metadata is authoritative when present, and the file must
    # still be in THIS gate's changed set: a stale contract entry is not proof
    # that this checkout implements anything.  Legacy/single-agent contracts
    # have no repo field, so changed-file membership is their repo identity.
    def belongs_to_this_gate(test):
        repo = test.get("repo")
        return (not repo or repo == test_repo) and test.get("file") in changed_set

    scoped_tests = [t for t in tests if belongs_to_this_gate(t)]

    # 1. forged/stale scenario ids on files this gate is about to commit
    for t in scoped_tests:
        sid = t.get("scenario_id")
        if sid and sid not in approved_ids:
            findings.append(f"UNAPPROVED_SCENARIO: {t.get('file')} claims "
                            f"'{sid}' which is not in the approved spec")
    # 2. coverage-or-waiver for every approved scenario (this repo's only)
    covered = {t.get("scenario_id") for t in scoped_tests}
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
