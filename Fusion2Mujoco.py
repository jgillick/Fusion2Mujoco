import os

from .fusion2mujoco.bundled_packages import add_vendor_path

add_vendor_path(os.path.dirname(os.path.abspath(__file__)))

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
