"""Paths, Python version, and pinned wheel definitions for the add-in."""

import os
from dataclasses import dataclass
from typing import Literal

# Target Python version matching Fusion 360's embedded interpreter.
FUSION_PYTHON_VERSION = "314"

# Add-in root: config.py -> bundled_packages/ -> fusion2mujoco/ -> <add-in root>
ADDON_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
VENDOR_DIR = os.path.join(ADDON_ROOT, "vendor")
WHEELS_DIR = os.path.join(VENDOR_DIR, "wheels")

ExtractTarget = Literal["universal", "native"]


@dataclass(frozen=True)
class BundledPackage:
    """A pinned wheel to download; extracted to vendor/ on first add-in launch."""

    name: str  # distribution name, e.g. "numpy"
    version: str  # pinned version, e.g. "2.4.4"
    platform_tags: tuple[
        str, ...
    ]  # pip --platform values, e.g. ("win_amd64", "none-any")
    extract_target: (
        ExtractTarget  # "universal" -> vendor/none/, "native" -> vendor/<platform>/
    )

    def pip_spec(self) -> str:
        return f"{self.name}=={self.version}"

    def wheel_prefix(self) -> str:
        """Normalized distribution prefix as it appears at the start of wheel filenames."""
        name = self.name.strip().lower().replace("-", "_")
        return f"{name}-{self.version}"


# Fusion 360 only runs on Windows and macOS (no Linux desktop client).
# CoACD: win_amd64 + macOS arm64 only (no win_arm64 wheel; collision UI hidden on Windows ARM).
# Intel Mac (macosx_x86_64) is omitted because CoACD dropped that wheel after 1.0.7.
BUNDLED_PACKAGES: tuple[BundledPackage, ...] = (
    BundledPackage(
        "coacd",
        "1.0.11",
        ("win_amd64", "macosx_11_0_arm64"),
        "native",
    ),
    BundledPackage(
        "numpy",
        "2.4.4",
        ("win_amd64", "macosx_11_0_arm64"),
        "native",
    ),
    BundledPackage("trimesh", "4.11.1", ("none-any",), "universal"),
)
