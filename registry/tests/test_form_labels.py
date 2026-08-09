"""Every form control the dashboard renders has an accessible name.

Found by driving the served page with a real browser: nine controls announced
to a screen reader as bare "edit text", "spin button" or "checkbox" with no
name (WCAG 4.1.2 Name/Role/Value). Eight were the alert-rule builder's row —
threshold, window, cooldown, channel, digest, enabled and two text fields — and
one was the plan editor's textarea.

Nothing was visually wrong, which is why no one noticed: a sighted user reads
the column header above the control. A screen reader reads the control alone.
The alert table is the worst case for this, because it is many identical
controls: "window (m)" announced eleven times identifies nothing, so the label
carries the RULE'S OWN NAME ("window in minutes — gate refusals"), which is
what a user recognises.

These assert on the SOURCE because rendering the page here is what made an
earlier UI test flaky (it writes reports/dashboard.html and raced other tests
on Windows). The behavioural proof — 0 unlabeled controls page-wide, labels
reading naturally, no page errors — was taken against the live server and is
recorded in the commit.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")


def _al_row():
    start = SRC.index("function alRow(")
    return SRC[start:SRC.index("\n}", start)]


# Every data-f field in the alert row, and the label wording it must carry.
# Wording mirrors the column headers so header and label cannot drift apart.
EXPECTED = {
    "name": "rule name", "kinds": "kinds", "outcome": "outcome",
    "target_contains": "target has", "threshold": "N (threshold)",
    "window_minutes": "window in minutes", "cooldown_minutes": "cooldown in minutes",
    "channel": "channel", "recipients": "recipients (to)",
    "digest": "digest", "enabled": "enabled",
}


def test_every_alert_row_control_gets_an_accessible_name():
    row = _al_row()
    fields = set(re.findall(r'data-f="(\w+)"', row))
    assert fields == set(EXPECTED), (
        f"the alert row's fields changed: {fields ^ set(EXPECTED)} — "
        "add the new one to EXPECTED with its label, or it ships unnamed")
    for field, label in EXPECTED.items():
        assert f"lbl('{label}')" in row, \
            f"control '{field}' lost its accessible name (expected lbl('{label}'))"


def test_the_label_helper_disambiguates_by_rule_name():
    """Many identical controls in one table: the field name alone is not enough.

    The first version of this test asserted `rn` was DEFINED and that escaping
    appeared somewhere nearby — both true even when the helper stopped
    interpolating `rn` at all. A mutation that dropped the rule name from the
    label survived it. Assert on the helper's own expression instead: what it
    BUILDS, not what happens to be in scope around it.
    """
    row = _al_row()
    assert "const lbl = t =>" in row, "the label helper is gone"
    body = row.split("const lbl = t =>", 1)[1].split(";", 1)[0]
    assert "rn" in body, (
        "the label no longer interpolates the rule name — 'window in minutes' "
        "repeated for every rule identifies none of them")
    assert "escHtml(" in body, \
        "the aria-label value is not escaped; a rule named with a quote breaks the attribute"
    assert "aria-label" in body, "the helper no longer emits an aria-label attribute"
    # The fallback matters: a brand-new row has no name yet, and an aria-label
    # ending in a bare em-dash is worse than the field name alone.
    assert "unnamed rule" in row, "an unnamed rule's controls lose their disambiguator"


def test_the_plan_editor_textarea_is_named():
    assert re.search(r'id="plan-text"[^>]*\n?\s*aria-label=', SRC), \
        "the plan editor textarea lost its accessible name"


def test_no_control_in_the_source_is_left_without_any_naming_mechanism():
    """A coarse sweep for controls rendered with neither placeholder, aria-label,
    nor an id a <label for=> could point at — the shape that produced all nine."""
    bad = []
    for m in re.finditer(r"<(input|select|textarea)\b([^>]*)>", SRC):
        attrs = m.group(2)
        if 'type="hidden"' in attrs or "data-f=" in attrs:   # data-f rows are covered above
            continue
        if ("aria-label" in attrs or "placeholder" in attrs
                or re.search(r'id="[\w-]+"', attrs) or "' + " in attrs):
            continue
        bad.append(m.group(0)[:70])
    assert not bad, f"controls with no naming mechanism at all: {bad}"
