#!/usr/bin/env python3
"""Resolve every registered E2E repository for the testcase knowledge index.

Pipeline workspaces are preferred because they represent the revision already
under test.  A repository absent from that workspace is cloned read-only through
its registered Scm adapter into a derived, safely replaceable checkout.  Failure
is an explicit per-repository outcome, never an empty repository inference.
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
from collections.abc import Callable, Iterable

import checkout_workspace
import env_flag
import work_queue

ROOT = pathlib.Path(__file__).resolve().parents[2]
SECRET_NAMES = (
    "GITHUB_TOKEN", "BITBUCKET_TOKEN", "STASH_TOKEN", "ATLASSIAN_MCP_TOKEN",
    "ANTHROPIC_API_KEY", "EMBED_API_KEY",
)


def _spec_dir(root: pathlib.Path, entry: dict) -> pathlib.Path:
    return root / ((entry.get("layout") or {}).get("specs") or "")


def _complete_workspace(root: pathlib.Path, entry: dict) -> bool:
    """A clone is reusable only when both clone and configured spec root exist."""
    return (root.is_dir() and (root / ".git").exists()
            and _spec_dir(root, entry).is_dir())


def _sanitize(value: str) -> str:
    """Bound adapter diagnostics and remove credentials before persistence."""
    text = re.sub(r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@", r"\1[redacted]@",
                  str(value or ""))
    text = re.sub(r"(?i)\b(Bearer|Basic)\s+\S+", r"\1 [redacted]", text)
    for name in SECRET_NAMES:
        secret = os.environ.get(name, "")
        if len(secret) >= 4:
            text = text.replace(secret, "[redacted]")
    return re.sub(r"\s+", " ", text).strip()[:400]


def _exit_class(code: int) -> str:
    if code == 124:
        return "timeout"
    if code in (126, 127):
        return "adapter_unavailable"
    return f"scm_exit_{code}"


def _adapter(entry: dict, root: pathlib.Path) -> pathlib.Path:
    if env_flag.mock():
        return root / "adapters/mock/scm.sh"
    kind = str(entry.get("scm") or os.environ.get("SCM_KIND") or "github")
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", kind):
        raise ValueError(f"invalid SCM kind {kind!r}")
    path = root / "adapters/scm" / f"{kind}.sh"
    if not path.is_file():
        raise ValueError(f"SCM adapter is not installed: {kind}")
    return path


def _clone(entry: dict, target: pathlib.Path, root: pathlib.Path):
    adapter = _adapter(entry, root)
    # Python passes argv directly to Git Bash, so MSYS does not reliably rewrite
    # a native ``C:\\...`` argument. Resolve the existing parent through the
    # selected Bash runtime, then append the already-validated repository name.
    bash_parent = work_queue.git_bash_path(target.parent)
    bash_target = f"{bash_parent.rstrip('/')}/{target.name}"
    return subprocess.run(
        [work_queue.bash_exe(), str(adapter), "clone_ro", entry["name"],
         bash_target], cwd=root, capture_output=True, text=True,
        encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
        timeout=300, check=False,
    )


def resolve(entries: Iterable[dict], root: pathlib.Path = ROOT,
            clone: Callable | None = None):
    """Return ``(roots, outcomes)`` for all ``entries``.

    ``roots`` contains only repositories safe to read. ``outcomes`` contains an
    indexed/not_indexed record for every registered repository.  A clone failure
    never raises or prevents resolution of later entries.
    """
    try:
        import settings_store
        settings_store.load_env_into()
    except (ImportError, OSError, ValueError):
        pass
    root = pathlib.Path(root)
    clone = clone or _clone
    roots, outcomes = {}, {}
    for entry in entries:
        name = str(entry.get("name") or "")
        scm = "mock" if env_flag.mock() else str(
            entry.get("scm") or os.environ.get("SCM_KIND") or "github")
        workspace = root / "workspace/tests" / name
        if _complete_workspace(workspace, entry):
            roots[name] = workspace
            outcomes[name] = {"status": "indexed", "source": "workspace",
                              "scm": scm, "exit_class": "not_called",
                              "reason": ""}
            continue

        # checkout_workspace owns the narrow deletion contract. Pass the
        # derived root explicitly so concurrent callers never share mutable
        # module state and dependency-injected test estates stay isolated.
        index_root = pathlib.Path(
            os.environ.get("AIQE_INDEX_CHECKOUT_DIR")
            or root / "reports/knowledge-index/checkouts")
        try:
            target = checkout_workspace.prepare("index", name,
                                                index_root=index_root)
        except (OSError, ValueError) as exc:
            outcomes[name] = {"status": "not_indexed", "source": "scm",
                              "scm": scm, "exit_class": "checkout_prepare",
                              "reason": _sanitize(str(exc))}
            continue

        try:
            result = clone(entry, target, root)
            code = int(result.returncode)
            diagnostic = result.stderr or result.stdout or "adapter returned no detail"
        except subprocess.TimeoutExpired as exc:
            code, diagnostic = 124, f"clone timed out after {exc.timeout}s"
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            code, diagnostic = 127, str(exc)

        if code != 0:
            # A failed adapter may have created a partial tree. Remove only the
            # validated derived target so a later build cannot mistake it for a
            # successful checkout.
            try:
                checkout_workspace.prepare("index", name,
                                            index_root=target.parent)
            except OSError:
                pass
            outcomes[name] = {
                "status": "not_indexed", "source": "scm", "scm": scm,
                "exit_class": _exit_class(code), "reason": _sanitize(diagnostic),
            }
            continue
        if not _spec_dir(target, entry).is_dir():
            outcomes[name] = {
                "status": "not_indexed", "source": "scm", "scm": scm,
                "exit_class": "invalid_checkout",
                "reason": ("clone succeeded but configured specs directory is absent: "
                           f"{(entry.get('layout') or {}).get('specs') or '.'}"),
            }
            continue
        roots[name] = target
        outcomes[name] = {"status": "indexed", "source": "scm", "scm": scm,
                          "exit_class": "ok", "reason": ""}
    return roots, outcomes
