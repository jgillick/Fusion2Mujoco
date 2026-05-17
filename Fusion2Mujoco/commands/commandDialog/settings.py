#
# Values that will be retained between command sessions
#
import json
import os

SETTINGS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "settings.json"
)

DEFAULT_SETTINGS = {
    "use_short_names": True,
    "mesh_resolution": "Low",
    "should_convexify": False,
    "convex_threshold": 0.2,
    "with_environment": True,
    "with_colors": True,
}


def load_settings() -> dict:
    """
    Loads the settings from the settings file
    """
    try:
        with open(SETTINGS_FILE, "r") as f:
            data = json.load(f)
        return {**DEFAULT_SETTINGS, **data}
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict):
    """
    Saves the settings to the settings file
    """
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)
    except Exception:
        pass
