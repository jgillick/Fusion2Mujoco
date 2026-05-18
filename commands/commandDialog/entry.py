import adsk.core
import adsk.fusion
import os
import re
from ...lib import fusionAddInUtils as futil
from ... import config
from ...core.exporter import Exporter
from .settings import load_settings, save_settings

_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


def _sanitize_filename(name: str) -> str:
    return _INVALID_FILENAME_CHARS.sub("", name).strip()


app = adsk.core.Application.get()
ui = app.userInterface

CMD_ID = f"{config.COMPANY_NAME}_{config.ADDIN_NAME}_cmdDialog"
CMD_NAME = "Export to Mujoco"
CMD_Description = "Export your model to Mujoco XML"

# The model attribute to save the export name to
ATTR_EXPORT_NAME = "model_export_name"


# Specify that the command will be promoted to the panel.
IS_PROMOTED = True

# The location where the command button will be created. ***
# This is done by specifying the workspace, the tab, and the panel, and the
# command it will be inserted beside.
WORKSPACE_ID = "FusionSolidEnvironment"
PANEL_ID = "SolidScriptsAddinsPanel"
COMMAND_BESIDE_ID = "ScriptsManagerCommand"

# Resource location for command icons
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_FOLDER = os.path.join(THIS_DIR, "resources", "")

# Local list of event handlers used to maintain a reference so
# they are not released and garbage collected.
local_handlers = []


# Executed when add-in is run.
def start():
    # Create a command Definition.
    cmd_def = ui.commandDefinitions.addButtonDefinition(
        CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER
    )

    # Define an event handler for the command created event. It will be called when the button is clicked.
    futil.add_handler(cmd_def.commandCreated, command_created)

    # ******** Add a button into the UI so the user can run the command. ********
    # Get the target workspace the button will be created in.
    workspace = ui.workspaces.itemById(WORKSPACE_ID)

    # Get the panel the button will be created in.
    panel = workspace.toolbarPanels.itemById(PANEL_ID)

    # Create the button command control in the UI after the specified existing command.
    control = panel.controls.addCommand(cmd_def, COMMAND_BESIDE_ID, False)

    # Specify if the command is promoted to the main toolbar.
    control.isPromoted = IS_PROMOTED


# Executed when add-in is stopped.
def stop():
    # Get the various UI elements for this command
    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    panel = workspace.toolbarPanels.itemById(PANEL_ID)
    command_control = panel.controls.itemById(CMD_ID)
    command_definition = ui.commandDefinitions.itemById(CMD_ID)

    # Delete the button command control
    if command_control:
        command_control.deleteMe()

    # Delete the command definition
    if command_definition:
        command_definition.deleteMe()


# Function that is called when a user clicks the corresponding button in the UI.
# This defines the contents of the command dialog and connects to the command related events.
def command_created(args: adsk.core.CommandCreatedEventArgs):
    args.command.isExecutedWhenPreEmpted = False

    settings = load_settings()
    default_name = load_model_export_name()

    # Input commands
    # https://help.autodesk.com/view/fusion360/ENU/?contextId=CommandInputs
    inputs = args.command.commandInputs

    name_input = inputs.addStringValueInput("model_name", "Name", default_name)
    name_input.tooltip = "Name of the exported model"
    name_input.tooltipDescription = "Used as the output folder name and MJCF model name. Cannot be blank or contain characters invalid in file names."

    env_input = inputs.addBoolValueInput(
        "with_environment", "Ground plane/Light", True, "", settings["with_environment"]
    )
    env_input.tooltip = (
        "Include an enviroment (ground plane, light, etc) around the exported model"
    )
    env_input.tooltipDescription = """
        If unchecked, the model is exported without an additional environment.

        This is useful if the model will be imported into simulation environments.
        """

    colors_input = inputs.addBoolValueInput(
        "with_colors", "Include colors", True, "", settings["with_colors"]
    )
    colors_input.tooltip = "Export component appearance colors and materials"
    colors_input.tooltipDescription = """
        Reads the appearance (color, roughness, metalness) assigned to each
        component in Fusion 360 and writes it into the MuJoCo XML.

        This does not include textures/patterns.
        """

    short_names_input = inputs.addBoolValueInput(
        "use_short_names", "Use short names", True, "", settings["use_short_names"]
    )
    short_names_input.tooltip = "Shorten body names by removing redundant path segments"
    short_names_input.tooltipDescription = """
      Instead of using the full assembly path as the name for each body, this option
      drops path segments that are identical across all instances, keeping only the
      segments needed to uniquely identify each one.
    """

    refinement_input = inputs.addDropDownCommandInput(
        "mesh_resolution",
        "Mesh resolution",
        adsk.core.DropDownStyles.TextListDropDownStyle,
    )
    for level in ("Low", "Medium", "High"):
        refinement_input.listItems.add(level, level == settings["mesh_resolution"])
    refinement_input.tooltip = "Mesh resolution used when exporting visual STL files"
    refinement_input.tooltipDescription = """
        Low  — fastest export, coarser geometry (default).
        Medium — balanced quality and speed.
        High — finest geometry, longest export time.
    """

    convexify_input = inputs.addBoolValueInput(
        "should_convexify", "Collision meshes", True, "", settings["should_convexify"]
    )
    convexify_input.tooltip = """
        Uses CoACD to create collision meshes for the visual body meshes
        """
    convexify_input.tooltipDescription = """
      This will make your simulations more stable and accurate, but takes a lot longer to create.

      Depending on how low you set the threshold, each body can take several minutes to export.

      https://github.com/SarahWeiii/CoACD
    """

    convex_threshold = inputs.addFloatSpinnerCommandInput(
        "convex_threshold",
        "Concavity threshold",
        "",
        0.01,
        1.0,
        0.05,
        settings["convex_threshold"],
    )
    convex_threshold.isEnabled = settings["should_convexify"]
    convex_threshold.tooltip = "The threshold for the CoACD algorithm"
    convex_threshold.tooltipDescription = """
      A lower number means more detailed collision meshes, but takes longer to create.

      More information: https://github.com/SarahWeiii/CoACD
    """

    futil.add_handler(
        args.command.execute, command_execute, local_handlers=local_handlers
    )
    futil.add_handler(
        args.command.inputChanged, command_input_changed, local_handlers=local_handlers
    )
    futil.add_handler(
        args.command.validateInputs,
        command_validate_input,
        local_handlers=local_handlers,
    )
    futil.add_handler(
        args.command.destroy, command_destroy, local_handlers=local_handlers
    )


# This event handler is called when the user clicks the OK button in the command dialog or
# is immediately called after the created event not command inputs were created for the dialog.
def command_execute(args: adsk.core.CommandEventArgs):
    inputs = args.command.commandInputs
    model_name: str = inputs.itemById("model_name").value.strip()
    with_environment: bool = inputs.itemById("with_environment").value
    with_colors: bool = inputs.itemById("with_colors").value
    use_short_names: bool = inputs.itemById("use_short_names").value
    mesh_resolution: str = inputs.itemById("mesh_resolution").selectedItem.name

    should_convexify: bool = inputs.itemById("should_convexify").value
    convex_threshold: float | None = None
    if should_convexify:
        convex_threshold = inputs.itemById("convex_threshold").value

    save_model_export_name(model_name)
    save_settings(
        {
            "with_environment": with_environment,
            "with_colors": with_colors,
            "use_short_names": use_short_names,
            "mesh_resolution": mesh_resolution,
            "should_convexify": should_convexify,
            "convex_threshold": inputs.itemById("convex_threshold").value,
        }
    )

    exporter = Exporter(
        name=model_name,
        use_short_names=use_short_names,
        mesh_resolution=mesh_resolution,
        convex_threshold=convex_threshold,
        with_environment=with_environment,
        with_colors=with_colors,
    )
    exporter.export()


# This event handler is called when the user changes anything in the command dialog
# allowing you to modify values of other inputs based on that change.
def command_input_changed(args: adsk.core.InputChangedEventArgs):
    changed_input = args.input
    inputs = args.inputs

    if changed_input.id == "model_name":
        cleaned = _INVALID_FILENAME_CHARS.sub("", changed_input.value)
        if cleaned != changed_input.value:
            changed_input.value = cleaned

    # Enable/disable the concavity threshold input based on the should_convexify input
    if changed_input.id == "should_convexify":
        should_convexify = inputs.itemById("should_convexify").value
        convex_threshold: adsk.core.FloatSpinnerCommandInput = inputs.itemById(
            "convex_threshold"
        )
        convex_threshold.isEnabled = should_convexify


# This event handler is called when the user interacts with any of the inputs in the dialog
# which allows you to verify that all of the inputs are valid and enables the OK button.
def command_validate_input(args: adsk.core.ValidateInputsEventArgs):
    inputs = args.inputs

    name_input = inputs.itemById("model_name")
    if name_input:
        name_val = name_input.value.strip()
        if not name_val or _INVALID_FILENAME_CHARS.search(name_val):
            args.areInputsValid = False
            return

    convex_threshold = inputs.itemById("convex_threshold")
    if convex_threshold and convex_threshold.isEnabled:
        if not (0.01 <= convex_threshold.value <= 1.0):
            args.areInputsValid = False
            return


# This event handler is called when the command terminates.
def command_destroy(args: adsk.core.CommandEventArgs):
    global local_handlers
    local_handlers = []


def load_model_export_name() -> str:
    """
    Loads the name of the base component, or the previous export name used for this model.
    """
    design = adsk.fusion.Design.cast(app.activeProduct)
    saved_name_attr = design.attributes.itemByName("Fusion2Mujoco", ATTR_EXPORT_NAME)
    if saved_name_attr and saved_name_attr.value.strip():
        return saved_name_attr.value.strip()
    else:
        return _sanitize_filename(design.rootComponent.name or "Model")


def save_model_export_name(name: str):
    """
    Saves the name of the export for this model.
    """

    # If the name is the same as the component name, do not save
    design = adsk.fusion.Design.cast(app.activeProduct)
    component_name = _sanitize_filename(design.rootComponent.name)
    if name == component_name:
        design.attributes.add("Fusion2Mujoco", ATTR_EXPORT_NAME, "")

    design = adsk.fusion.Design.cast(app.activeProduct)
    design.attributes.add("Fusion2Mujoco", ATTR_EXPORT_NAME, name)
