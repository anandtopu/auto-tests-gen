"""The deploy scripts must not destroy what they say they preserve.

`./deploy.sh --delete` ran `oc delete -k .`, and pvc.yaml is one of the
kustomization's resources — so it deleted the PersistentVolumeClaim holding
every run record, plan, approval, exported bundle and audit event. The very next
line it printed was:

    (PVC ai-qe-reports left in place — delete it manually to drop run history)

An operator tearing down a namespace to redeploy would have believed their
history survived, and found out only when they went looking for it. A
destructive command that reports the opposite of what it did is the worst kind,
because by the time the report is contradicted there is nothing to restore from.

These pin the teardown against the deploy, and the image substitution that makes
a built image actually reach every container.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy/openshift"
SCRIPT = DEPLOY / "deploy.sh"


def _kustomization():
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load((DEPLOY / "kustomization.yaml").read_text(encoding="utf-8"))


def _delete_files():
    """The manifest list the --delete branch iterates."""
    src = SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"for f in ([^\n;]+); do", src)
    assert m, "the --delete branch no longer iterates a manifest list"
    return [x for x in m.group(1).split() if x.endswith(".yaml")]


def test_teardown_never_deletes_the_volume_holding_run_history():
    src = SCRIPT.read_text(encoding="utf-8")
    branch = src.split('if [ "$ACTION" = "delete" ]', 1)[1].split("exit 0", 1)[0]
    assert "delete $NSARG -k ." not in branch, \
        "`delete -k .` is back — pvc.yaml is in the kustomization, so this deletes it"
    assert "pvc.yaml" not in _delete_files(), \
        "the teardown deletes the PVC it promises to leave in place"


def test_the_teardown_still_removes_everything_else_the_deploy_created():
    """The other direction: excluding the PVC must not quietly start leaving
    other resources behind. A resource added to kustomization.yaml and forgotten
    here would survive a teardown and collide with the next deploy."""
    resources = set(_kustomization()["resources"])
    deleted = set(_delete_files())
    missed = resources - deleted - {"pvc.yaml"}
    assert not missed, (
        f"these are deployed but never torn down: {sorted(missed)} — add them to "
        f"the --delete loop in deploy.sh")
    assert not (deleted - resources), \
        f"the teardown deletes files the deploy does not apply: {sorted(deleted - resources)}"


def test_the_message_matches_what_the_code_does():
    """The specific lie that motivated this file."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "left in place" in src, "the promise about the PVC vanished"
    assert "delete $NSARG pvc ai-qe-reports" in src, \
        "telling someone to delete it manually without saying how is half a message"


def test_every_container_image_is_substitutable_by_the_deploy():
    """deploy.sh substitutes the built image with one global sed on the rendered
    output. A manifest whose image literal differs by even a tag would silently
    keep `ai-qe-platform:latest`, which on a real cluster is either absent or a
    stale build — and nothing would say so."""
    pattern = re.search(r'sed "s\|image: ([^|]+)\|image: \$\{IMAGE\}\|g"',
                        SCRIPT.read_text(encoding="utf-8"))
    assert pattern, "deploy.sh no longer substitutes the image with a sed"
    expected = pattern.group(1)
    found = [(f.name, m.group(1))
             for f in sorted(DEPLOY.glob("*.yaml"))
             for m in re.finditer(r"^\s*image:\s*(\S+)",
                                  f.read_text(encoding="utf-8"), re.M)]
    assert found, "no container images found in the manifests"
    wrong = sorted({(n, i) for n, i in found if i != expected})
    assert not wrong, (
        f"these images do not match the literal deploy.sh substitutes "
        f"({expected!r}), so the built image never reaches them: {wrong}")


def test_the_cronjob_is_covered_by_the_substitution():
    """Specifically: the nightly maintenance job runs the same image as the
    services. Left at :latest it would run whatever that tag happens to be —
    including a build older than the code that scheduled it."""
    cj = (DEPLOY / "cronjob.yaml").read_text(encoding="utf-8")
    assert "image: ai-qe-platform:latest" in cj


def test_no_container_replaces_the_image_entrypoint():
    """In Kubernetes `command:` replaces the image ENTRYPOINT and `args:`
    replaces CMD — the OPPOSITE mapping to docker-compose, where `command:`
    means CMD. deploy/local/docker-compose.yml uses `command:` correctly; the
    OpenShift manifests were written to look the same and therefore replaced
    ENTRYPOINT.

    The cost: `tini -> bin/container-entrypoint.sh` never ran in the deployment,
    so a fresh cluster did NO first-boot state seeding — the exact failure the 17
    checks in tests/entrypoint-smoke.sh exist to prevent, fixed in the script and
    then bypassed by the manifest that was supposed to run it — and tini was no
    longer PID 1. Proven with podman: replacing the entrypoint skips the seeding,
    overriding CMD runs it.

    A container MAY set `command:`, but only if it starts with the entrypoint
    itself — otherwise seeding is silently skipped again.
    """
    yaml = pytest.importorskip("yaml")
    entry = "container-entrypoint.sh"
    checked = 0
    for f in sorted(DEPLOY.glob("*.yaml")):
        for doc in yaml.safe_load_all(f.read_text(encoding="utf-8")):
            if not isinstance(doc, dict):
                continue
            spec = doc.get("spec") or {}
            tmpl = ((spec.get("jobTemplate") or {}).get("spec", {}).get("template")
                    or spec.get("template") or {})
            for ct in (tmpl.get("spec") or {}).get("containers", []) or []:
                checked += 1
                cmd = ct.get("command")
                if cmd:
                    assert any(entry in str(x) for x in cmd), (
                        f"{f.name}:{ct['name']} sets command: {cmd} — that REPLACES "
                        f"the image ENTRYPOINT, so first-boot state seeding never "
                        f"runs. Use args: instead.")
    assert checked >= 3, f"only inspected {checked} containers; the walk broke"


def test_compose_keeps_using_command_because_it_means_something_else_there():
    """The mirror of the pin above. In compose, `command:` IS the right field —
    'fixing' it to args there would be wrong, and this says so out loud so the
    two files are not made to look alike again."""
    yaml = pytest.importorskip("yaml")
    compose = yaml.safe_load(
        (ROOT / "deploy/local/docker-compose.yml").read_text(encoding="utf-8"))
    for name, svc in (compose.get("services") or {}).items():
        if "dashboard_server" in str(svc.get("command", "")) or \
           "taskevent_receiver" in str(svc.get("command", "")):
            assert "entrypoint" not in svc, (
                f"compose service {name} overrides entrypoint — that IS the way to "
                f"skip seeding here")
            break
    else:
        raise AssertionError("neither compose service runs a known entrypoint command")


def _deployment():
    import yaml
    docs = list(yaml.safe_load_all(
        (ROOT / "deploy/openshift/deployment.yaml").read_text(encoding="utf-8")))
    return [d for d in docs if d and d.get("kind") == "Deployment"]


def _configmap():
    import yaml
    for d in yaml.safe_load_all(
            (ROOT / "deploy/openshift/configmap.yaml").read_text(encoding="utf-8")):
        if d and d.get("kind") == "ConfigMap":
            return d.get("data") or {}
    return {}


def test_a_path_the_app_writes_is_never_left_on_the_read_only_rootfs():
    """readOnlyRootFilesystem is ON, so every path the app WRITES has to be a
    mount or redirected onto one.

    `.env` was neither. The Settings page writes it and the pipeline reads it
    back through the same resolver (props_file dotenv-defaults ->
    settings_store.load), but it defaults to /app/.env -- on the read-only
    rootfs, under none of the mounts (/app/reports, /app/workspace, /app/out,
    /tmp, /state). Every Settings save would have failed in-cluster, and no
    provider or credential set through the UI would ever reach a run. Nothing
    caught it because each half is correct on its own: the hardening is right,
    the resolver is right, and only their combination is wrong.
    """
    import sys
    sys.path.insert(0, str(ROOT / "engine/lib"))

    mounts, env_of = set(), {}
    for dep in _deployment():
        for c in dep["spec"]["template"]["spec"].get("containers", []):
            if not (c.get("securityContext") or {}).get("readOnlyRootFilesystem"):
                continue
            for m in c.get("volumeMounts", []):
                mounts.add(m["mountPath"].rstrip("/"))
            for e in c.get("env", []):
                if "value" in e:
                    env_of[e["name"]] = str(e["value"])
    assert mounts, "no read-only container declares mounts; this pin sees nothing"

    # Resolve it the way the app does, under the manifest's own environment --
    # so it holds whether the path is relocated by AIQE_STATE_DIR (today) or
    # pinned explicitly with AIQE_ENV_FILE. Asserting a config KEY would have
    # broken the moment the mechanism changed, while the risk stayed identical.
    import os
    import importlib
    import app_paths
    saved = {k: os.environ.get(k) for k in ("AIQE_STATE_DIR", "AIQE_ENV_FILE")}
    try:
        for k in saved:
            os.environ.pop(k, None)
            if k in {**env_of, **_configmap()}:
                os.environ[k] = {**env_of, **_configmap()}[k]
        importlib.reload(app_paths)
        resolved = app_paths.env_file().as_posix()
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        importlib.reload(app_paths)

    assert any(resolved.startswith(m + "/") for m in mounts), (
        f".env resolves to {resolved!r}, which is not under any writable mount "
        f"{sorted(mounts)} -- every Settings save fails in-cluster")


def test_the_writer_and_the_reader_resolve_the_same_env_file():
    """Redirecting it would be worse than useless if the pipeline read a
    different path: the UI would report success and runs would ignore it."""
    settings = (ROOT / "engine/lib/settings_store.py").read_text(encoding="utf-8")
    props = (ROOT / "engine/lib/props_file.py").read_text(encoding="utf-8")
    paths = (ROOT / "engine/lib/app_paths.py").read_text(encoding="utf-8")
    # ONE definition of where .env lives, for the reason the catalog has one:
    # settings_store resolving it itself is exactly how it stopped following
    # AIQE_STATE_DIR and landed on a read-only rootfs.
    assert "app_paths.env_file(" in settings, \
        "settings_store resolves .env itself again -- it will not follow AIQE_STATE_DIR"
    assert 'def env_file(' in paths and '"AIQE_ENV_FILE"' in paths, \
        "app_paths no longer owns the .env location"
    assert "settings_store.load()" in props, (
        "props_file stopped reading through settings_store -- the pipeline may "
        "now read a different .env than the Settings page writes")
