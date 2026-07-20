#!/usr/bin/env bash
set -euo pipefail
VERB=${1:?verb}; shift || true

# Cicd port: run_job <job> | get_results <job> <build> | accept_trigger (see triggers/jenkins)
case "$VERB" in
  run_job)  curl -s -X POST -u "${JENKINS_USER}:${JENKINS_API_TOKEN}" \
            "${JENKINS_URL}/job/$1/build" && echo triggered ;;
  get_results) curl -s -u "${JENKINS_USER}:${JENKINS_API_TOKEN}" \
            "${JENKINS_URL}/job/$1/$2/testReport/api/json" ;;   # JUnit -> catalog telemetry
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
