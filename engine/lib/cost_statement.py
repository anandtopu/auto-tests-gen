#!/usr/bin/env python3
"""Auditable per-task token-cost statements (TCA-C1).

Statements are exact-key views over :func:`spend_history.spend_rows`.  Dollar
bases remain separate by construction; incomplete rows are counted, never
coerced to zero.  Non-user attribution (for example cache probes) is listed
outside task totals.
"""
from __future__ import annotations

import csv
import io
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app_paths
import fs_lock
import spend_history

ROOT = pathlib.Path(__file__).resolve().parents[2]
EXPORTS = app_paths.exports_dir(ROOT)
KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
FORMATS = ("md", "csv")
FIELDS = ("run_id", "mode", "phase", "provider", "model", "basis",
          "cost_usd", "input_tokens", "output_tokens", "cache_read_tokens",
          "cache_creation_tokens", "turns", "attempts", "attribution", "ts")


def validate_key(key):
    value = str(key or "").strip()
    if not KEY_RE.fullmatch(value) or value in (".", ".."):
        raise ValueError("key must be 1-128 letters, digits, dots, underscores, or hyphens")
    return value


def _totals(rows):
    out = {"reported_usd": 0.0, "estimated_usd": 0.0,
           "simulated_usd": 0.0, "local_tokens": 0,
           "unknown_rows": 0, "unrecorded_rows": 0,
           "not_reconciled_rows": 0, "incomplete_priced_rows": 0,
           "phases": len(rows),
           "provider_calls": 0}
    for row in rows:
        basis = row["basis"]
        attempts = max(1, int(row.get("attempts") or 1))
        out["provider_calls"] += attempts
        if basis in ("reported", "estimated", "simulated"):
            if row.get("cost_usd") is None:
                out["incomplete_priced_rows"] += 1
            else:
                out[f"{basis}_usd"] += float(row["cost_usd"])
        elif basis == "local":
            out["local_tokens"] += int(row.get("input_tokens") or 0)
            out["local_tokens"] += int(row.get("output_tokens") or 0)
        elif basis in ("unknown", "unrecorded", "not-reconciled"):
            name = basis.replace("-", "_") + "_rows"
            out[name] += 1
    for field in ("reported_usd", "estimated_usd", "simulated_usd"):
        out[field] = round(out[field], 6)
    return out


def statement(key, *, runs_dir=None, costs_dir=None, history_rows=None):
    """Return exact-key user totals plus separately attributed activity."""
    key = validate_key(key)
    history = (history_rows if history_rows is not None else
               spend_history.spend_rows(runs_dir=runs_dir, costs_dir=costs_dir))
    rows = [row for row in history if row["key"] == key]
    user = [row for row in rows if (row.get("attribution") or "user") == "user"]
    non_user = [row for row in rows if (row.get("attribution") or "user") != "user"]
    return {"schema": 1, "key": key, "rows": user,
            "totals": _totals(user), "non_user_rows": non_user,
            "non_user_totals": _totals(non_user)}


DEFAULT_ROW_LIMIT = 200


def bounded(doc, limit=DEFAULT_ROW_LIMIT):
    """A transport-sized view of a statement, with the truncation SAID.

    A statement grows one row per phase per run for the life of a key and has
    no upper bound. Measured on this estate, PROJ-301 carries 1800 rows /
    808 KB — for ONE ticket — and nothing reads them: `bin/dashboard.py`
    renders `totals` only, and the row-level surfaces are the md and csv
    exports. So the JSON endpoint shipped close to a megabyte per request that
    no consumer displays, and the figure grows with run history forever.

    Truncation is only safe if the short list cannot be READ as the whole
    list. The view therefore always carries the TRUE counts and says
    `truncated`, because a spend record that silently drops line items
    under-reports what a task cost — the exact shape C13 forbids. The
    `totals` block is computed over every row and is never affected here.

    `limit=None` returns everything, for a programmatic caller that wants the
    full record. Inert below the limit: the same doc back, `truncated` False.
    """
    rows = doc.get("rows") or []
    non_user = doc.get("non_user_rows") or []
    view = dict(doc)
    view["rows_total"] = len(rows)
    view["non_user_rows_total"] = len(non_user)
    if limit is None:
        view["truncated"] = False
        return view
    cap = max(0, int(limit))
    view["rows"] = rows[:cap]
    view["non_user_rows"] = non_user[:cap]
    view["truncated"] = len(rows) > cap or len(non_user) > cap
    return view


def _money(value, prefix="$"):
    return f"{prefix}{float(value or 0):.6f}"


def to_markdown(doc):
    totals = doc["totals"]
    lines = [f"# Token-cost statement — {doc['key']}", "",
             f"- Reported: {_money(totals['reported_usd'])}",
             f"- Estimated: {_money(totals['estimated_usd'], '~$')}",
             f"- Simulated: {_money(totals['simulated_usd'], '~$')}",
             f"- Local tokens: {totals['local_tokens']}",
             f"- Unknown rows: {totals['unknown_rows']}",
             f"- Unrecorded rows: {totals['unrecorded_rows']}",
             f"- Not reconciled rows: {totals['not_reconciled_rows']}",
             f"- Incomplete priced rows: {totals['incomplete_priced_rows']}", "",
             "## User task line items", "",
             "run | mode | phase | provider | model | basis | cost | in | out | cache-read | cache-created | turns | attempts",
             "---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:"]
    for row in doc["rows"]:
        cost = "—" if row["cost_usd"] is None else f"{row['cost_usd']:.6f}"
        values = [row["run_id"], row["mode"], row["phase"], row["provider"],
                  row["model"], row["basis"], cost, row["input_tokens"],
                  row["output_tokens"], row["cache_read_tokens"],
                  row["cache_creation_tokens"], row["turns"], row["attempts"]]
        lines.append(" | ".join("—" if value is None else str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")
                                for value in values))
    if doc["non_user_rows"]:
        lines += ["", "## Non-user attributed activity (excluded from task totals)", ""]
        for row in doc["non_user_rows"]:
            lines.append(f"- {row['attribution']}: {row['run_id']} / {row['phase']} "
                         f"({row['basis']}, {row['attempts']} call(s))")
    return "\n".join(lines) + "\n"


def _csv_safe(value):
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


def to_csv(doc):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=("key", *FIELDS), lineterminator="\n")
    writer.writeheader()
    for row in (*doc["rows"], *doc["non_user_rows"]):
        writer.writerow({field: _csv_safe(doc["key"] if field == "key" else row.get(field))
                         for field in ("key", *FIELDS)})
    return stream.getvalue()


def render(doc, fmt):
    if fmt == "md":
        return to_markdown(doc)
    if fmt == "csv":
        return to_csv(doc)
    raise ValueError("format must be md or csv")


def export(key, fmt="md", out=None):
    key, fmt = validate_key(key), str(fmt or "").lower()
    if fmt not in FORMATS:
        raise ValueError("format must be md or csv")
    target = pathlib.Path(out) if out else EXPORTS / f"{key}-cost-statement.{fmt}"
    target.parent.mkdir(parents=True, exist_ok=True)
    with fs_lock.lock(target):
        tmp = target.with_name(f".{target.name}.tmp")
        try:
            tmp.write_text(render(statement(key), fmt), encoding="utf-8", newline="\n")
            fs_lock.replace_atomic(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
    return target


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("key")
    parser.add_argument("--format", choices=FORMATS)
    parser.add_argument("--out")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        doc = statement(args.key)
        if args.out or args.format:
            path = export(args.key, args.format or "md", args.out)
            print(f"exported: {path}")
        elif args.json:
            print(json.dumps(doc, indent=2))
        else:
            print(to_markdown(doc), end="")
    except (OSError, TimeoutError, ValueError) as exc:
        print(f"cost statement: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
