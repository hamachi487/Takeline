"""Device/source detection.

To support a new manufacturer, add a DeviceDetector subclass (see cameras.py
for compact examples) and append it in default_detectors() below.
"""

from .apple import AppleDetector
from .base import DetectionContext, DetectionResult, DeviceDetector, Signal
from .cameras import CanonDetector, DJIDetector, GoProDetector, SonyDetector
from .rayban import RayBanDetector


def default_detectors() -> list[DeviceDetector]:
    return [RayBanDetector(), AppleDetector(), SonyDetector(), CanonDetector(),
            DJIDetector(), GoProDetector()]


# Devices offered in the manual-override menu: (code, display name)
DEVICE_CHOICES = [
    ("RAYBAN", "Ray-Ban Meta"), ("APPLE", "Apple"), ("SONY", "Sony"), ("CANON", "Canon"),
    ("DJI", "DJI"), ("GOPRO", "GoPro"), ("UNKNOWN", "Unknown"),
]

from .engine import DetectionEngine, GenericDetector, normalize_make  # noqa: E402

__all__ = ["DetectionEngine", "DetectionResult", "DetectionContext", "DeviceDetector", "Signal",
           "RayBanDetector", "AppleDetector", "SonyDetector", "CanonDetector", "DJIDetector",
           "GoProDetector", "GenericDetector", "DEVICE_CHOICES", "default_detectors",
           "normalize_make"]
