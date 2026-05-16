from ctypes import Union
import os
from typing import Literal
import coacd
import trimesh
import adsk, adsk.core, adsk.fusion, traceback
from dataclasses import dataclass
from os import path

VISUAL_BASE_NAME = "visual"
COLLISION_BASE_NAME = "collision"


@dataclass
class Mesh:
    name: int
    file: str
    type: Union[Literal["visual"], Literal["collision"]]


class MeshCollection:
    """
    Manages the collection of meshes of a single component occurrence / mjcf body.
    """

    def __init__(self):
        self.base_name: str | None = None
        self.collision_meshes: list[str] = []

    @property
    def mesh_items(self) -> list[Mesh]:
        """
        List of all (visual and collision) mesh items.
        """
        return [self.visual] + self.collisions

    @property
    def visual(self) -> Mesh:
        """
        The visual mesh item.
        """
        return Mesh(
            type="visual",
            name=f"{self.base_name}_{VISUAL_BASE_NAME}",
            file=path.join(self.base_name, f"{VISUAL_BASE_NAME}.stl"),
        )

    @property
    def collisions(self) -> list[Mesh]:
        """
        List of the collision mesh items.
        """
        items = []
        for collision in self.collision_meshes:
            item = Mesh(
                type="collision",
                name=f"{self.base_name}_{collision}",
                file=path.join(
                    self.base_name,
                    f"{collision}.stl",
                ),
            )
            items.append(item)
        return items

    def get_dir(self, base_path: str) -> str:
        """
        Get the directory for the mesh
        """
        return path.join(base_path, self.base_name)

    def export_visual_mesh(
        self,
        mesh_root: str,
        occurrence: adsk.fusion.Occurrence,
        export_manager: adsk.fusion.ExportManager,
    ):
        """
        Export the visual mesh to file

        Args:
            mesh_root: The root directory all meshes are exported to
            occurrence: The component occurrence to export the mesh from
            export_manager: The Fusion export manager to use to export the mesh
        """
        # Create the output directory and visual file name
        visual_path = path.join(mesh_root, self.visual.file)
        visual_dir = path.dirname(visual_path)
        if not path.exists(visual_dir):
            os.makedirs(visual_dir)

        # Export the mesh
        stl_export_options = export_manager.createSTLExportOptions(
            occurrence, visual_path
        )
        stl_export_options.sendToPrintUtility = False
        stl_export_options.isBinaryFormat = True
        stl_export_options.meshRefinement = (
            adsk.fusion.MeshRefinementSettings.MeshRefinementLow
        )
        export_manager.execute(stl_export_options)

    def create_collision_mesh(self, mesh_root: str, convex_threshold: float):
        """
        Create collision meshes from the visual mesh

        Args:
            mesh_root: The root directory all meshes are exported to
            convex_threshold: The concavity threshold for the CoACD algorithm
        """

        # Load the visual mesh
        visual_path = path.join(mesh_root, self.visual.file)
        visual_mesh = trimesh.load(path.abspath(visual_path), force="mesh")

        # Run the CoACD algorithm
        coacd_mesh = coacd.Mesh(visual_mesh.vertices, visual_mesh.faces)
        parts = coacd.run_coacd(
            coacd_mesh,
            threshold=convex_threshold,
        )

        # Export the collision meshes
        for part in parts:
            vertices = part[0]
            faces = part[1]

            idx = len(self.collision_meshes)
            collision_name = f"{COLLISION_BASE_NAME}{idx:02d}"
            collision_file = path.join(
                mesh_root, self.base_name, f"{collision_name}.stl"
            )

            part_mesh = trimesh.Trimesh(vertices=vertices, faces=faces)
            part_mesh.export(collision_file)
            self.collision_meshes.append(collision_name)
