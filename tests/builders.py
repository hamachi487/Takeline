"""Builders for small, structurally valid media files used by the test suite.

IMPORTANT: these are SYNTHETIC fixtures. They reproduce the container
structures (QuickTime keys/ilst/udta, EXIF IFDs, HEIF items, ...) that real
devices use, with metadata values chosen for the tests. They are NOT captures
from real Ray-Ban Meta glasses or iPhones and must not be treated as ground
truth for what those devices write. Real-device verification lives in
tests/real_samples (see the README there).

ExifTool parses every file produced here (see test_exiftool_crosscheck.py),
which keeps the builders honest.
"""

from __future__ import annotations

import datetime as dt
import struct
from pathlib import Path

QT_EPOCH = dt.datetime(1904, 1, 1, tzinfo=dt.timezone.utc)
MATRIX = struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)


# ----------------------------------------------------------------- ISO BMFF
def box(typ: bytes, payload: bytes = b"") -> bytes:
    return struct.pack(">I", 8 + len(payload)) + typ + payload


def full(typ: bytes, payload: bytes, version: int = 0, flags: int = 0) -> bytes:
    return box(typ, struct.pack(">I", (version << 24) | flags) + payload)


def qt_secs(when: dt.datetime | None) -> int:
    if when is None:
        return 0
    return int((when.astimezone(dt.timezone.utc) - QT_EPOCH).total_seconds())


def mvhd(created: dt.datetime | None, duration_s: float = 3.0) -> bytes:
    c = qt_secs(created)
    p = struct.pack(">IIII", c, c, 600, int(duration_s * 600))
    p += struct.pack(">IH10s", 0x10000, 0x100, b"") + MATRIX + b"\x00" * 24 + struct.pack(">I", 3)
    return full(b"mvhd", p)


def tkhd(created, track_id, width, height) -> bytes:
    c = qt_secs(created)
    p = struct.pack(">IIIII", c, c, track_id, 0, 1800) + b"\x00" * 8
    p += struct.pack(">hhhH", 0, 0, 0, 0) + MATRIX + struct.pack(">II", width << 16, height << 16)
    return full(b"tkhd", p, flags=3)


def mdhd(created) -> bytes:
    c = qt_secs(created)
    return full(b"mdhd", struct.pack(">IIIIHH", c, c, 600, 1800, 0x55C4, 0))


def hdlr(handler: bytes, name: str, pascal: bool = True, component: bytes = b"mhlr") -> bytes:
    nb = name.encode("utf-8")
    name_field = (bytes([len(nb)]) + nb) if pascal else nb + b"\x00"
    return full(b"hdlr", component + handler + b"\x00" * 12 + name_field)


def stsd_video(fmt: bytes, width: int, height: int, compressor: str = "") -> bytes:
    cn = compressor.encode()[:31]
    entry_body = b"\x00" * 6 + struct.pack(">H", 1) + b"\x00" * 16
    entry_body += struct.pack(">HHIIIH", width, height, 0x480000, 0x480000, 0, 1)
    entry_body += bytes([len(cn)]) + cn.ljust(31, b"\x00") + struct.pack(">Hh", 24, -1)
    entry = struct.pack(">I", 8 + len(entry_body)) + fmt + entry_body
    return full(b"stsd", struct.pack(">I", 1) + entry)


def stsd_audio(fmt: bytes = b"mp4a") -> bytes:
    body = b"\x00" * 6 + struct.pack(">H", 1) + b"\x00" * 8 + struct.pack(">HHHHI", 2, 16, 0, 0, 48000 << 16)
    entry = struct.pack(">I", 8 + len(body)) + fmt + body
    return full(b"stsd", struct.pack(">I", 1) + entry)


def trak(track_id, kind, handler_name, codec, created, width=1920, height=1080, compressor="",
         pascal=True) -> bytes:
    if kind == b"vide":
        stsd = stsd_video(codec, width, height, compressor)
        w, h = width, height
    else:
        stsd = stsd_audio(codec)
        w = h = 0
    stbl = box(b"stbl", stsd + full(b"stts", struct.pack(">I", 0)) + full(b"stsz", struct.pack(">II", 0, 0)))
    minf = box(b"minf", box(b"vmhd" if kind == b"vide" else b"smhd", b"\x00" * 12) + stbl)
    mdia = box(b"mdia", mdhd(created) + hdlr(kind, handler_name, pascal) + minf)
    return box(b"trak", tkhd(created, track_id, w, h) + mdia)


def data_atom(value) -> bytes:
    if isinstance(value, str):
        return box(b"data", struct.pack(">II", 1, 0) + value.encode("utf-8"))
    if isinstance(value, float):
        return box(b"data", struct.pack(">II", 23, 0) + struct.pack(">f", value))
    return box(b"data", struct.pack(">II", 21, 0) + struct.pack(">i", value))


def keys_meta(entries: dict[str, object]) -> bytes:
    """QuickTime-style 'meta' (no version/flags) with an mdta key table."""
    keys = b"".join(struct.pack(">I", 8 + len(k.encode())) + b"mdta" + k.encode() for k in entries)
    keys_box = full(b"keys", struct.pack(">I", len(entries)) + keys)
    items = b"".join(box(struct.pack(">I", i + 1), data_atom(v)) for i, v in enumerate(entries.values()))
    return box(b"meta", hdlr(b"mdta", "", pascal=False, component=b"\x00\x00\x00\x00") + keys_box + box(b"ilst", items))


def mdir_meta(items: dict[bytes, object]) -> bytes:
    """ISO-style 'meta' (FullBox) with iTunes-style ilst, as written by ffmpeg in udta."""
    ilst = b"".join(box(k, data_atom(v)) for k, v in items.items())
    return full(b"meta", hdlr(b"mdir", "", pascal=False, component=b"\x00\x00\x00\x00")[0:] + box(b"ilst", ilst))


def udta_text(items: dict[bytes, str], raw: dict[bytes, bytes] | None = None) -> bytes:
    payload = b""
    for k, v in items.items():
        t = v.encode("utf-8")
        payload += box(k, struct.pack(">HH", len(t), 0x55C4) + t)
    for k, v in (raw or {}).items():
        payload += box(k, v)
    return payload


def build_movie(path: Path, *, brand: bytes = b"qt  ", mvhd_time: dt.datetime | None = None,
                keys: dict[str, object] | None = None, udta: dict[bytes, str] | None = None,
                udta_raw: dict[bytes, bytes] | None = None, ilst: dict[bytes, object] | None = None,
                video_handler: str = "Core Media Video", compressor: str = "", codec: bytes = b"hvc1",
                width: int = 1920, height: int = 1080, with_audio: bool = True,
                extra_moov: bytes = b"", moov_at_end: bool = False, mdat_size: int = 4096,
                pascal: bool = True) -> Path:
    ftyp = box(b"ftyp", brand + struct.pack(">I", 0) + brand + (b"isom" if brand != b"qt  " else b""))
    moov_payload = mvhd(mvhd_time)
    moov_payload += trak(1, b"vide", video_handler, codec, mvhd_time, width, height, compressor, pascal)
    if with_audio:
        moov_payload += trak(2, b"soun", "Core Media Audio", b"mp4a", mvhd_time, pascal=pascal)
    udta_payload = b""
    if udta or udta_raw:
        udta_payload += udta_text(udta or {}, udta_raw)
    if ilst:
        udta_payload += mdir_meta(ilst)
    if udta_payload:
        moov_payload += box(b"udta", udta_payload)
    if keys:
        moov_payload += keys_meta(keys)
    moov_payload += extra_moov
    moov = box(b"moov", moov_payload)
    mdat = box(b"mdat", b"\x00" * mdat_size)
    blob = ftyp + (mdat + moov if moov_at_end else box(b"wide") + moov + mdat)
    path.write_bytes(blob)
    return path


# --------------------------------------------------------------------- TIFF
ASCII, SHORT, LONG, RATIONAL, UNDEFINED = 2, 3, 4, 5, 7


def tiff_bytes(ifd0: dict[int, tuple[int, object]], exif: dict[int, tuple[int, object]] | None = None,
               gps: dict[int, tuple[int, object]] | None = None, little: bool = False) -> bytes:
    """Build a TIFF/EXIF block. Values are (type, value); offsets computed automatically."""
    e = "<" if little else ">"

    def enc(typ, val) -> tuple[bytes, int]:
        if typ == ASCII:
            b = val.encode("utf-8") + b"\x00"
            return b, len(b)
        if typ == SHORT:
            vals = val if isinstance(val, (list, tuple)) else [val]
            return struct.pack(f"{e}{len(vals)}H", *vals), len(vals)
        if typ == LONG:
            vals = val if isinstance(val, (list, tuple)) else [val]
            return struct.pack(f"{e}{len(vals)}I", *vals), len(vals)
        if typ == RATIONAL:
            vals = val if isinstance(val, (list, tuple)) else [val]
            out = b"".join(struct.pack(f"{e}II", int(v * 1000), 1000) for v in vals)
            return out, len(vals)
        if typ == UNDEFINED:
            return bytes(val), len(val)
        raise ValueError(typ)

    def ifd_block(entries: dict, start: int, sub_ptrs: dict[int, int] | None = None) -> bytes:
        entries = dict(entries)
        for tag, off in (sub_ptrs or {}).items():
            entries[tag] = (LONG, off)
        tags = sorted(entries)
        n = len(tags)
        data_off = start + 2 + n * 12 + 4
        head = struct.pack(f"{e}H", n)
        data = b""
        for tag in tags:
            typ, val = entries[tag]
            raw, count = enc(typ, val)
            if len(raw) <= 4:
                head += struct.pack(f"{e}HHI", tag, typ, count) + raw.ljust(4, b"\x00")
            else:
                head += struct.pack(f"{e}HHII", tag, typ, count, data_off + len(data))
                data += raw + (b"\x00" if len(raw) % 2 else b"")
        head += struct.pack(f"{e}I", 0)
        return head + data

    header = (b"II*\x00" if little else b"MM\x00*") + struct.pack(f"{e}I", 8)

    def size_of(entries, extra_ptrs):
        return len(ifd_block(entries, 0, {t: 0 for t in extra_ptrs}))

    ptr_tags = []
    if exif is not None:
        ptr_tags.append(0x8769)
    if gps is not None:
        ptr_tags.append(0x8825)
    ifd0_len = size_of(ifd0, ptr_tags)
    pos = 8 + ifd0_len
    ptrs = {}
    blocks = b""
    if exif is not None:
        ptrs[0x8769] = pos
        b = ifd_block(exif, pos)
        blocks += b
        pos += len(b)
    if gps is not None:
        ptrs[0x8825] = pos
        b = ifd_block(gps, pos)
        blocks += b
        pos += len(b)
    return header + ifd_block(ifd0, 8, ptrs) + blocks


def exif_dict(make=None, model=None, software=None, original=None, offset=None, create=None,
              makernote: bytes | None = None, lens: str | None = None):
    ifd0 = {}
    if make:
        ifd0[0x010F] = (ASCII, make)
    if model:
        ifd0[0x0110] = (ASCII, model)
    if software:
        ifd0[0x0131] = (ASCII, software)
    exif = {0x9000: (UNDEFINED, b"0232")}
    if original:
        exif[0x9003] = (ASCII, original)
    if create:
        exif[0x9004] = (ASCII, create)
    if offset:
        exif[0x9011] = (ASCII, offset)
        exif[0x9010] = (ASCII, offset)
    if makernote:
        exif[0x927C] = (UNDEFINED, makernote)
    if lens:
        exif[0xA434] = (ASCII, lens)
    return ifd0, exif


def apple_makernote() -> bytes:
    """Apple iOS maker-note layout: header, version, 'MM', then an IFD (offsets relative to note)."""
    entries = struct.pack(">H", 1) + struct.pack(">HHII", 0x0001, 9, 1, 14) + struct.pack(">I", 0)
    return b"Apple iOS\x00" + b"\x00\x01" + b"MM" + entries


def build_jpeg(path: Path, tiff: bytes | None, xmp: str | None = None, width=64, height=48) -> Path:
    out = b"\xFF\xD8"
    out += b"\xFF\xE0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x02\x00\x00\x01\x00\x01\x00\x00"
    if tiff is not None:
        seg = b"Exif\x00\x00" + tiff
        out += b"\xFF\xE1" + struct.pack(">H", len(seg) + 2) + seg
    if xmp is not None:
        seg = b"http://ns.adobe.com/xap/1.0/\x00" + xmp.encode()
        out += b"\xFF\xE1" + struct.pack(">H", len(seg) + 2) + seg
    sof = struct.pack(">BHHB", 8, height, width, 1) + b"\x01\x11\x00"
    out += b"\xFF\xC0" + struct.pack(">H", len(sof) + 2) + sof
    out += b"\xFF\xDA" + struct.pack(">H", 8) + b"\x01\x01\x00\x00\x3F\x00" + b"\x00" * 16 + b"\xFF\xD9"
    path.write_bytes(out)
    return path


def build_heic(path: Path, tiff: bytes, width=4032, height=3024) -> Path:
    exif_payload = struct.pack(">I", 6) + b"Exif\x00\x00" + tiff
    ftyp = box(b"ftyp", b"heic" + struct.pack(">I", 0) + b"mif1heicmiaf")

    def meta_for(offset: int) -> bytes:
        hd = hdlr(b"pict", "", pascal=False, component=b"\x00\x00\x00\x00")
        pitm = full(b"pitm", struct.pack(">H", 1))
        infe1 = full(b"infe", struct.pack(">HH", 1, 0) + b"hvc1" + b"\x00", version=2)
        infe2 = full(b"infe", struct.pack(">HH", 2, 0) + b"Exif" + b"\x00", version=2, flags=1)
        iinf = full(b"iinf", struct.pack(">H", 2) + infe1 + infe2)
        # iloc v0: offset_size=4, length_size=4, base_offset_size=0
        iloc_body = bytes([0x44, 0x00]) + struct.pack(">H", 2)
        iloc_body += struct.pack(">HHHII", 1, 0, 1, offset + len(exif_payload), 16)
        iloc_body += struct.pack(">HHHII", 2, 0, 1, offset, len(exif_payload))
        iloc = full(b"iloc", iloc_body)
        ispe = full(b"ispe", struct.pack(">II", width, height))
        iprp = box(b"iprp", box(b"ipco", ispe))
        return full(b"meta", hd + pitm + iinf + iloc + iprp)

    meta_len = len(meta_for(0))
    data_offset = len(ftyp) + meta_len + 8
    blob = ftyp + meta_for(data_offset) + box(b"mdat", exif_payload + b"\x00" * 16)
    path.write_bytes(blob)
    return path


def build_png_with_exif(path: Path, tiff: bytes) -> Path:
    import zlib

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" + b"\x00" * 6 + b"\x00" + b"\x00" * 6)
    blob = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"eXIf", tiff) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    path.write_bytes(blob)
    return path


def build_avi(path: Path, idit: str | None, software: str | None = None) -> Path:
    def chunk(cid: bytes, data: bytes) -> bytes:
        return cid + struct.pack("<I", len(data)) + data + (b"\x00" if len(data) % 2 else b"")

    def lst(t: bytes, body: bytes) -> bytes:
        return b"LIST" + struct.pack("<I", len(body) + 4) + t + body

    avih = struct.pack("<10I", 33333, 0, 0, 0, 90, 0, 1, 0, 640, 480) + b"\x00" * 16
    hdrl_body = chunk(b"avih", avih)
    if idit:
        hdrl_body += chunk(b"IDIT", idit.encode() + b"\n\x00")
    body = b"AVI " + lst(b"hdrl", hdrl_body)
    if software:
        body += lst(b"INFO", chunk(b"ISFT", software.encode() + b"\x00"))
    body += lst(b"movi", b"")
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


def build_mkv(path: Path, date_utc: dt.datetime | None, writing_app: str = "TestWriter", webm=False) -> Path:
    def vint_size(n: int) -> bytes:
        return bytes([0x01]) + n.to_bytes(7, "big")

    def el(eid: int, data: bytes) -> bytes:
        idb = eid.to_bytes((eid.bit_length() + 7) // 8, "big")
        return idb + vint_size(len(data)) + data

    header = el(0x1A45DFA3, el(0x4282, b"webm" if webm else b"matroska"))
    info = el(0x2AD7B1, (1_000_000).to_bytes(3, "big")) + el(0x5741, writing_app.encode())
    info += el(0x4D80, b"libtest")
    if date_utc is not None:
        ns = int((date_utc - dt.datetime(2001, 1, 1, tzinfo=dt.timezone.utc)).total_seconds() * 1e9)
        info += el(0x4461, struct.pack(">q", ns))
    seg = el(0x18538067, el(0x1549A966, info))
    path.write_bytes(header + seg)
    return path
