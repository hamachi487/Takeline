"""Native ISO Base Media File Format parser.

Covers QuickTime (.mov), MPEG-4 (.mp4/.m4v) and HEIF/HEIC/AVIF images.
Only box headers and metadata payloads are read; media data (mdat) is
skipped with a seek, so multi-gigabyte clips are handled quickly.

Groups written (ExifTool-compatible names):
  QuickTime  movie header (mvhd)           CreateDate / ModifyDate / Duration
  TrackN     track + media headers          TrackCreateDate / MediaCreateDate ...
  Keys       meta/keys + ilst (mdta)        CreationDate / Make / Model / Software ...
  ItemList   meta/ilst (mdir, iTunes-style) ContentCreateDate / Encoder ...
  UserData   udta atoms                     Make / Model / ContentCreateDate / FirmwareVersion ...
  XML        XML metadata boxes (e.g. Sony) DeviceManufacturer / CreationDateValue ...
  Container  brands, codec, resolution, duration (informational)
"""

from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
from typing import BinaryIO, Iterator

from .binary import (MAX_PAYLOAD, ParseError, fourcc, qt_seconds_to_utc, read_exact,
                     u16be, u32be, u64be, uint_be)
from .record import MetadataRecord, printable_text
from . import exif as exif_mod
from . import xmp as xmp_mod

TOP_LEVEL_TYPES = {b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip", b"pnot", b"uuid",
                   b"meta", b"junk", b"PICT", b"styp", b"sidx", b"moof"}

XMP_UUID = bytes.fromhex("be7acfcb97a942e89c71999491e3afac")
CANON_UUID = bytes.fromhex("85c0b687820f11e08111f4ce462b6a48")

META_LIMIT = 16 * 1024 * 1024
SMALL_LIMIT = 1024 * 1024

UDTA_NAMES = {
    "©day": "ContentCreateDate", "©mak": "Make", "©mod": "Model", "©swr": "SoftwareVersion",
    "©swf": "SoftwareFormat", "©too": "Encoder", "©nam": "Title", "©cmt": "Comment",
    "©des": "Description", "©inf": "Information", "©xyz": "GPSCoordinates",
    "©fmt": "Format", "©src": "Source", "©ART": "Artist", "©aut": "Author",
    "©cpy": "Copyright", "©enc": "EncodedBy", "©req": "Requirements",
    "©dir": "Director", "©prd": "Producer", "©com": "Composer", "©alb": "Album",
    "©gen": "Genre", "©wrt": "Composer", "©prf": "Performers", "©dis": "Disclaimer",
    "desc": "Description", "ldes": "LongDescription", "cprt": "Copyright",
    "FIRM": "FirmwareVersion", "LENS": "LensSerialNumber", "CAME": "SerialNumberHash",
    "MUID": "MediaUID", "CNCV": "CompressorVersion", "CNMN": "Model", "CNFV": "FirmwareVersion",
    "manu": "Make", "modl": "Model", "name": "Name", "AllF": "PlayAllFrames",
    "date": "DateTimeOriginal", "TAGS": "Tags", "PANA": "PanasonicInfo", "NCDT": "NikonInfo",
    "SDLN": "PlayMode", "XYZ ": "GPSCoordinates", "cdat": "CreateDate",
}
BINARY_UDTA = {"GPMF", "CNTH", "CNDA", "HMMT", "PANA", "NCDT", "hinf", "hnti", "Xtra", "thmb",
               "CAME", "MUID", "free", "smta", "SNum", "PTNL"}

CODECS = {
    "avc1": "H.264/AVC", "avc3": "H.264/AVC", "hvc1": "HEVC/H.265", "hev1": "HEVC/H.265",
    "dvh1": "Dolby Vision HEVC", "av01": "AV1", "vp09": "VP9", "mp4v": "MPEG-4 Visual",
    "jpeg": "Motion JPEG", "mjpa": "Motion JPEG", "apch": "Apple ProRes 422 HQ",
    "apcn": "Apple ProRes 422", "apcs": "Apple ProRes 422 LT", "apco": "Apple ProRes 422 Proxy",
    "ap4h": "Apple ProRes 4444", "ap4x": "Apple ProRes 4444 XQ", "aprn": "Apple ProRes RAW",
    "mp4a": "AAC", "lpcm": "PCM", "sowt": "PCM", "twos": "PCM", "ac-3": "AC-3", "ec-3": "E-AC-3",
    "Opus": "Opus", "alac": "Apple Lossless", "mebx": "Timed metadata", "tmcd": "Timecode",
}
BRANDS = {
    "qt  ": "QuickTime movie", "isom": "MPEG-4", "iso2": "MPEG-4", "mp41": "MPEG-4",
    "mp42": "MPEG-4", "M4V ": "MPEG-4 (Apple M4V)", "M4VH": "MPEG-4 (Apple M4V)",
    "XAVC": "Sony XAVC", "heic": "HEIC image", "heix": "HEIC image", "mif1": "HEIF image",
    "msf1": "HEIF sequence", "avif": "AVIF image", "crx ": "Canon CR3 raw", "3gp4": "3GPP",
    "3gp5": "3GPP", "3gp6": "3GPP", "3g2a": "3GPP2", "dash": "MPEG-DASH", "MSNV": "Sony MP4",
}


def looks_like_isobmff(head: bytes) -> bool:
    return len(head) >= 8 and head[4:8] in TOP_LEVEL_TYPES


def keys_tag_name(key: str) -> str:
    """Map a QuickTime 'mdta' key to an ExifTool-style tag name.

    com.apple.quicktime.creationdate -> CreationDate
    com.apple.quicktime.location.ISO6709 -> GPSCoordinates
    other namespaces keep their full key so nothing is hidden.
    """
    special = {
        "com.apple.quicktime.location.ISO6709": "GPSCoordinates",
        "com.apple.quicktime.creationdate": "CreationDate",
        "com.android.manufacturer": "AndroidManufacturer",
        "com.android.model": "AndroidModel",
        "com.android.version": "AndroidVersion",
        "com.android.capture.fps": "AndroidCaptureFPS",
    }
    if key in special:
        return special[key]
    prefix = "com.apple.quicktime."
    if key.startswith(prefix):
        parts = re.split(r"[.\-_]", key[len(prefix):])
        return "".join(p[:1].upper() + p[1:] for p in parts if p)
    return key


def _iter_mem(buf: bytes, start: int, end: int) -> Iterator[tuple[str, int, int]]:
    """Iterate boxes inside an in-memory buffer: yields (type, payload_start, box_end)."""
    pos = start
    guard = 0
    while pos + 8 <= end and guard < 100_000:
        guard += 1
        size = u32be(buf, pos)
        typ = fourcc(buf[pos + 4:pos + 8])
        header = 8
        if size == 1:
            if pos + 16 > end:
                return
            size = u64be(buf, pos + 8)
            header = 16
        elif size == 0:
            size = end - pos
        if size < header:
            return
        box_end = min(pos + size, end)
        yield typ, pos + header, box_end
        pos = pos + size


class IsoBmffParser:
    def __init__(self, f: BinaryIO, record: MetadataRecord, file_size: int):
        self.f = f
        self.record = record
        self.size = file_size
        self.track = 0
        self.has_moov = False
        self.has_heif_meta = False
        self.video_size_set = False

    # ---------------------------------------------------------------- file IO
    def _boxes(self, start: int, end: int, depth: int = 0) -> Iterator[tuple[str, int, int, int]]:
        """Iterate boxes in the file: yields (type, box_start, payload_start, box_end)."""
        pos = start
        guard = 0
        while pos + 8 <= end and guard < 100_000:
            guard += 1
            self.f.seek(pos)
            hdr = self.f.read(8)
            if len(hdr) < 8:
                return
            size = u32be(hdr, 0)
            raw_type = hdr[4:8]
            typ = fourcc(raw_type)
            header = 8
            if size == 1:
                ext = self.f.read(8)
                if len(ext) < 8:
                    return
                size = u64be(ext, 0)
                header = 16
            elif size == 0:
                size = end - pos
            if size < header:
                self.record.warn(f"Malformed box '{typ}' at offset {pos} (size {size}); stopped reading this level")
                return
            box_end = pos + size
            if box_end > end:
                self.record.warn(f"Box '{typ}' at offset {pos} extends past its parent/file; truncated")
                box_end = end
            yield typ, pos, pos + header, box_end
            pos = pos + size

    def _payload(self, start: int, end: int, limit: int = SMALL_LIMIT) -> bytes | None:
        n = end - start
        if n < 0:
            return None
        if n > limit:
            self.record.warn(f"Skipped oversized metadata box at offset {start} ({n} bytes)")
            return None
        self.f.seek(start)
        return read_exact(self.f, n)

    # ------------------------------------------------------------------- main
    def parse(self) -> None:
        for typ, bstart, pstart, bend in self._boxes(0, self.size):
            try:
                if typ == "ftyp":
                    self._ftyp(self._payload(pstart, bend))
                elif typ == "moov":
                    self.has_moov = True
                    self._moov(pstart, bend)
                elif typ == "meta":
                    data = self._payload(pstart, bend, META_LIMIT)
                    if data is not None:
                        self._meta(data, group_hint="Meta", file_offset=pstart, top_level=True)
                elif typ == "uuid":
                    self._uuid(pstart, bend)
            except (ParseError, struct.error, IndexError, ValueError) as exc:
                self.record.warn(f"Box '{typ}': {exc}")
        if self.has_moov:
            self.record.container = "quicktime"
        elif self.has_heif_meta:
            self.record.container = "heif"
        else:
            self.record.container = "quicktime"
            self.record.warn("No movie header (moov) found: the file may be incomplete or corrupted")

    def _ftyp(self, data: bytes | None) -> None:
        if not data or len(data) < 8:
            return
        major = fourcc(data[0:4])
        brands = [fourcc(data[i:i + 4]) for i in range(8, len(data) - 3, 4)]
        self.record.set("Container", "MajorBrand", major.strip() or major)
        self.record.set("Container", "Format", BRANDS.get(major, f"ISO media ({major.strip()})"))
        if brands:
            self.record.set("Container", "CompatibleBrands", ", ".join(b.strip() for b in brands[:12]))

    # ------------------------------------------------------------------- moov
    def _moov(self, start: int, end: int) -> None:
        for typ, _bs, ps, be in self._boxes(start, end):
            try:
                if typ == "mvhd":
                    self._mvhd(self._payload(ps, be))
                elif typ == "trak":
                    self.track += 1
                    self._trak(ps, be, f"Track{self.track}")
                elif typ == "udta":
                    self._udta(ps, be, "UserData")
                elif typ == "meta":
                    data = self._payload(ps, be, META_LIMIT)
                    if data is not None:
                        self._meta(data, group_hint="Keys", file_offset=ps)
                elif typ == "uuid":
                    self._uuid(ps, be)
            except (ParseError, struct.error, IndexError, ValueError) as exc:
                self.record.warn(f"moov/{typ}: {exc}")

    def _mvhd(self, d: bytes | None) -> None:
        if not d or len(d) < 20:
            return
        if d[0] == 1 and len(d) >= 32:
            created, modified = u64be(d, 4), u64be(d, 12)
            timescale, duration = u32be(d, 20), u64be(d, 24)
        else:
            created, modified = u32be(d, 4), u32be(d, 8)
            timescale, duration = u32be(d, 12), u32be(d, 16)
        self.record.set("QuickTime", "CreateDate", qt_seconds_to_utc(created))
        self.record.set("QuickTime", "ModifyDate", qt_seconds_to_utc(modified))
        if timescale:
            secs = round(duration / timescale, 3)
            self.record.set("QuickTime", "Duration", secs)
            self.record.set("Container", "Duration", _fmt_duration(secs))

    def _trak(self, start: int, end: int, group: str) -> None:
        handler = None
        for typ, _bs, ps, be in self._boxes(start, end):
            if typ == "tkhd":
                self._tkhd(self._payload(ps, be), group)
            elif typ == "mdia":
                handler = self._mdia(ps, be, group)
            elif typ == "udta":
                self._udta(ps, be, "UserData")
            elif typ == "meta":
                data = self._payload(ps, be, META_LIMIT)
                if data is not None:
                    self._meta(data, group_hint=group, file_offset=ps)
        if handler == "vide" and not self.video_size_set:
            w = self.record.get(f"{group}:ImageWidth")
            h = self.record.get(f"{group}:ImageHeight")
            if w and h:
                self.record.set("Container", "Resolution", f"{w} × {h}")
                self.video_size_set = True

    def _tkhd(self, d: bytes | None, group: str) -> None:
        if not d or len(d) < 24:
            return
        if d[0] == 1 and len(d) >= 96:
            created, modified, tid = u64be(d, 4), u64be(d, 12), u32be(d, 20)
            wpos = 88
        else:
            created, modified, tid = u32be(d, 4), u32be(d, 8), u32be(d, 12)
            wpos = 76
        self.record.set(group, "TrackID", tid)
        self.record.set(group, "TrackCreateDate", qt_seconds_to_utc(created))
        self.record.set(group, "TrackModifyDate", qt_seconds_to_utc(modified))
        if len(d) >= wpos + 8:
            w, h = u32be(d, wpos) >> 16, u32be(d, wpos + 4) >> 16
            if w and h:
                self.record.set(group, "ImageWidth", w)
                self.record.set(group, "ImageHeight", h)

    def _mdia(self, start: int, end: int, group: str) -> str | None:
        handler = None
        for typ, _bs, ps, be in self._boxes(start, end):
            if typ == "mdhd":
                d = self._payload(ps, be)
                if d and len(d) >= 20:
                    if d[0] == 1 and len(d) >= 32:
                        created, modified = u64be(d, 4), u64be(d, 12)
                    else:
                        created, modified = u32be(d, 4), u32be(d, 8)
                    self.record.set(group, "MediaCreateDate", qt_seconds_to_utc(created))
                    self.record.set(group, "MediaModifyDate", qt_seconds_to_utc(modified))
            elif typ == "hdlr":
                handler = self._hdlr(self._payload(ps, be), group)
            elif typ == "minf":
                for t2, _b2, p2, e2 in self._boxes(ps, be):
                    if t2 == "stbl":
                        for t3, _b3, p3, e3 in self._boxes(p2, e2):
                            if t3 == "stsd":
                                self._stsd(self._payload(p3, e3, 256 * 1024), group)
                    elif t2 == "hdlr":
                        self._hdlr(self._payload(p2, e2), group, data_handler=True)
        return handler

    def _hdlr(self, d: bytes | None, group: str, data_handler: bool = False) -> str | None:
        if not d or len(d) < 12:
            return None
        htype = fourcc(d[8:12])
        name_raw = d[24:]
        name = ""
        if name_raw:
            if name_raw[0] == len(name_raw) - 1 or (0 < name_raw[0] < 32 and name_raw[0] <= len(name_raw) - 1):
                name = name_raw[1:1 + name_raw[0]].decode("utf-8", errors="replace")  # Pascal string
            else:
                name = name_raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
        if data_handler:
            return htype
        self.record.set(group, "HandlerType", htype)
        if name.strip():
            self.record.set(group, "HandlerDescription", name.strip())
        return htype

    def _stsd(self, d: bytes | None, group: str) -> None:
        if not d or len(d) < 16:
            return
        count = u32be(d, 4)
        if count < 1:
            return
        entry = d[8:]
        if len(entry) < 8:
            return
        fmt = fourcc(entry[4:8])
        self.record.set(group, "CompressorID", fmt)
        codec = CODECS.get(fmt)
        if codec:
            self.record.set(group, "Codec", codec)
        if len(entry) >= 82 and fmt not in ("mp4a", "lpcm", "sowt", "twos", "tmcd", "mebx"):
            n = entry[50]
            if 0 < n < 32:
                cname = entry[51:51 + n].decode("utf-8", errors="replace").strip("\x00 ")
                if cname:
                    self.record.set(group, "CompressorName", cname)

    # ------------------------------------------------------------------- udta
    def _udta(self, start: int, end: int, group: str) -> None:
        for typ, _bs, ps, be in self._boxes(start, end):
            try:
                if typ == "meta":
                    data = self._payload(ps, be, META_LIMIT)
                    if data is not None:
                        self._meta(data, group_hint="ItemList", file_offset=ps)
                    continue
                if typ == "XMP_":
                    data = self._payload(ps, be, xmp_mod.MAX_XMP)
                    if data:
                        xmp_mod.parse_xmp_bytes(data, self.record)
                    continue
                if be - ps > SMALL_LIMIT:
                    self.record.set(group, UDTA_NAMES.get(typ, f"Udta-{typ.strip()}"),
                                    f"<binary data, {be - ps} bytes>")
                    continue
                data = self._payload(ps, be)
                if data is None:
                    continue
                name = UDTA_NAMES.get(typ, f"Udta-{typ.strip()}")
                if typ in BINARY_UDTA:
                    text = printable_text(data)
                    self.record.set(group, name, text if text else f"<binary data, {len(data)} bytes>")
                    continue
                value = self._udta_value(typ, data)
                if value is not None:
                    self.record.set(group, name, value)
            except (ParseError, struct.error, IndexError) as exc:
                self.record.warn(f"udta/{typ}: {exc}")

    def _udta_value(self, typ: str, data: bytes):
        # ffmpeg-style: atom containing a 'data' box
        if len(data) >= 16 and data[4:8] == b"data":
            for t, ps, be in _iter_mem(data, 0, len(data)):
                if t == "data":
                    return _decode_data_box(data[ps:be])
        # QuickTime international text: [len u16][lang u16][text]...
        if typ.startswith("©") and len(data) >= 4:
            texts = []
            pos = 0
            while pos + 4 <= len(data):
                n = u16be(data, pos)
                if n == 0 or pos + 4 + n > len(data):
                    break
                chunk = data[pos + 4:pos + 4 + n]
                lang = u16be(data, pos + 2)
                enc = "mac_roman" if lang < 0x400 and lang != 0x7FFF else "utf-8"
                if chunk.startswith(b"\xFE\xFF"):
                    texts.append(chunk[2:].decode("utf-16-be", errors="replace"))
                else:
                    try:
                        texts.append(chunk.decode("utf-8"))
                    except UnicodeDecodeError:
                        texts.append(chunk.decode(enc, errors="replace"))
                pos += 4 + n
            if texts:
                return "; ".join(t.strip("\x00 ") for t in texts if t.strip("\x00 "))
        # Plain or lightly-prefixed text (FIRM, manu, modl, CNCV, ...)
        text = printable_text(data)
        if text is None and len(data) > 4:
            text = printable_text(data[4:])
        if text is not None:
            return text
        return f"<binary data, {len(data)} bytes>"

    # ------------------------------------------------------------------- meta
    def _meta(self, data: bytes, group_hint: str, file_offset: int, top_level: bool = False) -> None:
        # QuickTime 'meta' has no version/flags; ISO 'meta' is a FullBox whose
        # first four bytes (version + flags) are zero.
        start = 4 if data[:4] == b"\x00\x00\x00\x00" and data[4:8] != b"hdlr" else 0
        handler = None
        keys: list[str] = []
        children = list(_iter_mem(data, start, len(data)))
        for typ, ps, be in children:
            if typ == "hdlr" and be - ps >= 12:
                handler = fourcc(data[ps + 8:ps + 12])
            elif typ == "keys":
                keys = self._keys(data[ps:be])
        if handler == "mdta" or keys:
            group = "Keys"
        elif handler == "mdir":
            group = "ItemList"
        else:
            group = group_hint
        heif_boxes = {}
        for typ, ps, be in children:
            if typ == "ilst":
                self._ilst(data[ps:be], group, keys)
            elif typ in ("xml ", "XML "):
                parse_xml_metadata(data[ps + 4:be], self.record)
            elif typ in ("iinf", "iloc", "iprp", "idat", "pitm"):
                heif_boxes[typ] = (ps, be)
        if heif_boxes and (top_level or handler == "pict"):
            self.has_heif_meta = True
            self._heif(data, heif_boxes, file_offset + 0)

    def _keys(self, d: bytes) -> list[str]:
        out: list[str] = []
        if len(d) < 8:
            return out
        count = u32be(d, 4)
        pos = 8
        for _ in range(min(count, 10_000)):
            if pos + 8 > len(d):
                break
            size = u32be(d, pos)
            if size < 8 or pos + size > len(d):
                self.record.warn("Keys: malformed key entry")
                break
            out.append(d[pos + 8:pos + size].decode("utf-8", errors="replace"))
            pos += size
        return out

    def _ilst(self, d: bytes, group: str, keys: list[str]) -> None:
        for raw_type, ps, be in _iter_mem_raw(d, 0, len(d)):
            idx = struct.unpack(">I", raw_type)[0]
            if group == "Keys" and keys and 1 <= idx <= len(keys):
                name = keys_tag_name(keys[idx - 1])
            else:
                t = fourcc(raw_type)
                name = UDTA_NAMES.get(t, t.strip() if t.isprintable() else f"Item{idx}")
            freeform_name = None
            for t2, p2, e2 in _iter_mem(d, ps, be):
                if t2 == "name" and e2 - p2 > 4:
                    freeform_name = d[p2 + 4:e2].decode("utf-8", errors="replace")
                elif t2 == "data":
                    value = _decode_data_box(d[p2:e2])
                    if value is not None:
                        self.record.set(group, freeform_name or name, value)

    # ------------------------------------------------------------------- HEIF
    def _heif(self, data: bytes, boxes: dict, file_offset: int) -> None:
        items: dict[int, dict] = {}
        if "iinf" in boxes:
            ps, be = boxes["iinf"]
            d = data[ps:be]
            ver = d[0]
            pos = 6 if ver == 0 else 8
            for t, p2, e2 in _iter_mem(d, pos, len(d)):
                if t != "infe":
                    continue
                e = d[p2:e2]
                iv = e[0]
                if iv >= 2:
                    if iv == 2:
                        iid = u16be(e, 4)
                        p = 8
                    else:
                        iid = u32be(e, 4)
                        p = 10
                    itype = fourcc(e[p:p + 4])
                    rest = e[p + 4:].split(b"\x00")
                    content_type = rest[1].decode("utf-8", "replace") if itype == "mime" and len(rest) > 1 else ""
                    items[iid] = {"type": itype, "content_type": content_type}
        if "iloc" in boxes:
            ps, be = boxes["iloc"]
            idat_start = boxes["idat"][0] if "idat" in boxes else None
            self._iloc(data[ps:be], items, idat_start, data)
        width = height = 0
        if "iprp" in boxes:
            ps, be = boxes["iprp"]
            for t, p2, e2 in _iter_mem(data, ps, be):
                if t == "ipco":
                    for t3, p3, e3 in _iter_mem(data, p2, e2):
                        if t3 == "ispe" and e3 - p3 >= 12:
                            w, h = u32be(data, p3 + 4), u32be(data, p3 + 8)
                            if w * h > width * height:
                                width, height = w, h
        if width and height:
            self.record.set("Container", "ImageWidth", width)
            self.record.set("Container", "ImageHeight", height)
        counts: dict[str, int] = {}
        for iid, info in items.items():
            counts[info["type"]] = counts.get(info["type"], 0) + 1
            try:
                if info["type"] == "Exif" and info.get("extents"):
                    blob = self._read_item(info, idat_start=None)
                    if blob and len(blob) > 8:
                        tiff = _locate_tiff(blob)
                        if tiff is not None:
                            exif_mod.TiffParser(tiff, self.record).parse()
                elif info["type"] == "mime" and "xml" in info.get("content_type", "") and info.get("extents"):
                    blob = self._read_item(info, idat_start=None)
                    if blob:
                        xmp_mod.parse_xmp_bytes(blob, self.record)
            except (ParseError, struct.error, ValueError) as exc:
                self.record.warn(f"HEIF item {iid} ({info['type']}): {exc}")
        if counts:
            self.record.set("Container", "HEIFItems", ", ".join(f"{k}×{v}" for k, v in sorted(counts.items())))

    def _iloc(self, d: bytes, items: dict, idat_start, meta_data: bytes) -> None:
        ver = d[0]
        pos = 4
        b1, b2 = d[pos], d[pos + 1]
        off_size, len_size = b1 >> 4, b1 & 0x0F
        base_size = b2 >> 4
        idx_size = (b2 & 0x0F) if ver in (1, 2) else 0
        pos += 2
        if ver < 2:
            count = u16be(d, pos)
            pos += 2
        else:
            count = u32be(d, pos)
            pos += 4
        for _ in range(min(count, 100_000)):
            if ver < 2:
                iid = u16be(d, pos)
                pos += 2
            else:
                iid = u32be(d, pos)
                pos += 4
            method = 0
            if ver in (1, 2):
                method = u16be(d, pos) & 0x0F
                pos += 2
            pos += 2  # data_reference_index
            base = uint_be(d, pos, base_size)
            pos += base_size
            n_ext = u16be(d, pos)
            pos += 2
            extents = []
            for _e in range(n_ext):
                if idx_size:
                    pos += idx_size
                eo = uint_be(d, pos, off_size)
                pos += off_size
                el = uint_be(d, pos, len_size)
                pos += len_size
                extents.append((base + eo, el))
            if iid in items:
                items[iid]["extents"] = extents
                items[iid]["method"] = method
                if method == 1 and idat_start is not None:
                    items[iid]["idat"] = meta_data[idat_start:]

    def _read_item(self, info: dict, idat_start) -> bytes | None:
        chunks = []
        total = 0
        for off, length in info["extents"]:
            if info.get("method") == 1:
                src = info.get("idat")
                if src is None:
                    return None
                if length == 0:
                    length = len(src) - off
                chunks.append(src[off:off + length])
            else:
                if length == 0:
                    length = self.size - off
                if off < 0 or off + length > self.size or length > 8 * 1024 * 1024:
                    raise ParseError("item extent outside the file")
                self.f.seek(off)
                chunks.append(read_exact(self.f, length))
            total += length
            if total > MAX_PAYLOAD:
                raise ParseError("item too large")
        return b"".join(chunks)

    # ------------------------------------------------------------------- uuid
    def _uuid(self, start: int, end: int) -> None:
        if end - start < 16:
            return
        self.f.seek(start)
        uid = self.f.read(16)
        if uid == XMP_UUID:
            data = self._payload(start + 16, end, xmp_mod.MAX_XMP)
            if data:
                xmp_mod.parse_xmp_bytes(data, self.record)
        elif uid == CANON_UUID:
            data = self._payload(start + 16, end, 4 * 1024 * 1024)
            if data:
                for t, ps, be in _iter_mem(data, 0, len(data)):
                    if t in ("CNCV", "CNMN", "CNFV"):
                        txt = printable_text(data[ps:be])
                        if txt:
                            self.record.set("Canon", UDTA_NAMES.get(t, t), txt)
                    elif t in ("CMT1", "CMT2", "CMT4"):
                        try:
                            p = exif_mod.TiffParser(data[ps:be], self.record)
                            if t == "CMT1":
                                p.parse()
                            else:
                                grp, names = ("ExifIFD", exif_mod.EXIF_TAGS) if t == "CMT2" else ("GPS", exif_mod.GPS_TAGS)
                                p._parse_ifd(p.ifd0, grp, names, follow_next=False)
                        except (ParseError, struct.error) as exc:
                            self.record.warn(f"Canon {t}: {exc}")
        else:
            self.record.set("Other", f"UUID-{uid.hex()[:8]}", f"<{end - start - 16} bytes>")


def _iter_mem_raw(buf: bytes, start: int, end: int) -> Iterator[tuple[bytes, int, int]]:
    pos = start
    while pos + 8 <= end:
        size = u32be(buf, pos)
        if size < 8 or pos + size > end:
            break
        yield buf[pos + 4:pos + 8], pos + 8, pos + size
        pos += size


def _decode_data_box(d: bytes):
    """Decode an ilst 'data' box payload (well-known type + locale + value)."""
    if len(d) < 8:
        return None
    wkt = int.from_bytes(d[1:4], "big")
    v = d[8:]
    try:
        if wkt in (1, 4):
            return v.decode("utf-8", errors="replace")
        if wkt in (2, 5):
            return v.decode("utf-16-be", errors="replace")
        if wkt in (21, 65, 66, 67, 74):
            return int.from_bytes(v, "big", signed=True) if v else None
        if wkt in (22, 75, 76, 77, 78):
            return int.from_bytes(v, "big", signed=False) if v else None
        if wkt == 23 and len(v) == 4:
            return round(struct.unpack(">f", v)[0], 6)
        if wkt == 24 and len(v) == 8:
            return struct.unpack(">d", v)[0]
        if wkt == 13:
            return f"<JPEG image, {len(v)} bytes>"
        if wkt == 14:
            return f"<PNG image, {len(v)} bytes>"
        if wkt == 27:
            return f"<BMP image, {len(v)} bytes>"
    except (UnicodeDecodeError, struct.error):
        pass
    text = printable_text(v)
    return text if text is not None else f"<binary data, {len(v)} bytes>"


def _locate_tiff(blob: bytes) -> bytes | None:
    """HEIF Exif items start with a 4-byte offset to the TIFF header."""
    if len(blob) >= 4:
        off = u32be(blob, 0)
        p = 4 + off
        if p + 8 <= len(blob) and blob[p:p + 2] in (b"MM", b"II"):
            return blob[p:]
    for mark in (b"MM\x00*", b"II*\x00"):
        i = blob.find(mark, 0, 64)
        if i >= 0:
            return blob[i:]
    return None


def parse_xml_metadata(data: bytes, record: MetadataRecord) -> None:
    """Flatten an embedded XML metadata document (e.g. Sony NonRealTimeMeta).

    <Device manufacturer="Sony" modelName="ILCE-7M4"/> -> XML:DeviceManufacturer, XML:DeviceModelName
    <CreationDate value="2026-07-17T19:30:15-03:00"/>  -> XML:CreationDateValue
    """
    data = data[:2 * 1024 * 1024]
    data = re.sub(rb"<!DOCTYPE[\s\S]*?>", b"", data)
    i = data.find(b"<")
    if i < 0:
        return
    try:
        root = ET.fromstring(data[i:].rstrip(b"\x00 \r\n\t"))
    except ET.ParseError as exc:
        record.warn(f"XML metadata: {exc}")
        return
    if b"x:xmpmeta" in data[:200] or b"rdf:RDF" in data[:400]:
        xmp_mod.parse_xmp_bytes(data, record)
        return
    n = 0
    for el in root.iter():
        local = el.tag.rsplit("}", 1)[-1]
        for attr, value in el.attrib.items():
            a = attr.rsplit("}", 1)[-1]
            record.set("XML", f"{local}{a[:1].upper()}{a[1:]}", value)
            n += 1
        if el.text and el.text.strip() and len(el) == 0:
            record.set("XML", local, el.text.strip())
            n += 1
        if n > 400:
            break


def _fmt_duration(secs: float) -> str:
    m, s = divmod(secs, 60)
    h, m = divmod(int(m), 60)
    if h:
        return f"{h}:{m:02d}:{s:06.3f}"
    return f"{m}:{s:06.3f}"
