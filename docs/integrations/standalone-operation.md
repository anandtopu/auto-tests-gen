# Running without OpenHands — decision record

**Decision:** OpenHands is an **optional** trigger path, not a dependency, and which
posture you take is an explicit setting rather than an accident of connectivity. The
platform routes, generates, gates, commits and reports with no OpenHands installation
of any kind — *and* still uses an external Enterprise install when one is reachable.

The default is **hybrid**: use OpenHands when it answers, fall back to the CI /
TaskEvent / work-queue paths when it does not. Teams who cannot reach an Enterprise
deployment — licensing, procurement, network policy — keep shipping without changing
anything; teams who can, keep the Stop hook and path-triggered skills.

**Status:** adopted. The switch is `AIQE_OPENHANDS` /
[`engine/lib/openhands_mode.py`](../../engine/lib/openhands_mode.py) (§5), enforced in
[integration_check.py](../../engine/lib/integration_check.py) and
[bin/dashboard_server.py](../../bin/dashboard_server.py), and pinned by
`registry/tests/test_standalone.py`.

**Companion doc:** [openhands-review.md](openhands-review.md) is the capability
decision record — what we adopt *from* OpenHands when it is available. This file is
the inverse: what happens when it is not.

---

## 1. Why this was ever in doubt

The architecture (§4.3, §5.6) describes OpenHands as owning "orchestration and the
sandbox", which reads like a hard dependency. It is not. What an OpenHands
conversation actually does is send one instruction:

```
bash engine/pipeline.sh pr <repo> <pr>      # or: jira <KEY>
```

That is the whole integration. OpenHands is a **remote shell and scheduler** — the
pipeline, the phases, the catalog and the gate all run as plain bash + Python +
`claude -p` inside whatever process starts them.

Verified rather than assumed: with `OPENHANDS_URL` pointed at a dead host, a full run
routes, generates, gates, **commits**, and records an advisory critic score, exiting 0.
`registry/tests/test_standalone.py` pins this so it cannot regress.

## 2. What OpenHands provides, and what replaces it

| What it gives | Standalone replacement | Already shipped |
|---|---|---|
| PR trigger intake | `triggers/github-actions/`, `triggers/bitbucket-pipelines/`, `triggers/jenkins/` — each calls `pipeline.sh` directly | yes |
| Ticket trigger intake | `bin/taskevent_receiver.py` (`make hook-server`) — validated, idempotent, dedupe + enqueue; point Jira Automation at it | yes |
| Manual / batch runs | the work queue (`make queue-run`) and the dashboard's Intake view | yes |
| **Ephemeral sandbox** | this repo's own container image + `deploy/openshift/` (arbitrary non-root UID, state on a PVC) or `deploy/local/` Compose | yes |
| Watching a run | run records, the dashboard's Runs view, `make status`, Slack/email notifications | yes |

Nothing in that column needs building — it is all in the repo and exercised by
`make review`.

## 3. The one thing that genuinely matters: isolation

The sandbox is not a convenience. `engine/phases/run_phase.sh` passes
`--dangerously-skip-permissions`, which architecture §5.3 permits **only** because the
runtime is ephemeral, network-restricted and holds a least-privilege deploy token —
"never on shared infrastructure."

Dropping OpenHands therefore means replacing the *isolation*, not the orchestration.
The supported answer is the platform's own container:

```bash
make docker-build                 # non-root image (USER 1001)
make deploy-local                 # Compose, for a workstation or build agent
make deploy-openshift NS=ai-qe    # arbitrary non-root UID, RWO PVC, Routes
```

**Do not** run `AIQE_MOCK=0` phases directly on a shared build agent or a developer
workstation with broad credentials. That is the one way standalone operation can be
less safe than the OpenHands path, and it is entirely avoidable.

## 4. What you lose, honestly

Two features only fire *inside* an OpenHands conversation:

- **The Stop hook** (`.openhands/hooks.json`) — blocks an agent from declaring a task
  done when the gate would reject it. Standalone, the authoritative gate still runs and
  still rejects; you simply learn later instead of sooner. The check is also runnable
  by hand, because the hook is a thin wrapper around a normal gate invocation:

  ```bash
  cd workspace/tests/<repo> && AIQE_GATE_CHECK_ONLY=1 bash ../../../engine/gate/gate.sh KEY <repo>
  ```

- **Path-triggered skills** (`.agents/skills/e2e-{api,ui}-conventions/`) — inject UI or
  API conventions only when the agent touches a matching file. Standalone, `AGENTS.md`
  remains always-on and carries the same conventions to every phase, just without the
  per-discipline split. (This is the same fallback that already applies inside
  ACP-backed conversations, where path triggers do not fire either.)

Also unavailable: the OpenHands conversation UI and the agent event stream
(`/hooks/openhands/*` simply receives nothing). Both are observability, and the
dashboard already covers the same ground from run records.

**Nothing on this list affects whether a generated test is correct or whether it gets
committed.** The gate is unchanged and remains the only path that writes.

## 5. The switch: `AIQE_OPENHANDS`

`make check-integrations` used to **exit 1** when OpenHands was unreachable, turning a
CI gate red over a system the pipeline never calls. Rather than hard-code the opposite
opinion, the dependency level is now stated explicitly — one setting, three modes:

| Mode | Connectivity check | Exit code | Use when |
|---|---|---|---|
| `off` | `[skip] disabled — running standalone` | 0 | no installation, or you want the estate provably standalone |
| **`auto`** (default) | `[warn] … degraded` | **0** | **hybrid** — you have an install but must keep shipping when it is down |
| `required` | `[FAIL]` | **1** | you genuinely depend on it and want CI to go red |

- Set it per run with `AIQE_OPENHANDS=off|auto|required`, estate-wide via `openhands.mode`
  in `registry/org-config.yaml`, or from the dashboard's **Settings → OpenHands →
  Dependency mode**. Env wins over config, matching `AIQE_MOCK` and `AIQE_CRITIC`.
- Generous spellings are accepted (`0`/`false`/`none` → off, `1`/`hybrid`/`optional` →
  auto, `strict` → required). An unrecognised value falls back to `auto` rather than
  failing a run over a typo.
- In `off`, the mode wins over leftover credentials: a configured `OPENHANDS_URL` is
  still reported `[skip]`, and the dashboard answers **409** rather than delegating a
  run — a deliberate posture should not be contradicted by stale `.env` values.
- In `auto`, the hint names the alternative: *"optional — the pipeline does not call it.
  Trigger runs via CI or the TaskEvent receiver instead."* The Settings validator shows
  `unreachable (optional)` in warning colour and reports "runs are unaffected".

Slack, Splunk and Jenkins are deliberately **not** optional at any setting: configure
them, let them break, and you silently lose notifications or telemetry — worth failing
over. OpenHands is different only because its job is interchangeable with three other
trigger paths.

## 6. Connecting to an external OpenHands Enterprise install

Hybrid mode is the point of the flag: point the platform at your Enterprise deployment
and it will use it when reachable, without ever making the estate hostage to it.

```bash
# .env  (or dashboard Settings -> OpenHands)
AIQE_OPENHANDS=auto                          # hybrid: use it, but never depend on it
OPENHANDS_URL=https://openhands.your-corp.com
OPENHANDS_API_KEY=<token>
# OPENHANDS_CONVERSATIONS_PATH=/api/v1/app-conversations   # Cloud V1 only
```

Then confirm the connection — and note that a failure here is informational, not fatal:

```bash
python3 engine/lib/openhands_mode.py   # prints the effective mode
make check-integrations WHICH=openhands
make smoke-openhands                   # staged, deeper; AIQE_SMOKE_TRIGGER=1 costs money
```

Corporate-network gotchas worth trying before concluding the install is unreachable:

- **TLS interception** — a corporate CA will make the client fail on certificate
  verification rather than connectivity. `AIQE_SSL_VERIFY=0` is supported for exactly
  this case (commit `60cd590`); prefer installing the CA bundle where you can.
- **Health path** — deployments differ; override with `OPENHANDS_HEALTH_PATH` if the
  checker probes the wrong one.
- **API shape** — self-hosted Agent Server uses `POST /api/conversations`; Cloud V1
  uses `POST /api/v1/app-conversations`. Set `OPENHANDS_CONVERSATIONS_PATH` for Cloud.
- **Licensing** — `enterprise/` is PolyForm Free Trial (30 days/year without a
  commercial licence). If that is what actually blocks you, §7 is the way through: the
  MIT core is self-hostable and needs no licence.

While it is unreachable, runs continue through CI, the TaskEvent receiver and the work
queue. You lose only the Stop hook and path-triggered skills (§4) until it returns.

## 7. If you want OpenHands but cannot use Enterprise

The core is **MIT** and self-hostable; only `enterprise/` is PolyForm Free Trial (30
days/year, not an open-source licence). So an unreachable *enterprise* install does not
bar you from the capability:

- **`openhands-ai` / Agent Canvas** (MIT) — `pip install openhands-ai`, or
  `ghcr.io/openhands/agent-canvas`. Bundles the **Agent Server** (REST, many agents per
  host) and an optional **Automation Server** for scheduled and webhook-triggered runs.
- `engine/lib/openhands_client.py` already speaks the self-hosted shape
  (`POST /api/conversations`), so this is usually just repointing `OPENHANDS_URL` and
  `OPENHANDS_API_KEY`. Verify with `make check-integrations` then `make smoke-openhands`.
- Enterprise-only features you would still lack: SAML/SSO, RBAC and multi-user, the
  native Slack/Jira/Bitbucket integrations, and budget enforcement. The middle one is
  already covered by our own receiver and CI triggers.

⚠️ **Trap:** Agent Canvas runs third-party harnesses like Claude Code **through ACP**,
which is exactly the blocker recorded in [openhands-review.md §3.1](openhands-review.md)
— `tools`, `mcp_config`, `condenser` and `critic` raise `NotImplementedError`, so we
would lose per-phase `--allowedTools` and `--max-turns`. Avoid it: have the conversation
run `bash engine/pipeline.sh` as an ordinary shell task, exactly as
`triggers/openhands/skills/ai-qe/SKILL.md` already does. Then `claude -p` keeps full
per-phase control and ACP never enters the picture.

## 8. Recommended hybrid topology

Two independent ways in, one pipeline, one gate. The OpenHands path is additive — if it
goes away, the lower two paths carry the whole load unchanged.

```
OpenHands Enterprise ──(when reachable, AIQE_OPENHANDS=auto)──┐
                                                              │
Jira Automation ──webhook──> bin/taskevent_receiver.py (:4998)┤──> work queue
                                                              │
PR opened ────────CI────────> triggers/{github-actions,        │
                               bitbucket-pipelines,jenkins} ───┘
                                          │
                                          └──> engine/pipeline.sh  (in the platform's
                                                own container, on OpenShift)
                                                     │
                                                     └──> engine/gate/gate.sh → commit
```

Setup checklist:

1. `make docker-build && make deploy-openshift NS=<ns>` — gives you the isolated runtime.
2. Point Jira Automation at the receiver's `/hooks/taskevent` (token via `X-AIQE-Token`).
3. Copy the CI trigger for your SCM from `triggers/` into each source repo.
4. Choose your posture: leave `AIQE_OPENHANDS` at `auto` to use an Enterprise install
   whenever it is reachable, or set `off` to run provably standalone.
5. `make check-integrations` to confirm the systems you *do* use are reachable.
