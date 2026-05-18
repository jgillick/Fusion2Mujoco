import adsk, adsk.core
import math
import numpy as np


def matrix3d_to_pos(matrix: adsk.core.Matrix3D) -> tuple[float, float, float]:
    """
    Convert a Fusion translation matrix from centimeters to meters.

    Args:
        matrix : adsk.core.Matrix3D
            A Fusion 360 homogeneous transform matrix.

    Returns:
        tuple[float, float, float]
            The (x, y, z) position in meters.
    """
    return (
        matrix.translation.x * 0.01,
        matrix.translation.y * 0.01,
        matrix.translation.z * 0.01,
    )


def matrix3d_to_quat(matrix: adsk.core.Matrix3D) -> list:
    """
    Convert a Fusion 360 transform matrix into a MuJoCo-compatible pose.

    A "pose" describes where something is in space — its position (x, y, z)
    and its orientation. This function expresses orientation as a quaternion:
    a compact set of four numbers (w, x, y, z) that uniquely defines a 3D
    rotation without suffering from "gimbal lock" (a degeneracy that can occur
    with Euler angles). MuJoCo requires scalar-first ordering (w x y z).

    The rotation is computed using Shepperd's method, which selects the most
    numerically stable formula based on the dominant diagonal entry of the
    rotation matrix.

    Args:
        matrix : adsk.core.Matrix3D
        A Fusion 360 homogeneous transform matrix representing position and
        orientation in 3D space.

    Returns:
        list
        [qw, qx, qy, qz] — quaternion components in MuJoCo's scalar-first convention.
    """
    r00 = matrix.getCell(0, 0)
    r01 = matrix.getCell(0, 1)
    r02 = matrix.getCell(0, 2)
    r10 = matrix.getCell(1, 0)
    r11 = matrix.getCell(1, 1)
    r12 = matrix.getCell(1, 2)
    r20 = matrix.getCell(2, 0)
    r21 = matrix.getCell(2, 1)
    r22 = matrix.getCell(2, 2)

    trace = r00 + r11 + r22

    # Shepperd's method — numerically stable across all orientations
    if trace > 0:
        s = 0.5 / math.sqrt(trace + 1.0)
        qw = 0.25 / s
        qx = (r21 - r12) * s
        qy = (r02 - r20) * s
        qz = (r10 - r01) * s
    elif r00 > r11 and r00 > r22:
        s = 2.0 * math.sqrt(1.0 + r00 - r11 - r22)
        qw = (r21 - r12) / s
        qx = 0.25 * s
        qy = (r01 + r10) / s
        qz = (r02 + r20) / s
    elif r11 > r22:
        s = 2.0 * math.sqrt(1.0 + r11 - r00 - r22)
        qw = (r02 - r20) / s
        qx = (r01 + r10) / s
        qy = 0.25 * s
        qz = (r12 + r21) / s
    else:
        s = 2.0 * math.sqrt(1.0 + r22 - r00 - r11)
        qw = (r10 - r01) / s
        qx = (r02 + r20) / s
        qy = (r12 + r21) / s
        qz = 0.25 * s

    return [qw, qx, qy, qz]


def coordinate_transform(
    w_T_from: adsk.core.Matrix3D, w_T_to: adsk.core.Matrix3D
) -> adsk.core.Matrix3D:
    """
    Compute how one coordinate frame relates to another in the same world frame.

    Imagine two objects in a room, each with their own local coordinate system
    ("frame"). Both frames are described relative to the room (the "world
    frame"). This function answers: if you are standing inside `from_frame`,
    where is `to_frame` relative to you?

    Mathematically: from_T_to = inv(w_T_from) * w_T_to

    Neither input matrix is modified.

    Args:
        w_T_from : adsk.core.Matrix3D
        Transform of the "from" frame expressed in the world frame (i.e. how
        is this frame positioned and oriented in the world?).
    w_T_to : adsk.core.Matrix3D
        Transform of the "to" frame expressed in the world frame.

    Returns:
        adsk.core.Matrix3D
        The transform of `to_frame` expressed in `from_frame`'s local
        coordinates.
    """
    mat_from = np.array([[w_T_from.getCell(i, j) for j in range(4)] for i in range(4)])
    mat_to = np.array([[w_T_to.getCell(i, j) for j in range(4)] for i in range(4)])
    result = np.linalg.inv(mat_from) @ mat_to

    from_T_to = adsk.core.Matrix3D.create()
    for i in range(4):
        for j in range(4):
            from_T_to.setCell(i, j, float(result[i, j]))

    return from_T_to


def change_orientation(a_R_b: list, b_v: list) -> list:
    """
    Re-express a 3D vector from one coordinate frame into another.

    Imagine you have a vector — for example, an arrow pointing in some
    direction — described using frame B's axes. This function returns the same
    arrow described using frame A's axes. The arrow itself does not move; only
    the description changes.

    The vector is passed and returned in "column-vector" format: a list of
    three single-element lists, i.e. [[vx], [vy], [vz]].

    Args:
        a_R_b : list
        A 3x3 rotation matrix (list of rows) whose columns are frame B's unit
        axes expressed in frame A. It encodes the relative orientation between
        the two frames.
        b_v : list
        A 3x1 column vector [[vx], [vy], [vz]] expressed in frame B.

    Returns:
        list
        A 3x1 column vector [[vx], [vy], [vz]] of the same vector expressed
        in frame A.
    """
    flat_v = [row[0] for row in b_v]
    result = np.dot(a_R_b, flat_v)
    return [[float(v)] for v in result]


def get_rotation_matrix(transform: adsk.core.Matrix3D) -> list:
    """
    Extract the 3x3 rotation portion from a Fusion 360 4x4 transform matrix.

    A transform matrix encodes both rotation and translation (position) in a
    single 4x4 grid. This function discards the translation and returns only
    the top-left 3x3 block, which describes how the object is rotated in space.
    Each column of this matrix is one of the object's local axes expressed in
    world coordinates.

    Args:
        transform : adsk.core.Matrix3D
            A Fusion 360 homogeneous transform matrix.

    Returns:
        list
            A 3x3 nested list (list of rows) representing the rotation matrix.
    """
    return np.array(
        [[transform.getCell(i, j) for j in range(3)] for i in range(3)]
    ).tolist()


def matrix_transpose(M: list) -> list:
    """
    Flip a matrix along its diagonal, swapping rows and columns.

    For a rotation matrix, the transpose is also its inverse — if R rotates
    from frame A to frame B, then R^T rotates from frame B back to frame A.
    This property is used throughout the codebase to switch between reference
    frames without a costly general matrix inversion.

    Args:
        M : list
            A 3x3 matrix represented as a list of rows.

    Returns:
        list
            The transposed 3x3 matrix as a list of rows.
    """
    return np.array(M).T.tolist()


def matrix_multiply(M1: list, M2: list) -> list:
    """
    Multiply two 3x3 matrices together.

    In 3D geometry, multiplying rotation matrices is how you chain rotations:
    if R1 rotates from frame A to frame B, and R2 rotates from frame B to
    frame C, then R1 @ R2 rotates directly from frame A to frame C.

    Note: matrix multiplication is not commutative — M1 @ M2 ≠ M2 @ M1 in
    general.

    Args:
        M1 : list
            A 3x3 matrix (list of rows).
        M2 : list
            A 3x3 matrix (list of rows).

    Returns:
        list
            The 3x3 product M1 x M2 as a list of rows.
    """
    return np.matmul(M1, M2).tolist()
