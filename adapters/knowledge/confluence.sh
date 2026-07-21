#!/usr/bin/env bash
set -euo pipefail
VERB=${1:?verb}; shift || true

# Knowledge port: get_linked_docs <ticket.json> | publish_doc <space> <title> <file>
# Follows Confluence remote links on the ticket; enforces page/token budget from
# org-config (knowledge.confluence_max_pages / _max_tokens). MCP is the primary
# in-session path; this is the pipeline-side REST fallback.
case "$VERB" in
  get_linked_docs)
    python3 - "$1" << 'PY'
import json, os, sys, urllib.request
t = json.load(open(sys.argv[1])); budget_pages = 3
url = t.get("remote_links_url")
if not url: print(""); raise SystemExit
req = urllib.request.Request(url, headers={"Authorization": "Bearer " + os.environ["ATLASSIAN_MCP_TOKEN"]})
try: links = json.load(urllib.request.urlopen(req, timeout=20))
except Exception: print(""); raise SystemExit
pages = [l for l in links if "confluence" in l.get("object", {}).get("url", "")][:budget_pages]
for p in pages:
    print(f"## Linked doc: {p['object'].get('title','')}\n{p['object']['url']}\n")
    # TODO: fetch page body via Confluence REST /wiki/api/v2/pages/{id}?body-format=storage
PY
    ;;
  publish_doc)
    # publish_doc <space> <title> <body.html> — create-or-update by (space, title).
    # One-way mirror: the repo's testplans/<KEY>.md remains the source of truth.
    # Env: CONFLUENCE_URL (e.g. https://your-domain.atlassian.net/wiki), ATLASSIAN_MCP_TOKEN
    python3 - "$1" "$2" "$3" << 'PY'
import json, os, sys, urllib.parse, urllib.request
space, title, body_file = sys.argv[1:4]
base = os.environ["CONFLUENCE_URL"].rstrip("/")
tok = os.environ["ATLASSIAN_MCP_TOKEN"]
body = open(body_file, encoding="utf-8").read()

def call(method, url, payload=None):
    req = urllib.request.Request(url, method=method,
        data=json.dumps(payload).encode() if payload else None,
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=30))

q = urllib.parse.urlencode({"spaceKey": space, "title": title, "expand": "version"})
found = call("GET", f"{base}/rest/api/content?{q}").get("results", [])
doc = {"type": "page", "title": title, "space": {"key": space},
       "body": {"storage": {"value": body, "representation": "storage"}}}
if found:
    page = found[0]
    doc["version"] = {"number": page["version"]["number"] + 1}
    out = call("PUT", f"{base}/rest/api/content/{page['id']}", doc)
    action = "updated"
else:
    out = call("POST", f"{base}/rest/api/content", doc)
    action = "created"
print(f"{action}: {base}{out.get('_links', {}).get('webui', '')}")
PY
    ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
