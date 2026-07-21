SHELL := /bin/bash
.PHONY: deps test-routing bootstrap run-pr run-jira eval conformance \
        status coverage dashboard review-queue repos agents parity-pr parity-jira

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

dashboard:
	python3 bin/dashboard.py

# --- repo configuration & estate knowledge ---
repos:
	python3 bin/repos.py list

agents:
	python3 bin/gen_agents_md.py
