"""Registry loader shared by resolver, catalog, and eval."""
import yaml, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]

def load_registry():
    return yaml.safe_load((ROOT / "registry/repo-registry.yaml").read_text())

def load_org_config():
    return yaml.safe_load((ROOT / "registry/org-config.yaml").read_text())

def source_repo(reg, name):
    return next((r for r in reg["source_repositories"] if r["name"] == name), None)

def test_repos_for(reg, source_name, layers=None):
    """Coverage lookup: which test repos cover a source repo (catalog-generated)."""
    out = []
    for t in reg["test_repositories"]:
        if source_name in t.get("covers", []):
            if layers is None or t["layer"] in layers:
                out.append(t["name"])
    return out
