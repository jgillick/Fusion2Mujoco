# Application Global Variables
# This module serves as a way to share variables across different
# modules (global variables).

import os
import json

# Flag that indicates to run in Debug mode or not. When running in Debug mode
# more information is written to the Text Command window. Generally, it's useful
# to set this to True while developing an add-in and set it to False when you
# are ready to distribute it.
DEBUG = True

# Gets the name of the add-in from the name of the folder the py file is in.
# This is used when defining unique internal names for various UI elements
# that need a unique name. It's also recommended to use a company name as
# part of the ID to better ensure the ID is unique.
ADDIN_NAME = os.path.basename(os.path.dirname(__file__))
COMPANY_NAME = "Jeremy Gillick"
REPO_URL = "https://github.com/jgillick/Fusion2Mujoco"


def _read_manifest_version() -> str:
    """
    Read the add-in version from the Fusion manifest next to this file.
    Falls back to "unknown" if the manifest can't be read.
    """
    manifest_path = os.path.join(os.path.dirname(__file__), "Fusion2Mujoco.manifest")
    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            return str(json.load(handle).get("version", "unknown"))
    except (OSError, ValueError):
        return "unknown"


ADDIN_VERSION = _read_manifest_version()

# Palettes
sample_palette_id = f"{COMPANY_NAME}_{ADDIN_NAME}_palette_id"
