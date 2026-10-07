"""File operations (the only modules that modify the disk) and reporting."""

from .executor import (OpResult, copy_batch, latest_manifest, rename_batch, undo_manifest)

__all__ = ["OpResult", "copy_batch", "rename_batch", "undo_manifest", "latest_manifest"]
