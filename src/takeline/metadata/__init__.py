"""Metadata extraction layer (native parsers + optional bundled ExifTool)."""

from .extractor import (IMAGE_EXTENSIONS, SUPPORTED_EXTENSIONS, VIDEO_EXTENSIONS,
                        MetadataExtractor)
from .record import MetadataRecord

__all__ = ["MetadataExtractor", "MetadataRecord", "SUPPORTED_EXTENSIONS",
           "VIDEO_EXTENSIONS", "IMAGE_EXTENSIONS"]
