import datetime as dt
from pathlib import Path

import pytest
from takeline.naming import FilenameTemplate, PlanRequest, find_duplicates, plan_names, sanitize_filename

T = dt.datetime(2026, 7, 17, 19, 0, 49)


def test_default_template_and_suffix():
    tpl = FilenameTemplate()
    assert tpl.render("RAYBAN", T, ".MOV") == "RAYBAN-2026-07-17-190049.MOV"
    assert tpl.render("RAYBAN", T, "MOV", n=1) == "RAYBAN-2026-07-17-190049-01.MOV"
    assert tpl.render("RAYBAN", T, ".mov") == "RAYBAN-2026-07-17-190049.mov"  # extension preserved
    assert FilenameTemplate(ext_case="upper").render("APPLE", T, ".heic") == "APPLE-2026-07-17-190049.HEIC"


def test_alternative_template_gets_suffix_and_ext_automatically():
    tpl = FilenameTemplate("{DEVICE}_{YYYYMMDD}_{HHMMSS}")
    assert tpl.render("SONY", T, ".MP4") == "SONY_20260717_190049.MP4"
    assert tpl.render("SONY", T, ".MP4", n=2) == "SONY_20260717_190049-02.MP4"


def test_template_validation():
    with pytest.raises(ValueError):
        FilenameTemplate("{DEVICE}-{BOGUS}")
    with pytest.raises(ValueError):
        FilenameTemplate("{DEVICE}-{YYYY}")  # no time: would not sort chronologically


def test_windows_safe_names():
    assert sanitize_filename('a<b>:c"d|e?.MOV') == "a_b__c_d_e_.MOV"
    assert sanitize_filename("CON.MOV") == "_CON.MOV"
    assert FilenameTemplate().render('My Cam/2', T, ".MOV").startswith("My-Cam_2-2026")


def req(key, name, when, device="RAYBAN", folder=Path("/nonexistent/f")):
    p = folder / name
    return PlanRequest(key, p, folder, device, when, p.suffix)


def test_same_timestamp_gets_deterministic_suffixes():
    reqs = [req(1, "c.MOV", T), req(2, "a.MOV", T), req(3, "b.MOV", T)]
    out = plan_names(reqs, FilenameTemplate(), "copy", listing=lambda f: set())
    assert [out[k].name for k in (2, 3, 1)] == ["RAYBAN-2026-07-17-190049.MOV",
                                                "RAYBAN-2026-07-17-190049-01.MOV",
                                                "RAYBAN-2026-07-17-190049-02.MOV"]
    again = plan_names(list(reversed(reqs)), FilenameTemplate(), "copy", listing=lambda f: set())
    assert {k: v.name for k, v in out.items()} == {k: v.name for k, v in again.items()}


def test_different_extensions_do_not_collide_but_case_variants_do():
    out = plan_names([req(1, "a.MOV", T), req(2, "a.JPG", T)], FilenameTemplate(), "copy", listing=lambda f: set())
    assert {r.suffix_n for r in out.values()} == {0}
    out = plan_names([req(1, "a.MOV", T), req(2, "b.mov", T)], FilenameTemplate(), "copy", listing=lambda f: set())
    assert sorted(r.suffix_n for r in out.values()) == [0, 1]


def test_existing_files_are_never_targets():
    existing = {"rayban-2026-07-17-190049.mov", "rayban-2026-07-17-190049-01.mov"}
    out = plan_names([req(1, "x.MOV", T)], FilenameTemplate(), "copy", listing=lambda f: existing)
    assert out[1].name == "RAYBAN-2026-07-17-190049-02.MOV"


def test_rename_is_idempotent(tmp_path):
    for n in ("RAYBAN-2026-07-17-190049.MOV", "RAYBAN-2026-07-17-190049-01.MOV"):
        (tmp_path / n).write_bytes(b"x")
    reqs = [req(i, n, T, folder=tmp_path) for i, n in
            enumerate(["RAYBAN-2026-07-17-190049-01.MOV", "RAYBAN-2026-07-17-190049.MOV"])]
    out = plan_names(reqs, FilenameTemplate(), "rename")
    assert all(r.unchanged for r in out.values())


def test_duplicate_detection(tmp_path):
    a, b, c = tmp_path / "IMG_1.MOV", tmp_path / "IMG_1 (1).MOV", tmp_path / "other.MOV"
    a.write_bytes(b"same" * 1000)
    b.write_bytes(b"same" * 1000)
    c.write_bytes(b"diff" * 1000)
    d = find_duplicates([(1, a, 4000), (2, b, 4000), (3, c, 4000)])
    assert d == {2: 1}
