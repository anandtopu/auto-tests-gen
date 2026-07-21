#!/usr/bin/env python3
"""Export the generated test plan for a JIRA ticket as shareable Markdown or
standalone HTML — the plan file (testplans/<KEY>.md) enriched with everything a
stakeholder needs: release/review status, scenarios, canonical test data, the
generated tests with validation results, and where the commits landed.

Used by bin/qa.py export-plan and the dashboard server's download endpoint.
"""
import glob, html, json, pathlib, re, sys, time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import review_state

FORMATS = ("md", "html")


def _latest_run(key):
    runs = []
    for f in glob.glob(str(ROOT / "reports/runs/*.json")):
        if pathlib.Path(f).name in ("reviews.json", "queue.json"):
            continue
        try:
            r = json.load(open(f, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if r.get("trigger", {}).get("key") == key:
            runs.append(r)
    return max(runs, key=lambda r: r.get("ts", 0)) if runs else None


def available_plans():
    return sorted(p.stem for p in (ROOT / "testplans").glob("*.md"))


def build(key):
    """Collect everything for the export; raises SystemExit with a helpful message."""
    plan_file = ROOT / f"testplans/{key}.md"
    if not plan_file.exists():
        sys.exit(f"no test plan for '{key}' (plans exist for: "
                 f"{', '.join(available_plans()) or 'none'})")
    run = _latest_run(key) or {}
    contracts = {p["name"]: p["contract"] for p in run.get("phases", [])}
    rev = review_state.load().get(key, {})
    data_dir = ROOT / f"testdata/{key}"
    return {
        "key": key,
        "plan_md": plan_file.read_text(encoding="utf-8").strip(),
        "run": run,
        "scenarios": contracts.get("testplan", {}).get("scenarios", []),
        "tests": contracts.get("generate", {}).get("tests", []),
        "open_questions": (contracts.get("generate", {}).get("open_questions")
                           or contracts.get("testplan", {}).get("open_questions", [])),
        "validate": contracts.get("validate", {}),
        "gates": run.get("gates", []),
        "review": rev,
        "data_files": sorted(f"testdata/{key}/{p.relative_to(data_dir).as_posix()}"
                             for p in data_dir.rglob("*") if p.is_file())
                      if data_dir.exists() else [],
    }


def to_markdown(key):
    b = build(key)
    rev, v = b["review"], b["validate"]
    lines = [f"# Test Plan Export — {b['key']}", ""]
    meta = [f"- Exported: {time.strftime('%Y-%m-%d %H:%M')}"]
    if b["run"]:
        meta.append(f"- Pipeline run: `{b['run']['run_id']}` ({b['run'].get('overall', '?')})")
    if rev.get("release"):
        meta.append(f"- Target release: **{rev['release']}**")
    if rev.get("status"):
        meta.append(f"- Team review: **{rev['status']}**"
                    + (f" (by {rev['reviewer']})" if rev.get("reviewer") else ""))
    lines += meta + ["", "---", "", b["plan_md"], ""]
    if b["data_files"]:
        lines += ["## Canonical test data", ""] + [f"- `{f}`" for f in b["data_files"]] + [""]
    if b["tests"]:
        lines += ["## Generated tests", "",
                  "| file | test | action |", "|---|---|---|"]
        lines += [f"| `{t['file']}` | {t.get('name', '')} | {t.get('action', '')} |"
                  for t in b["tests"]] + [""]
    if v:
        lines += ["## Validation", "",
                  f"{v.get('passed', '?')} passed, {v.get('failed', '?')} failed, "
                  f"{v.get('repair_loops', '?')} repair loop(s)", ""]
    committed = [g for g in b["gates"] if g.get("commit")]
    if committed:
        lines += ["## Commits", ""] + [
            f"- `{g['test_repo']}` @ `{g['commit']}` (branch `test/{key}-ai-qe`)"
            for g in committed] + [""]
    if b["open_questions"]:
        lines += ["## Open questions", ""] + [f"- {q}" for q in b["open_questions"]] + [""]
    return "\n".join(lines)


def _md_to_html(md):
    """Minimal markdown renderer (headings, tables, lists, hr, code spans, bold)."""
    def inline(s):
        s = html.escape(s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        return s
    out, in_ul, in_table = [], False, False
    for line in md.splitlines():
        s = line.strip()
        if in_ul and not s.startswith("- "):
            out.append("</ul>"); in_ul = False
        if in_table and not s.startswith("|"):
            out.append("</table>"); in_table = False
        if not s:
            continue
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.fullmatch(r":?-+:?", c) for c in cells):
                continue                                   # separator row
            tag = "th" if not in_table else "td"
            if not in_table:
                out.append("<table>"); in_table = True
            out.append("<tr>" + "".join(f"<{tag}>{inline(c)}</{tag}>" for c in cells) + "</tr>")
        elif s.startswith("#"):
            n = len(s) - len(s.lstrip("#"))
            out.append(f"<h{min(n, 4)}>{inline(s.lstrip('# '))}</h{min(n, 4)}>")
        elif s.startswith("- "):
            if not in_ul:
                out.append("<ul>"); in_ul = True
            out.append(f"<li>{inline(s[2:])}</li>")
        elif s in ("---", "***"):
            out.append("<hr>")
        else:
            out.append(f"<p>{inline(s)}</p>")
    if in_ul:
        out.append("</ul>")
    if in_table:
        out.append("</table>")
    return "\n".join(out)


def to_html(key):
    body = _md_to_html(to_markdown(key))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Test Plan — {html.escape(key)}</title>
<style>
:root {{ --bg:#fff; --ink:#1a1a19; --ink2:#5f5e5b; --line:#e4e2de; --card:#f7f6f4 }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#1a1a19; --ink:#f0efec; --ink2:#a5a39e; --line:#3a3936; --card:#242422 }} }}
body {{ margin:0 auto; max-width:860px; padding:32px 24px; background:var(--bg);
       color:var(--ink); font:15px/1.6 ui-sans-serif,system-ui,sans-serif }}
h1 {{ font-size:22px }} h2 {{ font-size:17px; margin-top:28px }} h3 {{ font-size:15px }}
code {{ background:var(--card); padding:1px 5px; border-radius:4px; font-size:13px }}
table {{ border-collapse:collapse; width:100%; font-size:14px; margin:10px 0 }}
th,td {{ text-align:left; padding:6px 10px; border-bottom:1px solid var(--line) }}
th {{ color:var(--ink2); font-weight:600 }}
hr {{ border:none; border-top:1px solid var(--line); margin:20px 0 }}
ul {{ padding-left:22px }}
</style></head><body>
{body}
</body></html>
"""


def export(key, fmt="md", out=None):
    if fmt not in FORMATS:
        sys.exit(f"format must be one of: {', '.join(FORMATS)}")
    content = to_markdown(key) if fmt == "md" else to_html(key)
    path = pathlib.Path(out) if out else ROOT / f"reports/exports/{key}-testplan.{fmt}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    p = export(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "md",
               sys.argv[3] if len(sys.argv) > 3 else None)
    print(f"exported: {p}")
