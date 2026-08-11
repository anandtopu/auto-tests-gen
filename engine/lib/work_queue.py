#!/usr/bin/env python3
"""Manual work queue: PR / JIRA items queued from the dashboard (or CLI) and
processed sequentially through engine/pipeline.sh.

Store: reports/runs/queue.json — [{id, mode, target, pr, ticket, release,
issue_type, components, labels, fix_version, requested_by,
status: queued|running|done|failed, ts, finished, exit_code}]. Ticket attributes
are fetch-time display provenance only; the runner always refetches `get_item`.
Run-record globs must skip this file (like reviews.json).

CLI:
  work_queue.py add <pr|jira> <target> [pr_number] [release] [requested_by]
  work_queue.py list
  work_queue.py run             process every queued item (AIQE_MOCK=1 unless set)
  work_queue.py requeue <id>    put a failed item back in the queue
  work_queue.py remove  <id>    delete a non-running item from the queue
"""
import json, os, pathlib, shutil, subprocess, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fs_lock

ROOT = pathlib.Path(__file__).resolve().parents[2]
FILE = pathlib.Path(os.environ.get("AIQE_QUEUE_FILE", ROOT / "reports/runs/queue.json"))


def bash_exe():
    """Git Bash, never WSL's System32 bash.exe (which needs a WSL distro)."""
    if os.environ.get("AIQE_BASH"):
        return os.environ["AIQE_BASH"]
    if os.name != "nt":
        return "bash"
    w = shutil.which("bash")
    if w and "system32" not in w.lower():
        return w
    git = shutil.which("git")
    if git:
        p = pathlib.Path(git).resolve().parents[1] / "bin" / "bash.exe"
        if p.exists():
            return str(p)
    for p in (r"C:\Program Files\Git\bin\bash.exe",
              r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if pathlib.Path(p).exists():
            return p
    return "bash"


def git_bash_path(path, env=None):
    """Return ``path`` in the mount syntax used by the selected Git Bash.

    ``Path.as_posix()`` leaves a Windows drive colon (``C:/...``), which Bash
    interprets as a PATH separator. Asking the runtime itself also handles
    non-default MSYS mounts and UNC paths.
    """
    result = subprocess.run(
        [bash_exe(), "-c", "pwd"], cwd=path, env=env or os.environ.copy(),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, check=True,
    )
    return result.stdout.strip()


def git_bash_env(prepend=(), env=None, **extra):
    """Build an environment whose PATH is valid inside Git Bash.

    Python tests start with a native, semicolon-delimited Windows PATH. Merely
    prepending a temporary stub directory produces a mixed PATH and lets real
    network tools bypass the stub. Normalize both the inherited PATH and every
    prepended directory through the same Bash runtime.
    """
    result_env = dict(os.environ if env is None else env)
    runtime = subprocess.run(
        [bash_exe(), "-c", 'printf "%s" "$PATH"'], env=result_env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, check=True,
    ).stdout
    prefixes = [git_bash_path(pathlib.Path(p).resolve(), result_env)
                for p in prepend]
    result_env["PATH"] = ":".join([*prefixes, runtime])
    result_env.update({k: str(v) for k, v in extra.items()})
    return result_env


def git_bash_command(script, *args, prepend=(), env=None, **extra):
    """Return ``(argv, env)`` for a script with a deterministic Bash PATH.

    Git for Windows prepends its own tool directories while starting Bash,
    even when the supplied environment already contains a valid POSIX PATH.
    Assigning PATH after that startup step is the only reliable way for test
    doubles (or other explicit tool overrides) to win command lookup.
    """
    result_env = git_bash_env(prepend, env, **extra)
    result_env["AIQE_BASH_PATH"] = result_env["PATH"]
    argv = [
        bash_exe(), "-c",
        'PATH="$AIQE_BASH_PATH"; export PATH; exec "$BASH" "$@"',
        "aiqe-git-bash", str(script), *(str(arg) for arg in args),
    ]
    return argv, result_env


def load():
    # Guarded: corrupt -> quarantined + empty queue, not a crash (see fs_lock).
    return fs_lock.read_json_guarded(FILE, [])


def save(items):
    fs_lock.write_json_atomic(FILE, items)


def key_of(item):
    """Stable workflow key. A PR plan is still keyed by the PR, not its repo."""
    return (f"PR-{item['target']}-{item['pr']}"
            if item["mode"] == "pr" or
            (item["mode"] == "plan" and item.get("pr"))
            else item["target"])


def _pr_plan_enabled():
    """S5 is opt-in and fail-closed for unrecognized values."""
    return os.environ.get("AIQE_PR_PLAN", "0").strip().lower() in \
        ("1", "true", "yes", "on")


def _ticket_metadata(issue_type="", components=None, labels=None, fix_version=""):
    """Bound untrusted fetch-time display fields before durable queue storage."""
    def text(name, value, limit=200):
        if value is None:
            return ""
        if not isinstance(value, str):
            sys.exit(f"{name} must be a string")
        value = value.strip()
        if len(value) > limit:
            sys.exit(f"{name} is too long (max {limit} characters)")
        return value

    def names(name, values):
        if values is None:
            return []
        if not isinstance(values, list):
            sys.exit(f"{name} must be a list")
        if len(values) > 50:
            sys.exit(f"{name} has too many values (max 50)")
        cleaned = []
        for value in values:
            value = text(name, value, 100)
            if value:
                cleaned.append(value)
        return cleaned

    return {
        "issue_type": text("issue_type", issue_type, 100),
        "components": names("components", components),
        "labels": names("labels", labels),
        "fix_version": text("fix_version", fix_version),
    }


def add(mode, target, pr=None, release="", requested_by="", inline_file=None,
        force=False, ticket=None, issue_type="", components=None, labels=None,
        fix_version=""):
    # "tests" resumes generation from an approved test plan (pipeline.sh tests <KEY>)
    if mode not in ("pr", "jira", "plan", "tests"):
        sys.exit("mode must be pr|jira|plan|tests")
    pr_target = mode == "pr" or (mode == "plan" and bool(pr))
    if mode == "pr" and not pr:
        sys.exit("pr mode needs a PR number")
    if mode == "plan" and pr and not _pr_plan_enabled():
        sys.exit("plan-first from PR is disabled — set AIQE_PR_PLAN=1 to enable "
                 "the opt-in S5 workflow")
    # ...and it must BE a PR number. `target` was validated against the registry
    # right below with the comment "Validate at INTAKE, not minutes later in a
    # background runner nobody watches" — but `pr` was not validated at all, so
    # `-1`, `0` and a 200-digit string all queued 200 OK. The key becomes
    # PR-<repo>-<pr>, which passes the pipeline's charset check (digits and `-`
    # are legal), so the run starts and dies at the SCM call with whatever the
    # vendor says about a pull request that cannot exist — minutes later, in a
    # background process, for input that was wrong at the moment it was typed.
    # Every SCM we speak numbers PRs from 1 (pr_url.py parses `num` out of the
    # URL as digits), so this is the whole domain, not a guess at a limit.
    if pr_target:
        import re as _re
        if not _re.fullmatch(r"[1-9][0-9]{0,8}", str(pr).strip()):
            sys.exit(f"'{pr}' is not a pull-request number — PRs are numbered "
                     f"from 1 (e.g. 201). Paste the PR URL instead and the "
                     f"number is taken from it.")
    # Validate at INTAKE, not minutes later in a background runner nobody watches.
    # The pasted-URL path already refuses an unregistered repo with a hint; the
    # plain name+number path (wizard form, API, TaskEvent webhook) must match it.
    if pr_target:
        try:
            import repo_admin
            registered = repo_admin.is_registered(target)
        except Exception:
            registered = True             # registry unreadable — let the run report it
        if not registered:
            sys.exit(f"'{target}' is not a registered repository — add it in "
                     f"Repositories first, or paste the PR URL (it carries the "
                     f"repo and, on Stash, the project key)")
        if ticket:
            import ticket_discovery
            normalized = ticket_discovery.normalize_explicit(ticket)
            if normalized is None:
                sys.exit(f"'{ticket}' is not one bare JIRA key (e.g. PROJ-301)")
            ticket = normalized
    else:
        if ticket:
            sys.exit("an explicit PR ticket can only be supplied in pr mode or a PR plan")
        # Same charset the pipeline enforces (INVALID_KEY, exit 64) — fail here
        # with a message instead of queueing work that dies on arrival.
        import re
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", str(target or "")):
            sys.exit(f"'{target}' is not a valid ticket key — letters, digits, "
                     f". _ - only (e.g. PROJ-123)")
    if mode == "plan" and not force:
        # Re-authoring an APPROVED plan resets it to draft — a human sign-off is
        # destroyed by one click of "Author test plan" on a key that already went
        # through review. Refuse with the alternatives; `force` is the deliberate
        # override (and `make plan` via the CLI is unaffected — that path is the
        # documented tool for an intentional re-author).
        try:
            import plan_state
            plan_key = f"PR-{target}-{pr}" if pr else target
            if plan_state.get(plan_key).get("status") == "approved":
                sys.exit(f"the test plan for {plan_key} is APPROVED — re-authoring "
                         f"would reset it to draft and destroy the sign-off. Read "
                         f"it (make plan-show KEY={target}), edit it (which "
                         f"deliberately revokes approval), or pass force=true to "
                         f"re-author anyway.")
        except SystemExit:
            raise
        except Exception:
            pass                     # no plan state readable — nothing to protect
    metadata = _ticket_metadata(issue_type, components, labels, fix_version)
    with fs_lock.lock(FILE):
        items = load()
        sig = (mode, target, str(pr or ""), str(ticket or ""))
        for it in items:
            if (it["mode"], it["target"], str(it.get("pr") or ""),
                    str(it.get("ticket") or "")) == sig \
                    and it["status"] in ("queued", "running"):
                return it, False                   # already pending — dedupe
        base, n = f"q{int(time.time())}", len(items) + 1
        while any(i["id"] == f"{base}-{n}" for i in items):   # ids must be unique
            n += 1                                            # even after removals
        item = {"id": f"{base}-{n}", "mode": mode,
                "target": target, "pr": str(pr) if pr else None, "release": release,
                "requested_by": requested_by, "status": "queued", "ts": time.time(),
                "finished": None, "exit_code": None,
                "inline_file": str(inline_file) if inline_file else None}
        # Preserve byte-compatible flag-off/CLI records; fetched S2 tickets carry
        # at least one value and therefore persist the complete provenance block.
        if any(metadata.values()):
            item.update(metadata)
        if ticket:
            item["ticket"] = ticket
        warning = _envelope_warning(mode, target, pr)
        if warning:
            item["warning"] = warning
        items.append(item)
        save(items)
    return item, True


def _envelope_warning(mode, target, pr=None):
    """A WARNING (never a refusal) when this key's measured spend history
    already exceeds its workflow envelope (cost-reduction 5.2) — the human
    queueing it should know they are re-running an expensive key. Best-effort:
    no telemetry, no envelope, or any failure means no warning."""
    try:
        import budget
        cap, base, review_uplift = budget.workflow_envelope(mode)
        if cap <= 0:
            return ""
        import cost_report
        key = f"PR-{target}-{pr}" if pr and mode in ("pr", "plan") else str(target)
        for e in cost_report.report(None).get("by_key_top10", []):
            # MEASURED spend only. The docstring above has always said measured
            # and the code compared the total, so on a mock-heavy estate a
            # simulated history drove a prediction about a real run: measured
            # here, PR-orders-api-201 carried $12.00 of simulated spend against
            # a $1.50 pr envelope, and every operator queueing that key was
            # told to expect degradation or abort on evidence no money backed.
            # A simulated figure may inform a trend; it must never drive a
            # warning about what a real run will do.
            if e.get("key") == key and e.get("measured_usd", 0) > cap:
                review_note = (f" = ${base:.2f} base + "
                               f"${review_uplift:.2f} agent-review uplift"
                               if review_uplift else "")
                return (f"this key's MEASURED spend history "
                        f"(${e['measured_usd']:.2f}) already "
                        f"exceeds the effective {mode} envelope (${cap:.2f}"
                        f"{review_note}) — expect the "
                        f"run to degrade or abort")
    except Exception:
        pass
    return ""


def _mark(items, item, **kw):
    item.update(kw)
    save(items)


# The pipeline's exit codes are a documented contract (architecture §5.8) — turn each
# into something a human can act on rather than a bare number.
EXIT_MEANING = {
    64: "bad key or mode — the target was rejected before any work started",
    75: "another run holds the pipeline lock — retry when it finishes",
    77: "budget exceeded (cost or wall-clock) — the run aborted before the gate",
    2: "gate: a change fell outside the test repo's allowed scope, or a filename "
       "used unsafe characters",
    3: "gate: the secret/PII scan rejected the generated content",
    4: "gate: a generated spec had no catalog sidecar (not born-mapped)",
    5: "gate: the generated tests did not pass when executed",
    6: "gate: the target directory is not a standalone git repository",
    7: "gate: the push failed against the remote",
}

# Lines worth surfacing verbatim: the platform's own machine-readable failures and the
# adapters' actionable errors. Matched case-sensitively — they are emitted, not typed.
_SIGNALS = ("NO_STASH_PROJECT", "PIPELINE_BUSY", "BUDGET_EXCEEDED", "INVALID_KEY",
            "INVALID_MODE", "PLAN_SNAPSHOT_MISSING", "not approved",
            "needs_clarification", "SDD_REFUSAL[", "GATE_STATUS=", "clone failed", "fatal:",
            "HTTP 4", "HTTP 5", "Failed to authenticate", "error:")


def failure_reason(exit_code, stdout="", stderr="", limit=400):
    """A short, human-actionable explanation of why a queued run failed.

    Prefers a line the platform deliberately emitted (an adapter's NO_STASH_PROJECT
    beats "exit 3" every time); falls back to the exit code's documented meaning, then
    to the last non-empty output line. Always returns something non-empty for a
    non-zero exit — "we don't know" is still better said than left blank.
    """
    text = f"{stdout or ''}\n{stderr or ''}"
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    for line in reversed(lines):                    # most recent signal wins
        if any(sig in line for sig in _SIGNALS):
            # Shared SDD contracts are already bounded and must reach CLI and
            # UI byte-for-byte. The ordinary log-line limit remains unchanged.
            return line[:1000] if "SDD_REFUSAL[" in line else line[:limit]

    meaning = EXIT_MEANING.get(exit_code)
    if meaning:
        return f"exit {exit_code} — {meaning}"
    if lines:
        return f"exit {exit_code} — {lines[-1][:limit]}"
    return f"exit {exit_code} — no output captured"


def requeue(item_id, force=False):
    """Put a failed item back in the queue. Also the recovery path for an item
    stranded in `running` by a crashed worker — nothing else can transition it.

    RATE LIMITED. A retry is a full pipeline run: clones, an LLM call per phase,
    possibly a commit. Unbounded, one stuck UI or one impatient loop spends real
    money re-running a request that fails the same way every time. The refusal
    names which limit it hit and when the next attempt is allowed
    (engine/lib/retry_policy.py).

    THE PREVIOUS FAILURE IS KEPT. This used to clear exit_code and finished, so
    the third attempt looked exactly like the first and nothing recorded that it
    had failed before — which also made any limit unenforceable. `attempts` and
    `last_error` now survive the retry, because "this has failed twice already"
    is the single most useful thing to know before pressing it again.
    """
    import retry_policy
    with fs_lock.lock(FILE):
        items = load()
        item = next((i for i in items if i["id"] == item_id), None)
        if item is None:
            sys.exit(f"no such queue item: {item_id}")
        if item["status"] not in ("failed", "running"):
            sys.exit(f"only failed (or stranded running) items can be re-queued "
                     f"({item_id} is {item['status']})")
        key = key_of(item)
        if not force:
            verdict = retry_policy.check(key)
            if not verdict["allowed"]:
                sys.exit(f"RETRY_RATE_LIMITED: {verdict['reason']}")
        prev_error = item.get("error")
        prev_code = item.get("exit_code")
        _mark(items, item, status="queued", finished=None, exit_code=None,
              ts=time.time(),
              attempts=int(item.get("attempts") or 1) + 1,
              last_error=prev_error or item.get("last_error"),
              last_exit_code=prev_code if prev_code is not None
              else item.get("last_exit_code"))
    retry_policy.record(key)
    return item


def remove(item_id):
    """Delete a queued, failed, or done item; a running item cannot be removed."""
    with fs_lock.lock(FILE):
        items = load()
        item = next((i for i in items if i["id"] == item_id), None)
        if item is None:
            sys.exit(f"no such queue item: {item_id}")
        if item["status"] == "running":
            sys.exit(f"{item_id} is running - wait for it to finish")
        save([i for i in items if i["id"] != item_id])
    return item


def prune_done(keep=50):
    """Data retention for the queue HISTORY: keep the newest `keep` done items,
    drop the rest (and their now-useless inline ticket files). Pending, running
    and failed items are never touched — they are work, not history. The run
    records a done item produced live in reports/runs/ and have their own
    retention (qa.py prune)."""
    with fs_lock.lock(FILE):
        items = load()
        done = sorted((i for i in items if i.get("status") == "done"),
                      key=lambda i: i.get("finished") or i.get("ts") or 0,
                      reverse=True)
        doomed = done[keep:]
        doomed_ids = {i["id"] for i in doomed}
        for i in doomed:
            f = i.get("inline_file")
            if f:
                pathlib.Path(f).unlink(missing_ok=True)
        save([i for i in items if i["id"] not in doomed_ids])
    return {"kept": min(len(done), keep), "removed": len(doomed)}


def run_all():
    """Process queued items in order. Mock mode unless AIQE_MOCK is set by the caller."""
    env = {**os.environ}
    env.setdefault("AIQE_MOCK", "1")
    processed = 0
    while True:
        with fs_lock.lock(FILE):                   # claim atomically: multiple workers
            items = load()                         # may drain the same queue
            item = next((i for i in items if i["status"] == "queued"), None)
            if item is not None:
                _mark(items, item, status="running")
        if item is None:
            break
        pipeline_args = [item["mode"], item["target"]]
        if item["mode"] == "pr" or (item["mode"] == "plan" and item.get("pr")):
            pipeline_args.append(item["pr"])
        item_env = {**env}
        if item.get("inline_file"):                # pasted JIRA context, not a real ticket
            item_env["AIQE_INLINE_FILE"] = item["inline_file"]
        if item.get("ticket"):                     # explicit PR -> ticket linkage (A1)
            item_env["AIQE_PR_TICKET"] = item["ticket"]
        # Normalize PATH inside Git Bash before invoking the pipeline.  Passing
        # the dashboard's native Windows PATH directly can resolve ``python3``
        # to the Microsoft Store WindowsApps shim, which Bash translates to an
        # unexecutable path and reports as exit 127.  The same helper already
        # protects adapter/test subprocesses and is the single MSYS boundary.
        cmd, item_env = git_bash_command(
            ROOT / "engine/pipeline.sh", *pipeline_args,
            prepend=(pathlib.Path(sys.executable).parent,), env=item_env)
        r = subprocess.run(cmd, cwd=ROOT, env=item_env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", check=False)
        with fs_lock.lock(FILE):
            items = load()
            cur = next((i for i in items if i["id"] == item["id"]), None)
            if cur:
                # Persist WHY it failed. The runner is a background subprocess
                # launched by the dashboard, so its stdout/stderr goes to a console
                # no user ever reads — storing only the exit code left the UI able
                # to say nothing but "run failed", with the actionable message
                # (e.g. NO_STASH_PROJECT, PIPELINE_BUSY, a gate rejection) thrown
                # away at exactly the moment someone needed it.
                _mark(items, cur, status="done" if r.returncode == 0 else "failed",
                      finished=time.time(), exit_code=r.returncode,
                      error=("" if r.returncode == 0
                             else failure_reason(r.returncode, r.stdout, r.stderr)))
        # A release chosen at queue time is a fact about the work — persist it so
        # release-filtered views and reports include this key.
        if r.returncode == 0 and item.get("release"):
            import review_state
            review_state.set_release(key_of(item), item["release"], "queue")
        print(f"{key_of(item)}: {'done' if r.returncode == 0 else f'failed (exit {r.returncode})'}")
        if r.returncode != 0:
            print(r.stdout[-800:] + r.stderr[-800:], file=sys.stderr)
        processed += 1
    print(f"queue drained: {processed} item(s) processed")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "add":
        item, fresh = add(sys.argv[2], sys.argv[3],
                          sys.argv[4] if len(sys.argv) > 4 else None,
                          sys.argv[5] if len(sys.argv) > 5 else "",
                          sys.argv[6] if len(sys.argv) > 6 else "",
                          ticket=sys.argv[7] if len(sys.argv) > 7 else None)
        print(f"{'queued' if fresh else 'already queued'}: {key_of(item)} ({item['id']})")
    elif cmd == "list":
        for it in load():
            print(f"{it['id']:<16} {it['status']:<8} {it['mode']:<5} {key_of(it):<24} "
                  f"release={it.get('release') or '-'}")
    elif cmd == "run":
        run_all()
    elif cmd == "requeue":
        item = requeue(sys.argv[2])
        print(f"re-queued: {key_of(item)} ({item['id']})")
    elif cmd == "remove":
        item = remove(sys.argv[2])
        print(f"removed: {key_of(item)} ({item['id']})")
    else:
        sys.exit(__doc__)
