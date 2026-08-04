"""The backup must contain the deployment's state, not the image's.

`state_bundle.collect()` resolved every include against ROOT, so under R12
relocation (AIQE_STATE_DIR, or a per-path knob) it read the IMAGE's factory
copies instead of the operator's state on the volume.

Measured in a real container (podman, AIQE_STATE_DIR=/state, read-only rootfs):
a marker appended to /state/registry/repo-registry.yaml appeared in NO member of
the resulting bundle, while the export still printed "exported 29 file(s)" and
the nightly maintenance summary still said `ok` for the state-bundle snapshot.

That is the worst shape a defect can take here. A backup that silently holds
somebody else's data does not merely fail — it stops the operator worrying, and
the failure surfaces on the one day it cannot be fixed. It is also invisible on
a dev checkout, where ROOT and the state root are the same directory, which is
why nothing caught it.

The other half is portability: reading moves, the archive NAME must not. A
bundle taken from a relocated deployment has to import into one laid out
differently, or the bundle is only good for the machine it came from.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))


@pytest.fixture
def relocated(tmp_path, monkeypatch):
    """A state root that is NOT the checkout, with a marked registry in it."""
    import importlib
    state = tmp_path / "state"
    (state / "registry").mkdir(parents=True)
    (state / "catalog").mkdir(parents=True)
    real = (ROOT / "registry/repo-registry.yaml").read_text(encoding="utf-8")
    (state / "registry/repo-registry.yaml").write_text(
        real + "\n# ZZ-VOLUME-MARKER\n", encoding="utf-8")
    (state / "catalog/zz-volume.jsonl").write_text('{"id":"zz"}\n', encoding="utf-8")
    monkeypatch.setenv("AIQE_STATE_DIR", str(state))
    import app_paths
    importlib.reload(app_paths)
    import state_bundle
    importlib.reload(state_bundle)
    yield state, state_bundle
    monkeypatch.undo()
    importlib.reload(app_paths)
    importlib.reload(state_bundle)


def test_the_bundle_reads_the_state_volume_not_the_image(relocated):
    state, state_bundle = relocated
    src = state_bundle.source_of("registry/repo-registry.yaml")
    assert str(state) in str(src), \
        f"the backup reads {src} — the image copy, not the deployment's state"
    assert "ZZ-VOLUME-MARKER" in src.read_text(encoding="utf-8")


def test_a_file_that_exists_only_on_the_volume_is_collected(relocated):
    state, state_bundle = relocated
    names = {r.as_posix() for r in state_bundle.collect()}
    assert "catalog/zz-volume.jsonl" in names, \
        "a catalog file present only on the state volume was not bundled"


def test_archive_names_stay_repo_relative_so_the_bundle_stays_portable(relocated):
    """Reading moves; naming must not. A bundle whose members encode the source
    deployment's layout can only be restored onto that same layout."""
    state, state_bundle = relocated
    for rel in state_bundle.collect():
        posix = rel.as_posix()
        assert not posix.startswith(str(state).replace("\\", "/")), \
            f"member {posix} carries an absolute source path"
        assert ".." not in posix, f"member {posix} escapes the archive root"


def test_nothing_resolves_state_paths_by_string_concatenation():
    """The invariant, not today's value — the same shape as
    test_catalog_paths: a future edit re-deriving `ROOT / rel` for a mutable
    path silently restores the wrong-data backup."""
    src = (ROOT / "engine/lib/state_bundle.py").read_text(encoding="utf-8")
    body = src[src.index("def source_of("):src.index("def _sha(")]
    assert "app_paths.resolve_rel" in body, \
        "the bundle resolves state locations itself again"
    collect = src[src.index("def collect("):src.index("def _sha(")]
    assert "ROOT / d" not in collect and "ROOT / f" not in collect, \
        "collect() is back to resolving includes against ROOT"


def test_an_exported_bundle_actually_carries_the_volumes_bytes(relocated, tmp_path):
    """End to end, because the parts can each look right and still not compose:
    if the manifest hashed the image copy while the tar carried the volume's,
    every file would fail verification on import and the restore would refuse.

    This is the assertion that reproduces the measured container finding — a
    marker written on the state volume, absent from every member of the bundle."""
    import hashlib
    import json
    import tarfile
    state, state_bundle = relocated
    out = state_bundle.export(dest=tmp_path / "b.tar.gz")
    with tarfile.open(out) as t:
        manifest = json.loads(t.extractfile("manifest.json").read())
        members = {m.name: t.extractfile(m).read()
                   for m in t.getmembers() if m.isfile()}
    body = members["state/registry/repo-registry.yaml"]
    assert b"ZZ-VOLUME-MARKER" in body, \
        "the bundle carries the image's registry, not the deployment's"
    assert any(b"ZZ-VOLUME-MARKER" in v for v in members.values())
    recorded = manifest["files"]["registry/repo-registry.yaml"]
    assert recorded == hashlib.sha256(body).hexdigest(), \
        ("the manifest hash and the archived bytes disagree — import verifies "
         "each file against the manifest, so every restore would be refused")
