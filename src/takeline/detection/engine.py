"""Detection engine: runs every registered detector and picks the best match.

Specific detectors (Ray-Ban, Apple, Sony, ...) run first. The generic
detector is consulted only when none of them is confident, and it labels the
file from its own Make field (SAMSUNG, GOOGLE, NIKON, ...). If even that is
missing, the result is UNKNOWN with an explanation. Nothing is guessed from
the container type or file extension.
"""

from __future__ import annotations

import re

from ..metadata.record import MetadataRecord
from .base import DetectionContext, DetectionResult, DeviceDetector, Signal, rx

DEFAULT_THRESHOLD = 0.60

MAKE_ALIASES = {
    "nikon corporation": "NIKON", "nikon": "NIKON", "olympus imaging corp.": "OLYMPUS",
    "olympus corporation": "OLYMPUS", "om digital solutions": "OMSYSTEM", "fujifilm": "FUJIFILM",
    "panasonic": "PANASONIC", "samsung": "SAMSUNG", "google": "GOOGLE", "xiaomi": "XIAOMI",
    "huawei": "HUAWEI", "oneplus": "ONEPLUS", "motorola": "MOTOROLA", "insta360": "INSTA360",
    "arashi vision": "INSTA360", "ricoh": "RICOH", "pentax": "PENTAX", "leica camera ag": "LEICA",
    "eastman kodak company": "KODAK", "hasselblad": "HASSELBLAD", "blackmagic design": "BLACKMAGIC",
}
EDIT_SOFTWARE = rx(r"lavf|ffmpeg|handbrake|premiere|after effects|final cut|imovie|capcut|davinci|"
                   r"resolve|adobe|lightroom|photoshop|quicktime|compressor|vlc|shotcut|kdenlive|"
                   r"clipchamp|movavi|filmora|lumafusion|avc encoder|x264|x265")


class GenericDetector(DeviceDetector):
    """Labels files by their declared manufacturer when no dedicated detector exists."""
    code = "GENERIC"
    display_name = "Camera"

    def signals(self, ctx: DetectionContext) -> list[Signal]:
        s = []
        for q, v in ctx.makes:
            s.append(Signal(0.80, f"{q} = {v} (manufacturer without a dedicated detector)"))
            break
        else:
            for q, v in ctx.software:
                if q.endswith("AndroidVersion"):
                    s.append(Signal(0.70, f"{q} = {v} (recorded on an Android device)"))
                    break
        return s

    def classify(self, ctx: DetectionContext) -> tuple[str, str]:
        if ctx.makes:
            make = ctx.makes[0][1]
            return normalize_make(make), make.strip().title() if make.isupper() or make.islower() else make.strip()
        return "ANDROID", "Android device"


def normalize_make(make: str) -> str:
    key = make.strip().lower()
    if key in MAKE_ALIASES:
        return MAKE_ALIASES[key]
    for alias, code in MAKE_ALIASES.items():
        if key.startswith(alias):
            return code
    word = re.sub(r"[^A-Za-z0-9]", "", key.split()[0]) if key.split() else ""
    return word.upper()[:16] or "UNKNOWN"


class DetectionEngine:
    def __init__(self, detectors: list[DeviceDetector] | None = None,
                 threshold: float = DEFAULT_THRESHOLD, generic: DeviceDetector | None = None):
        from . import default_detectors
        self.detectors = list(detectors) if detectors is not None else default_detectors()
        self.generic = generic if generic is not None else GenericDetector()
        self.threshold = threshold

    def register(self, detector: DeviceDetector, first: bool = False) -> None:
        if first:
            self.detectors.insert(0, detector)
        else:
            self.detectors.append(detector)

    def detect(self, record: MetadataRecord) -> DetectionResult:
        ctx = DetectionContext(record)
        results: list[DetectionResult] = []
        for det in self.detectors:
            try:
                r = det.detect(ctx)
            except Exception as exc:  # a broken detector must not break the batch
                record.warn(f"{type(det).__name__} failed: {exc}")
                continue
            if r is not None and r.confidence > 0:
                results.append(r)
        results.sort(key=lambda r: -r.confidence)
        confident = [r for r in results if r.confidence >= self.threshold]
        if confident:
            best = confident[0]
            best.alternatives = [(r.device, r.confidence) for r in results[1:4]]
            if len(confident) > 1 and best.confidence - confident[1].confidence < 0.10:
                other = confident[1]
                best.confidence = round(max(0.0, best.confidence - 0.15), 4)
                best.reasons.append(
                    f"Conflicting metadata: also matches {other.display_name} "
                    f"({other.confidence:.0%}); please verify")
            return best
        generic = self.generic.detect(ctx) if self.generic else None
        if generic is not None and generic.confidence >= self.threshold:
            generic.alternatives = [(r.device, r.confidence) for r in results[:3]]
            return generic
        return self._unknown(ctx, results)

    def _unknown(self, ctx: DetectionContext, results: list[DetectionResult]) -> DetectionResult:
        reasons: list[str] = []
        if not ctx.makes and not ctx.models:
            reasons.append("No manufacturer metadata found.")
        edit = [f"{q} = {v}" for q, v in ctx.software if EDIT_SOFTWARE.search(v)]
        if edit:
            reasons.append("Only editing/encoding software is recorded (" + "; ".join(edit[:2]) +
                           "); the file looks re-encoded, so the original camera is unknown.")
        for r in results:
            reasons.append(f"Weak match for {r.display_name} ({r.confidence:.0%}): " + "; ".join(r.reasons[:2]))
        if ctx.record.errors:
            reasons.append("Metadata could not be read: " + ctx.record.errors[0])
        # For UNKNOWN, confidence reports how strong the best (rejected) guess was.
        conf = round(results[0].confidence, 2) if results else 0.0
        return DetectionResult(device="UNKNOWN", display_name="Unknown", model=None,
                               confidence=conf, reasons=reasons or ["No identifying metadata found."],
                               detector="none",
                               alternatives=[(r.device, r.confidence) for r in results[:3]])
