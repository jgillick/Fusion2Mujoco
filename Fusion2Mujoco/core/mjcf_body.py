from __future__ import annotations
from typing import TYPE_CHECKING
import adsk, adsk.fusion, adsk.core

from . import utils
from . import math_operation as math_op
from .joint import Joint
from .mesh import MeshCollection

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
        mesh (MeshCollection): The visual and collision mesh files associated
            with this body's component.
    """

    def __init__(self, occurrence: adsk.fusion.Occurrence) -> None:
        """
        Args:
            occurrence (adsk.fusion.Occurrence): The Fusion 360 occurrence
                this body wraps. Physical properties are queried immediately
                at construction time.
        """
        self.occurrence: adsk.fusion.Occurrence = occurrence
        self.pose: adsk.core.Matrix3D = occurrence.transform2
        self.phyPro = occurrence.getPhysicalProperties(
            adsk.fusion.CalculationAccuracy.VeryHighCalculationAccuracy
        )
        self.short_name: str | None = None
        self.mesh: MeshCollection = MeshCollection()

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


class MjcfBodyCollection:
    """
    An iterable collection of MjcfBody objects.
    """

    def __init__(self, items: list[MjcfBody] | None = None) -> None:
        self._items: list[MjcfBody] = items or []

    def __iter__(self):
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __getitem__(self, index):
        return self._items[index]

    @staticmethod
    def collect(
        exporter: Exporter, use_short_names: bool = False
    ) -> MjcfBodyCollection:
        """
        Build a collection from all visible, leaf occurrences in the design.

        Iterates over every occurrence in the root component, skipping those
        that are hidden, have no visible bodies, or have child components
        (since MuJoCo bodies must be leaves in the kinematic tree).

        Args:
            exporter (Exporter): The Exporter instance, used to access the
                root component and emit log messages.
            use_short_names (bool): When True, call ``shorten_names()`` on
                the collection before returning so each body uses the
                shortest name that remains unique.

        Returns:
            MjcfBodyCollection: The populated collection.
        """
        items: list[MjcfBody] = []
        name_to_tokens: dict[str, list[str]] = {}

        root: adsk.fusion.Component = exporter.root
        occs: list[adsk.fusion.Occurrence] = root.allOccurrences
        for occ in occs:
            if not occ.isLightBulbOn or not utils.component_has_bodies(occ.component):
                continue
            if occ.childOccurrences.count > 0:
                exporter.log(
                    f"Skipping {occ.fullPathName} because it has child components"
                )
                continue

            comp = occ.component
            mjcf_body = MjcfBody(occ)
            mjcf_body.mesh.base_name = comp.name

            # Track entity tokens per component name to detect distinct
            # components that happen to share the same name.
            tokens = name_to_tokens.setdefault(comp.name, [])
            if comp.entityToken not in tokens:
                tokens.append(comp.entityToken)

            # When multiple distinct components share a name, suffix the mesh
            # base name with a 1-based index to keep filenames unique.
            if len(tokens) > 1:
                entity_index = tokens.index(comp.entityToken) + 1
                mjcf_body.mesh.base_name += f"_{entity_index}"

            items.append(mjcf_body)

        collection = MjcfBodyCollection(items)
        if use_short_names:
            collection.shorten_names()
        return collection

    def shorten_names(self) -> None:
        """
        Assign each body a ``short_name`` using the minimum set of path
        segments needed to keep all names unique.

        All names remain unique; only the segments strictly necessary to
        distinguish each individual name from every other are retained.

        The algorithm uses per-name position sets rather than a single
        global set. This prevents a segment that is required to distinguish
        one group of names (e.g. Hip vs Tibia for Motor entries) from being
        unnecessarily injected into unrelated names (e.g. Foot entries where
        the joint-level segment adds no information).

        Steps:

        1. Split each full name by ``_`` into a segment list.
        2. Pad all lists to the same length with ``None`` for comparison.
        3. Seed each name's retained-position set with its last segment.
        4. Repeatedly find collision groups (names that currently share the
           same projected short name). For each group, add the single
           position that produces the most distinct values within the group
           (ties broken by earliest position) to every member's set.
        5. Repeat until no collisions remain or no progress can be made.
        6. Write the joined short name back onto each body's
           ``short_name`` attribute.
        """
        if not self._items:
            return

        full_names = [
            utils.get_valid_filename(occ.occurrence.fullPathName) for occ in self._items
        ]
        seg_lists: list[list[str]] = [name.split("_") for name in full_names]
        max_len = max(len(s) for s in seg_lists)

        # Pad shorter lists with None so all rows have the same width.
        padded: list[list[str | None]] = [
            segs + [None] * (max_len - len(segs)) for segs in seg_lists
        ]
        n = len(padded)

        # Per-name retained positions, each seeded with the name's last segment.
        kept: list[set[int]] = [{len(segs) - 1} for segs in seg_lists]

        def _short_name(i: int) -> str:
            segs = seg_lists[i]
            return "_".join(segs[p] for p in sorted(kept[i]) if p < len(segs))

        while True:
            # Group indices by their current projected short name.
            groups: dict[str, list[int]] = {}
            for i in range(n):
                groups.setdefault(_short_name(i), []).append(i)

            collision_groups = [g for g in groups.values() if len(g) > 1]
            if not collision_groups:
                break

            progress = False
            for group in collision_groups:
                # Positions already used by any member of this group.
                used = set().union(*(kept[i] for i in group))
                candidates = [p for p in range(max_len) if p not in used]
                if not candidates:
                    continue

                # Pick the position that creates the most distinct values
                # within this group; break ties by preferring the earliest.
                best_pos = min(
                    candidates,
                    key=lambda p: (-len({padded[i][p] for i in group}), p),
                )
                if len({padded[i][best_pos] for i in group}) < 2:
                    continue  # no position can split this group

                for i in group:
                    kept[i].add(best_pos)
                progress = True

            if not progress:
                break  # remaining collisions are unresolvable (truly identical names)

        # Assign final short names.
        for i, occ in enumerate(self._items):
            occ.short_name = _short_name(i)
