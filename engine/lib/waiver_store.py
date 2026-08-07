"""Waivers as a first-class object (SDD adoption S4).

A waiver says: this approved scenario has no test, and we are shipping anyway.
That is a legitimate thing to need and a dangerous thing to make easy. The gate
already REFUSES an uncovered scenario without a non-expired waiver
(`engine/gate/spec_check.py`), and `spec_store.load_waivers` already reports
expiry honestly. What was missing was any way to create one except editing YAML
by hand — so in practice the escape hatch was either unavailable or, once
someone learned the file format, unaccountable.

Three rules are enforced HERE, at creation, rather than only at the gate:

**A waiver without a reason is not a waiver.** `reason` and `by` are required.
A blank-reason bypass is indistinguishable from an accident six months later,
when the person who added it has forgotten and the person reading it never knew.

**Every waiver expires, and the expiry is capped.** An unbounded expiry is a
permanent bypass wearing a disguise: `expires: 2099-01-01` passes every check
the gate makes while meaning "never". MAX_DAYS makes the lie impossible to tell
in one step — renewing is cheap, and renewing REPEATEDLY is a visible pattern
that someone can act on.

**Nothing is deleted silently.** Removing a waiver is an explicit call and is
recorded; an expired waiver stays visible rather than vanishing, because "this
was waived and lapsed" is exactly what a reviewer needs to see.
"""
import datetime
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app_paths                      # R12: mutable paths resolve here
import fs_lock
import spec_store

ROOT = app_paths.ROOT

# The longest a single waiver may run. Deliberately shorter than a release
# cycle: the point is that somebody looks again, not that the exception is
# convenient. Renewal is one call; what it is not is invisible.
MAX_DAYS = 90
# Waivers inside this window are surfaced on the Overview — a waiver that
# lapses unnoticed turns into a gate refusal nobody expected.
EXPIRING_SOON_DAYS = 14


def _today():
    return datetime.date.today()


def path(key):
    return spec_store.waivers_path(key)


def validate_key(key):
    """Expose the spec store's containment rule to API callers."""
    return spec_store.validate_key(key)


def list_for(key):
    """Every waiver for one ticket, annotated with days remaining.

    Expired waivers are INCLUDED. Hiding them would make a lapsed exception look
    like it never existed, when it is the most interesting row on the page.
    """
    out = []
    for sid, w in (spec_store.load_waivers(key) or {}).items():
        exp = str(w.get("expires") or "")
        days = None
        if exp:
            try:
                days = (datetime.date.fromisoformat(exp) - _today()).days
            except ValueError:
                days = None
        out.append({
            "scenario": sid,
            "reason": w.get("reason") or "",
            "by": w.get("by") or "",
            "expires": exp,
            "expired": bool(w.get("expired")),
            "days_left": days,
            "expiring_soon": days is not None and 0 <= days <= EXPIRING_SOON_DAYS,
            # Inert: a spec exists for this key and has no such scenario.
            "unmatched": unmatched(key, sid),
        })
    return sorted(out, key=lambda r: (not r["expired"], r["expires"] or "9999"))


def spec_scenarios(key):
    """Scenario ids in the ticket's SIGNED spec, or None when there is no spec.

    None and empty-set mean different things and must not be conflated: no spec
    yet is normal (waiving during planning is legitimate), while a spec with no
    matching id means the waiver protects nothing.
    """
    try:
        doc = spec_store.load(key) or {}
    except Exception:                          # noqa: BLE001
        return None
    if not doc:
        return None
    ids = {str(sc.get("id") or "").strip()
           for sc in (doc.get("scenarios") or []) if isinstance(sc, dict)}
    ids.discard("")
    return ids or None


def unmatched(key, scenario):
    """True when a spec exists for `key` and `scenario` is not in it.

    A waiver whose scenario id matches nothing is INERT: the gate keeps refusing
    the scenario the author meant to waive, while the UI shows a healthy waiver
    with days remaining. That is the same failure the alert rules already guard
    against by reporting unknown kinds — configured-looking and doing nothing —
    and it is worse here, because the thing it silently fails to do is let a
    release through.

    Deliberately NOT a refusal. A waiver may legitimately be written before the
    plan is authored, and refusing would block that. It is reported instead, and
    surfaced everywhere the expired state is.
    """
    ids = spec_scenarios(key)
    return bool(ids) and str(scenario or "").strip() not in ids


def validate(scenario, reason, by, expires):
    """(normalized, problems). Problems are returned, not raised — the UI must
    show WHY a waiver was refused, and 'invalid' is not a reason."""
    problems = []
    scenario = str(scenario or "").strip()
    reason = str(reason or "").strip()
    by = str(by or "").strip()
    expires = str(expires or "").strip()

    if not scenario:
        problems.append("scenario id is required")
    if len(reason) < 10:
        problems.append("a reason of at least 10 characters is required — "
                        "'why is this shipping uncovered?' must be answerable "
                        "by someone reading it in six months")
    if not by:
        problems.append("an owner is required: a waiver nobody owns is nobody's "
                        "job to remove")
    if not expires:
        problems.append(f"an expiry is required (max {MAX_DAYS} days) — an "
                        f"unbounded waiver is a permanent bypass in disguise")
    else:
        try:
            d = datetime.date.fromisoformat(expires)
        except ValueError:
            problems.append(f"expiry {expires!r} is not an ISO date (YYYY-MM-DD)")
        else:
            if d <= _today():
                problems.append("expiry must be in the future")
            elif (d - _today()).days > MAX_DAYS:
                problems.append(
                    f"expiry is more than {MAX_DAYS} days away. Renew instead — "
                    f"repeated renewal is a visible pattern, a two-year waiver "
                    f"is not")
    return {"scenario": scenario, "reason": reason, "by": by,
            "expires": expires}, problems


def save(key, scenario, reason, by, expires):
    """Create or replace one waiver. Returns (record, problems); on any problem
    NOTHING is written."""
    rec, problems = validate(scenario, reason, by, expires)
    if problems:
        return rec, problems
    p = path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    import yaml
    with fs_lock.lock(p):
        doc = {}
        if p.exists():
            try:
                doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception:                  # noqa: BLE001
                doc = {}
        waivers = [w for w in (doc.get("waivers") or [])
                   if isinstance(w, dict) and w.get("scenario") != rec["scenario"]]
        waivers.append({"scenario": rec["scenario"], "reason": rec["reason"],
                        "by": rec["by"], "expires": rec["expires"]})
        # Quoted expiry: unquoted `2099-01-01` parses back as a datetime.date,
        # which is not JSON-serializable and already broke /api/plans/one once.
        p.write_text(yaml.safe_dump({"waivers": waivers}, sort_keys=False,
                                    default_flow_style=False),
                     encoding="utf-8", newline="\n")
    return rec, []


def remove(key, scenario):
    """Explicit removal. Returns True when something was removed."""
    p = path(key)
    if not p.exists():
        return False
    import yaml
    with fs_lock.lock(p):
        try:
            doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:                      # noqa: BLE001
            return False
        before = doc.get("waivers") or []
        after = [w for w in before
                 if isinstance(w, dict) and w.get("scenario") != scenario]
        if len(after) == len(before):
            return False
        p.write_text(yaml.safe_dump({"waivers": after}, sort_keys=False,
                                    default_flow_style=False),
                     encoding="utf-8", newline="\n")
    return True


def attention():
    """Waivers that need someone to look: expired, or expiring soon.

    Surfaced on the Overview because the failure mode is silence — a waiver
    lapses, and the next run meets a gate refusal nobody was expecting.
    """
    d = app_paths.specs_dir()
    if not d.is_dir():
        return {"expired": [], "expiring_soon": [], "unmatched": []}
    expired, soon, inert = [], [], []
    for sub in d.iterdir():
        if not sub.is_dir() or sub.name == "platform":
            continue
        for w in list_for(sub.name):
            row = dict(w, key=sub.name)
            # Checked FIRST and independently of expiry: an unmatched waiver is
            # broken whether or not it has time left, and reporting only the
            # expiry would hide the reason it never worked.
            if w.get("unmatched"):
                inert.append(row)
            if w["expired"]:
                expired.append(row)
            elif w["expiring_soon"]:
                soon.append(row)
    return {"expired": expired, "expiring_soon": soon, "unmatched": inert}


if __name__ == "__main__":
    import json
    sys.stdout.reconfigure(encoding="utf-8")
    argv = sys.argv[1:]
    if argv and argv[0] == "attention":
        print(json.dumps(attention(), indent=1))
    elif argv:
        print(json.dumps(list_for(argv[0]), indent=1))
    else:
        a = attention()
        print(f"expired: {len(a['expired'])}  expiring soon: {len(a['expiring_soon'])}")
        for r in a["expired"] + a["expiring_soon"]:
            print(f"  {r['key']:12} {r['scenario']:16} {r['expires']}  {r['reason'][:40]}")
