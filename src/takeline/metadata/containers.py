"""Native parsers for PNG, standalone TIFF, AVI (RIFF) and Matroska/WebM."""

from __future__ import annotations

import datetime as _dt
import io
import struct
import zlib
from typing import BinaryIO

from .binary import MKV_EPOCH, ParseError, read_exact
from .record import MetadataRecord, printable_text
from . import exif as exif_mod
from . import xmp as xmp_mod

# ------------------------------------------------------------------------ PNG
PNG_SIG = b"\x89PNG\r\n\x1a\n"


def parse_png(f: BinaryIO, record: MetadataRecord) -> None:
    record.container = "png"
    record.set("Container", "Format", "PNG image")
    f.seek(0)
    if f.read(8) != PNG_SIG:
        raise ParseError("bad PNG signature")
    for _ in range(10_000):
        hdr = f.read(8)
        if len(hdr) < 8:
            break
        length, ctype = struct.unpack(">I4s", hdr)
        ctype = ctype.decode("latin-1")
        if length > 64 * 1024 * 1024:
            raise ParseError("PNG chunk too large")
        if ctype in ("IDAT", "fdAT"):
            f.seek(length + 4, 1)
            continue
        data = read_exact(f, length) if length else b""
        f.seek(4, 1)  # CRC
        try:
            if ctype == "IHDR" and len(data) >= 8:
                w, h = struct.unpack(">II", data[:8])
                record.set("Container", "ImageWidth", w)
                record.set("Container", "ImageHeight", h)
            elif ctype == "eXIf":
                exif_mod.parse_exif_block(data, record)
            elif ctype in ("tEXt", "zTXt", "iTXt"):
                key, text = _png_text(ctype, data)
                if key is None:
                    continue
                if key == "XML:com.adobe.xmp":
                    xmp_mod.parse_xmp_bytes(text.encode("utf-8"), record)
                elif key.lower().startswith("raw profile type exif"):
                    blob = _raw_profile(text)
                    if blob:
                        exif_mod.parse_exif_block(blob, record)
                else:
                    name = "".join(w[:1].upper() + w[1:] for w in key.replace("-", " ").split())
                    record.set("PNG", name or key, text)
            elif ctype == "tIME" and len(data) == 7:
                y, mo, d, h, mi, s = struct.unpack(">HBBBBB", data)
                record.set("PNG", "ModifyDate", f"{y:04d}:{mo:02d}:{d:02d} {h:02d}:{mi:02d}:{s:02d}Z")
        except (ParseError, struct.error, zlib.error, ValueError) as exc:
            record.warn(f"PNG {ctype}: {exc}")
        if ctype == "IEND":
            break


def _png_text(ctype: str, data: bytes) -> tuple[str | None, str]:
    key, sep, rest = data.partition(b"\x00")
    if not sep:
        return None, ""
    k = key.decode("latin-1")
    if ctype == "tEXt":
        return k, rest.decode("latin-1")
    if ctype == "zTXt":
        return k, zlib.decompress(rest[1:])[:4 * 1024 * 1024].decode("latin-1")
    # iTXt: flag, method, lang\0, translated\0, text
    flag = rest[0] if rest else 0
    parts = rest[2:].split(b"\x00", 2)
    body = parts[2] if len(parts) == 3 else b""
    if flag:
        body = zlib.decompress(body)[:4 * 1024 * 1024]
    return k, body.decode("utf-8", errors="replace")


def _raw_profile(text: str) -> bytes | None:
    lines = text.strip().splitlines()
    if len(lines) < 3:
        return None
    try:
        return bytes.fromhex("".join(lines[2:]).replace(" ", ""))
    except ValueError:
        return None


# ----------------------------------------------------------------------- TIFF
def parse_tiff_file(f: BinaryIO, record: MetadataRecord, size: int) -> None:
    record.container = "tiff"
    record.set("Container", "Format", "TIFF image")
    f.seek(0)
    # EXIF blocks in TIFFs can sit anywhere; cap what we load.
    data = f.read(min(size, 64 * 1024 * 1024))
    exif_mod.TiffParser(data, record).parse()


# ------------------------------------------------------------------------ AVI
RIFF_INFO = {
    "ICRD": "DateCreated", "ISFT": "Software", "INAM": "Title", "ICMT": "Comment",
    "IART": "Artist", "ICOP": "Copyright", "ISRC": "Source", "IPRD": "Product",
    "IENG": "Engineer", "ITCH": "Technician", "ISBJ": "Subject", "IKEY": "Keywords",
    "IMAK": "Make", "IMOD": "Model",
}


def parse_riff(f: BinaryIO, record: MetadataRecord, size: int) -> None:
    record.container = "riff"
    f.seek(0)
    head = f.read(12)
    if len(head) < 12 or head[:4] != b"RIFF":
        raise ParseError("not a RIFF file")
    form = head[8:12].decode("latin-1")
    record.set("Container", "Format", "AVI video" if form == "AVI " else f"RIFF ({form.strip()})")
    _riff_chunks(f, record, 12, min(size, 8 + struct.unpack("<I", head[4:8])[0]), 0)


def _riff_chunks(f: BinaryIO, record: MetadataRecord, start: int, end: int, depth: int) -> None:
    pos = start
    guard = 0
    while pos + 8 <= end and guard < 50_000 and depth < 6:
        guard += 1
        f.seek(pos)
        hdr = f.read(8)
        if len(hdr) < 8:
            return
        cid = hdr[:4].decode("latin-1")
        csize = struct.unpack("<I", hdr[4:])[0]
        body = pos + 8
        nxt = body + csize + (csize & 1)
        if cid == "LIST":
            ltype = f.read(4).decode("latin-1")
            if ltype == "movi":
                pos = nxt
                continue
            if ltype == "INFO":
                _riff_info(f, record, body + 4, min(nxt, end))
            else:
                _riff_chunks(f, record, body + 4, min(nxt, end), depth + 1)
        elif cid == "IDIT" and csize < 256:
            text = f.read(csize).decode("latin-1").strip("\x00 \r\n")
            record.set("RIFF", "DateTimeOriginal", text)
        elif cid == "avih" and csize >= 40:
            d = f.read(40)
            usec_per_frame, _, _, _, frames = struct.unpack_from("<IIIII", d, 0)
            w, h = struct.unpack_from("<II", d, 32)
            if w and h:
                record.set("Container", "Resolution", f"{w} × {h}")
            if usec_per_frame and frames:
                record.set("RIFF", "Duration", round(frames * usec_per_frame / 1e6, 3))
        elif cid == "strd" and 8 < csize < 1024 * 1024:
            d = f.read(csize)
            i = d.find(b"Exif\x00\x00")
            if i >= 0:
                try:
                    exif_mod.parse_exif_block(d[i:], record)
                except ParseError as exc:
                    record.warn(f"AVI strd EXIF: {exc}")
        if nxt <= pos:
            return
        pos = nxt


def _riff_info(f: BinaryIO, record: MetadataRecord, start: int, end: int) -> None:
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        hdr = f.read(8)
        if len(hdr) < 8:
            return
        cid = hdr[:4].decode("latin-1")
        csize = struct.unpack("<I", hdr[4:])[0]
        if csize < 4096:
            text = f.read(csize).decode("latin-1").strip("\x00 \r\n")
            record.set("RIFF", RIFF_INFO.get(cid, cid), text)
        pos += 8 + csize + (csize & 1)


# ------------------------------------------------------------------- Matroska
EBML_ID = 0x1A45DFA3
SEGMENT = 0x18538067
INFO = 0x1549A966
TRACKS = 0x1654AE6B
TAGS = 0x1254C367
CLUSTER = 0x1F43B675


def _vint(f: BinaryIO, keep_marker: bool) -> tuple[int, int] | None:
    first = f.read(1)
    if not first:
        return None
    b = first[0]
    length = 1
    mask = 0x80
    while length <= 8 and not (b & mask):
        mask >>= 1
        length += 1
    if length > 8:
        return None
    value = b if keep_marker else (b & (mask - 1))
    rest = f.read(length - 1)
    if len(rest) != length - 1:
        return None
    for byte in rest:
        value = (value << 8) | byte
    return value, length


def _element(f: BinaryIO):
    eid = _vint(f, True)
    if eid is None:
        return None
    sz = _vint(f, False)
    if sz is None:
        return None
    size, slen = sz
    unknown = size == (1 << (7 * slen)) - 1
    return eid[0], size, unknown


def parse_matroska(f: BinaryIO, record: MetadataRecord, size: int) -> None:
    record.container = "matroska"
    f.seek(0)
    el = _element(f)
    if el is None or el[0] != EBML_ID:
        raise ParseError("not an EBML file")
    hdr_end = f.tell() + el[1]
    doctype = "matroska"
    while f.tell() < hdr_end:
        sub = _element(f)
        if sub is None:
            break
        if sub[0] == 0x4282:
            doctype = f.read(sub[1]).decode("ascii", "replace")
        else:
            f.seek(sub[1], 1)
    record.set("Container", "Format", "WebM video" if doctype == "webm" else "Matroska video")
    f.seek(hdr_end)
    seg = _element(f)
    if seg is None or seg[0] != SEGMENT:
        raise ParseError("Matroska segment not found")
    seg_end = size if seg[2] else min(size, f.tell() + seg[1])
    timecode_scale = 1_000_000
    duration = None
    guard = 0
    while f.tell() < seg_end and guard < 100_000:
        guard += 1
        start = f.tell()
        el = _element(f)
        if el is None:
            break
        eid, esize, unknown = el
        body = f.tell()
        if eid == CLUSTER and unknown:
            break  # live-recorded file; nothing after this can be skipped safely
        if unknown:
            break
        end = body + esize
        if eid == INFO:
            for cid, data in _children(f, body, end):
                if cid == 0x2AD7B1:
                    timecode_scale = int.from_bytes(data, "big") or timecode_scale
                elif cid == 0x4489:
                    duration = struct.unpack(">f" if len(data) == 4 else ">d", data)[0]
                elif cid == 0x4461 and len(data) == 8:
                    ns = struct.unpack(">q", data)[0]
                    try:
                        record.set("Matroska", "DateTimeOriginal", MKV_EPOCH + _dt.timedelta(microseconds=ns // 1000))
                    except OverflowError:
                        pass
                elif cid == 0x7BA9:
                    record.set("Matroska", "Title", data.decode("utf-8", "replace"))
                elif cid == 0x4D80:
                    record.set("Matroska", "MuxingApp", data.decode("utf-8", "replace"))
                elif cid == 0x5741:
                    record.set("Matroska", "WritingApp", data.decode("utf-8", "replace"))
        elif eid == TRACKS:
            _mkv_tracks(f, record, body, end)
        elif eid == TAGS:
            _mkv_tags(f, record, body, end)
        f.seek(end)
        if end <= start:
            break
    if duration:
        record.set("Matroska", "Duration", round(duration * timecode_scale / 1e9, 3))


def _children(f: BinaryIO, start: int, end: int):
    f.seek(start)
    while f.tell() < end:
        el = _element(f)
        if el is None or el[2]:
            return
        eid, esize, _ = el
        if esize > 1024 * 1024:
            f.seek(esize, 1)
            continue
        pos = f.tell()
        data = f.read(esize)
        yield eid, data
        f.seek(pos + esize)


def _mkv_tracks(f: BinaryIO, record: MetadataRecord, start: int, end: int) -> None:
    n = 0
    for eid, data in list(_children(f, start, end)):
        if eid != 0xAE:
            continue
        n += 1
        sub = io.BytesIO(data)
        for cid, cdata in _children(sub, 0, len(data)):
            if cid == 0x86:
                record.set(f"Track{n}", "CodecID", cdata.decode("ascii", "replace"))
            elif cid == 0xE0:
                vs = io.BytesIO(cdata)
                w = h = None
                for vid, vdata in _children(vs, 0, len(cdata)):
                    if vid == 0xB0:
                        w = int.from_bytes(vdata, "big")
                    elif vid == 0xBA:
                        h = int.from_bytes(vdata, "big")
                if w and h:
                    record.set("Container", "Resolution", f"{w} × {h}")


def _mkv_tags(f: BinaryIO, record: MetadataRecord, start: int, end: int) -> None:
    def walk(buf: bytes, depth: int):
        s = io.BytesIO(buf)
        for eid, data in _children(s, 0, len(buf)):
            if eid == 0x67C8:  # SimpleTag
                name = value = None
                ss = io.BytesIO(data)
                for cid, cdata in _children(ss, 0, len(data)):
                    if cid == 0x45A3:
                        name = cdata.decode("utf-8", "replace")
                    elif cid == 0x4487:
                        value = cdata.decode("utf-8", "replace")
                if name and value:
                    record.set("Matroska", name.title().replace("_", ""), value)
            elif eid in (0x7373,) and depth < 4:
                walk(data, depth + 1)

    f.seek(start)
    blob = f.read(min(end - start, 4 * 1024 * 1024))
    walk(blob, 0)
