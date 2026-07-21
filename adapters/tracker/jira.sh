#!/usr/bin/env bash
set -euo pipefail
VERB=${1:?verb}; shift || true

# Tracker port: get_item | search | comment
# Primary path: Atlassian Remote MCP inside the Claude Code session (registered in
# sandbox/mcp-setup.sh). This CLI adapter is the pipeline-side fallback via REST.
J="https://your-domain.atlassian.net/rest/api/3"
case "$VERB" in
  get_item) curl -s -H "Authorization: Bearer ${ATLASSIAN_MCP_TOKEN}" "$J/issue/$1" \
    | python3 -c "
import json,sys; i=json.load(sys.stdin); f=i['fields']
print(json.dumps({'key':i['key'],'summary':f['summary'],
 'description':str(f.get('description','')),
 'components':[c['name'] for c in f.get('components',[])],
 'labels':f.get('labels',[]),
 'fix_versions':[v['name'] for v in f.get('fixVersions',[])],
 'linked_repos':[],  # populated from dev-panel API if enabled
 'remote_links_url':'$J/issue/'+i['key']+'/remotelink'}))" ;;
  comment) curl -s -X POST -H "Authorization: Bearer ${ATLASSIAN_MCP_TOKEN}" \
    -H 'Content-Type: application/json' \
    -d "{\"body\":{\"type\":\"doc\",\"version\":1,\"content\":[{\"type\":\"paragraph\",\"content\":[{\"type\":\"text\",\"text\":\"$2\"}]}]}}" \
    "$J/issue/$1/comment" >/dev/null && echo ok ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
