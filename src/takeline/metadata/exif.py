"""Native EXIF (TIFF IFD), IPTC-IIM and JPEG segment parsing.

Group/tag names follow ExifTool's family-1 groups (IFD0, ExifIFD, GPS, IFD1,
IPTC, MakerNotes) so values from the optional ExifTool backend line up.
"""

from __future__ import annotations

import struct
from typing import BinaryIO

from .binary import ParseError, read_exact
from .record import MetadataRecord, printable_text
from . import xmp as xmp_mod

IFD0_TAGS = {
    0x0100: "ImageWidth", 0x0101: "ImageHeight", 0x010E: "ImageDescription",
    0x010F: "Make", 0x0110: "Model", 0x0112: "Orientation", 0x011A: "XResolution",
    0x011B: "YResolution", 0x0128: "ResolutionUnit", 0x0131: "Software",
    0x0132: "ModifyDate", 0x013B: "Artist", 0x013C: "HostComputer",
    0x8298: "Copyright", 0x9C9B: "XPTitle", 0x9C9C: "XPComment", 0x9C9D: "XPAuthor",
    0xC614: "UniqueCameraModel", 0xC62F: "CameraSerialNumber",
}
EXIF_TAGS = {
    0x829A: "ExposureTime", 0x829D: "FNumber", 0x8822: "ExposureProgram",
    0x8827: "ISO", 0x9000: "ExifVersion", 0x9003: "DateTimeOriginal",
    0x9004: "CreateDate", 0x9010: "OffsetTime", 0x9011: "OffsetTimeOriginal",
    0x9012: "OffsetTimeDigitized", 0x9201: "ShutterSpeedValue", 0x9202: "ApertureValue",
    0x9204: "ExposureCompensation", 0x9207: "MeteringMode", 0x9209: "Flash",
    0x920A: "FocalLength", 0x927C: "MakerNote", 0x9286: "UserComment",
    0x9290: "SubSecTime", 0x9291: "SubSecTimeOriginal", 0x9292: "SubSecTimeDigitized",
    0xA001: "ColorSpace", 0xA002: "ExifImageWidth", 0xA003: "ExifImageHeight",
    0xA402: "ExposureMode", 0xA403: "WhiteBalance", 0xA405: "FocalLengthIn35mmFormat",
    0xA406: "SceneCaptureType", 0xA420: "ImageUniqueID", 0xA430: "OwnerName",
    0xA431: "SerialNumber", 0xA432: "LensInfo", 0xA433: "LensMake", 0xA434: "LensModel",
    0xA435: "LensSerialNumber",
}
GPS_TAGS = {
    0x0000: "GPSVersionID", 0x0001: "GPSLatitudeRef", 0x0002: "GPSLatitude",
    0x0003: "GPSLongitudeRef", 0x0004: "GPSLongitude", 0x0005: "GPSAltitudeRef",
    0x0006: "GPSAltitude", 0x0007: "GPSTimeStamp", 0x0010: "GPSImgDirectionRef",
    0x0011: "GPSImgDirection", 0x0012: "GPSMapDatum", 0x001D: "GPSDateStamp",
}
APPLE_MAKER_TAGS = {
    0x0001: "MakerNoteVersion", 0x0008: "AccelerationVector", 0x000A: "HDRImageType",
    0x000B: "BurstUUID", 0x0011: "ContentIdentifier", 0x0014: "ImageCaptureType",
    0x0015: "ImageUniqueID", 0x0017: "LivePhotoVideoIndex",
}
IPTC_TAGS = {
    5: "ObjectName", 25: "Keywords", 55: "DateCreated", 60: "TimeCreated",
    62: "DigitalCreationDate", 63: "DigitalCreationTime", 65: "OriginatingProgram",
    70: "ProgramVersion", 80: "By-line", 90: "City", 101: "Country-PrimaryLocationName",
    110: "Credit", 115: "Source", 116: "CopyrightNotice", 120: "Caption-Abstract",
}

SUB_IFDS = {0x8769: "ExifIFD", 0x8825: "GPS", 0xA005: "InteropIFD"}
TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}

MAKERNOTE_HEADERS = [
    (b"Apple iOS", "Apple iOS"), (b"SONY DSC", "Sony"), (b"SONY CAM", "Sony"),
    (b"SONY MOBILE", "Sony Mobile"), (b"Nikon", "Nikon"), (b"OLYMPUS", "Olympus"),
    (b"OM SYSTEM", "OM System"), (b"FUJIFILM", "Fujifilm"), (b"Panasonic", "Panasonic"),
    (b"GOOGLE", "Google"), (b"HUAWEI", "Huawei"), (b"DJI", "DJI"), (b"GoPro", "GoPro"),
    (b"LEICA", "Leica"), (b"PENTAX", "Pentax"), (b"RICOH", "Ricoh"), (b"SAMSUNG", "Samsung"),
]


class TiffParser:
    """Parses a TIFF structure (as found in EXIF blocks, HEIF Exif items and .tif files)."""

    def __init__(self, data: bytes, record: MetadataRecord):
        if len(data) < 8:
            raise ParseError("TIFF block too short")
        if data[:2] == b"II":
            self.e = "<"
        elif data[:2] == b"MM":
            self.e = ">"
        else:
            raise ParseError("not a TIFF byte-order mark")
        magic = struct.unpack_from(self.e + "H", data, 2)[0]
        if magic not in (42, 43):
            raise ParseError("bad TIFF magic")
        self.data = data
        self.record = record
        self.visited: set[int] = set()
        self.ifd0 = struct.unpack_from(self.e + "I", data, 4)[0]

    def parse(self) -> None:
        nxt = self._parse_ifd(self.ifd0, "IFD0", IFD0_TAGS)
        if nxt:
            self._parse_ifd(nxt, "IFD1", IFD0_TAGS, follow_next=False)

    # -------------------------------------------------------------------------
    def _parse_ifd(self, offset: int, group: str, names: dict[int, str],
                   follow_next: bool = True, base: int = 0, data: bytes | None = None,
                   endian: str | None = None) -> int:
        data = self.data if data is None else data
        e = self.e if endian is None else endian
        key = (id(data), offset + base)
        if offset <= 0 or offset + base + 2 > len(data) or key in self.visited:
            return 0
        self.visited.add(key)
        pos = base + offset
        count = struct.unpack_from(e + "H", data, pos)[0]
        if count > 1000:
            self.record.warn(f"{group}: implausible entry count {count}")
            return 0
        pos += 2
        for i in range(count):
            ep = pos + i * 12
            if ep + 12 > len(data):
                self.record.warn(f"{group}: truncated IFD")
                break
            tag, typ, cnt = struct.unpack_from(e + "HHI", data, ep)
            size = TYPE_SIZES.get(typ)
            if size is None or cnt > 10_000_000:
                continue
            total = size * cnt
            if total <= 4:
                vpos = ep + 8
            else:
                vpos = base + struct.unpack_from(e + "I", data, ep + 8)[0]
            if vpos < 0 or vpos + total > len(data):
                self.record.warn(f"{group}: tag 0x{tag:04X} points outside the block")
                continue
            raw = data[vpos:vpos + total]
            if group in ("IFD0",) and tag in SUB_IFDS:
                sub = struct.unpack_from(e + "I", raw, 0)[0] if len(raw) >= 4 else 0
                self._parse_ifd(sub, SUB_IFDS[tag], EXIF_TAGS if tag == 0x8769 else
                                GPS_TAGS if tag == 0x8825 else {}, follow_next=False,
                                base=base, data=data, endian=e)
                continue
            if group == "ExifIFD" and tag == 0xA005:
                continue
            if tag == 0x927C and group == "ExifIFD":
                self._makernote(raw, vpos)
                continue
            if tag == 0x02BC:  # XMP stored inside TIFF
                xmp_mod.parse_xmp_bytes(raw, self.record)
                continue
            if tag == 0x83BB:  # IPTC stored inside TIFF
                parse_iptc(raw, self.record)
                continue
            name = names.get(tag, f"Tag0x{tag:04X}")
            value = self._value(typ, cnt, raw, e, tag)
            if value is None:
                continue
            if name == "UserComment" and isinstance(value, (bytes, bytearray)):
                value = _user_comment(bytes(value))
            if group == "GPS" and name in ("GPSLatitude", "GPSLongitude") and isinstance(value, list):
                value = _dms(value)
            if group == "GPS" and name == "GPSTimeStamp" and isinstance(value, list) and len(value) == 3:
                h, m, s = value
                value = f"{int(h):02d}:{int(m):02d}:{int(round(float(s))):02d}"
            if name.startswith("XP") and isinstance(value, (bytes, bytearray, list)):
                b = bytes(value) if not isinstance(value, list) else bytes(int(x) & 0xFF for x in value)
                value = b.decode("utf-16-le", errors="replace").rstrip("\x00")
            self.record.set(group, name, value)
        if not follow_next:
            return 0
        np = pos + count * 12
        if np + 4 <= len(data):
            return struct.unpack_from(e + "I", data, np)[0]
        return 0

    def _value(self, typ: int, cnt: int, raw: bytes, e: str, tag: int):
        try:
            if typ == 2:
                parts = [p for p in raw.split(b"\x00") if p.strip()]
                if not parts:
                    return None
                texts = []
                for p in parts:
                    try:
                        texts.append(p.decode("utf-8").strip())
                    except UnicodeDecodeError:
                        texts.append(p.decode("latin-1").strip())
                return texts[0] if len(texts) == 1 else "; ".join(texts)
            if typ in (1, 6):
                fmt = "B" if typ == 1 else "b"
                vals = list(struct.unpack_from(f"{e}{cnt}{fmt}", raw))
                if tag in (0x9C9B, 0x9C9C, 0x9C9D):
                    return bytes(v & 0xFF for v in vals)
                return vals[0] if cnt == 1 else vals[:64]
            if typ == 7:
                if tag == 0x9000:  # ExifVersion
                    return raw.decode("ascii", errors="replace")
                return bytes(raw)
            fmt = {3: "H", 4: "I", 8: "h", 9: "i", 11: "f", 12: "d", 13: "I"}.get(typ)
            if fmt:
                vals = list(struct.unpack_from(f"{e}{cnt}{fmt}", raw))
                return vals[0] if cnt == 1 else vals[:64]
            if typ in (5, 10):
                fmt = "I" if typ == 5 else "i"
                nums = struct.unpack_from(f"{e}{cnt * 2}{fmt}", raw)
                vals = []
                for i in range(0, len(nums), 2):
                    n, d = nums[i], nums[i + 1]
                    vals.append(n / d if d else 0.0)
                vals = [round(v, 6) for v in vals]
                return vals[0] if cnt == 1 else vals[:64]
        except struct.error:
            return None
        return None

    def _makernote(self, raw: bytes, vpos: int) -> None:
        kind = None
        for prefix, label in MAKERNOTE_HEADERS:
            if raw.startswith(prefix):
                kind = label
                break
        if kind is None:
            make = str(self.record.get("IFD0:Make") or "")
            if make.lower().startswith("canon"):
                kind = "Canon"
        self.record.set("MakerNotes", "MakerNoteType", kind or "Unknown")
        self.record.set("MakerNotes", "MakerNoteSize", len(raw))
        if kind == "Apple iOS" and len(raw) > 16 and raw[12:14] in (b"MM", b"II"):
            # Apple maker notes: 'Apple iOS\0' + version + byte order + IFD.
            # Offsets inside are relative to the start of the maker note.
            e = ">" if raw[12:14] == b"MM" else "<"
            try:
                self._parse_ifd(14, "Apple", APPLE_MAKER_TAGS, follow_next=False,
                                base=0, data=raw, endian=e)
            except (struct.error, ParseError) as exc:
                self.record.warn(f"Apple maker notes: {exc}")


def _user_comment(raw: bytes) -> str | None:
    head, body = raw[:8], raw[8:]
    if head.startswith(b"UNICODE"):
        for enc in ("utf-16-be", "utf-16-le"):
            try:
                return body.decode(enc).strip("\x00 ")
            except UnicodeDecodeError:
                continue
    text = printable_text(body if head[:5] in (b"ASCII", b"JIS\x00\x00", b"\x00\x00\x00\x00\x00") else raw)
    return text


def _dms(values: list) -> float | list:
    try:
        d, m, s = (float(v) for v in values[:3])
        return round(d + m / 60 + s / 3600, 7)
    except (TypeError, ValueError):
        return values


def parse_exif_block(data: bytes, record: MetadataRecord) -> None:
    """Parse an EXIF payload that may start with 'Exif\\0\\0' or directly with TIFF."""
    if data.startswith(b"Exif\x00\x00"):
        data = data[6:]
    elif data.startswith(b"Exif\x00"):
        data = data[5:]
    TiffParser(data, record).parse()


# ----------------------------------------------------------------------- IPTC
def parse_iptc(data: bytes, record: MetadataRecord) -> None:
    pos = 0
    n = len(data)
    while pos + 5 <= n:
        if data[pos] != 0x1C:
            pos += 1
            continue
        rec, ds = data[pos + 1], data[pos + 2]
        length = struct.unpack_from(">H", data, pos + 3)[0]
        pos += 5
        if length & 0x8000:  # extended dataset
            nbytes = length & 0x7FFF
            if nbytes > 4 or pos + nbytes > n:
                break
            length = int.from_bytes(data[pos:pos + nbytes], "big")
            pos += nbytes
        if pos + length > n:
            break
        value = data[pos:pos + length]
        pos += length
        if rec != 2:
            continue
        name = IPTC_TAGS.get(ds, f"Dataset2:{ds}")
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError:
            text = value.decode("latin-1")
        record.set("IPTC", name, text)


def parse_photoshop_irb(data: bytes, record: MetadataRecord) -> None:
    """Photoshop image resource blocks (APP13); resource 0x0404 holds IPTC."""
    pos = 0
    while pos + 12 <= len(data):
        if data[pos:pos + 4] != b"8BIM":
            break
        rid = struct.unpack_from(">H", data, pos + 4)[0]
        name_len = data[pos + 6]
        name_total = name_len + 1
        if name_total % 2:
            name_total += 1
        p = pos + 6 + name_total
        if p + 4 > len(data):
            break
        size = struct.unpack_from(">I", data, p)[0]
        p += 4
        block = data[p:p + size]
        if rid == 0x0404:
            parse_iptc(block, record)
        pos = p + size + (size % 2)


# ----------------------------------------------------------------------- JPEG
SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def parse_jpeg(f: BinaryIO, record: MetadataRecord) -> None:
    record.container = "jpeg"
    record.set("Container", "Format", "JPEG image")
    f.seek(0)
    if f.read(2) != b"\xFF\xD8":
        raise ParseError("missing JPEG start-of-image marker")
    segments = 0
    while segments < 500:
        segments += 1
        b = f.read(1)
        if not b:
            break
        if b != b"\xFF":
            continue  # tolerate garbage between segments
        marker = f.read(1)
        while marker == b"\xFF":
            marker = f.read(1)
        if not marker:
            break
        m = marker[0]
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
            continue
        if m in (0xD9, 0xDA):  # EOI or start of scan: metadata is over
            break
        length_bytes = f.read(2)
        if len(length_bytes) < 2:
            break
        length = struct.unpack(">H", length_bytes)[0] - 2
        if length < 0:
            break
        if m in SOF_MARKERS:
            seg = read_exact(f, length)
            if len(seg) >= 5:
                h, w = struct.unpack_from(">HH", seg, 1)
                record.set("Container", "ImageWidth", w)
                record.set("Container", "ImageHeight", h)
            continue
        if m in (0xE0, 0xE1, 0xED, 0xFE) or m == 0xE2:
            seg = read_exact(f, length)
            try:
                if m == 0xE1 and seg.startswith(b"Exif\x00"):
                    parse_exif_block(seg, record)
                elif m == 0xE1 and seg.startswith(b"http://ns.adobe.com/xap/1.0/\x00"):
                    xmp_mod.parse_xmp_bytes(seg[29:], record)
                elif m == 0xED and seg.startswith(b"Photoshop 3.0\x00"):
                    parse_photoshop_irb(seg[14:], record)
                elif m == 0xE0 and seg.startswith(b"JFIF\x00"):
                    record.set("JFIF", "JFIFVersion", f"{seg[5]}.{seg[6]:02d}")
                elif m == 0xFE:
                    text = printable_text(seg)
                    if text:
                        record.set("File", "Comment", text)
            except (ParseError, struct.error, ValueError) as exc:
                record.warn(f"JPEG APP{m - 0xE0} segment: {exc}")
            continue
        f.seek(length, 1)
