#!/usr/bin/env python3
"""Prepare the exact derived-workspace checkout used by a pipeline clone.

The CLI intentionally accepts a workspace kind and repository name rather than
an arbitrary path.  This makes cleanup repeatable without turning a registry or
resolve-contract value into a broad recursive-delete primitive.
"""
import pathlib
import re
import shutil
import stat
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "workspace"
SAFE_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _remove_readonly(func, path, _exc):
    """Git object files can be read-only on Windows; make only the exact
    failing entry writable and retry the deletion operation."""
    pathlib.Path(path).chmod(stat.S_IWRITE)
    func(path)


def checkout_path(kind: str, repo: str) -> pathlib.Path:
    if kind not in {"src", "tests"}:
        raise ValueError("workspace kind must be src or tests")
    if not SAFE_REPO.fullmatch(repo) or repo in {".", ".."}:
        raise ValueError(f"invalid repository name: {repo!r}")
    base = (WORKSPACE / kind).resolve()
    target = base / repo
    if target.is_symlink():
        raise ValueError(f"checkout target must not be a symlink: {target}")
    if target.resolve(strict=False).parent != base:
        raise ValueError(f"checkout target escapes workspace: {target}")
    return target


def prepare(kind: str, repo: str) -> pathlib.Path:
    target = checkout_path(kind, repo)
    for attempt in range(5):
        try:
            if target.exists():
                shutil.rmtree(target, onerror=_remove_readonly)
            break
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.2 * (attempt + 1))
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3 or args[0] != "prepare":
        print("usage: checkout_workspace.py prepare <src|tests> <repo>", file=sys.stderr)
        return 64
    try:
        print(prepare(args[1], args[2]))
        return 0
    except (OSError, ValueError) as exc:
        print(f"CHECKOUT_PREPARE_FAILED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
