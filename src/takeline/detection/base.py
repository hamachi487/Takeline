"""Device detection interface.

Each detector inspects a MetadataRecord and returns weighted evidence
("signals"). Signals are combined with a noisy-OR, so two independent strong
signals (Make + Model) give a higher confidence than either alone, and many
weak hints can never add up to certainty on their own beyond what their
weights allow.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..metadata.record import MetadataRecord

MAKE_TAGS = {"Make", "AndroidManufacturer", "DeviceManufacturer", "Manufacturer"}
MODEL_TAGS = {"Model", "AndroidModel", "DeviceModelName", "UniqueCameraModel",
              "CameraModelName", "ModelName"}
SOFTWARE_TAGS = {"Software", "SoftwareVersion", "Encoder", "CreatorTool", "WritingApp",
                 "MuxingApp", "FirmwareVersion", "CompressorVersion", "OriginatingProgram",
                 "HostComputer", "EncodedBy", "AndroidVersion"}
TEXT_TAGS = {"Comment", "Description", "Title", "UserComment", "ImageDescription",
             "Information", "LongDescription", "Caption-Abstract", "XPComment", "Name"}
NON_CONTENT_GROUPS = {"File", "System"}

_SUFFIX = re.compile(r" \(\d+\)$")


@dataclass
class Signal:
    weight: float
    reason: str


@dataclass
class DetectionResult:
    device: str                    # code used in filenames, e.g. "RAYBAN"
    display_name: str              # e.g. "Ray-Ban Meta"
    model: str | None
    confidence: float              # 0..1
    reasons: list[str]
    detector: str = ""
    alternatives: list[tuple[str, float]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "device": self.device, "display_name": self.display_name, "model": self.model,
            "confidence": round(self.confidence, 3), "reasons": list(self.reasons),
            "detector": self.detector,
            "alternatives": [{"device": d, "confidence": round(c, 3)} for d, c in self.alternatives],
        }


class DetectionContext:
    """Convenience views over a record that every detector needs."""

    def __init__(self, record: MetadataRecord):
        self.record = record
        self.path = Path(record.path)
        self.filename = self.path.name
        self.makes = self._collect(MAKE_TAGS)
        self.models = self._collect(MODEL_TAGS)
        self.software = self._collect(SOFTWARE_TAGS)
        self.texts = self._collect(TEXT_TAGS)
        self.handlers = self._collect({"HandlerDescription", "CompressorName"})
        known = MAKE_TAGS | MODEL_TAGS | SOFTWARE_TAGS | TEXT_TAGS | {"HandlerDescription", "CompressorName"}
        self.other_strings = [
            (f"{g}:{t}", v) for g, t, v in record.items()
            if isinstance(v, str) and g not in NON_CONTENT_GROUPS
            and _SUFFIX.sub("", t) not in known and not v.startswith("<")
        ]

    def _collect(self, names: set[str]) -> list[tuple[str, str]]:
        out = []
        for g, t, v in self.record.items():
            if g in NON_CONTENT_GROUPS or not isinstance(v, str):
                continue
            if _SUFFIX.sub("", t) in names and v.strip():
                out.append((f"{g}:{t}", v.strip()))
        return out

    def first_model(self) -> str | None:
        return self.models[0][1] if self.models else None

    def has_group(self, prefix: str) -> bool:
        return any(g == prefix or g.startswith(prefix) for g in self.record.groups)

    def foreign_make(self, own: re.Pattern) -> str | None:
        """A Make that names some other manufacturer (evidence against this detector)."""
        for _q, v in self.makes:
            if not own.search(v):
                return v
        return None


class DeviceDetector(ABC):
    code: str = "UNKNOWN"
    display_name: str = "Unknown"
    #: Pattern matching this manufacturer's own Make values. A Make naming a
    #: different manufacturer caps confidence unless strong evidence exists.
    make_pattern: re.Pattern | None = None
    #: Signals at or above this weight count as "strong" (Make/Model level).
    strong: float = 0.8

    @abstractmethod
    def signals(self, ctx: DetectionContext) -> list[Signal]:
        ...

    def model_for(self, ctx: DetectionContext) -> str | None:
        return ctx.first_model()

    def classify(self, ctx: DetectionContext) -> tuple[str, str]:
        """Return (code, display_name); detectors can refine per model."""
        return self.code, self.display_name

    def detect(self, ctx: DetectionContext) -> DetectionResult | None:
        sigs = self.signals(ctx)
        if not sigs:
            return None
        conf = combine(s.weight for s in sigs)
        code, name = self.classify(ctx)
        reasons = []
        for s in sorted(sigs, key=lambda s: -s.weight):
            if s.reason not in reasons:
                reasons.append(s.reason)
        if self.make_pattern is not None and not any(s.weight >= self.strong for s in sigs):
            foreign = ctx.foreign_make(self.make_pattern)
            if foreign:
                conf = min(conf, 0.35)
                reasons.append(f"Make = {foreign} names a different manufacturer")
        return DetectionResult(device=code, display_name=name, model=self.model_for(ctx),
                               confidence=conf, reasons=reasons, detector=type(self).__name__)


def combine(weights) -> float:
    """Noisy-OR combination, capped at 0.99: metadata can be edited, so never claim certainty."""
    p = 1.0
    for w in weights:
        w = max(0.0, min(0.995, w))
        p *= 1.0 - w
    return round(min(0.99, 1.0 - p), 4)


def rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)
