# Integrating Bitbucket Cloud and Stash (Bitbucket Server / Data Center)

Both products serve the **Scm port** (clone, diff, branch, comment) and provide a
**trigger path** for Workflow A. They are different APIs with different auth — pick the
section that matches your deployment. Mixed estates can register repos per-SCM in the
registry (`scm: bitbucket` vs `scm: stash`).

| | Bitbucket **Cloud** (bitbucket.org) | **Stash** / Bitbucket **Server/DC** (self-hosted) |
|---|---|---|
| Adapter | `adapters/scm/bitbucket.sh` (REST 2.0) | `adapters/scm/stash.sh` (REST 1.0) |
| Auth | workspace/app access token (`BITBUCKET_TOKEN`) | HTTP access token (`STASH_TOKEN`, Bearer) |
| Clone URL | `bitbucket.org/<workspace>/<repo>.git` | `<STASH_URL>/scm/<PROJECT>/<repo>.git` |
| Atlassian MCP coverage | ✅ (same connection as Jira) | ❌ — REST adapter only |
| CI trigger | Bitbucket Pipelines (Path 2) | Webhook → Jenkins (Path 3) |
| OpenHands native trigger | ✅ | ❌ (use Path 3) |

---

## A. Bitbucket Cloud

### 1. Credentials

Create a workspace or repository **access token** (or app password) with:
`repository:read` on source repos, `repository:write` on test repos (branch pushes
only — protect main), `pullrequest:read` + `pullrequest:write` (comments). Fill
`.env`: `BITBUCKET_TOKEN=...`.

### 2. Adapter configuration

Edit [adapters/scm/bitbucket.sh](../../adapters/scm/bitbucket.sh): replace the
`workspace` placeholder in the `BB=` base URL and clone URLs with your workspace slug.
Verbs: `changed_files` (PR diffstat), `clone_ro`, `clone_rw` (creates
`test/<KEY>-ai-qe`), `comment`.

### 3. Registry entries

```yaml
source_repositories:
  - name: catalog-api
    scm: bitbucket
    url: <workspace>/catalog-api
    ...
```

Runs against Bitbucket repos select the adapter with `SCM_KIND=bitbucket` in the
trigger environment (see the Pipelines file below).

### 4. Trigger — Bitbucket Pipelines (Path 2)

Copy [triggers/bitbucket-pipelines/bitbucket-pipelines.yml](../../triggers/bitbucket-pipelines/bitbucket-pipelines.yml)
into each source repo. It runs on every PR: clones this control repo, registers MCP,
and runs `SCM_KIND=bitbucket bash engine/pipeline.sh pr $BITBUCKET_REPO_SLUG $BITBUCKET_PR_ID`.
Set `BITBUCKET_TOKEN`, `ANTHROPIC_API_KEY`, `ATLASSIAN_MCP_TOKEN`, `SLACK_WEBHOOK_URL`
as **secured repository/workspace variables**. Alternatively use OpenHands' native
Bitbucket Cloud integration (Path 1 — see [openhands.md](openhands.md)).

### 5. Verify

```bash
BITBUCKET_TOKEN=... bash adapters/scm/bitbucket.sh changed_files <repo-slug> <pr-id>
# expect: the PR's changed file paths, one per line
```

Then open a PR touching a `testable_path` and watch the Pipelines step comment the
run summary.

---

## B. Stash / Bitbucket Server / Data Center

### 1. Credentials

Create an **HTTP access token** (profile → Manage account → HTTP access tokens, or a
project/repo-scoped token on newer DC versions) with `PROJECT_READ` + `REPO_READ` on
source repos and `REPO_WRITE` on test repos. Fill `.env`:

```bash
STASH_URL=https://stash.company.com     # base URL, no trailing slash
STASH_PROJECT=ENG                       # DEFAULT project — used only for repos that
                                        # do not declare their own (see "Multiple
                                        # projects" below); may be left unset if every
                                        # repo's url is PROJECT/slug
STASH_TOKEN=<http access token>
```

If the service account needs `PROJECT_READ`/`REPO_READ` across several projects, grant
them in each project rather than assuming one.

Branch permissions on every test repo: restrict pushes to `test/*` for the service
account; deny direct pushes to the default branch (the gate is the only push path).

### 2. Adapter

[adapters/scm/stash.sh](../../adapters/scm/stash.sh) implements the same four verbs
against `rest/api/1.0/projects/<PROJECT>/repos/...`:

- `changed_files <repo> <pr>` → `/pull-requests/<pr>/changes` (file paths)
- `clone_ro|clone_rw <repo> <dir> [branch]` → `<STASH_URL>/scm/<PROJECT>/<slug>.git`
- `comment <repo> <pr> <text>` → PR comment

#### Multiple projects (app repos and E2E repos under different project keys)

The adapter resolves each repo's **project and slug individually** — a single global
`STASH_PROJECT` is no longer assumed. For every verb, the repo name is passed to
[engine/lib/stash_target.py](../../engine/lib/stash_target.py), which resolves the
project in this order:

1. the entry's explicit `stash_project:` field, if set;
2. the first path segment of its `url:` (`ENG/payments-api` → project `ENG`,
   slug `payments-api`);
3. `STASH_PROJECT` — the fallback default for repos that declare neither.

So an estate with app repos under `ENG` and E2E suites under `QA` needs no global
project at all — each registry entry's `url:` carries its own:

```yaml
source_repositories:
  - { name: payments-api,   scm: stash, url: ENG/payments-api }   # project ENG
test_repositories:
  - { name: e2e-payments,   scm: stash, url: QA/e2e-payments }    # project QA
```

The slug may also differ from the registry `name` (the `url:` last segment wins), so a
friendly registry name can map to a different Stash repo slug.

### 3. Registry entries

```yaml
source_repositories:
  - name: payments-api
    scm: stash
    url: ENG/payments-api
    ...
```

`SCM_KIND=stash` in the trigger environment selects this adapter (it is registered in
`registry/org-config.yaml` under `adapters.scm.stash`).

### 4. Trigger — webhook → Jenkins (Path 3)

Server/DC has no Pipelines and no OpenHands-native integration, so wire the repo
webhook to Jenkins:

1. Repo → Settings → Webhooks → Create: event **Pull request: opened** (and
   **Source branch updated**), URL = your Jenkins generic-webhook endpoint.
2. Install the Jenkins job from [triggers/jenkins/Jenkinsfile](../../triggers/jenkins/Jenkinsfile);
   map the webhook payload to parameters `MODE=pr`, `TARGET=<repo slug>`,
   `PR=<pull request id>`; add `STASH_URL`/`STASH_PROJECT`/`STASH_TOKEN` to the
   Jenkins credentials block and export `SCM_KIND=stash` in the run stage.
3. The same Jenkins endpoint serves the Jira automation rule for Workflow B
   (`MODE=jira`, `TARGET=<KEY>`).

### 5. Verify

```bash
# Read side (any open PR):
STASH_URL=... STASH_PROJECT=ENG STASH_TOKEN=... \
  bash adapters/scm/stash.sh changed_files payments-api 42

# Clone side:
STASH_URL=... STASH_PROJECT=ENG STASH_TOKEN=... \
  bash adapters/scm/stash.sh clone_ro payments-api /tmp/stash-check && ls /tmp/stash-check
```

Then open a PR, confirm the webhook reaches Jenkins (job build with the right
parameters), and check the PR comment + `make status` in the control repo.

---

## Troubleshooting (both)

| Symptom | Check |
|---|---|
| 401 on `changed_files` | Cloud: token vs app-password auth style; Server: token sent as `Authorization: Bearer` and not expired |
| Clone prompts for password | Token not embedded in clone URL — adapter builds it; check `STASH_TOKEN`/`BITBUCKET_TOKEN` exported in the trigger env |
| `PUSH_SKIPPED` in gate output | Expected in demo mode (no remote); in real runs check test-repo `REPO_WRITE`/branch permissions on `test/*` |
| Wrong adapter used | `SCM_KIND` env var in the trigger (github default) — must be `bitbucket` or `stash` |
| PR comment missing | Comment scope on the token (`pullrequest:write` / `REPO_READ`+comment); check adapter output in the run log |
| Server API path 404s | You're on Cloud syntax (`2.0`) against Server or vice-versa — the two APIs are not interchangeable; use the matching adapter |
