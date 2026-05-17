import coacd
import adsk, adsk.core, adsk.fusion, traceback
import os
from os import path
from .mesh import MeshCollection
from .body_collection import MjcfBodyCollection
from .mjcf_builder import MjcfBuilder

MESH_DIR_NAME = "meshes"


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
        convex_threshold: float | None = None,
        with_environment: bool = True,
        with_colors: bool = True,
    ):
        self.name = name
        self.design = None
        self.rootComp = None
        self.short_body_names: bool = use_short_names
        self.mesh_resolution: str = mesh_resolution
        self.convexify: bool = convex_threshold is not None
        self.convex_threshold: float | None = convex_threshold
        self.with_environment: bool = with_environment
        self.with_colors: bool = with_colors
        self.destination: str = None
        self.mesh_root: str = None
        self.mjcf_bodies: MjcfBodyCollection = MjcfBodyCollection()
        self.progress: adsk.core.ProgressDialog = None
        self._progress_step = 0

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
        self._progress_step += 1
        self.progress.progressValue = self._progress_step
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

        # open a text palette for debugging
        self.textPalette = self.ui.palettes.itemById("TextCommands")
        if not self.textPalette.isVisible:
            self.textPalette.isVisible = True

        self.progress = self.ui.createProgressDialog()
        self.progress.cancelButtonText = "Cancel"
        self.progress.isBackgroundTranslucent = False
        self.progress.show("Fusion2Mujoco Export", "Initializing...", 0, 100, 0)

        try:
            self.root = self.design.rootComponent
            self.choose_destination()

            self.update_progress("Collecting links...")
            self.mjcf_bodies = MjcfBodyCollection.collect(
                self, use_short_names=self.short_body_names
            )

            # Now that we know how many unique meshes there are, set the total step count.
            # Steps: 2 (joints + links) + 2 per unique mesh (export STL + convexify)
            unique_meshes = set(body.mesh.base_name for body in self.mjcf_bodies)
            mesh_operations = len(unique_meshes)
            if self.convexify:
                mesh_operations *= 2
            self.progress.maximumValue = 3 + mesh_operations

            # Export each link's mesh body
            self.export_meshes()

            # Build/export the mujoco XML file
            mjcfBuilder = MjcfBuilder(
                exporter=self,
                with_environment=self.with_environment,
                with_colors=self.with_colors,
            )
            mjcfBuilder.build()
            mjcfBuilder.save()

        except ExportCancelledException:
            self.log("Export cancelled by user.")
        except:
            self.message_box("Failed:\n{}".format(traceback.format_exc()))
        finally:
            if self.progress:
                self.progress.hide()

    def choose_destination(self) -> str | None:
        """
        Choose the folder to export the model
        """

        folder_dialog = self.ui.createFolderDialog()
        folder_dialog.title = "Choose the folder to save to"
        dialog_result = folder_dialog.showDialog()

        selected_dir = ""
        if dialog_result == adsk.core.DialogResults.DialogOK:
            selected_dir = folder_dialog.folder
        else:
            raise ExportCancelledException()

        self.destination = path.join(selected_dir, self.name)
        os.makedirs(self.destination, exist_ok=True)

        self.mesh_root = path.join(self.destination, MESH_DIR_NAME)
        if not path.exists(self.mesh_root):
            os.makedirs(self.mesh_root)

        return self.destination

    def export_meshes(self):
        """
        Export one merged STL per unique component into <destination>/meshes/.
        """
        self.log(f"Exporting meshes to {self.mesh_root}")
        export_manager = self.design.exportManager
        exported: dict[str, MeshCollection] = {}
        coacd.set_log_level("error")
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
                self.update_progress(f"Convexifying: {mesh.base_name}")
                mesh.create_collision_mesh(self.mesh_root, self.convex_threshold)
