"""
Download platform-specific binary wheels into vendor/wheels/.

Fusion 360 embeds CPython 3.14 (as of Feb 2026) and runs on Windows and macOS only.
Run from the repository root whenever you want to update the bundled libraries:

    python -m fusion2mujoco.bundled_packages.download

Requirements: pip must be available in the environment running this command.
"""

import os
import shutil
import sys
import tempfile
import subprocess

from .config import BUNDLED_PACKAGES, FUSION_PYTHON_VERSION, WHEELS_DIR


def download_package(package: str, pip_platform: str, dest_dir: str) -> list[str]:
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
        f"--python-version={FUSION_PYTHON_VERSION}",
        "-d",
        dest_dir,
        "--quiet",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(f"Could not download '{package}' — {result.stderr.strip()}")
    return [
        os.path.join(dest_dir, f) for f in os.listdir(dest_dir) if f.endswith(".whl")
    ]


def download_wheels(wheels_dir: str) -> None:
    """Download every wheel declared in BUNDLED_PACKAGES."""
    for bundled in BUNDLED_PACKAGES:
        for platform_tag in bundled.platform_tags:
            print(f"=== {bundled.pip_spec()} ({platform_tag}) ===")
            with tempfile.TemporaryDirectory() as tmpdir:
                wheels = download_package(
                    bundled.pip_spec(),
                    platform_tag,
                    tmpdir,
                )
                for wheel_path in wheels:
                    dest = os.path.join(wheels_dir, os.path.basename(wheel_path))
                    shutil.move(wheel_path, dest)
                    print(f"    saved       {os.path.basename(dest)}")
            print()


def main() -> None:
    print(f"Downloading wheels into: {WHEELS_DIR}\n")

    if os.path.isdir(WHEELS_DIR):
        shutil.rmtree(WHEELS_DIR)
    os.makedirs(WHEELS_DIR, exist_ok=True)

    download_wheels(WHEELS_DIR)

    print("Done. Commit vendor/wheels/ alongside your AddIn.")


if __name__ == "__main__":
    main()
