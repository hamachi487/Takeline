"""Native parsers: structure coverage and robustness against malformed input."""
import datetime as dt
import struct

import builders as b
from takeline.metadata import MetadataExtractor
from conftest import UTC, iphone_movie, rayban_movie

X = MetadataExtractor(use_exiftool=False)


def test_quicktime_keys_and_headers(media):
    p = rayban_movie(media / "v.MOV", "2026-07-17T19:00:49", dt.datetime(2026, 7, 17, 22, 0, 49, tzinfo=UTC))
    r = X.extract(p)
    assert not r.errors
    assert r.container == "quicktime"
    assert r.get("Keys:Make") == "Meta AI"
    assert r.get("Keys:Model") == "Ray-Ban Meta Smart Glasses"
    assert r.get("Keys:CreationDate") == "2026-07-17T19:00:49"
    assert r.get("QuickTime:CreateDate") == dt.datetime(2026, 7, 17, 22, 0, 49, tzinfo=UTC)
    assert r.get("Track1:MediaCreateDate") == dt.datetime(2026, 7, 17, 22, 0, 49, tzinfo=UTC)
    assert r.get("Track1:HandlerType") == "vide"


def test_moov_after_mdat_and_largesize(media):
    p = b.build_movie(media / "late.mp4", brand=b"isom", mvhd_time=dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
                      moov_at_end=True, pascal=False)
    assert X.extract(p).get("QuickTime:CreateDate").year == 2026
    # 64-bit mdat header (size field == 1)
    data = p.read_bytes()
    i = data.index(b"mdat") - 4
    size = struct.unpack(">I", data[i:i + 4])[0]
    patched = data[:i] + struct.pack(">I", 1) + b"mdat" + struct.pack(">Q", size + 8) + data[i + 8:]
    q = media / "large.mp4"
    q.write_bytes(patched)
    assert X.extract(q).get("QuickTime:CreateDate").year == 2026


def test_userdata_and_itemlist(media):
    p = b.build_movie(media / "u.mp4", brand=b"mp42", mvhd_time=None, udta={b"\xa9mak": "Canon", b"\xa9mod": "EOS R6"},
                      ilst={b"\xa9too": "Lavf60", b"\xa9day": "2026-07-17T19:40:00-0300"}, pascal=False)
    r = X.extract(p)
    assert r.get("UserData:Make") == "Canon"
    assert r.get("UserData:Model") == "EOS R6"
    assert r.get("ItemList:Encoder") == "Lavf60"
    assert r.get("ItemList:ContentCreateDate") == "2026-07-17T19:40:00-0300"
    assert r.get("QuickTime:CreateDate") is None  # zero header date means "not set"


def test_sony_style_xml_box(media):
    xml = (b'<?xml version="1.0"?><NonRealTimeMeta xmlns="urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.00">'
           b'<CreationDate value="2026-07-17T19:30:15-03:00"/><Device manufacturer="Sony" modelName="ILCE-7M4"/>'
           b'</NonRealTimeMeta>')
    meta = b.full(b"meta", b.hdlr(b"nrtm", "", pascal=False, component=b"\0\0\0\0") + b.full(b"xml ", xml))
    p = b.build_movie(media / "C0001.MP4", brand=b"XAVC", mvhd_time=None, extra_moov=meta, pascal=False)
    r = X.extract(p)
    assert r.get("XML:DeviceManufacturer") == "Sony"
    assert r.get("XML:DeviceModelName") == "ILCE-7M4"
    assert r.get("XML:CreationDateValue") == "2026-07-17T19:30:15-03:00"


def test_jpeg_exif_with_offset_and_makernote(media):
    ifd0, ex = b.exif_dict("Apple", "iPhone 15 Pro", "17.5", "2026:07:17 19:20:01", "-03:00",
                           makernote=b.apple_makernote())
    r = X.extract(b.build_jpeg(media / "IMG_1.JPG", b.tiff_bytes(ifd0, ex)))
    assert r.get("IFD0:Make") == "Apple"
    assert r.get("ExifIFD:DateTimeOriginal") == "2026:07:17 19:20:01"
    assert r.get("ExifIFD:OffsetTimeOriginal") == "-03:00"
    assert r.get("MakerNotes:MakerNoteType") == "Apple iOS"


def test_little_endian_exif_and_xmp(media):
    ifd0, ex = b.exif_dict("SONY", "ILCE-7M4", None, "2026:07:17 10:11:12")
    xmp = ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           '<rdf:Description xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:CreateDate="2026-07-17T10:11:12"/>'
           '</rdf:RDF></x:xmpmeta>')
    r = X.extract(b.build_jpeg(media / "a.jpg", b.tiff_bytes(ifd0, ex, little=True), xmp=xmp))
    assert r.get("IFD0:Model") == "ILCE-7M4"
    assert r.get("XMP-xmp:CreateDate") == "2026-07-17T10:11:12"


def test_heic_exif_item(media):
    ifd0, ex = b.exif_dict("Apple", "iPhone 17 Pro", None, "2026:10:07 13:25:30", "-03:00")
    r = X.extract(b.build_heic(media / "IMG_9.HEIC", b.tiff_bytes(ifd0, ex)))
    assert r.container == "heif"
    assert r.get("IFD0:Model") == "iPhone 17 Pro"
    assert r.get("ExifIFD:DateTimeOriginal") == "2026:10:07 13:25:30"
    assert r.get("Container:ImageWidth") == 4032


def test_png_avi_mkv(media):
    r = X.extract(b.build_png_with_exif(media / "p.PNG", b.tiff_bytes(*b.exif_dict("Google", "Pixel 8", None, "2026:07:17 10:00:00"))))
    assert r.get("IFD0:Make") == "Google"
    r = X.extract(b.build_avi(media / "a.AVI", "Fri Jul 17 19:00:49 2026"))
    assert r.get("RIFF:DateTimeOriginal") == "Fri Jul 17 19:00:49 2026"
    r = X.extract(b.build_mkv(media / "m.webm", dt.datetime(2026, 7, 17, 22, 0, tzinfo=UTC), webm=True))
    assert r.get("Matroska:DateTimeOriginal") == dt.datetime(2026, 7, 17, 22, 0, tzinfo=UTC)
    assert r.get("Container:Format") == "WebM video"


def test_content_sniffing_beats_extension(media):
    ifd0, ex = b.exif_dict("Apple", "iPhone 15", None, "2026:07:17 19:20:01")
    p = media / "really_heic.jpg"
    b.build_heic(p, b.tiff_bytes(ifd0, ex))
    r = X.extract(p)
    assert r.container == "heif" and r.get("IFD0:Make") == "Apple"
    assert any("extension" in w for w in r.warnings)


def test_corrupted_and_truncated_never_raise(media):
    good = iphone_movie(media / "g.MOV", "2026-07-17T19:15:30-0300", None).read_bytes()
    cases = {
        "empty.mov": b"",
        "garbage.mov": b"\x13\x37" * 500,
        "truncated.mov": good[: len(good) // 3],
        "badsize.mov": good[:40] + struct.pack(">I", 3) + good[44:],
        "huge_claim.mov": struct.pack(">I", 0xFFFFFFF0) + b"moov" + b"\0" * 64,
        "bad.jpg": b"\xFF\xD8\xFF\xE1\x00\x10Exif\x00\x00MM\x00*\xFF\xFF\xFF\xFF",
        "bad.heic": b.box(b"ftyp", b"heic\0\0\0\0mif1") + b.full(b"meta", b"\xFF" * 40),
    }
    for name, data in cases.items():
        p = media / name
        p.write_bytes(data)
        r = X.extract(p)  # must not raise
        assert isinstance(r.errors, list)
    assert X.extract(media / "empty.mov").errors


def test_malformed_keys_table(media):
    keys = b.full(b"keys", struct.pack(">I", 5) + struct.pack(">I", 3) + b"mdta")  # entry size < 8
    meta = b.box(b"meta", b.hdlr(b"mdta", "", pascal=False, component=b"\0\0\0\0") + keys + b.box(b"ilst"))
    p = b.build_movie(media / "k.MOV", mvhd_time=dt.datetime(2026, 7, 17, 22, 0, tzinfo=UTC), extra_moov=meta)
    r = X.extract(p)
    assert not r.errors
    assert r.get("QuickTime:CreateDate") is not None


def test_missing_file_is_an_error_not_an_exception(media):
    r = X.extract(media / "nope.mov")
    assert r.errors and "access" in r.errors[0]
