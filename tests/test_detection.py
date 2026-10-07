"""Device detection. Fixtures are SYNTHETIC; see tests/real_samples for real-file checks."""
import datetime as dt

import builders as b
import pytest
from takeline.detection import DetectionEngine
from takeline.metadata import MetadataExtractor, MetadataRecord
from conftest import UTC, iphone_movie, rayban_movie

X = MetadataExtractor(use_exiftool=False)
E = DetectionEngine()


def detect(path):
    return E.detect(X.extract(path))


def rec(**groups) -> MetadataRecord:
    r = MetadataRecord(path="/x/file.MOV")
    for g, tags in groups.items():
        for t, v in tags.items():
            r.set(g, t, v)
    return r


def test_rayban_with_reasons(media):
    d = detect(rayban_movie(media / "video-277_singular_display.MOV", "2026-07-17T19:00:49", None))
    assert d.device == "RAYBAN" and d.display_name == "Ray-Ban Meta"
    assert d.confidence >= 0.95
    assert any("Keys:Make = Meta AI" in r for r in d.reasons)
    assert any("Keys:Model = Ray-Ban Meta Smart Glasses" in r for r in d.reasons)
    assert "Keys:CreationDate found" in d.reasons


@pytest.mark.parametrize("make,model", [
    ("Meta AI", "Ray-Ban Meta Smart Glasses 2"),
    ("Meta", "Meta Ray-Ban Display"),
    ("Meta Platforms, Inc.", None),
    ("Ray-Ban", "Ray-Ban Stories"),
    (None, "Ray-Ban Meta Wayfarer"),
    ("Meta AI", "Smart Glasses Gen 3"),
])
def test_rayban_tolerates_model_changes(media, make, model):
    d = detect(rayban_movie(media / "c.MOV", "2026-07-17T19:00:49", None, model=model, make=make))
    assert d.device == "RAYBAN", d.reasons


def test_oakley_meta_not_mislabelled_rayban(media):
    d = detect(rayban_movie(media / "o.MOV", "2026-07-17T19:00:49", None, model="Oakley Meta HSTN"))
    assert d.device == "OAKLEY"


def test_iphone_is_never_rayban(media):
    d = detect(iphone_movie(media / "IMG_4821.MOV", "2026-07-17T19:15:30-0300", None))
    assert d.device == "APPLE" and d.display_name == "Apple iPhone" and d.model == "iPhone 15 Pro"
    # even if the iPhone clip carries a Meta-app-like filename
    d = detect(iphone_movie(media / "video-300_singular_display.MOV", "2026-07-17T19:15:30-0300", None))
    assert d.device == "APPLE"


def test_ipad_and_generic_apple():
    assert E.detect(rec(IFD0={"Make": "Apple", "Model": "iPad Pro (11-inch)"})).display_name == "Apple iPad"
    d = E.detect(rec(Keys={"Make": "Apple", "Model": "MacBook Pro"}))
    assert d.device == "APPLE" and d.display_name == "Apple device"


def test_heic_apple_makernotes(media):
    ifd0, ex = b.exif_dict("Apple", "iPhone 17 Pro", None, "2026:10:07 13:25:30", "-03:00", makernote=b.apple_makernote())
    d = detect(b.build_heic(media / "IMG.HEIC", b.tiff_bytes(ifd0, ex)))
    assert d.device == "APPLE" and "Apple maker notes present" in d.reasons


def test_filename_alone_is_never_enough(media):
    p = b.build_movie(media / "video-123_singular_display.MOV", mvhd_time=None)
    d = detect(p)
    assert d.device == "UNKNOWN"
    assert any("never used alone" in r for r in d.reasons)


def test_reencoded_file_is_unknown_with_explanation(media):
    p = b.build_movie(media / "export.mp4", brand=b"isom", mvhd_time=None, ilst={b"\xa9too": "Lavf60.16.100"}, pascal=False)
    d = detect(p)
    assert d.device == "UNKNOWN"
    assert "No manufacturer metadata found." in d.reasons
    assert any("re-encoded" in r for r in d.reasons)


def test_sony_canon_dji_gopro():
    assert E.detect(rec(XML={"DeviceManufacturer": "Sony", "DeviceModelName": "ILCE-7M4"})).device == "SONY"
    assert E.detect(rec(IFD0={"Make": "Canon", "Model": "Canon EOS R6"})).device == "CANON"
    assert E.detect(rec(UserData={"CompressorVersion": "CanonAVC0005"})).device == "CANON"
    assert E.detect(rec(IFD0={"Make": "DJI", "Model": "FC3582"})).device == "DJI"
    assert E.detect(rec(Track1={"HandlerDescription": "GoPro AVC"},
                        UserData={"FirmwareVersion": "HD9.01.01.60.00"})).device == "GOPRO"


def test_generic_manufacturer_from_make():
    d = E.detect(rec(Keys={"AndroidManufacturer": "samsung", "AndroidModel": "SM-S928B"}))
    assert d.device == "SAMSUNG" and d.model == "SM-S928B"
    assert E.detect(rec(IFD0={"Make": "NIKON CORPORATION", "Model": "NIKON Z 6"})).device == "NIKON"


def test_foreign_make_caps_weak_rayban_hints():
    d = E.detect(rec(Keys={"Make": "Apple", "Model": "iPhone 15", "Comment": "shot next to my Ray-Ban glasses"}))
    assert d.device == "APPLE"
    assert all(dev != "RAYBAN" or c < 0.6 for dev, c in d.alternatives)


def test_conflicting_strong_evidence_lowers_confidence():
    d = E.detect(rec(Keys={"Make": "Apple", "Model": "Ray-Ban Meta Smart Glasses"}))
    assert d.confidence < 0.9
    assert any("Conflicting metadata" in r for r in d.reasons)


def test_threshold_is_configurable():
    r = rec(Keys={"Encoder": "Meta View 1.2"})  # 0.45 software-only signal
    assert DetectionEngine(threshold=0.6).detect(r).device == "UNKNOWN"
    assert DetectionEngine(threshold=0.4).detect(r).device == "RAYBAN"
