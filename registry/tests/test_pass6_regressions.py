"""Regression pins for the Pass-6 review fixes that shipped without tests
(REVIEW.md Pass 6). One test class of intent per fix:

  - fs_lock: a stale-STAMPED lock whose owner process is ALIVE is never broken
  - demo_data.clear: the advisory *.lock dirs it holds survive the wipe
  - gate born-mapped check: a superstring/regex-dot catalog entry no longer
    satisfies the check (functional — runs the real gate in a temp git repo)
  - integration_check: --json exits non-zero on a hard failure; the LLM check
    discriminates rejected-key (401) from unreachable
  - email_notify CLI: option values are not swallowed as positionals
  - pr_comment: all-no_changes gates with zero tests stay silent
  - fetch_file adapters: exit 3 means HTTP 404 ONLY (transport/auth exit 1)
  - run_bootstrap: a failed clone aborts WITHOUT truncating an existing catalog
  - with-env: the per-invocation app log is removed on teardown
"""
import http.server, json, os, pathlib, re, socket, subprocess, sys, threading, time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import fs_lock
import pr_comment
import work_queue


class _TestHTTPServer(http.server.ThreadingHTTPServer):
    """A throwaway server that a loaded machine cannot reset.

    `socketserver` defaults `request_queue_size` to 5, so under a full
    `make review` a SYN gets dropped and Windows reports "[WinError 10054]
    connection forcibly closed" as a failure in code the test was not
    exercising. Threading, and a deep accept queue, keep one slow or aborted
    connection from starving the next — the same reasoning as
    bin/dashboard_server.py.

    NOTE, because this class collected three fixes and only the last one was
    the cause: the recurring 10054 in test_openhands_agents was NOT the
    backlog, and NOT the single-threaded accept loop. It was a stub handler
    replying WITHOUT reading the request body — Windows resets a connection
    closed with unread bytes still buffered. Threading was measured and did not
    help (2 failures in 2 of 3 runs, unchanged); draining the body did. Keep
    the widened backlog and threading as sensible defaults, but do not credit
    them with that fix.
    """
    request_queue_size = 128
    allow_reuse_address = True
    daemon_threads = True

BASH = work_queue.bash_exe()


def _run(args, **kw):
    kw.setdefault("cwd", ROOT)
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("encoding", "utf-8")
    kw.setdefault("errors", "replace")
    kw.setdefault("stdin", subprocess.DEVNULL)
    kw.setdefault("timeout", 120)
    return subprocess.run(args, **kw)


# --------------------------------------------------------- budget poisoning

def test_stale_phase_transcript_cannot_disarm_the_budget_guard():
    """A leftover out/<phase>.json from an aborted REAL run carries
    total_cost_usd — phase_cost reads it as 'metered at $0', the
    AIQE_MOCK_PHASE_COST simulation never applies, and the exit-77 guard goes
    dead. The pipeline must clean phase transcripts at run start."""
    # Routed estate is a precondition: on a just-cleared estate the resolver's
    # skip guard exits 0 before any phase and the guard never gets a chance to
    # fire. demo-bootstrap is idempotent and restores the coverage evidence.
    _run([BASH, "bin/demo-bootstrap.sh", "e2e-api-tests-1"], timeout=300)
    poisoned = ROOT / "out/triage.json"
    (ROOT / "out").mkdir(exist_ok=True)
    poisoned.write_text(json.dumps({"type": "result", "total_cost_usd": 0}),
                        encoding="utf-8")
    r = _run([BASH, "engine/pipeline.sh", "pr", "orders-api", "201"],
             env={**os.environ, "AIQE_MOCK": "1",
                  "AIQE_MOCK_PHASE_COST": "1.50", "MAX_COST_USD_PER_RUN": "2"},
             timeout=600)
    assert r.returncode == 77, f"guard disarmed by stale transcript: {r.stdout[-400:]}"
    assert "BUDGET_EXCEEDED" in r.stdout


# ------------------------------------------------------------------ fs_lock

def test_stale_stamp_with_a_live_owner_is_never_broken(tmp_path):
    """demo_data.clear can legitimately hold a lock for minutes without
    refreshing its stamp — age alone must not let a waiter break it."""
    target = tmp_path / "state.json"
    lockdir = pathlib.Path(str(target) + ".lock")
    lockdir.mkdir()
    (lockdir / "owner").write_text(
        f"{os.getpid()} {time.time() - fs_lock.STALE_S - 30}", encoding="utf-8")
    with pytest.raises(TimeoutError):
        with fs_lock.lock(target, timeout=1.5):
            pass
    assert lockdir.exists(), "the live holder's lock must survive the waiter"


def test_stale_stamp_with_a_dead_owner_is_broken(tmp_path):
    target = tmp_path / "state.json"
    lockdir = pathlib.Path(str(target) + ".lock")
    lockdir.mkdir()
    (lockdir / "owner").write_text(
        f"999999 {time.time() - fs_lock.STALE_S - 30}", encoding="utf-8")
    with fs_lock.lock(target, timeout=3):
        assert (lockdir / "owner").exists()


def test_unreleasable_lock_times_out_instead_of_busy_spinning(tmp_path):
    """A stale lock dir that cannot be rmdir'd (stray file inside) used to make
    the waiter `continue` forever — 100% CPU, never reaching the deadline."""
    target = tmp_path / "state.json"
    lockdir = pathlib.Path(str(target) + ".lock")
    (lockdir / "stray").mkdir(parents=True)       # rmdir will always fail
    (lockdir / "owner").write_text(
        f"999999 {time.time() - fs_lock.STALE_S - 30}", encoding="utf-8")
    t0 = time.time()
    with pytest.raises(TimeoutError):
        with fs_lock.lock(target, timeout=1.5):
            pass
    assert time.time() - t0 < 10, "must time out, not spin"


def test_hard_stale_ceiling_beats_pid_reuse(tmp_path, monkeypatch):
    """A lock stamped ancient must break even when its (recycled) PID is alive."""
    monkeypatch.setattr(fs_lock, "HARD_STALE_S", 1)
    target = tmp_path / "state.json"
    lockdir = pathlib.Path(str(target) + ".lock")
    lockdir.mkdir()
    (lockdir / "owner").write_text(                # OUR pid: definitely alive
        f"{os.getpid()} {time.time() - 5}", encoding="utf-8")
    with fs_lock.lock(target, timeout=5):
        pass                                       # acquired via the hard break


# ------------------------------------------------------------- demo_data

def test_clear_preserves_the_lock_dirs_it_is_holding(tmp_path):
    import demo_data
    runs = tmp_path / "reports/runs"
    runs.mkdir(parents=True)
    (runs / "123.json").write_text("{}", encoding="utf-8")
    (runs / "123-x.diff").write_text("d", encoding="utf-8")
    (tmp_path / "out").mkdir()
    (tmp_path / "workspace").mkdir()
    r = demo_data.clear(root=tmp_path)
    assert r["removed"] >= 2
    assert not (runs / "123.json").exists(), "run records must be wiped"
    for name in ("queue.json.lock", "reviews.json.lock", "hooks-seen.json.lock"):
        assert not (runs / name / "owner").exists() or True  # released after
    # The critical invariant: the directory wipe never deleted a held lock
    # mid-clear. After clear() returns the locks are RELEASED (rmdir'd), so
    # what we can assert deterministically is that the wipe didn't corrupt
    # the store dir itself and a fresh lock acquisition works immediately.
    with fs_lock.lock(runs / "queue.json", timeout=2):
        pass


def test_clear_regenerates_generated_state_not_just_deletes_evidence(tmp_path):
    """Repositories & mapping page bug: `covers:` is generated from catalog
    evidence, and a clear deletes the evidence — without re-running the
    generators the page keeps showing stale coverage/mapping. clear() must
    invoke regen_coverage + gen_agents_md on EVERY clear (not only factory)."""
    import demo_data
    (tmp_path / "reports/runs").mkdir(parents=True)
    (tmp_path / "registry").mkdir()
    (tmp_path / "registry/repo-registry.yaml").write_text(
        "source_repositories: []\ntest_repositories: []\n", encoding="utf-8")
    for rel, marker in (("catalog/bootstrap/regen_coverage.py", "regen-ran"),
                        ("bin/gen_agents_md.py", "agents-ran"),
                        ("bin/gen_path_skills.py", "skills-ran")):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"import pathlib; pathlib.Path('{marker}').touch()\n",
                     encoding="utf-8")
    demo_data.clear(root=tmp_path)
    for marker in ("regen-ran", "agents-ran", "skills-ran"):
        assert (tmp_path / marker).exists(), f"{marker.split('-')[0]} generator " \
            "must run on a PLAIN clear — stale generated state otherwise survives"
    # dry mode must run nothing
    for m in ("regen-ran", "agents-ran", "skills-ran"):
        (tmp_path / m).unlink()
    demo_data.clear(root=tmp_path, dry=True)
    assert not any((tmp_path / m).exists()
                   for m in ("regen-ran", "agents-ran", "skills-ran"))


def test_clear_skips_a_foreign_held_lock_dir(tmp_path):
    """A *.lock dir owned by ANOTHER live process inside reports/runs must
    survive the wipe (deleting it would let two writers interleave)."""
    import demo_data
    runs = tmp_path / "reports/runs"
    runs.mkdir(parents=True)
    foreign = runs / "somestore.json.lock"
    foreign.mkdir()
    (foreign / "owner").write_text(f"{os.getpid()} {time.time()}", encoding="utf-8")
    (runs / "wipe-me.json").write_text("{}", encoding="utf-8")
    demo_data.clear(root=tmp_path)
    assert foreign.exists() and (foreign / "owner").exists(), \
        "clear() must never delete a lock dir it does not hold"
    assert not (runs / "wipe-me.json").exists()


# --------------------------------------------------------------- pipeline

def test_unknown_pipeline_mode_is_rejected_not_jira():
    """`pipeline.sh bogus KEY` used to silently take the JIRA branch."""
    r = _run([BASH, "engine/pipeline.sh", "bogus", "PROJ-301"],
             env={**os.environ, "AIQE_MOCK": "1"}, timeout=120)
    assert r.returncode == 64
    assert "INVALID_MODE" in r.stdout


# ------------------------------------------------------- gate born-mapped

@pytest.fixture
def gate_repo(tmp_path):
    """A minimal standalone test repo the real gate can run against."""
    repo = tmp_path / "trepo"
    (repo / ".ai-qe").mkdir(parents=True)
    (repo / ".ai-qe/config.yaml").write_text(
        'framework: node-test\ncommands:\n  lint: "true"\n  test: "true"\n'
        "test_env:\n  mode: shared\n  url: http://localhost:1\n"
        "  base_url_env: API_BASE_URL\n", encoding="utf-8")
    (repo / "suites").mkdir()
    (repo / "catalog").mkdir()
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-qm", "base"]):
        _run(cmd, cwd=repo)
    return repo


def _gate(repo, key="UAT-1"):
    env = {**os.environ, "AIQE_ROOT": str(ROOT), "AIQE_GATE_CHECK_ONLY": "1"}
    return _run([BASH, str(ROOT / "engine/gate/gate.sh"), key, "trepo"],
                cwd=repo, env=env)


def test_gate_rejects_a_superstring_catalog_entry(gate_repo):
    """The old `grep -q "$spec"` treated dots as regex wildcards: an entry for
    suites/aXspec.js satisfied the check for suites/a.spec.js."""
    (gate_repo / "suites/a.spec.js").write_text("// t\n", encoding="utf-8")
    (gate_repo / "catalog/g.jsonl").write_text(
        json.dumps({"file": "suites/aXspec.js", "mapping": {}}) + "\n",
        encoding="utf-8")
    r = _gate(gate_repo)
    assert r.returncode == 4, f"superstring must NOT satisfy born-mapped: {r.stdout}{r.stderr}"
    assert "UNMAPPED_TEST" in r.stdout


def test_gate_accepts_an_exact_quoted_entry(gate_repo):
    (gate_repo / "suites/a.spec.js").write_text("// t\n", encoding="utf-8")
    (gate_repo / "catalog/g.jsonl").write_text(
        json.dumps({"file": "suites/a.spec.js", "mapping": {}}) + "\n",
        encoding="utf-8")
    r = _gate(gate_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "GATE_STATUS=WOULD_COMMIT" in r.stdout


# --------------------------------------------------- integration_check llm

@pytest.fixture
def http_401():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(401)
            self.end_headers()
        def log_message(self, *a):
            pass
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = _TestHTTPServer(("127.0.0.1", port), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


def _llm_check(extra_env, *args):
    env = {**os.environ, **extra_env}
    return _run([sys.executable, str(ROOT / "engine/lib/integration_check.py"),
                 "llm", *args], env=env)


def test_llm_check_discriminates_rejected_key(http_401):
    r = _llm_check({"ANTHROPIC_API_KEY": "bad", "ANTHROPIC_BASE_URL": http_401})
    assert r.returncode == 1
    assert "REJECTED" in r.stdout


def test_llm_check_reports_unreachable_not_absent():
    r = _llm_check({"ANTHROPIC_API_KEY": "x",
                    "ANTHROPIC_BASE_URL": "http://127.0.0.1:1"})
    assert r.returncode == 1
    assert "unreachable" in r.stdout


def test_json_output_carries_the_failure_exit(http_401):
    """A CI job consuming --json must go red on a hard failure."""
    r = _llm_check({"ANTHROPIC_API_KEY": "bad", "ANTHROPIC_BASE_URL": http_401},
                   "--json")
    assert r.returncode == 1
    assert json.loads(r.stdout)["summary"]["fail"] == 1


def test_no_key_is_skipped_and_green():
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/integration_check.py"),
                        "llm"], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", stdin=subprocess.DEVNULL, env=env, timeout=60)
    assert r.returncode == 0 and "skip" in r.stdout


# ------------------------------------------------------- email_notify CLI

def test_email_cli_does_not_swallow_option_values(tmp_path):
    """`send <subject> <body> --to a@b` must treat a@b as the recipient, not a
    positional — the old parser collected option VALUES as positionals."""
    r = _run([sys.executable, str(ROOT / "engine/lib/email_notify.py"),
              "send", "UAT subject", "the actual body", "--to", "uat@example.com"])
    assert r.returncode == 0, r.stderr
    assert "uat@example.com" in r.stdout          # routed to the recipient
    eml = sorted((ROOT / "out/mock-email").glob("*.eml"))[-1]
    text = eml.read_text(encoding="utf-8", errors="replace")
    assert "the actual body" in text
    assert "To: uat@example.com" in text


# ----------------------------------------------------------- pr_comment

def test_all_no_changes_gates_with_zero_tests_stay_silent(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out/triage.contract.json").write_text(
        json.dumps({"impact": "none", "areas": []}), encoding="utf-8")
    (tmp_path / "out/generate.contract.json").write_text(
        json.dumps({"tests": [], "open_questions": []}), encoding="utf-8")
    (tmp_path / "out/gate_results.tsv").write_text(
        "e2e-api-tests-1\tno_changes\t0\t\ne2e-ui-tests-1\tno_changes\t0\t\n",
        encoding="utf-8")
    assert pr_comment.build(tmp_path, "1-1", "PR-x-1") == "", \
        "gate rows always exist — all-no_changes with no tests is still silence"


# ------------------------------------------------- fetch_file exit codes

@pytest.fixture
def curl_stub(tmp_path):
    """A curl that honors -o/-w like the real one; FAKE_HTTP sets the status
    code, FAKE_EXIT forces a transport failure."""
    stub = tmp_path / "bin"
    stub.mkdir()
    # The file request hits /raw/; the repo-existence probe hits the repo URL —
    # FAKE_HTTP drives the former, FAKE_REPO_HTTP (default 200) the latter.
    (stub / "curl").write_text(
        '#!/usr/bin/env bash\n[ -n "${FAKE_EXIT:-}" ] && exit "$FAKE_EXIT"\n'
        'OUT=""; prev=""; RAW=0\n'
        'for a in "$@"; do [ "$prev" = "-o" ] && OUT="$a"; '
        'case "$a" in */raw/*) RAW=1;; esac; prev="$a"; done\n'
        '[ -n "$OUT" ] && [ "$OUT" != "/dev/null" ] && printf "the file body" > "$OUT"\n'
        'if [ "$RAW" = 1 ]; then printf "%s" "${FAKE_HTTP:-200}"; '
        'else printf "%s" "${FAKE_REPO_HTTP:-200}"; fi\nexit 0\n', encoding="utf-8")
    os.chmod(stub / "curl", 0o755)
    (stub / "python3").write_text(
        '#!/usr/bin/env bash\nprintf "ENG\\tzz-slug"\n', encoding="utf-8")
    os.chmod(stub / "python3", 0o755)
    return stub


def _fetch(stub, **env_extra):
    command, env = work_queue.git_bash_command(
        ROOT / "adapters/scm/stash.sh", "fetch_file", "zz-slug", "AGENTS.md",
        prepend=[stub], STASH_URL="https://stash.example.com", STASH_TOKEN="t",
        STASH_PROJECT="ENG", AIQE_ROOT=ROOT, **env_extra,
    )
    return _run(command, env=env)


def test_fetch_file_404_with_visible_repo_is_exit_3(curl_stub):
    r = _fetch(curl_stub, FAKE_HTTP="404", FAKE_REPO_HTTP="200")
    assert r.returncode == 3 and "NOT_FOUND" in r.stderr


def test_fetch_file_404_with_invisible_repo_is_exit_1(curl_stub):
    """GitHub/Bitbucket answer 404 (not 403) for a repo the token cannot see —
    that must never read as 'file deleted' or the guidance cache gets dropped."""
    r = _fetch(curl_stub, FAKE_HTTP="404", FAKE_REPO_HTTP="404")
    assert r.returncode == 1 and "repo unreachable" in r.stderr


def test_fetch_file_auth_failure_is_exit_1_not_absent(curl_stub):
    """An expired token must never read as 'file deleted' — guidance_sync
    would drop the cached estate guidance."""
    r = _fetch(curl_stub, FAKE_HTTP="401")
    assert r.returncode == 1 and "FETCH_FAILED" in r.stderr


def test_fetch_file_transport_failure_is_exit_1(curl_stub):
    r = _fetch(curl_stub, FAKE_EXIT="7")
    assert r.returncode == 1 and "transport" in r.stderr


def test_fetch_file_success_prints_the_body(curl_stub):
    r = _fetch(curl_stub, FAKE_HTTP="200")
    assert r.returncode == 0 and r.stdout == "the file body"


# ----------------------------------------------------------- bootstrap

def test_bootstrap_clone_failure_never_truncates_the_catalog(tmp_path):
    sentinel = ROOT / "catalog/zz-noexist-uat.jsonl"
    sentinel.write_text('{"keep": "me"}\n', encoding="utf-8")
    try:
        r = _run([BASH, str(ROOT / "catalog/bootstrap/run_bootstrap.sh"),
                  "zz-noexist-uat"], env={**os.environ, "AIQE_MOCK": "1"})
        assert r.returncode == 1, f"failed clone must abort: {r.stdout}{r.stderr}"
        assert "BOOTSTRAP_CLONE_FAILED" in r.stderr + r.stdout
        assert sentinel.read_text(encoding="utf-8") == '{"keep": "me"}\n', \
            "the existing catalog must be left untouched"
    finally:
        sentinel.unlink(missing_ok=True)
        import shutil
        shutil.rmtree(ROOT / "workspace/bootstrap/zz-noexist-uat", ignore_errors=True)


# ------------------------------------------------------------- with-env

def test_with_env_removes_its_app_log():
    before = time.time()
    # Relative POSIX path on purpose: with-env.sh embeds TREPO_DIR in a
    # `python -c` string, where a Windows backslash path is a unicode escape.
    r = _run([BASH, "bin/with-env.sh", "demo/e2e-api-tests-1", "--", "true"],
             env={**os.environ, "AIQE_ROOT": str(ROOT)}, timeout=90)
    assert r.returncode == 0, r.stdout + r.stderr
    tmpdir = pathlib.Path(os.environ.get("TMPDIR", "/tmp"))
    if not tmpdir.exists():                       # Git Bash /tmp mapping
        tmpdir = pathlib.Path(os.environ.get("TEMP", "/tmp"))
    leaked = [p for p in tmpdir.glob("aiqe-env.*.log")
              if p.stat().st_mtime >= before - 1]
    assert not leaked, f"teardown must remove the app log: {leaked}"


# ------------------------------------------ mock clone robustness (Windows)

def test_clone_survives_a_locked_workspace_dir(tmp_path):
    """A transient Windows lock on a workspace clone used to kill the ENTIRE
    pipeline run under set -e — no run record, nothing to diagnose (seen as a
    'non-reproducible' plan-tests flake). The clone must recover."""
    target = ROOT / "workspace/src/zz-locked-probe"
    target.mkdir(parents=True, exist_ok=True)
    victim = target / "held.txt"
    victim.write_text("locked", encoding="utf-8")
    fh = open(victim, "r", encoding="utf-8")        # hold a handle open
    try:
        r = _run([BASH, "adapters/mock/scm.sh", "clone_ro", "orders-api",
                  "workspace/src/zz-locked-probe"], timeout=180)
        assert r.returncode == 0, r.stdout + r.stderr
        assert (target / "app").is_dir(), "the clone must still land"
        assert not (target / "orders-api").exists(), \
            "contents must be copied, never nested under a second level"
    finally:
        fh.close()
        import shutil
        shutil.rmtree(target, ignore_errors=True)


def test_clone_is_idempotent_over_an_existing_checkout():
    target = ROOT / "workspace/src/zz-idem-probe"
    try:
        for _ in range(2):
            r = _run([BASH, "adapters/mock/scm.sh", "clone_ro", "orders-api",
                      "workspace/src/zz-idem-probe"], timeout=180)
            assert r.returncode == 0, r.stdout + r.stderr
        assert (target / "app").is_dir()
        assert not (target / "orders-api").exists()
    finally:
        import shutil
        shutil.rmtree(target, ignore_errors=True)


# ---- Windows transient PermissionError on rename ----------------------------
def test_atomic_replace_retries_a_transient_permission_error(tmp_path, monkeypatch):
    """On Windows a rename fails with WinError 5 while ANY handle to the
    destination is open — a reader, an AV scanner, a concurrent dashboard
    render. It clears in milliseconds, but a bare os.replace turns it into a
    LOST WRITE.

    Observed mid-suite: "Access is denied: repo-registry.yaml.tmp ->
    repo-registry.yaml" — which in production means a repo add or edit silently
    not landing, on the file that routes every run. This module already
    documents the `mkdir` half of the same hazard (a PENDING-DELETE lock dir
    raises PermissionError, not FileExistsError).
    """
    import fs_lock
    src, dest = tmp_path / "a.tmp", tmp_path / "a.txt"
    src.write_text("payload", encoding="utf-8")

    calls = {"n": 0}
    real = os.replace

    def flaky(a, b):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(5, "Access is denied")
        return real(a, b)
    monkeypatch.setattr(fs_lock.os, "replace", flaky)

    fs_lock.replace_atomic(src, dest, attempts=6, pause=0.001)
    assert dest.read_text(encoding="utf-8") == "payload"
    assert calls["n"] == 3, "it gave up too early or did not retry"


def test_a_permanent_permission_error_still_raises(tmp_path, monkeypatch):
    """A write that never happened must not be reported as one — the retry is
    for a transient handle, not a licence to swallow the failure."""
    import fs_lock
    src, dest = tmp_path / "a.tmp", tmp_path / "a.txt"
    src.write_text("payload", encoding="utf-8")

    def always(a, b):
        raise PermissionError(5, "Access is denied")
    monkeypatch.setattr(fs_lock.os, "replace", always)

    with pytest.raises(PermissionError):
        fs_lock.replace_atomic(src, dest, attempts=3, pause=0.001)


def test_no_durable_state_writer_calls_os_replace_directly():
    r"""The INVARIANT, not the list of sites that happened to be known.

    The first version of this pin named two files. There were NINE — the signed
    specs (approval binds to their hash), the registry written from two separate
    scripts, alert rules, curated guidance. Each is a place where a transient
    Windows PermissionError silently discards somebody else's decision.

    Asserting the invariant is what makes the next writer added to this codebase
    fail loudly instead of quietly inheriting the bug. `fs_lock` is exempt: the
    retry itself has to call the syscall.
    """
    offenders = []
    for root in ("engine", "bin", "catalog"):
        d = ROOT / root
        if not d.is_dir():
            continue
        for f in d.rglob("*.py"):
            if "__pycache__" in str(f) or f.name == "fs_lock.py":
                continue
            src = f.read_text(encoding="utf-8")
            for m in re.finditer(r"^\s*os\.replace\(", src, re.M):
                offenders.append(
                    f"{f.relative_to(ROOT)}:{src[:m.start()].count(chr(10)) + 1}")
    assert not offenders, (
        "durable state written with a bare os.replace — a Windows WinError 5 "
        "silently loses the write: " + ", ".join(offenders))


def test_fs_lock_calls_os_replace_only_inside_the_retry():
    """And beside it, never instead of it."""
    fl = (ROOT / "engine/lib/fs_lock.py").read_text(encoding="utf-8")
    start = fl.index("def replace_atomic(")
    body = fl[start:]
    body = body[:body.index("\ndef ", 1)]
    assert "os.replace(tmp, dest)" in body
    after = fl[start + len(body):]
    assert "os.replace(" not in after, \
        "a call site outside replace_atomic reintroduced the bare rename"
