#!/usr/bin/env bash
# Adapter conformance: every adapter must fail cleanly on unknown verbs (exit 64)
# and each port's required verbs must be handled. Extend with golden tests per tool.
set -u
fail=0
declare -A verbs=( [scm/github.sh]="clone_ro clone_rw changed_files comment"
                   [scm/bitbucket.sh]="clone_ro clone_rw changed_files comment"
                   [scm/stash.sh]="clone_ro clone_rw changed_files comment"
                   [tracker/jira.sh]="get_item comment search_release attach"
                   [knowledge/confluence.sh]="get_linked_docs publish_doc"
                   [cicd/jenkins.sh]="run_job get_results"
                   [notify/slack.sh]="post digest"
                   [telemetry/splunk.sh]="emit_event" )
for a in "${!verbs[@]}"; do
  bash "adapters/$a" definitely_unknown_verb 2>/dev/null; [ $? -eq 64 ] || { echo "FAIL unknown-verb: $a"; fail=1; }
  for v in ${verbs[$a]}; do
    grep -q "$v" "adapters/$a" || { echo "FAIL missing verb $v in $a"; fail=1; }
  done
done
[ $fail -eq 0 ] && echo "adapter conformance OK"
exit $fail
