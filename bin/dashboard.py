#!/usr/bin/env python3
"""Generate reports/dashboard.html — the QA monitoring & mapping dashboard.
Self-contained (no external assets); reads reports/runs/*.json, catalog/*.jsonl,
and registry/repo-registry.yaml. Regenerate any time: make dashboard."""
import glob, html, json, pathlib, sys, time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine/lib"))
from registry import load_registry
import review_state

esc = html.escape

runs = []
for f in glob.glob(str(ROOT / "reports/runs/*.json")):
    if pathlib.Path(f).name in ("reviews.json", "queue.json"):  # state files, not run records
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

# ---- aggregates -------------------------------------------------------------
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

STATUS = {  # gate/run + mapping + team-review states -> (icon, css class, label)
    "committed":   ("&#10003;", "good", "committed"),
    "no_changes":  ("&#8212;",  "muted", "no changes"),
    "quarantined": ("&#9888;",  "critical", "quarantined"),
    "auto":        ("&#10003;", "good", "auto"),
    "confirmed":   ("&#10003;", "good", "confirmed"),
    "needs_review":("&#9998;",  "warning", "needs review"),
    "orphan":      ("&#9888;",  "critical", "orphan"),
    "pending_review":    ("&#9998;",  "warning",  "yet to be reviewed"),
    "in_review":         ("&#9998;",  "warning",  "in review"),
    "approved":          ("&#10003;", "good",     "reviewed + approved"),
    "changes_requested": ("&#9888;",  "critical", "changes requested"),
}
reviews = review_state.load()
def review_chip(key):
    st = reviews.get(key, {}).get("status")
    return chip(st) if st else '<span class="chip muted">&#8212;</span>'
def release_cell(key):
    rel = reviews.get(key, {}).get("release")
    return f"<code>{esc(rel)}</code>" if rel else '<span class="chip muted">&#8212;</span>'
def chip(status):
    icon, cls, label = STATUS.get(status, ("", "muted", status))
    return f'<span class="chip {cls}">{icon} {esc(label)}</span>'

def tile(value, label, cls=""):
    return (f'<div class="tile {cls}"><div class="v">{value}</div>'
            f'<div class="l">{esc(label)}</div></div>')

# ---- sections ---------------------------------------------------------------
runs_rows = ""
for r in runs[:25]:
    ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(r.get("ts", 0)))
    gates = r.get("gates", []) or [{"test_repo": "—", "status": "?", "exit_code": 0}]
    span = len(gates)
    run_release = reviews.get(r["trigger"]["key"], {}).get("release", "")
    for i, g in enumerate(gates):
        # every row of the group carries the run's release so the filter hides whole runs
        row = (f'<tr data-run="{esc(r["run_id"])}" data-release="{esc(run_release)}">')
        if i == 0:  # run-level cells span all of this run's test-repo rows
            row += (f'<td rowspan="{span}"><code>{esc(r["run_id"])}</code></td>'
                    f'<td rowspan="{span}">{esc(r["trigger"]["type"])}</td>'
                    f'<td rowspan="{span}"><strong>{esc(r["trigger"]["key"])}</strong></td>'
                    f'<td rowspan="{span}">{ts}</td>'
                    f'<td rowspan="{span}">{chip(r.get("overall", "?"))}</td>'
                    f'<td rowspan="{span}">{review_chip(r["trigger"]["key"])}</td>'
                    f'<td rowspan="{span}">{release_cell(r["trigger"]["key"])}</td>')
        links = ""
        if g.get("commit"):
            links += f' <code>{esc(g["commit"][:7])}</code>'
        if g.get("diff"):
            links += f' <a href="runs/{esc(pathlib.PurePosixPath(g["diff"]).name)}">diff</a>'
        if g.get("status") == "quarantined" and g.get("log"):
            links += f' <a href="{esc(pathlib.PurePosixPath(g["log"]).name)}">log</a>'
        row += (f'<td class="repo"><strong>{esc(g["test_repo"])}</strong></td>'
                f'<td>{chip(g["status"])}{links}</td></tr>')
        runs_rows += row

matrix_head = "".join(f"<th>{esc(t['name'])}</th>" for t in trepos)
matrix_rows = ""
for s in sources:
    cells = ""
    for t in trepos:
        n = counts.get((s, t["name"]), 0)
        cells += (f'<td class="num">{n}</td>' if n
                  else '<td class="num covers">&#10003;</td>' if s in t.get("covers", [])
                  else '<td class="num dim">&middot;</td>')
    cls = ' class="gap"' if s in uncovered else ""
    matrix_rows += f"<tr{cls}><th>{esc(s)}</th>{cells}</tr>"

cat_rows = ""
for e in sorted(catalog, key=lambda e: (e["test_repo"], e["file"])):
    m = e["mapping"]
    ev = e["evidence"]["endpoints"] or e["evidence"]["ui_routes"] or []
    cat_rows += (f'<tr data-repo="{esc(e["test_repo"])}" data-status="{esc(m["status"])}">'
                 f'<td>{esc(e["test_repo"])}</td>'
                 f'<td><code>{esc(e["file"])}</code></td>'
                 f'<td>{esc(e["title"])}</td>'
                 f'<td>{esc(", ".join(m["app_repos"])) or "&#8212;"}</td>'
                 f'<td class="num">{m["confidence"]}</td>'
                 f'<td>{esc(", ".join(m["method"]))}</td>'
                 f'<td>{chip(m["status"])}</td></tr>')

# Generated artifacts per key (latest run per trigger key)
latest_by_key = {}
for r in runs:                                   # runs sorted newest-first
    latest_by_key.setdefault(r["trigger"]["key"], r)
art_blocks = ""
for key, r in latest_by_key.items():
    contracts = {p["name"]: p["contract"] for p in r.get("phases", [])}
    inner = ""
    plan = ROOT / f"testplans/{key}.md"
    if plan.exists():
        inner += (f"<h4>Test plan &mdash; <code>testplans/{esc(key)}.md</code>"
                  f' <span class="served-only">&middot; export:'
                  + "".join(f' <a href="/api/export/plan?key={esc(key)}&amp;format={f}">{f}</a>'
                            for f in ("md", "html", "docx", "pdf"))
                  + f' &middot; <button class="pubconf" data-key="{esc(key)}">publish to Confluence</button>'
                  + f"</span></h4>"
                  f"<pre>{esc(plan.read_text(encoding='utf-8'))}</pre>")
    scen = contracts.get("testplan", {}).get("scenarios", [])
    if scen:
        inner += ("<h4>Scenarios</h4><ul>"
                  + "".join(f"<li><code>{esc(s['id'])}</code> {esc(s['title'])} "
                            f"[{esc(s['layer'])}] &rarr; {esc(s['target_repo'])}</li>"
                            for s in scen) + "</ul>")
    data_dir = ROOT / f"testdata/{key}"
    if data_dir.exists():
        files = [p for p in sorted(data_dir.rglob("*")) if p.is_file()]
        inner += ("<h4>Test data</h4><ul>"
                  + "".join(f"<li><code>testdata/{esc(key)}/"
                            f"{esc(p.relative_to(data_dir).as_posix())}</code></li>"
                            for p in files) + "</ul>")
    gen = contracts.get("generate", {})
    if gen.get("tests"):
        inner += ("<h4>Generated tests</h4><ul>"
                  + "".join(f"<li><code>{esc(t['file'])}</code> ({esc(t.get('action', '?'))})</li>"
                            for t in gen["tests"]) + "</ul>")
    v = contracts.get("validate", {})
    if v:
        inner += (f"<h4>Validation</h4><p>{v.get('passed', '?')} passed &middot; "
                  f"{v.get('failed', '?')} failed &middot; "
                  f"{v.get('repair_loops', '?')} repair loop(s)</p>")
    for g in r.get("gates", []):
        if g.get("diff") and (ROOT / g["diff"]).exists():
            diff_text = (ROOT / g["diff"]).read_text(encoding="utf-8", errors="replace")
            inner += (f"<details><summary>Generated test code &mdash; "
                      f"{esc(g['test_repo'])} @ <code>{esc(g.get('commit') or '')}</code>"
                      f"</summary><pre>{esc(diff_text)}</pre></details>")
    if inner:
        rev = reviews.get(key, {})
        rev_line = ""
        if rev:
            who = f" by {esc(rev['reviewer'])}" if rev.get("reviewer") else ""
            note = f" &mdash; {esc(rev['note'])}" if rev.get("note") else ""
            rel = (f" &middot; release <code>{esc(rev['release'])}</code>"
                   if rev.get("release") else "")
            rev_line = f"<p>Team review: {review_chip(key)}{who}{note}{rel}</p>"
        rel_sum = (f" &middot; {release_cell(key)}"
                   if reviews.get(key, {}).get("release") else "")
        art_blocks += (f"<details class='art'><summary><strong>{esc(key)}</strong> "
                       f"&mdash; run {esc(r['run_id'])} &middot; {chip(r.get('overall', '?'))}"
                       f" &middot; {review_chip(key)}{rel_sum}"
                       f"</summary>{rev_line}{inner}</details>")

repo_opts = "".join(f'<option>{esc(t["name"])}</option>' for t in trepos)
release_opts = "".join(
    f"<option>{esc(v)}</option>"
    for v in sorted({e["release"] for e in reviews.values() if e.get("release")}))
gen_ts = time.strftime("%Y-%m-%d %H:%M:%S")

page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI QE — QA Dashboard</title>
<style>
:root {{ --bg:#ffffff; --ink:#1a1a19; --ink2:#5f5e5b; --line:#e4e2de; --card:#f7f6f4;
        --good:#0ca30c; --warning:#b27400; --critical:#d03b3b; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#1a1a19; --ink:#f0efec; --ink2:#a5a39e; --line:#3a3936; --card:#242422;
          --warning:#fab219; }} }}
* {{ box-sizing:border-box }}
body {{ margin:0; padding:24px; background:var(--bg); color:var(--ink);
       font:14px/1.5 ui-sans-serif,system-ui,sans-serif }}
h1 {{ font-size:20px; margin:0 0 4px }} h2 {{ font-size:15px; margin:32px 0 10px }}
.sub {{ color:var(--ink2); margin-bottom:20px }}
.tiles {{ display:flex; flex-wrap:wrap; gap:12px }}
.tile {{ background:var(--card); border:1px solid var(--line); border-radius:8px;
        padding:12px 18px; min-width:130px }}
.tile .v {{ font-size:26px; font-weight:650 }} .tile .l {{ color:var(--ink2); font-size:12px }}
.tile.alert .v {{ color:var(--critical) }}
table {{ border-collapse:collapse; width:100%; font-size:13px }}
.scroll {{ overflow-x:auto }}
th,td {{ text-align:left; padding:6px 10px; border-bottom:1px solid var(--line);
        vertical-align:top }}
thead th {{ color:var(--ink2); font-weight:600; font-size:12px }}
td.num {{ text-align:right; font-variant-numeric:tabular-nums }}
td.dim {{ color:var(--line) }} td.covers {{ color:var(--ink2) }}
tr.gap th {{ color:var(--critical) }}
td.repo {{ white-space:nowrap }}
td[rowspan] {{ vertical-align:top }}
code {{ background:var(--card); padding:1px 5px; border-radius:4px; font-size:12px }}
.chip {{ white-space:nowrap; font-size:12px; font-weight:600 }}
.chip.good {{ color:var(--good) }} .chip.warning {{ color:var(--warning) }}
.chip.critical {{ color:var(--critical) }} .chip.muted {{ color:var(--ink2) }}
.filters {{ display:flex; gap:10px; margin:0 0 10px; align-items:center }}
select,input,button {{ background:var(--card); color:var(--ink); border:1px solid var(--line);
               border-radius:6px; padding:5px 8px; font-size:13px }}
button {{ cursor:pointer }} button:disabled {{ opacity:.5; cursor:default }}
a {{ color:inherit }}
details.art {{ background:var(--card); border:1px solid var(--line); border-radius:8px;
              padding:10px 14px; margin:8px 0 }}
details.art h4 {{ margin:12px 0 4px; font-size:13px }}
details.art pre {{ background:var(--bg); border:1px solid var(--line); border-radius:6px;
                  padding:10px; overflow-x:auto; font-size:12px; line-height:1.45 }}
details.art summary {{ cursor:pointer }}
details details {{ margin:8px 0 }}
</style></head><body>
<h1>AI QE &mdash; QA Dashboard</h1>
<div class="sub">Generated {gen_ts} &middot; regenerate with <code>make dashboard</code></div>

<div class="tiles">
{tile(len(runs), "pipeline runs")}
{tile(n_committed, "runs committed")}
{tile(n_quar, "runs quarantined", "alert" if n_quar else "")}
{tile(len(catalog), "tests cataloged")}
{tile(mapped, "mapped (auto+confirmed)")}
{tile(by_status.get("needs_review", 0), "needs review",
      "alert" if by_status.get("needs_review") else "")}
{tile(by_status.get("orphan", 0), "orphans", "alert" if by_status.get("orphan") else "")}
{tile(len(uncovered), "uncovered app repos", "alert" if uncovered else "")}
{tile(sum(1 for e in reviews.values() if e.get("status") in ("pending_review", "in_review")),
      "awaiting team review",
      "alert" if any(e.get("status") in ("pending_review", "in_review")
                     for e in reviews.values()) else "")}
</div>

<h2>Fetch &amp; queue work</h2>
<div class="sub">Fetch JIRA tickets and pull requests for a release, queue them, then run the
queue — the pipeline processes items in order. Interactive only when served via
<code>make serve</code>; as a static file this section is read-only.</div>
<div class="filters">
  <label>release <select id="qrel"><option value="">all</option>{release_opts}</select></label>
  <button id="qfetch">Fetch items</button>
  <button id="qrun">Run queue</button>
  <span class="sub" id="qmsg"></span>
</div>
<div class="scroll"><table id="qitems" style="display:none">
<thead><tr><th>type</th><th>key</th><th>summary</th><th>release</th><th></th></tr></thead>
<tbody></tbody></table></div>
<h3 style="font-size:14px;margin:16px 0 6px">Queue</h3>
<div class="scroll"><table id="qtable">
<thead><tr><th>id</th><th>status</th><th>type</th><th>key</th><th>release</th><th>requested by</th><th>actions</th></tr></thead>
<tbody></tbody></table></div>

<h2>Recent runs</h2>
<div class="filters">
  <label>release <select id="frel"><option value="">all</option>{release_opts}
    <option value="__none__">(no release)</option></select></label>
  <span class="sub" id="frelcount"></span>
</div>
<div class="scroll"><table id="runs">
<thead><tr><th>run</th><th>trigger</th><th>key</th><th>time</th><th>overall</th>
<th>team review</th><th>release</th><th>E2E test repository</th><th>gate result</th></tr></thead>
<tbody>{runs_rows or '<tr><td colspan="9">no runs recorded yet</td></tr>'}</tbody>
</table></div>

<h2>Generated artifacts &mdash; test plans &amp; E2E tests per PR / story</h2>
<div class="sub">Latest run per key. Expand a key for the plan, scenarios, data, and the
committed test code (also via <code>bin/qa.py artifacts &lt;KEY&gt; --full</code>).</div>
{art_blocks or '<p class="sub">no artifacts yet — run make demo-pr / demo-jira</p>'}

<h2>Coverage matrix &mdash; app repos &times; E2E test repos</h2>
<div class="sub">Numbers = mapped tests (auto/confirmed). &#10003; = registry coverage
without cataloged tests yet. Rows in red have no E2E coverage at all.</div>
<div class="scroll"><table>
<thead><tr><th></th>{matrix_head}</tr></thead><tbody>{matrix_rows}</tbody>
</table></div>

<h2>Test knowledge catalog</h2>
<div class="filters">
  <label>repo <select id="frepo"><option value="">all</option>{repo_opts}</select></label>
  <label>status <select id="fstatus"><option value="">all</option>
    <option>auto</option><option>confirmed</option>
    <option>needs_review</option><option>orphan</option></select></label>
  <label>search <input id="fq" placeholder="title / file / app repo"></label>
  <span class="sub" id="fcount"></span>
</div>
<div class="scroll"><table id="cat">
<thead><tr><th>test repo</th><th>file</th><th>title</th><th>app repos</th>
<th>conf</th><th>evidence</th><th>status</th></tr></thead>
<tbody>{cat_rows or '<tr><td colspan="7">catalog empty — run make demo-bootstrap</td></tr>'}</tbody>
</table></div>

<script>
const frepo=document.getElementById('frepo'), fstatus=document.getElementById('fstatus'),
      fq=document.getElementById('fq'), fcount=document.getElementById('fcount'),
      rows=[...document.querySelectorAll('#cat tbody tr')];
function apply() {{
  const q=fq.value.toLowerCase(); let n=0;
  rows.forEach(r => {{
    const ok=(!frepo.value || r.dataset.repo===frepo.value)
      && (!fstatus.value || r.dataset.status===fstatus.value)
      && (!q || r.textContent.toLowerCase().includes(q));
    r.style.display = ok ? '' : 'none'; if (ok) n++;
  }});
  fcount.textContent = n + ' / ' + rows.length + ' tests';
}}
[frepo,fstatus].forEach(x=>x.addEventListener('change',apply));
fq.addEventListener('input',apply); apply();

const frel=document.getElementById('frel'), frelcount=document.getElementById('frelcount'),
      runRows=[...document.querySelectorAll('#runs tbody tr')];
function applyRel() {{
  const v=frel.value, shown=new Set(), all=new Set();
  runRows.forEach(r => {{
    all.add(r.dataset.run);
    const rel=r.dataset.release||'';
    const ok = !v || (v==='__none__' ? rel==='' : rel===v);
    r.style.display = ok ? '' : 'none'; if (ok) shown.add(r.dataset.run);
  }});
  frelcount.textContent = shown.size + ' / ' + all.size + ' runs';
}}
frel.addEventListener('change',applyRel); applyRel();

// --- Fetch & queue (active only when served by bin/dashboard_server.py) ---
const qrel=document.getElementById('qrel'), qmsg=document.getElementById('qmsg'),
      qitems=document.getElementById('qitems'), qtable=document.getElementById('qtable');
const served = location.protocol.startsWith('http');
function say(t) {{ qmsg.textContent=t; }}
if (!served) {{
  say('static file — start the server with: make serve');
  // export links need the server; hide them in static mode
  document.querySelectorAll('.served-only').forEach(e => e.style.display='none');
}}

async function api(path, opts) {{
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json()).error || r.status);
  return r.json();
}}
function keyOf(i) {{ return i.mode==='pr' ? 'PR-'+i.target+'-'+i.pr : i.target; }}

async function qAction(path, id) {{
  try {{ await api(path, {{method:'POST', headers:{{'Content-Type':'application/json'}},
                          body: JSON.stringify({{id}})}}); }}
  catch (e) {{ say(e.message); }}
  refreshQueue();
}}

async function refreshQueue() {{
  if (!served) return;
  const q = await api('/api/queue');
  qtable.tBodies[0].innerHTML = q.length ? q.map(i => {{
    const acts = [];
    if (i.status === 'failed')
      acts.push('<button data-act="requeue" data-id="'+i.id+'">re-queue</button>');
    if (i.status !== 'running')
      acts.push('<button data-act="remove" data-id="'+i.id+'">remove</button>');
    return '<tr><td><code>'+i.id+'</code></td><td>'+i.status+
      (i.status==='failed' && i.exit_code!=null ? ' (exit '+i.exit_code+')' : '')+'</td>'+
      '<td>'+i.mode+'</td><td><strong>'+keyOf(i)+'</strong></td>'+
      '<td>'+(i.release||'&#8212;')+'</td><td>'+(i.requested_by||'&#8212;')+'</td>'+
      '<td>'+(acts.join(' ')||'&#8212;')+'</td></tr>';
  }}).join('') : '<tr><td colspan="7">queue is empty</td></tr>';
  qtable.tBodies[0].querySelectorAll('button').forEach(b =>
    b.addEventListener('click', () => qAction('/api/queue/'+b.dataset.act, b.dataset.id)));
  return q;
}}

document.getElementById('qfetch').addEventListener('click', async () => {{
  if (!served) return say('static file — start the server with: make serve');
  try {{
    say('fetching...');
    const items = await api('/api/items?release='+encodeURIComponent(qrel.value));
    qitems.style.display='';
    qitems.tBodies[0].innerHTML = items.length ? items.map((i,n) =>
      '<tr><td>'+i.mode+'</td><td><strong>'+i.key+'</strong></td><td>'+i.summary+'</td>'+
      '<td>'+(i.release||'&#8212;')+'</td><td><button data-n="'+n+'" '+(i.queued?'disabled':'')+'>'+
      (i.queued?'queued':'queue')+'</button></td></tr>').join('')
      : '<tr><td colspan="5">no items for this release</td></tr>';
    qitems.tBodies[0].querySelectorAll('button').forEach(b => b.addEventListener('click', async () => {{
      const i = items[+b.dataset.n];
      await api('/api/queue', {{method:'POST', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{mode:i.mode, target:i.target, pr:i.pr, release:i.release}})}});
      b.disabled = true; b.textContent = 'queued'; refreshQueue();
    }}));
    say(items.length+' item(s)');
  }} catch (e) {{ say('fetch failed: '+e.message); }}
}});

document.getElementById('qrun').addEventListener('click', async () => {{
  if (!served) return say('static file — start the server with: make serve');
  try {{
    await api('/api/queue/run', {{method:'POST'}});
    say('queue running... (statuses refresh automatically)');
    const t = setInterval(async () => {{
      const q = await refreshQueue();
      if (!q.some(i => i.status==='queued' || i.status==='running')) {{
        clearInterval(t); say('queue drained — reload the page for new runs');
      }}
    }}, 3000);
  }} catch (e) {{ say(e.message); }}
}});
refreshQueue();

document.querySelectorAll('button.pubconf').forEach(b => b.addEventListener('click', async () => {{
  b.disabled = true; b.textContent = 'publishing...';
  try {{
    const r = await api('/api/export/confluence', {{method:'POST',
      headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{key: b.dataset.key}})}});
    b.textContent = 'published'; say(r.result);
  }} catch (e) {{ b.disabled = false; b.textContent = 'publish to Confluence'; say(e.message); }}
}}));
</script>
</body></html>"""

out = ROOT / "reports/dashboard.html"
out.write_text(page, encoding="utf-8")
print(f"dashboard written: {out} ({len(runs)} runs, {len(catalog)} catalog entries)")
