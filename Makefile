SHELL := /bin/bash
.PHONY: deps test-routing bootstrap run-pr run-jira eval conformance

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
