#!/usr/bin/env python3
"""Batch spool -- many requests in ONE Message Batch (batch slice 2).

WHAT THIS IS FOR, stated precisely, because the first draft of the PRD got it
wrong: this does NOT save more money than slice 1. All Batches API usage is
charged at 50% of standard prices -- the discount comes from using the API, and
a batch of one already gets it. What a spool buys is WALL CLOCK. Forty tickets
submitted as forty one-request batches are forty sequential waits of up to an
hour; submitted as one batch they finish together.

So the honest pitch is throughput for bulk work nobody is waiting on: author
plans for a whole release overnight, drain them in the morning.

Three things this module refuses to get wrong:

  * `custom_id` correlation. Results may be returned in ANY order, so nothing
    is ever matched by position. With N requests in one batch, a positional
    shortcut would not merely be wrong, it would attach one ticket's plan to
    another ticket's key.
  * Partial outcomes. A batch can end with some requests succeeded, some
    errored and some expired. Draining keeps every good result AND names every
    bad one -- dropping the failures would silently under-deliver, and failing
    the whole drain would throw away work that was paid for.
  * `expired`/`canceled` are NOT verdicts (C13). Those requests never reached
    the model and were never billed; reporting them as "no plan was produced"
    asserts something we have no basis for.

State lives under AIQE_BATCH_DIR (default reports/batch), written through
fs_lock like every other durable store here.

CLI: batch_spool.py add <key> <phase> <model> <prompt_file>
     batch_spool.py pending | submit | status | drain
"""
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import budget  # noqa: E402
import fs_lock  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
DIR = pathlib.Path(os.environ.get("AIQE_BATCH_DIR") or (ROOT / "reports/batch"))
SPOOL = DIR / "spool.json"
BATCHES = DIR / "batches.json"

BASE = (os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")
MAX_TOKENS = int(os.environ.get("AIQE_BATCH_MAX_TOKENS") or 8192)
# The API's own ceiling. Exceeding it is a submit-time refusal rather than a
# rejected HTTP call, so the operator is told which requests did not go.
MAX_REQUESTS = 100_000


def _headers():
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "the Message Batches API needs ANTHROPIC_API_KEY -- the Claude Code "
            "CLI subscription login does not work here. No silent fallback.")
    return {"x-api-key": key, "anthropic-version": "2023-06-01",
            "content-type": "application/json"}


def _call(method, url, body=None):
    req = urllib.request.Request(
        url, method=method, headers=_headers(),
        data=json.dumps(body).encode("utf-8") if body is not None else None)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def _read(path, default):
    return fs_lock.read_json_guarded(path, default)


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fs_lock.write_json_atomic(path, data)


# --- spooling ---------------------------------------------------------------

def add(key, phase, model, prompt):
    """Queue one request. Returns its custom_id."""
    if not key or not phase:
        raise ValueError("key and phase are required")
    with fs_lock.lock(str(SPOOL)):
        d = _read(SPOOL, {"requests": []})
        seq = len(d["requests"]) + 1
        # Carries key AND phase so a drained result can be routed back without
        # consulting anything else; `seq` only disambiguates repeats.
        cid = f"{key}:{phase}:{seq}"
        d["requests"].append({"custom_id": cid, "key": key, "phase": phase,
                              "model": model, "prompt": prompt})
        _write(SPOOL, d)
    return cid


def pending():
    return _read(SPOOL, {"requests": []})["requests"]


def clear():
    _write(SPOOL, {"requests": []})


# --- submission -------------------------------------------------------------

def estimate(reqs=None):
    """(usd, basis, detail) for what submitting would cost.

    An ESTIMATE, and labelled as one everywhere it surfaces. Input tokens are
    approximated from prompt length (~4 chars/token) and output is charged at
    the full `max_tokens` per request, so this is a WORST CASE -- deliberately,
    because a ceiling that is only respected on average is not a ceiling.
    """
    reqs = pending() if reqs is None else reqs
    if not reqs:
        return 0.0, "estimated", "nothing spooled"
    total, basis = 0.0, "estimated"
    for r in reqs:
        usage = {"input_tokens": max(1, len(r.get("prompt") or "") // 4),
                 "output_tokens": MAX_TOKENS}
        cost, b = budget.priced("batch", r.get("model") or "", usage)
        if b == "unknown" or cost is None:
            # One unpriceable model makes the whole total unknown; adding the
            # rest would report a partial figure as if it were the answer.
            return None, "unknown", (
                f"no `pricing:` entry for batch model {r.get('model')!r}")
        total += cost
    return round(total, 6), basis, f"{len(reqs)} request(s), worst case"


def _ceiling():
    """(usd, source). 0 means no ceiling was asked for."""
    raw = (os.environ.get("AIQE_BATCH_SPOOL_MAX_USD") or "").strip()
    if raw:
        try:
            return float(raw), "AIQE_BATCH_SPOOL_MAX_USD"
        except ValueError:
            # A typo must not silently mean "unlimited".
            raise RuntimeError(
                f"AIQE_BATCH_SPOOL_MAX_USD={raw!r} is not a number -- refusing "
                f"to submit rather than treat an unreadable ceiling as none")
    try:
        import yaml
        cfg = yaml.safe_load(open(ROOT / "registry/org-config.yaml",
                                  encoding="utf-8")) or {}
        v = (cfg.get("budgets") or {}).get("batch_spool_usd")
        if isinstance(v, (int, float)) and v > 0:
            return float(v), "org-config budgets.batch_spool_usd"
    except Exception:
        pass
    return 0.0, ""


def submit(now=None):
    """Send every spooled request as ONE batch. Returns the batch record."""
    reqs = pending()
    if not reqs:
        return {"state": "empty",
                "message": "nothing spooled -- `add` some requests first"}

    # A spool is the largest single spend this platform can commit, and the
    # exit-77 ceiling cannot see it: that one is checked BEFORE each phase of a
    # RUN, and this is many keys at once, committed in a single call. So the
    # check happens here or nowhere.
    cap, source = _ceiling()
    est, basis, detail = estimate(reqs)
    if cap > 0:
        if basis == "unknown":
            # C13: asked for a ceiling, cannot compute spend. Refusing is the
            # only honest answer -- proceeding would enforce nothing silently.
            raise RuntimeError(
                f"BATCH_CEILING_UNENFORCEABLE: a ${cap:.2f} ceiling is set "
                f"({source}) but this spool cannot be priced -- {detail}. "
                f"Add the entry to `pricing:` in registry/org-config.yaml, or "
                f"unset the ceiling to submit unpriced.")
        if est > cap:
            raise RuntimeError(
                f"BATCH_CEILING_EXCEEDED: submitting would cost about "
                f"~${est:.2f} ({detail}) against a ${cap:.2f} ceiling "
                f"({source}). Nothing was sent and the spool is untouched -- "
                f"split it, or raise the ceiling.")
    if len(reqs) > MAX_REQUESTS:
        raise RuntimeError(
            f"{len(reqs)} requests exceeds the API's {MAX_REQUESTS}-per-batch "
            f"limit; split the spool")

    body = {"requests": [
        {"custom_id": r["custom_id"],
         "params": {"model": r["model"], "max_tokens": MAX_TOKENS,
                    "messages": [{"role": "user", "content": r["prompt"]}]}}
        for r in reqs]}
    try:
        resp = _call("POST", f"{BASE}/v1/messages/batches", body)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(
            f"PROVIDER_UNREACHABLE: batch submit failed ({e}). Nothing was "
            f"sent and the spool is untouched -- no silent fallback.") from e

    bid = resp.get("id")
    if not bid:
        raise RuntimeError(f"batch submit returned no id ({json.dumps(resp)[:200]})")

    rec = {"id": bid, "submitted": int(now or time.time()), "drained": False,
           # Recorded so "what did that batch cost?" is answerable
           # later; `basis` keeps it readable as an estimate.
           "estimate_usd": est, "estimate_basis": basis,
           # The routing table. Kept HERE rather than re-derived at drain time,
           # because the spool is cleared once the batch is accepted and a
           # result whose key we cannot recover is a plan nobody can find.
           "requests": [{k: r[k] for k in ("custom_id", "key", "phase", "model")}
                        for r in reqs]}
    # THE DANGEROUS WINDOW. The API has accepted the batch: it is running and it
    # WILL be billed. If recording it fails (lock timeout, disk, permissions)
    # the id exists only in this stack frame -- and because the spool is
    # deliberately left intact, the natural reaction is to retry, which submits
    # a SECOND batch and pays twice for an orphan nobody can cancel. The id
    # cannot be recorded before submission (it does not exist yet), so the
    # window is inherent; what is fixable is failing LOUDLY with the id.
    try:
        with fs_lock.lock(str(BATCHES)):
            d = _read(BATCHES, {"batches": []})
            d["batches"].append(rec)
            _write(BATCHES, d)
    except Exception as e:
        raise RuntimeError(
            f"BATCH_SUBMITTED_BUT_UNRECORDED: batch {bid} IS RUNNING and will "
            f"be billed, but it could not be written to {BATCHES} ({e}).\n"
            f"  Do NOT simply re-run submit -- that sends a second batch and "
            f"pays twice. Record or cancel {bid} first; the spool has been "
            f"left intact on purpose so nothing was lost.") from e
    # Only now: the batch is durably recorded, so clearing cannot lose work.
    clear()
    return rec


def batches():
    return _read(BATCHES, {"batches": []})["batches"]


def status():
    """Per in-flight batch: what the API says, without guessing."""
    out = []
    for b in batches():
        if b.get("drained"):
            out.append({"id": b["id"], "state": "drained",
                        "requests": len(b["requests"])})
            continue
        try:
            resp = _call("GET", f"{BASE}/v1/messages/batches/{b['id']}")
            state = resp.get("processing_status") or "unknown"
        except Exception as e:
            # C13: we could not ask. That is not "still running" and not "done".
            out.append({"id": b["id"], "state": "unknown",
                        "requests": len(b["requests"]),
                        "detail": f"could not reach the API: {e}"})
            continue
        out.append({"id": b["id"], "state": state,
                    "requests": len(b["requests"]),
                    "results_url": resp.get("results_url")})
    return out


# --- draining ---------------------------------------------------------------

def _fetch_results(url):
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read().decode("utf-8")


def drain(batch_id=None):
    """Retrieve ended batches. Returns per-request outcomes.

    Every request gets an entry. A batch that has not ended yet is reported as
    such and left alone -- never as a batch that produced nothing.
    """
    results, touched = [], []
    for b in batches():
        if b.get("drained") or (batch_id and b["id"] != batch_id):
            continue
        try:
            meta = _call("GET", f"{BASE}/v1/messages/batches/{b['id']}")
        except Exception as e:
            results.append({"batch": b["id"], "state": "unknown",
                            "detail": f"could not reach the API: {e}"})
            continue
        if meta.get("processing_status") != "ended":
            results.append({"batch": b["id"], "state": "still_processing",
                            "detail": "not ended yet; it is still running and "
                                      "will still be billed"})
            continue
        url = meta.get("results_url")
        if not url:
            results.append({"batch": b["id"], "state": "no_results_url"})
            continue
        try:
            raw = _fetch_results(url)
        except Exception as e:
            results.append({"batch": b["id"], "state": "unknown",
                            "detail": f"results download failed: {e}"})
            continue

        by_id = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("custom_id"):
                by_id[row["custom_id"]] = row.get("result") or {}

        # Where successful text lands. Draining marks the batch done, so a
        # result that is not persisted HERE is gone: paid for, retrieved once,
        # and unrecoverable. Found by driving `make batch-drain` -- the library
        # returned results correctly and the only caller threw them away.
        outdir = DIR / "results"
        for r in b["requests"]:
            # Matched by custom_id, never by position: with N requests a
            # positional read would attach one ticket's plan to another's key.
            res = by_id.get(r["custom_id"])
            entry = {"batch": b["id"], "key": r["key"], "phase": r["phase"],
                     "custom_id": r["custom_id"]}
            if res is None:
                entry.update(state="missing",
                             detail="no result row carried this custom_id")
            else:
                out = _outcome(res)
                if out.get("state") == "succeeded":
                    # Persist BEFORE the batch is marked drained, for the same
                    # reason submit records before clearing: the step after
                    # this one destroys the ability to try again.
                    outdir.mkdir(parents=True, exist_ok=True)
                    dest = outdir / f"{r['key']}-{r['phase']}.txt"
                    dest.write_bytes((out.get("text") or "").encode("utf-8"))
                    out["saved_to"] = str(dest)
                entry.update(out)
            results.append(entry)
        touched.append(b["id"])

    if touched:
        with fs_lock.lock(str(BATCHES)):
            d = _read(BATCHES, {"batches": []})
            for b in d["batches"]:
                if b["id"] in touched:
                    b["drained"] = True
            _write(BATCHES, d)
    return results


def _outcome(res):
    kind = res.get("type")
    if kind == "succeeded":
        msg = res.get("message") or {}
        text = "".join(b.get("text") or "" for b in (msg.get("content") or [])
                       if isinstance(b, dict) and b.get("type") == "text")
        usage = msg.get("usage") or {}
        return {"state": "succeeded", "text": text,
                "usage": {"input_tokens": int(usage.get("input_tokens") or 0),
                          "output_tokens": int(usage.get("output_tokens") or 0)},
                "model": msg.get("model") or ""}
    if kind in ("expired", "canceled"):
        # NOT a verdict about the request. It never reached the model.
        return {"state": kind, "billed": False,
                "detail": f"{kind} before the model saw it -- NOT billed, and "
                          f"nothing is known about this phase. Re-spool it."}
    return {"state": "errored", "billed": True,
            "detail": json.dumps(res.get("error") or res)[:300]}


def summarize(results):
    """Counts per state -- so a partial drain reads as partial, not as success."""
    counts = {}
    for r in results:
        counts[r.get("state", "?")] = counts.get(r.get("state", "?"), 0) + 1
    return counts


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "pending"
    if cmd == "add":
        key, phase, model, prompt_file = argv[2:6]
        cid = add(key, phase, model,
                  pathlib.Path(prompt_file).read_text(encoding="utf-8"))
        print(cid)
    elif cmd == "pending":
        reqs = pending()
        print(f"{len(reqs)} request(s) spooled")
        for r in reqs:
            print(f"  {r['custom_id']}  ({r['model']})")
    elif cmd == "submit":
        rec = submit()
        if rec.get("state") == "empty":
            print(rec["message"])
            return 0
        print(f"submitted {rec['id']} with {len(rec['requests'])} request(s)")
    elif cmd == "status":
        for s in status():
            extra = f" -- {s['detail']}" if s.get("detail") else ""
            print(f"{s['id']}  {s['state']}  ({s['requests']} request(s)){extra}")
    elif cmd == "drain":
        res = drain()
        counts = summarize(res)
        print(json.dumps(counts))
        saved = [r for r in res if r.get("saved_to")]
        for r in saved:
            print(f"  {r['key']} -> {r['saved_to']}")
        if counts.get("succeeded") and not saved:
            # Would mean text was retrieved and dropped on the floor.
            print("  WARNING: succeeded results were not written to disk",
                  file=sys.stderr)
        for r in res:
            if r.get("state") != "succeeded":
                print(f"  {r.get('key', r.get('batch'))}: {r.get('state')}"
                      f" -- {r.get('detail', '')}")
    else:
        print(f"unknown command {cmd}", file=sys.stderr)
        return 64
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
