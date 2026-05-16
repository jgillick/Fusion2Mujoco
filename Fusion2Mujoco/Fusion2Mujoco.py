import os
import sys
import platform


def _add_vendor_path() -> None:
    """Prepend the platform-specific vendor directory to sys.path."""
    this_dir = os.path.dirname(os.path.abspath(__file__))
    machine = platform.machine().lower()

    plats = ["none"]
    if sys.platform == "win32":
        plats.append("win_amd64")
    elif sys.platform == "darwin":
        plats.append("macosx_arm64" if machine == "arm64" else "macosx_x86_64")
    else:
        plats.append(f"linux_{machine}")

    for plat in plats:
        vendor_path = os.path.join(this_dir, "vendor", plat)
        if os.path.isdir(vendor_path) and vendor_path not in sys.path:
            sys.path.insert(0, vendor_path)


_add_vendor_path()

from . import commands
from .lib import fusionAddInUtils as futil


def run(context):
    try:
        # This will run the start function in each of your commands as defined in commands/__init__.py
        commands.start()

    except:
        futil.handle_error("run")


def stop(context):
    try:
        # Remove all of the event handlers your app has created
        futil.clear_handlers()

        # This will run the start function in each of your commands as defined in commands/__init__.py
        commands.stop()

    except:
        futil.handle_error("stop")
