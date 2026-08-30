import adsk, adsk.core, adsk.fusion, traceback
import os
from os import path
from .mesh import MeshCollection
from .body_collection import MjcfBodyCollection
from .body import MjcfBody
from .naming import OccurrenceNamer
from .mjcf_builder import MjcfBuilder
from .errors import ExportError

MESH_DIR_NAME = "meshes"
ATTR_GROUP = "Fusion2Mujoco"
ATTR_EXPORT_DIR = "export_destination_dir"


class ExportCancelledException(Exception):
    pass


class Exporter:
    """
    Handles the process of exporting the model to mujoco
    """

    def __init__(
        self,
        name: str = "Model",
        use_short_names: bool = False,
        mesh_resolution: str = "Low",
        convexify: bool = False,
        convex_threshold: float | None = None,
        component_collision_settings: dict[str, float | None] | None = None,
        with_environment: bool = True,
        with_colors: bool = True,
    ):
        # Options
        self.short_body_names: bool = use_short_names
        self.mesh_resolution: str = mesh_resolution
        self.convexify: bool = convexify
        self.convex_threshold: float | None = convex_threshold
        self.component_collision_settings: dict[str, float | None] | None = (
            component_collision_settings
        )
        self.with_environment: bool = with_environment
        self.with_colors: bool = with_colors

        # State
        self.name = name
        self.design = None
        self.rootComp = None
        self.namer: OccurrenceNamer = OccurrenceNamer()
        self.destination: str = None
        self.mesh_root: str = None
        self.xml_file_name: str = f"{name}.xml"
        self.xml_file_path: str = None
        self.mjcf_bodies: MjcfBodyCollection = MjcfBodyCollection()
        self.progress: adsk.core.ProgressDialog = None
        self.progress_step = 0

    def message_box(self, message: str, title: str = "Fusion2Mujoco"):
        """
        Display a message box
        """
        if self.ui:
            self.ui.messageBox(message, title)

    def log(self, message: str):
        """
        Log a message
        """
        if self.textPalette:
            self.textPalette.writeText(message)

    def start_progress(self):
        """
        Start the progress dialog
        """
        # Count the number of steps in the progress dialog
        builder_steps = 2
        export_meshes = set(body.mesh.base_name for body in self.mjcf_bodies)

        visual_mesh_count = len(export_meshes)
        if self.convexify:
            visual_mesh_count *= 2

        collision_mesh_count = 0
        if self.convexify:
            collision_mesh_count = self.num_collision_meshes()

        maximumValue = builder_steps + visual_mesh_count + collision_mesh_count

        self.progress = self.ui.createProgressDialog()
        self.progress.cancelButtonText = "Cancel"
        self.progress.isBackgroundTranslucent = False
        self.progress.show(
            "Fusion2Mujoco Export", "Making magic...", 0, maximumValue, 0
        )

    def update_progress(self, message: str):
        """
        Advance the step counter, update the progress dialog message, and pump the
        event loop so Fusion repaints.
        Raises ExportCancelledException if the user cancelled the progress dialog.
        """
        if self.progress is None:
            return
        if self.progress.wasCancelled:
            raise ExportCancelledException()
        self.progress_step += 1
        self.progress.progressValue = self.progress_step
        self.progress.message = message
        adsk.doEvents()
        if self.progress.wasCancelled:
            raise ExportCancelledException()

    def export(self):
        """
        Setup the application and the design
        """
        app = adsk.core.Application.get()
        self.ui = app.userInterface
        self.design = adsk.fusion.Design.cast(app.activeProduct)
        self.rootComp = self.design.rootComponent
        self.textPalette = self.ui.palettes.itemById("TextCommands")

        try:
            self.root = self.design.rootComponent
            self.namer = OccurrenceNamer(self.root.allOccurrences)
            self.choose_destination()

            self.mjcf_bodies = MjcfBodyCollection.collect(
                self, use_short_names=self.short_body_names
            )
            self.start_progress()

            # Export each link's mesh body
            self.export_meshes()

            # Build/export the mujoco XML file
            mjcfBuilder = MjcfBuilder(
                exporter=self,
                with_environment=self.with_environment,
                with_colors=self.with_colors,
            )
            mjcfBuilder.build()
            mjcfBuilder.save(self.xml_file_path)

        except ExportCancelledException:
            self.log("Export cancelled by user.")
        except ExportError as e:
            self.log(f"Export failed: {e}")
            self.message_box(f"Mujoco export failed:\n\n{e}")
        except:
            self.message_box("Mujoco export failed:\n{}".format(traceback.format_exc()))
        finally:
            if self.progress:
                self.progress.hide()

    def choose_destination(self) -> str | None:
        """
        Choose the folder to export the model
        """

        folder_dialog = self.ui.createFolderDialog()
        folder_dialog.title = "Choose the folder to save to"

        # Default to the last directory the user selected
        saved_dir_attr = self.design.attributes.itemByName(ATTR_GROUP, ATTR_EXPORT_DIR)
        if saved_dir_attr and path.isdir(saved_dir_attr.value):
            folder_dialog.initialDirectory = saved_dir_attr.value

        dialog_result = folder_dialog.showDialog()

        self.destination = ""
        if dialog_result == adsk.core.DialogResults.DialogOK:
            self.destination = folder_dialog.folder
        else:
            raise ExportCancelledException()

        # If the target file already exists, ask the user if we should overwrite it
        self.xml_file_path = path.join(self.destination, self.xml_file_name)
        if path.isfile(self.xml_file_path):
            confirm = self.ui.messageBox(
                f'"{self.xml_file_name}" already exists in the selected folder.\n\nOverwrite it?',
                "Fusion2Mujoco",
                adsk.core.MessageBoxButtonTypes.YesNoButtonType,
                adsk.core.MessageBoxIconTypes.QuestionIconType,
            )
            if confirm != adsk.core.DialogResults.DialogYes:
                raise ExportCancelledException()

        self.mesh_root = path.join(self.destination, MESH_DIR_NAME)
        if not path.exists(self.mesh_root):
            os.makedirs(self.mesh_root)

        # Save the selected directory for next time
        self.design.attributes.add(ATTR_GROUP, ATTR_EXPORT_DIR, self.destination)

        return self.destination

    def export_meshes(self):
        """
        Export one merged STL per unique component into <destination>/meshes/.
        """
        self.log(f"Exporting meshes to {self.mesh_root}")
        export_manager = self.design.exportManager
        exported: dict[str, MeshCollection] = {}
        for body in self.mjcf_bodies:
            # If this mesh has already been exported, reuse the cached instance.
            if body.mesh.base_name in exported:
                body.mesh = exported[body.mesh.base_name]
                continue

            mesh = body.mesh
            exported[mesh.base_name] = mesh

            self.update_progress(f"Exporting mesh: {mesh.base_name}")
            mesh.export_visual_mesh(
                mesh_root=self.mesh_root,
                export_manager=export_manager,
                mesh_resolution=self.mesh_resolution,
            )

            if self.convexify:
                # Get convex threshold for this component
                threshold = self.convex_threshold
                token = body.occurrence.component.entityToken
                if self.component_collision_settings is not None:
                    threshold = self.component_collision_settings.get(token, threshold)

                if threshold:
                    self.update_progress(f"Convexifying: {mesh.base_name}")
                    mesh.create_collision_mesh(self.mesh_root, threshold)

    def collision_threshold_for_body(self, body: MjcfBody) -> float | None:
        if not self.convexify:
            return None

        threshold = self.convex_threshold
        token = body.occurrence.component.entityToken
        if self.component_collision_settings is not None:
            threshold = self.component_collision_settings.get(token, threshold)
        return threshold

    def num_collision_meshes(self) -> int:
        seen_tokens: set[str] = set()
        count = 0
        for body in self.mjcf_bodies:
            token = body.occurrence.component.entityToken
            if token in seen_tokens:
                continue
            seen_tokens.add(token)
            threshold = self.collision_threshold_for_body(body)
            if threshold is not None:
                count += 1
        return count
