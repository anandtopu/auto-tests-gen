SHELL := /bin/bash
.PHONY: deps test-routing test-unit python-coverage python-coverage-check python-coverage-html bootstrap run-pr run-jira eval discovery-eval retrieval-eval reviewer-eval reviewer-eval-real conformance \
        status coverage dashboard review-queue reviews repos agents parity-pr parity-jira \
        serve queue-run export-plan publish-plan attach-plan hook-server prune \
        gaps catalog-db ingest-results smoke-openhands clear-demo report critic \
        test-gate demo-bootstrap demo-pr demo-jira review \
        docker-build deploy-local deploy-local-down deploy-openshift email \
        plan plan-show plan-approve plan-changes plan-edit plan-link plan-tests plans \
        demo-plan demo-plan-tests sync-guidance sync-status check-integrations skills repo-agents config \
        cost-report cost-statement cost-reconcile index-rebuild index-stats cache-probe cost-baseline \
        requirements demo-requirements requirements-approve spec-verify \
        test-providers test-state test-routing-adv test-observability test-bootstrap test-entrypoint \
        parity-compare repo-facts explain select select-finalize

deps:
	pip install --break-system-packages -r requirements.txt

test-routing:
	python3 -m pytest registry/tests -q

PY_COVERAGE_MIN ?= 67

test-unit:
	python3 -m pytest registry/tests -q

python-coverage:
	python3 -m pytest registry/tests -q --cov=engine/lib --cov=bin --cov-branch --cov-report=term-missing --cov-report=xml:reports/coverage.xml

python-coverage-check:
	python3 -m pytest registry/tests -q --cov=engine/lib --cov=bin --cov-branch --cov-report=term-missing --cov-report=xml:reports/coverage.xml --cov-fail-under=$(PY_COVERAGE_MIN)

python-coverage-html:
	python3 -m pytest registry/tests -q --cov=engine/lib --cov=bin --cov-branch --cov-report=term-missing --cov-report=html

bootstrap:
	bash catalog/bootstrap/run_bootstrap.sh $(REPO)

run-pr:
	bash engine/pipeline.sh pr $(REPO) $(PR)

run-jira:
	bash engine/pipeline.sh jira $(KEY)

eval:
	bash eval/replay.sh && python3 eval/context_check.py && python3 eval/discovery_quality.py && python3 eval/retrieval_quality.py && python3 eval/reviewer_quality.py && python3 eval/token_cost_coverage.py && python3 eval/scorecard.py

discovery-eval:
	python3 eval/discovery_quality.py

retrieval-eval:
	python3 eval/retrieval_quality.py

reviewer-eval:
	python3 eval/reviewer_quality.py

# Explicit and potentially billable. Never part of `eval`/`review`: it invokes
# the configured real reviewer provider and requires the same auth as parity.
reviewer-eval-real:
	python3 eval/reviewer_quality.py --real

conformance:
	bash adapters/conformance/test_adapters.sh

test-gate:
	bash tests/gate-adversarial.sh

# Adversarial UAT for the LLM Runner port (multi-LLM 5.3)
test-providers:
	bash tests/provider-adversarial.sh

repo-facts:           # harvested facts: E2E repos + opted-in app repos
	python3 engine/lib/repo_facts.py rebuild $(REPO)

# Adversarial UAT for the STATE layer (review pass C)
test-state:
	bash tests/state-adversarial.sh

# Adversarial UAT for ATTRIBUTION + ROUTING (correlator -> covers: -> resolver)
test-routing-adv:
	bash tests/routing-adversarial.sh

# Adversarial UAT for the OBSERVABILITY stack (log, activity, alerts, delivery)
test-observability:
	bash tests/observability-adversarial.sh

# The catalog has ONE definition of where it lives (app_paths.catalog_files).
# CLAUDE.md listed this among the runnable commands and the target did not
# exist, so anyone following the docs got "No rule to make target".
test-catalog-paths:
	python3 -m pytest registry/tests/test_catalog_paths.py -q

# Smoke + adversarial UAT for the CATALOG BOOTSTRAP CHAIN. Runs the REAL
# catalog/bootstrap/run_bootstrap.sh (isolated via AIQE_CATALOG_DIR), not the
# demo reimplementation — the two had silently diverged because only the demo
# copy was ever executed.
test-bootstrap:
	bash tests/bootstrap-smoke.sh

# Adversarial UAT for FIRST-BOOT STATE SEEDING (R12). The entrypoint decides
# whether a new deployment has an estate at all; it was the only entry point in
# the repo that nothing referenced, and it seeded nothing while reporting
# success.
test-entrypoint:
	bash tests/entrypoint-smoke.sh

demo-bootstrap:
	bash bin/demo-bootstrap.sh e2e-api-tests-1 && bash bin/demo-bootstrap.sh e2e-ui-tests-1

demo-pr:
	AIQE_MOCK=1 bash engine/pipeline.sh pr orders-api 201

demo-jira:
	AIQE_MOCK=1 bash engine/pipeline.sh jira PROJ-301

# Real-LLM parity: real phases, demo estate + mock adapters (REVIEW.md item 1).
# LLM_PROVIDER=ollama|codex|claude routes the phases at a provider (multi-LLM
# 2.5) so the SAME four quality claims can be measured per provider before
# anyone trusts a cheaper model with judgement work. Empty = org-config default.
parity-pr:
	AIQE_MOCK=1 AIQE_REAL_LLM=1 AIQE_LLM_PROVIDER=$(LLM_PROVIDER) bash engine/pipeline.sh pr orders-api 201

parity-jira:
	AIQE_MOCK=1 AIQE_REAL_LLM=1 AIQE_LLM_PROVIDER=$(LLM_PROVIDER) bash engine/pipeline.sh jira PROJ-301

# Compare parity runs ACROSS providers (multi-LLM 2.5): commit rate, critic
# score, spend and turns per provider, from the run records they wrote.
parity-compare:
	python3 engine/lib/parity_compare.py $(DAYS)

review:
	$(MAKE) python-coverage-check && bash adapters/conformance/test_adapters.sh && bash tests/gate-adversarial.sh && bash tests/provider-adversarial.sh && bash tests/state-adversarial.sh && bash tests/routing-adversarial.sh && bash tests/observability-adversarial.sh && bash tests/bootstrap-smoke.sh && bash tests/entrypoint-smoke.sh && bash eval/replay.sh && python3 eval/context_check.py && python3 eval/discovery_quality.py && python3 eval/retrieval_quality.py && python3 eval/reviewer_quality.py && python3 eval/scorecard.py

# --- QA monitoring & mapping management ---
status:
	python3 bin/qa.py status

coverage:
	python3 bin/qa.py coverage

review-queue:
	python3 bin/qa.py review

reviews:
	python3 bin/qa.py reviews

dashboard:
	python3 bin/dashboard.py

serve:
	python3 bin/dashboard_server.py

hook-server:
	python3 bin/taskevent_receiver.py

config:               # which properties file is loaded + which keys it sets (names only)
	python3 engine/lib/props_file.py
	@python3 engine/lib/org_config_check.py

check-integrations:   # read-only connectivity check for every configured system
	python3 engine/lib/integration_check.py $(WHICH)

smoke-openhands:
	bash bin/smoke-openhands.sh

select:               # selective review: show/approve individual scenarios + tests (KEY=..)
	python3 engine/lib/selection.py $(KEY)

select-finalize:      # emit the approved artifacts from the SELECTED items (KEY=..)
	python3 engine/lib/selection.py $(KEY) finalize

explain:              # why the AI did what it did for a request (KEY=.. [JSON=1])
	python3 engine/lib/explain.py $(KEY) $(if $(JSON),--json,)

trace-matrix:         # requirement traceability: key -> scenario -> spec -> gate -> CI ([KEY=..] [CSV=1])
	python3 engine/lib/trace_matrix.py $(KEY) $(if $(CSV),--csv,)

spec-savings:         # work a signed spec makes unnecessary: covered vs to-author ([KEY=..])
	python3 engine/lib/spec_savings.py $(KEY)

RETAIN_DAYS ?= $(shell python3 -c "import yaml;print((yaml.safe_load(open('registry/org-config.yaml')) or {}).get('observability',{}).get('retain_days',30))" 2>/dev/null || echo 30)

maintain:             # nightly estate maintenance (call from cron / a K8s CronJob):
	@# Steps stay best-effort and INDEPENDENT — a network blip in guidance sync
	@# must not skip the backup that runs after it. What the `-` prefixes used to
	@# hide is which ones failed: measured, two sabotaged steps (including the
	@# state-bundle snapshot) printed "maintenance complete" and exited 0, which a
	@# CronJob reads as success. The runner keeps the tolerance and adds the report.
	python3 engine/lib/maintenance.py $(RETAIN_DAYS)

state-export:         # portable bundle of all durable state -> reports/exports/
	python3 engine/lib/state_bundle.py export $(OUT)
state-inspect:        # manifest + checksum verification, no writes (BUNDLE=path)
	python3 engine/lib/state_bundle.py inspect $(BUNDLE)
state-import:         # restore a bundle here (BUNDLE=path [REPLACE=1] [DRY=1])
	python3 engine/lib/state_bundle.py import $(BUNDLE) $(if $(REPLACE),--replace,) $(if $(DRY),--dry-run,)

cost-report:          # LLM spend attribution: by workflow/key/phase/model + turn calibration (DAYS=N)
	python3 engine/lib/cost_report.py report $(if $(DAYS),--days $(DAYS),)

cost-statement:       # exact-key auditable spend lines (KEY=... FORMAT=md|csv)
	python3 engine/lib/cost_statement.py $(KEY) --format $(or $(FORMAT),md)

cost-baseline:        # freeze per-phase MEASURED medians as the regression baseline (refuses simulated)
	python3 engine/lib/cost_report.py baseline

cost-reconcile:       # provider-reported cost through adapter port (DAYS=N [PROVIDER=name])
	python3 engine/lib/cost_reconcile.py --days $(or $(DAYS),30) $(if $(PROVIDER),--provider $(PROVIDER),)

cache-probe:          # measure whether provider prompt caching engages (real CLI auth; ~$$0.02)
	bash bin/cache-probe.sh

index-rebuild:        # force-rebuild the semantic vector index from the knowledge chunks
	python3 engine/lib/knowledge_chunks.py rebuild
	python3 engine/lib/vector_index.py rebuild

index-stats:          # testcase parse coverage by registered E2E repository
	python3 engine/lib/knowledge_chunks.py index-stats

batch-spool:          # queue one request for the next batch (KEY=.. PHASE=.. MODEL=.. FILE=..)
	python3 engine/lib/batch_spool.py add "$(KEY)" "$(PHASE)" "$(MODEL)" "$(FILE)"
batch-pending:        # what is queued but not yet sent
	python3 engine/lib/batch_spool.py pending
batch-submit:         # send everything queued as ONE batch (50% off, async)
	python3 engine/lib/batch_spool.py submit
batch-status:         # per in-flight batch: what the API says (never a guess)
	python3 engine/lib/batch_spool.py status
batch-drain:          # retrieve ended batches; every request gets an outcome
	python3 engine/lib/batch_spool.py drain
cache-stats:          # phase-cache hit report (LLM calls avoided)
	python3 engine/lib/phase_cache.py stats
cache-clear:          # drop every cached phase result
	python3 engine/lib/phase_cache.py clear

prune:
	python3 bin/qa.py prune --keep $(or $(KEEP),200)

clear-demo:
	python3 engine/lib/demo_data.py $(if $(DRY),--dry,)

# Container image. Engine auto-detected at run time: docker, else podman (same
# Dockerfile — podman consumes it unchanged), else podman inside the default WSL
# machine (covers a Windows box where the podman.exe shim isn't installed).
# Override explicitly with ENGINE=..., e.g.:
#   make docker-build ENGINE=podman
#   make docker-build ENGINE="wsl -d podman-machine-default podman"
docker-build:
	@set -e; ENG="$(ENGINE)"; \
	if [ -z "$$ENG" ]; then \
	  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then ENG=docker; \
	  elif command -v podman >/dev/null 2>&1; then ENG=podman; \
	  elif wsl -d podman-machine-default podman --version >/dev/null 2>&1; then ENG="wsl -d podman-machine-default podman"; \
	  elif command -v docker >/dev/null 2>&1; then ENG=docker; \
	  else echo "ERROR: no container engine found (docker or podman; or set ENGINE=...)" >&2; exit 1; fi; \
	fi; \
	echo "[docker-build] engine: $$ENG"; \
	$$ENG build -t $(or $(IMAGE),ai-qe-platform:local) $(if $(REAL),--build-arg INSTALL_REAL_TOOLS=1,) .

deploy-local:
	bash deploy/local/deploy.sh $(if $(SEED),--seed,)

deploy-local-down:
	bash deploy/local/deploy.sh --down

deploy-openshift:
	bash deploy/openshift/deploy.sh $(if $(NS),-n $(NS),)

report:
	python3 bin/qa.py report $(if $(DAYS),--days $(DAYS),) $(if $(RELEASE),--release $(RELEASE),) $(if $(FORMAT),--format $(FORMAT),)

email:
	python3 bin/qa.py email $(or $(KIND),report) $(RUN_ID) $(if $(DAYS),--days $(DAYS),) $(if $(RELEASE),--release $(RELEASE),) $(if $(TO),--to $(TO),)

# --- JIRA test-plan workflow: author -> review/edit -> approve -> link -> generate ---
# plan/plan-tests are real runs (like run-jira); demo-plan/demo-plan-tests use mocks.
spec-verify:          # SDD 4.2: read-only - are a (stale) spec's EXISTING tests still passing?
	python3 engine/lib/spec_verify.py $(KEY)

requirements:         # SDD 2.2: formalize EARS requirements, then stop for human validation
	bash engine/pipeline.sh requirements $(KEY)
demo-requirements:
	AIQE_MOCK=1 bash engine/pipeline.sh requirements $(or $(KEY),PROJ-301)
requirements-approve: # validate the requirements spec (signs its hash)
	python3 engine/lib/plan_state.py requirements-set $(KEY) approved $(or $(BY),)

plan:                 # author the plan only, then stop for human review
	bash engine/pipeline.sh plan $(KEY)
demo-plan:
	AIQE_MOCK=1 bash engine/pipeline.sh plan $(or $(KEY),PROJ-301)
demo-plan-tests:
	AIQE_MOCK=1 bash engine/pipeline.sh tests $(or $(KEY),PROJ-301)
plans:                # list every plan and its status
	python3 bin/qa.py plan list
plan-show:
	python3 bin/qa.py plan show $(KEY)
plan-edit:            # FILE=<edited.md>
	python3 bin/qa.py plan edit $(KEY) --file $(FILE) $(if $(BY),--by $(BY),)
plan-approve:
	python3 bin/qa.py plan approve $(KEY) $(if $(BY),--by $(BY),) $(if $(NOTE),--note "$(NOTE)",)
plan-changes:         # request changes: NOTE="..."
	python3 bin/qa.py plan request-changes $(KEY) $(if $(BY),--by $(BY),) $(if $(NOTE),--note "$(NOTE)",)
plan-link:            # attach the approved plan to the JIRA ticket
	python3 bin/qa.py plan link $(KEY) $(if $(FORMAT),--format $(FORMAT),)
plan-tests:           # generate E2E tests from the APPROVED plan
	bash engine/pipeline.sh tests $(KEY)

gaps:
	python3 bin/qa.py gaps

critic:               # advisory test-quality scores (never gates a commit)
	python3 bin/qa.py critic $(if $(FINDINGS),--findings,)

catalog-db:
	python3 catalog/bootstrap/index_db.py

ingest-results:
	python3 bin/qa.py ingest-results $(FILE)

queue-run:
	python3 engine/lib/work_queue.py run

export-plan:
	python3 bin/qa.py export-plan $(KEY) --format $(or $(FORMAT),md)

publish-plan:
	python3 bin/qa.py publish-plan $(KEY)

attach-plan:
	python3 bin/qa.py attach-plan $(KEY) --format $(or $(FORMAT),pdf)

# --- repo configuration & estate knowledge ---
repos:
	python3 bin/repos.py list

agents:
	python3 bin/gen_agents_md.py
	python3 bin/gen_path_skills.py

skills:               # path-triggered OpenHands skills (UI/API split) from the registry
	python3 bin/gen_path_skills.py

sync-guidance:        # pull AGENTS.md/CLAUDE.md from the SCM (REPO=... for one repo)
	python3 bin/repos.py sync $(REPO) $(if $(REF),--ref $(REF),)
sync-status:
	python3 bin/repos.py sync-status

repo-agents:          # generate AGENTS.md for repos that ship none (REPO=... or all)
	python3 bin/repos.py gen-guidance $(REPO) $(if $(FORCE),--force,)
