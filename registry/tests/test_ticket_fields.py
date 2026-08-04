"""One parse of the ticket instead of five, with eval-safety pinned.

pipeline.sh's jira branch ran five `python3 -c` one-liners, each a ~200ms
interpreter start parsing the SAME out/ticket.json for one field. The
consolidation is only safe under two conditions, and these pin both:

  1. the values are byte-identical to the expressions they replaced — they feed
     resolve.py (routing) and the issue-guidance selection, so a drift here
     silently reroutes work or hands a security ticket story guidance;
  2. the emitted assignments are shlex-quoted, because pipeline.sh now `eval`s
     them and ticket text is untrusted JIRA data. The old $() capture did not
     need quoting; eval does, and a component name carrying `'; rm -rf` must
     stay a string.
"""
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import ticket_fields  # noqa: E402
import work_queue  # noqa: E402

OLD_EXPRS = {
    "AIQE_T_COMP": lambda t: ",".join(t.get("components", [])),
    "AIQE_T_LBL": lambda t: ",".join(t.get("labels", [])),
    "AIQE_T_LINKED": lambda t: ",".join(t.get("linked_repos", [])),
    "AIQE_T_FIXV": lambda t: ",".join(t.get("fix_versions", [])),
    "AIQE_T_ITYPE": lambda t: (t.get("issue_type") or "story").lower(),
}

CASES = [
    {"components": ["Checkout"], "labels": ["api-only"],
     "linked_repos": ["orders-api"], "fix_versions": ["2.4"],
     "issue_type": "Story"},
    {},                                                   # everything absent
    {"components": [], "labels": [], "issue_type": None}, # empty + null
    {"components": ["a b", "c'd"], "labels": ['x"y'],     # quoting hazards
     "linked_repos": ["r1", "r2"], "fix_versions": [], "issue_type": "BUG"},
]


@pytest.mark.parametrize("ticket", CASES)
def test_fields_match_the_one_liners_they_replaced(ticket):
    got = ticket_fields.fields(ticket)
    for k, fn in OLD_EXPRS.items():
        assert got[k] == fn(ticket), f"{k} drifted from the original expression"


def _eval_through_bash(ticket, tmp_path):
    """Exactly what pipeline.sh does: eval the emitted assignments in bash and
    read the bound values back."""
    p = tmp_path / "ticket.json"
    p.write_text(json.dumps(ticket), encoding="utf-8")
    script = (f'eval "$(python3 engine/lib/ticket_fields.py {json.dumps(str(p))})"\n'
              'printf "%s\\x1f" "$AIQE_T_COMP" "$AIQE_T_LBL" "$AIQE_T_LINKED" '
              '"$AIQE_T_FIXV" "$AIQE_T_ITYPE"')
    r = subprocess.run([work_queue.bash_exe(), "-c", script], cwd=ROOT,
                       capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       timeout=60)
    assert r.returncode == 0, r.stderr
    return dict(zip(OLD_EXPRS, r.stdout.split("\x1f")[:5]))


def test_hostile_ticket_text_survives_eval_as_data(tmp_path):
    """The reason quoting is not optional: these values are now eval'ed."""
    marker = tmp_path / "pwned"
    ticket = {"components": [f"evil'; touch {marker.as_posix()}; '"],
              "labels": ["$(id)"], "issue_type": "sec\nurity"}
    got = _eval_through_bash(ticket, tmp_path)
    assert not marker.exists(), "ticket text EXECUTED under eval — quoting failed"
    assert got["AIQE_T_COMP"] == OLD_EXPRS["AIQE_T_COMP"](ticket)
    assert got["AIQE_T_LBL"] == "$(id)", "command substitution ran instead of binding"
    assert got["AIQE_T_ITYPE"] == "sec\nurity", "an embedded newline broke the binding"


def test_a_normal_ticket_round_trips_through_bash(tmp_path):
    ticket = CASES[0]
    got = _eval_through_bash(ticket, tmp_path)
    for k, fn in OLD_EXPRS.items():
        assert got[k] == fn(ticket)


def test_an_unreadable_ticket_still_fails_the_run(tmp_path):
    """The one-liners crashed under set -e when the ticket was unreadable.
    Continuing on empty fields would route by guesswork — keep the crash."""
    r = subprocess.run([sys.executable, "engine/lib/ticket_fields.py",
                        str(tmp_path / "absent.json")], cwd=ROOT,
                       capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       timeout=60)
    assert r.returncode != 0
    assert "cannot read" in r.stderr


def test_pipeline_uses_the_consolidated_emitter():
    """The saving only exists while pipeline.sh calls it — and the five
    one-liners must not creep back beside it."""
    src = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    assert 'eval "$(python3 engine/lib/ticket_fields.py out/ticket.json)"' in src
    assert src.count("json.load(open('out/ticket.json'))") == 0, \
        "a per-field one-liner crept back beside the consolidated emitter"
