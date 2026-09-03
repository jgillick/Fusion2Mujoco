# -*- coding: utf-8 -*-
"""
Get information about joints from the Fusion 360 API.
"""

from typing import Union
import adsk, adsk.fusion
from . import math_operation as math_op

# Maps Fusion joint types to MuJoCo joint types
_JOINT_TYPE_MAP = {
    adsk.fusion.JointTypes.RigidJointType: None,
    adsk.fusion.JointTypes.RevoluteJointType: "hinge",
    adsk.fusion.JointTypes.SliderJointType: "slide",
}


class Joint:
    """
    Wraps a Fusion 360 joint and extracts the information MuJoCo needs.

    A joint connects two component occurrences — a parent and a child — and
    constrains how they can move relative to each other. This class supports
    three kinds of joints:

    - Rigid: the two parts are fixed together and cannot move at all.
    - Revolute (hinge): the child can rotate around a single axis.
    - Slider: the child can slide along a single axis.

    The class works with both standard Fusion joints and "as-built" joints
    (joints defined after assembly rather than during modelling).
    """

    def __init__(
        self,
        joint: Union[adsk.fusion.Joint, adsk.fusion.AsBuiltJoint],
        parent: adsk.fusion.Occurrence | None = None,
        child: adsk.fusion.Occurrence | None = None,
    ) -> None:
        """
        Args:
            joint: The Fusion joint (or joint proxy) to wrap.
            parent: Root-context occurrence to use as the parent link instead
                of ``joint.occurrenceTwo``. Joint proxies report their
                occurrences in the owning component's context, so callers that
                know the root-context occurrences should pass them here.
            child: Root-context occurrence to use as the child link instead of
                ``joint.occurrenceOne``.
        """
        self.joint = joint
        try:
            self.parent = parent if parent is not None else joint.occurrenceTwo
            self.child = child if child is not None else joint.occurrenceOne
        except Exception as e:
            raise ValueError(f"Invalid joint: {joint.name}: {str(e)}")

    @property
    def name(self) -> str:
        return self.joint.name

    @property
    def is_as_built(self) -> bool:
        """
        True if this wraps an as-built joint (``adsk.fusion.AsBuiltJoint``)
        rather than a standard joint (``adsk.fusion.Joint``).
        """
        return self.joint.objectType == adsk.fusion.AsBuiltJoint.classType()

    def _attachment_point(self) -> list[float]:
        """
        Return the joint's attachment point in root-component space, in
        Fusion's native centimeters.

        As-built joints carry a single ``geometry``; standard joints carry a
        geometry (or joint origin) per side, of which the parent side
        (``geometryOrOriginTwo``) is used.

        Returns:
            list[float]: ``[x, y, z]`` in centimeters, in root-component
                (world) space.

        Raises:
            ValueError: If an as-built joint has no geometry (only rigid
                as-built joints, which never need an origin).
        """
        if self.is_as_built:
            geometry = self.joint.geometry
            if geometry is None:
                # Only rigid as-built joints have no geometry, and those never
                # need an origin because no <joint> element is emitted.
                raise ValueError(f"As-built joint '{self.name}' has no geometry")
            return geometry.origin.asArray()

        geom_or_origin = self.joint.geometryOrOriginTwo
        joint_origin = adsk.fusion.JointOrigin.cast(geom_or_origin)
        if joint_origin is not None:
            return joint_origin.geometry.origin.asArray()
        return geom_or_origin.origin.asArray()

    def get_origin(self) -> list:
        """
        Find the joint attachment point expressed in the child link's own frame.

        MuJoCo defines a joint's position relative to the body it is attached
        to (the child link). This function computes that offset: starting from
        where the child link's origin sits in the world, how far and in which
        direction do you need to travel — using the child link's own axes — to
        reach the joint's attachment point?

        Returns:
            list: [x, y, z] offset in meters, expressed in the child link's
                local coordinate frame.
        """
        w_P_Jc = [round(i * 0.01, 6) for i in self._attachment_point()]
        w_P_Lc = math_op.matrix3d_to_pos(self.child.transform2)

        w_V_LcJc = [[w_P_Jc[i] - w_P_Lc[i]] for i in range(3)]

        w_R_Lc = math_op.get_rotation_matrix(self.child.transform2)
        Lc_R_w = math_op.matrix_transpose(w_R_Lc)
        Lc_V_LcJc = math_op.change_orientation(Lc_R_w, w_V_LcJc)

        return [row[0] for row in Lc_V_LcJc]

    def get_joint_type(self) -> str | None:
        """
        Return the MuJoCo joint type string for this joint.

        MuJoCo uses the following strings to describe joint motion:

        - ``None``    — rigid (no movement allowed)
        - ``"hinge"`` — rotation around a single axis (revolute joint)
        - ``"slide"`` — translation along a single axis (prismatic joint)

        Any Fusion joint type not in this set (e.g. cylindrical, ball) returns
        ``None`` and will be treated as rigid by the caller.

        Returns:
            str | None: The MuJoCo joint type string, or ``None`` for
                rigid/unknown joints.
        """
        return _JOINT_TYPE_MAP.get(self.joint.jointMotion.jointType)

    def get_limits(self) -> list | None:
        """
        Return the joint's motion limits, or ``None`` if the joint is unlimited
        or rigid.

        For hinge joints the limits are angles in radians. For slider joints
        they are distances in meters. Both are returned as
        ``[lower_limit, upper_limit]``.

        Limits are only returned when both the minimum and maximum are
        explicitly enabled in Fusion. If either is disabled, ``None`` is
        returned.

        Returns:
            list | None: ``[lower_limit, upper_limit]``, or ``None`` if the
                joint is rigid or has no limits configured.
        """
        motion = self.joint.jointMotion

        if motion.jointType == adsk.fusion.JointTypes.RigidJointType:
            return None

        if motion.jointType == adsk.fusion.JointTypes.RevoluteJointType:
            limits = motion.rotationLimits
            if limits.isMaximumValueEnabled and limits.isMinimumValueEnabled:
                return [round(limits.minimumValue, 6), round(limits.maximumValue, 6)]
            return None

        if motion.jointType == adsk.fusion.JointTypes.SliderJointType:
            limits = motion.slideLimits
            if limits.isMaximumValueEnabled and limits.isMinimumValueEnabled:
                return [
                    round(limits.minimumValue * 0.01, 6),  # cm -> m
                    round(limits.maximumValue * 0.01, 6),  # cm -> m
                ]
            return None

        return None

    def get_axis(self) -> list | None:
        """
        Return the joint's motion axis expressed in the child link's frame.

        For a hinge joint this is the axis the child rotates around. For a
        slider joint it is the direction the child slides along. The result is
        expressed using the child link's own coordinate axes rather than world
        axes — this is the convention MuJoCo expects.

        Returns ``None`` for rigid joints, which have no meaningful axis.

        Returns:
            list | None: [x, y, z] unit vector in the child link's local
                frame, or ``None`` for rigid joints.
        """
        motion = self.joint.jointMotion
        axis = None

        if motion.jointType == adsk.fusion.JointTypes.RevoluteJointType:
            axis = [round(v, 6) for v in motion.rotationAxisVector.asArray()]
        elif motion.jointType == adsk.fusion.JointTypes.SliderJointType:
            axis = [round(v, 6) for v in motion.slideDirectionVector.asArray()]

        if axis is None:
            return None

        axis_col = [[v] for v in axis]
        child_rotation = math_op.get_rotation_matrix(self.child.transform2)
        child_R_world = math_op.matrix_transpose(child_rotation)
        result_col = math_op.change_orientation(child_R_world, axis_col)
        return [row[0] for row in result_col]

    @staticmethod
    def collect_joints(root: adsk.fusion.Component) -> list["Joint"]:
        """
        Return every usable joint in the design, both standard and as-built,
        wrapped as ``Joint`` objects whose occurrences and geometry are
        expressed in the root assembly context.

        Joints defined at the root level are used natively. Joints defined
        inside a subcomponent are reported by Fusion in the owning
        component's own context, so each one is wrapped in a root-context
        proxy with ``createForAssemblyContext`` — one per occurrence of the
        owning component, since every instance of the component carries its
        own copy of the joint. Even a joint proxy still reports its
        ``occurrenceOne``/``occurrenceTwo`` in the owning component's
        context, so the root-context occurrences are resolved separately by
        prefixing each occurrence path with the owning occurrence's path and
        looking it up in ``root.allOccurrences``.

        Joints are skipped when they are suppressed or when either occurrence
        is missing (a standard joint grounded directly to the root component
        has no ``occurrenceTwo``).

        Args:
            root (adsk.fusion.Component): The root component of the design.

        Returns:
            list[Joint]: Root-level joints first, then nested joints in
                occurrence-tree order.
        """
        occ_by_path: dict[str, adsk.fusion.Occurrence] = {
            occ.fullPathName: occ for occ in root.allOccurrences
        }
        joints: list[Joint] = []

        def resolve_occurrence(
            occ: adsk.fusion.Occurrence, context: adsk.fusion.Occurrence
        ) -> adsk.fusion.Occurrence | None:
            """
            Return the root-context occurrence for ``occ``, an occurrence a
            nested joint reports in its owning component's context, by
            prefixing its path with the context occurrence's path.
            """
            prefix = context.fullPathName + "+"
            if occ.fullPathName.startswith(prefix):
                return occ  # already in root context
            return occ_by_path.get(prefix + occ.fullPathName)

        def append_joint(fusion_joint, context: adsk.fusion.Occurrence | None):
            if fusion_joint.isSuppressed:
                return
            child = fusion_joint.occurrenceOne
            parent = fusion_joint.occurrenceTwo
            if child is None or parent is None:
                return
            if context is not None:
                child = resolve_occurrence(child, context)
                parent = resolve_occurrence(parent, context)
                if child is None or parent is None:
                    return
            joints.append(Joint(fusion_joint, parent=parent, child=child))

        for fusion_joint in list(root.joints) + list(root.asBuiltJoints):
            append_joint(fusion_joint, None)

        for occ in occ_by_path.values():
            comp = occ.component
            for nested_joint in list(comp.joints) + list(comp.asBuiltJoints):
                # The proxy expresses the joint's geometry and axis vectors in
                # root coordinates; fall back to the native joint if Fusion
                # cannot create one (only matters for non-rigid joints).
                proxy = nested_joint.createForAssemblyContext(occ)
                append_joint(proxy or nested_joint, occ)

        return joints
