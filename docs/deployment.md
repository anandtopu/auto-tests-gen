# Deployment Guide

How to run the AI QE platform as a long-running service — locally with Docker
Compose, and on a remote OpenShift / Kubernetes cluster. The demo/mock mode needs
**no credentials and makes no external calls**, so you can stand the whole thing up
first and wire real integrations later.

## What gets deployed

The platform runs two long-lived HTTP services that share filesystem state:

| Service | Port | Role |
|---|---|---|
| **Dashboard** (`bin/dashboard_server.py`) | 4999 | Seven-view QA UI; also runs the pipeline when you drain the work queue |
| **TaskEvent receiver** (`bin/taskevent_receiver.py`) | 4998 | Webhook endpoint: validates, de-duplicates, and enqueues events |

Both coordinate through an advisory lock (`engine/lib/fs_lock.py`) on a shared
filesystem, so **they must run co-located** (same pod / same volumes). The pipeline
(`engine/pipeline.sh`) is spawned on demand and holds an exclusive per-checkout run
lock — hence a **single replica, single writer** model.

State layout:

| Path | Persistence | Contents |
|---|---|---|
| `reports/` | **persistent volume** | run records + archived diffs, review board, work queue, exports |
| `workspace/`, `out/` | ephemeral scratch | per-run clones and phase artifacts (safe to lose) |

The container image (`Dockerfile`) bundles the app, Python 3 + PyYAML, Node 20 (the
demo estate and the `node --test` gate), and bash/git/curl/jq. It is
**OpenShift-compatible**: the app tree is group-0 writable and the process runs as an
arbitrary non-root UID — no root, no fixed UID.

---

## 1. Local deployment (Docker Compose)

Prerequisites: Docker with the Compose plugin.

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
`make deploy-local-down`, and `make docker-build` (add `IMAGE=…` / `REAL=1`).

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

Real-mode credentials (`ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, `JIRA_URL`,
`ATLASSIAN_MCP_TOKEN`, `CONFLUENCE_URL`, `OPENHANDS_URL`, …) live in the Secret; the
full list is in [`.env.example`](../.env.example) and the dashboard **Settings** view.

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

- **Health** — the dashboard's `GET /api/queue` returns `200` (no auth needed when no
  UI token is set); probes use a TCP check on each port.
- **Logs** — `oc logs deploy/ai-qe -c dashboard -f` (or `-c receiver`); locally
  `docker compose logs -f`.
- **Scaling** — do **not** raise `replicas`: the single-writer model requires exactly
  one pod against the RWO volume (`strategy: Recreate` enforces no overlap on rollout).
- **Backups** — everything durable is under the `ai-qe-reports` PVC; snapshot or copy
  it to preserve run history, the review board, and the work queue.
- **Retention** — `make prune KEEP=200` trims old run records/diffs; run it
  periodically (e.g. an OpenShift `CronJob` invoking `python3 bin/qa.py prune`).
- **Upgrades** — rebuild the image and re-run `deploy.sh`; the PVC (state) survives the
  `Recreate` rollout.

See [diagrams.md](diagrams.md) for the runtime architecture and
[user-guide.md](user-guide.md) for operating the platform once it is up.
