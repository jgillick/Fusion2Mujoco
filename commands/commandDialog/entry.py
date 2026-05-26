import adsk.core
import adsk.fusion
import os

from ...lib import fusionAddInUtils as futil
from ... import config
from ...fusion2mujoco.exporter import Exporter
from .general import GeneralInputs, INVALID_FILENAME_CHARS
from .collision import CollisionInputs
from .logger import Logger


def _sanitize_filename(name: str) -> str:
    return INVALID_FILENAME_CHARS.sub("", name).strip()


app = adsk.core.Application.get()
ui = app.userInterface

CMD_ID = f"{config.COMPANY_NAME}_{config.ADDIN_NAME}_cmdDialog"
CMD_NAME = "Export to Mujoco"
CMD_Description = "Export your model to Mujoco XML"


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
local_handlers: list = []
general_inputs: GeneralInputs | None = None
collision_inputs: CollisionInputs | None = None

ID_TAB_GENERAL = "export_tab_general"
ID_TAB_COLLISIONS = "export_tab_collisions"


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
    global general_inputs, collision_inputs
    design = adsk.fusion.Design.cast(app.activeProduct)

    args.command.isExecutedWhenPreEmpted = False
    args.command.setDialogSize(420, 480)

    logger = Logger()
    inputs = args.command.commandInputs

    general_tab = inputs.addTabCommandInput(ID_TAB_GENERAL, "General")
    general_inputs = GeneralInputs(inputs=general_tab.children, design=design)
    general_inputs.build()

    collisions_tab = inputs.addTabCommandInput(ID_TAB_COLLISIONS, "Collisions")
    collision_inputs = CollisionInputs(
        inputs=collisions_tab.children,
        design=design,
        logger=logger,
    )
    collision_inputs.build()

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
    if general_inputs is None:
        return

    # Save input values for the next session
    general_inputs.save()
    collision_inputs.save()

    # Export the model
    exporter = Exporter(
        name=general_inputs.model_name,
        use_short_names=general_inputs.use_short_names,
        mesh_resolution=general_inputs.mesh_resolution,
        with_environment=general_inputs.with_environment,
        with_colors=general_inputs.with_colors,
        convexify=collision_inputs.should_convexify,
        convex_threshold=collision_inputs.convex_threshold,
        component_collision_settings=collision_inputs.component_collision_settings,
    )
    exporter.export()


# This event handler is called when the user changes anything in the command dialog
# allowing you to modify values of other inputs based on that change.
def command_input_changed(args: adsk.core.InputChangedEventArgs):
    changed_input = args.input
    general_inputs.handle_input_changed(changed_input)
    collision_inputs.handle_input_changed(changed_input)


# This event handler is called when the user interacts with any of the inputs in the dialog
# which allows you to verify that all of the inputs are valid and enables the OK button.
def command_validate_input(args: adsk.core.ValidateInputsEventArgs):
    general_valid = True
    collision_valid = True

    if general_inputs is not None and not general_inputs.validate():
        general_valid = False
    if collision_inputs is not None and not collision_inputs.validate():
        collision_valid = False

    args.areInputsValid = general_valid or collision_valid


# This event handler is called when the command terminates.
def command_destroy(args: adsk.core.CommandEventArgs):
    global local_handlers, general_inputs, collision_inputs
    local_handlers = []
    general_inputs = None
    collision_inputs = None
