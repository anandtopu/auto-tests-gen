#!/usr/bin/env bash
set -euo pipefail
VERB=${1:?verb}; shift || true

# Tracker port: get_item | search | search_release | comment | attach
# Primary path: Atlassian Remote MCP inside the Claude Code session (registered in
# sandbox/mcp-setup.sh). This CLI adapter is the pipeline-side fallback via REST.
# JIRA_API_VERSION: 2 = Jira Server / Data Center (on-prem); 3 = Jira Cloud (ADF bodies).
# Default to 2 — the adf() parser already handles both plain strings and ADF objects,
# so v2 works against Cloud too, making it the safer default for on-prem estates.
J="${JIRA_URL:-https://your-domain.atlassian.net}/rest/api/${JIRA_API_VERSION:-2}"

# Proxy and SSL flags shared by every curl call in this adapter.
# Proxy is handled via standard HTTPS_PROXY / NO_PROXY env vars (mapped from
# AIQE_HTTPS_PROXY / AIQE_NO_PROXY by settings_store.load_env_into() or by
# pipeline.sh which sources .env). curl reads these automatically, so internal
# hosts listed in NO_PROXY are reached directly without needing explicit -x flags.
CURL_FLAGS=(-s --fail-with-body)
if [[ "${AIQE_SSL_VERIFY:-1}" == "0" ]]; then CURL_FLAGS+=(-k); fi

run_search() {
  local filters=${1:-'{}'} jql
  jql=$(python3 engine/lib/ticket_search.py jql "$filters") || return $?
  curl "${CURL_FLAGS[@]}" -G -H "Authorization: Bearer ${ATLASSIAN_MCP_TOKEN}" \
    --data-urlencode "jql=$jql" \
    --data-urlencode "fields=summary,issuetype,components,labels,fixVersions,status" \
    --data-urlencode "startAt=0" --data-urlencode "maxResults=50" \
    "$J/search" | python3 engine/lib/ticket_search.py project
}

case "$VERB" in
  get_item)
    BODY=$(mktemp "${TMPDIR:-/tmp}/aiqe-jira-item.XXXXXX")
    trap 'rm -f "$BODY"' EXIT
    ITEM_FLAGS=(-s)
    if [[ "${AIQE_SSL_VERIFY:-1}" == "0" ]]; then ITEM_FLAGS+=(-k); fi
    HTTP=$(curl "${ITEM_FLAGS[@]}" -o "$BODY" -w '%{http_code}' \
      -H "Authorization: Bearer ${ATLASSIAN_MCP_TOKEN}" \
      "$J/issue/$1?fields=summary,description,components,labels,fixVersions,issuetype,status,comment") \
      || exit 1
    # Some estates wrap curl for recording/replay and return the body on stdout
    # without implementing -o/-w. Preserve that supported adapter-test shape,
    # but accept it only when it is visibly a JSON object; arbitrary output is
    # still an unavailable validation, never a successful ticket lookup.
    case "$HTTP" in
      [0-9][0-9][0-9]) ;;
      \{*) printf '%s\n' "$HTTP" > "$BODY"; HTTP=200 ;;
      *) exit 1 ;;
    esac
    [ "$HTTP" = "404" ] && exit 3
    [ "$HTTP" = "200" ] || { cat "$BODY" >&2; exit 1; }
    python3 -c "
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
 'status':(f.get('status') or {}).get('name',''),
 'status_category':((f.get('status') or {}).get('statusCategory') or {}).get('key',''),
 'comments':comments,
 'linked_repos':[],  # populated from dev-panel API if enabled
 'remote_links_url':'$J/issue/'+i['key']+'/remotelink'}))" < "$BODY" ;;
  search) run_search "${1:-'{}'}" ;;
  search_release)  # compatibility: the old list response, over structured search
    FILTERS=$(python3 engine/lib/ticket_search.py release "${1:-}") || exit $?
    run_search "$FILTERS" | python3 -c \
      "import json,sys; print(json.dumps(json.load(sys.stdin)['items']))" ;;
  attach)  # attach <KEY> <file> — upload as a Jira issue attachment
    curl "${CURL_FLAGS[@]}" -X POST -H "Authorization: Bearer ${ATLASSIAN_MCP_TOKEN}" \
      -H "X-Atlassian-Token: no-check" \
      -F "file=@$2" "$J/issue/$1/attachments" \
      | python3 -c "
import json,sys
r=json.load(sys.stdin)
print('attached: ' + ', '.join(a['filename'] for a in r))" ;;
  comment) curl "${CURL_FLAGS[@]}" -X POST -H "Authorization: Bearer ${ATLASSIAN_MCP_TOKEN}" \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c "import json,sys;print(json.dumps({'body':{'type':'doc','version':1,'content':[{'type':'paragraph','content':[{'type':'text','text':sys.argv[1]}]}]}}))" "$2")" \
    "$J/issue/$1/comment" >/dev/null && echo ok ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
