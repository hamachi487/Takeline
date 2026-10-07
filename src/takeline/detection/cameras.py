"""Detectors for dedicated camera brands: Sony, Canon, DJI, GoPro."""

from __future__ import annotations

from .base import DetectionContext, DeviceDetector, Signal, rx


class SonyDetector(DeviceDetector):
    code = "SONY"
    display_name = "Sony"
    make_pattern = rx(r"^\s*sony\b")
    MODEL = rx(r"^\s*(ILCE|ILME|ILCA|DSC-|ZV-|FX\d|HDR-|PXW-|FDR-|NEX-|SLT-|XQ\w|Xperia|PMW-|DCR-)")

    def signals(self, ctx: DetectionContext) -> list[Signal]:
        s = []
        for q, v in ctx.makes:
            if self.make_pattern.search(v):
                s.append(Signal(0.90, f"{q} = {v}"))
        for q, v in ctx.models:
            if self.MODEL.match(v):
                s.append(Signal(0.60, f"{q} = {v} (Sony model naming)"))
        if str(ctx.record.get("MakerNotes:MakerNoteType", "")).startswith("Sony"):
            s.append(Signal(0.80, "Sony maker notes present"))
        if str(ctx.record.get("Container:MajorBrand", "")).upper().startswith("XAVC"):
            s.append(Signal(0.60, "Container brand is XAVC (Sony)"))
        if ctx.record.get("XML:CreationDateValue") and any("sony" in v.lower() for _q, v in ctx.makes):
            s.append(Signal(0.0, "Sony XML metadata with capture date found"))
        return s


class CanonDetector(DeviceDetector):
    code = "CANON"
    display_name = "Canon"
    make_pattern = rx(r"^\s*canon\b")
    MODEL = rx(r"^\s*(canon\b|EOS\b|PowerShot|IXUS|IXY|VIXIA|LEGRIA|XF\d|XA\d)")

    def signals(self, ctx: DetectionContext) -> list[Signal]:
        s = []
        for q, v in ctx.makes:
            if self.make_pattern.search(v):
                s.append(Signal(0.90, f"{q} = {v}"))
        for q, v in ctx.models:
            if self.MODEL.match(v):
                s.append(Signal(0.80 if v.lower().startswith("canon") else 0.60, f"{q} = {v}"))
        for q, v in ctx.software:
            if v.lower().startswith("canon"):
                s.append(Signal(0.75, f"{q} = {v}"))
        if "Canon" in ctx.record.groups:
            s.append(Signal(0.80, "Canon metadata box present"))
        if ctx.record.get("MakerNotes:MakerNoteType") == "Canon":
            s.append(Signal(0.50, "Canon maker notes present"))
        return s


class DJIDetector(DeviceDetector):
    code = "DJI"
    display_name = "DJI"
    make_pattern = rx(r"\bdji\b|hasselblad")
    MODEL = rx(r"^\s*(FC\d{3,4}|DJI\b|Mavic|Osmo|Phantom|Inspire|Avata|Matrice|L1D-|L2D-|OT-\d|AC\d{3})")
    TEXT = rx(r"\bdji\b")

    def signals(self, ctx: DetectionContext) -> list[Signal]:
        s = []
        for q, v in ctx.makes:
            if self.TEXT.search(v):
                s.append(Signal(0.90, f"{q} = {v}"))
        for q, v in ctx.models:
            if self.MODEL.match(v):
                s.append(Signal(0.60, f"{q} = {v} (DJI model naming)"))
        if ctx.has_group("XMP-drone-dji"):
            s.append(Signal(0.90, "DJI drone XMP metadata present"))
        for q, v in ctx.software + ctx.handlers + ctx.texts:
            if self.TEXT.search(v):
                s.append(Signal(0.70, f"{q} = {v}"))
                break
        if str(ctx.record.get("MakerNotes:MakerNoteType", "")) == "DJI":
            s.append(Signal(0.80, "DJI maker notes present"))
        if ctx.filename.upper().startswith("DJI_"):
            s.append(Signal(0.25, "Filename starts with DJI_ (weak hint, never used alone)"))
        return s


class GoProDetector(DeviceDetector):
    code = "GOPRO"
    display_name = "GoPro"
    make_pattern = rx(r"^\s*gopro\b")
    FIRMWARE = rx(r"^H(D\d{1,2}|\d{2})[A-Z]?\.\d{2}\.")
    NAME = rx(r"^(G[HXLS]\d{6}|GOPR\d{4}|GP\d{6})\.(mp4|jpg|lrv)$")

    def signals(self, ctx: DetectionContext) -> list[Signal]:
        s = []
        for q, v in ctx.makes:
            if self.make_pattern.search(v):
                s.append(Signal(0.90, f"{q} = {v}"))
        for q, v in ctx.models:
            if v.lower().startswith(("hero", "gopro")):
                s.append(Signal(0.80, f"{q} = {v}"))
        for q, v in ctx.handlers:
            if "gopro" in v.lower():
                s.append(Signal(0.80, f"{q} = {v}"))
                break
        fw = ctx.record.get("UserData:FirmwareVersion")
        if isinstance(fw, str) and self.FIRMWARE.match(fw):
            s.append(Signal(0.60, f"UserData:FirmwareVersion = {fw} (GoPro firmware format)"))
        if ctx.record.get("UserData:Udta-GPMF") or ctx.record.get("UserData:GPMF"):
            s.append(Signal(0.50, "GoPro GPMF telemetry atom present"))
        if self.NAME.match(ctx.filename):
            s.append(Signal(0.25, "Filename follows GoPro naming (weak hint, never used alone)"))
        return s
