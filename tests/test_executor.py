"""File operations: never overwrite, survive problems, undo."""
import os
from pathlib import Path

from takeline.operations import copy_batch, latest_manifest, rename_batch, undo_manifest


def make(folder: Path, name: str, data: bytes = b"data") -> Path:
    p = folder / name
    p.write_bytes(data)
    return p


def test_copy_never_overwrites_and_preserves_times(tmp_path):
    src = make(tmp_path, "in.MOV", b"new")
    os.utime(src, (1_700_000_000, 1_700_000_000))
    dest = tmp_path / "out"
    dest.mkdir()
    existing = make(dest, "RAYBAN-x.MOV", b"precious")
    res = copy_batch([(1, src, dest / "RAYBAN-x.MOV")])
    assert res[0].status == "copied" and Path(res[0].dst) != existing
    assert existing.read_bytes() == b"precious"
    assert Path(res[0].dst).read_bytes() == b"new"
    assert int(Path(res[0].dst).stat().st_mtime) == 1_700_000_000
    assert src.exists()


def test_copy_error_does_not_stop_batch(tmp_path):
    good = make(tmp_path, "good.MOV")
    res = copy_batch([(1, tmp_path / "missing.MOV", tmp_path / "o" / "A.MOV"), (2, good, tmp_path / "o" / "B.MOV")])
    assert [r.status for r in res] == ["error", "copied"]
    assert not (tmp_path / "o" / "A.MOV").exists()


def test_cancelled_copy_leaves_no_partial_file(tmp_path):
    import threading
    import pytest
    from takeline.operations.executor import CancelledError, copy_one
    src = make(tmp_path, "big.MOV", b"x" * 10)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(CancelledError):  # exercises the cleanup path inside copy_one itself
        copy_one(src, tmp_path / "o" / "A.MOV", cancel)
    assert not (tmp_path / "o" / "A.MOV").exists()
    assert src.read_bytes() == b"x" * 10


def test_rename_chain_swap_and_foreign_blocker(tmp_path):
    a, b, c = make(tmp_path, "a.MOV", b"A"), make(tmp_path, "b.MOV", b"B"), make(tmp_path, "c.MOV", b"C")
    make(tmp_path, "taken.MOV", b"FOREIGN")
    plan = [(1, a, tmp_path / "b.MOV"), (2, b, tmp_path / "a.MOV"),   # swap (cycle)
            (3, c, tmp_path / "taken.MOV")]                            # blocked by a file outside the batch
    res, manifest = rename_batch(plan, tmp_path / "hist")
    st = {r.key: r.status for r in res}
    assert st == {1: "renamed", 2: "renamed", 3: "error"}
    assert (tmp_path / "b.MOV").read_bytes() == b"A" and (tmp_path / "a.MOV").read_bytes() == b"B"
    assert (tmp_path / "taken.MOV").read_bytes() == b"FOREIGN" and c.read_bytes() == b"C"
    assert not list(tmp_path.glob(".takeline-tmp-*"))
    # undo restores the swap too
    assert latest_manifest(tmp_path / "hist") == manifest
    undo = undo_manifest(manifest)
    assert sorted(r.status for r in undo) == ["renamed", "renamed", "skipped"]
    assert (tmp_path / "taken.MOV").read_bytes() == b"FOREIGN"  # never moved by undo
    assert (tmp_path / "a.MOV").read_bytes() == b"A" and (tmp_path / "b.MOV").read_bytes() == b"B"
    assert latest_manifest(tmp_path / "hist") is None


def test_rename_unchanged_writes_no_manifest(tmp_path):
    a = make(tmp_path, "X.MOV")
    res, manifest = rename_batch([(1, a, a)], tmp_path / "hist")
    assert res[0].status == "unchanged" and manifest is None


def test_undo_never_overwrites(tmp_path):
    a = make(tmp_path, "orig.MOV", b"A")
    _res, manifest = rename_batch([(1, a, tmp_path / "NEW.MOV")], tmp_path / "hist")
    make(tmp_path, "orig.MOV", b"someone else's file")
    r = undo_manifest(manifest)
    assert r[0].status == "error"
    assert (tmp_path / "NEW.MOV").read_bytes() == b"A"
