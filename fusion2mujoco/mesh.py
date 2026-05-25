from ctypes import Union
import os
from typing import Literal
import trimesh
import adsk, adsk.fusion
from dataclasses import dataclass
from os import path

VISUAL_BASE_NAME = "visual"
COLLISION_BASE_NAME = "collision"

RESOLUTION_MAP = {
    "Low": adsk.fusion.MeshRefinementSettings.MeshRefinementLow,
    "Medium": adsk.fusion.MeshRefinementSettings.MeshRefinementMedium,
    "High": adsk.fusion.MeshRefinementSettings.MeshRefinementHigh,
}


@dataclass
class Mesh:
    name: int
    file: str
    type: Union[Literal["visual"], Literal["collision"]]


class MeshCollection:
    """
    Manages the collection of meshes for a single component occurrence / mjcf body.

    The visual STL is the result of merging all direct BRepBodies of the
    component. Optional collision STLs are produced by CoACD decomposition of
    that merged visual mesh.
    """

    def __init__(self, bodies: list[adsk.fusion.BRepBody]):
        self.base_name: str | None = None
        self.collision_meshes: list[str] = []
        self.bodies = bodies
        self.visible_bodies = [b for b in bodies if b.isLightBulbOn]

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
        export_manager: adsk.fusion.ExportManager,
        mesh_resolution: str = "Low",
    ):
        """
        Export the visual mesh for a component to file.

        Each body in ``self.bodies`` is exported individually (Fusion's API
        does not support multi-body export to a single file), then all parts
        are concatenated with trimesh into one ``visual.stl``. Temporary
        per-body files are removed after the merge.

        Args:
            mesh_root: The root directory all meshes are exported to.
            export_manager: The Fusion export manager to use to export the mesh.
        """
        visual_path = path.join(mesh_root, self.visual.file)
        visual_dir = path.dirname(visual_path)
        if not path.exists(visual_dir):
            os.makedirs(visual_dir)

        def export_body(entity, out_path):
            opts = export_manager.createSTLExportOptions(entity, out_path)
            opts.sendToPrintUtility = False
            opts.isBinaryFormat = True
            opts.meshRefinement = RESOLUTION_MAP.get(
                mesh_resolution,
                adsk.fusion.MeshRefinementSettings.MeshRefinementLow,
            )
            export_manager.execute(opts)

        if len(self.visible_bodies) == 1:
            # Fast path: single body — export directly, no trimesh needed.
            export_body(self.visible_bodies[0], visual_path)
        else:
            # Export each body to a temp file, then concatenate with trimesh.
            temp_paths = []
            for i, body in enumerate(self.visible_bodies):
                temp_path = path.join(visual_dir, f"_tmp_{i}.stl")
                export_body(body, temp_path)
                temp_paths.append(temp_path)

            parts = [trimesh.load(p, force="mesh") for p in temp_paths]
            merged = trimesh.util.concatenate(parts)
            merged.export(visual_path)

            for p in temp_paths:
                os.remove(p)

    def create_collision_mesh(self, mesh_root: str, convex_threshold: float):
        """
        Create collision meshes from the visual mesh

        Args:
            mesh_root: The root directory all meshes are exported to
            convex_threshold: The concavity threshold for the CoACD algorithm
        """
        # CoACD isn't supported on Windows on ARM.
        # The collision options are hidden for this platform, so we should
        # never get here on that platform.
        import coacd

        # Load the visual mesh
        visual_path = path.join(mesh_root, self.visual.file)
        visual_mesh = trimesh.load(path.abspath(visual_path), force="mesh")

        coacd.set_log_level("error")
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
