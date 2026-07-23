"""The Artifacts view renders GENERATED TEST CODE, not just a raw diff
(bin/dashboard.py specs_from_diff + the artifact panel).

A reviewer wants to read the tests the pipeline wrote for a PR / story / plan. The
durable copy is the gate commit (reports/runs/<id>-<repo>.diff, a `git show`), so the
dashboard extracts each added file's content from it and shows clean per-file code.
"""
import importlib, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin"))


def _fn():
    # dashboard.py runs report generation at import; that is a harmless side effect
    # here and gives us the function under test.
    mod = importlib.import_module("dashboard")
    return mod.specs_from_diff


SAMPLE = """diff --git a/catalog/generated.jsonl b/catalog/generated.jsonl
--- a/catalog/generated.jsonl
+++ b/catalog/generated.jsonl
@@ -0,0 +1 @@
+{"test_id":"e2e::suites/x.spec.js::K","file":"suites/x.spec.js"}
diff --git a/suites/orders/K-boundary.spec.js b/suites/orders/K-boundary.spec.js
new file mode 100644
index 0000000..fb6f24f
--- /dev/null
+++ b/suites/orders/K-boundary.spec.js
@@ -0,0 +1,3 @@
+// K: boundary (AI-generated)
+const { test } = require('node:test');
+test('rejects', async () => {});
"""


def test_extracts_the_spec_code_not_the_diff_markers():
    specs = _fn()(SAMPLE)
    spec = next(s for s in specs if s["path"] == "suites/orders/K-boundary.spec.js")
    assert spec["new"] is True
    assert spec["is_catalog"] is False
    assert spec["lang"] == "javascript"
    # the added lines are the whole file, with no leading '+' and no @@/diff noise
    assert spec["code"] == ("// K: boundary (AI-generated)\n"
                            "const { test } = require('node:test');\n"
                            "test('rejects', async () => {});")
    assert "+" not in spec["code"].split("\n")[0]
    assert "@@" not in spec["code"]


def test_catalog_sidecar_is_flagged_separately():
    specs = _fn()(SAMPLE)
    cat = [s for s in specs if s["is_catalog"]]
    assert len(cat) == 1 and cat[0]["path"] == "catalog/generated.jsonl"


def test_modified_file_reports_added_and_removed_counts():
    diff = ("diff --git a/suites/y.spec.ts b/suites/y.spec.ts\n"
            "--- a/suites/y.spec.ts\n+++ b/suites/y.spec.ts\n"
            "@@ -1,2 +1,3 @@\n const a = 1;\n-const old = 2;\n+const b = 2;\n+const c = 3;\n")
    spec = next(s for s in _fn()(diff) if s["path"] == "suites/y.spec.ts")
    assert spec["new"] is False
    assert len(spec["added"]) == 2 and spec["removed"] == 1
    assert spec["lang"] == "typescript"


def test_empty_or_garbage_diff_yields_no_specs():
    fn = _fn()
    assert fn("") == []
    assert fn("not a diff at all\njust text") == []


def test_artifact_panel_renders_a_code_block_and_keeps_the_raw_diff():
    html = (ROOT / "reports/dashboard.html")
    if not html.exists():
        importlib.import_module("dashboard")            # regenerate
    text = html.read_text(encoding="utf-8")
    # only meaningful when at least one committed run exists
    if "Generated test code" in text:
        assert 'class="spec-file"' in text, "code not rendered as clean per-file blocks"
        assert "Raw commit diff" in text, "raw diff must remain available"
