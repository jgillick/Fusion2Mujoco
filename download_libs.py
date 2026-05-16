#!/usr/bin/env python3
"""
Downloads and extracts platform-specific binary wheels into Fusion2Mujoco/vendor/.

Fusion 360 embeds CPython 3.14 (as of Feb 2026) and runs on Windows and macOS only.
Run this script from the repository root whenever you want to update the bundled libraries:

    python download_libs.py

Requirements: pip must be available in the environment running this script.
"""

import os
import sys
import zipfile
import tempfile
import subprocess

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
VENDOR_DIR = os.path.join(REPO_ROOT, "Fusion2Mujoco", "vendor")

# Target Python version matching Fusion 360's embedded interpreter.
FUSION_PYTHON_VERSION = "314"

# Packages to bundle. Listed individually so failures are isolated.
# Versions are pinned to match the wheels already committed to vendor/.
PACKAGES = [
    "coacd==1.0.11",
    "numpy==2.4.4",
    "trimesh==4.11.1",
]

# Fusion 360 only runs on Windows and macOS (no Linux desktop client).
# Intel Mac (macosx_x86_64) is omitted because CoACD dropped that wheel after 1.0.7.
PLATFORMS = [
    {
        "name": "none",
        "pip_platform": "none-any",
    },
    {
        "name": "win_amd64",
        "pip_platform": "win_amd64",
    },
    {
        "name": "macosx_arm64",
        "pip_platform": "macosx_11_0_arm64",
    },
]


def download_package(
    package: str, pip_platform: str, python_version: str, dest_dir: str
) -> list[str]:
    """Download a single package wheel for the given platform into dest_dir."""
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "download",
        package,
        "--only-binary=:all:",
        "--no-deps",
        "--implementation=cp",
        f"--platform={pip_platform}",
        f"--python-version={python_version}",
        "-d",
        dest_dir,
        "--quiet",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"    WARNING: could not download '{package}' — {result.stderr.strip()}")
        return []
    return [
        os.path.join(dest_dir, f) for f in os.listdir(dest_dir) if f.endswith(".whl")
    ]


def extract_wheel(wheel_path: str, dest_dir: str) -> None:
    """Extract a wheel into dest_dir, skipping .dist-info metadata."""
    with zipfile.ZipFile(wheel_path, "r") as zf:
        members = [m for m in zf.namelist() if ".dist-info/" not in m]
        zf.extractall(dest_dir, members=members)
    print(f"    extracted  {os.path.basename(wheel_path)}")


def process_platform(plat: dict) -> None:
    plat_dir = os.path.join(VENDOR_DIR, plat["name"])
    print(f"  output dir: {plat_dir}")

    # Wipe stale files so old versions don't linger alongside new ones.
    if os.path.isdir(plat_dir):
        import shutil

        shutil.rmtree(plat_dir)
    os.makedirs(plat_dir, exist_ok=True)

    for package in PACKAGES:
        print(f"  downloading {package} ...")
        with tempfile.TemporaryDirectory() as tmpdir:
            wheels = download_package(
                package, plat["pip_platform"], FUSION_PYTHON_VERSION, tmpdir
            )
            if not wheels:
                continue
            for wheel_path in wheels:
                extract_wheel(wheel_path, plat_dir)


def main() -> None:
    print(f"Bundling libraries into: {VENDOR_DIR}\n")
    for plat in PLATFORMS:
        print(f"=== {plat['name']} ===")
        process_platform(plat)
        print()
    print("Done. Commit the vendor/ directory alongside your AddIn.")


if __name__ == "__main__":
    main()
