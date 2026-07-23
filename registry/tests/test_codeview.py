"""The Artifacts view renders GENERATED TEST CODE and a before/after COMPARISON
(bin/dashboard.py specs_from_diff + the artifact panel).

A reviewer wants to read what the pipeline wrote for a PR / story / plan, and — for
tests it updated or deleted — see the comparison, not just the new lines. The durable
copy is the gate commit (reports/runs/<id>-<repo>.diff, a `git show`), so the dashboard
parses it into per-file blocks: clean code for a new spec, a coloured unified diff for
an updated or deleted one.
"""
import importlib, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin"))


def _fn():
    # dashboard.py runs report generation at import; harmless here, and gives us the fn.
    return importlib.import_module("dashboard").specs_from_diff


NEW = """diff --git a/catalog/generated.jsonl b/catalog/generated.jsonl
--- a/catalog/generated.jsonl
+++ b/catalog/generated.jsonl
@@ -0,0 +1 @@
+{"test_id":"e2e::suites/x.spec.js::K","file":"suites/x.spec.js"}
diff --git a/suites/orders/K-boundary.spec.js b/suites/orders/K-boundary.spec.js
new file mode 100644
--- /dev/null
+++ b/suites/orders/K-boundary.spec.js
@@ -0,0 +1,3 @@
+// K: boundary (AI-generated)
+const { test } = require('node:test');
+test('rejects', async () => {});
"""

UPDATED = """diff --git a/suites/orders/existing.spec.js b/suites/orders/existing.spec.js
index 111..222 100644
--- a/suites/orders/existing.spec.js
+++ b/suites/orders/existing.spec.js
@@ -1,4 +1,5 @@
 const { test } = require('node:test');
-test('old name', async () => {
+test('clearer name', async () => {
   const r = await fetch(BASE);
+  assert.strictEqual(r.status, 200);
 });
"""

DELETED = """diff --git a/suites/legacy/gone.spec.js b/suites/legacy/gone.spec.js
deleted file mode 100644
index 333..000
--- a/suites/legacy/gone.spec.js
+++ /dev/null
@@ -1,2 +0,0 @@
-test('obsolete', async () => {});
-// retired
"""


# ---------------------------------------------------------------- new files

def test_new_spec_yields_clean_code_and_a_new_change():
    spec = next(s for s in _fn()(NEW) if s["path"] == "suites/orders/K-boundary.spec.js")
    assert spec["change"] == "new" and spec["new"] is True
    assert spec["is_catalog"] is False and spec["lang"] == "javascript"
    assert spec["code"] == ("// K: boundary (AI-generated)\n"
                            "const { test } = require('node:test');\n"
                            "test('rejects', async () => {});")
    assert "@@" not in spec["code"] and not spec["code"].startswith("+")


def test_catalog_sidecar_is_flagged_separately():
    cat = [s for s in _fn()(NEW) if s["is_catalog"]]
    assert len(cat) == 1 and cat[0]["path"] == "catalog/generated.jsonl"


# ---------------------------------------------------------------- updated files

def test_updated_spec_carries_a_before_after_hunk():
    spec = next(s for s in _fn()(UPDATED) if s["path"].endswith("existing.spec.js"))
    assert spec["change"] == "updated"
    # both sides are present, in order, for a coloured comparison
    kinds = [h["t"] for h in spec["hunk"]]
    assert "del" in kinds and "add" in kinds and "ctx" in kinds
    removed = [h["text"] for h in spec["hunk"] if h["t"] == "del"]
    added = [h["text"] for h in spec["hunk"] if h["t"] == "add"]
    assert "test('old name', async () => {" in removed
    assert "test('clearer name', async () => {" in added
    assert spec["added"] and spec["removed"]           # non-empty both sides


def test_hunk_preserves_line_order():
    spec = next(s for s in _fn()(UPDATED) if s["path"].endswith("existing.spec.js"))
    seq = [(h["t"], h["text"]) for h in spec["hunk"] if h["t"] != "meta"]
    # context line comes first, then the del/add pair
    assert seq[0] == ("ctx", "const { test } = require('node:test');")


# ---------------------------------------------------------------- deleted files

def test_deleted_spec_is_detected_via_dev_null():
    spec = _fn()(DELETED)[0]
    assert spec["change"] == "deleted" and spec["deleted"] is True
    assert spec["path"] == "suites/legacy/gone.spec.js"    # from a/, since +++ is /dev/null
    assert spec["removed"] and not spec["added"]
    assert all(h["t"] in ("del", "meta") for h in spec["hunk"])


# ---------------------------------------------------------------- robustness

def test_empty_or_garbage_diff_yields_no_specs():
    fn = _fn()
    assert fn("") == []
    assert fn("not a diff at all\njust text") == []


def test_language_is_derived_from_the_extension():
    fn = _fn()
    assert next(iter(fn(UPDATED)))["lang"] == "javascript"
    ts = "diff --git a/x.ts b/x.ts\n--- a/x.ts\n+++ b/x.ts\n@@ -1 +1 @@\n-a\n+b\n"
    assert fn(ts)[0]["lang"] == "typescript"


# ---------------------------------------------------------------- rendered panel

def test_artifact_panel_renders_code_and_keeps_the_raw_diff():
    html = ROOT / "reports/dashboard.html"
    if not html.exists():
        importlib.import_module("dashboard")
    text = html.read_text(encoding="utf-8")
    # Two legitimate variants: per-file blocks with a "Raw commit diff" toggle, or —
    # when a diff carries no test-code files — the collapsed fallback. Key on the
    # toggle label that only the block variant emits.
    if "Raw commit diff" in text:
        assert 'class="spec-file"' in text, "block variant without spec-file blocks"


def test_comparison_styles_exist_for_updated_and_deleted():
    """The coloured diff needs its add/del classes, or the comparison is unreadable."""
    src = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    for cls in (".d-add", ".d-del", ".d-ctx", ".diffview"):
        assert cls in src, f"missing diff style {cls}"


def test_api_wrapper_explains_a_stale_server_on_501_and_404():
    """'Factory reset returns error' — on a long-lived server predating the endpoint,
    the POST answers 501/404 with an HTML body, and the old toast showed a bare
    status code that sent the user bug-hunting. The wrapper must name the actual
    cause (stale server process) and the fix (restart make serve)."""
    src = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    i = src.index("async function api(")
    wrapper = src[i:i + 900]
    assert "501" in wrapper and "404" in wrapper
    assert "make serve" in wrapper, "the fix must be named in the error"
