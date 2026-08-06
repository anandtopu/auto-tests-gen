#!/usr/bin/env python3
"""Tiny cross-platform advisory lock (mkdir-based — atomic on POSIX and Windows).

Guards the shared state files (reviews.json, queue.json) against concurrent
writers: multiple queue workers, the dashboard server, and CLI invocations may
mutate them at the same time. Stale locks (holder died) are broken after
STALE_S seconds.
"""
import contextlib, os, pathlib, time

STALE_S = 60
# Hard ceiling for the PID-liveness override: on Windows PIDs recycle fast, so a
# crashed holder whose PID was reused reads as "alive" forever — without this
# ceiling every waiter times out until someone deletes the dir by hand. No
# legitimate holder in this codebase runs an hour.
HARD_STALE_S = 3600
# A lock dir with NO owner file is not a working holder: the creator died inside the
# microseconds-wide mkdir->write window, or a release's rmdir failed (Windows swallows
# that). Such orphans used to be UNBREAKABLE — the stale check needs owner, so every
# later acquisition timed out (seen as "Factory reset returns error": the clear died
# on TimeoutError against reports/runs/queue.json.lock). A short grace is enough,
# because a healthy holder writes owner immediately after mkdir.
ORPHAN_GRACE_S = 5


def _pid_alive(pid):
    """Best-effort holder liveness. STALE_S (60 s) is shorter than a legitimate
    long hold (demo_data.clear rmtree'ing workspace/ can take minutes and never
    refreshes its stamp), so age alone must not break a lock whose owner process
    is still running. Unknown -> assume alive: never tear down a live writer."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if os.name == "nt":
        try:
            import ctypes
            SYNCHRONIZE = 0x00100000
            h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
                return True
            return False
        except Exception:                      # noqa: BLE001 — err on "alive"
            return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:                            # EPERM etc. — exists, not ours
        return True


@contextlib.contextmanager
def lock(path, timeout=10.0):
    """Exclusive lock named after `path` (creates `<path>.lock/`)."""
    lockdir = pathlib.Path(str(path) + ".lock")
    deadline = time.time() + timeout
    denied = None
    while True:
        try:
            lockdir.mkdir(parents=True, exist_ok=False)
            (lockdir / "owner").write_text(f"{os.getpid()} {time.time()}",
                                           encoding="utf-8")
            break
        except PermissionError as e:
            # Windows raises WinError 5 (not FileExistsError) when the lock dir
            # is in a PENDING-DELETE state — the holder just released and the
            # directory entry has not cleared yet. Measured at ~1.8% of
            # acquisitions under 6-way contention. This used to escape `lock()`
            # entirely: no retry, no timeout, just a crash inside whatever was
            # mutating a state file. Treat it as "taken, try again" and keep the
            # cause so a REAL permission problem still reports itself below
            # rather than masquerading as a timeout.
            denied = e
            if time.time() > deadline:
                raise TimeoutError(
                    f"could not acquire {lockdir}: {e}") from e
            time.sleep(0.05)
            continue
        except FileExistsError:
            try:                                   # break stale locks
                owner_txt = (lockdir / "owner").read_text(encoding="utf-8")
                stamp = float(owner_txt.split()[1])
            except (OSError, IndexError, ValueError):
                owner_txt, stamp = None, None      # ownerless orphan (see above)
            age = None if stamp is None else time.time() - stamp
            if age is not None and (age > HARD_STALE_S or
                                    (age > STALE_S and
                                     not _pid_alive(owner_txt.split()[0]))):
                # Old stamp AND (dead owner, or so old that a PID-reuse false
                # "alive" must not wedge us forever). Re-verify the owner is
                # unchanged right before breaking — a concurrent waiter may have
                # already broken and re-acquired this lock; tearing down the NEW
                # holder would give two writers.
                try:
                    if (lockdir / "owner").read_text(encoding="utf-8") == owner_txt:
                        _release(lockdir)
                except OSError:
                    pass
                # Only skip the deadline/sleep when the break actually landed:
                # an unreleasable dir (sharing violation, stray file) must fall
                # through to the timeout, not busy-spin at 100% CPU forever.
                if not lockdir.exists():
                    continue
            elif stamp is None:
                try:                               # judge the orphan by dir age
                    if time.time() - lockdir.stat().st_mtime > ORPHAN_GRACE_S:
                        _release(lockdir)
                        if not lockdir.exists():
                            continue
                except OSError:
                    pass                           # vanished between checks — retry
            if time.time() > deadline:
                raise TimeoutError(f"could not acquire {lockdir}"
                                   + (f" (last error: {denied})" if denied else ""))
            time.sleep(0.05)
    try:
        yield
    finally:
        _release(lockdir)


def _release(lockdir):
    """Drop the lock; retry only the owner unlink, never the rmdir.

    On Windows the directory entry for a just-unlinked file lingers for a few
    milliseconds, so `rmdir` immediately after `unlink("owner")` can raise
    PermissionError(WinError 32) — the classic "directory not empty right after
    emptying it". A single swallowed failure left the lockdir standing with NO
    owner file, and an ownerless orphan is only breakable after ORPHAN_GRACE_S
    (5 s), so every waiter spun and callers with the default 10 s timeout
    started raising TimeoutError. Reproduced by tests/state-adversarial.sh:
    one WinError 32 turned into six timed-out writers and 77 lost decisions.

    The marker retry is bounded at ~50 ms. A genuinely undeletable directory
    still falls through to the stale-break path rather than blocking here.
    """
    _unlink_owner(lockdir)
    try:
        lockdir.rmdir()
    except OSError:
        # Windows can refuse the rmdir for a few ms after the unlink
        # (WinError 32). Retrying here was MEASURED and rejected: it made
        # contention worse (8/8 trials losing decisions vs 7/8), because a
        # retry can outlive our ownership and delete a lock a waiter has
        # since acquired. The orphan-grace path in lock() recovers instead.
        pass


def _unlink_owner(lockdir, attempts=5, pause=0.01):
    """Remove our owner marker past transient Windows sharing violations.

    Retrying the marker is safe: nobody can legitimately acquire the lock
    while the directory still contains it.  The directory removal itself must
    remain single-shot because a delayed retry could delete a successor's lock.
    """
    for attempt in range(attempts):
        try:
            (lockdir / "owner").unlink(missing_ok=True)
            return True
        except OSError:
            if attempt + 1 < attempts:
                time.sleep(pause)
    return False


# --------------------------------------------------------------------------
# Torn-write protection for the shared JSON state files.
#
# Every state store (plan lifecycle, review board, work queue, OpenHands trace)
# used to write DIRECTLY to its final path. A crash mid-write — OOM kill, pod
# eviction, power loss — leaves a truncated file, and what happened next depended
# on the loader:
#
#   * loaders that swallowed JSONDecodeError returned {} ... and the next save
#     OVERWROTE the file with empty state. Human plan approvals silently vanished.
#   * loaders that did not catch took the review board, queue and wizard down
#     until someone hand-edited the file.
#
# Both are the same root cause, fixed the same way: write to a tmp file in the
# SAME directory and os.replace() it over the target (atomic on POSIX and on
# Windows for same-volume renames) — a crash at any instant leaves either the
# old complete file or the new complete file, never a torn one. And if a corrupt
# file is met anyway (pre-fix damage, disk fault), QUARANTINE it — rename it
# aside with a timestamp so the bytes survive for recovery and the event is
# visible — instead of silently treating it as empty.

def replace_atomic(tmp, dest, attempts=12, pause=0.05):
    """os.replace(tmp, dest), retried past Windows' transient PermissionError.

    On Windows a rename fails with WinError 5 while ANY handle to the
    destination is open — another reader, an AV scanner, the indexer, or a
    concurrent dashboard render that is reading the file this very moment. It
    is transient and clears in milliseconds, but a bare os.replace turns it
    into a lost write.

    Observed here: `save_and_verify` failed mid-suite with
    "Access is denied: repo-registry.yaml.tmp -> repo-registry.yaml", which
    would mean a repo add or edit silently not landing — on the file that
    routes every run. The same hazard is already documented for `mkdir` in this
    module (a PENDING-DELETE lock dir raises PermissionError, not
    FileExistsError); this is the rename half of it.

    Raises if it never succeeds: a write that did not happen must not be
    reported as one.
    """
    last = None
    for i in range(attempts):
        try:
            os.replace(tmp, dest)
            return
        except PermissionError as e:       # Windows only; POSIX renames win
            last = e
            time.sleep(pause * (i + 1))    # brief, widening backoff
    raise last


def write_json_atomic(path, obj, **dump_kw):
    """Write JSON so a crash can never leave a truncated file."""
    import json
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    dump_kw.setdefault("indent", 2)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(obj, fh, **dump_kw)
            fh.write("\n")
        replace_atomic(tmp, path)
    finally:
        # A failure between write and replace must not litter tmp files that a
        # later glob or bundle export would sweep up.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def read_json_guarded(path, default):
    """Load a state file; quarantine a corrupt one instead of losing it.

    Returning `default` for a corrupt file while LEAVING it in place is the silent
    data-loss path (the next save overwrites real data); raising takes every caller
    down. Quarantining threads the needle: callers keep working from `default`, the
    damaged bytes are preserved as <name>.corrupt-<ts> for manual recovery, and the
    event is loudly visible on stderr instead of nowhere.
    """
    import json, sys
    path = pathlib.Path(path)
    if not path.exists():
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        quarantine = path.with_name(f"{path.name}.corrupt-{stamp}")
        try:
            # Retried too: the corrupt file is one somebody just failed to read,
            # so a lingering handle is exactly the likely case here.
            replace_atomic(path, quarantine)
            print(f"[fs_lock] {path.name} was corrupt (torn write?) — moved to "
                  f"{quarantine.name}; continuing from empty state. Recover by "
                  f"inspecting that file.", file=sys.stderr)
        except OSError:
            print(f"[fs_lock] {path.name} is corrupt and could not be quarantined — "
                  f"continuing from empty state WITHOUT overwriting it.",
                  file=sys.stderr)
        return default
    except OSError as e:
        # The file EXISTS (checked above) but could not be read — a sharing
        # violation, EIO, a full disk. Returning `default` here is the same
        # silent data-loss path this function was written to close for a
        # corrupt file: the caller reads "empty", saves, and a human's plan
        # approval or review decision is gone. Verified: one transient OSError
        # turned an approved plan into {}.
        #
        # Transient causes clear in milliseconds, so retry briefly; if it still
        # cannot be read, RAISE. A loud failure on a surface is recoverable —
        # a silently emptied state file is not.
        for _ in range(4):
            time.sleep(0.05)
            try:
                with open(path, encoding="utf-8") as fh:
                    return json.load(fh)
            except json.JSONDecodeError:
                return read_json_guarded(path, default)   # quarantine path
            except OSError:
                continue
        raise OSError(
            f"{path} exists but could not be read ({e}) — refusing to report it "
            f"as empty, because the next save would overwrite it") from e
