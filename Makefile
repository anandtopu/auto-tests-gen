SHELL := /bin/bash
.PHONY: deps test-routing bootstrap run-pr run-jira eval conformance \
        status coverage dashboard review-queue reviews repos agents parity-pr parity-jira \
        serve queue-run export-plan publish-plan attach-plan hook-server prune \
        gaps catalog-db ingest-results smoke-openhands clear-demo report \
        test-gate demo-bootstrap demo-pr demo-jira review \
        docker-build deploy-local deploy-local-down deploy-openshift email

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
