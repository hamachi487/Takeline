"""End-to-end dry run, including the critical acceptance scenario."""
import datetime as dt
import os
import time

import builders as b
from takeline.config import Settings, history_dir
from takeline.operations import copy_batch, rename_batch
from takeline.pipeline import Analyzer, MediaItem, collect_files, plan
from conftest import UTC, iphone_movie, rayban_movie


def scan(folder, settings=None):
    s = settings or Settings(use_exiftool=False)
    files, ignored = collect_files([folder])
    items = [MediaItem(i, p) for i, p in enumerate(files)]
    an = Analyzer(s)
    an.analyze_many(items)
    an.mark_duplicates(items)
    an.close()
    plan(items, s, "rename")
    return items, ignored


def build_acceptance_folder(d):
    utc = lambda *a: dt.datetime(*a, tzinfo=UTC)  # noqa: E731
    rayban_movie(d / "video-277_singular_display.MOV", "2026-07-17T19:00:49", utc(2026, 7, 17, 22, 0, 49))
    rayban_movie(d / "video-281_singular_display.MOV", "2026-07-17T19:02:15", utc(2026, 7, 17, 22, 2, 15))
    iphone_movie(d / "IMG_4821.MOV", "2026-07-17T19:15:30-0300", utc(2026, 7, 17, 22, 15, 30))
    iphone_movie(d / "IMG_4822.MOV", "2026-07-17T19:20:01-0300", utc(2026, 7, 17, 22, 20, 1))
    rayban_movie(d / "video-290_singular_display.MOV", "2026-07-17T19:21:14", utc(2026, 7, 17, 22, 21, 14))
    # Filesystem dates deliberately wrong and in reverse order: they must not matter.
    for i, p in enumerate(sorted(d.iterdir(), reverse=True)):
        os.utime(p, (1_500_000_000 + i, 1_500_000_000 + i))


EXPECTED = ["RAYBAN-2026-07-17-190049.MOV", "RAYBAN-2026-07-17-190215.MOV", "APPLE-2026-07-17-191530.MOV",
            "APPLE-2026-07-17-192001.MOV", "RAYBAN-2026-07-17-192114.MOV"]


def test_critical_acceptance_scenario(media):
    build_acceptance_folder(media)
    items, _ = scan(media)
    ordered = sorted(items, key=lambda it: it.capture_time)
    assert [it.proposed_name for it in ordered] == EXPECTED
    assert all(it.status[0] == "ok" for it in items)
    assert {it.path.name: it.device for it in items}["IMG_4821.MOV"] == "APPLE"


def test_acceptance_with_manual_sao_paulo_zone(media):
    build_acceptance_folder(media)
    items, _ = scan(media, Settings(use_exiftool=False, tz_mode="manual", tz_zone="America/Sao_Paulo"))
    assert sorted(it.proposed_name for it in items) == sorted(EXPECTED)


def test_acceptance_copy_then_rename(media, tmp_path):
    build_acceptance_folder(media)
    items, _ = scan(media)
    s = Settings(use_exiftool=False)
    out = tmp_path / "out"
    plan(items, s, "copy", out)
    res = copy_batch([(it.id, it.path, out / it.proposed_name) for it in items])
    assert all(r.status == "copied" for r in res)
    assert sorted(os.listdir(out)) == sorted(EXPECTED)
    assert len(os.listdir(media)) == 5  # originals untouched
    plan(items, s, "rename")
    res, manifest = rename_batch([(it.id, it.path, it.path.with_name(it.proposed_name)) for it in items], history_dir())
    assert sorted(os.listdir(media)) == sorted(EXPECTED) and manifest.exists()
    items2, _ = scan(media)  # second run changes nothing
    assert all(it.unchanged for it in items2)


def test_collect_files_filters(media):
    iphone_movie(media / "IMG_1.mov", "2026-07-17T19:15:30-0300", None)
    (media / "._IMG_1.mov").write_bytes(b"\0\x05\x16\x07")  # macOS AppleDouble
    (media / "notes.txt").write_text("x")
    (media / "Thumbs.db").write_bytes(b"x")
    sub = media / "sub"
    sub.mkdir()
    iphone_movie(sub / "IMG_2.MOV", "2026-07-17T19:16:30-0300", None)
    files, ignored = collect_files([media, media / "IMG_1.mov"])
    assert sorted(p.name for p in files) == ["IMG_1.mov", "IMG_2.MOV"]
    assert ignored == 3
    files, _ = collect_files([media], recursive=False)
    assert [p.name for p in files] == ["IMG_1.mov"]


def test_bad_file_does_not_break_batch_and_is_excluded(media):
    build_acceptance_folder(media)
    (media / "corrupt.MOV").write_bytes(b"\x00" * 3 + b"not a movie")
    items, _ = scan(media)
    bad = [it for it in items if it.path.name == "corrupt.MOV"][0]
    assert bad.error and not bad.include and bad.status[0] == "error"
    assert len([it for it in items if it.status[0] == "ok"]) == 5


def test_overrides_change_names(media):
    p = b.build_movie(media / "clip.mp4", brand=b"isom", mvhd_time=None, pascal=False)
    items, _ = scan(media)
    it = items[0]
    it.device_override = "DJI"
    it.time_override = dt.datetime(2026, 7, 17, 12, 34, 56)
    plan(items, Settings(use_exiftool=False), "rename")
    assert it.proposed_name == "DJI-2026-07-17-123456.mp4" and p.exists()


def test_thousands_of_files_quickly(media):
    proto = iphone_movie(media / "proto.MOV", "2026-07-17T19:15:30-0300", None).read_bytes()
    for i in range(1500):
        (media / f"IMG_{i:04d}.MOV").write_bytes(proto)
    (media / "proto.MOV").unlink()
    t0 = time.time()
    items, _ = scan(media)
    assert len(items) == 1500 and time.time() - t0 < 60
    names = {it.proposed_name for it in items}
    assert len(names) == 1500  # all identical timestamps, all unique names


def test_settings_folder_migrates_from_old_name(tmp_path, monkeypatch):
    from takeline import config
    base = tmp_path / "cfg"
    old = base / ("CaptureSort" if os.name == "nt" else "capturesort")
    (old / "history").mkdir(parents=True)
    (old / "history" / "rename-1.json").write_text("{}")
    monkeypatch.setenv("APPDATA", str(base))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(base))
    d = config.app_data_dir()
    assert d.name.lower() == "takeline" and (d / "history" / "rename-1.json").exists()
    assert not old.exists()
    assert config.app_data_dir() == d  # stable afterwards
