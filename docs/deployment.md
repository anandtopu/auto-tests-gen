# Deployment Guide

How to run the AI QE platform as a long-running service — locally with Docker
Compose, and on a remote OpenShift / Kubernetes cluster. The demo/mock mode needs
**no credentials and makes no external calls**, so you can stand the whole thing up
first and wire real integrations later.

## What gets deployed

The platform runs two long-lived HTTP services that share filesystem state:

| Service | Port | Role |
|---|---|---|
| **Dashboard** (`bin/dashboard_server.py`) | 4999 | Fifteen-view QA UI ([ui-guide.md](ui-guide.md)); also runs the pipeline when you drain the work queue |
| **TaskEvent receiver** (`bin/taskevent_receiver.py`) | 4998 | Webhook endpoint: validates, de-duplicates, and enqueues events |

Both coordinate through an advisory lock (`engine/lib/fs_lock.py`) on a shared
filesystem, so **they must run co-located** (same pod / same volumes). The pipeline
(`engine/pipeline.sh`) is spawned on demand and holds an exclusive per-checkout run
lock — hence a **single replica, single writer** model.

State layout:

| Path | Persistence | Contents |
|---|---|---|
| `reports/` | **persistent volume** | run records + archived diffs, review board, work queue, exports |
| `knowledge/curated/` | **committed with the control repo** | user-curated per-repo AGENTS.md/CLAUDE.md (Repositories view) — durable across redeployments because the control repo *is* the deployment; commit it like any config change |
| `workspace/`, `out/` | ephemeral scratch | per-run clones and phase artifacts (safe to lose) |

The container image (`Dockerfile`) bundles the app, Python 3 + PyYAML, Node 20 (the
demo estate and the `node --test` gate), and bash/git/curl/jq. It is
**OpenShift-compatible**: the app tree is group-0 writable and the process runs as an
arbitrary non-root UID — no root, no fixed UID. Both containers run with
`readOnlyRootFilesystem: true` (see [review-readonly-rootfs.md](review-readonly-rootfs.md)),
which is why state lives on a volume rather than in `/app`.

### First boot: what a new deployment seeds

With a read-only root the image tree is immutable, so every mutable path is
redirected to `AIQE_STATE_DIR` (`engine/lib/app_paths.py`). Three of those paths
**ship content in the image** and would otherwise start empty on a brand-new
volume: the catalog mappings, the repo registry, and the knowledge base. A fresh
deployment with none of them routes nothing — every resolution resolves to no
repo, which is failure that looks like "no work to do".

`bin/container-entrypoint.sh` copies them in **once**, under two rules:

- **Never overwrite.** Once the volume holds a path, the volume wins — it carries
  a human's edits (mappings confirmed in review, repos added through Settings,
  curated guidance). Re-seeding on restart would silently revert somebody's work.
- **Data only, never code or config.** `catalog/bootstrap/*.py`, `catalog/schema.json`
  and `registry/org-config.yaml` stay in the image so an image upgrade actually
  ships new logic. Copying them onto the volume freezes them at first boot —
  the exact failure the relocation design exists to avoid. The policy lives in
  `app_paths.seed_plan()`, so the entrypoint copies what it is given and decides
  nothing.

Generated directories (`testplans/`, `testdata/`, `specs/`) are created empty and
never seeded: restoring a stale plan over an empty volume would present it as state.

`make test-entrypoint` is 17 checks on this path. It is worth knowing what they
caught, because both failures were silent:

- The entrypoint reported `state root already populated — nothing seeded` about a
  directory it had **just created empty**. "We copied nothing" and "the volume
  already had it" were the same branch. It now samples the state root *before*
  seeding and warns loudly when an empty root receives nothing.
- **The Kubernetes manifests then bypassed the entrypoint entirely.** In
  Kubernetes, `command:` replaces the image ENTRYPOINT and `args:` replaces CMD —
  the *opposite* mapping to docker-compose, where `command:` means CMD. The
  manifests had been written to look like the (correct) compose file, so
  `tini → container-entrypoint.sh` never ran on a cluster and a fresh deployment
  did no seeding at all. `tini` also stopped being PID 1.

Both directions are pinned in `test_deploy_manifests.py`: no Kubernetes container
may set a `command:` that does not start with the entrypoint, and compose must
**keep** its `command:` — "fixing" it there would be the wrong direction.

---

## 1. Local deployment (Docker Compose)

Prerequisites: Docker **or Podman** — both the image build and compose engines are
auto-detected (see the paragraph after the Make targets below).

```bash
cd deploy/local
./deploy.sh              # build the image, start both services, wait for health
./deploy.sh --seed       # …and seed the demo estate (bootstrap + Workflow A + B)
```

Then open:

- Dashboard — <http://localhost:4999>
- Receiver — `POST http://localhost:4998/hooks/taskevent` with header `X-AIQE-Token: change-me`

Smoke-test the webhook path:

```bash
curl -sS -X POST http://localhost:4998/hooks/taskevent \
  -H 'Content-Type: application/json' -H 'X-AIQE-Token: change-me' \
  -d '{"mode":"pr","repo":"orders-api","pr":201,"updated":"v1"}'
# -> {"accepted": true, ...}   (a second identical POST returns accepted:false — dedupe)
```

Manage the stack:

```bash
docker compose logs -f          # follow logs (run from deploy/local)
./deploy.sh --down              # stop; named volumes keep run history / queue / reviews
```

Equivalent Make targets from the repo root: `make deploy-local` (add `SEED=1`),
`make deploy-local-down`, and `make docker-build` (add `IMAGE=…` / `REAL=1`). The build engine is auto-detected — docker (with a live daemon), else podman, else podman inside the default WSL machine; podman consumes the same Dockerfile unchanged. Force one with `ENGINE=…`, e.g. `make docker-build ENGINE=podman`. `deploy/local/deploy.sh` applies the same policy to compose — docker compose (live daemon) → docker-compose → podman compose → podman-compose, each gated on a live backend so a leftover CLI with a dead daemon is skipped; override with `COMPOSE="podman compose" ./deploy.sh`. Note: `podman compose` needs a provider (docker-compose or podman-compose) reachable by podman.

---

## 2. OpenShift deployment

Prerequisites: `oc` logged in to your cluster (`oc login …`). No local Docker needed —
the image is built in-cluster.

```bash
cd deploy/openshift
./deploy.sh -n ai-qe            # create project ai-qe, build in-cluster, apply, wait
```

The script:

1. creates the project if absent,
2. runs an **in-cluster binary build** (`oc new-build --binary` + `oc start-build --from-dir`), pushing to the internal registry,
3. applies the Secret (`secret.yaml` if you made one, else `secret.example.yaml` with a warning),
4. renders the kustomization, substitutes the built image, and applies it,
5. waits for rollout and prints the Route URLs.

When it finishes you get two HTTPS Routes (edge TLS):

```
Dashboard:          https://ai-qe-dashboard-ai-qe.apps.<cluster-domain>
TaskEvent receiver: https://ai-qe-receiver-ai-qe.apps.<cluster-domain>/hooks/taskevent
```

### Set real tokens before exposing Routes

The Routes are public; protect them with the two service tokens:

```bash
cp secret.example.yaml secret.yaml
# edit secret.yaml: set AIQE_UI_TOKEN and AIQE_HOOK_TOKEN (and real-mode creds if any)
./deploy.sh -n ai-qe            # re-run — it applies secret.yaml and rolls out
```

Reach the dashboard with `https://…/?token=<AIQE_UI_TOKEN>` (it sets an HttpOnly
cookie), or send `Authorization: Bearer <AIQE_UI_TOKEN>`.

### Using a prebuilt image instead of the in-cluster build

Build and push anywhere, then point the deploy at it:

```bash
make docker-build IMAGE=quay.io/acme/ai-qe:1.0
docker push quay.io/acme/ai-qe:1.0
IMAGE=quay.io/acme/ai-qe:1.0 ./deploy.sh -n ai-qe
```

### Tear down

```bash
./deploy.sh --delete -n ai-qe   # removes everything except the PVC (run history)
oc delete pvc ai-qe-reports -n ai-qe   # also drop persisted state
```

That first line is enforced, not merely intended. It used to run `oc delete -k .`,
and `pvc.yaml` is one of the kustomization's resources — so the teardown deleted the
PVC holding every run record, plan, approval and audit event while printing
"PVC ai-qe-reports left in place". The doc above and the script's own message both
promised preservation; only the command disagreed. It now deletes the manifests by
file, and `test_deploy_manifests.py` pins BOTH directions: the PVC is never in that
list, and every other kustomization resource always is — so a new resource cannot be
added to the deploy and silently survive the teardown.

---

## 3. Vanilla Kubernetes

The manifests are plain Kubernetes plus one OpenShift Route. To run on upstream
Kubernetes:

- Supply a prebuilt image (no in-cluster build): `IMAGE=… ./deploy.sh -n ai-qe`
  — the script auto-detects `kubectl` when `oc` is absent.
- Swap networking: use `ingress.yaml` (edit the hosts/TLS) instead of `route.yaml`,
  and drop `route.yaml` from `kustomization.yaml`'s `resources`.
- If your CSI driver provisions **root-owned** volumes, the non-root UID can't write
  `reports/`. Add a pod-level `fsGroup` to `deployment.yaml`:

  ```yaml
  spec:
    template:
      spec:
        securityContext:
          runAsNonRoot: true
          fsGroup: 1001          # OpenShift assigns this itself — only add on vanilla k8s
  ```

Without an Ingress you can still try it via port-forward:

```bash
kubectl -n ai-qe port-forward deploy/ai-qe 4999:4999 4998:4998
```

---

## 4. Configuration reference

Config is injected as environment variables — the ConfigMap for non-secret values,
the Secret for tokens and credentials. (Env vars take precedence over any `.env`, so
the same image serves every environment.)

| Variable | Default | Meaning |
|---|---|---|
| `AIQE_MOCK` | `1` | `1` = mock adapters + demo estate (no external calls); `0` = real adapters |
| `AIQE_UI_HOST` / `AIQE_UI_PORT` | `0.0.0.0` / `4999` | Dashboard bind (set to `0.0.0.0` in containers) |
| `AIQE_HOOK_HOST` / `AIQE_HOOK_PORT` | `0.0.0.0` / `4998` | Receiver bind |
| `AIQE_UI_TOKEN` | *(unset)* | If set, dashboard requires `?token=` / Bearer auth |
| `AIQE_HOOK_TOKEN` | *(unset)* | If set, receiver requires `X-AIQE-Token` |
| `AIQE_HOOK_AUTORUN` | `0` | `1` = drain the queue in-process when an event is accepted |
| `SCM_KIND` | `github` | Real-mode SCM adapter: `github` \| `bitbucket` \| `stash` |
| `AIQE_SSO_HEADER` | *(unset)* | Trust this reverse-proxy identity header (e.g. `X-Forwarded-User`); fails closed — see the SSO section below |
| `MAX_COST_USD_PER_RUN` / `MAX_WALLCLOCK_MIN` | org-config `budgets:` | Per-run cost / wall-clock ceilings; over-limit runs abort with exit 77 before the gate |
| `AIQE_OPENHANDS` | `auto` | How much an OpenHands outage matters: `off` \| `auto` (degraded, never fatal) \| `required` |
| `AIQE_STATUS_URL` | *(unset)* | Dashboard base URL linked from PR build statuses and comments |

Real-mode credentials (`ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, `JIRA_URL`,
`ATLASSIAN_MCP_TOKEN`, `CONFLUENCE_URL`, `OPENHANDS_URL`, …) live in the Secret; the
full list is in [`.env.example`](../.env.example) and the dashboard **Settings** view.

> **Properties-file baseline.** Everything above can also come from a Java-style
> `aiqe.properties` file — the natural shape for an OpenShift **ConfigMap mount**:
> mount it at `/app/aiqe.properties` (or point `AIQE_PROPERTIES` at the path) and
> both the pipeline and every Python entry point load it at startup. Precedence is
> `aiqe.properties < .env < explicit environment`, so ConfigMap values are the
> deploy-time baseline and container env vars still win. `make config` shows which
> file loaded and the key names it set (never values).

> **Persisting Settings-UI edits.** The Settings view writes `.env`. In a container
> that file is inside the image layer and is lost on restart, so treat the
> ConfigMap/Secret as the source of truth in a cluster. To make UI edits durable,
> mount a small writable volume at `/app/.env` (a Secret or a PVC subPath).

---

## 5. Going real (`AIQE_MOCK=0`)

Mock mode needs nothing external. Real mode needs the Claude CLI and (for UI suites)
Playwright browsers, plus credentials:

1. **Build the real-tools image:**
   ```bash
   make docker-build IMAGE=quay.io/acme/ai-qe:real REAL=1   # adds claude CLI + Playwright chromium
   ```
2. **Provide credentials** in the Secret (`ANTHROPIC_API_KEY` and your SCM/JIRA/etc. tokens).
3. **Flip the mode:** set `AIQE_MOCK: "0"` in the ConfigMap.
4. Redeploy with the real image: `IMAGE=quay.io/acme/ai-qe:real ./deploy.sh -n ai-qe`.

Validate credentials before a real run with the staged smoke test
(`make smoke-openhands`, documented in [integrations/openhands.md](integrations/openhands.md)).

---

## 6. Operations

- **Health** — the receiver exposes an unauthenticated `GET /healthz` (also `GET /`)
  returning a small JSON status, which the Kubernetes probes use. The dashboard has no
  unauthenticated health route, so its probes use a TCP check on the port (`GET /api/queue`
  works for manual checks when no UI token is set).
- **Logs** — `oc logs deploy/ai-qe -c dashboard -f` (or `-c receiver`); locally
  `docker compose logs -f`.
- **Scaling** — do **not** raise `replicas`: the single-writer model requires exactly
  one pod against the RWO volume (`strategy: Recreate` enforces no overlap on rollout).
- **Backups** — everything durable is under the `ai-qe-reports` PVC; snapshot or copy
  it to preserve run history, the review board, and the work queue.
- **Retention** — `make prune KEEP=200` trims old run records/diffs; run it
  periodically (e.g. an OpenShift `CronJob` invoking `python3 bin/qa.py prune`),
  or run `make maintain` nightly — it also rebuilds the retrieval substrate.
  On OpenShift/Kubernetes this is `deploy/openshift/cronjob.yaml` (applied by
  `oc apply -k .`), which did not exist until 2026-08-04: the docs said to run
  maintenance nightly and nothing did, so a by-the-book deployment took NO
  state-bundle snapshots. Its exit code is meaningful — `make maintain` now
  exits 1 when a LOCAL step fails and names it, where it previously ignored
  every step failure and exited 0 under an unconditional "maintenance
  complete". A step that depends on an external system (SCM, the embedding
  endpoint) reports `DEGRADED` and keeps the job green, so a red CronJob means
  something on this side is actually broken
  (`reports/knowledge-index/`, derived data that a fresh deployment restores
  with `make index-rebuild`) and runs the cost regression alarm.
- **Upgrades** — rebuild the image and re-run `deploy.sh`; the PVC (state) survives the
  `Recreate` rollout.

### Request limits at the trigger ingress

The receiver binds `0.0.0.0` in both the Dockerfile and `configmap.yaml`, so its
request-body handling is exposed to whatever can reach the Route. Both servers read
bodies through `engine/lib/http_body.read_body()`, which enforces this contract:

| Route | Body limit |
|---|---|
| `POST /hooks/ci/results` (raw JUnit/Jenkins) | **5 MB** |
| Every other receiver route (small JSON envelopes) | **1 MB** |
| Dashboard API (`bin/dashboard_server.py`) | **2 MB** |

| Client behaviour | Response |
|---|---|
| `Content-Length` unparseable or negative | `400` |
| Declared size over the route's limit | `413`, refused **before** the body is read |
| Declared more than it sent, then stopped | `400` after the socket timeout |
| Under the limit, honest | processed |

The ordering is the point, and each part replaced a measured failure against a
running receiver:

- **Route first, then read.** The 5 MB cap used to be applied *after* the whole body
  was in memory, so the check preventing a huge allocation ran once the allocation
  had happened. A 3 MB body was accepted on `/hooks/taskevent`, which has a 1 MB cap.
- **An unparseable `Content-Length` is a 400, not a crash.** It used to raise
  `ValueError` out of the handler: the client got no response line at all and a
  traceback landed in the log.
- **A lying `Content-Length` cannot hold a worker thread.** `Content-Length: 10000000`
  with a short body used to block forever. Each such connection ties up a thread, so a
  handful stop the ingress accepting PR and JIRA events — silently, because nothing
  fails, it just never answers.

Two limits are stated rather than glossed. A body overshooting the cap by more than
2× is refused *without* draining, so a grossly oversized request may see a connection
reset instead of a readable `413` — that is the trade for not waiting on bytes that
may never arrive. And a request that lies *under* the limit is bounded by the
handlers' 30-second `timeout`, not refused instantly: 30 seconds of one thread, not
forever. Both servers must keep that class-level `timeout` set, or the socket read has
no deadline to hit.

If you front the receiver with a proxy, set its own body cap at or below these values
so an oversized request is refused at the edge.

See [diagrams.md](diagrams.md) for the runtime architecture and
[user-guide.md](user-guide.md) for operating the platform once it is up.

## SSO in front of the dashboard (reverse-proxy header auth)

The dashboard trusts a single identity header when — and only when — you tell it to:

```bash
AIQE_SSO_HEADER=X-Forwarded-User      # oauth2-proxy/oauth-proxy default
```

Rules the implementation enforces and you must honor in the deployment:

- **Only enable it behind a proxy that terminates auth and OVERWRITES the header**
  (OpenShift oauth-proxy sidecar, oauth2-proxy, nginx with auth_request). The header
  is trusted verbatim; a directly reachable server with SSO on is spoofable —
  keep the Service/Route pointing at the proxy, never at the app port.
- **Fails closed:** with the variable set, a request without the header gets 401,
  so a proxy misconfiguration can never silently expose the dashboard.
- `Authorization: Bearer <AIQE_UI_TOKEN>` still authenticates API clients that
  bypass the proxy (health checks, CLI), acting as `token-client`.
- The identity signs actions: review marks and plan approvals default their
  `by`/`reviewer` to the SSO user when not explicitly provided, and the sidebar
  shows who is signed in.

OpenShift sketch: add an `openshift/oauth-proxy` sidecar to the dashboard pod
(`--upstream=http://localhost:4999`, `--pass-user-headers`), point the Route at the
proxy port, and set `AIQE_SSO_HEADER=X-Forwarded-User` on the app container.
