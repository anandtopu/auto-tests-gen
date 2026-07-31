#!/usr/bin/env bash
# Embedding port — real adapter (cost-reduction story 3.1).
#
# Speaks to any OpenAI-compatible /v1/embeddings endpoint (Voyage, OpenAI, Azure,
# local TEI/Ollama) via python stdlib HTTP — no SDK, per the ADR
# (docs/adr/embeddings.md). Config from the environment:
#   EMBED_URL       e.g. https://api.voyageai.com/v1  (base, /embeddings appended)
#   EMBED_API_KEY   bearer token (secret — Settings stores it write-only)
#   EMBED_MODEL     e.g. voyage-3-lite
#   EMBED_DIMS      optional; passed through when the provider supports it
#
# Verbs:
#   embed_texts   stdin: JSONL {"id":..,"text":..}  stdout: JSONL {"id":..,"vec":[..]}
#   dims          prints the vector dimensionality (embeds one probe string)
#
# Exit codes: 0 ok · 3 not configured (EMBED_URL unset) · 64 unknown verb
set -euo pipefail
VERB=${1:?verb}; shift || true

case "$VERB" in
  embed_texts|dims)
    [ -n "${EMBED_URL:-}" ] || { echo "EMBED_URL not configured" >&2; exit 3; }
    # The heredoc below owns stdin — park the piped JSONL in a file first.
    TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT
    [ "$VERB" = embed_texts ] && cat > "$TMP" || true
    python3 - "$VERB" "$TMP" <<'PY'
import json, os, sys, urllib.request

verb = sys.argv[1]
base = os.environ["EMBED_URL"].rstrip("/")
url = base if base.endswith("/embeddings") else base + "/embeddings"
headers = {"Content-Type": "application/json"}
if os.environ.get("EMBED_API_KEY"):
    headers["Authorization"] = "Bearer " + os.environ["EMBED_API_KEY"]

def embed(texts):
    body = {"model": os.environ.get("EMBED_MODEL", ""), "input": texts}
    if os.environ.get("EMBED_DIMS"):
        body["dimensions"] = int(os.environ["EMBED_DIMS"])
    req = urllib.request.Request(url, json.dumps(body).encode("utf-8"),
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    rows = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
    return [d["embedding"] for d in rows]

if verb == "dims":
    print(len(embed(["probe"])[0]))
    sys.exit(0)

items = [json.loads(l) for l in open(sys.argv[2], encoding="utf-8") if l.strip()]
BATCH = 64
for i in range(0, len(items), BATCH):
    chunk = items[i:i + BATCH]
    for item, vec in zip(chunk, embed([c["text"] for c in chunk])):
        print(json.dumps({"id": item["id"], "vec": vec}))
PY
    ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
