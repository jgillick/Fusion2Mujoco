"""
General tab of the Export to Mujoco command dialog.
"""

from __future__ import annotations

import os
import re
import tempfile
import traceback
import webbrowser
from pathlib import Path
import adsk.core
import adsk.fusion

from ...fusion2mujoco.exporter import Exporter
from .constants import ATTR_GROUP_NAMESPACE, ATTR_EXPORT_NAME
from .settings import load_settings, merge_settings

INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')

# Joint graph previews are scratch files, so they go to a fixed folder under
# the system temp directory rather than anywhere the user keeps work.
PREVIEW_DIR_NAME = "Fusion2Mujoco"


class GeneralInputs:
    """General export options on the export dialog."""

    def __init__(
        self,
        inputs: adsk.core.CommandInputs,
        design: adsk.fusion.Design,
    ):
        self.design = design
        self.inputs = inputs
        self.settings: dict = {}
        self.default_name: str = "Model"
        self.model_name_input: adsk.core.StringValueCommandInput | None = None
        self.with_environment_input: adsk.core.BoolValueCommandInput | None = None
        self.with_colors_input: adsk.core.BoolValueCommandInput | None = None
        self.use_short_names_input: adsk.core.BoolValueCommandInput | None = None
        self.mesh_resolution_input: adsk.core.DropDownCommandInput | None = None
        self.joint_graph_input: adsk.core.BoolValueCommandInput | None = None
        self.load()

    @property
    def model_name(self) -> str:
        return self.model_name_input.value.strip()

    @property
    def with_environment(self) -> bool:
        return self.with_environment_input.value

    @property
    def with_colors(self) -> bool:
        return self.with_colors_input.value

    @property
    def use_short_names(self) -> bool:
        return self.use_short_names_input.value

    @property
    def mesh_resolution(self) -> str:
        return self.mesh_resolution_input.selectedItem.name

    def build(self) -> None:
        self.model_name_input = self.inputs.addStringValueInput(
            "model_name", "Name", self.default_name
        )
        self.model_name_input.tooltip = "Name of the exported model"
        self.model_name_input.tooltipDescription = (
            "Used as the output folder name and MJCF model name. Cannot be blank "
            "or contain characters invalid in file names."
        )

        self.with_environment_input = self.inputs.addBoolValueInput(
            "with_environment",
            "Ground plane",
            True,
            "",
            self.settings["with_environment"],
        )
        self.with_environment_input.tooltip = (
            "Include an enviroment (ground plane, light, etc) around the exported model"
        )
        self.with_environment_input.tooltipDescription = """
            If unchecked, the model is exported without an additional environment.

            This is useful if the model will be imported into simulation environments.
        """

        self.with_colors_input = self.inputs.addBoolValueInput(
            "with_colors",
            "Include colors",
            True,
            "",
            self.settings["with_colors"],
        )
        self.with_colors_input.tooltip = (
            "Export component appearance colors and materials"
        )
        self.with_colors_input.tooltipDescription = """
            Reads the appearance (color, roughness, metalness) assigned to each
            component in Fusion 360 and writes it into the MuJoCo XML.

            This does not include textures/patterns.
        """

        self.use_short_names_input = self.inputs.addBoolValueInput(
            "use_short_names",
            "Short names",
            True,
            "",
            self.settings["use_short_names"],
        )
        self.use_short_names_input.tooltip = (
            "Shorten body names by removing redundant path segments"
        )
        self.use_short_names_input.tooltipDescription = """
            Instead of using the full assembly path as the name for each body, this option
            drops path segments that are identical across all instances, keeping only the
            segments needed to uniquely identify each one.
        """

        self.mesh_resolution_input = self.inputs.addDropDownCommandInput(
            "mesh_resolution",
            "Mesh resolution",
            adsk.core.DropDownStyles.TextListDropDownStyle,
        )
        for level in ("Low", "Medium", "High"):
            self.mesh_resolution_input.listItems.add(
                level, level == self.settings["mesh_resolution"]
            )

        self.mesh_resolution_input.tooltip = (
            "Mesh resolution used when exporting visual STL files"
        )
        self.mesh_resolution_input.tooltipDescription = """
            Low  — fastest export, coarser geometry (default).
            Medium — balanced quality and speed.
            High — finest geometry, longest export time.
        """

        # isCheckBox=False renders this as a button rather than a checkbox,
        # so clicking it is the action itself instead of an export option.
        # An empty resource folder gives a plain text button; passing an icon
        # folder here would make it an image button instead.
        #
        # "name" is the label in the dialog's left-hand column and "text" is
        # the caption on the button itself -- they are separate properties, so
        # the row reads "Debug  [Preview joint graph]".
        self.joint_graph_input = self.inputs.addBoolValueInput(
            "preview_joint_graph",
            "Debug",
            False,
            "",
            False,
        )
        self.joint_graph_input.text = "Preview joint graph"
        self.joint_graph_input.tooltip = "Draw the model's joint graph now"
        self.joint_graph_input.tooltipDescription = """
            Builds the kinematic tree from the model's joints and opens a diagram
            of it in your browser, without exporting anything.

            The diagram shows which body each joint attaches to, which bodies are
            attached to the world, and any joints that would be dropped — useful
            when a model comes out in pieces or a part goes missing.

            Mermaid and Graphviz copies are written alongside it, and the same
            files are written next to the MJCF whenever an export fails because
            the joint graph is invalid.
        """

    def handle_input_changed(self, input: adsk.core.CommandInput) -> None:
        """Handle input changes for the general inputs."""
        if input.id == self.joint_graph_input.id:
            # A button input latches its value like a checkbox, so it has to
            # be cleared or it stays pressed. Clearing it fires this handler
            # a second time, which is the call being ignored here -- without
            # the guard the preview would run twice per click.
            if not self.joint_graph_input.value:
                return
            self.joint_graph_input.value = False
            self.preview_joint_graph()
            return

        if input.id == self.model_name_input.id:
            # Sanitize the model name
            cleaned = self.sanitize_name(input.value)
            if cleaned != input.value:
                input.value = cleaned
            self.model_name_input.isValid = True

    def validate(self) -> bool:
        """Validate the general inputs."""
        name_val = self.model_name_input.value.strip()
        if not name_val or INVALID_FILENAME_CHARS.search(name_val):
            self.model_name_input.isValid = False
            return False
        return True

    def sanitize_name(self, name: str) -> str:
        """
        Sanitize the model name to only valid characters for file names.
        """
        return INVALID_FILENAME_CHARS.sub("", name).strip()

    def preview_joint_graph(self) -> None:
        """
        Build the joint graph for the current design and open it in the
        user's browser.

        Runs while the dialog is still open, so it reads the name and
        short-name settings as they are now rather than as they were saved.
        Nothing is exported and no mesh is written; failures are reported in
        a message box instead of propagating, since this is a debug aid and
        must not take the dialog down with it.
        """
        ui = adsk.core.Application.get().userInterface
        try:
            name = self.model_name or self.default_name
            exporter = Exporter(
                name=name,
                use_short_names=self.use_short_names,
                with_environment=self.with_environment,
                with_colors=self.with_colors,
            )
            destination = os.path.join(
                tempfile.gettempdir(), PREVIEW_DIR_NAME, self.sanitize_name(name)
            )
            written = exporter.generate_joint_graph(destination)
        except Exception:
            ui.messageBox(
                "Could not build the joint graph:\n\n{}".format(traceback.format_exc()),
                "Fusion2Mujoco",
            )
            return

        if not written:
            ui.messageBox(
                "The joint graph could not be written to:\n{}".format(destination),
                "Fusion2Mujoco",
            )
            return

        # write() puts the HTML first; that is the one worth opening.
        # as_uri() percent-encodes spaces and gives Windows paths the
        # file:///C:/... form that a plain "file://" + path does not.
        webbrowser.open(Path(written[0]).as_uri())

    def load(self) -> None:
        """Load the input values from the last session."""
        self.settings = load_settings()

        # Load the model name from the design attributes
        attr = self.design.attributes.itemByName(ATTR_GROUP_NAMESPACE, ATTR_EXPORT_NAME)
        if attr and attr.value.strip():
            self.default_name = attr.value.strip()
        else:
            name = self.sanitize_name(self.design.rootComponent.name)
            if name:
                self.default_name = name

    def save(self) -> None:
        """Retain the input values for the next session."""

        # Global settings
        merge_settings(
            {
                "with_environment": self.with_environment,
                "with_colors": self.with_colors,
                "use_short_names": self.use_short_names,
                "mesh_resolution": self.mesh_resolution,
            }
        )

        # Save the model name to the design attributes
        # Note: If the name is the same as the component name, do not save
        component_name = self.sanitize_name(self.design.rootComponent.name)
        if self.model_name == component_name:
            self.design.attributes.add(ATTR_GROUP_NAMESPACE, ATTR_EXPORT_NAME, "")
        else:
            self.design.attributes.add(
                ATTR_GROUP_NAMESPACE, ATTR_EXPORT_NAME, self.model_name
            )
