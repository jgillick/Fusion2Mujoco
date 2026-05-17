from __future__ import annotations

from os import path
import adsk, adsk.core
import xml.etree.ElementTree as ET
from .body import MjcfBody
from .joint import Joint
from . import math_operation as math_op
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .exporter import Exporter

GROUND_PLANE_SIZE = 10
COLLISION_CLASS = "collision"


class MjcfBuilder:
    """
    Build the Mujoco XML file for the model
    """

    def __init__(
        self,
        exporter: Exporter,
        with_environment: bool = True,
        with_colors: bool = True,
    ):
        self.exporter = exporter
        self.with_environment = with_environment
        self.with_colors = with_colors

        self.root_el: ET.Element = None
        self.compiler_el: ET.Element = None
        self.assets_el: ET.Element = None
        self.worldbody_el: ET.Element = None
        self.rootbody_el: ET.Element = None

        self.built_materials: dict[str, str] = {}
        self.has_collision_meshes = exporter.convexify
        self.root_bodies: set[MjcfBody] = set([])
        self.joint_relationships: dict[str, list[MjcfBody]] = {}

    def build(self):
        """
        Build the MJCF file
        """
        self.exporter.update_progress("Building MJCF file...")
        self.parse_joint_relationships()

        # Build XML
        self.root_el = ET.Element("mujoco", {"model": self.exporter.name})
        self.build_compiler()
        self.build_defaults()
        self.build_assets()
        self.build_material_assets()
        self.build_worldbody()
        self.build_environment()

    def save(self):
        """
        Save the MJCF file
        """
        # Format
        tree = ET.ElementTree(self.root_el)
        ET.indent(tree, space="    ", level=0)
        file_path = path.join(self.exporter.destination, f"{self.exporter.name}.xml")

        self.exporter.update_progress(f"Saving to {file_path}...")
        with open(file_path, "wb") as handle:
            tree.write(handle, encoding="utf-8", xml_declaration=False)

    def build_compiler(self):
        """
        Build the compiler block
        """
        self.compiler_el = ET.SubElement(self.root_el, "compiler", {"angle": "radian"})
        return self.compiler_el

    def build_defaults(self):
        """
        Build the defaults block
        """
        defaults = ET.SubElement(self.root_el, "default")
        ET.SubElement(defaults, "mesh", {"scale": "0.001 0.001 0.001"})

        # If this model has collision meshes, create defaults to differentiate between them
        if self.has_collision_meshes:
            # By default, visual bodies are not collidable
            ET.SubElement(
                defaults,
                "geom",
                {
                    "contype": "0",
                    "conaffinity": "0",
                    "group": "2",
                },
            )
            # Elements with the collision class can collide
            class_default_el = ET.SubElement(
                defaults,
                "default",
                {"class": COLLISION_CLASS},
            )
            ET.SubElement(
                class_default_el,
                "geom",
                {
                    "contype": "1",
                    "conaffinity": "1",
                    "group": "3",
                    "pos": "0 0 0",
                    "mass": "0.0",
                },
            )

        return defaults

    def build_assets(self):
        """
        Build the assets block
        """
        self.assets_el = ET.SubElement(self.root_el, "asset")
        added_meshes = set()
        relative_mesh_root = path.relpath(
            self.exporter.mesh_root, self.exporter.destination
        )
        for body in self.exporter.mjcf_bodies:
            meshes = body.mesh
            if meshes.base_name in added_meshes:
                continue
            added_meshes.add(meshes.base_name)

            for mesh in meshes.mesh_items:
                ET.SubElement(
                    self.assets_el,
                    "mesh",
                    {
                        "name": mesh.name,
                        "file": path.join(relative_mesh_root, mesh.file),
                    },
                )

    def build_worldbody(self):
        """
        Build the worldbody block
        """
        self.worldbody_el = ET.SubElement(self.root_el, "worldbody")

        pos = "0.0 0.0 0.0"
        if self.with_environment:
            z_offset = self.compute_ground_offset()
            pos = f"0.0 0.0 {z_offset:.6g}"

        root_body_el = ET.SubElement(
            self.worldbody_el,
            "body",
            {"name": "model_root", "pos": pos, "quat": "1 0 0 0"},
        )

        # Add all the top-level bodies/occurrences
        for body in self.root_bodies:
            self.build_body(body, None, root_body_el)

    def build_body(
        self, body: MjcfBody, parent: MjcfBody | None, parent_el: ET.Element
    ) -> ET.Element:
        """
        Construct and return a the MJCF body element
        """

        if parent is None:
            # A root body element is positioned in the world frame
            pos = math_op.matrix3d_to_pos(body.occurrence.transform2)
            quat = math_op.matrix3d_to_quat(body.occurrence.transform2)
        else:
            # This body is nested another another body
            # The position is relative to the parent
            parent_frame: adsk.core.Matrix3D = parent.occurrence.transform2
            child_frame: adsk.core.Matrix3D = body.occurrence.transform2
            parent_T_child = math_op.coordinate_transform(parent_frame, child_frame)
            pos = math_op.matrix3d_to_pos(parent_T_child)
            quat = math_op.matrix3d_to_quat(parent_T_child)

        # Create element
        (x, y, z) = pos
        (qw, qx, qy, qz) = quat
        body_el = ET.SubElement(parent_el, "body")
        body_el.attrib = {
            "name": body.name,
            "pos": "{} {} {}".format(x, y, z),
            "quat": "{} {} {} {}".format(qw, qx, qy, qz),
        }

        # Add parent joint if it exists
        parent_joint = body.get_parent_joint()
        if parent_joint is not None:
            self.build_joint(parent_joint, body, body_el)

        # Other body elements
        self.build_body_geoms(body, body_el)
        self.build_body_inertial(body, body_el)

        # Add child bodies
        children = self.joint_relationships.get(body.full_name, [])
        for child in children:
            self.build_body(child, body, body_el)

        return body_el

    def build_joint(
        self, joint: Joint, child_body: MjcfBody, parent_el: ET.Element
    ) -> ET.Element:
        """
        Construct and return a the MJCF joint element
        """
        joint_type = joint.get_joint_type()
        if joint_type is None:
            return

        joint_el = ET.Element("joint")
        pose = joint.get_origin()
        axis = joint.get_axis()
        joint_el.attrib = {
            "type": joint_type,
            "name": f"{child_body.name}",
            "axis": "{} {} {}".format(axis[0], axis[1], axis[2]),
            "pos": "{} {} {}".format(pose[0], pose[1], pose[2]),
        }

        limits = joint.get_limits()
        if limits is not None:
            lower, upper = limits
            joint_el.attrib["range"] = "{} {}".format(lower, upper)
            joint_el.attrib["limited"] = "true"

        parent_el.append(joint_el)

    def build_body_geoms(self, body: MjcfBody, parent_el: ET.Element) -> ET.Element:
        """
        Construct the MJCF geom elements for a body.
        """
        for mesh in body.mesh.mesh_items:
            attrs = {
                "mesh": mesh.name,
                "name": f"{body.name}_{mesh.name}_geom",
                "type": "mesh",
                "pos": "0 0 0",
                "quat": "1 0 0 0",
            }
            if mesh.type == "collision":
                attrs["class"] = "collision"
            elif self.with_colors and body.mesh.base_name in self.built_materials:
                attrs["material"] = self.built_materials[body.mesh.base_name]
            ET.SubElement(parent_el, "geom", attrs)

    def build_body_inertial(self, body: MjcfBody, parent_el: ET.Element) -> ET.Element:
        """
        Construct a the MJCF inertial element for a body
        """
        inertial_ele = ET.Element("inertial")
        mass: str = str(body.get_mass())
        CoM: list = body.get_CoM_wrt_component()
        pos_att: str = "{} {} {}".format(CoM[0], CoM[1], CoM[2])
        inertial: list = body.get_inertia()
        # 6 numbers in the following order: M(1,1), M(2,2), M(3,3), M(1,2), M(1,3), M(2,3).
        # which is ixx, iyy, izz, ixy, ixz, iyz
        fullinertia_att = "{} {} {} {} {} {}".format(
            inertial[0], inertial[1], inertial[2], inertial[3], inertial[4], inertial[5]
        )
        inertial_ele.attrib = {
            "mass": mass,
            "pos": pos_att,
            "fullinertia": fullinertia_att,
        }

        parent_el.append(inertial_ele)

    def build_material_assets(self):
        """
        Add body material assets
        """
        if not self.with_colors:
            return

        for body in self.exporter.mjcf_bodies:
            # Return the material name if it has already been built/added
            material_name = f"{body.mesh.base_name}_mat"
            if body.mesh.base_name in self.built_materials:
                continue

            appearance = body.get_appearance()
            if appearance is None:
                continue

            # Material asset
            attrs: dict[str, str] = {"name": material_name}
            if appearance.rgba is not None:
                attrs["rgba"] = "{:.4f} {:.4f} {:.4f} {:.4f}".format(*appearance.rgba)
            if appearance.roughness is not None:
                attrs["roughness"] = f"{appearance.roughness:.4f}"
            if appearance.metallic is not None:
                attrs["metallic"] = f"{appearance.metallic:.4f}"

            self.built_materials[body.mesh.base_name] = material_name
            ET.SubElement(self.assets_el, "material", attrs)

    def build_environment(self):
        """
        Build an environment (ground plane, light, etc) around the model.
        """
        if not self.with_environment:
            return

        # Ground plane assets
        ET.SubElement(
            self.assets_el,
            "texture",
            {
                "type": "2d",
                "name": "ground_plane",
                "builtin": "checker",
                "mark": "edge",
                "rgb1": "0.2 0.3 0.4",
                "rgb2": "0.1 0.2 0.3",
                "markrgb": "0.8 0.8 0.8",
                "width": "200",
                "height": "200",
            },
        )
        ET.SubElement(
            self.assets_el,
            "material",
            {
                "name": "ground_plane",
                "texture": "ground_plane",
                "texuniform": "true",
                "texrepeat": "5 5",
                "reflectance": "0.2",
            },
        )

        # Add ground plane and light to the worldbody
        ground_plane_geom_el = ET.Element(
            "geom",
            {
                "name": "floor",
                "size": f"{GROUND_PLANE_SIZE} {GROUND_PLANE_SIZE} 0.05",
                "type": "plane",
                "contype": "1",
                "conaffinity": "1",
                "rgba": "1 1 1 1",
                "material": "ground_plane",
            },
        )
        light_el = ET.Element(
            "light",
            {
                "directional": "true",
                "pos": "-0.5 0.5 3",
                "dir": "0 0 -1",
            },
        )
        self.worldbody_el.insert(0, light_el)
        self.worldbody_el.insert(1, ground_plane_geom_el)

    def parse_joint_relationships(self):
        """
        Create a lookup dictionary of all joints by their parent occurrence,
        and a set of all root bodies.
        """

        # Map paths to mjcf body instances
        body_path_map: dict[str, MjcfBody] = {}
        for body in self.exporter.mjcf_bodies:
            body_path_map[body.full_name] = body
            self.root_bodies.add(body)

        # Build the joint relationships dictionary
        for joint in self.exporter.root.allJoints:
            parent = joint.occurrenceTwo
            child = joint.occurrenceOne
            if parent is None or child.fullPathName not in body_path_map:
                continue
            if parent.fullPathName not in self.joint_relationships:
                self.joint_relationships[parent.fullPathName] = []

            child_body = body_path_map[child.fullPathName]
            self.joint_relationships[parent.fullPathName].append(child_body)
            if child_body in self.root_bodies:
                self.root_bodies.remove(child_body)

    def compute_ground_offset(self) -> float:
        """
        Return the Z offset (in meters) needed to place the model's lowest
        point exactly at z=0 (the ground plane).

        Iterates over every body's bounding box in the root assembly frame
        (Fusion reports these in cm), finds the global minimum Z, and returns
        its negation so that adding this offset to ``model_root`` lifts the
        entire robot until its lowest vertex touches z=0.

        Returns:
            float: Z offset in meters. Positive means lift up; 0.0 if no
                bounding-box data is available.
        """
        min_z = float("inf")
        for body in self.exporter.mjcf_bodies:
            bb = body.occurrence.boundingBox
            if bb is not None:
                min_z = min(min_z, bb.minPoint.z * 0.01)  # cm -> m
        if min_z == float("inf"):
            return 0.0
        return -min_z
