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
import app_paths
import run_progress                      # R12: mutable paths resolve here
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
for f in app_paths.catalog_files(ROOT):
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
# LLM spend tile (cost-reduction 1.2). `~` marks simulated figures — a mock
# estate's number must never read as a measured dollar.
try:
    import cost_report as _cr
    _rep = _cr.report(None)
    if _rep["runs"] and _rep["simulated_share"] is not None:
        _sim = "~" if _rep["simulated_share"] > 0 else ""
        tiles.append((f"{_sim}${_rep['total_cost_usd']:.2f}", "LLM spend (all time)",
                      "cost", False))
        # Savings counterfactual (6.3): honest or absent — the tile only exists
        # when a MEASURED median can price the avoided calls.
        if _rep.get("phase_cache_savings_usd") is not None:
            tiles.append((f"${_rep['phase_cache_savings_usd']:.2f}",
                          "est. avoided spend (cache hits x measured median)",
                          "cost", False))
except Exception:
    pass

# Observability 5.1 — firing alerts and recent activity on the Overview.
# The tile only EXISTS when there is something to say: a permanent "0 alerts"
# is furniture people stop reading, and this whole epic is about signals that
# still mean something when they appear. Firing rules are marked `alert` so
# they render as a warning; a rule that could not be evaluated gets its OWN
# tile, because "unevaluable" is not "healthy".
try:
    import alert_rules as _ar
    _st = _ar.evaluate(notify=False)          # never notifies from a render
    _firing = [s for s in _st if s.get("status") == "firing"]
    _unevaluable = [s for s in _st if s.get("status") == "unevaluable"]
    if _firing:
        tiles.append((str(len(_firing)), "alert rule(s) firing", "alerts", True))
    if _unevaluable:
        tiles.append((str(len(_unevaluable)),
                      "alert rule(s) UNEVALUABLE — not the same as healthy",
                      "alerts", True))
except Exception:
    pass
# S4: waivers needing attention. Conditional, like every other tile — a
# permanent "0 waivers" teaches people to stop reading the row.
try:
    import waiver_store as _ws
    _att = _ws.attention()
    if _att["expired"]:
        tiles.append((str(len(_att["expired"])),
                      "EXPIRED waiver(s) — the gate will refuse these",
                      "specflow", True))
    if _att["expiring_soon"]:
        tiles.append((str(len(_att["expiring_soon"])),
                      "waiver(s) expiring soon", "specflow", True))
    if _att.get("unmatched"):
        # Worse than expired: an expired waiver did its job once. This one
        # never has, and looks healthy while the gate refuses the release.
        tiles.append((str(len(_att["unmatched"])),
                      "waiver(s) matching NO scenario — protecting nothing",
                      "specflow", True))
except Exception:
    pass
try:
    import event_log as _el
    _ev, _corrupt = _el.read(limit=200)
    if _ev:
        _bad = len([e for e in _ev if e.get("outcome") in ("failed", "refused")])
        tiles.append((str(len(_ev)), "recent transactions"
                      + (f" ({_bad} failed/refused)" if _bad else ""),
                      "activity", bool(_bad)))
    if _el.health()["degraded"]:
        tiles.append(("!", "transaction log INCOMPLETE — events were dropped",
                      "activity", True))
except Exception:
    pass

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
    plan = app_paths.testplans_dir(ROOT) / f"{key}.md"
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
    # A run record is LLM output that reached disk. `s["id"]` on a scenario that
    # is a bare int, or a test row with no `file`, raised out of the whole
    # generator and killed dashboard generation entirely — one malformed record
    # and the operator has no dashboard at all, for every OTHER run too. The
    # contracts are schema-validated upstream, so this is defence in depth, and
    # it degrades the ROW rather than the page. Same reasoning as run_record's
    # torn-TSV line and the conftest sweep's non-dict guard.
    scen = contracts.get("testplan", {}).get("scenarios", [])
    if scen:
        left += "<h3>Scenarios</h3>" + "".join(
            (f'<div class="scen"><code>{esc(s.get("id", "?"))}</code> '
             f'{esc(s.get("title", ""))} '
             f'<span class="chip chip-info sm">{esc(s.get("layer", "?"))}</span>'
             f'<span class="muted sm">→ {esc(s.get("target_repo", "?"))}</span></div>'
             if isinstance(s, dict) else
             f'<div class="scen muted sm">unreadable scenario entry: {esc(repr(s)[:60])}'
             f' — the record is malformed, not empty</div>')
            for s in scen)
    data_dir = app_paths.testdata_dir(ROOT) / key
    if data_dir.exists():
        files = [p for p in sorted(data_dir.rglob("*")) if p.is_file()]
        left += "<h3>Test data</h3>" + "".join(
            f'<div><code class="sm muted">testdata/{esc(key)}/'
            f'{esc(p.relative_to(data_dir).as_posix())}</code></div>' for p in files)
    gen = contracts.get("generate", {})
    if gen.get("tests"):
        right += "<h3>Generated tests</h3>" + "".join(
            (f'<div class="sm"><code>{esc(t.get("file", "?"))}</code> '
             f'<span class="chip chip-success sm">{esc(t.get("action", "?"))}</span></div>'
             if isinstance(t, dict) else
             f'<div class="sm muted">unreadable test entry: {esc(repr(t)[:60])}</div>')
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
    # In-place review (roadmap 4.1): the decision lives on the same screen as the
    # code. The reviewer reads the rendered diff above and acts here — no hop to
    # the Runs view, no losing the context they just built.
    review_bar = (
        f'<div class="art-sec art-review" data-review-key="{esc(key)}" '
        f'style="display:flex; gap:8px; align-items:center; flex-wrap:wrap">'
        f'<input class="h32 art-note" placeholder="review note (optional — '
        f'required for changes requested)" style="flex:1; min-width:220px">'
        f'<button class="btn btn-sm approve-here">Approve</button>'
        f'<button class="btn btn-sm danger changes-here">Request changes</button>'
        f'<span class="sm muted art-review-state"></span></div>')
    art_panels_html += (f'<article class="card art-panel{"" if first else " hidden"}" '
                        f'data-art-panel="{esc(key)}">{head}{inner or chr(10)}'
                        f'{review_bar}</article>')
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
                            for x in run_progress.dict_rows(m.get("tests"))[:6])
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
       ("progress", "◉", "Run progress"),
       ("queue", "⇥", "Intake & queue"),
       ("plans", "✎", "Test plans"), ("specflow", "✓", "Spec workflow"),
       ("runs", "▶", "Runs & reviews"), ("activity", "⚡", "Activity"), ("alerts", "△", "Alerts"),
       ("trace", "⇢", "Trace"),
       ("cost", "◔", "Cost"),
       ("artifacts", "❏", "Artifacts"),
       ("catalog", "☰", "Test catalog"), ("repos", "⛁", "Repositories"),
       ("settings", "⚙", "Settings")]
TITLES = {"overview": "Overview", "wizard": "Guided run — PR or JIRA, step by step",
          "progress": "Run progress — where a request is, and why it failed",
          "queue": "Intake & work queue",
          "plans": "Test plans — review & approval",
          "specflow": "Spec workflow — how an E2E test gets built here",
          "runs": "Runs & team reviews",
          "activity": "Activity — every transaction, who did it and what happened",
          "alerts": "Alerts — rules over the transaction log",
          "cost": "Cost — LLM spend & savings",
          "artifacts": "Generated artifacts",
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

.rp-steps{list-style:none;padding:0;margin:12px 0 0}
.rp-s{display:flex;gap:10px;padding:9px 0;border-top:1px solid var(--sr-border)}
.rp-m{width:26px;text-align:center;font-weight:700;font-family:var(--sr-font-mono);font-size:12px}
.rp-l{font-weight:600}
.rp-st{margin-left:8px;font-weight:400;font-size:11px;text-transform:uppercase;
  letter-spacing:.04em;color:var(--sr-fg-muted)}
.rp-w{font-size:12px;color:var(--sr-fg-muted);margin-top:2px}
.rp-d{font-size:12px;margin-top:3px}
.rp-done .rp-m{color:var(--sr-success-fg)}
.rp-running .rp-m{color:var(--sr-info-fg)}
.rp-failed .rp-m{color:var(--sr-danger-fg)}
.rp-unknown .rp-m{color:var(--sr-warning-fg)}
.rp-pending .rp-m,.rp-skipped .rp-m{color:var(--sr-fg-muted);opacity:.6}
.rp-bad{margin-top:10px;border-left:3px solid var(--sr-danger-fg)}
.rp-log{background:var(--sr-bg-muted);padding:8px;border-radius:6px;font-size:11px;
  max-height:220px;overflow:auto;white-space:pre-wrap;font-family:var(--sr-font-mono)}

"""

# ---------------------------------------------------------------- client JS
JS = """
const served = location.protocol.startsWith('http');
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
// Every client-rendered cell goes through this — queue items and fetched JIRA
// summaries are external data and must never reach innerHTML unescaped.
// Escapes the single quote too. Every attribute in this file is double-quoted,
// so `"` alone is sufficient TODAY — and that is exactly the shape of guard
// this codebase keeps losing: correct until someone writes one single-quoted
// attribute, at which point ticket text becomes executable. Escaping both
// quote characters costs nothing and removes the dependency on a convention
// nobody is enforcing.
const escHtml = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
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
  progress: 'Run progress — where a request is, and why it failed',
  queue: 'Intake & work queue',
  plans: 'Test plans — review & approval',
  specflow: 'Spec workflow — how an E2E test gets built here',
  runs: 'Runs & team reviews',
  activity: 'Activity — every transaction, who did it and what happened',
  alerts: 'Alerts — rules over the transaction log',
  trace: 'Trace — story/PR to release',
  cost: 'Cost — LLM spend & savings',
  artifacts: 'Generated artifacts',
  catalog: 'Test knowledge catalog', repos: 'Repositories & mapping',
  settings: 'Settings & integrations' };
// Loaders register themselves here; `go` runs the entering view's on ARRIVAL.
// Before this existed, every loader fired exactly once at page load and never
// again, which broke two different ways:
//   * a loader that failed at load (server still starting, one transient error)
//     left its table permanently empty — and its catch swallowed the error, so
//     the user saw a blank table with no explanation and no way to retry
//     except a full reload;
//   * views whose whole purpose is "what is happening NOW" — the transaction
//     log, alerts, the run queue — silently showed a page-load snapshot. A
//     stale activity log is worse than an empty one: it looks current.
// Registration is deferred (loaders are defined further down) and `go` is
// called once before any of them exist, so the lookup must tolerate a miss.
const VIEW_LOAD = {};
function onEnter(view, fn) { (VIEW_LOAD[view] = VIEW_LOAD[view] || []).push(fn); }
function runViewLoaders(view) {
  (VIEW_LOAD[view] || []).forEach(fn => {
    // One failing loader must not stop its neighbours on the same view.
    try { Promise.resolve(fn()).catch(() => {}); } catch (e) { /* keep going */ }
  });
}
function go(view) {
  $$('[data-view]').forEach(v => v.classList.toggle('on', v.dataset.view === view));
  $$('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.go === view));
  $('#view-title').textContent = TITLES[view] || view;
  // Persist the active view in the URL hash: repo add/edit/remove, settings
  // saves and clears finish with location.reload(), and without this every
  // reload dumped the user back on Overview instead of the view they were in.
  try { history.replaceState(null, '', '#' + view); } catch (e) { /* file:// */ }
  runViewLoaders(view);
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

// A loader that fails must SAY so. Swallowing the error left an empty table
// that is indistinguishable from "there is genuinely nothing here" — and on the
// activity and alert views those two readings lead to opposite actions.
function loadFailed(sel, cols, err) {
  const tb = document.querySelector(sel);
  if (!tb) return;
  tb.innerHTML = '<tr><td colspan="' + cols + '" class="muted">Could not load ' +
    'this — ' + escHtml(String((err && err.message) || err || 'request failed')) +
    '. This is a display failure, not an empty result: use Refresh, and check ' +
    'the server is still running.</td></tr>';
}

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
  // A failed fetch used to reject straight out of here; runViewLoaders swallows
  // it so the view keeps whatever it had. On THIS view that reads as "the queue
  // is empty, nothing is running" — the reading that makes an operator queue a
  // duplicate run, or walk away believing their submission was never accepted.
  // Every other loading view reports; this one did not. Found by failing fetch
  // against the served page, which is also how the loadFailed pattern was found.
  let q;
  try {
    q = await api('/api/queue');
  } catch (err) {
    loadFailed('#queue-table tbody', 7, err);
    const c = $('#queue-count');
    if (c) c.textContent = 'queue status unknown — the list below is not current';
    return [];
  }
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
onEnter('queue', refreshQueue);

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
onEnter('runs', refreshOpenHands);

// ---- spec workflow (SDD adoption S1)
async function refreshSpecFlow() {
  const tb = document.querySelector('#sf-table tbody');
  if (!served || !tb) return;
  try {
    const d = await api('/api/spec-workflow');
    const g = d.governance || {};
    // Say plainly whether any of this is ENFORCED. A workflow view that
    // silently reflects configuration teaches a rule the platform is not
    // applying — the adoption gap this whole view exists to close.
    const gov = $('#sf-gov');
    if (gov) {
      // A configured value the gate could not use is reported ABOVE the
      // enforcement answer. Without it the page says a bare "off", which reads
      // as a decision somebody made rather than a typo silently ignored.
      const probs = (g.problems || []).length
        ? '<div class="chip chip-danger">CONFIGURATION IGNORED — ' +
          escHtml((g.problems || []).join(' · ')) + '</div>'
        : '';
      gov.innerHTML = probs + (d.enforced
        ? '<b>Enforced.</b> requirements gate: <code>' +
          escHtml(String(g.requirements_gate)) + '</code> · spec enforce: <code>' +
          escHtml(g.spec_enforce) + '</code><br>' + escHtml(g.spec_enforce_effect)
        : '<b>Nothing below is enforced yet.</b> requirements gate is off and ' +
          'spec enforce is <code>off</code>, so every step is advisory — the ' +
          'platform will not stop a run that skips it. Turn them on in Settings ' +
          'when the signal looks clean (start with <code>warn</code>).');
    }
    if (!d.rows.length) {
      tb.innerHTML = '<tr><td colspan="5" class="muted">No tickets in the ' +
        'workflow yet — run <code>make requirements KEY=..</code> or ' +
        '<code>make plan KEY=..</code> to start one.</td></tr>';
      return;
    }
    tb.innerHTML = d.rows.map(r => {
      const done = !r.blocker;
      const cls = done ? '' : (r.advisory ? 'chip-warning' : 'chip-danger');
      // The progress trail makes "how far along" readable at a glance.
      const trail = d.states.map((s, i) =>
        '<span class="' + (i < r.state_index ? '' : (i === r.state_index ? 'chip ' + cls : 'muted')) +
        '" title="' + escHtml(s) + '">' + (i < r.state_index ? '●' : (i === r.state_index ? '◉' : '○')) +
        '</span>').join(' ');
      return '<tr><td class="mono sm">' + escHtml(r.key) + '</td>' +
        '<td class="sm">' + trail + '<div class="muted sm">' + escHtml(r.state) +
          (r.advisory ? ' (advisory)' : '') + '</div></td>' +
        '<td class="sm">' + (done ? '<span class="chip">complete</span>'
                                  : escHtml(r.blocker)) + '</td>' +
        '<td class="sm">' + escHtml(r.owner || '—') + '</td>' +
        '<td class="sm mono">' + escHtml(r.action || '—') + '</td></tr>';
    }).join('');
  } catch (e) { loadFailed('#sf-table tbody', 5, e); }
}
if ($('#sf-refresh')) $('#sf-refresh').addEventListener('click', refreshSpecFlow);
refreshSpecFlow();
onEnter('specflow', refreshSpecFlow);

// ---- coverage subtraction (SDD adoption S5)
async function loadSavings() {
  const el = $('#sv-body');
  if (!served || !el) return;
  try {
    const d = await api('/api/spec-savings');
    const s = d.savings || {};
    if (!d.scenarios) {
      el.innerHTML = '<div class="muted sm">No signed specs yet — nothing to ' +
        'subtract. Approve a plan with structured scenarios to start.</div>';
      return;
    }
    // The count is measured. The money is not, and says so in those words:
    // a zero would read as "no saving", an estimate as a measurement.
    const money = s.usd === null || s.usd === undefined
      ? '<span class="chip chip-warning">value not measured</span> ' +
        '<span class="muted sm">' + escHtml(s.why || '') + '</span>'
      : '<span class="chip">~$' + escHtml(String(s.usd)) + ' (' + escHtml(s.basis) + ')</span>';
    el.innerHTML =
      '<div class="sub" style="padding-bottom:6px"><b>' + d.already_covered +
      '</b> of <b>' + d.scenarios + '</b> approved scenario(s) already covered — ' +
      '<b>' + d.to_author + '</b> would still need authoring.</div>' + money +
      '<div class="scroll" style="padding-top:8px"><table>' +
      '<thead><tr><th>ticket</th><th>covered</th><th>to author</th>' +
      '<th>unlinked tests</th></tr></thead><tbody>' +
      d.keys.map(p => '<tr><td class="mono sm">' + escHtml(p.key) + '</td>' +
        '<td class="sm">' + p.already_covered + '/' + p.scenarios + '</td>' +
        '<td class="sm">' + p.to_author + '</td>' +
        '<td class="sm muted">' + (p.unlinked_tests || 0) + '</td></tr>').join('') +
      '</tbody></table></div>' +
      '<div class="muted sm" style="padding-top:6px">Advisory: nothing is ' +
      'skipped automatically. A wrong join would silently drop coverage — the ' +
      'one failure this platform cannot see.</div>';
  } catch (e) { el.innerHTML = '<div class="muted sm">Could not load the '
      + 'subtraction — ' + escHtml(String(e && e.message || e)) + '. This is a '
      + 'display failure, not a zero.</div>'; }
}
if ($('#sv-load')) $('#sv-load').addEventListener('click', loadSavings);
loadSavings();
onEnter('specflow', loadSavings);

// ---- generated governance page (SDD adoption S6)
async function loadGovernance() {
  const el = $('#gv-body');
  if (!served || !el) return;
  try {
    const d = await api('/api/governance');
    // The honest headline first: a reader who takes these rules as enforced,
    // when they are not, has been misled by this page.
    const head = d.enforced
      ? '<div class="chip">ENFORCED — ' + escHtml(d.governance.spec_enforce_effect) + '</div>'
      : '<div class="chip chip-warning">NOT ENFORCED — every rule below is ' +
        'advisory in this estate; the platform will not stop a run that skips it</div>';
    const warn = d.unpinned.length
      ? '<div class="chip chip-danger">clause(s) with no live pin: ' +
        escHtml(d.unpinned.join(', ')) + ' — rules nothing currently defends</div>'
      : '';
    el.innerHTML = head + warn + '<div class="sub" style="padding-top:6px">' +
      d.clause_count + ' clauses, read from ' + escHtml(d.source) + '</div>';
  } catch (e) { /* the static rules below still render */ }
}
loadGovernance();
onEnter('specflow', loadGovernance);

// ---- waivers (SDD adoption S4)
async function loadWaivers() {
  const tb = document.querySelector('#wv-table tbody');
  const key = ($('#rq-key') && $('#rq-key').value.trim()) || '';
  if (!served || !tb || !key) return;
  try {
    const d = await api('/api/waivers?key=' + encodeURIComponent(key));
    tb.innerHTML = d.waivers.length ? d.waivers.map(w => {
      // MATCHES NOTHING outranks the expiry chip: a waiver whose scenario id
      // is not in the signed spec is inert whether or not it has time left,
      // and showing "44d left" on it is the reassuring half of the truth.
      const state = w.unmatched
        ? '<span class="chip chip-danger" title="This scenario id is not in the ' +
          'signed spec — the gate will keep refusing whatever you meant to ' +
          'waive">MATCHES NOTHING</span>'
        : (w.expired ? '<span class="chip chip-danger">EXPIRED</span>'
        : (w.expiring_soon ? '<span class="chip chip-warning">' + w.days_left + 'd left</span>'
                           : '<span class="chip">' + w.days_left + 'd left</span>'));
      return '<tr><td class="mono sm">' + escHtml(w.scenario) + '</td>' +
        '<td class="sm">' + escHtml(w.reason) + '</td>' +
        '<td class="sm">' + escHtml(w.by) + '</td>' +
        '<td class="mono sm">' + escHtml(w.expires) + '</td>' +
        '<td>' + state + '</td>' +
        '<td><button class="btn btn-sm wv-del" data-sid="' + escHtml(w.scenario) +
          '">remove</button></td></tr>';
    }).join('') : '<tr><td colspan="6" class="muted">No waivers — every approved ' +
      'scenario is either covered or the gate will refuse it.</td></tr>';
  } catch (e) { /* diagnostic panel — never break the view */ }
}
function wvMsg(t, bad) {
  const m = $('#wv-msg'); if (!m) return;
  m.textContent = t; m.style.display = t ? '' : 'none';
  m.style.color = bad ? 'var(--sr-danger-fg)' : '';
}
if ($('#wv-load')) $('#wv-load').addEventListener('click', loadWaivers);
if ($('#wv-add')) $('#wv-add').addEventListener('click', async () => {
  const key = ($('#rq-key') && $('#rq-key').value.trim()) || '';
  if (!key) { wvMsg('enter a ticket key in the Requirements panel first', true); return; }
  try {
    const r = await api('/api/waivers/save', {
      key: key, scenario: ($('#wv-sid') || {}).value, reason: ($('#wv-reason') || {}).value,
      expires: ($('#wv-exp') || {}).value });
    // Saved, but possibly inert. Reported at the moment of saving, not only in
    // the row: the person who just typed the id is the one who can fix it, and
    // "Waiver saved." alone reads as "you are covered".
    wvMsg(r && r.warning ? 'Saved, but it protects nothing: ' + r.warning
                         : 'Waiver saved.', !!(r && r.warning));
    loadWaivers();
  } catch (e) {
    // The refusal text IS the answer: it says what would make it acceptable.
    wvMsg('Refused: ' + e.message, true);
  }
});
document.addEventListener('click', async e => {
  if (!e.target.classList.contains('wv-del')) return;
  const key = ($('#rq-key') && $('#rq-key').value.trim()) || '';
  try {
    await api('/api/waivers/remove', { key: key, scenario: e.target.dataset.sid });
    wvMsg('Waiver removed — the gate will refuse this scenario until it is covered.');
    loadWaivers();
  } catch (err) { wvMsg('Remove failed: ' + err.message, true); }
});

// ---- requirements review (SDD adoption S2)
async function loadRequirements() {
  const key = ($('#rq-key') && $('#rq-key').value.trim()) || '';
  const body = $('#rq-body');
  if (!served || !body || !key) return;
  try {
    const d = await api('/api/requirements?key=' + encodeURIComponent(key));
    const rows = (d.requirements || []).map(r =>
      '<tr><td class="mono sm">' + escHtml(r.id || '') + '</td>' +
      '<td class="sm">' + escHtml(r.ears || '') + '</td>' +
      '<td class="sm muted">' + escHtml(r.source || '') + '</td></tr>').join('');
    // Blocking ambiguities lead, because they are the reason not to approve.
    const amb = (d.ambiguities || []).map(a =>
      '<li class="sm">' + (a.blocking ? '<span class="chip chip-danger">blocking</span> ' : '') +
      '<b>' + escHtml(a.id || '') + '</b> — ' + escHtml(a.question || '') + '</li>').join('');
    let banner = '';
    if (d.stale) {
      banner = '<div class="chip chip-warning">requirements changed AFTER approval — ' +
               're-approve or the signature refers to text nobody read</div>';
    } else if (d.status === 'approved') {
      banner = '<div class="chip">approved' + (d.by ? ' by ' + escHtml(d.by) : '') + '</div>';
    } else {
      banner = '<div class="chip chip-warning">not approved' +
               (d.gate_on ? ' — planning will REFUSE until it is'
                          : ' — advisory: the gate is off, planning proceeds anyway') + '</div>';
    }
    body.innerHTML = banner +
      (amb ? '<h3 class="sm">What the ticket does not say</h3><ul>' + amb + '</ul>' : '') +
      '<div class="scroll"><table><thead><tr><th>id</th><th>EARS statement</th>' +
      '<th>source</th></tr></thead><tbody>' +
      (rows || '<tr><td colspan="3" class="muted">no requirements yet — run ' +
               '<code>make requirements KEY=' + escHtml(key) + '</code></td></tr>') +
      '</tbody></table></div>';
  } catch (e) { body.innerHTML = '<div class="muted sm">could not load: ' + escHtml(e.message) + '</div>'; }
}
function rqMsg(t, bad) {
  const m = $('#rq-msg'); if (!m) return;
  m.textContent = t; m.style.display = t ? '' : 'none';
  m.style.color = bad ? 'var(--sr-danger-fg)' : '';
}
if ($('#rq-load')) $('#rq-load').addEventListener('click', loadRequirements);
if ($('#rq-approve')) $('#rq-approve').addEventListener('click', async () => {
  const key = ($('#rq-key') && $('#rq-key').value.trim()) || '';
  if (!key) { rqMsg('enter a ticket key first', true); return; }
  try {
    const r = await api('/api/requirements/status', { key: key, status: 'approved' });
    rqMsg('Approved — the requirements hash is now signed.');
    loadRequirements(); refreshSpecFlow();
  } catch (e) {
    // A refusal is a RESULT worth reading, not an error to swallow: it names
    // the unanswered question that makes approval premature.
    rqMsg('Not approved: ' + e.message, true);
  }
});

// ---- alert rules (observability 3.1-3.4)
let AL_RULES = [], AL_META = { kinds: [], channels: ['slack', 'email', 'both'] };
function alRow(r, st) {
  const m = r.match || {};
  // Status carries its REASON when unevaluable — "ok" with no explanation is
  // exactly the lie this feature exists to avoid.
  let badge = '<span class="chip">—</span>';
  if (st) {
    const cls = st.status === 'firing' ? 'chip-danger'
      : (st.status === 'unevaluable' ? 'chip-warning' : '');
    badge = '<span class="chip ' + cls + '" title="' + escHtml(st.reason || '') + '">' +
      escHtml(st.status) + (st.hits !== undefined ? ' (' + st.hits + ')' : '') + '</span>';
    if (st.problems && st.problems.length) {
      badge += '<div class="muted sm">' + escHtml(st.problems.join('; ')) + '</div>';
    }
  }
  return '<tr data-id="' + escHtml(r.id) + '">' +
    '<td><input class="h32 al-f" data-f="name" value="' + escHtml(r.name || '') + '"></td>' +
    '<td><input class="h32 al-f" data-f="kinds" style="min-width:210px" value="' +
      escHtml((m.kinds || []).join(',')) + '" placeholder="gate.refused,run.aborted"></td>' +
    '<td><input class="h32 al-f" data-f="outcome" style="width:90px" value="' +
      escHtml(m.outcome || '') + '" placeholder="any"></td>' +
    '<td><input class="h32 al-f" data-f="target_contains" style="width:120px" value="' +
      escHtml(m.target_contains || '') + '"></td>' +
    '<td><input class="h32 al-f" data-f="threshold" type="number" min="1" style="width:64px" value="' +
      escHtml(String(r.threshold || 1)) + '"></td>' +
    '<td><input class="h32 al-f" data-f="window_minutes" type="number" min="1" style="width:80px" value="' +
      escHtml(String(r.window_minutes || 60)) + '"></td>' +
    '<td><input class="h32 al-f" data-f="cooldown_minutes" type="number" min="0" style="width:80px" value="' +
      escHtml(String(r.cooldown_minutes == null ? 60 : r.cooldown_minutes)) + '"></td>' +
    '<td><select class="h32 al-f" data-f="channel">' +
      AL_META.channels.map(c => '<option' + (r.channel === c ? ' selected' : '') + '>' +
        escHtml(c) + '</option>').join('') + '</select></td>' +
    // Per-rule recipients. The backend has always honoured these (they set
    // SMTP_TO for the delivery), but the row had no field and alCollect sent
    // recipients:[] unconditionally — so an email rule built in this UI could
    // never deliver, and the only feedback was the rule's own "nothing will be
    // delivered" warning with nothing the user could do about it.
    '<td><input class="h32 al-f" data-f="recipients" style="width:150px" ' +
      'placeholder="qa@example.com" value="' +
      escHtml((r.recipients || []).join(', ')) + '"></td>' +
    '<td><input type="checkbox" class="al-f" data-f="digest"' +
      (r.digest ? ' checked' : '') + '></td>' +
    '<td><input type="checkbox" class="al-f" data-f="enabled"' +
      (r.enabled === false ? '' : ' checked') + '></td>' +
    '<td>' + badge + '</td>' +
    '<td><button class="btn btn-sm al-test" title="Send through the REAL channel">Test</button>' +
      ' <button class="btn btn-sm al-del">✕</button></td></tr>';
}
function alCollect() {
  return Array.from(document.querySelectorAll('#al-table tbody tr')).map((tr, i) => {
    const g = f => { const e = tr.querySelector('[data-f="' + f + '"]'); return e ? e.value : ''; };
    const chk = tr.querySelector('[data-f="enabled"]');
    const dig = tr.querySelector('[data-f="digest"]');
    return {
      id: tr.dataset.id || ('rule-' + (i + 1)), name: g('name'),
      enabled: chk ? chk.checked : true,
      match: { kinds: g('kinds').split(',').map(x => x.trim()).filter(Boolean),
               outcome: g('outcome'), target_contains: g('target_contains') },
      threshold: Number(g('threshold') || 1),
      window_minutes: Number(g('window_minutes') || 60),
      cooldown_minutes: Number(g('cooldown_minutes') || 60),
      channel: g('channel') || 'slack',
      recipients: g('recipients').split(',').map(x => x.trim()).filter(Boolean),
      digest: dig ? dig.checked : false
    };
  });
}
async function refreshAlerts() {
  const tb = document.querySelector('#al-table tbody');
  if (!served || !tb) return;
  try {
    const d = await api('/api/alerts');
    AL_RULES = d.rules || []; AL_META = { kinds: d.kinds || [], channels: d.channels || AL_META.channels };
    const byId = {};
    (d.status || []).forEach(s => { byId[s.id] = s; });
    tb.innerHTML = AL_RULES.length
      ? AL_RULES.map(r => alRow(r, byId[r.id])).join('')
      : '<tr><td colspan="13" class="muted">No rules yet — "Add rule" creates one.</td></tr>';
  } catch (e) { loadFailed('#al-table tbody', 13, e); }
}
function alMsg(t, bad) {
  const m = $('#al-msg'); if (!m) return;
  m.textContent = t; m.style.display = t ? '' : 'none';
  m.style.color = bad ? 'var(--sr-danger-fg)' : '';
}
if ($('#al-add')) {
  $('#al-add').addEventListener('click', () => {
    const tb = document.querySelector('#al-table tbody');
    if (tb.querySelector('td.muted')) tb.innerHTML = '';
    tb.insertAdjacentHTML('beforeend', alRow(
      { id: 'rule-' + (tb.children.length + 1), name: 'new rule', enabled: true,
        match: { kinds: [] }, threshold: 3, window_minutes: 60,
        cooldown_minutes: 60, channel: 'slack' }, null));
  });
}
if ($('#al-save')) {
  $('#al-save').addEventListener('click', async () => {
    try {
      const r = await api('/api/alerts/save', { rules: alCollect() });
      const probs = Object.entries(r.problems || {});
      // Problems are shown, not swallowed: a rule that can never match is
      // saved so the user can fix it, but they must be told.
      alMsg(probs.length
        ? 'Saved ' + r.saved + ' rule(s). Check: ' +
          probs.map(([id, p]) => id + ': ' + p.join('; ')).join(' · ')
        : 'Saved ' + r.saved + ' rule(s).', probs.length > 0);
      refreshAlerts();
    } catch (e) { alMsg('Save failed: ' + e.message, true); }
  });
}
document.addEventListener('click', async e => {
  const tr = e.target.closest('#al-table tr');
  if (!tr) return;
  if (e.target.classList.contains('al-del')) { tr.remove(); return; }
  if (e.target.classList.contains('al-test')) {
    alMsg('Sending a test through the real channel…');
    try {
      const r = await api('/api/alerts/test', { id: tr.dataset.id });
      alMsg(r.ok ? 'Test delivered via ' + r.channel + ' — check the Activity view for notify.sent'
                 : 'Test FAILED via ' + r.channel + ' (' + (r.problems || []).join('; ') + ')', !r.ok);
    } catch (err) { alMsg('Test failed: ' + err.message, true); }
  }
});
refreshAlerts();
onEnter('alerts', refreshAlerts);

// ---- activity: the transaction log (observability 2.1-2.3)
async function refreshActivity() {
  const tb = document.querySelector('#ev-table tbody');
  if (!served || !tb) return;
  const q = new URLSearchParams();
  const val = sel => { const e = $(sel); return e ? String(e.value || '').trim() : ''; };
  if (val('#ev-kind')) q.set('kind', val('#ev-kind'));
  if (val('#ev-actor')) q.set('actor', val('#ev-actor'));
  if (val('#ev-target')) q.set('target', val('#ev-target'));
  if (val('#ev-outcome')) q.set('outcome', val('#ev-outcome'));
  q.set('limit', '300');
  try {
    const d = await api('/api/events?' + q.toString());
    // Say the history is INCOMPLETE rather than showing a convincing partial
    // list — the same rule the cost report follows about unmeasured figures.
    const warn = $('#ev-warn'), notes = [];
    if (d.corrupt) notes.push(d.corrupt + ' unreadable line(s) skipped');
    if (d.health && d.health.degraded) {
      notes.push('this process could not write ' + d.health.dropped +
                 ' event(s) — the list below is INCOMPLETE');
    }
    if (warn) {
      warn.textContent = notes.join(' · ');
      warn.style.display = notes.length ? '' : 'none';
    }
    if (!d.events.length) {
      tb.innerHTML = '<tr><td colspan="7" class="muted">No transactions match. ' +
        'The log starts when the platform next does something — it is not backfilled.' +
        '</td></tr>';
      return;
    }
    // Every cell escaped: `actor` arrives from an SSO header and `target` from a
    // request path, neither of which this platform controls.
    tb.innerHTML = d.events.map(r => {
      const cls = r.outcome === 'ok' ? '' :
        (r.outcome === 'refused' ? 'chip-warning' : 'chip-danger');
      return '<tr><td class="mono sm">' + escHtml(r.ts) + '</td>' +
        '<td class="sm">' + escHtml(r.kind) + '</td>' +
        '<td class="sm">' + escHtml(r.actor || '—') +
          (r.actor_source && r.actor_source !== 'explicit'
            ? '<span class="muted sm"> (' + escHtml(r.actor_source) + ')</span>' : '') + '</td>' +
        '<td class="mono sm">' + escHtml(r.target || '—') + '</td>' +
        '<td><span class="chip ' + cls + '">' + escHtml(r.outcome) + '</span></td>' +
        '<td class="num sm">' + (r.duration_ms == null ? '—' : escHtml(String(r.duration_ms))) + '</td>' +
        '<td class="mono sm muted">' + escHtml(r.run_id || '—') + '</td></tr>';
    }).join('');
  } catch (err) { loadFailed('#ev-table tbody', 7, err); }
}
['#ev-refresh', '#ev-kind', '#ev-actor', '#ev-target', '#ev-outcome'].forEach(sel => {
  const el = $(sel);
  if (el) el.addEventListener(el.tagName === 'BUTTON' ? 'click' : 'change', refreshActivity);
});
refreshActivity();
onEnter('activity', refreshActivity);

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
onEnter('trace', refreshTraceMatrix);

// ---- Cost view (cost-reduction 6.1): one payload, three cards. Measured vs
// simulated is labelled on every number — the iron rule made visible.
async function refreshCost() {
  if (!served) return;
  try {
    const d = await api('/api/cost-report');
    const sim = d.simulated_share;
    const badge = sim === null ? '<span class="chip chip-muted">no spend data</span>'
      : sim === 0 ? '<span class="chip chip-ok">measured</span>'
      : sim === 1 ? '<span class="chip chip-warning">all simulated</span>'
      : '<span class="chip chip-warning">' + Math.round(sim * 100) + '% simulated</span>';
    const el = document.getElementById('cost-badge');
    if (el) el.innerHTML = badge;
    const modes = Object.entries(d.by_mode || {}).map(([m, v]) =>
      escHtml(m) + ': ' + v.runs + ' run(s) $' + v.cost_usd.toFixed(4)).join(' · ');
    const sum = document.getElementById('cost-summary');
    if (sum) sum.innerHTML = '<b>Total $' + (d.total_cost_usd || 0).toFixed(4) +
      '</b> across ' + d.runs + ' run(s)' + (modes ? ' — ' + modes : '');
    const pt = document.querySelector('#cost-phase-table tbody');
    if (pt) pt.innerHTML = Object.entries(d.by_phase || {}).sort().map(([k, v]) =>
      '<tr><td class="mono sm">' + escHtml(k) + '</td><td>' + v.calls + '</td>' +
      '<td>$' + v.cost_usd.toFixed(4) + '</td><td>' + v.input_tokens + '</td>' +
      '<td>' + v.cache_read_tokens + '</td><td>' + Math.round(v.cache_hit_rate * 100) + '%</td>' +
      '<td>' + v.turns_p50 + '/' + v.turns_p95 + '</td><td>' + v.max_turns + '</td>' +
      '<td>' + v.suggested_max_turns + '</td></tr>').join('') ||
      '<tr><td colspan="9"><div class="empty">No spend recorded yet.</div></td></tr>';
    const pt2 = document.querySelector('#cost-provider-table tbody');
    if (pt2) {
      const fmt = (v, bases) => {
        const b = Object.keys(bases || {});
        if (b.length === 1 && b[0] === 'local') return '$0 (local)';
        if (b.length === 1 && b[0] === 'simulated') return '~$' + v.toFixed(4);
        if (b.includes('estimated')) return '~$' + v.toFixed(4);
        if (b.length === 1 && b[0] === 'unknown') return 'unknown';
        return '$' + v.toFixed(4);
      };
      pt2.innerHTML = Object.entries(d.by_provider || {}).sort().map(([k, v]) =>
        '<tr><td class="mono sm">' + escHtml(k) + '</td><td>' + v.calls + '</td>' +
        '<td>' + fmt(v.cost_usd, v.bases) + '</td>' +
        '<td class="sm muted">' + escHtml(Object.keys(v.bases || {}).join(', ') || '—') + '</td>' +
        '<td>' + v.input_tokens + '</td><td>' + v.output_tokens + '</td></tr>').join('') ||
        '<tr><td colspan="6"><div class="empty">No provider spend recorded yet.</div></td></tr>';
    }
    const ls = document.getElementById('cost-localsplit');
    if (ls) ls.textContent = (d.local_tokens || d.cloud_tokens)
      ? 'Local vs cloud tokens: ' + (d.local_tokens || 0) + ' local (no cloud spend) vs ' +
        (d.cloud_tokens || 0) + ' cloud — moving phases to a local provider avoids the local share.'
      : '';
    const kt = document.querySelector('#cost-keys-table tbody');
    if (kt) kt.innerHTML = (d.by_key_top10 || []).map(e =>
      '<tr><td class="mono sm">' + escHtml(e.key) + '</td><td>' + e.runs + '</td>' +
      '<td>$' + e.cost_usd.toFixed(4) + '</td></tr>').join('') ||
      '<tr><td colspan="3"><div class="empty">No keyed spend yet.</div></td></tr>';
    const sv = document.getElementById('cost-savings');
    if (sv) sv.textContent = 'Phase-cache hits: ' + (d.phase_cache_hits || 0) +
      ' — estimated saving: ' + (d.phase_cache_savings_usd != null
        ? '$' + d.phase_cache_savings_usd.toFixed(4)
        : 'n/a (no measured runs yet)') +
      (d.openhands_payload_est_tokens
        ? ' · OpenHands payloads ~' + d.openhands_payload_est_tokens +
          ' tokens (billed on the OpenHands side)' : '');
  } catch (err) { /* advisory view — never block the dashboard */ }
}
refreshCost();
onEnter('cost', refreshCost);

// ---- batch review (roadmap 4.3): clear a filtered set in one confirmed pass
const batchBtn = document.getElementById('approve-filtered');
if (batchBtn) batchBtn.addEventListener('click', async () => {
  if (needsServer()) return;
  // Only VISIBLE pending rows: the release/review filters define the batch, so
  // what you see is exactly what you approve.
  const btns = [...document.querySelectorAll(
    '[data-view="runs"] button.approve[data-key]')].filter(b => b.offsetParent !== null);
  const keys = [...new Set(btns.map(b => b.dataset.key))];
  if (!keys.length) { toast('Nothing visible is awaiting review'); return; }
  if (!confirm('Approve ' + keys.length + ' key(s)?\\n\\n' + keys.join('\\n') +
               '\\n\\nEach decision is recorded individually on the review board.')) return;
  let ok = 0;
  for (const key of keys) {
    try {
      await api('/api/review', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key, status: 'approved', by: 'dashboard',
                               note: 'batch approval' }) });
      ok++;
    } catch (err) { toast(key + ': ' + err.message); }
  }
  toast('Approved ' + ok + '/' + keys.length + ' — reloading');
  location.reload();
});

// ---- in-place review on the Artifacts panels (roadmap 4.1)
document.addEventListener('click', async e => {
  const btn = e.target.closest('.approve-here, .changes-here');
  if (!btn) return;
  if (needsServer()) return;
  const bar = btn.closest('.art-review');
  const key = bar.dataset.reviewKey;
  const note = bar.querySelector('.art-note').value.trim();
  const changes = btn.classList.contains('changes-here');
  if (changes && !note) {
    toast('Requesting changes needs a note — say what to change'); return;
  }
  btn.disabled = true;
  try {
    await api('/api/review', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key, status: changes ? 'changes_requested' : 'approved',
                             by: 'dashboard', note }) });
    bar.querySelector('.art-review-state').textContent =
      (changes ? 'changes requested' : 'approved') + ' ✓ recorded on the board';
    toast((changes ? 'Changes requested for ' : 'Approved ') + key);
  } catch (err) { toast(err.message); btn.disabled = false; }
});

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
  // Reuse provenance (cost-reduction 3.3/6.2): a reused draft must never read
  // as fresh authorship — the reviewer is approving an ADAPTATION.
  const ru = $('#plan-reused');
  if (ru) {
    if (p.reused_from) {
      ru.textContent = 'Reused from ' + p.reused_from + ' (similarity ' +
        (p.similarity != null ? p.similarity : '?') +
        ') — adapted mechanically; check the VERIFY section before approving.';
      ru.classList.remove('hidden');
    } else { ru.classList.add('hidden'); }
  }
  // Per-gap verdicts (roadmap 4.5): the reviewer approves a plan that was already
  // challenged — show WHAT was challenged, not just that a challenge happened.
  // Structured spec (SDD 6.1): review at the level the machine enforces —
  // scenario cards with GWT steps, verification clauses, requirement links
  // and waivers, rendered from specs/<KEY>/testplan.yaml via /api/plans/one.
  const specEl = $('#plan-spec');
  if (specEl) {
    const sc = (p.spec && p.spec.scenarios) || [];
    if (sc.length) {
      const wv = p.waivers || {};
      specEl.innerHTML = sc.map(x => {
        const steps = x.steps || {};
        const gwt = ['given', 'when', 'then'].filter(k => steps[k])
          .map(k => '<div class="sm"><b>' + k.charAt(0).toUpperCase() + k.slice(1) +
                    '</b> ' + escHtml(steps[k]) + '</div>').join('');
        const ver = (x.verification || []).map(v =>
          '<div class="sm muted">verify: ' + escHtml(v) + '</div>').join('');
        const req = (x.requirement_refs || []).length
          ? '<span class="chip chip-muted">' + escHtml(x.requirement_refs.join(', ')) + '</span>' : '';
        const w = wv[x.id];
        const wchip = w ? '<span class="chip ' + (w.expired ? 'chip-danger' : 'chip-warning') + '">' +
          (w.expired ? 'waiver EXPIRED' : 'waived') + '</span>' : '';
        const stale = (p.stale_scenarios || []).includes(x.id)
          ? '<span class="chip chip-danger" title="This scenario references application surface that no longer exists (spec drift) — re-approve, edit, or waive.">stale</span>' : '';
        return '<div style="border:1px solid var(--sr-border); border-radius:8px; padding:8px 12px">' +
          '<div><b class="mono sm">' + escHtml(x.id) + '</b> ' + escHtml(x.title || '') +
          ' <span class="muted sm">[' + escHtml(x.layer || '') + ' · ' + escHtml(x.target_repo || '') + ']</span> ' +
          req + ' ' + wchip + ' ' + stale + '</div>' + gwt + ver + '</div>';
      }).join('');
      specEl.classList.remove('hidden');
    } else { specEl.classList.add('hidden'); }
  }
  // Requirement ambiguities (SDD 2.1): what the ticket left undefined.
  const amb = $('#plan-ambiguities');
  if (amb) {
    const items = p.ambiguities || [];
    if (items.length) {
      amb.innerHTML = items.map(a =>
        '<li><span class="chip ' + (a.blocking ? 'chip-danger' : 'chip-warning') + '">' +
        (a.blocking ? 'blocking' : 'ambiguity') + '</span> <b>' + escHtml(a.id) +
        '</b> <span class="sm">' + escHtml(a.question) + '</span></li>').join('');
      amb.classList.remove('hidden');
    } else { amb.classList.add('hidden'); }
  }
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
  // What changed since approval (roadmap 4.2) — shown only when a previously
  // approved plan differs from its signed baseline, so a re-approval is a review
  // of the DELTA rather than a leap of faith over the whole document.
  const adiff = $('#plan-appdiff');
  if (adiff) {
    adiff.classList.add('hidden');
    api('/api/plans/diff-since-approval?key=' + encodeURIComponent(key)).then(d => {
      if (!d.diff) return;
      adiff.innerHTML = '<b>Changed since last approval</b> — the previous sign-off '
        + 'no longer covers this text:<pre style="white-space:pre-wrap; max-height:240px; '
        + 'overflow:auto; margin:6px 0 0">' + escHtml(d.diff) + '</pre>';
      adiff.classList.remove('hidden');
    }).catch(() => {});
  }
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
    if (!note.trim()) return toast('a note is required — say what needs changing');
    return planPost('/api/plans/status', { status: 'changes_requested', note }, 'Changes requested');
  }
  if (id === 'plan-link')     return planPost('/api/plans/link', {}, r => 'Linked to JIRA: ' + r.ref);
  if (id === 'plan-comment')  return planPost('/api/plans/comment', {}, r => 'Commented on the ticket: plan + tests linked');
  if (id === 'plan-generate') return planPost('/api/plans/generate', {},
    'Queued test generation from the approved plan — press Run queue');
});
refreshPlans();
onEnter('plans', refreshPlans);

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

// ---------------------------------------------------------------- run progress
// The step ladder for ONE run. The Guided run view answers the JOURNEY question
// and collapses the run into a single step; this is the inside of that step —
// what a user needs while waiting, and what they need when it failed.
//
// `unknown` renders DISTINCTLY from pending and done on purpose: it means the
// backend could not observe the step (a dead lock holder, a run that vanished).
// Showing it as pending would read as "starting soon" about something that has
// already stopped.
const RP_MARK = { done: 'ok', running: '>>', failed: 'X', skipped: '-',
                  pending: '.', unknown: '?' };
let rpTimer = null;

function rpRender(p) {
  const src = document.querySelector('#rp-src');
  src.textContent = p.source === 'live' ? 'live'
    : p.source === 'record' ? 'finished run ' + (p.run_id || '') : 'no run';
  const body = document.querySelector('#rp-body');
  if (p.source === 'none') {
    body.textContent = p.detail || 'No run recorded for that key.';
    document.querySelector('#rp-steps').innerHTML = '';
    document.querySelector('#rp-fail').innerHTML = '';
    return;
  }
  body.textContent = (p.key || '') + ' - mode ' + (p.mode || '?')
    + ' - overall ' + p.overall
    + (p.lock === 'stale' ? ' - the run holding this checkout is gone' : '');
  document.querySelector('#rp-steps').innerHTML = (p.steps || []).map(function (st) {
    const detail = st.detail
      ? '<div class="rp-d">' + escHtml(st.detail) + '</div>' : '';
    return '<li class="rp-s rp-' + st.state + '">'
      + '<span class="rp-m">' + (RP_MARK[st.state] || '.') + '</span>'
      + '<div><div class="rp-l">' + escHtml(st.label)
      + '<span class="rp-st">' + escHtml(st.state) + '</span></div>'
      + '<div class="rp-w">' + escHtml(st.why || '') + '</div>' + detail + '</div></li>';
  }).join('');
  // Failure panel: the debugging half. Only the repos that actually failed.
  const gate = (p.steps || []).find(function (x) { return x.id === 'gate' && x.repos; });
  const bad = gate ? gate.repos.filter(function (r) {
    return r.status !== 'committed' && r.status !== 'no_changes'; }) : [];
  document.querySelector('#rp-fail').innerHTML = bad.map(function (r) {
    const tail = r.log_tail === null
      ? '<div class="sub">The log could not be read - that is not the same as an '
        + 'empty log.</div>'
      : '<pre class="rp-log">' + escHtml(r.log_tail || '(empty)') + '</pre>';
    return '<div class="card rp-bad">'
      + '<b>' + escHtml(r.test_repo) + '</b> - ' + escHtml(r.status)
      + ' (exit ' + escHtml(String(r.exit_code)) + ': <b>' + escHtml(r.meaning) + '</b>)'
      + '<div class="sub" style="margin:6px 0">' + escHtml(r.why || '') + '</div>'
      + '<div class="sub">log: <code>' + escHtml(r.log || '') + '</code></div>'
      + tail + '</div>';
  }).join('');
}

let rpSeq = 0;
async function rpLoad(polling) {
  const el = document.querySelector('#rp-key');
  const key = ((el && el.value) || '').trim();
  if (!key) return;
  // Responses can arrive out of order: entering the view fires the loader for
  // whatever key is already in the box, and a user who then types a new key and
  // hits Track has two requests in flight. Without this token the SLOWER,
  // EARLIER response wins and the page shows one key's steps under another
  // key's name - which is the view confidently describing a run nobody asked
  // about. Observed while driving the page, not in review.
  const mine = ++rpSeq;
  try {
    const p = await api('/api/run-progress?key=' + encodeURIComponent(key));
    if (mine !== rpSeq) return;          // a newer request has superseded this one
    rpRender(p);
    if (rpTimer) { clearTimeout(rpTimer); rpTimer = null; }
    // Poll only while a LIVE holder owns the lock. A stale lock reports
    // busy=false, so a dead run stops the poll instead of spinning forever.
    if (p.busy) rpTimer = setTimeout(function () { rpLoad(true); }, 4000);
  } catch (e) {
    // loadFailed writes a table row and this view has no table, so say it here.
    // An unchanged ladder would look like the run simply had not moved.
    if (!polling && mine === rpSeq) {
      document.querySelector('#rp-body').textContent =
        'Could not load run progress - ' + String((e && e.message) || e)
        + '. This is a display failure, not an empty result: retry, and check the '
        + 'server is still running.';
    }
  }
}
document.addEventListener('click', function (ev) {
  if (ev.target && ev.target.id === 'rp-go') rpLoad(false);
});
document.addEventListener('keydown', function (ev) {
  if (ev.key === 'Enter' && ev.target && ev.target.id === 'rp-key') rpLoad(false);
});
onEnter('progress', function () {
  const el = document.querySelector('#rp-key');
  if (el && el.value.trim()) rpLoad(false);
});

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

  <div data-view="progress">
    <section class="card">
      <div class="card-h"><div><h2>Run progress</h2>
        <div class="sub">Where a submitted request is right now, step by step — and
        when a step failed, what its exit code means plus the tail of that step's own
        log. Enter a PR key (PR-&lt;repo&gt;-&lt;number&gt;) or a ticket key. While a run
        is live this polls every few seconds; once it finishes the run record is the
        source of truth.</div></div></div>
      <div class="row" style="gap:8px;flex-wrap:wrap;margin-bottom:10px">
        <input id="rp-key" placeholder="PR-orders-api-201 or PROJ-301" style="min-width:260px">
        <button class="btn" id="rp-go">Track</button>
        <span id="rp-src" class="pill"></span>
      </div>
      <div id="rp-body" class="sub">Enter a key to trace a run.</div>
      <ol id="rp-steps" class="rp-steps"></ol>
      <div id="rp-fail"></div>
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
        <button class="btn btn-sm approve" id="approve-filtered"
          title="Approve every key currently visible and awaiting review — one confirmation, each decision still recorded individually on the board">
          Approve all shown</button>
        <span class="sub" style="margin-left:auto" id="run-count"></span></div>
      <div class="scroll"><table id="runs-table">
        <thead><tr><th>key / run</th><th>trigger</th><th>time</th><th>overall</th>
          <th title="Advisory test-quality score - never gates a commit">critic</th>
          <th>release</th><th style="min-width:280px">gate results per test repo</th>
          <th>team review</th></tr></thead>
        <tbody>{runs_rows}</tbody></table></div>
    </section>
  </div>

  <div data-view="specflow">
    <section class="card">
      <div class="card-h"><div><h2>How an E2E test gets built here</h2>
        <div class="sub">Six states, one owner each. The platform authors; a
        human decides. Every transition below is a command — this view never
        advances a workflow, it only shows you where things are.</div></div>
        <span class="grow"></span>
        <button class="btn btn-sm" id="sf-refresh">Refresh</button>
      </div>
      <div id="sf-gov" class="sub" style="padding:0 14px 10px"></div>
      <div class="scroll"><table id="sf-table">
        <thead><tr><th>ticket</th><th>state</th><th>what is blocking</th>
          <th>who</th><th>next step</th></tr></thead>
        <tbody></tbody></table></div>
    </section>

    <section class="card">
      <div class="card-h"><div><h2>Requirements</h2>
        <div class="sub">EARS statements formalized from the ticket, plus what
        the ticket does NOT say. Approving signs the file's hash — the cheapest
        place to fix a misunderstanding is here, before anything is generated.</div></div>
        <span class="grow"></span>
        <label class="f">Ticket <input id="rq-key" class="h32" placeholder="PROJ-301" style="width:130px"></label>
        <button class="btn btn-sm" id="rq-load">Load</button>
        <button class="btn btn-primary btn-sm" id="rq-approve">Approve</button>
      </div>
      <div id="rq-msg" class="sub" style="display:none;padding:0 14px 8px"></div>
      <div id="rq-body" style="padding:0 14px 14px"></div>
    </section>

    <section class="card">
      <div class="card-h"><div><h2>Waivers</h2>
        <div class="sub">An approved scenario shipping without a test. Every
        waiver needs a reason, an owner and an expiry — capped, so
        "temporarily" cannot quietly become "forever". Expired ones stay
        listed: a lapsed exception is the row worth reading.</div></div>
        <span class="grow"></span>
        <button class="btn btn-sm" id="wv-load">Load</button>
      </div>
      <div id="wv-msg" class="sub" style="display:none;padding:0 14px 8px"></div>
      <div style="padding:0 14px 12px" class="card-h">
        <label class="f">Scenario <input id="wv-sid" class="h32" placeholder="S-1" style="width:110px"></label>
        <label class="f">Reason <input id="wv-reason" class="h32" placeholder="why is this shipping uncovered?" style="min-width:260px"></label>
        <label class="f">Expires <input id="wv-exp" class="h32" type="date" style="width:150px"></label>
        <button class="btn btn-sm" id="wv-add">Add waiver</button>
      </div>
      <div class="scroll"><table id="wv-table">
        <thead><tr><th>scenario</th><th>reason</th><th>owner</th>
          <th>expires</th><th>state</th><th></th></tr></thead>
        <tbody></tbody></table></div>
    </section>

    <section class="card">
      <div class="card-h"><div><h2>Work this spec makes unnecessary</h2>
        <div class="sub">An approved scenario a cataloged test already exercises
        needs no authoring call. This counts them. It deliberately does NOT
        price them: putting money on it needs a measured authoring cost, and an
        invented figure is the one people repeat in a status update.</div></div>
        <span class="grow"></span>
        <button class="btn btn-sm" id="sv-load">Refresh</button>
      </div>
      <div id="sv-body" style="padding:0 14px 16px"></div>
    </section>

    <section class="card">
      <div class="card-h"><div><h2>The rules</h2>
        <div class="sub">Generated from <code>specs/platform/constitution.yaml</code>
        — each rule shows the test that holds it, so this page cannot drift from
        what the platform enforces.</div></div>
        <span class="grow"></span>
        <a class="btn btn-sm" href="/api/governance?format=md" download="governance.md">Download</a>
      </div>
      <div id="gv-body" style="padding:0 14px 8px"></div>
      <div style="padding:4px 14px 16px" class="sm">
        <p><b>1. A spec is signed, not just saved.</b> Approving binds to a
        content hash. Editing an approved plan revokes the approval — so
        "approved" always refers to the text somebody actually read.</p>
        <p><b>2. Prose a human wrote wins.</b> A free-form edit that diverges
        from the structured spec supersedes it; the old spec is kept for
        forensics, never silently discarded.</p>
        <p><b>3. An approved scenario is covered, waived, or refused.</b>
        Waivers carry a reason, an owner and an <b>expiry</b> — so
        "temporarily" cannot quietly become "forever".</p>
        <p><b>4. Enforcement rolls out in two steps.</b> <code>warn</code> until
        the signal is clean, then <code>strict</code>. Turning on strict first
        just teaches people to bypass the gate.</p>
        <p><b>5. The plan adversary is read-only.</b> It may only ADD scenarios.
        An opponent that can edit the plan is just a second author.</p>
        <p><b>6. Ambiguity stops the line.</b> A blocking ambiguity halts
        planning with a question on the ticket rather than a guess — the
        cheapest artifact to change is a sentence, not a committed test.</p>
      </div>
    </section>
  </div>

  <div data-view="alerts">
    <section class="card">
      <div class="card-h"><div><h2>Alert rules</h2>
        <div class="sub">A rule asks a question of the transaction log on the
        nightly tick: "did N matching events happen inside a window?" Firing is
        a state — a rule resolves when the condition clears, and a cooldown
        stops a flapping condition sending the same message repeatedly. A rule
        that cannot be evaluated reports <b>unevaluable</b>, never "ok".</div></div>
        <span class="grow"></span>
        <button class="btn btn-sm" id="al-add">Add rule</button>
        <button class="btn btn-primary btn-sm" id="al-save">Save rules</button>
      </div>
      <div id="al-msg" class="sub" style="display:none;padding:0 14px 8px"></div>
      <div class="scroll"><table id="al-table">
        <thead><tr><th>name</th><th>kinds</th><th>outcome</th><th>target has</th>
          <th>N</th><th>window (m)</th><th>cooldown (m)</th><th>channel</th>
          <th title="Comma-separated. Email/both rules deliver NOWHERE without this">to</th>
          <th title="One combined message per tick instead of one per rule">digest</th>
          <th>on</th><th>status</th><th></th></tr></thead>
        <tbody></tbody></table></div>
    </section>
  </div>

  <div data-view="activity">
    <section class="card">
      <div class="card-h"><div><h2>Transaction log</h2>
        <div class="sub">Every state-changing request and pipeline transaction —
        who did it, what happened, and how long it took. Browsing (GET) is not
        recorded; request bodies are never stored, and secret values are
        redacted at write time.</div></div>
        <span class="grow"></span>
        <a class="btn btn-sm" id="ev-csv" href="/api/events?format=csv&amp;limit=2000"
           download>Download CSV</a>
      </div>
      <div class="card-h">
        <label class="f">Kind <input id="ev-kind" class="h32" placeholder="gate.refused,plan.approved"></label>
        <label class="f">Actor <input id="ev-actor" class="h32" placeholder="anyone"></label>
        <label class="f">Target <input id="ev-target" class="h32" placeholder="PROJ-301"></label>
        <label class="f">Outcome <select id="ev-outcome" class="h32">
          <option value="">any</option><option>ok</option><option>refused</option>
          <option>failed</option><option>degraded</option></select></label>
        <button class="btn btn-sm" id="ev-refresh">Refresh</button>
      </div>
      <div id="ev-warn" class="sub" style="display:none;padding:0 14px 8px"></div>
      <div class="scroll"><table id="ev-table">
        <thead><tr><th>when</th><th>kind</th><th>actor</th><th>target</th>
          <th>outcome</th><th>ms</th><th>run</th></tr></thead>
        <tbody></tbody></table></div>
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

  <div data-view="cost">
    <section class="card">
      <div class="card-h"><div><h2>LLM spend</h2>
        <div class="sub">From the <code>spend</code> blocks every run records
        (harvested from the CLI's own usage report). Simulated figures — mock
        runs — are always labelled; they never masquerade as measured
        dollars.</div></div>
        <span class="grow"></span><span id="cost-badge"></span>
      </div>
      <div id="cost-summary" class="sm" style="padding:0 16px 12px"></div>
    </section>
    <section class="card">
      <div class="card-h"><div><h2>By provider</h2>
        <div class="sub">Which LLM ran the work, and how each cost figure was
        arrived at: <b>$</b> provider-reported · <b>~$</b> list-price estimate ·
        <b>$0 (local)</b> local inference, tokens still tracked ·
        <b>~</b> simulated. The four never cross.</div></div>
      </div>
      <div class="scroll"><table id="cost-provider-table">
        <thead><tr><th>provider</th><th>calls</th><th>cost</th><th>basis</th>
          <th>in tokens</th><th>out tokens</th></tr></thead>
        <tbody></tbody></table></div>
      <div id="cost-localsplit" class="sm muted" style="padding:0 16px 12px"></div>
    </section>
    <section class="card">
      <div class="card-h"><h2>By phase — turn calibration &amp; cache hit rate</h2></div>
      <div class="scroll"><table id="cost-phase-table">
        <thead><tr><th>phase</th><th>calls</th><th>cost</th><th>in tokens</th>
          <th>cache-read</th><th>hit rate</th><th>turns p50/p95</th>
          <th>ceiling</th><th>suggested</th></tr></thead>
        <tbody></tbody></table></div>
    </section>
    <section class="card">
      <div class="card-h"><h2>Top keys</h2></div>
      <div class="scroll"><table id="cost-keys-table">
        <thead><tr><th>key</th><th>runs</th><th>cost</th></tr></thead>
        <tbody></tbody></table></div>
      <div id="cost-savings" class="sm muted" style="padding:0 16px 12px"></div>
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
        <span id="plan-reused" class="hidden sm chip chip-warning"
          title="This draft was adapted mechanically from another key's approved plan (no model re-authored it). The VERIFY FOR THIS TICKET section lists what to re-check."></span>
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
        <div id="plan-spec" class="hidden" style="display:flex; flex-direction:column; gap:8px"></div>
        <ul id="plan-ambiguities" class="hidden"
          title="What the ticket's requirements left undefined (SDD 2.1) — the scenarios below had to route around these; consider resolving them on the ticket before approving."
          style="list-style:none; margin:0; padding:8px 12px; display:flex;
                 flex-direction:column; gap:6px; border:1px dashed var(--sr-border);
                 border-radius:8px"></ul>
        <div id="plan-similar" class="hidden sm"
          style="border:1px solid var(--sr-border); border-radius:8px;
                 padding:8px 12px"></div>
        <div id="plan-appdiff" class="hidden sm"
          style="border:1px solid var(--sr-warning-fg); border-radius:8px;
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
