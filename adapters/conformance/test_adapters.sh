#!/usr/bin/env bash
# Adapter conformance: every adapter must fail cleanly on unknown verbs (exit 64)
# and each port's required verbs must be handled. Extend with golden tests per tool.
set -u
# The transaction log is REDIRECTED. This suite invokes every adapter, and an
# adapter that emits reaches the estate's REAL audit log — the record an
# operator reads, and the input `make maintain` feeds to alert_rules.evaluate(),
# which delivers through the Notify port. Nothing here is a transaction on this
# estate. (Found by the review-chain invariant in test_audit_log_isolation.py,
# not by enumerating suites by hand — which had missed this one.)
_CONF_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
mkdir -p "$_CONF_ROOT/out/test-events"
export AIQE_EVENTS_DIR="$(cd "$_CONF_ROOT/out/test-events" && pwd -W 2>/dev/null || pwd)"
fail=0
declare -A verbs=( [scm/github.sh]="clone_ro clone_rw changed_files diff comment set_status fetch_file"
                   [scm/bitbucket.sh]="clone_ro clone_rw changed_files diff comment set_status fetch_file"
                   [scm/stash.sh]="clone_ro clone_rw changed_files diff comment set_status fetch_file"
                   [tracker/jira.sh]="get_item comment search_release attach"
                   [knowledge/confluence.sh]="get_linked_docs publish_doc"
                   [cicd/jenkins.sh]="run_job get_results"
                   [notify/slack.sh]="post digest"
                   [notify/email.sh]="post digest"
                   [telemetry/splunk.sh]="emit_event"
                   [llm/claude.sh]="run_phase capabilities check tool_policy"
                   [llm/ollama.sh]="run_phase capabilities check tool_policy"
                   [llm/codex.sh]="run_phase capabilities check tool_policy"
                   [llm/openhands.sh]="run_phase capabilities check tool_policy"
                   [mock/llm.sh]="run_phase capabilities check tool_policy"
                   [embed/http.sh]="embed_texts dims"
                   [mock/embed.sh]="embed_texts dims" )
for a in "${!verbs[@]}"; do
  bash "adapters/$a" definitely_unknown_verb 2>/dev/null; [ $? -eq 64 ] || { echo "FAIL unknown-verb: $a"; fail=1; }
  for v in ${verbs[$a]}; do
    grep -q "$v" "adapters/$a" || { echo "FAIL missing verb $v in $a"; fail=1; }
  done
done

# --- LLM tool policy (multi-LLM 5.1) -----------------------------------------
# Every LLM adapter must ANSWER what it will actually enforce for a given
# allow-list, and the answer must never be more permissive than the policy.
# The failure this prevents is silent: an adapter whose runtime cannot express
# "read-only" (codex governs by sandbox, not per-tool) quietly giving write
# access to the critic or the plan adversary — at which point "advisory" and
# "an opponent that cannot edit the plan" stop being true.
for a in llm/claude.sh llm/codex.sh llm/ollama.sh llm/openhands.sh mock/llm.sh; do
  out=$(bash "adapters/$a" tool_policy "Read" 2>&1) || { echo "FAIL tool_policy errored: $a"; fail=1; continue; }
  case "$out" in
    readonly*|none*) ;;
    *) echo "FAIL read-only policy became '$out' in $a"; fail=1 ;;
  esac
  out=$(bash "adapters/$a" tool_policy "Read,Write,Edit" 2>&1) || true
  case "$out" in
    writable*|none*) ;;
    *) echo "FAIL authoring policy became '$out' in $a"; fail=1 ;;
  esac
done

[ $fail -eq 0 ] && echo "adapter conformance OK"
exit $fail
