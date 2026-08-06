"""The Python suite must produce line/branch coverage and enforce its baseline."""
import pathlib
import re


ROOT = pathlib.Path(__file__).resolve().parents[2]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


def test_pytest_coverage_plugin_is_a_declared_dependency():
    assert re.search(r"^pytest-cov(?:[<>=!~].*)?$", _read("requirements.txt"), re.M)


def test_coverage_config_enables_branch_measurement_for_runtime_python():
    config = _read(".coveragerc")
    assert re.search(r"^branch\s*=\s*True$", config, re.M)
    assert re.search(r"^\s+engine/lib$", config, re.M)
    assert re.search(r"^\s+bin$", config, re.M)


def test_full_review_enforces_a_nonzero_coverage_floor():
    makefile = _read("Makefile")
    floor = re.search(r"^PY_COVERAGE_MIN\s*\?=\s*(\d+)$", makefile, re.M)
    assert floor and int(floor.group(1)) > 0
    review = re.search(r"^review:\n(.*?)(?=\n[a-z])", makefile, re.S | re.M)
    assert review and "$(MAKE) python-coverage-check" in review.group(1)
    assert "--cov-branch" in makefile
