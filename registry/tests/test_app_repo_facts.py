"""B4: opt-in structured facts for application repositories."""
import json
import pathlib

import repo_facts as rf
import repo_guidance_gen as rgg


def _registry():
    return {
        "source_repositories": [
            {"name": "orders-api", "type": "backend", "scm": "github",
             "url": "org/orders-api", "domains": ["orders", "checkout"],
             "contract": "openapi/orders.yaml", "testable_paths": ["app/**"],
             "consumed_by": ["store-ui"]},
            {"name": "store-ui", "type": "frontend", "scm": "github",
             "url": "org/store-ui", "domains": ["checkout"],
             "route_table": "src/routes.tsx", "consumes_services": ["orders-api"]},
        ],
        "test_repositories": [
            {"name": "api-e2e", "layer": "api", "framework": "playwright-api",
             "scm": "github", "url": "org/api-e2e", "layout": {"specs": "suites/"},
             "covers": ["orders-api"]},
        ],
    }


def _estate(tmp_path, monkeypatch, app="orders-api"):
    facts = tmp_path / "knowledge/facts"
    monkeypatch.setattr(rf, "ROOT", tmp_path)
    monkeypatch.setattr(rf, "FACTS_DIR", facts)
    monkeypatch.setattr(rf, "DERIVED_DIR", facts / "derived")
    monkeypatch.delenv("AIQE_STATE_DIR", raising=False)
    monkeypatch.delenv("AIQE_CATALOG_DIR", raising=False)
    facts.mkdir(parents=True)
    (facts / f"{app}.yaml").write_text(
        f"repo: {app}\nschema: 1\nauthored:\n"
        "  conventions:\n"
        "    - id: use-public-contract\n"
        "      rule: Test only the versioned public contract.\n"
        "      severity: must\n",
        encoding="utf-8",
    )
    return _registry(), facts


def _catalog(root, rows):
    path = root / "catalog/catalog.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows),
                    encoding="utf-8")


def test_application_repositories_require_an_authored_opt_in(tmp_path, monkeypatch):
    reg = _registry()
    monkeypatch.setattr(rf, "FACTS_DIR", tmp_path / "facts")
    assert rf.is_app_repo("orders-api", reg)
    assert not rf.app_opted_in("orders-api", reg)
    assert rf.build_harvested("orders-api", reg) == {}
    assert rf.facts_repo_names(reg) == ["api-e2e"]


def test_backend_harvest_is_deterministic_and_evidence_based(tmp_path, monkeypatch):
    reg, _ = _estate(tmp_path, monkeypatch)
    contract = tmp_path / "workspace/src/orders-api/openapi/orders.yaml"
    contract.parent.mkdir(parents=True)
    contract.write_text("paths:\n  /v1/orders/{id}:\n  /v1/orders:\n", encoding="utf-8")
    _catalog(tmp_path, [{
        "test_id": "api-e2e::orders",
        "mapping": {"app_repos": ["orders-api"], "status": "confirmed"},
        "evidence": {"endpoints": ["GET /v1/orders/{id}"], "ui_routes": []},
    }])

    first = rf.build_harvested("orders-api", reg)
    second = rf.build_harvested("orders-api", reg)
    assert first == second
    assert "generated_at" not in first
    assert first["repo_kind"] == "application"
    assert first["surface"] == {
        "kind": "endpoints", "input": "contract",
        "configured_path": "openapi/orders.yaml", "status": "available",
        "source": "workspace/src/orders-api/openapi/orders.yaml",
        "items": ["/v1/orders", "/v1/orders/{id}"],
    }
    assert first["catalog"]["mapped_tests"] == ["api-e2e::orders"]
    assert first["catalog"]["surface_covered"] == ["GET /v1/orders/{id}"]
    assert first["covering_test_repositories"] == ["api-e2e"]


def test_unavailable_surface_is_not_reported_as_an_empty_surface(tmp_path, monkeypatch):
    reg, _ = _estate(tmp_path, monkeypatch)
    missing = rf.build_harvested("orders-api", reg)["surface"]
    assert missing["status"] == "unavailable" and missing["items"] == []

    contract = tmp_path / "demo/orders-api/openapi/orders.yaml"
    contract.parent.mkdir(parents=True)
    contract.write_text("paths: {}\n", encoding="utf-8")
    empty = rf.build_harvested("orders-api", reg)["surface"]
    assert empty["status"] == "available" and empty["items"] == []

    reg["source_repositories"][0].pop("contract")
    unconfigured = rf.build_harvested("orders-api", reg)["surface"]
    assert unconfigured["status"] == "not_configured"


def test_frontend_routes_use_the_same_common_schema(tmp_path, monkeypatch):
    reg, _ = _estate(tmp_path, monkeypatch, app="store-ui")
    routes = tmp_path / "workspace/src/store-ui/src/routes.tsx"
    routes.parent.mkdir(parents=True)
    routes.write_text("{ path: '/checkout' },\n{ path: '/cart' },\n", encoding="utf-8")
    harvested = rf.build_harvested("store-ui", reg)
    assert harvested["repo_kind"] == "application"
    assert harvested["surface"]["kind"] == "routes"
    assert harvested["surface"]["items"] == ["/cart", "/checkout"]


def test_rebuild_is_byte_deterministic_and_never_rewrites_authored(tmp_path, monkeypatch):
    reg, facts = _estate(tmp_path, monkeypatch)
    authored_before = (facts / "orders-api.yaml").read_bytes()
    assert rf.rebuild(["orders-api"], reg) == ["orders-api"]
    derived = facts / "derived/orders-api.yaml"
    first = derived.read_bytes()
    assert rf.rebuild(["orders-api"], reg) == ["orders-api"]
    assert derived.read_bytes() == first
    assert (facts / "orders-api.yaml").read_bytes() == authored_before


def test_existing_guidance_generator_merges_opted_in_facts(tmp_path, monkeypatch):
    reg, _ = _estate(tmp_path, monkeypatch)
    contract = tmp_path / "demo/orders-api/openapi/orders.yaml"
    contract.parent.mkdir(parents=True)
    contract.write_text("paths:\n  /v1/orders:\n", encoding="utf-8")
    monkeypatch.setattr(rgg, "_catalog", lambda: [])
    text = "\n".join(rgg._render_app(reg["source_repositories"][0], reg))
    assert "Team-authored structured facts" in text
    assert "[must] use-public-contract" in text
    assert "`/v1/orders`" in text


def test_no_opt_in_keeps_the_existing_guidance_path(tmp_path, monkeypatch):
    reg = _registry()
    monkeypatch.setattr(rf, "FACTS_DIR", tmp_path / "facts")
    monkeypatch.setattr(rgg, "_catalog", lambda: [])
    monkeypatch.setattr(rgg, "_harvest", lambda entry: ["/legacy-path"])
    text = "\n".join(rgg._render_app(reg["source_repositories"][0], reg))
    assert "Team-authored structured facts" not in text
    assert "`/legacy-path`" in text


def test_opted_in_facts_refresh_only_the_existing_generated_path(tmp_path, monkeypatch):
    reg, _ = _estate(tmp_path, monkeypatch)
    generated = tmp_path / "generated"
    monkeypatch.setattr(rgg, "GEN_DIR", generated)
    monkeypatch.setattr(rgg, "_catalog", lambda: [])
    monkeypatch.setattr(rgg, "has_real_guidance", lambda name: False)
    dest = generated / "orders-api/AGENTS.md"
    dest.parent.mkdir(parents=True)
    dest.write_text("stale\n", encoding="utf-8")
    result = rgg.ensure("orders-api", reg=reg)
    assert result["status"] == "written"
    assert "use-public-contract" in dest.read_text(encoding="utf-8")

    monkeypatch.setattr(rgg, "has_real_guidance", lambda name: True)
    assert rgg.ensure("orders-api", reg=reg)["status"] == "skipped_has_own"
    monkeypatch.setattr(rgg, "has_real_guidance", lambda name: False)

    # Removing the authored file restores the old create-once behavior.
    (rf.FACTS_DIR / "orders-api.yaml").unlink()
    dest.write_text("keep-existing\n", encoding="utf-8")
    result = rgg.ensure("orders-api", reg=reg)
    assert result["status"] == "skipped_exists"
    assert dest.read_text(encoding="utf-8") == "keep-existing\n"


def test_catalog_unavailable_and_available_empty_are_distinct(tmp_path, monkeypatch):
    reg, _ = _estate(tmp_path, monkeypatch)
    assert rf.build_harvested("orders-api", reg)["catalog"]["status"] == "unavailable"
    _catalog(tmp_path, [])
    catalog = rf.build_harvested("orders-api", reg)["catalog"]
    assert catalog["status"] == "available"
    assert catalog["source_count"] == 1 and catalog["mapped_tests"] == []


def test_harvester_has_no_model_or_subprocess_dependency():
    source = pathlib.Path(rf.__file__).read_text(encoding="utf-8")
    assert "adapters.llm" not in source
    assert "subprocess" not in source


def test_malformed_authored_tier_cannot_break_guidance(tmp_path, monkeypatch):
    reg, facts = _estate(tmp_path, monkeypatch)
    (facts / "orders-api.yaml").write_text(
        "repo: orders-api\nschema: 1\nauthored: [not, a, mapping]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(rgg, "_catalog", lambda: [])
    text = "\n".join(rgg._render_app(reg["source_repositories"][0], reg))
    assert "What this repository is" in text
    assert rf.authored("orders-api") == {}
