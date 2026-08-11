#!/usr/bin/env python3
"""Embedding port client (cost-reduction story 3.1).

The ONLY module that talks to an embedding adapter — the engine never imports a
vector-DB or embedding SDK (there is none to import: the real adapter is
stdlib-HTTP shell, docs/adr/embeddings.md). Selection mirrors every other port:
AIQE_MOCK=1 -> adapters/mock/embed.sh (deterministic hash vectors, no network),
else adapters/embed/http.sh (needs EMBED_URL).

`configured()` is the fallback switch every consumer honours: False means
"take the TF-IDF path, silently and correctly" — an unconfigured estate must
never crash or degrade loudly (pinned in test_vector_index.py).
"""
import json
import os

import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import work_queue  # bash_exe(): plain "bash" resolves to WSL's stub on Windows
import env_flag                     # AIQE_MOCK means what it says

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _mock():
    return env_flag.mock()


def _adapter():
    return ROOT / ("adapters/mock/embed.sh" if _mock() else "adapters/embed/http.sh")


def configured():
    """True when embedding calls can succeed. Mock mode is always configured —
    demos and tests exercise the vector path without credentials."""
    return _mock() or bool(os.environ.get("EMBED_URL", "").strip())


def identity():
    """A short name for the VECTOR SPACE these calls produce.

    Two vectors are only comparable when they came from the same embedding
    model at the same width. Cosine similarity across two models is not a
    weaker signal, it is a meaningless one — and it looks exactly like a strong
    one, because the arithmetic still returns a number in [-1, 1].

    So this is the vector index's staleness key, the same role
    `PROVIDER:MODEL` plays in the phase cache: content alone is not enough to
    decide a stored vector is still usable.

    Deliberately NOT the URL. Two gateways serving the same model produce the
    same space, so keying on the host would re-embed the whole corpus for a
    DNS change; and this string is written to a file and printed, so a private
    hostname does not belong in it. Model + width is what determines the space.
    """
    if not configured():
        return ""
    if _mock():
        # Hash vectors. Named so nobody mistakes an index built by a demo for a
        # semantic one -- they carry no meaning at ANY width.
        return f"mock-hash:{os.environ.get('EMBED_DIMS', '').strip() or '64'}"
    model = os.environ.get("EMBED_MODEL", "").strip() or "default"
    width = os.environ.get("EMBED_DIMS", "").strip() or "adapter"
    return f"http:{model}:{width}"


def dims():
    """Vector dimensionality, or 0 when unconfigured/unreachable."""
    if not configured():
        return 0
    r = subprocess.run([work_queue.bash_exe(), str(_adapter()), "dims"],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL)
    try:
        return int(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 else 0
    except (ValueError, IndexError):
        return 0


def embed(texts):
    """[[float]] for each text, in order. Raises RuntimeError on failure —
    callers decide whether that means fallback (query) or partial stop
    (refresh); it must never mean a corrupted index."""
    if not configured():
        raise RuntimeError("embeddings not configured (set EMBED_URL, or run "
                           "with AIQE_MOCK=1 for the deterministic mock)")
    if not texts:
        return []
    payload = "".join(json.dumps({"id": i, "text": t}) + "\n"
                      for i, t in enumerate(texts))
    r = subprocess.run([work_queue.bash_exe(), str(_adapter()), "embed_texts"],
                       cwd=ROOT, input=payload, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"embed adapter failed (exit {r.returncode}): "
                           f"{(r.stderr or r.stdout).strip()[:200]}")
    by_id = {}
    for line in r.stdout.splitlines():
        if line.strip():
            try:
                row = json.loads(line)
                by_id[row["id"]] = row["vec"]
            except (ValueError, KeyError):
                continue
    out = []
    for i in range(len(texts)):
        if i not in by_id:
            raise RuntimeError(f"embed adapter returned no vector for item {i}")
        out.append(by_id[i])
    return out
