"""Filename templates.

Default: {DEVICE}-{YYYY}-{MM}-{DD}-{HH}{MIN}{SEC}{SUFFIX}.{EXT}
         -> RAYBAN-2026-07-17-190049.MOV

Tokens:
  {DEVICE}  device code (RAYBAN, APPLE, ...)      {MODEL}    model, if known
  {YYYY} {YY} {MM} {DD} {HH} {MIN} {SEC}          {YYYYMMDD} {HHMMSS}
  {SUFFIX}  collision suffix ("" or "-01", "-02", ...)
  {EXT}     original extension                     {ORIGINAL} original name (no extension)
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

DEFAULT_TEMPLATE = "{DEVICE}-{YYYY}-{MM}-{DD}-{HH}{MIN}{SEC}{SUFFIX}.{EXT}"
DEFAULT_SUFFIX = "-{n:02d}"
TOKENS = {"DEVICE", "MODEL", "YYYY", "YY", "MM", "DD", "HH", "MIN", "SEC", "YYYYMMDD", "HHMMSS",
          "SUFFIX", "EXT", "ORIGINAL"}
EXT_CASES = ("preserve", "lower", "upper")

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1F]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MAX_NAME = 200


def sanitize_component(text: str, fallback: str = "UNKNOWN") -> str:
    text = _INVALID.sub("_", text or "").strip()
    text = re.sub(r"\s+", "-", text)
    return text.strip(".-_ ") or fallback


def sanitize_filename(name: str) -> str:
    name = _INVALID.sub("_", name).strip()
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = stem.rstrip(". ") or "file"
    if stem.split(".")[0].upper() in _RESERVED:
        stem = "_" + stem
    if len(stem) + len(ext) + 1 > MAX_NAME:
        stem = stem[:MAX_NAME - len(ext) - 1]
    return f"{stem}.{ext}" if ext else stem


@dataclass
class FilenameTemplate:
    template: str = DEFAULT_TEMPLATE
    suffix_format: str = DEFAULT_SUFFIX
    ext_case: str = "preserve"

    def __post_init__(self):
        problems = self.problems()
        if problems:
            raise ValueError("; ".join(problems))
        t = self.template
        if "{SUFFIX}" not in t:
            t = t.replace(".{EXT}", "{SUFFIX}.{EXT}") if ".{EXT}" in t else t + "{SUFFIX}"
        if "{EXT}" not in t:
            t = t + ".{EXT}"
        self._effective = t

    def problems(self) -> list[str]:
        out = []
        for tok in re.findall(r"\{([^{}]*)\}", self.template):
            if tok not in TOKENS:
                out.append(f"Unknown token {{{tok}}}")
        if not any(f"{{{t}}}" in self.template for t in ("HH", "HHMMSS", "SEC", "MIN")):
            out.append("Template must include the time ({HH}{MIN}{SEC} or {HHMMSS}) so names sort by capture time")
        if self.ext_case not in EXT_CASES:
            out.append(f"Extension case must be one of {', '.join(EXT_CASES)}")
        try:
            self.suffix_format.format(n=1)
        except (KeyError, ValueError, IndexError):
            out.append("Suffix format must use {n}, e.g. -{n:02d}")
        return out

    @property
    def effective(self) -> str:
        return self._effective

    def render(self, device: str, when: dt.datetime, ext: str, n: int = 0,
               model: str | None = None, original: str = "") -> str:
        ext = ext.lstrip(".")
        if self.ext_case == "lower":
            ext = ext.lower()
        elif self.ext_case == "upper":
            ext = ext.upper()
        values = {
            "DEVICE": sanitize_component(device),
            "MODEL": sanitize_component(model or "", "UNKNOWN-MODEL"),
            "YYYY": f"{when.year:04d}", "YY": f"{when.year % 100:02d}",
            "MM": f"{when.month:02d}", "DD": f"{when.day:02d}",
            "HH": f"{when.hour:02d}", "MIN": f"{when.minute:02d}", "SEC": f"{when.second:02d}",
            "YYYYMMDD": when.strftime("%Y%m%d"), "HHMMSS": when.strftime("%H%M%S"),
            "SUFFIX": "" if n <= 0 else self.suffix_format.format(n=n),
            "EXT": ext, "ORIGINAL": sanitize_component(original, "file"),
        }
        name = re.sub(r"\{([A-Z]+)\}", lambda m: values.get(m.group(1), m.group(0)), self._effective)
        if not ext:
            name = name.rstrip(".")
        return sanitize_filename(name)
