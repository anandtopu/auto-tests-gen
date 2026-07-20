# Onboarding a New Team / Estate (target: ≤1 day)
1. Fork this control repo template.
2. Fill registry/repo-registry.yaml (source + test repos) and registry/org-config.yaml.
3. Drop templates/test-repo/* into each test repo; templates/source-repo/* into app repos.
4. Add trigger config (triggers/…) matching the team's SCM + CI.
5. Run `make bootstrap REPO=<each test repo>`; QE reviews the queue; merge catalog PR.
6. `make test-routing && make eval` — green means go.
