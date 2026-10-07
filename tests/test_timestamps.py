"""Capture-time resolution and timezone handling."""
import datetime as dt

import builders as b
from takeline.metadata import MetadataExtractor, MetadataRecord
from takeline.timestamps import CaptureTimestampResolver, TimezonePolicy, parse_date_value, parse_filename_date
from conftest import UTC, iphone_movie, rayban_movie

X = MetadataExtractor(use_exiftool=False)


def resolve(path, device, policy=None, **kw):
    return CaptureTimestampResolver(policy or TimezonePolicy("auto"), **kw).resolve(X.extract(path), device)


def test_rayban_keys_creationdate_wins_and_is_not_shifted(media):
    p = rayban_movie(media / "r.MOV", "2026-07-17T19:00:49", dt.datetime(2026, 7, 17, 22, 0, 49, tzinfo=UTC))
    for policy in (TimezonePolicy("auto"), TimezonePolicy("system"), TimezonePolicy("manual", "Asia/Tokyo")):
        t = resolve(p, "RAYBAN", policy)
        assert t.source == "Keys:CreationDate"
        assert t.local == dt.datetime(2026, 7, 17, 19, 0, 49)  # timezone-less: kept exactly as recorded
    assert any("UTC−03:00" in n for n in resolve(p, "RAYBAN").notes)  # implied by the UTC header


def test_iphone_offset_kept_in_auto_and_converted_in_manual(media):
    p = iphone_movie(media / "i.MOV", "2026-07-17T19:15:30-0300", dt.datetime(2026, 7, 17, 22, 15, 30, tzinfo=UTC))
    t = resolve(p, "APPLE")
    assert t.local == dt.datetime(2026, 7, 17, 19, 15, 30) and t.offset == dt.timedelta(hours=-3)
    assert any("Confirmed by" in n for n in t.notes)
    t = resolve(p, "APPLE", TimezonePolicy("manual", "Europe/Lisbon"))
    assert t.local == dt.datetime(2026, 7, 17, 23, 15, 30)  # Lisbon is UTC+1 in July
    t = resolve(p, "APPLE", TimezonePolicy("manual", "America/Sao_Paulo"))
    assert t.local == dt.datetime(2026, 7, 17, 19, 15, 30)


def test_manual_zone_respects_dst_at_capture_date(media):
    winter = b.build_movie(media / "w.mp4", brand=b"isom", mvhd_time=dt.datetime(2026, 1, 15, 17, 0, tzinfo=UTC), pascal=False)
    summer = b.build_movie(media / "s.mp4", brand=b"isom", mvhd_time=dt.datetime(2026, 7, 15, 17, 0, tzinfo=UTC), pascal=False)
    ny = TimezonePolicy("manual", "America/New_York")
    assert resolve(winter, "UNKNOWN", ny).local.hour == 12  # EST
    assert resolve(summer, "UNKNOWN", ny).local.hour == 13  # EDT


def test_utc_only_header_shown_in_system_timezone_by_default(media):
    p = b.build_movie(media / "h.mp4", brand=b"isom", mvhd_time=dt.datetime(2026, 7, 17, 22, 3, tzinfo=UTC), pascal=False)
    t = resolve(p, "UNKNOWN")
    expected = dt.datetime(2026, 7, 17, 22, 3, tzinfo=UTC).astimezone().replace(tzinfo=None)
    assert t.source == "QuickTime:CreateDate" and t.local == expected
    assert "Stored as UTC" in t.interpretation


def test_device_profile_header_local_for_gopro(media):
    p = b.build_movie(media / "GX010001.MP4", brand=b"mp41", mvhd_time=dt.datetime(2026, 7, 17, 19, 40, tzinfo=UTC), pascal=False)
    t = resolve(p, "GOPRO", TimezonePolicy("manual", "Asia/Tokyo"))
    assert t.local == dt.datetime(2026, 7, 17, 19, 40)  # local clock, not converted
    assert "local time" in t.label


def test_exif_original_beats_digitized_and_modify(media):
    ifd0 = {0x010F: (b.ASCII, "Canon"), 0x0132: (b.ASCII, "2026:08:01 09:00:00")}
    ex = {0x9003: (b.ASCII, "2026:07:17 19:44:22"), 0x9004: (b.ASCII, "2026:07:17 19:44:23")}
    t = resolve(b.build_jpeg(media / "c.jpg", b.tiff_bytes(ifd0, ex)), "CANON")
    assert t.source == "ExifIFD:DateTimeOriginal" and t.local == dt.datetime(2026, 7, 17, 19, 44, 22)


def test_invalid_dates_are_skipped(media):
    ifd0 = {0x010F: (b.ASCII, "X")}
    ex = {0x9003: (b.ASCII, "0000:00:00 00:00:00"), 0x9004: (b.ASCII, "2026:07:17 08:00:00")}
    t = resolve(b.build_jpeg(media / "z.jpg", b.tiff_bytes(ifd0, ex)), None)
    assert t.source == "ExifIFD:CreateDate"
    assert parse_date_value("1904:01:01 00:00:00") is None
    assert parse_date_value("    :  :     :  :  ") is None


def test_filename_then_filesystem_fallback(media):
    p = b.build_movie(media / "PXL_20260717_190049123.mp4", brand=b"isom", mvhd_time=None, pascal=False)
    t = resolve(p, "UNKNOWN")
    assert t.source == "Filename" and t.local == dt.datetime(2026, 7, 17, 19, 0, 49) and t.from_filename
    q = b.build_movie(media / "clip.mp4", brand=b"isom", mvhd_time=None, pascal=False)
    t = resolve(q, "UNKNOWN")
    assert t.is_fallback and t.source == "System:FileModifyDate" and t.confidence < 0.3
    assert resolve(q, "UNKNOWN", allow_filesystem=False) is None


def test_filename_patterns():
    assert parse_filename_date("IMG_20260717_190049.jpg").wall == dt.datetime(2026, 7, 17, 19, 0, 49)
    assert parse_filename_date("RAYBAN-2026-07-17-190049.MOV").wall == dt.datetime(2026, 7, 17, 19, 0, 49)
    assert parse_filename_date("DJI_20260717190049_0001_D.MP4").wall.second == 49
    assert parse_filename_date("Screen Recording 2026-07-17 at 19.00.49.mov").wall.minute == 0
    assert parse_filename_date("video-277_singular_display.MOV") is None
    assert parse_filename_date("IMG_4821.MOV") is None


def test_naive_as_utc_setting(media):
    p = rayban_movie(media / "r.MOV", "2026-07-17T22:00:49", None)
    t = resolve(p, "RAYBAN", TimezonePolicy("manual", "America/Sao_Paulo", naive_is_utc=True))
    assert t.local == dt.datetime(2026, 7, 17, 19, 0, 49)


def test_disagreeing_dates_are_flagged():
    r = MetadataRecord(path="/x/a.MOV")
    r.set("Keys", "CreationDate", "2026-07-17T19:00:49-0300")
    r.set("XMP-xmp", "CreateDate", "2024-01-01T10:00:00-0300")
    t = CaptureTimestampResolver().resolve(r, "APPLE")
    assert any("disagree" in n for n in t.notes) and t.confidence < 0.8


def test_iptc_and_gps_candidates():
    r = MetadataRecord(path="/x/a.jpg")
    r.set("IPTC", "DateCreated", "20260717")
    r.set("IPTC", "TimeCreated", "190049-0300")
    r.set("GPS", "GPSDateStamp", "2026:07:17")
    r.set("GPS", "GPSTimeStamp", "22:00:49")
    t = CaptureTimestampResolver(TimezonePolicy("manual", "America/Sao_Paulo")).resolve(r, None)
    assert t.local == dt.datetime(2026, 7, 17, 19, 0, 49)
    assert any("GPS" in n for n in t.notes)
