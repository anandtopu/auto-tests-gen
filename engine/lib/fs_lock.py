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
    while True:
        try:
            lockdir.mkdir(parents=True, exist_ok=False)
            (lockdir / "owner").write_text(f"{os.getpid()} {time.time()}",
                                           encoding="utf-8")
            break
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
                raise TimeoutError(f"could not acquire {lockdir}")
            time.sleep(0.05)
    try:
        yield
    finally:
        _release(lockdir)


def _release(lockdir):
    try:
        (lockdir / "owner").unlink(missing_ok=True)
        lockdir.rmdir()
    except OSError:
        pass
