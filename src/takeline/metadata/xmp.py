"""XMP packet parsing.

Groups are named "XMP-<prefix>" using canonical prefixes derived from the
namespace URI (not whatever prefix the writer happened to choose), so
XMP-xmp:CreateDate and XMP-exif:DateTimeOriginal are stable lookups.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .record import MetadataRecord

NS_PREFIX = {
    "http://ns.adobe.com/xap/1.0/": "xmp",
    "http://ns.adobe.com/exif/1.0/": "exif",
    "http://cipa.jp/exif/1.0/": "exifEX",
    "http://ns.adobe.com/exif/1.0/aux/": "aux",
    "http://ns.adobe.com/tiff/1.0/": "tiff",
    "http://ns.adobe.com/photoshop/1.0/": "photoshop",
    "http://purl.org/dc/elements/1.1/": "dc",
    "http://ns.adobe.com/xap/1.0/mm/": "xmpMM",
    "http://ns.adobe.com/xmp/1.0/DynamicMedia/": "xmpDM",
    "http://ns.adobe.com/camera-raw-settings/1.0/": "crs",
    "http://ns.google.com/photos/1.0/camera/": "GCamera",
    "http://ns.google.com/photos/1.0/container/": "Container",
    "http://www.dji.com/drone-dji/1.0/": "drone-dji",
    "http://ns.apple.com/adjustment-settings/1.0/": "AppleAdjust",
    "http://iptc.org/std/Iptc4xmpCore/1.0/xmlns/": "iptcCore",
    "http://ns.adobe.com/xap/1.0/rights/": "xmpRights",
    "http://ns.adobe.com/lightroom/1.0/": "lr",
    "http://ns.useplus.org/ldf/xmp/1.0/": "plus",
}
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
MAX_XMP = 4 * 1024 * 1024

_PACKET_RX = re.compile(rb"<x:xmpmeta[\s\S]*?</x:xmpmeta>|<rdf:RDF[\s\S]*?</rdf:RDF>")


def _prefix_for(uri: str) -> str:
    if uri in NS_PREFIX:
        return NS_PREFIX[uri]
    tail = uri.rstrip("/#").rsplit("/", 1)[-1]
    tail = re.sub(r"[^A-Za-z0-9_-]", "", tail) or "ns"
    if re.fullmatch(r"\d+(\.\d+)*", tail):  # version segment, use the one before it
        parts = [p for p in uri.rstrip("/#").split("/") if p and not re.fullmatch(r"\d+(\.\d+)*", p)]
        tail = re.sub(r"[^A-Za-z0-9_-]", "", parts[-1]) if parts else "ns"
    return tail


def _split(tag: str) -> tuple[str, str]:
    if tag.startswith("{"):
        uri, _, local = tag[1:].partition("}")
        return uri, local
    return "", tag


def find_packet(data: bytes) -> bytes | None:
    m = _PACKET_RX.search(data[:MAX_XMP])
    return m.group(0) if m else None


def parse_xmp_bytes(data: bytes, record: MetadataRecord) -> bool:
    packet = find_packet(data)
    if packet is None:
        return False
    # Never resolve entities/DTDs from untrusted files.
    packet = re.sub(rb"<!DOCTYPE[\s\S]*?>", b"", packet)
    try:
        root = ET.fromstring(packet)
    except ET.ParseError as exc:
        record.warn(f"XMP: malformed packet ({exc})")
        return False
    for desc in root.iter(f"{{{RDF}}}Description"):
        for attr, value in desc.attrib.items():
            uri, local = _split(attr)
            if uri == RDF or not uri:
                continue
            record.set(f"XMP-{_prefix_for(uri)}", local[:1].upper() + local[1:], value)
        for child in desc:
            uri, local = _split(child.tag)
            if not uri:
                continue
            value = _element_value(child)
            if value:
                record.set(f"XMP-{_prefix_for(uri)}", local[:1].upper() + local[1:], value)
    return True


def _element_value(el: ET.Element) -> str | None:
    if el.text and el.text.strip() and len(el) == 0:
        return el.text.strip()
    resource = el.attrib.get(f"{{{RDF}}}resource")
    if resource:
        return resource
    items = [li.text.strip() for li in el.iter(f"{{{RDF}}}li") if li.text and li.text.strip()]
    if items:
        return "; ".join(items[:20])
    # Structured value: flatten attributes of nested Description
    parts = []
    for sub in el.iter():
        for attr, v in sub.attrib.items():
            uri, local = _split(attr)
            if uri != RDF and v:
                parts.append(f"{local}={v}")
    return ", ".join(parts[:20]) or None
