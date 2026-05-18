from __future__ import annotations
from typing import TYPE_CHECKING
import adsk, adsk.fusion, adsk.core

from . import utils
from . import math_operation as math_op
from .joint import Joint
from .mesh import MeshCollection
from .appearance import AppearanceData

if TYPE_CHECKING:
    from .exporter import Exporter


class MjcfBody:
    """
    Wraps a Fusion 360 occurrence and provides the physical data MuJoCo needs.

    Each MjcfBody corresponds to one ``<body>`` element in the output MJCF
    file. It exposes mass, inertia, center-of-mass position, and mesh
    information extracted from the Fusion 360 occurrence's physical properties.

    Attributes:
        occurrence (adsk.fusion.Occurrence): The underlying Fusion 360
            occurrence this body is built from.
        pose (adsk.core.Matrix3D): The occurrence's transform in the root
            component's assembly context (equivalent to
            ``occurrence.transform2``).
        phyPro (adsk.fusion.PhysicalProperties): Physical properties of the
            occurrence, computed at very high accuracy. Used to query mass,
            inertia, and center of mass.
        short_name (str | None): An optional abbreviated name assigned by
            ``MjcfBodyCollection.shorten_names()``. When set, ``name``
            returns this instead of the full sanitised path.
        mesh (MeshCollection): The visual and collision mesh files for this
            body. The visual STL is built by merging all direct visible
            BRepBodies of the component (child-component bodies are excluded).
    """

    def __init__(self, exporter: Exporter, occurrence: adsk.fusion.Occurrence) -> None:
        """
        Args:
            occurrence (adsk.fusion.Occurrence): The Fusion 360 occurrence
                this body wraps. Physical properties are queried immediately
                at construction time.
        """
        self.exporter: Exporter = exporter
        self.occurrence: adsk.fusion.Occurrence = occurrence
        self.pose: adsk.core.Matrix3D = occurrence.transform2
        self.phyPro = occurrence.getPhysicalProperties(
            adsk.fusion.CalculationAccuracy.VeryHighCalculationAccuracy
        )
        self.short_name: str | None = None
        self.mesh: MeshCollection = MeshCollection(occurrence.component.bRepBodies)

    @property
    def max_bbox_cm(self) -> float | None:
        """
        Largest axis-aligned bounding-box dimension of this occurrence in
        centimetres (Fusion's native unit).  Returns ``None`` if the bounding
        box is unavailable.
        """
        try:
            bb = self.occurrence.boundingBox
            dx = bb.maxPoint.x - bb.minPoint.x
            dy = bb.maxPoint.y - bb.minPoint.y
            dz = bb.maxPoint.z - bb.minPoint.z
            return max(dx, dy, dz)
        except Exception:
            return None

    def get_parent_joint(self) -> Joint | None:
        """
        Return the joint whose child occurrence is this body, or ``None``.

        In a closed-loop mechanism a component may have more than one parent
        joint; only the first match is returned.

        Returns:
            Joint | None: The parent joint, or ``None`` if this body is a
                root (unjointed) body.
        """
        app = adsk.core.Application.get()
        root = adsk.fusion.Design.cast(app.activeProduct).rootComponent
        for joint in root.allJoints:
            if joint.occurrenceOne == self.occurrence:
                return Joint(joint)
        for joint in root.allAsBuiltJoints:
            if joint.occurrenceOne == self.occurrence:
                return Joint(joint)
        return None

    @property
    def full_name(self) -> str:
        """
        The occurrence's full path name as reported by Fusion 360.

        Returns:
            str: The raw ``fullPathName`` string of the underlying occurrence.
        """
        return self.occurrence.fullPathName

    @property
    def name(self) -> str:
        """
        The body's display name, used as the ``name`` attribute in MJCF.

        Returns the ``short_name`` if one has been assigned by
        ``MjcfBodyCollection.shorten_names()``, otherwise returns the full
        path sanitised into a valid filename string.

        Returns:
            str: The body's name.
        """
        if self.short_name is not None:
            return self.short_name
        return utils.get_valid_filename(self.full_name)

    def get_inertia(self) -> list:
        """
        Return the inertia tensor of this body in MuJoCo order.

        Fusion 360 reports moments of inertia about the world-frame origin.
        This method applies the parallel-axis theorem to shift them to the
        body's center of mass, then rotates the tensor into the body's own
        link frame (the frame described by ``self.pose``), which is the
        orientation MuJoCo uses for inertial properties.

        All values are in SI units (kg·m²). Unit conversions applied:
        - Fusion reports inertia in kg·cm² → multiply by 0.0001 for kg·m².
        - Fusion reports position in cm → multiply by 0.01 for m.

        Returns:
            list: Six inertia components in MuJoCo's ``fullinertia`` order:
                [ixx, iyy, izz, ixy, ixz, iyz].
        """
        (_, w_ixx, w_iyy, w_izz, w_ixy, w_iyz, w_ixz) = (
            self.phyPro.getXYZMomentsOfInertia()
        )  # unit: kg*cm^2

        # Shift inertia from world-frame origin to center of mass via the
        # parallel-axis theorem: I_com = I_world - m*(d x d)
        com = self.phyPro.centerOfMass
        x, y, z = com.x * 0.01, com.y * 0.01, com.z * 0.01  # cm -> m
        mass = self.phyPro.mass  # kg

        com_ixx = w_ixx * 0.0001 - mass * (y**2 + z**2)
        com_iyy = w_iyy * 0.0001 - mass * (x**2 + z**2)
        com_izz = w_izz * 0.0001 - mass * (x**2 + y**2)
        com_ixy = w_ixy * 0.0001 + mass * (x * y)
        com_iyz = w_iyz * 0.0001 + mass * (y * z)
        com_ixz = w_ixz * 0.0001 + mass * (x * z)

        # Rotate the tensor into the link frame: I_L = R^T * I_world * R
        # reference: https://robot.sia.cn/CN/abstract/abstract374.shtml
        R = math_op.get_rotation_matrix(self.pose)
        R_T = math_op.matrix_transpose(R)
        inertia_tensor = [
            [com_ixx, com_ixy, com_ixz],
            [com_ixy, com_iyy, com_iyz],
            [com_ixz, com_iyz, com_izz],
        ]
        I = math_op.matrix_multiply(math_op.matrix_multiply(R_T, inertia_tensor), R)

        return [
            I[0][0],
            I[1][1],
            I[2][2],
            I[0][1],
            I[0][2],
            I[1][2],
        ]  # ixx, iyy, izz, ixy, ixz, iyz

    def get_mass(self) -> float:
        """
        Return the mass of this body in kilograms.

        Returns:
            float: Mass in kg.
        """
        return self.phyPro.mass

    def get_CoM_wrt_component(self) -> list:
        """
        Return the center of mass expressed in the body's own link frame.

        MuJoCo places inertial frames relative to the body frame. This method
        computes the offset from the link-frame origin to the center of mass,
        expressed using the link frame's own axes. Orientation is assumed to
        match the link frame (roll = pitch = yaw = 0).

        Returns:
            list: [x, y, z, roll, pitch, yaw] in meters and radians.
        """
        com = self.phyPro.centerOfMass
        lo = self.pose.translation
        w_Lo_CoM = [
            [(com.x - lo.x) * 0.01],
            [(com.y - lo.y) * 0.01],
            [(com.z - lo.z) * 0.01],
        ]  # cm -> m

        L_R_w = math_op.matrix_transpose(math_op.get_rotation_matrix(self.pose))
        L_Lo_CoM = math_op.change_orientation(L_R_w, w_Lo_CoM)

        return [row[0] for row in L_Lo_CoM] + [0.0, 0.0, 0.0]

    def get_appearance(self) -> AppearanceData | None:
        """
        Extract color, roughness, metalness, and optional texture path from
        the Fusion 360 appearance assigned to this occurrence.

        Walks the override chain via ``_resolve_appearance()``. Returns None
        if no usable appearance data is found (no color and no texture).

        Returns:
            AppearanceData | None
        """
        data = AppearanceData()
        data.load(self.occurrence, self.mesh.visible_bodies)
        return data
