#!/usr/bin/env python3
"""Generate reports/dashboard.html — the QA operations dashboard.

Implements the "QA Dashboard" Claude Design (project: QA Dashboard UI redesign):
sidebar navigation over seven views (Overview, Intake & queue, Runs & reviews,
Artifacts, Test catalog, Repositories, Settings), SentinelRAG design tokens (light + dark), semantic
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


def specs_from_diff(diff_text):
    """Extract each file a gate commit touched (a `git show HEAD`), with enough to
    render a real before/after comparison — not just the added lines.

    Per file: {path, change ('new'|'updated'|'deleted'), added [str], removed [str],
    hunk [{t: 'add'|'del'|'ctx', text}], code (added joined), is_catalog, lang}. The
    `hunk` preserves +/-/context order so the UI can colour a unified diff; `code` is
    the clean added content, still handy for a brand-new spec.
    """
    files, cur = [], None
    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            if cur:
                files.append(cur)
            cur = {"a_path": "", "b_path": "", "new": False, "deleted": False,
                   "added": [], "removed": [], "hunk": []}
        elif cur is None:
            continue
        elif line.startswith("new file"):
            cur["new"] = True
        elif line.startswith("deleted file"):
            cur["deleted"] = True
        elif line.startswith("--- a/"):
            cur["a_path"] = line[6:]
        elif line.startswith("+++ b/"):
            cur["b_path"] = line[6:]
        elif line.startswith("@@"):
            cur["hunk"].append({"t": "meta", "text": line})
        elif line.startswith("+") and not line.startswith("+++"):
            cur["added"].append(line[1:])
            cur["hunk"].append({"t": "add", "text": line[1:]})
        elif line.startswith("-") and not line.startswith("---"):
            cur["removed"].append(line[1:])
            cur["hunk"].append({"t": "del", "text": line[1:]})
        elif line.startswith(" ") and cur["hunk"]:      # context only inside a hunk
            cur["hunk"].append({"t": "ctx", "text": line[1:]})
    if cur:
        files.append(cur)
    ext_lang = {"js": "javascript", "ts": "typescript", "py": "python",
                "json": "json", "jsonl": "json", "java": "java"}
    out = []
    for f in files:
        # a deleted file's +++ is /dev/null, so fall back to the a/ path
        path = f["b_path"] if f["b_path"] and f["b_path"] != "/dev/null" else f["a_path"]
        if not path:
            continue
        change = "new" if f["new"] else "deleted" if f["deleted"] else "updated"
        ext = path.rsplit(".", 1)[-1].lower()
        out.append({"path": path, "change": change,
                    "new": f["new"], "deleted": f["deleted"],
                    "added": f["added"], "removed": f["removed"], "hunk": f["hunk"],
                    "code": "\n".join(f["added"]),
                    "is_catalog": path.endswith(".jsonl"),
                    "lang": ext_lang.get(ext, "")})
    return out


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
    # Advisory critic score. Rendered next to (never instead of) the gate outcome —
    # the point is that a "weak" score sits beside a green "committed" without
    # contradicting it, because it did not and cannot gate the commit.
    c = r.get("critic")
    if c:
        cls = {"accept": "success", "review": "warning", "weak": "danger"}.get(
            c.get("verdict"), "muted")
        tip = (f'{c.get("verdict", "")} — {c.get("noise_count", 0)}'
               f'/{c.get("specs_reviewed", 0)} specs flagged noisy. '
               f'{c.get("rationale", "")} (advisory: never gates a commit)')
        critic_cell = (f'<span class="chip chip-{cls}" title="{esc(tip)}">'
                       f'{c.get("score", 0):.2f}</span>')
    else:
        critic_cell = '<span class="muted sm">—</span>'
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
        f'<td class="nowrap">{critic_cell}</td>'
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

    if r.get("trigger", {}).get("type") == "pr":
        # The coverage-delta report Workflow A posts on the PR, rebuilt from the
        # run record so it stays viewable/downloadable long after the run.
        try:
            import pr_comment
            cov = pr_comment.from_record(r)
        except Exception:
            cov = ""
        if cov:
            inner += (
                f'<div class="art-sec"><div class="art-row">'
                f'<h3>PR coverage report</h3><span class="spacer"></span>'
                f'<a class="btn btn-sm info" '
                f'href="/api/pr-coverage?key={esc(key)}&download=1">Download md</a>'
                f'</div><pre>{esc(cov)}</pre></div>')

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
            specs = specs_from_diff(diff_text)
            code_files = [s for s in specs if not s["is_catalog"]]
            catalog_files = [s for s in specs if s["is_catalog"]]
            # The generated test CODE, rendered clean and expanded — one titled block
            # per spec (the added lines, i.e. the whole file for a new spec), so a
            # reviewer reads the tests without wading through diff markers.
            chip_for = {"new": "success", "updated": "warning", "deleted": "danger"}
            blocks = ""
            for s in code_files:
                change = s["change"]
                counts = (f'{" · +" + str(len(s["added"])) if s["added"] else ""}'
                          f'{" −" + str(len(s["removed"])) if s["removed"] else ""}')
                head = (
                    f'<div class="spec-head"><code class="mono">{esc(s["path"])}</code>'
                    f'<span class="chip chip-{chip_for[change]} sm">{change}</span>'
                    f'<span class="muted sm">{esc(g["test_repo"])}{counts}</span></div>')
                if change == "new":
                    # Nothing to compare against — show the clean new file.
                    body = (f'<pre class="code lang-{esc(s["lang"] or "text")}">'
                            f'{esc(s["code"] or "(no added lines)")}</pre>')
                else:
                    # Updated or deleted: render the before/after comparison as a
                    # coloured unified diff (removed red, added green, context muted).
                    rows = ""
                    for h in s["hunk"]:
                        cls = {"add": "d-add", "del": "d-del",
                               "ctx": "d-ctx", "meta": "d-meta"}[h["t"]]
                        sign = {"add": "+", "del": "−", "ctx": " ", "meta": ""}[h["t"]]
                        rows += (f'<div class="d-line {cls}">'
                                 f'<span class="d-sign">{sign}</span>'
                                 f'<span class="d-text">{esc(h["text"])}</span></div>')
                    body = f'<div class="diffview">{rows or "(no hunks)"}</div>'
                blocks += f'<div class="spec-file">{head}{body}</div>'
            cat_note = ""
            if catalog_files:
                cat_note = ('<div class="sm muted spec-catalog">Catalog sidecar: '
                            + ", ".join(f'<code>{esc(s["path"])}</code>'
                                        for s in catalog_files)
                            + ' — every spec is born-mapped in the same commit.</div>')
            header = (f'<div class="art-row"><h3>Generated test code</h3>'
                      f'<span class="spacer"></span>'
                      f'<span class="mono sm muted">{esc(g["test_repo"])} @ '
                      f'{esc((g.get("commit") or "")[:10])}</span></div>')
            if blocks:
                inner += (f'<div class="art-sec">{header}{blocks}{cat_note}'
                          f'<button class="code-toggle"><span class="chev">▶</span> '
                          f'Raw commit diff</button>'
                          f'<pre class="code hidden">{esc(diff_text)}</pre></div>')
            else:
                inner += (
                    f'<div class="art-sec"><button class="code-toggle">'
                    f'<span class="chev">▶</span> Generated test code — '
                    f'<code>{esc(g["test_repo"])} @ {esc(g.get("commit") or "")}</code>'
                    f'</button><pre class="code hidden">{esc(diff_text)}</pre></div>')

    head = (f'<div class="art-head"><h2>{esc(key)}</h2>{chip(r.get("overall", "?"))}'
            + (chip(rstat) if rstat else "")
            + f'<span class="mono sm muted">run {esc(r["run_id"])}</span>'
            + (f'<span class="mono sm muted">· release {esc(release)}</span>' if release else ""))
    head += "</div>"
    art_panels_html += (f'<article class="card art-panel{"" if first else " hidden"}" '
                        f'data-art-panel="{esc(key)}">{head}{inner or chr(10)}</article>')
    first = False

# ---------------------------------------------------------------- trace view
import trace as trace_lib   # ours (engine/lib), shadows stdlib trace by path order

KIND_DOT = {"plan": "info", "run": "success", "review": "warning", "release": "muted"}
trace_keys_html, trace_panels_html = "", ""
_tfirst = True
for _tk in trace_lib.keys()[:12]:
    _tr = trace_lib.build(_tk)
    if not _tr["events"]:
        continue
    trace_keys_html += (
        f'<button class="art-key trace-key{" active" if _tfirst else ""}" '
        f'data-trace="{esc(_tk)}"><span class="strong sm">{esc(_tk)}</span>'
        f'<span class="sm muted">{len(_tr["events"])} event(s)'
        f'{" · " + esc(_tr["release"]) if _tr["release"] else ""}</span></button>')
    rows = ""
    for ev in _tr["events"]:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ev["ts"])) if ev["ts"] else "—"
        dot = KIND_DOT.get(ev["kind"], "muted")
        extra = ""
        if ev["kind"] == "run":
            m = ev["meta"]
            dot = {"committed": "success", "quarantined": "danger"}.get(
                m.get("overall"), "muted")
            _gcls = {"committed": "success", "no_changes": "muted",
                     "quarantined": "danger"}
            bits = "".join(
                f'<span class="chip chip-{_gcls.get(g["status"], "muted")} sm">'
                f'{esc(g["repo"])}: {esc(g["status"])}'
                f'{" @" + esc((g.get("commit") or "")[:7]) if g.get("commit") else ""}</span>'
                for g in m["gates"])
            c = m.get("critic")
            if c:
                ccls = {"accept": "success", "review": "warning",
                        "weak": "danger"}.get(c.get("verdict"), "muted")
                bits += (f'<span class="chip chip-{ccls} sm" title="advisory — never gates">'
                         f'critic {c.get("score")}</span>')
            files = "".join(f'<div class="mono sm muted">{esc(x.get("file", "?"))} '
                            f'({esc(x.get("action", "?"))})</div>'
                            for x in m.get("tests", [])[:6])
            extra = (f'<div class="chips" style="margin-top:4px">{bits}</div>{files}'
                     f'<div class="mono sm muted">run {esc(m.get("run_id") or "")}</div>')
        actor = f'<span class="muted sm"> — {esc(ev["actor"])}</span>' if ev.get("actor") else ""
        detail = f'<div class="sm muted">{esc(ev["detail"])}</div>' if ev.get("detail") else ""
        rows += (f'<div class="tl-row"><div class="tl-dot {dot}"></div>'
                 f'<div class="tl-body"><div class="tl-when mono sm muted">{when}</div>'
                 f'<div class="strong sm">{esc(ev["title"])}{actor}</div>'
                 f'{detail}{extra}</div></div>')
    head_chips = (
        (chip(_tr["plan_status"], "") if _tr["plan_status"] else "")
        + (chip(_tr["review_status"], "") if _tr["review_status"] else "")
        + (f'<span class="chip chip-muted">release {esc(_tr["release"])}</span>'
           if _tr["release"] else ""))
    trace_panels_html += (
        f'<article class="card art-panel trace-panel{"" if _tfirst else " hidden"}" '
        f'data-trace-panel="{esc(_tk)}">'
        f'<div class="art-head"><h2>{esc(_tk)}</h2>'
        f'<span class="pill">{esc(_tr["trigger_type"] or "?")}</span>{head_chips}</div>'
        f'<div class="tl">{rows}</div></article>')
    _tfirst = False

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

# ---------------------------------------------------------------- plans view
# Server-rendered like every other view, so the static snapshot (make dashboard,
# no server) shows real plans instead of an empty placeholder.
import plan_state
PLAN_CHIP = {"draft": ("draft", "muted"), "in_review": ("✎ in review", "warning"),
             "approved": ("✓ approved", "success"),
             "changes_requested": ("✗ changes requested", "danger")}
plan_rows = ""
for p in plan_state.summary():
    lbl, cls = PLAN_CHIP.get(p["status"], (p["status"] or "—", "muted"))
    plan_rows += (
        f'<tr><td class="strong">{esc(p["key"])}</td>'
        f'<td><span class="chip chip-{cls}">{esc(lbl)}</span></td>'
        f'<td>' + ('<span class="chip chip-success">✓ linked</span>' if p["linked"]
                   else '<span class="muted">—</span>') + '</td>'
        f'<td class="mono sm muted">{esc(p["generated_run"] or "—")}</td>'
        f'<td class="sm muted">{esc(p["note"] or "")}</td>'
        f'<td class="right"><button class="btn btn-sm plan-open" '
        f'data-key="{esc(p["key"])}">Review</button></td></tr>')
plan_rows = plan_rows or ('<tr><td colspan="6"><div class="empty">No test plans yet — '
                          'author one with <code>make plan KEY=PROJ-123</code>.'
                          "</div></td></tr>")
n_plans = len(plan_state.summary())
n_appr = sum(1 for p in plan_state.summary() if p["status"] == "approved")

# ---------------------------------------------------------------- repos view
import repo_admin
estate = repo_admin.summary()
app_rows = ""
for r in estate["app_repos"]:
    entry = {"name": r["name"], "kind": r["kind"], "scm": r.get("scm", ""),
             "url": r.get("url", ""), "domains": ", ".join(r.get("domains", [])),
             "testable_paths": ", ".join(r.get("testable_paths", [])),
             "contract": r.get("contract", ""), "route_table": r.get("route_table", ""),
             "consumes_services": ", ".join(r.get("consumes_services", []))}
    guid = []
    if r["has_notes"]:
        guid.append("notes")
    guid += [pathlib.PurePosixPath(p).name for p in r["local_files"]]
    app_rows += (
        f'<tr><td class="strong">{esc(r["name"])}</td>'
        f'<td><span class="pill">{esc(r["kind"])}</span></td>'
        f'<td class="mono sm muted">{esc(r.get("scm", "?"))}</td>'
        f'<td class="sm">{esc(", ".join(r.get("domains", []))) or "—"}</td>'
        f'<td class="mono sm muted">{esc(r.get("contract") or r.get("route_table") or "—")}</td>'
        f'<td class="sm">{esc(", ".join(r["covered_by"])) if r["covered_by"] else chip("gap")}</td>'
        f'<td class="sm muted">{esc(", ".join(guid)) or "—"}</td>'
        f'<td class="right nowrap">'
        f'<button class="btn btn-sm repo-edit" data-form="app" '
        f'data-entry="{esc(json.dumps(entry))}">Edit</button> '
        f'<button class="btn btn-sm danger repo-del" data-name="{esc(r["name"])}" '
        f'data-section="app">Remove</button></td></tr>')

test_rows = ""
for t in estate["test_repos"]:
    entry = {"name": t["name"], "layer": t.get("layer", ""),
             "framework": t.get("framework", ""), "scm": t.get("scm", ""),
             "url": t.get("url", ""),
             "specs": t.get("layout", {}).get("specs", ""),
             "fixtures": t.get("layout", {}).get("fixtures", ""),
             "scope": ", ".join(t.get("scope", []))}
    test_rows += (
        f'<tr><td class="strong">{esc(t["name"])}</td>'
        f'<td><span class="pill">{esc(t.get("layer", "?"))}</span></td>'
        f'<td class="mono sm muted">{esc(t.get("framework", "?"))}</td>'
        f'<td class="sm">{esc(", ".join(t.get("covers", []))) or "—"}</td>'
        f'<td class="nowrap"><input class="h32 scope-in" data-repo="{esc(t["name"])}" '
        f'value="{esc(", ".join(t.get("scope", [])))}" placeholder="app repos (csv)" '
        f'style="width:200px"> '
        f'<button class="btn btn-sm scope-save" data-repo="{esc(t["name"])}">Save</button></td>'
        f'<td class="right nowrap">'
        f'<button class="btn btn-sm repo-edit" data-form="test" '
        f'data-entry="{esc(json.dumps(entry))}">Edit</button> '
        f'<button class="btn btn-sm danger repo-del" data-name="{esc(t["name"])}" '
        f'data-section="test">Remove</button></td></tr>')

all_repo_opts = "".join(f"<option>{esc(r['name'])}</option>"
                        for r in estate["app_repos"] + estate["test_repos"])

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

NAV = [("overview", "◧", "Overview"), ("wizard", "✦", "Guided run"),
       ("queue", "⇥", "Intake & queue"),
       ("plans", "✎", "Test plans"),
       ("runs", "▶", "Runs & reviews"), ("trace", "⇢", "Trace"),
       ("artifacts", "❏", "Artifacts"),
       ("catalog", "☰", "Test catalog"), ("repos", "⛁", "Repositories"),
       ("settings", "⚙", "Settings")]
TITLES = {"overview": "Overview", "wizard": "Guided run — PR or JIRA, step by step",
          "queue": "Intake & work queue",
          "plans": "Test plans — review & approval",
          "runs": "Runs & team reviews", "artifacts": "Generated artifacts",
          "catalog": "Test knowledge catalog", "repos": "Repositories & mapping",
          "settings": "Settings & integrations"}
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
/* Manual theme toggle: an explicit choice (persisted in localStorage, stamped as
   data-theme on <html>) must beat the OS preference IN BOTH DIRECTIONS — dark on a
   light OS and light on a dark OS. Token-level overrides, same palettes as above. */
:root[data-theme="dark"] {
  --sr-bg: hsl(222.2 47.4% 7%); --sr-bg-muted: hsl(217.2 32.6% 17.5%);
  --sr-fg: hsl(210 40% 98%); --sr-fg-muted: hsl(215 20.2% 65.1%);
  --sr-primary: hsl(210 40% 98%); --sr-primary-90: hsl(210 40% 88%);
  --sr-fg-on-primary: hsl(222.2 47.4% 11.2%);
  --sr-border: hsl(217.2 32.6% 22%); --sr-input: hsl(217.2 32.6% 22%);
}
:root[data-theme="light"] {
  --sr-bg: hsl(0 0% 100%); --sr-bg-muted: hsl(210 40% 96.1%);
  --sr-fg: hsl(222.2 47.4% 11.2%); --sr-fg-muted: hsl(215.4 16.3% 46.9%);
  --sr-fg-on-primary: hsl(210 40% 98%);
  --sr-primary: hsl(222.2 47.4% 11.2%); --sr-primary-90: hsl(222.2 47.4% 18%);
  --sr-border: hsl(214.3 31.8% 91.4%); --sr-input: hsl(214.3 31.8% 91.4%);
}
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
/* A chip inside a column-flex .stack label would otherwise stretch to the full
   field width and read as a coloured band rather than a tag. */
.stack > .lbl { display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
.stack .chip { align-self:flex-start; flex:none; }
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
/* Guided run (wizard): a numbered step ladder whose state is the engine's, not
   the page's — every step reflects real queue/run/plan/review state. */
.wz-row { display:flex; align-items:flex-end; gap:10px; flex-wrap:wrap; }
.wz-steps { list-style:none; margin:0; padding:0; display:flex;
            flex-direction:column; gap:8px; counter-reset:wz; }
.wz-steps li { display:flex; align-items:flex-start; gap:10px; padding:10px 12px;
               border:1px solid var(--sr-border); border-radius:8px;
               background:var(--sr-bg); }
.wz-steps li::before { counter-increment:wz; content:counter(wz);
  flex:0 0 22px; height:22px; border-radius:50%; display:grid; place-items:center;
  font-size:11px; font-weight:700; background:var(--sr-bg-muted); color:var(--sr-fg-muted); }
.wz-steps li.done::before { content:"✓"; background:#16a34a22; color:#16a34a; }
.wz-steps li.running::before { content:"◐"; background:#2563eb22; color:#2563eb; }
.wz-steps li.blocked::before { content:"!"; background:#d9770622; color:#d97706; }
.wz-steps li.failed::before { content:"✗"; background:#dc262622; color:#dc2626; }
.wz-steps .wz-lb { font-weight:600; font-size:13px; }
.wz-steps .wz-dt { font-size:12px; color:var(--sr-fg-muted); }
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
/* Generated test code: one titled block per spec file */
.spec-file { margin-bottom:14px; }
.spec-head { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:6px; }
.spec-head code { font-size:12px; font-weight:600; color:var(--sr-fg);
  background:var(--sr-bg-muted); border:1px solid var(--sr-border);
  border-radius:5px; padding:1px 7px; }
.spec-file pre.code { margin-top:0; white-space:pre; max-height:420px; overflow:auto; }
.spec-catalog { margin:2px 0 12px; }
/* Trace timeline */
.tl { padding:6px 24px 18px; }
.tl-row { display:flex; gap:14px; position:relative; padding:10px 0; }
.tl-row::before { content:""; position:absolute; left:5px; top:26px; bottom:-10px;
  width:2px; background:var(--sr-border); }
.tl-row:last-child::before { display:none; }
.tl-dot { flex:none; width:12px; height:12px; border-radius:50%; margin-top:4px;
  border:2px solid var(--sr-border); background:var(--sr-bg); z-index:1; }
.tl-dot.success { background:var(--sr-success-fg); border-color:var(--sr-success-fg); }
.tl-dot.danger  { background:var(--sr-danger-fg);  border-color:var(--sr-danger-fg); }
.tl-dot.warning { background:var(--sr-warning-fg); border-color:var(--sr-warning-fg); }
.tl-dot.info    { background:var(--sr-info-fg, #5b8def); border-color:var(--sr-info-fg, #5b8def); }
.tl-body { flex:1; min-width:0; }
.tl-when { margin-bottom:1px; }
/* before/after comparison for updated & deleted specs (coloured unified diff) */
.diffview { border:1px solid var(--sr-border); border-radius:8px; overflow:auto;
  max-height:460px; font-family:var(--sr-font-mono); font-size:12px; line-height:1.55;
  background:var(--sr-bg-muted); }
.d-line { display:flex; white-space:pre; }
.d-sign { flex:none; width:1.6em; text-align:center; opacity:.7;
  user-select:none; border-right:1px solid var(--sr-border); }
.d-text { padding-left:8px; flex:1; }
.d-add { background:color-mix(in srgb, var(--sr-success-fg) 15%, transparent); }
.d-del { background:color-mix(in srgb, var(--sr-danger-fg) 15%, transparent); }
.d-add .d-sign { color:var(--sr-success-fg); }
.d-del .d-sign { color:var(--sr-danger-fg); }
.d-meta { color:var(--sr-fg-muted); background:transparent; font-style:italic; }
.d-ctx { color:var(--sr-fg); }
.stale-banner { background:var(--sr-warning-bg); color:var(--sr-warning-fg);
  border:1px solid var(--sr-warning-fg); border-radius:8px; padding:10px 14px;
  margin:16px 24px 0; font-size:13px; font-weight:600; }
.hidden { display:none; }
[data-view] { display:none; flex-direction:column; gap:24px; }
[data-view].on { display:flex; }

#toast { position:fixed; bottom:20px; right:20px; background:var(--sr-primary);
  color:var(--sr-fg-on-primary); border-radius:8px; padding:10px 16px; font-size:13px;
  box-shadow:var(--sr-shadow-md); animation:srfade .2s ease; z-index:50; max-width:360px; }
/* Narrow screens: collapse the sidebar into a horizontal, scrollable nav strip.
   (It used to be display:none, which left no way at all to change view.) */
@media (max-width: 900px) {
  body { flex-direction:column; }
  aside { width:auto; flex:none; height:auto; position:static;
    border-right:none; border-bottom:1px solid var(--sr-border); }
  .logo-row { border-bottom:none; }
  .logo-s { display:none; }
  nav.side { flex-direction:row; overflow-x:auto; padding:8px; gap:4px;
    -webkit-overflow-scrolling:touch; }
  .nav-item { flex:0 0 auto; }
  .nav-lb { white-space:nowrap; }
  .badge { margin-left:4px; }
  .side-foot { display:none; }
  header { padding:0 14px; }
  .content { padding:16px; }
  .art-layout { grid-template-columns:1fr; }
  .art-list { position:static; }
  .art-grid { grid-template-columns:1fr; }
}
"""

# ---------------------------------------------------------------- client JS
JS = """
const served = location.protocol.startsWith('http');
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
// Every client-rendered cell goes through this — queue items and fetched JIRA
// summaries are external data and must never reach innerHTML unescaped.
const escHtml = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
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
// A launch is either resolved (real conversation id + link) or still starting.
// Reporting a start-task id as the conversation id is what produced links that
// 404ed while the conversation existed in OpenHands under a different id.
function ohLaunchMsg(r, suffix) {
  if (r.conversation_id) return 'OpenHands conversation started: ' + r.conversation_id + suffix;
  if (r.pending) return 'OpenHands accepted the request (start task '
    + (r.start_task_id || '?') + ') — the conversation id appears in "OpenHands agent '
    + 'runs" once it registers; no link yet';
  return 'OpenHands accepted the request' + suffix;
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    // A 501/404 from an /api/ path means the SERVER process predates this page's
    // code (it renders the page fresh from disk but keeps its own old handlers).
    // Say so explicitly — "501" alone sent users hunting for a bug that a restart
    // fixes. json() also fails on those responses (HTML error body), hence the catch.
    let msg, hint;
    // Keep the `hint` an endpoint sends alongside `error`. Several handlers answer a
    // rejection with the exact fields to fix (an unregistered PR URL names the repo
    // entry to add) — dropping it here left the user with the complaint and none of
    // the remedy, which is the half that matters.
    try { const b = await r.json(); msg = b.error; hint = b.hint; } catch (e) { msg = null; }
    if (msg && hint) msg = msg + ' — ' + hint;
    if (!msg && (r.status === 501 || r.status === 404))
      msg = 'The dashboard server is running older code than this page (HTTP '
        + r.status + '). Restart it — stop and re-run make serve — then retry.';
    throw new Error(msg || ('HTTP ' + r.status));
  }
  return r.json();
}
const TITLES = { overview: 'Overview', wizard: 'Guided run — PR or JIRA, step by step',
  queue: 'Intake & work queue',
  plans: 'Test plans — review & approval',
  runs: 'Runs & team reviews', trace: 'Trace — story/PR to release',
  artifacts: 'Generated artifacts',
  catalog: 'Test knowledge catalog', repos: 'Repositories & mapping',
  settings: 'Settings & integrations' };
function go(view) {
  $$('[data-view]').forEach(v => v.classList.toggle('on', v.dataset.view === view));
  $$('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.go === view));
  $('#view-title').textContent = TITLES[view] || view;
  // Persist the active view in the URL hash: repo add/edit/remove, settings
  // saves and clears finish with location.reload(), and without this every
  // reload dumped the user back on Overview instead of the view they were in.
  try { history.replaceState(null, '', '#' + view); } catch (e) { /* file:// */ }
}
// Restore the view a mutation-reload came from (deep links work too).
(function () {
  const wanted = location.hash.replace('#', '');
  if (wanted && TITLES[wanted]) go(wanted);
})();
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
  // `[data-key]` is load-bearing, not decoration: `approve` is also a STYLE class
  // (the Test plans view's Approve button wears it), and without this the delegated
  // handler fired there too and POSTed /api/review with no key — the server answered
  // KeyError and the user saw a toast reading literally `'key'` while their actual
  // action succeeded. Match on the attribute this handler reads.
  const b = e.target.closest('button.approve[data-key]');
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
  if (k && k.dataset.trace !== undefined) {
    $$('.trace-key').forEach(x => x.classList.toggle('active', x === k));
    $$('.trace-panel').forEach(p => p.classList.toggle('hidden', p.dataset.tracePanel !== k.dataset.trace));
    return;
  }
  if (k) {
    $$('.art-key:not(.trace-key)').forEach(x => x.classList.toggle('active', x === k));
    $$('.art-panel:not(.trace-panel)').forEach(p => p.classList.toggle('hidden', p.dataset.artPanel !== k.dataset.art));
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
      if (i.status === 'failed') acts += '<button class="btn btn-sm qact" data-act="requeue" data-id="' + escHtml(i.id) + '">Re-queue</button> ';
      if (i.status !== 'running') acts += '<button class="btn btn-sm danger qact" data-act="remove" data-id="' + escHtml(i.id) + '">Remove</button>';
      return '<tr><td class="mono sm muted">' + escHtml(i.id) + '</td>' +
        '<td><span class="chip chip-' + cls + '">' + escHtml(lb + extra) + '</span></td>' +
        '<td><span class="pill">' + escHtml(i.mode) + '</span></td>' +
        '<td class="strong">' + escHtml(keyOf(i)) + '</td>' +
        '<td class="mono sm muted">' + escHtml(i.release || '—') + '</td>' +
        '<td class="muted">' + escHtml(i.requested_by || '—') + '</td>' +
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

// ---- guided run (wizard): sequences the EXISTING endpoints, polls one status
let wzTimer = null, wzKey = '', wzMode = 'pr';
function wzRender(d) {
  const steps = (d.steps || []).map(s =>
    '<li class="' + escHtml(s.state) + '"><div><div class="wz-lb">' +
    escHtml(s.label) + '</div>' +
    (s.detail ? '<div class="wz-dt">' + escHtml(s.detail) + '</div>' : '') +
    '</div></li>').join('');
  $('#wz-steps').innerHTML = steps || '<li>Nothing started yet.</li>';
  const files = (d.tests || []).map(t =>
    '<div class="sm"><code>' + escHtml(t.file || '?') + '</code> ' +
    '<span class="chip chip-success sm">' + escHtml(t.action || '?') + '</span></div>'
  ).join('');
  $('#wz-result').innerHTML = files
    ? '<h3 style="margin:6px 0 4px">Generated tests</h3>' + files +
      (d.run_id ? '<div class="sm muted" style="margin-top:6px">run ' +
        escHtml(d.run_id) + ' · <a href="/api/pr-coverage?key=' +
        encodeURIComponent(d.key) + '&download=1">download coverage report</a>' +
        ' · open <b>Artifacts</b> for the code and diff</div>' : '')
    : '';
  $('#wz-hint').textContent = d.busy
    ? 'Working… this runs asynchronously; you can leave this page and come back.'
    : (d.overall ? 'Last run: ' + d.overall : 'Idle.');
}
async function wzPoll() {
  if (!wzKey) return;
  try {
    const d = await api('/api/wizard/status?key=' + encodeURIComponent(wzKey) +
                        '&mode=' + wzMode);
    wzRender(d);
    clearTimeout(wzTimer);
    // Poll only while work is in flight — an idle wizard costs nothing.
    if (d.busy) wzTimer = setTimeout(wzPoll, 3000);
  } catch (err) { $('#wz-hint').textContent = err.message; }
}
if ($('#wz-mode')) {
  const syncFlow = () => {
    wzMode = $('#wz-mode').value;
    $('#wz-pr-inputs').classList.toggle('hidden', wzMode !== 'pr');
    $('#wz-jira-inputs').classList.toggle('hidden', wzMode !== 'jira');
    $('#wz-steps').innerHTML = ''; $('#wz-result').innerHTML = ''; wzKey = '';
  };
  $('#wz-mode').addEventListener('change', syncFlow);
  syncFlow();

  $('#wz-start-pr').addEventListener('click', async () => {
    if (needsServer()) return;
    const repo = $('#wz-repo').value.trim(), pr = $('#wz-pr').value.trim();
    // A pasted PR URL carries the repo and number (and the Stash project), so the
    // PR # box is optional when the first field is a URL — the server parses it.
    const isUrl = repo.includes('pull-requests') || repo.includes('/pull/');
    if (!repo || (!pr && !isUrl)) {
      toast('Enter the app repo and PR number — or paste the pull-request URL'); return; }
    try {
      const r = await api('/api/queue', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'pr', target: repo, pr: pr }) });
      // Derive the key from what the SERVER resolved, so a URL-driven run polls
      // the right key instead of one built from the raw URL text.
      wzKey = (r.item && r.item.pr)
        ? 'PR-' + r.item.target + '-' + r.item.pr
        : 'PR-' + repo + '-' + pr;
      wzMode = 'pr';
      await api('/api/queue/run', { method: 'POST' });
      toast('Analyzing ' + wzKey + ' — generation runs in the background');
      wzPoll();
    } catch (err) { toast(err.message); }   // api() already folds in any hint
  });

  const wzJiraKey = () => {
    const k = $('#wz-key').value.trim();
    if (!k) { toast('Enter a ticket key'); return null; }
    wzKey = k; wzMode = 'jira';
    return k;
  };
  $('#wz-start-plan').addEventListener('click', async () => {
    if (needsServer()) return;
    const k = wzJiraKey(); if (!k) return;
    try {
      await api('/api/queue', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'plan', target: k }) });
      await api('/api/queue/run', { method: 'POST' });
      toast('Authoring the plan for ' + k + ' — it stops for your approval');
      wzPoll();
    } catch (err) { toast(err.message); }
  });
  $('#wz-approve').addEventListener('click', async () => {
    if (needsServer()) return;
    const k = wzJiraKey(); if (!k) return;
    try {
      await api('/api/plans/status', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: k, status: 'approved' }) });
      toast('Plan approved — you can generate tests now');
      wzPoll();
    } catch (err) { toast(err.message); }
  });
  $('#wz-generate').addEventListener('click', async () => {
    if (needsServer()) return;
    const k = wzJiraKey(); if (!k) return;
    try {
      await api('/api/plans/generate', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: k }) });
      await api('/api/queue/run', { method: 'POST' });
      toast('Generating tests from the approved plan — running in the background');
      wzPoll();
    } catch (err) { toast(err.message); }
  });
  $('#wz-link').addEventListener('click', async () => {
    if (needsServer()) return;
    const k = wzJiraKey(); if (!k) return;
    try {
      // Attach FIRST so the comment can cite the attachment, then post the comment
      // (the J6 deliverable). Attaching needs an approved plan; when it is not
      // approved yet we still post the comment rather than failing the whole step.
      let attached = false;
      try {
        await api('/api/plans/link', { method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: k, format: 'pdf' }) });
        attached = true;
      } catch (e) { /* not approved, or attach unavailable — comment still stands */ }
      await api('/api/plans/comment', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: k }) });
      toast('Commented on ' + k + ': plan + tests linked'
            + (attached ? ' (plan attached)' : ''));
      wzPoll();
    } catch (err) { toast(err.message); }
  });
}

// ---- fetch work
$('#fetch-btn').addEventListener('click', async () => {
  if (needsServer()) return;
  const btn = $('#fetch-btn');
  btn.disabled = true; btn.textContent = 'Fetching…';
  try {
    const items = await api('/api/items?release=' + encodeURIComponent($('#fetch-rel').value));
    const card = $('#fetched-wrap'); card.classList.remove('hidden');
    $('#fetched-table tbody').innerHTML = items.length ? items.map((i, n) =>
      '<tr><td><span class="pill">' + escHtml(i.mode) + '</span></td>' +
      '<td class="strong">' + escHtml(i.key) + '</td><td>' + escHtml(i.summary) + '</td>' +
      '<td class="mono sm muted">' + escHtml(i.release || '—') + '</td>' +
      '<td class="right">' +
      (i.mode === 'jira'
        ? '<button class="btn btn-sm fq" data-n="' + n + '" data-mode="plan" ' +
          (i.plan_queued ? 'disabled' : '') + '>' +
          (i.plan_queued ? 'Plan queued' : 'Plan only') + '</button> '
        : '') +
      '<button class="btn btn-sm fq" data-n="' + n + '" ' +
      (i.queued ? 'disabled' : '') + '>' + (i.queued ? 'Queued' : 'Queue') + '</button></td></tr>'
    ).join('') : '<tr><td colspan="5"><div class="empty">No items for this release.</div></td></tr>';
    $('#fetch-msg').textContent = items.length + ' item(s) found';
    $$('#fetched-table button.fq').forEach(b => b.addEventListener('click', async () => {
      const i = items[+b.dataset.n];
      const mode = b.dataset.mode === 'plan' ? 'plan' : i.mode;
      try {
        await api('/api/queue', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: mode, target: i.target, pr: i.pr, release: i.release }) });
        b.disabled = true; b.textContent = 'Queued';
        toast('Queued ' + i.key + (mode === 'plan'
          ? ' (plan only — stops for human approval)' : '') +
          ' — press Run queue to execute'); refreshQueue();
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
$('#inl-plan-oh').addEventListener('click', async () => {
  if (needsServer()) return;
  const text = $('#inl-text').value.trim();
  if (!text) { toast('Paste the ticket description first'); return; }
  const key = $('#inl-key').value.trim();
  if (!key) {
    // The OpenHands test-plan agent runs `pipeline.sh plan <KEY>` against the
    // tracker — without a real ticket key that command cannot succeed. The
    // pasted-only path is "Queue inline ticket" (synthesizes the ticket).
    toast('Enter the real ticket key for the OpenHands path — or use ' +
      '"Queue inline ticket" for pasted-only text');
    return;
  }
  try {
    const r = await api('/api/openhands/agent', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent: 'test-plan', target: key,
        description: text }) });
    refreshOpenHands();
    toast(ohLaunchMsg(r, ' — the plan will stop for human approval; track it under Test plans'));
  } catch (err) { toast(err.message); }
});
refreshQueue();

// ---- plan authoring (Test plans view)
if ($('#plan-author')) {
  $('#plan-author').addEventListener('click', async () => {
    if (needsServer()) return;
    const key = $('#plan-new-key').value.trim();
    if (!key) { toast('Enter a ticket key'); return; }
    try {
      await api('/api/queue', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'plan', target: key }) });
      toast('Queued plan authoring for ' + key +
        ' — run the queue from Intake; the plan stops here for approval');
    } catch (err) { toast(err.message); }
  });
  $('#plan-author-oh').addEventListener('click', async () => {
    if (needsServer()) return;
    const key = $('#plan-new-key').value.trim();
    if (!key) { toast('Enter a ticket key'); return; }
    try {
      const r = await api('/api/openhands/agent', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent: 'test-plan', target: key }) });
      // Show it immediately in the card on THIS page — the whole point of
      // recording the launch is that the user can follow it from where they were.
      refreshOpenHands();
      toast(ohLaunchMsg(r, ' — tracked in "OpenHands agent runs" below'));
    } catch (err) { toast(err.message); }
  });
}

// ---- OpenHands agent runs (fed by the receiver's webhook routes)
const OH_CHIP = { launched: ['◇ launched','info'],
  running: ['● running','warning'], finished: ['✓ finished','success'],
  complete: ['✓ complete','success'], completed: ['✓ complete','success'],
  error: ['✗ error','danger'], stopped: ['stopped','muted'], cancelled: ['cancelled','muted'] };
// Rendered into EVERY .oh-card on the page, not just the Runs view: an agent
// launched from Test plans used to leave the user on a page that could not show
// it. The launcher and the tracker belong next to each other.
async function refreshOpenHands() {
  const cards = document.querySelectorAll('.oh-card');
  if (!served || !cards.length) return;
  try {
    const rows = await api('/api/openhands');
    cards.forEach(card => {
      const tb = card.querySelector('tbody'), count = card.querySelector('.oh-count');
      if (!rows.length) { card.classList.add('hidden'); return; }
      card.classList.remove('hidden');
      tb.innerHTML = rows.map(r => {
        const [lb, cls] = OH_CHIP[r.status] || [r.status || 'running', 'info'];
        const id = escHtml(r.conversation_id.slice(0, 28));
        // Link out when we know the URL — recording the id is only useful if the
        // user can actually reach the conversation.
        const idCell = r.url
          ? '<a href="' + escAttr(r.url) + '" target="_blank" rel="noopener">' + id + '</a>'
          : id;
        return '<tr><td class="mono sm">' + idCell +
          (r.title ? '<div class="sm muted">' + escHtml(r.title.slice(0, 48)) + '</div>' : '') +
          '</td><td><span class="chip chip-' + cls + '">' + escHtml(lb) + '</span>' +
          (r.error ? '<div class="sm danger-fg">' + escHtml(r.error.slice(0, 70)) + '</div>' : '') +
          '</td><td class="sm">' + escHtml(r.repo || r.key || '—') + '</td>' +
          '<td class="num">' + r.event_count + '</td>' +
          '<td class="sm muted">' + escHtml(r.last_event || '—') + '</td></tr>';
      }).join('');
      count.textContent = rows.length + ' conversation(s) · ' +
        rows.filter(r => !r.terminal).length + ' in flight';
    });
  } catch (err) { /* advisory panel — never block the view it sits in */ }
}
refreshOpenHands();

// ---- traceability matrix (roadmap 3.1)
async function refreshTraceMatrix() {
  const tb = document.querySelector('#tmx-table tbody');
  if (!served || !tb) return;
  try {
    const d = await api('/api/trace-matrix');
    if (!d.rows.length) { $('#tmx-card').classList.add('hidden'); return; }
    $('#tmx-card').classList.remove('hidden');
    tb.innerHTML = d.rows.map(r => {
      const noTest = !r.file;
      const ci = (r.ci_runs !== '' && r.ci_runs !== undefined && r.ci_runs !== null)
        ? escHtml(String(r.ci_last || '?')) + ' (' + r.ci_failures + '/' + r.ci_runs + ' failed)'
        : '—';
      return '<tr' + (noTest ? ' style="outline:1px solid var(--sr-warning-fg)"' : '') + '>' +
        '<td class="mono sm">' + escHtml(r.key) + '</td>' +
        '<td class="sm">' + escHtml(r.scenario_id || '—') +
          (r.scenario_title ? '<div class="muted sm">' + escHtml(r.scenario_title) + '</div>' : '') + '</td>' +
        '<td class="mono sm">' + (noTest
          ? '<span class="chip chip-warning">no test yet</span>' : escHtml(r.file)) + '</td>' +
        '<td class="sm">' + escHtml(r.test_repo || '—') + '</td>' +
        '<td class="sm">' + escHtml(r.gate_status || '—') + '</td>' +
        '<td class="mono sm">' + escHtml(r.commit || '—') + '</td>' +
        '<td class="sm">' + ci + '</td></tr>';
    }).join('');
  } catch (err) { /* advisory table — never block the Trace view */ }
}
refreshTraceMatrix();

// ---- test plans: review -> edit -> approve -> link -> generate
const PLAN_CHIP = { draft: ['draft', 'muted'], in_review: ['✎ in review', 'warning'],
  approved: ['✓ approved', 'success'], changes_requested: ['✗ changes requested', 'danger'] };
let planKey = null;
function planChip(s) {
  const [lb, cls] = PLAN_CHIP[s] || [s || '—', 'muted'];
  return '<span class="chip chip-' + cls + '">' + escHtml(lb) + '</span>';
}
async function refreshPlans() {
  if (!served || !$('#plans-table')) return;
  try {
    const plans = await api('/api/plans');
    const body = $('#plans-table tbody');
    body.innerHTML = plans.length ? plans.map(p =>
      '<tr><td class="strong">' + escHtml(p.key) + '</td>' +
      '<td>' + planChip(p.status) + '</td>' +
      '<td>' + (p.linked ? '<span class="chip chip-success">✓ linked</span>' : '<span class="muted">—</span>') + '</td>' +
      '<td class="mono sm muted">' + escHtml(p.generated_run || '—') + '</td>' +
      '<td class="sm muted">' + escHtml(p.note || '') + '</td>' +
      '<td class="right"><button class="btn btn-sm plan-open" data-key="' + escHtml(p.key) + '">Review</button></td></tr>'
    ).join('') : '<tr><td colspan="6"><div class="empty">No test plans yet — author one with <code>make plan KEY=PROJ-123</code>.</div></td></tr>';
    $('#plans-count').textContent = plans.length + ' plan(s) · ' +
      plans.filter(p => p.status === 'approved').length + ' approved';
  } catch (err) { toast(err.message); }
}
async function openPlan(key) {
  const p = await api('/api/plans/one?key=' + encodeURIComponent(key));
  planKey = key;
  $('#plan-editor').classList.remove('hidden');
  $('#plan-key').textContent = key;
  $('#plan-status').innerHTML = planChip(p.status);
  // The reviewer is approving a plan that was already challenged — say so, or the
  // adversarial pass is invisible to the only person it was run for.
  const adv = $('#plan-adversary');
  adv.textContent = p.adversary || '';
  adv.classList.toggle('hidden', !p.adversary);
  // Per-gap verdicts (roadmap 4.5): the reviewer approves a plan that was already
  // challenged — show WHAT was challenged, not just that a challenge happened.
  const det = $('#plan-adversary-detail');
  if (det) {
    const gaps = (p.adversary_detail && p.adversary_detail.gaps) || [];
    if (gaps.length) {
      det.innerHTML = gaps.map(g =>
        '<li><span class="chip chip-' +
        (g.severity === 'high' ? 'danger' : g.severity === 'med' ? 'warning' : 'muted') +
        '">' + escHtml(g.severity || '?') + '</span> <b>' + escHtml(g.title || '') +
        '</b> <span class="muted sm">[' + escHtml(g.category || '') + ']</span>' +
        (g.rationale ? '<div class="sm muted">' + escHtml(g.rationale) + '</div>' : '') +
        '</li>').join('');
      det.classList.remove('hidden');
    } else {
      det.innerHTML = '';
      det.classList.add('hidden');
    }
  }
  $('#plan-text').value = p.text;
  // Similar prior plans (roadmap 6.1) — a SUGGESTION strip, never auto-applied.
  // Loaded after the editor so a slow lookup can't delay opening the plan.
  const sim = $('#plan-similar');
  if (sim) {
    sim.classList.add('hidden');
    api('/api/plans/similar?key=' + encodeURIComponent(key)).then(d => {
      if (!d.similar || !d.similar.length) return;
      sim.innerHTML = d.similar.map((m, i) =>
        '<div><b>Similar prior plan:</b> ' + escHtml(m.key) +
        ' (' + Math.round(m.score * 100) + '%' +
        (m.status ? ' · ' + escHtml(m.status) : '') + ')' +
        ' <span class="muted">shared: ' + escHtml((m.shared_terms || []).join(', ')) +
        '</span> <a href="#" data-simidx="' + i + '" class="sim-view">view</a>' +
        '<pre class="hidden" data-simpre="' + i + '" style="white-space:pre-wrap; ' +
        'max-height:260px; overflow:auto; margin:6px 0 0">' + escHtml(m.text || '') +
        '</pre></div>').join('');
      sim.classList.remove('hidden');
      sim.querySelectorAll('.sim-view').forEach(a => a.addEventListener('click', ev => {
        ev.preventDefault();
        const pre = sim.querySelector('[data-simpre="' + a.dataset.simidx + '"]');
        pre.classList.toggle('hidden');
      }));
    }).catch(() => { /* suggestions are optional */ });
  }
  $('#plan-editor').scrollIntoView({ behavior: 'smooth', block: 'start' });
}
async function planPost(path, payload, okMsg) {
  try {
    const r = await api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: planKey, ...payload }) });
    toast(typeof okMsg === 'function' ? okMsg(r) : okMsg);
    await refreshPlans();
    if (planKey) await openPlan(planKey);
  } catch (err) { toast(err.message); }
}
document.addEventListener('click', async e => {
  const open = e.target.closest('button.plan-open');
  if (open) { if (needsServer()) return; openPlan(open.dataset.key).catch(x => toast(x.message)); return; }
  const id = e.target.id;
  if (!planKey || !['plan-save','plan-review','plan-changes','plan-approve','plan-link','plan-comment','plan-generate'].includes(id)) return;
  if (needsServer()) return;
  if (id === 'plan-save')
    return planPost('/api/plans/save', { text: $('#plan-text').value },
      r => 'Saved — status is now ' + r.status + (r.status === 'draft' ? ' (edits revoke approval)' : ''));
  if (id === 'plan-review')   return planPost('/api/plans/status', { status: 'in_review' }, 'Marked in review');
  if (id === 'plan-approve')  return planPost('/api/plans/status', { status: 'approved' }, 'Plan approved — you can now link it and generate tests');
  if (id === 'plan-changes') {
    const note = prompt('What needs changing?', '');
    if (note === null) return;
    return planPost('/api/plans/status', { status: 'changes_requested', note }, 'Changes requested');
  }
  if (id === 'plan-link')     return planPost('/api/plans/link', {}, r => 'Linked to JIRA: ' + r.ref);
  if (id === 'plan-comment')  return planPost('/api/plans/comment', {}, r => 'Commented on the ticket: plan + tests linked');
  if (id === 'plan-generate') return planPost('/api/plans/generate', {},
    'Queued test generation from the approved plan — press Run queue');
});
refreshPlans();

// ---- repositories & mapping
async function repoPost(path, payload, okMsg) {
  try {
    await api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload) });
    toast(okMsg + ' — goldens re-run, AGENTS.md regenerated. Reloading…');
    setTimeout(() => location.reload(), 1200);
  } catch (err) { toast(err.message); }
}
const APP_FIELDS = { name: 'app-name', kind: 'app-kind', scm: 'app-scm', url: 'app-url',
  stash_project: 'app-stashproj',
  domains: 'app-domains', testable_paths: 'app-paths', contract: 'app-contract',
  route_table: 'app-routes', consumes_services: 'app-consumes' };
const TEST_FIELDS = { name: 'test-name', layer: 'test-layer', framework: 'test-framework',
  scm: 'test-scm', url: 'test-url', stash_project: 'test-stashproj',
  specs: 'test-specs', fixtures: 'test-fixtures',
  scope: 'test-scope' };
document.addEventListener('click', async e => {
  const edit = e.target.closest('button.repo-edit');
  if (edit) {
    const entry = JSON.parse(edit.dataset.entry);
    const map = edit.dataset.form === 'app' ? APP_FIELDS : TEST_FIELDS;
    Object.entries(map).forEach(([k, id]) => { $('#' + id).value = entry[k] || ''; });
    toast('Editing ' + entry.name + ' — change fields below and Save');
    return;
  }
  const del = e.target.closest('button.repo-del');
  if (del) {
    if (needsServer()) return;
    if (!confirm('Remove ' + del.dataset.name + ' from the registry?')) return;
    repoPost('/api/repos/remove', { name: del.dataset.name, section: del.dataset.section },
      'Removed ' + del.dataset.name);
    return;
  }
  const sc = e.target.closest('button.scope-save');
  if (sc) {
    if (needsServer()) return;
    const val = document.querySelector('input.scope-in[data-repo="' + sc.dataset.repo + '"]').value;
    repoPost('/api/repos/scope', { test_repo: sc.dataset.repo, apps: val },
      'Mapped ' + sc.dataset.repo + ' scope');
    return;
  }
  if (e.target.id === 'app-save' || e.target.id === 'test-save') {
    if (needsServer()) return;
    const isApp = e.target.id === 'app-save';
    const map = isApp ? APP_FIELDS : TEST_FIELDS;
    const payload = {};
    Object.entries(map).forEach(([k, id]) => {
      const v = $('#' + id).value.trim();
      if (v) payload[k] = v;
    });
    if (!payload.name) { toast('Repo name is required'); return; }
    repoPost(isApp ? '/api/repos/app' : '/api/repos/test', payload,
      'Saved ' + payload.name);
  }
});
async function loadNotes() {
  if (!served || !$('#notes-repo')) return;
  try {
    const repo = $('#notes-repo').value;
    const n = await api('/api/repos/notes?repo=' + encodeURIComponent(repo));
    $('#notes-text').value = n.team;
    let msg = n.local_files.length
      ? 'Repo-local guidance merged: ' + n.local_files.map(f => f.path).join(', ')
      : 'No repo-local AGENTS.md/CLAUDE.md found — team notes below are the only guidance.';
    try {                                    // append last-sync info for this repo
      const st = (await api('/api/repos/sync')).find(s => s.name === repo);
      if (st) msg += st.synced_at
        ? '  ·  last SCM sync: ' + new Date(st.synced_at * 1000).toLocaleString() +
          ' (' + (st.files.join(', ') || 'no guidance in repo') + ')'
        : '  ·  never synced from SCM';
    } catch (e) { /* status is advisory */ }
    $('#notes-local').textContent = msg;
  } catch (err) { $('#notes-local').textContent = err.message; }
}
document.addEventListener('click', async e => {
  const genAll = e.target.id === 'gen-guidance-all';
  const genOne = e.target.id === 'gen-guidance-one';
  if (!genAll && !genOne) return;
  if (needsServer()) return;
  const btn = e.target, idle = btn.textContent;
  btn.disabled = true; btn.textContent = 'Generating…';
  try {
    const r = await api('/api/repos/guidance', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(genAll ? {} : { repo: $('#notes-repo').value }) });
    const rows = r.generated || [];
    const wrote = rows.filter(x => x.status === 'written');
    const own = rows.filter(x => x.status === 'skipped_has_own');
    // Report the actual mix: "already ship their own" is only true for some of them,
    // and claiming it for repos that merely have a generated file is misleading.
    const kept = rows.filter(x => x.status === 'skipped_exists');
    const parts = [];
    if (wrote.length) parts.push('generated ' + wrote.length);
    if (own.length) parts.push(own.length + ' ship their own');
    if (kept.length) parts.push(kept.length + ' already generated');
    toast(wrote.length
      ? 'AGENTS.md: ' + parts.join(' · ') + ' — estate knowledge regenerated'
      : 'Nothing to generate — ' + (parts.join(' · ') || 'no repos registered'));
    await loadNotes();
  } catch (err) { toast(err.message); }
  btn.disabled = false; btn.textContent = idle;
});

document.addEventListener('click', async e => {
  const all = e.target.id === 'sync-all', one = e.target.id === 'sync-one';
  if (!all && !one) return;
  if (needsServer()) return;
  const btn = e.target, idle = btn.textContent;
  btn.disabled = true; btn.textContent = 'Syncing…';
  try {
    const r = await api('/api/repos/sync', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(all ? {} : { repo: $('#notes-repo').value }) });
    toast(all
      ? 'Synced ' + r.repos + ' repo(s) from SCM — ' + r.with_guidance +
        ' carry guidance; AGENTS.md regenerated'
      : r.repo + ': ' + (r.files.join(', ') || 'no AGENTS.md/CLAUDE.md in the repo') +
        ' — AGENTS.md regenerated');
    await loadNotes();
  } catch (err) { toast(err.message); }
  btn.disabled = false; btn.textContent = idle;
});
// ---- curated guidance (durable per-repo AGENTS.md / CLAUDE.md)
let curatedCache = { files: {}, generated: '' };
async function loadCurated() {
  if (!served || !$('#cur-text')) return;
  try {
    const repo = $('#notes-repo').value;
    curatedCache = await api('/api/repos/curated?repo=' + encodeURIComponent(repo));
    const fn = $('#cur-file').value;
    $('#cur-text').value = curatedCache.files[fn] || '';
    const have = Object.keys(curatedCache.files);
    $('#cur-status').textContent =
      (have.length ? 'Curated on disk: ' + have.join(', ') : 'No curated file yet') +
      '  ·  effective sources for the phases: ' +
      (curatedCache.effective.join(', ') || 'none');
  } catch (err) {
    // Reset — keeping the PREVIOUS repo's content in the editor after a failed
    // load would let a Save write repo A's guidance under repo B.
    curatedCache = { files: {}, generated: '', effective: [] };
    $('#cur-text').value = '';
    $('#cur-status').textContent = err.message;
  }
}
if ($('#cur-text')) {
  $('#cur-file').addEventListener('change', () => {
    $('#cur-text').value = curatedCache.files[$('#cur-file').value] || '';
  });
  $('#cur-load-gen').addEventListener('click', async () => {
    if (needsServer()) return;
    if (!curatedCache.generated) {
      // Produce a draft on demand for repos that never had one generated.
      try {
        await api('/api/repos/guidance', { method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ repo: $('#notes-repo').value, force: true }) });
        curatedCache = await api('/api/repos/curated?repo=' +
          encodeURIComponent($('#notes-repo').value));
      } catch (err) { toast(err.message); return; }
    }
    if (!curatedCache.generated) { toast('No generated draft available'); return; }
    $('#cur-text').value = curatedCache.generated;
    toast('Generated draft loaded — edit and Save to make it durable');
  });
  $('#cur-save').addEventListener('click', async () => {
    if (needsServer()) return;
    try {
      const r = await api('/api/repos/curated', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo: $('#notes-repo').value,
          file: $('#cur-file').value, content: $('#cur-text').value }) });
      toast(r.deleted ? 'Curated ' + r.file + ' deleted for ' + r.repo
                      : 'Saved ' + r.path + ' (durable) — AGENTS.md regenerated');
      await loadCurated();
    } catch (err) { toast(err.message); }
  });
  $('#cur-export').addEventListener('click', () => {
    if (needsServer()) return;
    location.href = '/api/repos/curated/export?repo=' +
      encodeURIComponent($('#notes-repo').value) +
      '&file=' + encodeURIComponent($('#cur-file').value);
  });
}
if ($('#notes-repo')) {
  $('#notes-repo').addEventListener('change', () => { loadNotes(); loadCurated(); });
  $('#notes-save').addEventListener('click', async () => {
    if (needsServer()) return;
    try {
      const r = await api('/api/repos/notes', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo: $('#notes-repo').value, text: $('#notes-text').value }) });
      toast(r.saved ? 'Guidance saved to ' + r.path + ' and merged into AGENTS.md'
                    : 'Guidance cleared for ' + r.repo);
    } catch (err) { toast(err.message); }
  });
  loadNotes();
  loadCurated();
}

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
document.addEventListener('click', async e => {
  const b = e.target.closest('button.report-email');
  if (!b) return;
  if (needsServer()) return;
  const to = prompt('Email the team report to (comma-separated; blank = SMTP_TO default):', '');
  if (to === null) return;
  b.disabled = true; const idle = b.textContent; b.textContent = 'Sending…';
  const days = $('#rep-days').value, rel = $('#rep-rel').value;
  try {
    const r = await api('/api/email/report', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ days: days ? +days : null, release: rel || null, to: to || null }) });
    toast(r.result);
  } catch (err) { toast(err.message); }
  b.disabled = false; b.textContent = idle;
});

// ---- stale-server guard: this page is rendered fresh per request, but the server
// PROCESS keeps whatever code it started with. If its /api/version disagrees (or is
// absent), actions like "Clear demo data" run OLD logic while the page promises the
// new — tell the operator to restart instead of letting the mismatch look like a bug.
const UI_SCHEMA = 2;
if (served) {
  fetch('/api/version').then(r => r.ok ? r.json() : {ui_schema: 0})
    .catch(() => ({ui_schema: 0}))
    .then(v => {
      if (v.user && v.user !== 'token-client') {
        const foot = document.querySelector('.side-foot');
        if (foot) {
          const el = document.createElement('div');
          el.className = 'sm muted';
          el.textContent = '👤 ' + v.user + (v.sso ? ' (SSO)' : '');
          foot.prepend(el);
        }
      }
      if (v.ui_schema !== UI_SCHEMA) {
        const b = document.createElement('div');
        b.className = 'stale-banner';
        b.textContent = 'The dashboard server is running older code than this page — '
          + 'buttons may behave stale. Restart it: stop and re-run make serve.';
        document.querySelector('main').prepend(b);
      }
    });
}

// ---- theme toggle: explicit choice beats the OS preference, both directions
(function () {
  const KEY = 'aiqe-theme';                    // 'dark' | 'light' | absent = system
  const root = document.documentElement;
  const btn = document.getElementById('theme-toggle');
  function apply() {
    const t = localStorage.getItem(KEY);
    if (t === 'dark' || t === 'light') root.dataset.theme = t;
    else delete root.dataset.theme;            // follow the OS again
    if (btn) btn.textContent = t === 'dark' ? '◐ Dark' : t === 'light' ? '◐ Light' : '◐ Auto';
  }
  if (btn) btn.addEventListener('click', () => {
    const cur = localStorage.getItem(KEY);
    const next = cur === 'dark' ? 'light' : cur === 'light' ? null : 'dark';
    if (next) localStorage.setItem(KEY, next); else localStorage.removeItem(KEY);
    apply();
  });
  apply();
})();

// ---- settings
const escAttr = escHtml;
async function loadSettings() {
  if (!served) return;
  try {
    const secs = await api('/api/settings');
    $('#settings-body').innerHTML = secs.map(s =>
      '<div class="set-sec"><h3>' + escAttr(s.section) + '</h3>' +
      '<div class="hint">' + escAttr(s.hint) + '</div><div class="form-grid">' +
      s.fields.map(f => {
        // Provenance: a value coming from the properties baseline is NOT in .env, so
        // without this an operator cannot tell where it came from — or why editing
        // .env by hand appeared to change nothing.
        const prov = f.source === 'properties'
          ? ' <span class="chip chip-info sm" title="from the properties file; saving here writes .env, which overrides it">properties</span>'
          : '';
        if (f.options) {
          return '<label class="stack"><span class="lbl">' + escAttr(f.label) + prov +
            '</span><select data-env="' + f.env + '">' + f.options.map(o =>
              '<option value="' + o[0] + '"' + (f.value === o[0] ? ' selected' : '') +
              '>' + escAttr(o[1]) + '</option>').join('') + '</select></label>';
        }
        const ph = f.secret ? (f.set ? '•••••• set — type to replace'
                                     : 'not set') : (f.help || '');
        return '<label class="stack"><span class="lbl">' + escAttr(f.label) +
          (f.secret ? ' 🔒' : '') + prov + '</span>' +
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
// ---- validate integrations (read-only connectivity check)
const CHECK_CHIP = { ok: ['✓ connected', 'success'], fail: ['✗ failed', 'danger'],
                     degraded: ['unreachable (optional)', 'warning'],
                     skipped: ['not configured', 'muted'] };
$('#check-integrations').addEventListener('click', async () => {
  if (needsServer()) return;
  const b = $('#check-integrations'), idle = b.textContent;
  b.disabled = true; b.textContent = 'Checking…';
  try {
    const r = await api('/api/integrations/check', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const rows = r.results.map(x => {
      const [lb, cls] = CHECK_CHIP[x.status] || [x.status, 'muted'];
      return '<tr><td class="strong">' + escHtml(x.name) + '</td>' +
        '<td><span class="chip chip-' + cls + '">' + escHtml(lb) + '</span></td>' +
        '<td class="sm muted">' + escHtml(x.detail) +
        (x.hint && x.status !== 'ok'
          ? '<div class="sm" style="color:var(--sr-warning-fg)">→ ' + escHtml(x.hint) + '</div>'
          : '') + '</td></tr>';
    }).join('');
    $('#check-table tbody').innerHTML = rows;
    $('#check-summary').textContent =
      r.summary.ok + ' connected · ' + r.summary.fail + ' failed · ' +
      (r.summary.degraded ? r.summary.degraded + ' degraded (optional) · ' : '') +
      r.summary.skipped + ' not configured' +
      (r.mock_mode ? '  (runs still use mock adapters — AIQE_MOCK=1)' : '');
    $('#check-card').classList.remove('hidden');
    $('#check-card').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    toast(r.summary.fail ? r.summary.fail + ' integration(s) failed — see details'
          : r.summary.degraded ? r.summary.degraded + ' optional integration(s) unreachable — runs are unaffected'
          : 'All configured integrations reachable');
  } catch (err) { toast(err.message); }
  b.disabled = false; b.textContent = idle;
});

$('#factory-reset').addEventListener('click', async () => {
  if (needsServer()) return;
  if (!confirm('FACTORY RESET?\\n\\nDeletes everything Clear demo data deletes PLUS all '
    + 'registered repositories and per-repo team notes. The Repositories view will be '
    + 'empty afterwards.\\n\\nThis cannot be undone.')) return;
  if (!confirm('Really delete ALL repositories from the registry?')) return;
  const b = $('#factory-reset');
  b.disabled = true;
  try {
    const r = await api('/api/demo/clear', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ factory: true }) });
    toast('Factory reset: removed ' + r.removed + ' item(s) — reloading…');
    setTimeout(() => location.reload(), 900);
    return;
  } catch (err) {
    if (/pipeline run looks active/i.test(err.message) &&
        confirm(err.message + '\\n\\nReset anyway?')) {
      try {
        const r = await api('/api/demo/clear', { method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ factory: true, force: true }) });
        toast('Factory reset (forced): ' + r.removed + ' item(s) — reloading…');
        setTimeout(() => location.reload(), 900);
        return;
      } catch (e2) { toast(e2.message); }
    } else { toast(err.message); }
  }
  b.disabled = false;
});
$('#clear-demo').addEventListener('click', async () => {
  if (needsServer()) return;
  if (!confirm('Delete ALL generated demo data?\\n\\nRemoves run history, archived ' +
    'diffs, review/queue/webhook state, test plans, test data, the bootstrapped test ' +
    'catalog + coverage evidence, generated guidance, exports, logs and scratch dirs.' +
    '\\n\\nKept: your repository configuration (remove repos in the Repositories view), ' +
    'the demo repos, and AGENTS.md.\\n\\nThe page reloads when done. This cannot be undone.')) return;
  const b = $('#clear-demo');
  b.disabled = true;
  const post = force => api('/api/demo/clear', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ force }) });
  try {
    const r = await post(false);
    toast('Cleared ' + r.removed + ' generated file(s) — reloading…');
    setTimeout(() => location.reload(), 900);
    return;
  } catch (err) {
    // A pipeline lock left behind by a killed run used to make this a dead end.
    // Offer the override here rather than sending the user to the shell.
    if (/pipeline run looks active/i.test(err.message) &&
        confirm(err.message + '\\n\\nClear anyway?')) {
      try {
        const r = await post(true);
        toast('Cleared ' + r.removed + ' generated file(s) (forced past the lock) — reloading…');
        setTimeout(() => location.reload(), 900);
        return;
      } catch (e2) { toast(e2.message); }
    } else { toast(err.message); }
  }
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
    <button class="btn btn-sm" id="theme-toggle"
      title="Switch theme (follows your OS until you choose)">◐ Theme</button>
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
            for f in ("md", "html", "docx", "pdf"))}
          <button class="btn btn-sm info report-email">Email</button></span>
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

  <div data-view="wizard">
    <section class="card">
      <div class="card-h"><div><h2>Guided run</h2>
        <div class="sub">Two long journeys, step by step. Everything here drives the
        SAME engine as the other views — the wizard only sequences it and shows
        live progress. Generation is <b>asynchronous</b> (a run takes minutes; an
        OpenHands conversation longer), so start it, leave, and come back: the
        steps below reflect the current state whenever you return.</div></div>
        <span class="grow"></span>
        <label class="f">Flow <select id="wz-mode" class="h32">
          <option value="pr">Pull request → E2E tests</option>
          <option value="jira">JIRA ticket → plan → E2E tests</option>
        </select></label>
      </div>
      <div class="card-b" style="display:flex; flex-direction:column; gap:12px">
        <div class="wz-row" id="wz-pr-inputs">
          <label class="f">App repo or PR URL <input id="wz-repo" class="h32"
            placeholder="orders-api  —  or paste the full pull-request URL"
            title="Paste a Stash, Bitbucket or GitHub pull-request URL and the repo, PR number and project are read from it"
            style="width:330px"></label>
          <label class="f">PR # <input id="wz-pr" class="h32"
            placeholder="201" style="width:80px"></label>
          <button class="btn btn-primary" id="wz-start-pr">Analyze PR &amp; generate tests</button>
        </div>
        <div class="wz-row hidden" id="wz-jira-inputs">
          <label class="f">Ticket <input id="wz-key" class="h32"
            placeholder="PROJ-301" style="width:130px"></label>
          <button class="btn btn-primary" id="wz-start-plan">Author test plan</button>
          <button class="btn" id="wz-approve">Approve plan</button>
          <button class="btn" id="wz-generate">Generate tests</button>
          <button class="btn info" id="wz-link">Comment plan + tests on the ticket</button>
        </div>
        <div class="sm muted" id="wz-hint">Pick a flow, fill in the target, and start.
          Progress refreshes automatically while work is running.</div>
        <ol class="wz-steps" id="wz-steps"></ol>
        <div id="wz-result"></div>
      </div>
    </section>
  </div>

  <div data-view="queue">
    <section class="card">
      <div class="card-h"><div><h2>Fetch work from JIRA &amp; SCM</h2>
        <div class="sub">Pull tickets and PRs for a release, queue them, then run the
        queue — items are processed in order.</div></div></div>
      <div class="card-b filters">
        <label class="f">Release / fixVersion <input id="fetch-rel" class="h32"
          list="fetch-rel-known" placeholder="any fixVersion — empty = all"
          style="width:190px"></label>
        <datalist id="fetch-rel-known">{release_opts}</datalist>
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
        <div style="display:flex; gap:8px">
          <button class="btn btn-primary" id="inl-queue" style="height:36px">
          Queue inline ticket</button>
          <button class="btn info" id="inl-plan-oh" style="height:36px"
            title="Hand the pasted description to an OpenHands conversation (LLM) that authors the test plan and stops for approval">
          Plan via OpenHands</button></div>
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
    <section class="card hidden oh-card">
      <div class="card-h"><h2 class="grow">OpenHands agent runs</h2>
        <span class="sub oh-count"></span></div>
      <div class="scroll"><table class="oh-table">
        <thead><tr><th>conversation</th><th>status</th><th>repo / ticket</th>
          <th class="num">events</th><th>last event</th></tr></thead>
        <tbody></tbody></table></div>
    </section>
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
          <th title="Advisory test-quality score - never gates a commit">critic</th>
          <th>release</th><th style="min-width:280px">gate results per test repo</th>
          <th>team review</th></tr></thead>
        <tbody>{runs_rows}</tbody></table></div>
    </section>
  </div>

  <div data-view="trace">
    <section class="card" id="tmx-card">
      <div class="card-h"><div><h2>Traceability matrix</h2>
        <div class="sub">One row per plan scenario: ticket → scenario → generated
        spec → gate commit → CI health. A scenario with no spec is a requirement
        someone approved that nothing exercises yet.</div></div>
        <span class="grow"></span>
        <a class="btn btn-sm" href="/api/trace-matrix?format=csv" download>Download CSV</a>
      </div>
      <div class="scroll"><table id="tmx-table">
        <thead><tr><th>key</th><th>scenario</th><th>spec</th><th>repo</th>
          <th>gate</th><th>commit</th><th>CI</th></tr></thead>
        <tbody></tbody></table></div>
    </section>
    <div class="art-layout">
      <nav class="card art-list">
        <div class="art-list-h">Keys with a trace</div>
        {trace_keys_html or '<div class="empty">No traced keys yet.</div>'}
      </nav>
      <div>{trace_panels_html or '<div class="card"><div class="empty">No traces yet — run make demo-pr / demo-jira.</div></div>'}</div>
    </div>
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

  <div data-view="plans">
    <section class="card hidden oh-card">
      <div class="card-h"><h2 class="grow">OpenHands agent runs</h2>
        <span class="sub oh-count"></span></div>
      <div class="scroll"><table class="oh-table">
        <thead><tr><th>conversation</th><th>status</th><th>repo / ticket</th>
          <th class="num">events</th><th>last event</th></tr></thead>
        <tbody></tbody></table></div>
    </section>
    <section class="card">
      <div class="card-h"><div><h2>Test plans from JIRA</h2>
        <div class="sub">Author a plan from a ticket (<code>make plan KEY=…</code>), then
        review, edit and approve it here. Test generation is blocked until the plan is
        approved; editing an approved plan revokes the approval.</div></div>
        <span class="grow"></span>
        <label class="f">Ticket <input id="plan-new-key" class="h32"
          placeholder="PROJ-123" style="width:110px"></label>
        <button class="btn btn-sm" id="plan-author">Author plan (queue)</button>
        <button class="btn btn-sm info" id="plan-author-oh">Author via OpenHands</button>
        <span class="sub" id="plans-count">{n_plans} plan(s) · {n_appr} approved</span>
      </div>
      <div class="scroll"><table id="plans-table">
        <thead><tr><th>ticket</th><th>status</th><th>linked to JIRA</th>
          <th>tests generated</th><th>note</th><th class="right">actions</th></tr></thead>
        <tbody>{plan_rows}</tbody></table></div>
    </section>
    <section class="card hidden" id="plan-editor">
      <div class="card-h"><h2 class="grow">Reviewing <span id="plan-key"></span></h2>
        <span id="plan-status"></span>
        <span id="plan-adversary" class="hidden sm muted"
          title="A read-only adversary phase challenged this plan for missing negative, boundary, authz, state and cross-repo cases; an arbiter folded the accepted gaps in before you were asked to approve it."></span>
        <button class="btn btn-sm" id="plan-save">Save edits</button>
        <button class="btn btn-sm info" id="plan-review">Mark in review</button>
        <button class="btn btn-sm danger" id="plan-changes">Request changes</button>
        <button class="btn btn-sm approve" id="plan-approve">Approve</button>
        <button class="btn btn-sm info" id="plan-link">Link to JIRA</button>
        <button class="btn btn-sm info" id="plan-comment"
          title="Post one ticket comment linking the plan AND the generated E2E tests (files, gate commits, branch)">
          Comment plan + tests</button>
        <button class="btn btn-primary" id="plan-generate">Generate tests</button>
      </div>
      <div class="card-b" style="display:flex; flex-direction:column; gap:10px">
        <ul id="plan-adversary-detail" class="hidden"
          style="list-style:none; margin:0; padding:8px 12px; display:flex;
                 flex-direction:column; gap:6px; border:1px solid var(--sr-border);
                 border-radius:8px"></ul>
        <div id="plan-similar" class="hidden sm"
          style="border:1px solid var(--sr-border); border-radius:8px;
                 padding:8px 12px"></div>
        <textarea id="plan-text" rows="22" spellcheck="false"
          style="font-family:var(--sr-font-mono); font-size:12px"></textarea>
      </div>
    </section>
  </div>

  <div data-view="repos">
    <section class="card">
      <div class="card-h"><div><h2>Application repositories</h2>
        <div class="sub">UI and service repos under test. Coverage gaps are flagged;
        Edit fills the form below.</div></div></div>
      <div class="scroll"><table>
        <thead><tr><th>repo</th><th>kind</th><th>scm</th><th>domains</th>
          <th>contract / routes</th><th>covered by</th><th>guidance</th>
          <th class="right">actions</th></tr></thead>
        <tbody>{app_rows}</tbody></table></div>
      <div class="card-b" style="border-top:1px solid var(--sr-border)">
        <div class="strong sm" style="margin-bottom:8px">Add / edit application repo</div>
        <div class="form-grid">
          <label class="stack">Name<input id="app-name" placeholder="payments-ui"></label>
          <label class="stack">Kind<select id="app-kind">
            <option value="ui">ui</option><option value="service">service</option></select></label>
          <label class="stack">SCM<select id="app-scm"><option>bitbucket</option>
            <option>github</option><option>stash</option></select></label>
          <label class="stack">URL / slug<input id="app-url" placeholder="workspace/payments-ui"></label>
          <label class="stack">Stash project<input id="app-stashproj"
            placeholder="ENG (Stash only — overrides the URL's project segment)"></label>
          <label class="stack">Domains (csv)<input id="app-domains" placeholder="payments"></label>
          <label class="stack">Testable paths (csv)<input id="app-paths" placeholder="src/**"></label>
          <label class="stack">Contract (service)<input id="app-contract" placeholder="openapi/x.yaml"></label>
          <label class="stack">Route table (ui)<input id="app-routes" placeholder="src/routes.tsx"></label>
          <label class="stack">Consumes services (csv)<input id="app-consumes" placeholder="orders-api"></label>
        </div>
        <div style="margin-top:12px"><button class="btn btn-primary" id="app-save"
          style="height:36px">Save app repo</button></div>
      </div>
    </section>
    <section class="card">
      <div class="card-h"><div><h2>E2E test repositories &amp; mapping</h2>
        <div class="sub">One test repo covers many app repos. <b>Scope</b> is the
        declared responsibility you manage here; <b>covers</b> (evidence ∪ scope) is
        regenerated — routing uses it immediately.</div></div></div>
      <div class="scroll"><table>
        <thead><tr><th>repo</th><th>layer</th><th>framework</th>
          <th>covers (generated)</th><th>scope — mapped app repos</th>
          <th class="right">actions</th></tr></thead>
        <tbody>{test_rows}</tbody></table></div>
      <div class="card-b" style="border-top:1px solid var(--sr-border)">
        <div class="strong sm" style="margin-bottom:8px">Add / edit E2E test repo</div>
        <div class="form-grid">
          <label class="stack">Name<input id="test-name" placeholder="e2e-payments-tests"></label>
          <label class="stack">Layer<select id="test-layer">
            <option>api</option><option>ui</option></select></label>
          <label class="stack">Framework<input id="test-framework" placeholder="playwright"></label>
          <label class="stack">SCM<select id="test-scm"><option>bitbucket</option>
            <option>github</option><option>stash</option></select></label>
          <label class="stack">URL / slug<input id="test-url" placeholder="workspace/e2e-payments"></label>
          <label class="stack">Stash project<input id="test-stashproj"
            placeholder="QA (Stash only — overrides the URL's project segment)"></label>
          <label class="stack">Specs dir<input id="test-specs" placeholder="tests/"></label>
          <label class="stack">Fixtures dir<input id="test-fixtures" placeholder="fixtures/"></label>
          <label class="stack">Scope (app repos csv)<input id="test-scope" placeholder="payments-api, payments-ui"></label>
        </div>
        <div style="margin-top:12px"><button class="btn btn-primary" id="test-save"
          style="height:36px">Save test repo</button></div>
      </div>
    </section>
    <section class="card">
      <div class="card-h"><div><h2>Repository guidance (AGENTS.md / CLAUDE.md)</h2>
        <div class="sub">Per-repo conventions merged into the estate <code>AGENTS.md</code>
        and injected into every generation phase (PR triage/generation and JIRA
        story/bug plans + tests). <b>Sync from SCM</b> pulls each repo's own
        <code>AGENTS.md</code>/<code>CLAUDE.md</code> straight from Bitbucket/GitHub/Stash
        — app repos (ui + service) and E2E test repos alike — then regenerates
        <code>AGENTS.md</code>. Most repos ship neither: <b>Generate missing</b> writes a
        starter <code>AGENTS.md</code> from the registry, the harvested API/route surface
        and catalog evidence, so a new repo still teaches the agent something. A
        repo-owned file always wins over a generated one.</div></div>
        <span class="grow"></span>
        <button class="btn btn-sm info" id="sync-all">Sync all from SCM</button>
        <button class="btn btn-sm" id="gen-guidance-all">Generate missing</button>
        <label class="f">Repo <select id="notes-repo" class="h32">{all_repo_opts}</select></label>
        <button class="btn btn-sm" id="sync-one">Sync this repo</button>
        <button class="btn btn-sm" id="gen-guidance-one">Generate for this repo</button>
      </div>
      <div class="card-b" style="display:flex; flex-direction:column; gap:10px">
        <div class="sm muted" id="notes-local"></div>
        <textarea id="notes-text" rows="7"
          placeholder="Conventions, selectors, auth flows, data setup for this repo…"></textarea>
        <div><button class="btn btn-primary" id="notes-save" style="height:36px">
          Save guidance</button></div>
      </div>
    </section>

    <section class="card">
      <div class="card-h"><div><h2>Curated guidance file (durable)</h2>
        <div class="sub">A full <code>AGENTS.md</code> or <code>CLAUDE.md</code> the
        platform keeps for this repo — <b>committed with the control repo</b>, so it
        survives redeployments, and editable/exportable here. <b>Load generated
        draft</b> starts from the platform-generated file (registry + harvested
        surface + catalog evidence); edit, then <b>Save</b>. Ranking: a file the
        repo itself ships always wins, then this curated copy, then generated
        scratch — so curating never overrides a team's own committed guidance.
        Kept by "Clear demo data"; removed only by factory reset.</div></div>
        <span class="grow"></span>
        <label class="f">File <select id="cur-file" class="h32">
          <option>AGENTS.md</option><option>CLAUDE.md</option></select></label>
        <button class="btn btn-sm" id="cur-load-gen">Load generated draft</button>
        <button class="btn btn-sm info" id="cur-export">Export</button>
      </div>
      <div class="card-b" style="display:flex; flex-direction:column; gap:10px">
        <div class="sm muted" id="cur-status"></div>
        <textarea id="cur-text" rows="12" class="mono"
          placeholder="No curated file yet — Load generated draft, edit, then Save. Saving empty content deletes the curated file."></textarea>
        <div><button class="btn btn-primary" id="cur-save" style="height:36px">
          Save curated file</button></div>
      </div>
    </section>
  </div>

  <div data-view="settings">
    <section class="card">
      <div class="card-h"><div><h2>Integrations</h2>
        <div class="sub">Stored in the gitignored <code>.env</code> — the same file
        the adapters read. Secrets are write-only: a set secret shows as
        <code>••••••</code>; type a new value to replace it, leave blank to keep it.
        Adapter-mode and SCM changes take effect on the next run;
        restart <code>make serve</code> to switch the server's fetch source.</div></div>
        <span class="grow"></span>
        <button class="btn btn-sm info" id="check-integrations">Validate connections</button>
      </div>
      <div class="card-b" id="settings-body">
        <div class="empty">Start the server (<code>make serve</code>) to view and
        edit integration settings.</div>
      </div>
    </section>
    <section class="card hidden" id="check-card">
      <div class="card-h"><h2 class="grow">Connection check</h2>
        <span class="sub" id="check-summary"></span></div>
      <div class="scroll"><table id="check-table">
        <thead><tr><th>system</th><th>result</th><th>detail</th></tr></thead>
        <tbody></tbody></table></div>
      <div class="card-b sub">Read-only: nothing is posted, pushed or sent. A Slack
        webhook can only be fully verified by posting, so it is checked for shape and
        reachability only. For the deeper OpenHands test (which can start a real,
        billable conversation) run <code>make smoke-openhands</code>.</div>
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
      <div class="card-b danger-row">
        <div class="grow"><div class="strong">Factory reset</div>
          <div class="sm muted">Everything above <b>plus the repositories</b>: empties the
          repo registry and deletes per-repo team notes. The app returns to a blank
          estate — re-add repositories in the Repositories view, or restore the demo
          estate with <code>git checkout -- registry/</code>.</div></div>
        <button class="btn danger" id="factory-reset">Factory reset</button>
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
