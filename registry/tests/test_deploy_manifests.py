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
