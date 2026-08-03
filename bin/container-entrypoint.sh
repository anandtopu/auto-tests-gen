#!/usr/bin/env bash
# First-boot seeding for a relocated state root (R12, docs/review-readonly-rootfs.md).
#
# With readOnlyRootFilesystem the image tree is immutable, so every mutable path
# is redirected to AIQE_STATE_DIR (a volume). Three of those paths SHIP CONTENT
# in the image and would otherwise start empty on a new deployment: the catalog
# mappings, the repo registry, and the knowledge base. This copies them in ONCE.
#
# Two rules make this safe, and both matter:
#
#   1. NEVER overwrite. Once the volume has a path, the volume is authoritative
#      — it holds a human's edits (mappings confirmed in review, repos added
#      through Settings, curated guidance). Re-seeding on every start would
#      silently revert somebody's work on a routine pod restart.
#   2. Seed DATA ONLY, never code or config. `catalog/bootstrap/*.py`,
#      `catalog/schema.json` and `registry/org-config.yaml` deliberately stay in
#      the image and are read from there, so an image upgrade actually ships new
#      logic. Copying them into the volume is precisely the freeze this whole
#      design exists to avoid.
#
# Generated directories (testplans/, testdata/, specs/, generated skills) are
# NOT seeded — they are created empty by app_paths.ensure_dirs(). Seeding a
# generated path would restore a stale plan over an empty volume and call it
# state.
set -euo pipefail
ROOT="${APP_HOME:-/app}"
cd "$ROOT"

STATE="$(printf '%s' "${AIQE_STATE_DIR:-}" | tr -d '[:space:]')"
if [ -z "$STATE" ]; then
  # No relocation configured: the checkout is the state root, exactly as in
  # development. Nothing to seed.
  exec "$@"
fi

# Was the state root already carrying anything BEFORE we touched it? This is
# the fact the summary needs and could not previously tell apart from "we
# copied nothing": on a first boot where seeding silently did nothing, the old
# code reported "already populated" about a directory it had just created
# empty, and the deployment came up with no mappings, no registry and no
# knowledge — looking healthy.
preexisting=0
if [ -d "$STATE" ] && [ -n "$(ls -A "$STATE" 2>/dev/null)" ]; then
  preexisting=1
fi
mkdir -p "$STATE"

# Command substitution, not `< <(...)`: process substitution discards the
# producer's exit status, so a crashing app_paths yielded an empty list that
# read exactly like "there is nothing to seed". `tr -d '\r'` because python
# emits CRLF on some hosts and `$ROOT/catalog/*.jsonl<CR>` matches no file —
# every path was skipped and the boot still claimed success.
if ! SEED_LIST=$(python3 engine/lib/app_paths.py --seed-plan | tr -d '\r'); then
  echo "[entrypoint] FATAL: could not read the seed plan from" \
       "engine/lib/app_paths.py --seed-plan. Refusing to start: an unseeded" \
       "state root has no catalog, no registry and no knowledge, and every" \
       "routing decision would silently resolve to nothing." >&2
  exit 1
fi

# --seed-plan returns individual FILES, already expanded and filtered. This
# loop copies and decides nothing: when it expanded the globs itself and copied
# whole directories, `catalog/review` dragged export_review_queue.py and a
# __pycache__ into the volume, and `knowledge/facts` dragged the derived tier —
# each contradicting a rule stated at the top of this file and pinned, in
# string form only, on the other side of the boundary.
seeded=0; missing=""
while IFS= read -r rel; do
  [ -n "$rel" ] || continue
  src="$ROOT/$rel"
  if [ ! -e "$src" ]; then
    # In the IMAGE but gone by boot: a build defect, not an empty estate.
    missing="$missing $rel"
    continue
  fi
  dst="$STATE/$rel"
  [ -e "$dst" ] && continue              # rule 1 — the volume wins
  mkdir -p "$(dirname "$dst")"
  cp -a "$src" "$dst"
  echo "[entrypoint] seeded $rel"
  seeded=$((seeded + 1))
done <<EOF
$SEED_LIST
EOF

# An EMPTY plan means the image shipped none of the seed paths at all — a
# different fact from "every path was already present in the volume".
if [ -z "$(printf '%s' "$SEED_LIST" | tr -d '[:space:]')" ]; then
  echo "[entrypoint] WARNING: the seed plan is empty — the image contains none" \
       "of the paths in app_paths.SEEDED. Nothing can be seeded." >&2
fi

python3 engine/lib/app_paths.py --ensure-dirs

[ -n "$missing" ] && echo "[entrypoint] WARNING: no file in the image matched:$missing" >&2
if [ "$seeded" -gt 0 ]; then
  echo "[entrypoint] seeded $seeded path(s) into $STATE"
elif [ "$preexisting" -eq 1 ]; then
  echo "[entrypoint] state root already populated at $STATE — nothing to seed"
else
  # Empty state root AND nothing copied. Not fatal — the dashboard coming up is
  # how an operator investigates, and a crash-loop hides the reason. But it must
  # never read as a normal boot (constitution C13).
  echo "[entrypoint] WARNING: state root $STATE was EMPTY and nothing was" \
       "seeded. This deployment has no catalog mappings and no repo registry," \
       "so routing will resolve to nothing. Check the image contains the paths" \
       "listed by 'python3 engine/lib/app_paths.py --seeded'." >&2
fi

exec "$@"
