"""
Collision tab of the Export to Mujoco command dialog.
"""

from __future__ import annotations

import sys
import platform
import adsk.core
import adsk.fusion

from ..settings import load_settings, merge_settings
from ..logger import Logger
from .config import THRESHOLD_MAX, THRESHOLD_MIN
from .table import CollisionTable


class CollisionInputs:
    """Collision tab of the export dialog."""

    def __init__(
        self,
        inputs: adsk.core.CommandInputs,
        design: adsk.fusion.Design,
        logger: Logger | None = None,
    ):
        self.inputs = inputs
        self.design = design
        self.settings: dict = {}
        self.logger = logger

        self.convexify_input: adsk.core.BoolValueCommandInput | None = None
        self.threshold_input: adsk.core.FloatSpinnerCommandInput | None = None
        self.table: CollisionTable | None = None

        # CoACD is not supported on Windows on ARM processors yet
        self.is_supported = not (
            sys.platform == "win32"
            and platform.machine().lower()
            in (
                "arm64",
                "aarch64",
            )
        )

        self.load()

    @property
    def is_convexify_enabled(self) -> bool:
        if not self.is_supported:
            return False
        return bool(self.convexify_input and self.convexify_input.value)

    @property
    def should_convexify(self) -> bool:
        if not self.is_supported:
            return False
        return self.convexify_input.value

    @property
    def convex_threshold(self) -> float | None:
        if not self.should_convexify:
            return None
        return self.threshold_input.value

    @property
    def component_collision_settings(self) -> dict[str, float] | None:
        if not self.should_convexify:
            return None
        return self.table.read_component_collision_settings()

    def build(self) -> None:
        """Build the collision inputs."""

        # If CoACD is not supported on this platform, build the unsupported tab.
        if not self.is_supported:
            textbox = self.inputs.addTextBoxCommandInput(
                "coacd_unsupported",
                "",
                """
                    <div align='center'><i>
                        <a href='https://github.com/SarahWeiii/CoACD'>CoACD</a>
                        doesn't support Windows on ARM processors yet
                    </i></div>
                """,
                2,
                True,
            )
            textbox.isFullWidth = True
            return None

        # Enable collision mesh creation
        self.convexify_input = self.inputs.addBoolValueInput(
            "should_convexify",
            "Generate collision meshes",
            True,
            "",
            self.settings["should_convexify"],
        )
        self.convexify_input.tooltip = (
            "Uses CoACD to create collision meshes for the visual body meshes"
        )
        self.convexify_input.tooltipDescription = (
            "This will make your simulations more stable and accurate, but takes a lot "
            "longer to create. Depending on how low you set the threshold, each body "
            "can take several minutes to export. https://github.com/SarahWeiii/CoACD"
        )

        # Set the CoACD threshold
        self.threshold_input = self.inputs.addFloatSpinnerCommandInput(
            "convex_threshold",
            "Concavity threshold",
            "",
            THRESHOLD_MIN,
            THRESHOLD_MAX,
            0.05,
            self.settings["convex_threshold"],
        )
        self.threshold_input.isEnabled = self.settings["should_convexify"]
        self.threshold_input.tooltip = "The threshold for the CoACD algorithm"
        self.threshold_input.tooltipDescription = (
            "A lower number means more detailed collision meshes, but takes longer "
            "to create. https://github.com/SarahWeiii/CoACD"
        )

        # Add per component settings
        self.table = CollisionTable(
            parent_inputs=self.inputs,
            design=self.design,
            settings=self.settings,
            logger=self.logger,
        )
        self.table.build()

    def load(self) -> None:
        """Load the input values from the last session."""
        self.settings = load_settings()

    def save(self) -> None:
        """Retain the input values for the next session."""
        merge_settings(
            {
                "should_convexify": self.should_convexify,
                "convex_threshold": self.convex_threshold,
            }
        )
        self.table.save()

    def set_enabled(self, enabled: bool) -> None:
        """Set the fields to enabled or disabled."""
        self.threshold_input.isEnabled = self.convexify_input.value
        self.table.set_enabled(enabled)

    def handle_input_changed(self, input: adsk.core.CommandInput) -> None:
        if input.id == self.convexify_input.id:
            self.set_enabled(self.convexify_input.value)

        if self.table:
            self.table.handle_input_changed(
                input,
                global_threshold=self.threshold_input.value,
            )

    def validate(self) -> bool:
        if self.threshold_input.isEnabled:
            if not (THRESHOLD_MIN <= self.threshold_input.value <= THRESHOLD_MAX):
                return False
        return self.table.validate()
