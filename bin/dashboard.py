#!/usr/bin/env python3
"""Generate reports/dashboard.html — the QA operations dashboard.

Implements the "QA Dashboard" Claude Design (project: QA Dashboard UI redesign):
sidebar navigation over six views (Overview, Intake & queue, Runs & reviews,
Artifacts, Test catalog, Settings), SentinelRAG design tokens (light + dark), semantic
status chips, a needs-attention feed, and toast feedback. Self-contained HTML,
server-rendered from real state; interactive actions light up when served by
bin/dashboard_server.py (make serve). Regenerate: make dashboard.
"""
import glob, html, json, pathlib, sys, time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine/lib"))
from registry import load_registry
import review_state, test_health, work_queue

esc = html.escape

# ---------------------------------------------------------------- data loading
runs = []
for f in glob.glob(str(ROOT / "reports/runs/*.json")):
    if pathlib.Path(f).name in ("reviews.json", "queue.json", "hooks-seen.json"):
        continue
    try:
        runs.append(json.load(open(f, encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        pass
runs.sort(key=lambda r: r.get("ts", 0), reverse=True)

catalog = []
for f in sorted(glob.glob(str(ROOT / "catalog/*.jsonl"))):
    if pathlib.Path(f).name == "catalog.sample.jsonl":
        continue
    for line in open(f, encoding="utf-8"):
        if line.strip():
            catalog.append(json.loads(line))

reg = load_registry()
sources = [s["name"] for s in reg["source_repositories"]]
trepos = reg["test_repositories"]
reviews = review_state.load()
health = test_health.load()
queue = work_queue.load()

# ---------------------------------------------------------------- aggregates
n_committed = sum(1 for r in runs if r.get("overall") == "committed")
n_quar = sum(1 for r in runs if r.get("overall") == "quarantined")
by_status = {}
for e in catalog:
    by_status[e["mapping"]["status"]] = by_status.get(e["mapping"]["status"], 0) + 1
mapped = by_status.get("auto", 0) + by_status.get("confirmed", 0)
counts = {}
for e in catalog:
    if e["mapping"]["status"] in ("confirmed", "auto"):
        for app in e["mapping"]["app_repos"]:
            counts[(app, e["test_repo"])] = counts.get((app, e["test_repo"]), 0) + 1
uncovered = [s for s in sources
             if not any(counts.get((s, t["name"])) or s in t.get("covers", []) for t in trepos)]
pending_review_keys = sorted(k for k, v in reviews.items()
                             if v.get("status") in ("pending_review", "in_review"))
orphans = [e for e in catalog if e["mapping"]["status"] == "orphan"]
releases = sorted({e["release"] for e in reviews.values() if e.get("release")})

CHIP = {  # status -> (label, css class)
    "committed":   ("✓ committed", "success"),
    "no_changes":  ("no changes", "muted"),
    "quarantined": ("⚠ quarantined", "danger"),
    "pending_review":    ("✎ awaiting review", "warning"),
    "in_review":         ("✎ in review", "warning"),
    "approved":          ("✓ approved", "success"),
    "changes_requested": ("✗ changes requested", "danger"),
    "queued":  ("queued", "info"), "running": ("● running", "warning"),
    "done":    ("✓ done", "success"), "failed": ("✗ failed", "danger"),
    "auto":    ("✓ auto", "success"), "confirmed": ("✓ confirmed", "info"),
    "needs_review": ("? needs review", "warning"), "orphan": ("⚠ orphan", "danger"),
    "covered": ("covered", "success"), "gap": ("no coverage", "danger"),
}


def chip(status, extra=""):
    label, cls = CHIP.get(status, (status or "—", "muted"))
    return f'<span class="chip chip-{cls}">{esc(label)}{esc(extra)}</span>'


def review_of(key):
    return reviews.get(key, {})


# ---------------------------------------------------------------- overview view
tiles = [
    (len(runs), "pipeline runs", "runs", False),
    (n_committed, "runs committed", "runs", False),
    (n_quar, "runs quarantined", "runs", n_quar > 0),
    (len(catalog), "tests cataloged", "catalog", False),
    (mapped, "mapped (auto + confirmed)", "catalog", False),
    (len(orphans), "orphan tests", "catalog", len(orphans) > 0),
    (len(uncovered), "uncovered app repos", "overview", len(uncovered) > 0),
    (len(pending_review_keys), "awaiting team review", "runs", len(pending_review_keys) > 0),
]
tiles_html = "".join(
    f'<button class="tile" data-go="{view}">'
    f'<span class="tile-v{" alert" if alert else ""}">{value}</span>'
    f'<span class="tile-l">{esc(label)}</span></button>'
    for value, label, view, alert in tiles)

attention = []
quarantined_runs = [r for r in runs if r.get("overall") == "quarantined"][:3]
for r in quarantined_runs:
    attention.append(("quarantined", "danger",
                      f"{r['trigger']['key']} was quarantined by the gate — generated "
                      f"tests failed validation and were not pushed.", "Inspect run", "runs"))
if pending_review_keys:
    attention.append(("review", "warning",
                      f"{len(pending_review_keys)} key(s) committed AI-generated tests "
                      f"awaiting team review: {', '.join(pending_review_keys[:4])}"
                      + ("…" if len(pending_review_keys) > 4 else ""), "Review board", "runs"))
if uncovered:
    attention.append(("coverage", "danger",
                      f"{', '.join(uncovered)} have no E2E coverage at all.",
                      "See matrix", "overview"))
for e in orphans[:2]:
    attention.append(("orphan", "warning",
                      f"{e['file']} maps to no app repo — confirm a mapping or retire it.",
                      "Open catalog", "catalog"))
attention_html = "".join(
    f'<button class="attn" data-go="{view}">'
    f'<span class="chip chip-{cls}">{esc(tag)}</span>'
    f'<span class="attn-text">{esc(text)}</span>'
    f'<span class="attn-act">{esc(action)} →</span></button>'
    for tag, cls, text, action, view in attention) or \
    '<div class="empty">Nothing needs attention — all clear.</div>'

matrix_head = "".join(f'<th class="c">{esc(t["name"])}</th>' for t in trepos)
matrix_rows = ""
for s in sources:
    is_gap = s in uncovered
    cells = ""
    for t in trepos:
        n = counts.get((s, t["name"]), 0)
        cells += (f'<td class="c num">{n}</td>' if n
                  else f'<td class="c num {"dim" if s not in t.get("covers", []) else "cov"}">'
                       f'{"·" if s not in t.get("covers", []) else "✓"}</td>')
    matrix_rows += (f'<tr><th class="mono{" gap" if is_gap else ""}">{esc(s)}</th>{cells}'
                    f'<td>{chip("gap" if is_gap else "covered")}</td></tr>')

# ---------------------------------------------------------------- runs view
release_opts = "".join(f"<option>{esc(v)}</option>" for v in releases)
runs_rows = ""
for r in runs[:25]:
    key = r["trigger"]["key"]
    rev = review_of(key)
    rstat = rev.get("status") or ""
    release = rev.get("release", "")
    ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(r.get("ts", 0)))
    repo_stack = ""
    for g in r.get("gates", []):
        sha = (f' <a class="mono sm" href="runs/'
               f'{esc(pathlib.PurePosixPath(g["diff"]).name)}">{esc((g.get("commit") or "")[:7])} · diff</a>'
               if g.get("diff") else
               (f' <span class="mono sm muted">{esc((g.get("commit") or "")[:7])}</span>'
                if g.get("commit") else ""))
        log = (f' <a class="sm" href="{esc(pathlib.PurePosixPath(g["log"]).name)}">log</a>'
               if g.get("status") == "quarantined" and g.get("log") else "")
        repo_stack += (f'<div class="gate-line"><span class="mono sm repo">'
                       f'{esc(g["test_repo"])}</span>{chip(g["status"])}{sha}{log}</div>')
    review_cell = chip(rstat) if rstat else '<span class="chip chip-muted">—</span>'
    if rstat in ("pending_review", "in_review"):
        review_cell += (f' <button class="btn btn-sm approve" data-key="{esc(key)}">'
                        f'Approve</button>')
    runs_rows += (
        f'<tr data-release="{esc(release)}" data-review="{esc(rstat)}">'
        f'<td><div class="strong">{esc(key)}</div>'
        f'<div class="mono sm muted">{esc(r["run_id"])}</div></td>'
        f'<td><span class="pill">{esc(r["trigger"]["type"])}</span></td>'
        f'<td class="muted nowrap">{ts}</td>'
        f'<td>{chip(r.get("overall", "?"))}</td>'
        f'<td class="mono sm muted">{esc(release) or "—"}</td>'
        f'<td>{repo_stack or "—"}</td>'
        f'<td class="nowrap">{review_cell}</td></tr>')

# ---------------------------------------------------------------- artifacts view
latest_by_key = {}
for r in runs:
    latest_by_key.setdefault(r["trigger"]["key"], r)
art_keys_html, art_panels_html = "", ""
first = True
for key, r in latest_by_key.items():
    contracts = {p["name"]: p["contract"] for p in r.get("phases", [])}
    rev = review_of(key)
    release = rev.get("release", "")
    rstat = rev.get("status") or ""
    plan = ROOT / f"testplans/{key}.md"
    art_keys_html += (
        f'<button class="art-key{" active" if first else ""}" data-art="{esc(key)}">'
        f'<span class="strong sm">{esc(key)}</span>'
        f'<span class="sm muted">run {esc(r["run_id"])}'
        f'{" · " + esc(release) if release else ""}</span></button>')

    inner = ""
    if plan.exists():
        exports = "".join(
            f'<button class="btn btn-sm export" data-key="{esc(key)}" data-fmt="{f}">{f}</button>'
            for f in ("md", "html", "docx", "pdf"))
        inner += (
            f'<div class="art-sec"><div class="art-row">'
            f'<h3>Test plan <span class="mono sm muted">testplans/{esc(key)}.md</span></h3>'
            f'<span class="spacer"></span>{exports}'
            f'<button class="btn btn-sm info pubconf" data-key="{esc(key)}">Publish to Confluence</button>'
            f'<button class="btn btn-sm info attachjira" data-key="{esc(key)}">Attach to JIRA (pdf)</button>'
            f'</div><pre>{esc(plan.read_text(encoding="utf-8"))}</pre></div>')

    left, right = "", ""
    scen = contracts.get("testplan", {}).get("scenarios", [])
    if scen:
        left += "<h3>Scenarios</h3>" + "".join(
            f'<div class="scen"><code>{esc(s["id"])}</code> {esc(s["title"])} '
            f'<span class="chip chip-info sm">{esc(s["layer"])}</span>'
            f'<span class="muted sm">→ {esc(s["target_repo"])}</span></div>' for s in scen)
    data_dir = ROOT / f"testdata/{key}"
    if data_dir.exists():
        files = [p for p in sorted(data_dir.rglob("*")) if p.is_file()]
        left += "<h3>Test data</h3>" + "".join(
            f'<div><code class="sm muted">testdata/{esc(key)}/'
            f'{esc(p.relative_to(data_dir).as_posix())}</code></div>' for p in files)
    gen = contracts.get("generate", {})
    if gen.get("tests"):
        right += "<h3>Generated tests</h3>" + "".join(
            f'<div class="sm"><code>{esc(t["file"])}</code> '
            f'<span class="chip chip-success sm">{esc(t.get("action", "?"))}</span></div>'
            for t in gen["tests"])
    v = contracts.get("validate", {})
    if v:
        failed = v.get("failed", 0)
        right += ('<h3>Validation</h3><div class="chips">'
                  f'<span class="chip chip-success">{v.get("passed", "?")} passed</span>'
                  f'<span class="chip chip-{"danger" if failed else "muted"}">{failed} failed</span>'
                  f'<span class="chip chip-muted">{v.get("repair_loops", "?")} repair loops</span></div>')
    oq = gen.get("open_questions") or contracts.get("testplan", {}).get("open_questions", [])
    if oq:
        right += "<h3>Open questions</h3>" + "".join(
            f'<div class="sm muted">• {esc(q)}</div>' for q in oq)
    if left or right:
        inner += f'<div class="art-sec art-grid"><div>{left}</div><div>{right}</div></div>'

    for g in r.get("gates", []):
        if g.get("diff") and (ROOT / g["diff"]).exists():
            diff_text = (ROOT / g["diff"]).read_text(encoding="utf-8", errors="replace")
            inner += (
                f'<div class="art-sec"><button class="code-toggle">'
                f'<span class="chev">▶</span> Generated test code — '
                f'<code>{esc(g["test_repo"])} @ {esc(g.get("commit") or "")}</code></button>'
                f'<pre class="code hidden">{esc(diff_text)}</pre></div>')

    head = (f'<div class="art-head"><h2>{esc(key)}</h2>{chip(r.get("overall", "?"))}'
            + (chip(rstat) if rstat else "")
            + f'<span class="mono sm muted">run {esc(r["run_id"])}</span>'
            + (f'<span class="mono sm muted">· release {esc(release)}</span>' if release else ""))
    head += "</div>"
    art_panels_html += (f'<article class="card art-panel{"" if first else " hidden"}" '
                        f'data-art-panel="{esc(key)}">{head}{inner or chr(10)}</article>')
    first = False

# ---------------------------------------------------------------- catalog view
repo_opts = "".join(f"<option>{esc(t['name'])}</option>" for t in trepos)
cat_rows = ""
for e in sorted(catalog, key=lambda e: (e["test_repo"], e["file"])):
    m = e["mapping"]
    h = health.get(e["test_id"])
    if h:
        hcls = "success" if h.get("pass_rate", 0) >= 0.8 and not h.get("flaky") else "warning"
        if h.get("flaky"):
            hcls = "danger"
        health_cell = (f'<span class="{hcls}-fg strong sm">{h.get("pass_rate", 0):.0%} pass'
                       f'{" · FLAKY" if h.get("flaky") else ""}</span> '
                       f'<span class="sm muted">({h.get("runs", 0)} run'
                       f'{"s" if h.get("runs", 0) != 1 else ""})</span>')
    else:
        health_cell = '<span class="muted">—</span>'
    cat_rows += (
        f'<tr data-repo="{esc(e["test_repo"])}" data-status="{esc(m["status"])}">'
        f'<td class="mono sm muted nowrap">{esc(e["test_repo"])}</td>'
        f'<td><div class="mono sm">{esc(e["file"])}</div>'
        f'<div class="sm muted">{esc(e["title"])}</div></td>'
        f'<td class="mono sm">{esc(", ".join(m["app_repos"])) or "—"}</td>'
        f'<td class="num">{m["confidence"]}</td>'
        f'<td class="sm muted">{esc(", ".join(m["method"]))}</td>'
        f'<td>{chip(m["status"])}</td><td>{health_cell}</td></tr>')

# ---------------------------------------------------------------- queue view
def queue_rows_html(items):
    if not items:
        return ('<tr><td colspan="7"><div class="empty">Queue is empty — fetch items '
                "above or paste JIRA context to get started.</div></td></tr>")
    out = ""
    for i in items:
        extra = (f' (exit {i["exit_code"]})'
                 if i["status"] == "failed" and i.get("exit_code") is not None else "")
        acts = ""
        if i["status"] == "failed":
            acts += (f'<button class="btn btn-sm qact" data-act="requeue" '
                     f'data-id="{esc(i["id"])}">Re-queue</button> ')
        if i["status"] != "running":
            acts += (f'<button class="btn btn-sm danger qact" data-act="remove" '
                     f'data-id="{esc(i["id"])}">Remove</button>')
        out += (f'<tr><td class="mono sm muted">{esc(i["id"])}</td>'
                f'<td>{chip(i["status"], extra)}</td>'
                f'<td><span class="pill">{esc(i["mode"])}</span></td>'
                f'<td class="strong">{esc(work_queue.key_of(i))}</td>'
                f'<td class="mono sm muted">{esc(i.get("release") or "—")}</td>'
                f'<td class="muted">{esc(i.get("requested_by") or "—")}</td>'
                f'<td class="right nowrap">{acts or "—"}</td></tr>')
    return out


queued_n = sum(1 for i in queue if i["status"] == "queued")
nav_badges = {
    "queue": sum(1 for i in queue if i["status"] in ("queued", "failed")),
    "runs": len(pending_review_keys),
}
gen_ts = time.strftime("%Y-%m-%d %H:%M")

NAV = [("overview", "◧", "Overview"), ("queue", "⇥", "Intake & queue"),
       ("runs", "▶", "Runs & reviews"), ("artifacts", "❏", "Artifacts"),
       ("catalog", "☰", "Test catalog"), ("settings", "⚙", "Settings")]
TITLES = {"overview": "Overview", "queue": "Intake & work queue",
          "runs": "Runs & team reviews", "artifacts": "Generated artifacts",
          "catalog": "Test knowledge catalog", "settings": "Settings & integrations"}
nav_html = "".join(
    f'<button class="nav-item{" active" if vid == "overview" else ""}" data-go="{vid}">'
    f'<span class="nav-ic">{icon}</span><span class="nav-lb">{esc(label)}</span>'
    + (f'<span class="badge">{nav_badges[vid]}</span>'
       if nav_badges.get(vid) else "") + "</button>"
    for vid, icon, label in NAV)

# ---------------------------------------------------------------- CSS (design tokens)
CSS = """
:root {
  --sr-bg: hsl(0 0% 100%); --sr-bg-muted: hsl(210 40% 96.1%);
  --sr-fg: hsl(222.2 47.4% 11.2%); --sr-fg-muted: hsl(215.4 16.3% 46.9%);
  --sr-fg-on-primary: hsl(210 40% 98%);
  --sr-primary: hsl(222.2 47.4% 11.2%); --sr-primary-90: hsl(222.2 47.4% 18%);
  --sr-border: hsl(214.3 31.8% 91.4%); --sr-input: hsl(214.3 31.8% 91.4%);
  --sr-success-bg: hsl(160 84% 39% / .15); --sr-success-fg: hsl(160 84% 28%);
  --sr-warning-bg: hsl(38 92% 50% / .15); --sr-warning-fg: hsl(32 81% 35%);
  --sr-danger-bg: hsl(0 84.2% 60.2% / .15); --sr-danger-fg: hsl(0 72% 45%);
  --sr-info-bg: hsl(217 91% 60% / .12); --sr-info-fg: hsl(217 91% 38%);
  --sr-font-sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Inter, Helvetica, Arial, sans-serif;
  --sr-font-mono: ui-monospace, "JetBrains Mono", "SF Mono", Menlo, "Cascadia Mono", Consolas, monospace;
  --sr-shadow-sm: 0 1px 2px 0 rgb(0 0 0 / .05);
  --sr-shadow: 0 1px 3px 0 rgb(0 0 0 / .08), 0 1px 2px -1px rgb(0 0 0 / .06);
  --sr-shadow-md: 0 4px 6px -1px rgb(0 0 0 / .08), 0 2px 4px -2px rgb(0 0 0 / .06);
}
@media (prefers-color-scheme: dark) { :root {
  --sr-bg: hsl(222.2 47.4% 7%); --sr-bg-muted: hsl(217.2 32.6% 17.5%);
  --sr-fg: hsl(210 40% 98%); --sr-fg-muted: hsl(215 20.2% 65.1%);
  --sr-primary: hsl(210 40% 98%); --sr-primary-90: hsl(210 40% 88%);
  --sr-fg-on-primary: hsl(222.2 47.4% 11.2%);
  --sr-border: hsl(217.2 32.6% 22%); --sr-input: hsl(217.2 32.6% 22%);
} }
* { box-sizing: border-box; }
body { margin:0; display:flex; min-height:100vh; background:var(--sr-bg-muted); color:var(--sr-fg);
  font-family:var(--sr-font-sans); font-size:14px; line-height:1.5; }
a { color:var(--sr-info-fg); text-decoration:none; } a:hover { text-decoration:underline; }
code { font-family:var(--sr-font-mono); }
@keyframes srfade { from { opacity:0; transform:translateY(6px); } to { opacity:1; transform:none; } }

aside { width:240px; flex:0 0 240px; background:var(--sr-bg); border-right:1px solid var(--sr-border);
  display:flex; flex-direction:column; position:sticky; top:0; height:100vh; }
.logo-row { height:56px; display:flex; align-items:center; gap:10px; padding:0 16px;
  border-bottom:1px solid var(--sr-border); }
.logo { width:28px; height:28px; border-radius:8px; background:var(--sr-primary);
  color:var(--sr-fg-on-primary); display:flex; align-items:center; justify-content:center;
  font-weight:700; font-size:13px; }
.logo-t { font-weight:600; font-size:14px; line-height:1.2; }
.logo-s { font-size:11px; color:var(--sr-fg-muted); line-height:1.2; }
nav.side { display:flex; flex-direction:column; gap:2px; padding:12px 8px; }
.nav-item { display:flex; align-items:center; gap:10px; padding:8px 10px; border:none;
  text-align:left; cursor:pointer; border-radius:8px; font-size:14px; font-family:var(--sr-font-sans);
  background:transparent; color:var(--sr-fg-muted); }
.nav-item:hover { background:var(--sr-bg-muted); }
.nav-item.active { background:var(--sr-bg-muted); color:var(--sr-fg); font-weight:600; }
.nav-ic { width:18px; text-align:center; font-size:13px; }
.nav-lb { flex:1; }
.badge { background:var(--sr-warning-bg); color:var(--sr-warning-fg); border-radius:9999px;
  font-size:11px; font-weight:600; padding:1px 7px; }
.side-foot { margin-top:auto; padding:14px 16px; border-top:1px solid var(--sr-border);
  display:flex; flex-direction:column; gap:8px; font-size:11px; color:var(--sr-fg-muted); }
.dot-row { display:flex; align-items:center; gap:8px; font-size:12px; }
.dot { width:8px; height:8px; border-radius:9999px; background:var(--sr-warning-fg); }
.dot.on { background:hsl(160 84% 39%); }

main { flex:1; min-width:0; display:flex; flex-direction:column; }
header { height:56px; background:var(--sr-bg); border-bottom:1px solid var(--sr-border);
  display:flex; align-items:center; gap:16px; padding:0 24px; position:sticky; top:0; z-index:5; }
header h1 { font-size:16px; font-weight:600; margin:0; flex:1; }
.static-pill { background:var(--sr-info-bg); color:var(--sr-info-fg); border-radius:9999px;
  font-size:12px; font-weight:500; padding:3px 10px; }
.content { padding:24px; display:flex; flex-direction:column; gap:24px; max-width:1200px;
  width:100%; margin:0 auto; }

.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; }
.tile { background:var(--sr-bg); border:1px solid var(--sr-border); border-radius:12px;
  padding:14px 16px; text-align:left; cursor:pointer; box-shadow:var(--sr-shadow-sm);
  display:flex; flex-direction:column; gap:2px; font-family:var(--sr-font-sans); }
.tile:hover { border-color:var(--sr-fg-muted); box-shadow:var(--sr-shadow); }
.tile-v { font-size:26px; font-weight:650; font-variant-numeric:tabular-nums; color:var(--sr-fg); }
.tile-v.alert { color:var(--sr-danger-fg); }
.tile-l { font-size:12px; color:var(--sr-fg-muted); }

.card { background:var(--sr-bg); border:1px solid var(--sr-border); border-radius:12px;
  box-shadow:var(--sr-shadow-sm); overflow:hidden; }
.card-h { padding:14px 20px; border-bottom:1px solid var(--sr-border); display:flex;
  align-items:center; gap:12px; flex-wrap:wrap; }
.card-h h2 { margin:0; font-size:14px; font-weight:600; }
.card-h .sub { font-size:12px; color:var(--sr-fg-muted); }
.card-h .grow { flex:1; }
.card-b { padding:16px 20px; }

.attn { display:flex; align-items:center; gap:12px; padding:12px 20px; border:none; width:100%;
  border-bottom:1px solid var(--sr-border); background:var(--sr-bg); cursor:pointer;
  text-align:left; font-family:var(--sr-font-sans); font-size:14px; color:var(--sr-fg); }
.attn:hover { background:var(--sr-bg-muted); } .attn:last-child { border-bottom:none; }
.attn-text { flex:1; } .attn-act { color:var(--sr-fg-muted); font-size:13px; white-space:nowrap; }

.scroll { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:13px; }
th, td { text-align:left; padding:9px 12px; border-bottom:1px solid var(--sr-border);
  vertical-align:top; }
th:first-child, td:first-child { padding-left:20px; }
th:last-child, td:last-child { padding-right:20px; }
thead th { color:var(--sr-fg-muted); font-weight:600; font-size:12px; }
tbody tr:last-child td, tbody tr:last-child th { border-bottom:none; }
td.num, th.c { text-align:center; } td.num { font-variant-numeric:tabular-nums; }
td.right { text-align:right; }
.dim { color:var(--sr-border); } .cov { color:var(--sr-fg-muted); }
th.mono, .mono { font-family:var(--sr-font-mono); font-size:12px; }
th.gap { color:var(--sr-danger-fg); }
.sm { font-size:12px; } .muted { color:var(--sr-fg-muted); } .strong { font-weight:600; }
.nowrap { white-space:nowrap; } .spacer { flex:1; }
.success-fg { color:var(--sr-success-fg); } .warning-fg { color:var(--sr-warning-fg); }
.danger-fg { color:var(--sr-danger-fg); }
.empty { padding:28px 20px; text-align:center; color:var(--sr-fg-muted); font-size:13px; }

.chip { border-radius:9999px; font-size:11px; font-weight:600; padding:2px 9px; white-space:nowrap;
  display:inline-block; }
.chip.sm { font-size:10px; padding:1px 7px; }
.chip-success { background:var(--sr-success-bg); color:var(--sr-success-fg); }
.chip-warning { background:var(--sr-warning-bg); color:var(--sr-warning-fg); }
.chip-danger { background:var(--sr-danger-bg); color:var(--sr-danger-fg); }
.chip-info { background:var(--sr-info-bg); color:var(--sr-info-fg); }
.chip-muted { background:var(--sr-bg-muted); color:var(--sr-fg-muted); }
.pill { background:var(--sr-bg-muted); border-radius:6px; padding:2px 8px; font-size:11px;
  font-weight:600; text-transform:uppercase; color:var(--sr-fg-muted); }

.btn { height:36px; padding:0 16px; border-radius:8px; border:1px solid var(--sr-border);
  background:var(--sr-bg); color:var(--sr-fg); font-size:13px; font-weight:500; cursor:pointer;
  font-family:var(--sr-font-sans); }
.btn:hover { background:var(--sr-bg-muted); }
.btn:disabled { opacity:.55; cursor:default; }
.btn-sm { height:28px; padding:0 12px; font-size:12px; }
.btn-primary { background:var(--sr-primary); color:var(--sr-fg-on-primary); border:none;
  height:32px; padding:0 14px; }
.btn-primary:hover { background:var(--sr-primary-90); }
.btn.danger { color:var(--sr-danger-fg); } .btn.danger:hover { background:var(--sr-danger-bg); }
.btn.info { color:var(--sr-info-fg); } .btn.info:hover { background:var(--sr-info-bg); }
.btn.approve { color:var(--sr-success-fg); height:26px; padding:0 10px; }
.btn.approve:hover { background:var(--sr-success-bg); }
select, input, textarea { height:36px; padding:0 10px; border-radius:8px;
  border:1px solid var(--sr-input); background:var(--sr-bg); color:var(--sr-fg); font-size:13px;
  font-family:var(--sr-font-sans); }
select.h32, input.h32 { height:32px; }
textarea { height:auto; padding:10px 12px; resize:vertical; width:100%; }
label.f { display:flex; align-items:center; gap:8px; font-size:13px; color:var(--sr-fg-muted); }
label.stack { display:flex; flex-direction:column; gap:4px; font-size:12px; color:var(--sr-fg-muted); }
.filters { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
.form-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:10px; }

.gate-line { display:flex; align-items:center; gap:8px; margin:2px 0; }
.gate-line .repo { min-width:130px; display:inline-block; }

.set-sec { padding:14px 0; border-bottom:1px solid var(--sr-border); }
.set-sec:first-child { padding-top:0; } .set-sec:last-of-type { border-bottom:none; }
.set-sec h3 { margin:0 0 2px; font-size:13px; font-weight:600; }
.set-sec .hint { font-size:12px; color:var(--sr-fg-muted); margin-bottom:10px; }
.danger-row { display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
.danger-row .grow { flex:1; min-width:240px; }

.art-layout { display:grid; grid-template-columns:260px 1fr; gap:20px; align-items:start; }
.art-list { position:sticky; top:80px; }
.art-list-h { padding:12px 16px; border-bottom:1px solid var(--sr-border); font-size:12px;
  font-weight:600; color:var(--sr-fg-muted); text-transform:uppercase; letter-spacing:.04em; }
.art-key { display:flex; flex-direction:column; gap:2px; width:100%; padding:10px 16px;
  border:none; border-bottom:1px solid var(--sr-border); text-align:left; cursor:pointer;
  font-family:var(--sr-font-sans); background:var(--sr-bg); color:var(--sr-fg); }
.art-key:hover, .art-key.active { background:var(--sr-bg-muted); }
.art-key:last-child { border-bottom:none; }
.art-panel { animation:srfade .25s ease; }
.art-head { padding:16px 24px; border-bottom:1px solid var(--sr-border); display:flex;
  align-items:center; gap:10px; flex-wrap:wrap; }
.art-head h2 { margin:0; font-size:16px; font-weight:600; }
.art-sec { padding:16px 24px; border-bottom:1px solid var(--sr-border); }
.art-sec:last-child { border-bottom:none; }
.art-row { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:10px; }
.art-row h3 { margin:0; font-size:13px; font-weight:600; }
.art-grid { display:grid; grid-template-columns:1fr 1fr; gap:20px; }
.art-grid h3 { margin:0 0 8px; font-size:13px; font-weight:600; }
.art-grid h3:not(:first-child) { margin-top:16px; }
.scen { display:flex; align-items:center; gap:8px; font-size:13px; margin:3px 0; flex-wrap:wrap; }
.scen code { font-size:12px; background:var(--sr-bg-muted); border-radius:6px; padding:1px 6px; }
.chips { display:flex; gap:8px; flex-wrap:wrap; }
pre { margin:0; background:var(--sr-bg-muted); border:1px solid var(--sr-border); border-radius:8px;
  padding:12px 14px; overflow-x:auto; font-size:12px; line-height:1.5;
  font-family:var(--sr-font-mono); white-space:pre-wrap; }
.code-toggle { display:flex; align-items:center; gap:8px; border:none; background:none; padding:0;
  cursor:pointer; font-size:13px; font-weight:600; color:var(--sr-fg);
  font-family:var(--sr-font-sans); }
.code-toggle .chev { font-size:11px; color:var(--sr-fg-muted); }
.code { margin-top:12px; white-space:pre; }
.hidden { display:none; }
[data-view] { display:none; flex-direction:column; gap:24px; }
[data-view].on { display:flex; }

#toast { position:fixed; bottom:20px; right:20px; background:var(--sr-primary);
  color:var(--sr-fg-on-primary); border-radius:8px; padding:10px 16px; font-size:13px;
  box-shadow:var(--sr-shadow-md); animation:srfade .2s ease; z-index:50; max-width:360px; }
@media (max-width: 900px) { aside { display:none; } .art-layout { grid-template-columns:1fr; }
  .art-grid { grid-template-columns:1fr; } }
"""

# ---------------------------------------------------------------- client JS
JS = """
const served = location.protocol.startsWith('http');
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
let toastT;
function toast(t) {
  let el = $('#toast');
  if (!el) { el = document.createElement('div'); el.id = 'toast'; document.body.appendChild(el); }
  el.textContent = t; el.style.display = 'block';
  clearTimeout(toastT); toastT = setTimeout(() => { el.style.display = 'none'; }, 3200);
}
function needsServer() {
  if (!served) toast('Static snapshot — start the server with: make serve');
  return !served;
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json()).error || r.status);
  return r.json();
}
const TITLES = { overview: 'Overview', queue: 'Intake & work queue',
  runs: 'Runs & team reviews', artifacts: 'Generated artifacts',
  catalog: 'Test knowledge catalog', settings: 'Settings & integrations' };
function go(view) {
  $$('[data-view]').forEach(v => v.classList.toggle('on', v.dataset.view === view));
  $$('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.go === view));
  $('#view-title').textContent = TITLES[view] || view;
}
document.addEventListener('click', e => {
  const nav = e.target.closest('[data-go]');
  if (nav) go(nav.dataset.go);
});
if (!served) { $('#static-pill').style.display = ''; }
else { $('#server-dot').classList.add('on'); $('#server-label').textContent = 'Server connected · ' + location.host; }

// ---- runs filters
function applyRunFilters() {
  const rel = $('#f-rel').value, rev = $('#f-rev').value;
  let shown = 0, total = 0;
  $$('#runs-table tbody tr').forEach(r => {
    total++;
    const rOk = !rel || (rel === '__none__' ? r.dataset.release === '' : r.dataset.release === rel);
    const vOk = !rev || (rev === 'pending' ? (r.dataset.review === 'pending_review' || r.dataset.review === 'in_review')
                                           : r.dataset.review === rev);
    r.style.display = rOk && vOk ? '' : 'none'; if (rOk && vOk) shown++;
  });
  $('#run-count').textContent = shown + ' / ' + total + ' runs';
}
['#f-rel', '#f-rev'].forEach(s => $(s).addEventListener('change', applyRunFilters));
applyRunFilters();

// ---- approve (team review)
document.addEventListener('click', async e => {
  const b = e.target.closest('button.approve');
  if (!b) return;
  if (needsServer()) return;
  b.disabled = true;
  try {
    await api('/api/review', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: b.dataset.key, status: 'approved', by: 'dashboard' }) });
    const cell = b.parentElement;
    cell.innerHTML = '<span class="chip chip-success">✓ approved</span>';
    toast('Approved ' + b.dataset.key + ' — recorded on the review board');
  } catch (err) { b.disabled = false; toast(err.message); }
});

// ---- artifacts key switcher + code toggles
document.addEventListener('click', e => {
  const k = e.target.closest('.art-key');
  if (k) {
    $$('.art-key').forEach(x => x.classList.toggle('active', x === k));
    $$('.art-panel').forEach(p => p.classList.toggle('hidden', p.dataset.artPanel !== k.dataset.art));
    return;
  }
  const t = e.target.closest('.code-toggle');
  if (t) {
    const pre = t.parentElement.querySelector('pre.code');
    const open = pre.classList.toggle('hidden');
    t.querySelector('.chev').textContent = open ? '▶' : '▼';
  }
});

// ---- exports / publish / attach
document.addEventListener('click', async e => {
  const x = e.target.closest('button.export');
  if (x) {
    if (needsServer()) return;
    location.href = '/api/export/plan?key=' + encodeURIComponent(x.dataset.key) + '&format=' + x.dataset.fmt;
    return;
  }
  const act = e.target.closest('button.pubconf, button.attachjira');
  if (!act) return;
  if (needsServer()) return;
  const isPub = act.classList.contains('pubconf');
  const idle = act.textContent;
  act.disabled = true; act.textContent = isPub ? 'Publishing…' : 'Attaching…';
  try {
    const r = await api(isPub ? '/api/export/confluence' : '/api/export/attach',
      { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: act.dataset.key, format: 'pdf' }) });
    act.textContent = isPub ? 'Published' : 'Attached'; toast(r.result);
  } catch (err) { act.disabled = false; act.textContent = idle; toast(err.message); }
});

// ---- catalog filters
function applyCatFilters() {
  const repo = $('#c-repo').value, st = $('#c-status').value, q = $('#c-q').value.toLowerCase();
  let shown = 0, total = 0;
  $$('#cat-table tbody tr').forEach(r => {
    total++;
    const ok = (!repo || r.dataset.repo === repo) && (!st || r.dataset.status === st)
      && (!q || r.textContent.toLowerCase().includes(q));
    r.style.display = ok ? '' : 'none'; if (ok) shown++;
  });
  $('#cat-count').textContent = shown + ' / ' + total + ' tests';
}
['#c-repo', '#c-status'].forEach(s => $(s).addEventListener('change', applyCatFilters));
$('#c-q').addEventListener('input', applyCatFilters);
applyCatFilters();

// ---- queue
const chipMap = { queued: ['queued', 'info'], running: ['● running', 'warning'],
  done: ['✓ done', 'success'], failed: ['✗ failed', 'danger'] };
function keyOf(i) { return i.mode === 'pr' ? 'PR-' + i.target + '-' + i.pr : i.target; }
async function refreshQueue() {
  if (!served) return;
  const q = await api('/api/queue');
  const body = $('#queue-table tbody');
  if (!q.length) {
    body.innerHTML = '<tr><td colspan="7"><div class="empty">Queue is empty — fetch items above or paste JIRA context to get started.</div></td></tr>';
  } else {
    body.innerHTML = q.map(i => {
      const [lb, cls] = chipMap[i.status] || [i.status, 'muted'];
      const extra = i.status === 'failed' && i.exit_code != null ? ' (exit ' + i.exit_code + ')' : '';
      let acts = '';
      if (i.status === 'failed') acts += '<button class="btn btn-sm qact" data-act="requeue" data-id="' + i.id + '">Re-queue</button> ';
      if (i.status !== 'running') acts += '<button class="btn btn-sm danger qact" data-act="remove" data-id="' + i.id + '">Remove</button>';
      return '<tr><td class="mono sm muted">' + i.id + '</td>' +
        '<td><span class="chip chip-' + cls + '">' + lb + extra + '</span></td>' +
        '<td><span class="pill">' + i.mode + '</span></td>' +
        '<td class="strong">' + keyOf(i) + '</td>' +
        '<td class="mono sm muted">' + (i.release || '—') + '</td>' +
        '<td class="muted">' + (i.requested_by || '—') + '</td>' +
        '<td class="right nowrap">' + (acts || '—') + '</td></tr>';
    }).join('');
  }
  const n = q.filter(i => i.status === 'queued').length;
  $('#queue-count').textContent = q.length + ' item(s) · ' + n + ' queued';
  $('#run-queue').textContent = 'Run queue (' + n + ')';
  return q;
}
document.addEventListener('click', async e => {
  const b = e.target.closest('button.qact');
  if (!b) return;
  if (needsServer()) return;
  try {
    await api('/api/queue/' + b.dataset.act, { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: b.dataset.id }) });
    toast((b.dataset.act === 'requeue' ? 'Re-queued ' : 'Removed ') + b.dataset.id);
  } catch (err) { toast(err.message); }
  refreshQueue();
});
$('#run-queue').addEventListener('click', async () => {
  if (needsServer()) return;
  try {
    await api('/api/queue/run', { method: 'POST' });
    toast('Queue running… statuses refresh automatically');
    const t = setInterval(async () => {
      const q = await refreshQueue();
      if (!q.some(i => i.status === 'queued' || i.status === 'running')) {
        clearInterval(t); toast('Queue drained — reload for new runs');
      }
    }, 3000);
  } catch (err) { toast(err.message); }
});

// ---- fetch work
$('#fetch-btn').addEventListener('click', async () => {
  if (needsServer()) return;
  const btn = $('#fetch-btn');
  btn.disabled = true; btn.textContent = 'Fetching…';
  try {
    const items = await api('/api/items?release=' + encodeURIComponent($('#fetch-rel').value));
    const card = $('#fetched-wrap'); card.classList.remove('hidden');
    $('#fetched-table tbody').innerHTML = items.length ? items.map((i, n) =>
      '<tr><td><span class="pill">' + i.mode + '</span></td>' +
      '<td class="strong">' + i.key + '</td><td>' + i.summary + '</td>' +
      '<td class="mono sm muted">' + (i.release || '—') + '</td>' +
      '<td class="right"><button class="btn btn-sm fq" data-n="' + n + '" ' +
      (i.queued ? 'disabled' : '') + '>' + (i.queued ? 'Queued' : 'Queue') + '</button></td></tr>'
    ).join('') : '<tr><td colspan="5"><div class="empty">No items for this release.</div></td></tr>';
    $('#fetch-msg').textContent = items.length + ' item(s) found';
    $$('#fetched-table button.fq').forEach(b => b.addEventListener('click', async () => {
      const i = items[+b.dataset.n];
      try {
        await api('/api/queue', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: i.mode, target: i.target, pr: i.pr, release: i.release }) });
        b.disabled = true; b.textContent = 'Queued';
        toast('Queued ' + i.key + ' — press Run queue to execute'); refreshQueue();
      } catch (err) { toast(err.message); }
    }));
  } catch (err) { toast('Fetch failed: ' + err.message); }
  btn.disabled = false; btn.textContent = 'Fetch items';
});

// ---- inline ticket
$('#inl-queue').addEventListener('click', async () => {
  if (needsServer()) return;
  const val = id => $('#' + id).value;
  if (!val('inl-text').trim()) { toast('Paste the ticket text first'); return; }
  try {
    const r = await api('/api/queue/inline', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: val('inl-text'), key: val('inl-key'),
        components: val('inl-components'), labels: val('inl-labels'),
        repos: val('inl-repos'), type: val('inl-type') }) });
    toast((r.queued ? 'Queued inline ticket ' : 'Already queued ') + r.key +
      ' — press Run queue to execute');
    $('#inl-text').value = ''; $('#inl-key').value = ''; refreshQueue();
  } catch (err) { toast(err.message); }
});
refreshQueue();

// ---- team report
document.addEventListener('click', e => {
  const b = e.target.closest('button.report-dl');
  if (!b) return;
  if (needsServer()) return;
  const days = $('#rep-days').value, rel = $('#rep-rel').value;
  location.href = '/api/report?format=' + b.dataset.fmt +
    (days ? '&days=' + days : '') + (rel ? '&release=' + encodeURIComponent(rel) : '');
  toast('Generating team report (' + b.dataset.fmt + ')…');
});

// ---- settings
const escAttr = s => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
async function loadSettings() {
  if (!served) return;
  try {
    const secs = await api('/api/settings');
    $('#settings-body').innerHTML = secs.map(s =>
      '<div class="set-sec"><h3>' + escAttr(s.section) + '</h3>' +
      '<div class="hint">' + escAttr(s.hint) + '</div><div class="form-grid">' +
      s.fields.map(f => {
        if (f.options) {
          return '<label class="stack">' + escAttr(f.label) +
            '<select data-env="' + f.env + '">' + f.options.map(o =>
              '<option value="' + o[0] + '"' + (f.value === o[0] ? ' selected' : '') +
              '>' + escAttr(o[1]) + '</option>').join('') + '</select></label>';
        }
        const ph = f.secret ? (f.set ? '•••••• set — type to replace'
                                     : 'not set') : (f.help || '');
        return '<label class="stack">' + escAttr(f.label) + (f.secret ? ' 🔒' : '') +
          '<input data-env="' + f.env + '"' + (f.secret ? ' type="password" autocomplete="new-password"' : '') +
          ' value="' + escAttr(f.value || '') + '" placeholder="' + escAttr(ph) + '"></label>';
      }).join('') + '</div></div>').join('') +
      '<div style="padding-top:14px"><button class="btn btn-primary" id="save-settings" ' +
      'style="height:36px">Save settings</button></div>';
    $$('#settings-body [data-env]').forEach(el => { el.dataset.init = el.value; });
  } catch (err) { $('#settings-body').innerHTML = '<div class="empty">' + escAttr(err.message) + '</div>'; }
}
document.addEventListener('click', async e => {
  if (e.target.id !== 'save-settings') return;
  const updates = {};
  $$('#settings-body [data-env]').forEach(el => {
    // secrets: an empty password field means "keep the stored value"
    if (el.value !== el.dataset.init && !(el.type === 'password' && !el.value))
      updates[el.dataset.env] = el.value;
  });
  if (!Object.keys(updates).length) { toast('Nothing changed'); return; }
  e.target.disabled = true;
  try {
    const r = await api('/api/settings', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ updates }) });
    toast('Saved ' + r.updated.length + ' setting(s) to .env');
    loadSettings();
  } catch (err) { e.target.disabled = false; toast(err.message); }
});
$('#clear-demo').addEventListener('click', async () => {
  if (needsServer()) return;
  if (!confirm('Delete ALL generated demo data?\\n\\nRemoves run history, archived ' +
    'diffs, review/queue/webhook state, test plans, test data, exports, logs and ' +
    'scratch dirs. The registry, catalog and demo repos are kept.\\n\\nThis cannot be undone.')) return;
  const b = $('#clear-demo');
  b.disabled = true;
  try {
    const r = await api('/api/demo/clear', { method: 'POST' });
    toast('Cleared ' + r.removed + ' generated file(s) — reload to see the reset dashboard');
  } catch (err) { toast(err.message); }
  b.disabled = false;
});
loadSettings();
"""

# ---------------------------------------------------------------- page assembly
page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI QE — QA Dashboard</title>
<style>{CSS}</style></head><body>

<aside>
  <div class="logo-row"><div class="logo">QE</div>
    <div><div class="logo-t">AI QE Platform</div><div class="logo-s">QA operations</div></div>
  </div>
  <nav class="side">{nav_html}</nav>
  <div class="side-foot">
    <div class="dot-row"><span class="dot" id="server-dot"></span>
      <span id="server-label">Static snapshot (make serve)</span></div>
    <div>Generated {gen_ts} · <code>make dashboard</code></div>
  </div>
</aside>

<main>
  <header>
    <h1 id="view-title">Overview</h1>
    <span class="static-pill" id="static-pill" style="display:none">Static snapshot —
      run <code>make serve</code> for actions</span>
    <button class="btn btn-primary" id="run-queue">Run queue ({queued_n})</button>
  </header>
  <div class="content">

  <div data-view="overview" class="on">
    <div class="tiles">{tiles_html}</div>
    <section class="card">
      <div class="card-h"><h2>Needs attention</h2>
        <span class="sub">what a QA lead should look at first</span></div>
      <div>{attention_html}</div>
    </section>
    <section class="card">
      <div class="card-h"><div><h2>Team report</h2>
        <div class="sub">Completed work, review backlog, queue, throughput and estate
        health in one shareable document (also: <code>make report</code>).</div></div>
        <span class="grow"></span>
        <label class="f">Period <select id="rep-days" class="h32">
          <option value="7">last 7 days</option><option value="30">last 30 days</option>
          <option value="90">last 90 days</option><option value="">all time</option>
        </select></label>
        <label class="f">Release <select id="rep-rel" class="h32">
          <option value="">all</option>{release_opts}</select></label>
        <span class="chips">{"".join(
            f'<button class="btn btn-sm report-dl" data-fmt="{f}">{f}</button>'
            for f in ("md", "html", "docx", "pdf"))}</span>
      </div>
    </section>
    <section class="card">
      <div class="card-h"><h2>Coverage matrix</h2>
        <span class="sub">app repos × E2E test repos · numbers are mapped tests ·
        red rows have no coverage</span></div>
      <div class="scroll"><table>
        <thead><tr><th>app repo</th>{matrix_head}<th>status</th></tr></thead>
        <tbody>{matrix_rows}</tbody></table></div>
    </section>
  </div>

  <div data-view="queue">
    <section class="card">
      <div class="card-h"><div><h2>Fetch work from JIRA &amp; SCM</h2>
        <div class="sub">Pull tickets and PRs for a release, queue them, then run the
        queue — items are processed in order.</div></div></div>
      <div class="card-b filters">
        <label class="f">Release <select id="fetch-rel"><option value="">all releases</option>
          {release_opts}</select></label>
        <button class="btn" id="fetch-btn">Fetch items</button>
        <span class="sub" id="fetch-msg"></span>
      </div>
      <div class="scroll hidden" id="fetched-wrap" style="border-top:1px solid var(--sr-border)">
        <table id="fetched-table"><thead><tr><th>type</th><th>key</th>
          <th style="width:50%">summary</th><th>release</th><th></th></tr></thead>
          <tbody></tbody></table>
      </div>
    </section>
    <section class="card">
      <div class="card-h"><div><h2>Run from pasted JIRA context</h2>
        <div class="sub">No ticket needed. First line becomes the summary;
        <code>AC-1: …</code> lines become acceptance criteria.</div></div></div>
      <div class="card-b" style="display:flex; flex-direction:column; gap:12px">
        <textarea id="inl-text" rows="4"
          placeholder="Paste the story / bug / security-fix text here…"></textarea>
        <div class="form-grid">
          <label class="stack">Key (optional)<input id="inl-key" placeholder="ADHOC-1"></label>
          <label class="stack">Components (csv)<input id="inl-components" placeholder="Checkout"></label>
          <label class="stack">Labels (csv)<input id="inl-labels" placeholder="api-only"></label>
          <label class="stack">Linked repos (csv)<input id="inl-repos" placeholder="orders-api"></label>
          <label class="stack">Issue type<select id="inl-type">
            <option>Story</option><option>Bug</option><option>Security</option></select></label>
        </div>
        <div><button class="btn btn-primary" id="inl-queue" style="height:36px">
          Queue inline ticket</button></div>
      </div>
    </section>
    <section class="card">
      <div class="card-h"><h2 class="grow">Queue</h2>
        <span class="sub" id="queue-count">{len(queue)} item(s) · {queued_n} queued</span></div>
      <div class="scroll"><table id="queue-table">
        <thead><tr><th>id</th><th>status</th><th>type</th><th>key</th><th>release</th>
          <th>requested by</th><th class="right">actions</th></tr></thead>
        <tbody>{queue_rows_html(queue)}</tbody></table></div>
    </section>
  </div>

  <div data-view="runs">
    <section class="card">
      <div class="card-h"><h2>Recent runs</h2>
        <label class="f">Release <select id="f-rel" class="h32"><option value="">all</option>
          {release_opts}<option value="__none__">(no release)</option></select></label>
        <label class="f">Review <select id="f-rev" class="h32"><option value="">all</option>
          <option value="pending">awaiting review</option><option value="approved">approved</option>
          <option value="changes_requested">changes requested</option></select></label>
        <span class="sub" style="margin-left:auto" id="run-count"></span></div>
      <div class="scroll"><table id="runs-table">
        <thead><tr><th>key / run</th><th>trigger</th><th>time</th><th>overall</th>
          <th>release</th><th style="min-width:280px">gate results per test repo</th>
          <th>team review</th></tr></thead>
        <tbody>{runs_rows}</tbody></table></div>
    </section>
  </div>

  <div data-view="artifacts">
    <div class="art-layout">
      <nav class="card art-list">
        <div class="art-list-h">Latest run per key</div>
        {art_keys_html or '<div class="empty">No runs yet.</div>'}
      </nav>
      <div>{art_panels_html or '<div class="card"><div class="empty">No artifacts yet — run make demo-pr / demo-jira.</div></div>'}</div>
    </div>
  </div>

  <div data-view="catalog">
    <section class="card">
      <div class="card-h"><h2>Test knowledge catalog</h2>
        <label class="f">Repo <select id="c-repo" class="h32"><option value="">all</option>
          {repo_opts}</select></label>
        <label class="f">Status <select id="c-status" class="h32"><option value="">all</option>
          <option>auto</option><option>confirmed</option><option>needs_review</option>
          <option>orphan</option></select></label>
        <input id="c-q" class="h32" placeholder="Search title / file / app repo…"
          style="flex:1; min-width:180px">
        <span class="sub" id="cat-count"></span></div>
      <div class="scroll"><table id="cat-table">
        <thead><tr><th>test repo</th><th>file / title</th><th>app repos</th>
          <th class="num">conf</th><th>evidence</th><th>mapping</th><th>CI health</th></tr></thead>
        <tbody>{cat_rows}</tbody></table></div>
    </section>
  </div>

  <div data-view="settings">
    <section class="card">
      <div class="card-h"><div><h2>Integrations</h2>
        <div class="sub">Stored in the gitignored <code>.env</code> — the same file
        the adapters read. Secrets are write-only: a set secret shows as
        <code>••••••</code>; type a new value to replace it, leave blank to keep it.
        Adapter-mode and SCM changes take effect on the next run;
        restart <code>make serve</code> to switch the server's fetch source.</div></div></div>
      <div class="card-b" id="settings-body">
        <div class="empty">Start the server (<code>make serve</code>) to view and
        edit integration settings.</div>
      </div>
    </section>
    <section class="card">
      <div class="card-h"><div><h2>Danger zone</h2>
        <div class="sub">Destructive operations — these cannot be undone.</div></div></div>
      <div class="card-b danger-row">
        <div class="grow"><div class="strong">Clear demo data</div>
          <div class="sm muted">Deletes all generated data: run history &amp; archived
          diffs, review/queue/webhook state, test plans, test data, exports, logs and
          scratch dirs. The estate itself (repo registry, test catalog, AGENTS.md,
          demo repos) is kept — rebuild demo state with <code>make demo-bootstrap</code>.</div></div>
        <button class="btn danger" id="clear-demo">Clear demo data</button>
      </div>
    </section>
  </div>

  </div>
</main>
<script>{JS}</script>
</body></html>"""

out = ROOT / "reports/dashboard.html"
out.write_text(page, encoding="utf-8", newline="\n")
print(f"dashboard written: {out} ({len(runs)} runs, {len(catalog)} catalog entries, "
      f"{len(latest_by_key)} artifact keys)")
