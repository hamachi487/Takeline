"""Apple iPhone / iPad / generic Apple device detector.

A MOV container or Apple's QuickTime key namespace (com.apple.quicktime.*)
is NOT treated as evidence of an iPhone: other devices and apps write those
keys too. Only capture-identity evidence counts strongly: Make/Model, Apple
maker notes, an iPhone lens description. Apple camera-capture keys are a
moderate supporting signal and are discounted when Make names another brand.
"""

from __future__ import annotations

import re

from .base import DetectionContext, DeviceDetector, Signal, rx

APPLE_MAKE = rx(r"^\s*apple\b")
IPHONE = rx(r"^\s*iphone")
IPAD = rx(r"^\s*ipad")
IPOD = rx(r"^\s*ipod")
MAC = rx(r"^\s*(mac|imac|macbook)")
APPLE_CAPTURE_KEYS = {
    "LocationAccuracyHorizontal", "ContentIdentifier", "LivePhotoAuto", "LivePhotoVitalityScore",
    "LivePhotoVitalityScoringVersion", "CameraLensModel", "CameraFocalLength35mmEquivalent",
    "FullFrameRatePlaybackIntent", "CinematicVideo", "SpatialOverCaptureGroupIdentifier",
}


class AppleDetector(DeviceDetector):
    code = "APPLE"
    display_name = "Apple"
    make_pattern = APPLE_MAKE

    def signals(self, ctx: DetectionContext) -> list[Signal]:
        s: list[Signal] = []
        apple_make = False
        for q, v in ctx.makes:
            if APPLE_MAKE.match(v):
                s.append(Signal(0.90, f"{q} = {v}"))
                apple_make = True
        for q, v in ctx.models:
            if IPHONE.match(v) or IPAD.match(v) or IPOD.match(v):
                s.append(Signal(0.90, f"{q} = {v}"))
            elif MAC.match(v):
                s.append(Signal(0.60, f"{q} = {v}"))
        mn = ctx.record.get("MakerNotes:MakerNoteType")
        if mn == "Apple iOS" or "Apple" in ctx.record.groups:
            s.append(Signal(0.85, "Apple maker notes present"))
        for q, v in ctx.record.match("*:LensModel") + ctx.record.match("*:LensMake"):
            if isinstance(v, str) and (IPHONE.search(v) or IPAD.search(v) or "iphone" in v.lower()):
                s.append(Signal(0.80, f"{q} = {v}"))
                break
        keys = set(ctx.record.groups.get("Keys", {}))
        found = sorted(keys & APPLE_CAPTURE_KEYS)
        if found:
            s.append(Signal(0.35, "Apple camera-capture keys present (" + ", ".join(f"Keys:{k}" for k in found[:3]) + ")"))
        if apple_make:
            for q, v in ctx.software:
                if re.fullmatch(r"\d+(\.\d+){0,3}( \(\w+\))?", v):
                    s.append(Signal(0.10, f"{q} = {v} (iOS version)"))
                    break
        if s:
            for q, v in ctx.handlers:
                if v.lower().startswith("core media"):
                    s.append(Signal(0.05, f"{q} = {v} (Apple media framework)"))
                    break
        return s

    def classify(self, ctx: DetectionContext) -> tuple[str, str]:
        model = ctx.first_model() or ""
        lens = " ".join(str(v) for _q, v in ctx.record.match("*:LensModel"))
        if IPHONE.match(model) or "iphone" in lens.lower():
            return self.code, "Apple iPhone"
        if IPAD.match(model) or "ipad" in lens.lower():
            return self.code, "Apple iPad"
        return self.code, "Apple device"

    def model_for(self, ctx: DetectionContext) -> str | None:
        for _q, v in ctx.models:
            if IPHONE.match(v) or IPAD.match(v) or IPOD.match(v) or MAC.match(v):
                return v
        return ctx.first_model()
