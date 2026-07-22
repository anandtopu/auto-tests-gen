SHELL := /bin/bash
.PHONY: deps test-routing bootstrap run-pr run-jira eval conformance \
        status coverage dashboard review-queue reviews repos agents parity-pr parity-jira \
        serve queue-run export-plan publish-plan attach-plan hook-server prune \
        gaps catalog-db ingest-results smoke-openhands clear-demo report \
        test-gate demo-bootstrap demo-pr demo-jira review \
        docker-build deploy-local deploy-local-down deploy-openshift email \
        plan plan-show plan-approve plan-changes plan-edit plan-link plan-tests plans \
        demo-plan demo-plan-tests sync-guidance sync-status check-integrations skills

deps:
	pip install --break-system-packages -r requirements.txt

test-routing:
	python3 -m pytest registry/tests -q

bootstrap:
	bash catalog/bootstrap/run_bootstrap.sh $(REPO)

run-pr:
	bash engine/pipeline.sh pr $(REPO) $(PR)

run-jira:
	bash engine/pipeline.sh jira $(KEY)

eval:
	bash eval/replay.sh && python3 eval/scorecard.py

conformance:
	bash adapters/conformance/test_adapters.sh

test-gate:
	bash tests/gate-adversarial.sh

demo-bootstrap:
	bash bin/demo-bootstrap.sh e2e-api-tests-1 && bash bin/demo-bootstrap.sh e2e-ui-tests-1

demo-pr:
	AIQE_MOCK=1 bash engine/pipeline.sh pr orders-api 201

demo-jira:
	AIQE_MOCK=1 bash engine/pipeline.sh jira PROJ-301

# Real-LLM parity: claude -p phases, demo estate + mock adapters (REVIEW.md item 1)
parity-pr:
	AIQE_MOCK=1 AIQE_REAL_LLM=1 bash engine/pipeline.sh pr orders-api 201

parity-jira:
	AIQE_MOCK=1 AIQE_REAL_LLM=1 bash engine/pipeline.sh jira PROJ-301

review:
	python3 -m pytest registry/tests -q && bash adapters/conformance/test_adapters.sh && bash tests/gate-adversarial.sh && bash eval/replay.sh && python3 eval/scorecard.py

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

check-integrations:   # read-only connectivity check for every configured system
	python3 engine/lib/integration_check.py $(WHICH)

smoke-openhands:
	bash bin/smoke-openhands.sh

prune:
	python3 bin/qa.py prune --keep $(or $(KEEP),200)

clear-demo:
	python3 engine/lib/demo_data.py $(if $(DRY),--dry,)

docker-build:
	docker build -t $(or $(IMAGE),ai-qe-platform:local) $(if $(REAL),--build-arg INSTALL_REAL_TOOLS=1,) .

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
