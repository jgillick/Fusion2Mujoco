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
        self, joint: Union[adsk.fusion.Joint, adsk.fusion.AsBuiltJoint]
    ) -> None:
        self.joint = joint
        try:
            self.parent = joint.occurrenceTwo  # parent link of joint
            self.child = joint.occurrenceOne
        except Exception as e:
            raise ValueError(f"Invalid joint: {joint.name}: {str(e)}")

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
        if hasattr(self.joint, "geometry"):
            w_P_Jc = self.joint.geometry.origin.asArray()
        elif self.joint.geometryOrOriginTwo == adsk.fusion.JointOrigin:
            w_P_Jc = self.joint.geometryOrOriginTwo.geometry.origin.asArray()
        else:
            w_P_Jc = self.joint.geometryOrOriginTwo.origin.asArray()

        w_P_Jc = [round(i * 0.01, 6) for i in w_P_Jc]
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
