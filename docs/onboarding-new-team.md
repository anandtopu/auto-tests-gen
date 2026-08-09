# Onboarding a New Team / Estate (target: ≤1 day)
1. Fork this control repo template.
2. Fill registry/repo-registry.yaml (source + test repos) and registry/org-config.yaml.
3. Drop templates/test-repo/* into each test repo; templates/source-repo/* into app repos.
4. Add trigger config (triggers/…) matching the team's SCM + CI.
5. Run `make bootstrap REPO=<each test repo>`; QE reviews the queue; merge catalog PR.
6. `make test-routing && make eval` — green means go.

## Before you onboard a repo: what onboarding grants

Onboarding a **test** repo grants that repo's committers code execution on the
platform host. This is by design, not a defect: the gate's whole job is to lint
and RUN the repo's tests before it will commit anything, and it takes those
commands from the repo's own `.ai-qe/config.yaml`.

The consequence is worth stating plainly, because it is easy to miss:

- The gate reads `commands.{lint,test}` from the **committed** config
  (`git show HEAD:`) and executes them. That defends against a *run* rewriting
  what gets executed — an LLM phase cannot escalate this way (§5.5.1). It is
  **not** a defence against a repo whose committers you do not trust.
- Those commands inherit the environment `engine/pipeline.sh` builds, which
  evals the `aiqe.properties` / `.env` defaults. Every credential configured
  there — SCM token, JIRA token, `ANTHROPIC_API_KEY`, SMTP — is reachable from
  a command that repo controls.

So the practical rule is either of:

- onboard only repos already inside the same trust boundary as those
  credentials (the usual case: your own team's E2E repos), **or**
- give the platform its own narrowly-scoped tokens, so the blast radius of a
  hostile or compromised test repo is bounded to what the platform itself may
  do (branch-scoped SCM write, project-scoped JIRA comment).

A repo you would not give your CI credentials to is not a repo to onboard here.
