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

mkdir -p "$STATE"

seeded=0
while IFS= read -r rel; do
  [ -n "$rel" ] || continue
  for src in $ROOT/$rel; do              # unquoted: $rel may be a glob
    [ -e "$src" ] || continue
    dst="$STATE/${src#$ROOT/}"
    if [ -e "$dst" ]; then continue; fi  # rule 1 — the volume wins
    mkdir -p "$(dirname "$dst")"
    cp -a "$src" "$dst"
    echo "[entrypoint] seeded ${src#$ROOT/}"
    seeded=$((seeded + 1))
  done
done < <(python3 engine/lib/app_paths.py --seeded)

python3 engine/lib/app_paths.py --ensure-dirs
if [ "$seeded" -eq 0 ]; then
  echo "[entrypoint] state root already populated at $STATE — nothing seeded"
else
  echo "[entrypoint] seeded $seeded path(s) into $STATE"
fi

exec "$@"
