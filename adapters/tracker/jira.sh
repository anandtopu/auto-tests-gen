#!/usr/bin/env bash
set -euo pipefail
VERB=${1:?verb}; shift || true

# Tracker port: get_item | search_release | comment | attach
# Primary path: Atlassian Remote MCP inside the Claude Code session (registered in
# sandbox/mcp-setup.sh). This CLI adapter is the pipeline-side fallback via REST.
J="${JIRA_URL:-https://your-domain.atlassian.net}/rest/api/3"
case "$VERB" in
  get_item) curl -s -H "Authorization: Bearer ${ATLASSIAN_MCP_TOKEN}" \
    "$J/issue/$1?fields=summary,description,components,labels,fixVersions,issuetype,comment" \
    | python3 -c "
import json,sys; i=json.load(sys.stdin); f=i['fields']

def adf(n):
    # Jira Cloud v3 bodies are ADF documents; Server/v2 are plain strings. Flatten
    # either to text — a test plan needs the words, not the markup tree.
    if isinstance(n, str): return n
    if isinstance(n, list): return ''.join(adf(x) for x in n)
    if not isinstance(n, dict): return ''
    if n.get('type') == 'text': return n.get('text', '')
    inner = adf(n.get('content', []))
    return inner + ('\n' if n.get('type') in ('paragraph','heading','listItem') else '')

# Comments carry the clarifications and edge cases the description lacks — cap at the
# last 20 so a years-old ticket cannot blow the phase context budget.
comments = [{'author': ((c.get('author') or {}).get('displayName')
                        or (c.get('author') or {}).get('name') or ''),
             'created': c.get('created',''),
             'body': adf(c.get('body','')).strip()}
            for c in ((f.get('comment') or {}).get('comments') or [])[-20:]]
print(json.dumps({'key':i['key'],'summary':f['summary'],
 'description':adf(f.get('description') or '').strip(),
 'components':[c['name'] for c in f.get('components',[])],
 'labels':f.get('labels',[]),
 'fix_versions':[v['name'] for v in f.get('fixVersions',[])],
 'issue_type':(f.get('issuetype') or {}).get('name',''),
 'comments':comments,
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
