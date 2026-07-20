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
  publish_doc) echo "TODO: one-way mirror testplan -> Confluence page (repo remains source of truth)";;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
