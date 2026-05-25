"""Pinned third-party wheels bundled with the Fusion 360 add-in."""

from .config import (
    ADDON_ROOT,
    BUNDLED_PACKAGES,
    BundledPackage,
    FUSION_PYTHON_VERSION,
    VENDOR_DIR,
    WHEELS_DIR,
)
from .vendor import add_vendor_path

__all__ = [
    "ADDON_ROOT",
    "BUNDLED_PACKAGES",
    "BundledPackage",
    "FUSION_PYTHON_VERSION",
    "VENDOR_DIR",
    "WHEELS_DIR",
    "add_vendor_path",
]
