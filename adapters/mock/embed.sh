#!/usr/bin/env bash
# Embedding port — deterministic mock (cost-reduction story 3.1).
#
# Hash-based vectors: sha256(text) bytes expanded to EMBED_DIMS floats in
# [-1, 1]. Deterministic across runs and platforms, so demos and tests get
# stable similarity without any network or credentials. Identical texts get
# identical vectors; similar texts do NOT get similar vectors — the mock proves
# plumbing, never retrieval quality (that is what the eval benchmark is for).
#
# Verbs mirror adapters/embed/http.sh: embed_texts (JSONL in/out), dims.
set -euo pipefail
VERB=${1:?verb}; shift || true

case "$VERB" in
  embed_texts|dims)
    # The heredoc below owns stdin, so the piped JSONL must be parked in a file
    # first — piping straight through would hand python an empty stream.
    TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT
    [ "$VERB" = embed_texts ] && cat > "$TMP" || true
    python3 - "$VERB" "$TMP" <<'PY'
import hashlib, json, os, struct, sys

DIMS = int(os.environ.get("EMBED_DIMS", "") or 64)

def vec(text):
    out, seed, n = [], text.encode("utf-8"), 0
    while len(out) < DIMS:
        h = hashlib.sha256(seed + str(n).encode()).digest()
        for i in range(0, len(h) - 3, 4):
            (u,) = struct.unpack(">I", h[i:i + 4])
            out.append(round(u / 2**31 - 1.0, 6))
            if len(out) == DIMS:
                break
        n += 1
    return out

if sys.argv[1] == "dims":
    print(DIMS)
    sys.exit(0)
for line in open(sys.argv[2], encoding="utf-8"):
    if line.strip():
        item = json.loads(line)
        print(json.dumps({"id": item["id"], "vec": vec(item["text"])}))
PY
    ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
