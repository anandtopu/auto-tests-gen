"""Read-modify-write state stores never read an unreadable file as empty.

`fs_lock.read_json_guarded` exists because returning a default for a file that
IS there is the silent data-loss path: the caller's next save overwrites real
data with the default. Two stores were bypassing it —
`selection.py._load_all` and `retry_policy.py._load` both caught OSError and
returned `{}`, then read-modify-wrote over the file.

Selection is the sharp one. It holds the reviewer's include/exclude rulings
with who and why, and the module's own rule is that an item nobody ruled on is
INCLUDED. So a transient sharing violation — an AV scanner or a concurrent
dashboard render holding the file for a few hundred ms, exactly what
`replace_atomic` retries for — would drop every other decision, and the
dropped exclusions would come back as inclusions. The reviewer believes tests
were dropped while they ship.

Retry counters are the same shape with a different consequence: reset the
history a limiter decides on and the limiter becomes a permit.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import fs_lock  # noqa: E402
import retry_policy  # noqa: E402
import selection  # noqa: E402

SRC = {
    "selection": (ROOT / "engine/lib/selection.py").read_text(encoding="utf-8"),
    "retry_policy": (ROOT / "engine/lib/retry_policy.py").read_text(encoding="utf-8"),
}


@pytest.mark.parametrize("mod", sorted(SRC))
def test_the_loader_uses_the_guarded_reader(mod):
    src = SRC[mod]
    assert "read_json_guarded" in src, (
        f"{mod} no longer reads through fs_lock.read_json_guarded — an "
        "unreadable file would be read as empty and the next save would "
        "overwrite it")


@pytest.mark.parametrize("mod", sorted(SRC))
def test_the_loader_does_not_swallow_oserror(mod):
    """The specific shape that caused this: `except (OSError, ValueError)`
    around the load, returning a default."""
    src = SRC[mod]
    loader = "def _load_all(" if mod == "selection" else "def _load("
    body = src.split(loader, 1)[1].split("\ndef ", 1)[0]
    assert "except (OSError" not in body and "except OSError" not in body, (
        f"{mod}'s loader swallows OSError again — that is the silent "
        "data-loss path read_json_guarded exists to close")


def test_an_unreadable_selection_store_raises_rather_than_reading_empty(tmp_path, monkeypatch):
    """The behaviour, not just the call. A directory where the file should be
    is an OSError the loader must NOT translate into 'nothing decided'."""
    target = tmp_path / "reports/plans/selection.json"
    target.mkdir(parents=True)          # a directory: open() raises OSError
    monkeypatch.setattr(selection, "FILE", target)
    with pytest.raises(Exception) as exc:
        selection._load_all()
    assert not isinstance(exc.value, AssertionError)


def test_a_corrupt_selection_store_is_quarantined_not_silently_dropped(tmp_path, monkeypatch):
    """Corrupt is different from unreadable: keep the bytes, keep working."""
    target = tmp_path / "selection.json"
    target.write_text('{"PROJ-1": {"scenarios":', encoding="utf-8")   # torn
    monkeypatch.setattr(selection, "FILE", target)
    assert selection._load_all() == {}
    quarantined = list(tmp_path.glob("selection.json.corrupt-*"))
    assert quarantined, "the corrupt bytes were discarded instead of preserved"


def test_a_readable_store_still_loads(tmp_path, monkeypatch):
    """The other direction — a guard that refuses everything would pass the
    tests above while breaking the feature."""
    target = tmp_path / "selection.json"
    payload = {"PROJ-9": {"scenarios": {"S1": {"included": False, "by": "qa"}}}}
    target.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(selection, "FILE", target)
    assert selection._load_all() == payload


def test_retry_counters_survive_a_readable_file(tmp_path, monkeypatch):
    target = tmp_path / "retries.json"
    target.write_text(json.dumps({"PROJ-1": {"attempts": 2}}), encoding="utf-8")
    monkeypatch.setattr(retry_policy, "FILE", target)
    assert retry_policy._load()["PROJ-1"]["attempts"] == 2
