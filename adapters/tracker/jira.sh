#!/usr/bin/env bash
set -euo pipefail
VERB=${1:?verb}; shift || true

# Tracker port: get_item | search_release | comment | attach
# Primary path: Atlassian Remote MCP inside the Claude Code session (registered in
# sandbox/mcp-setup.sh). This CLI adapter is the pipeline-side fallback via REST.
J="${JIRA_URL:-https://your-domain.atlassian.net}/rest/api/3"
case "$VERB" in
  get_item) curl -s -H "Authorization: Bearer ${ATLASSIAN_MCP_TOKEN}" "$J/issue/$1" \
    | python3 -c "
import json,sys; i=json.load(sys.stdin); f=i['fields']
print(json.dumps({'key':i['key'],'summary':f['summary'],
 'description':str(f.get('description','')),
 'components':[c['name'] for c in f.get('components',[])],
 'labels':f.get('labels',[]),
 'fix_versions':[v['name'] for v in f.get('fixVersions',[])],
 'issue_type':(f.get('issuetype') or {}).get('name',''),
 'linked_repos':[],  # populated from dev-panel API if enabled
 'remote_links_url':'$J/issue/'+i['key']+'/remotelink'}))" ;;
  search_release)  # JQL: tickets targeting a fixVersion (empty arg = all with any fixVersion)
    JQL="fixVersion is not EMPTY"; [ -n "${1:-}" ] && JQL="fixVersion = \"$1\""
    curl -s -G -H "Authorization: Bearer ${ATLASSIAN_MCP_TOKEN}" \
      --data-urlencode "jql=$JQL" --data-urlencode "fields=summary,fixVersions" \
      "$J/search" | python3 -c "
import json,sys
r=json.load(sys.stdin)
print(json.dumps([{'key':i['key'],'summary':i['fields']['summary'],
 'fix_versions':[v['name'] for v in i['fields'].get('fixVersions',[])]}
 for i in r.get('issues',[])]))" ;;
  attach)  # attach <KEY> <file> — upload as a Jira issue attachment
    curl -s -X POST -H "Authorization: Bearer ${ATLASSIAN_MCP_TOKEN}" \
      -H "X-Atlassian-Token: no-check" \
      -F "file=@$2" "$J/issue/$1/attachments" \
      | python3 -c "
import json,sys
r=json.load(sys.stdin)
print('attached: ' + ', '.join(a['filename'] for a in r))" ;;
  comment) curl -s -X POST -H "Authorization: Bearer ${ATLASSIAN_MCP_TOKEN}" \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c "import json,sys;print(json.dumps({'body':{'type':'doc','version':1,'content':[{'type':'paragraph','content':[{'type':'text','text':sys.argv[1]}]}]}}))" "$2")" \
    "$J/issue/$1/comment" >/dev/null && echo ok ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
