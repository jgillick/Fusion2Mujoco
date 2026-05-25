"""Extract bundled wheels and prepend vendor dirs to sys.path."""

import os
import platform
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass

from .config import (
    ADDON_ROOT,
    BUNDLED_PACKAGES,
    BundledPackage,
)

SENTINEL_NAME = ".extracted"


@dataclass(frozen=True)
class _HostArchitecture:
    """Runtime host: wheel filename tags and vendor extract dir for native wheels."""

    wheel_tag_pattern: str
    native_vendor_dir: str

    def matches_wheel(self, wheel_name: str) -> bool:
        return re.search(f"-{self.wheel_tag_pattern}", wheel_name) is not None


def _get_host_architecture() -> _HostArchitecture:
    """Return the host architecture tag for the current platform."""
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return _HostArchitecture(
            wheel_tag_pattern="win_amd64", native_vendor_dir="win_amd64"
        )
    if sys.platform == "darwin":
        if machine == "arm64":
            return _HostArchitecture(
                wheel_tag_pattern="macosx_[0-9_]+_arm64",
                native_vendor_dir="macosx_arm64",
            )
        # Intel Mac: no wheels bundled (see config.py).
        return _HostArchitecture(
            wheel_tag_pattern="macosx", native_vendor_dir="macosx_x86_64"
        )
    return _HostArchitecture(
        wheel_tag_pattern=f"linux_{machine}",
        native_vendor_dir=f"linux_{machine}",
    )


def _wheel_fingerprint(wheels_dir: str) -> str:
    """Return a string that uniquely identifies the wheels in the given directory."""
    entries = sorted(
        f"{name}:{os.path.getsize(os.path.join(wheels_dir, name))}"
        for name in os.listdir(wheels_dir)
        if name.endswith(".whl")
    )
    return "\n".join(entries)


def _get_packag_for_wheel(wheel_name: str) -> BundledPackage | None:
    for bundled in BUNDLED_PACKAGES:
        if wheel_name.startswith(bundled.wheel_prefix()):
            return bundled
    return None


def _get_wheels(
    wheels_dir: str, host_arch: _HostArchitecture
) -> tuple[list[str], list[str]]:
    """Return (universal_wheels, native_wheels) to extract for this host."""
    universal_wheels: list[str] = []
    native_wheels: list[str] = []

    for wheel_name in sorted(os.listdir(wheels_dir)):
        if not wheel_name.endswith(".whl"):
            continue

        bundled = _get_packag_for_wheel(wheel_name)
        if bundled is None:
            continue

        wheel_path = os.path.join(wheels_dir, wheel_name)
        if bundled.extract_target == "universal":
            universal_wheels.append(wheel_path)
        elif host_arch.matches_wheel(wheel_name):
            native_wheels.append(wheel_path)

    return universal_wheels, native_wheels


def _extraction_is_current(
    sentinel_path: str, wheels_dir: str, extract_dirs: list[str]
) -> bool:
    """Check the .extracted file to ensure that the vendor directory is up to date."""
    if not os.path.isfile(sentinel_path):
        return False
    if not all(os.path.isdir(d) for d in extract_dirs):
        return False
    with open(sentinel_path, encoding="utf-8") as f:
        recorded = f.read().strip()
    return recorded == _wheel_fingerprint(wheels_dir)


def _extract_wheels(wheel_paths: list[str], extract_dir: str) -> None:
    """Extract wheels into the given directory."""
    if os.path.isdir(extract_dir):
        shutil.rmtree(extract_dir)
    os.makedirs(extract_dir, exist_ok=True)

    for wheel_path in wheel_paths:
        with zipfile.ZipFile(wheel_path, "r") as zf:
            members = [m for m in zf.namelist() if ".dist-info/" not in m]
            zf.extractall(extract_dir, members=members)


def _ensure_vendor_extracted(
    vendor_root: str,
    wheels_dir: str,
    to_extract: dict[str, list[str]],
) -> None:
    """Extract wheels into dest dirs when the sentinel is missing or stale."""
    if not len(to_extract):
        return

    extract_dirs = to_extract.keys()
    sentinel_path = os.path.join(vendor_root, SENTINEL_NAME)
    if _extraction_is_current(sentinel_path, wheels_dir, extract_dirs):
        return

    print(
        "Fusion2Mujoco: extracting bundled libraries (first launch, may take a few seconds)..."
    )
    for dest, wheels in to_extract.items():
        _extract_wheels(wheels, dest)

    with open(sentinel_path, "w", encoding="utf-8") as f:
        f.write(_wheel_fingerprint(wheels_dir))


def add_vendor_path(addon_dir: str | None = None) -> None:
    """Extract bundled wheels on first launch, then prepend vendor dirs to sys.path."""
    if addon_dir is None:
        addon_dir = ADDON_ROOT

    vendor_root = os.path.join(addon_dir, "vendor")
    wheels_dir = os.path.join(vendor_root, "wheels")

    if not os.path.isdir(wheels_dir):
        raise Exception(
            "vendor/wheels/ not found — bundled libraries are unavailable. "
            "From the add-in root, run: python -m fusion2mujoco.bundled_packages.download"
        )

    host_arch = _get_host_architecture()
    universal_wheels, native_wheels = _get_wheels(wheels_dir, host_arch)

    to_extract: dict[str, list[str]] = {}
    if universal_wheels:
        universal_dir = os.path.join(vendor_root, "none")
        to_extract[universal_dir] = universal_wheels
    if native_wheels:
        native_dir = os.path.join(vendor_root, host_arch.native_vendor_dir)
        to_extract[native_dir] = native_wheels
    _ensure_vendor_extracted(vendor_root, wheels_dir, to_extract)

    for extract_dir in to_extract.keys():
        if os.path.isdir(extract_dir) and extract_dir not in sys.path:
            sys.path.insert(0, extract_dir)
