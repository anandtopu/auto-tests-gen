#!/usr/bin/env python3
"""Catalog slice — the existing-test knowledge a run actually needs.

`out/catalog-slice.jsonl` was named a slice and built as a CONCATENATION: every
`catalog/*.jsonl` in the estate, handed to every phase of every run. A PR that
resolves one API test repo still received the UI repo's rows. Harmless on a
demo estate; on the multi-repo estate this platform targets it is both token
cost and dilution — and it contradicts the fan-out design, where each per-repo
generate agent deliberately sees only ITS repo's conventions.

Relevance is decided by the SAME mapping that routed the run (`covers:`):

  * rows from a test repo this run resolved — what already exists where I am
    about to write, so generation extends instead of duplicating;
  * rows whose mapping covers a source repo this run touched, from ANY test
    repo — so the agent knows the surface is already covered elsewhere and
    does not re-test it in a second place.

FALLBACK IS ALWAYS THE FULL CATALOG. A filter that silently empties the
existing-test context would make generation duplicate work it cannot see, so
an empty or unreadable selection falls back to everything and says so on
stderr. Starving the phase is worse than over-feeding it.

CLI:
  catalog_slice.py <resolve_contract.json> [target_repo] > out/catalog-slice.jsonl
"""
import glob
import json
import pathlib
import sys
import app_paths                      # R12: mutable paths resolve here

ROOT = pathlib.Path(__file__).resolve().parents[2]
SAMPLE = "catalog.sample.jsonl"       # the committed example, excluded like every reader


def load_rows(catalog_dir=None, drops=None):
    """Every catalog row in the estate.

    `drops` is an optional dict the caller passes in to learn what was NOT
    read: {"lines": n, "files": n, "detail": [...]}. Skipping is the right
    policy here — a torn row must not starve the generate phase of the whole
    catalog — but an uncounted skip means the phase is handed a short view of
    existing tests and nobody, including the agent, can tell.
    """
    d = pathlib.Path(catalog_dir) if catalog_dir else app_paths.catalog_dir(ROOT)
    out = []
    for f in sorted(glob.glob(str(d / "*.jsonl"))):
        if pathlib.Path(f).name == SAMPLE:
            continue
        try:
            for line in open(f, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    # One bad line never drops the file — but it is COUNTED.
                    # This slice is the existing-test context handed to the
                    # generate phase; silently short, it makes the agent
                    # re-author coverage it cannot see, and duplicate tests are
                    # the one outcome this file exists to prevent. A skip that
                    # nobody counts is indistinguishable from a catalog that
                    # never had the row.
                    if drops is not None:
                        drops["lines"] = drops.get("lines", 0) + 1
                    continue
        except OSError as exc:
            # A whole repo's catalog vanishing is far worse than one row, and
            # it was the same silent `continue`.
            if drops is not None:
                drops["files"] = drops.get("files", 0) + 1
                drops.setdefault("detail", []).append(
                    f"{pathlib.Path(f).name}: {type(exc).__name__}")
            continue
    return out


def is_relevant(row, test_repos, source_repos, target_repo=""):
    if not isinstance(row, dict):
        return False
    repo = str(row.get("test_repo") or "")
    covered = set((row.get("mapping") or {}).get("app_repos") or [])
    if target_repo:
        # Fan-out: this agent writes into ONE repo. Its own rows, plus anything
        # covering the same app surface elsewhere.
        return repo == target_repo or bool(covered & set(source_repos))
    return repo in set(test_repos) or bool(covered & set(source_repos))


def slice_rows(rows, test_repos=(), source_repos=(), target_repo=""):
    """(selected_rows, fell_back). Never returns an empty selection."""
    if not test_repos and not source_repos:
        return rows, True
    sel = [r for r in rows
           if is_relevant(r, test_repos, source_repos, target_repo)]
    if not sel:
        return rows, True
    return sel, False


def main(argv):
    sys.stderr.reconfigure(encoding="utf-8")
    if not argv:
        print(__doc__, file=sys.stderr)
        return 64
    contract, target = argv[0], (argv[1] if len(argv) > 1 else "")
    try:
        c = json.load(open(contract, encoding="utf-8"))
        test_repos = list(c.get("test_repos") or [])
        source_repos = list(c.get("source_repos") or [])
    except Exception as e:
        # No resolution to filter by -> the full catalog, loudly.
        print(f"[catalog-slice] {contract} unreadable ({e}) — using the full "
              f"catalog", file=sys.stderr)
        test_repos = source_repos = []
    drops = {}
    rows = load_rows(drops=drops)
    sel, fell_back = slice_rows(rows, test_repos, source_repos, target)
    for r in sel:
        sys.stdout.write(json.dumps(r) + "\n")
    # Say what could NOT be read, before saying what was selected — otherwise
    # "3/3 row(s) relevant" reads as the whole catalog when two rows were
    # dropped on the way in. The counts make the difference visible in the run
    # log, where a reviewer wondering why a duplicate test appeared will look.
    if drops:
        parts = []
        if drops.get("files"):
            parts.append(f"{drops['files']} catalog file(s) UNREADABLE "
                         f"({'; '.join(drops.get('detail', []))})")
        if drops.get("lines"):
            parts.append(f"{drops['lines']} malformed row(s) skipped")
        print(f"[catalog-slice] INCOMPLETE INPUT: {', '.join(parts)} — the "
              f"existing-test context below is short by that much, so "
              f"generation may re-author coverage it cannot see",
              file=sys.stderr)
    scope = f"target={target}" if target else f"repos={','.join(test_repos) or '-'}"
    if fell_back:
        print(f"[catalog-slice] no rows matched ({scope}) — handing over the "
              f"full catalog ({len(rows)} row(s)) rather than starving the "
              f"phase", file=sys.stderr)
    else:
        print(f"[catalog-slice] {len(sel)}/{len(rows)} row(s) relevant to "
              f"{scope}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
