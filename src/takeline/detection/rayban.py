"""Ray-Ban Meta smart glasses detector.

Tolerant by design: it does not depend on one exact Make/Model string, so new
glasses generations ("Ray-Ban Meta Smart Glasses 2", "Meta Ray-Ban Display",
...) and renamed companion apps ("Meta View" -> "Meta AI") are still
recognized. Every signal is reported so the preview explains the decision.

Evidence (strongest first):
  Make / Model mentioning Ray-Ban                      0.90
  Make naming Meta ("Meta AI", "Meta", "Meta Platforms") 0.85
  Model naming Meta smart glasses                       0.80
  Model mentioning "smart glasses" (brand not stated)   0.50
  Software/encoder naming Meta View / Meta AI app        0.45
  Ray-Ban/Meta mentioned in any other metadata field     0.30-0.40
  Filename in the Meta app export style (weak hint)      0.25  <- never enough alone

Oakley Meta glasses share Meta's metadata conventions; they are reported as
OAKLEY rather than mislabelled as Ray-Ban.
"""

from __future__ import annotations

import re

from .base import DetectionContext, DeviceDetector, Signal, rx

RAYBAN = rx(r"ray[\s\-_]?ban")
META_MAKE = rx(r"^\s*meta(\s+(ai|platforms(,?\s*inc\.?)?|reality\s+labs|view))?\s*$")
META_WORD = rx(r"\bmeta\b")
GLASSES = rx(r"glasses|stories|wayfarer|skyler|headliner")
SMART_GLASSES = rx(r"smart\s*glasses")
META_APP = rx(r"\bmeta\s*(view|ai)\b|facebook\s*view|ray[\s\-_]?ban")
OAKLEY = rx(r"oakley")
EXPORT_NAME = rx(r"^(video|photo)-\d+_[a-z_]+\.(mov|mp4|jpe?g|heic)$")
META_NAMESPACE = rx(r"^(com\.)?(meta|facebook|oculus|rayban|ray-ban|luxottica)\.")


class RayBanDetector(DeviceDetector):
    code = "RAYBAN"
    display_name = "Ray-Ban Meta"
    make_pattern = rx(r"ray[\s\-_]?ban|\bmeta\b|luxottica|oakley")

    def signals(self, ctx: DetectionContext) -> list[Signal]:
        s: list[Signal] = []
        for q, v in ctx.makes:
            if RAYBAN.search(v):
                s.append(Signal(0.90, f"{q} = {v}"))
            elif META_MAKE.match(v) or "meta ai" in v.lower():
                s.append(Signal(0.85, f"{q} = {v}"))
            elif re.search(r"luxottica", v, re.I):
                s.append(Signal(0.70, f"{q} = {v}"))
        for q, v in ctx.models:
            if RAYBAN.search(v):
                s.append(Signal(0.90, f"{q} = {v}"))
            elif OAKLEY.search(v) and META_WORD.search(v):
                s.append(Signal(0.90, f"{q} = {v}"))
            elif META_WORD.search(v) and GLASSES.search(v):
                s.append(Signal(0.80, f"{q} = {v}"))
            elif SMART_GLASSES.search(v):
                s.append(Signal(0.50, f"{q} = {v} (smart glasses, brand not stated)"))
        for q, v in ctx.software + ctx.handlers:
            if META_APP.search(v):
                s.append(Signal(0.45, f"{q} = {v}"))
        for q, v in ctx.texts + ctx.other_strings:
            if RAYBAN.search(v):
                s.append(Signal(0.40, f"{q} mentions Ray-Ban"))
            elif META_APP.search(v) or SMART_GLASSES.search(v):
                s.append(Signal(0.30, f"{q} mentions Meta smart glasses"))
        for g, t, _v in ctx.record.items():
            if g == "Keys" and META_NAMESPACE.match(t):
                s.append(Signal(0.50, f"Keys contain a Meta namespace key ({t})"))
                break
        if EXPORT_NAME.match(ctx.filename):
            s.append(Signal(0.25, "Filename follows the Meta app export style (weak hint, never used alone)"))
        if s and any(x.weight >= 0.8 for x in s) and ctx.record.has("Keys:CreationDate"):
            s.append(Signal(0.0, "Keys:CreationDate found"))
        return s

    def classify(self, ctx: DetectionContext) -> tuple[str, str]:
        model = ctx.first_model() or ""
        if OAKLEY.search(model) and not RAYBAN.search(model):
            return "OAKLEY", "Oakley Meta"
        return self.code, self.display_name

    def model_for(self, ctx: DetectionContext) -> str | None:
        for _q, v in ctx.models:
            if RAYBAN.search(v) or META_WORD.search(v) or SMART_GLASSES.search(v) or OAKLEY.search(v):
                return v
        return ctx.first_model()
