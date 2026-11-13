"""Provides a 3D camera class."""

import math
from enum import IntEnum

from PySide6.QtGui import QMatrix4x4, QVector3D, QVector4D


class OrthogonalDirection(IntEnum):
    """
    Enumeration defining standard orthogonal viewing directions for 3D camera positioning.
    This enum provides predefined integer values for common orthogonal camera orientations
    used in 3D mesh viewing applications. Each direction represents a standard viewpoint
    from which to observe 3D objects.
    Attributes:
        FRONT (int): Front-facing view direction (value: 1)
        RIGHT (int): Right-side view direction (value: 3)
        TOP (int): Top-down view direction (value: 7)
    """

    FRONT = 1
    RIGHT = 3
    TOP = 7


class Camera:
    """
    A 3D camera class for mesh viewing with support for perspective and orthographic projections.
    This camera implementation provides functionality for 3D scene navigation including:
    - Orbital camera movement around a target point
    - Pan and zoom operations
    - Perspective and orthographic projection modes
    - Euler angle rotation handling
    - View and projection matrix generation
    The camera uses Qt's QVector4D and QMatrix4x4 classes for 3D math operations
    and supports both free-form movement and constrained orthogonal views.
    Attributes:
        pitch (float): Rotation around X-axis in degrees
        yaw (float): Rotation around Y-axis in degrees
        roll (float): Rotation around Z-axis in degrees
        dist (float): Distance from camera to target point
        fov_y (float): Vertical field of view in degrees for perspective projection
        aspect_ratio (float): Width/height ratio for projection calculations
        perspective (bool): True for perspective projection, False for orthographic
        min_dist (float): Minimum allowed distance for zoom operations
        max_dist (float): Maximum allowed distance for zoom operations
    Example:
        camera = Camera()
        camera.set_aspect_ratio(800, 600)
        camera.orbit(10, 5)  # Rotate camera
        camera.dolly(-2)     # Zoom in
        view_matrix = camera.view()
        proj_matrix = camera.proj()
    """

    def __init__(self):
        self._pos = QVector4D(0, 1, 4, 1)
        self.pitch = 0.0
        self.yaw = 0.0
        self.roll = 0.0
        self._dist = 5
        self.fov_y = 45
        self._vp_size = (1, 1)
        self.aspect_ratio = 1
        self.perspective = True
        self.min_dist = 5
        self.max_dist = 400000

    @property
    def dist(self):
        """Return current distance from camera to target."""
        return self._dist

    @dist.setter
    def dist(self, value):
        """Set distance, ensuring it stays within min/max bounds."""
        self._dist = max(self.min_dist, min(value, self.max_dist))

    def move(self, velocity: QVector4D):
        """Update camera position based on velocity."""
        vec = self.rot().inverted()[0].map(velocity)
        self._pos += vec

    def pos(self):
        """Return camera position."""
        return self._pos

    def euler(self):
        """Return euler angles in radians as QVector3D"""
        roll = math.radians(self.roll)
        pitch = math.radians(self.pitch)
        yaw = math.radians(self.yaw)
        return QVector3D(pitch, yaw, roll)

    def rot(self):
        """Return rotation matrix"""
        matrix = QMatrix4x4()
        euler = self.euler()
        matrix.rotate(math.degrees(euler.x()), QVector3D(1, 0, 0))  # pitch
        matrix.rotate(math.degrees(euler.y()), QVector3D(0, 1, 0))  # yaw
        matrix.rotate(math.degrees(euler.z()), QVector3D(0, 0, 1))  # roll
        return matrix

    def view(self):
        """Return view matrix"""
        view = QMatrix4x4()
        view.translate(-self._pos.toVector3D())
        view = self.rot() * view

        # Apply distance translation by multiplying from the left
        dist_matrix = QMatrix4x4()
        dist_matrix.translate(QVector3D(0.0, 0.0, -self.dist))
        view = dist_matrix * view

        return view

    def proj(self):
        """Return projection matrix"""
        far_clip = 500000.0  # <aex>1 unit size is 0.065555 in NeoX
        proj = QMatrix4x4()
        if self.perspective:
            proj.perspective(self.fov_y, self.aspect_ratio, 0.1, far_clip)
        else:
            length = math.tan(math.radians(self.fov_y / 2)) * abs(self.dist)
            if self.aspect_ratio >= 1:
                proj.ortho(
                    -length * self.aspect_ratio,
                    length * self.aspect_ratio,
                    -length,
                    length,
                    0.1,
                    far_clip,
                )
            else:
                proj.ortho(
                    -length,
                    length,
                    -length / self.aspect_ratio,
                    length / self.aspect_ratio,
                    0.1,
                    far_clip,
                )
        return proj

    def view_proj(self):
        """Return combined view-projection matrix"""
        return self.proj() * self.view()

    def dolly(self, amount):
        """Zoom in/out by adjusting distance"""
        self.dist *= amount
        self.dist = max(self.min_dist, min(self.dist, self.max_dist))

    def orbit(self, dx, dy):
        """Orbit camera around target"""
        self.perspective = True
        self.yaw -= dx * 0.5
        self.pitch = max(-89.0, min(89.0, self.pitch - dy * 0.5))

    def pan(self, dx_pix: float, dy_pix: float):
        """Pan camera by moving in screen space"""
        fov_rad = math.radians(self.fov_y)
        per_pix_y = 2.0 * self.dist * math.tan(fov_rad * 0.5) / self._vp_size[1]
        per_pix_x = per_pix_y * self.aspect_ratio

        right_amount = -dx_pix * per_pix_x
        up_amount = dy_pix * per_pix_y

        rot_inv = self.rot().inverted()[0]
        right = rot_inv.map(QVector4D(1, 0, 0, 0))
        up = rot_inv.map(QVector4D(0, 1, 0, 0))

        offset = right * right_amount + up * up_amount

        self._pos += offset

    def orthogonal(self, direct: OrthogonalDirection, opposite=False):
        """Set camera to orthogonal view"""
        self.perspective = False
        self.yaw, self.pitch, self.roll = 0.0, 0.0, 0.0
        if direct == OrthogonalDirection.FRONT:
            self.yaw = 0.0 if not opposite else 180.0
        elif direct == OrthogonalDirection.RIGHT:
            self.yaw = 90.0 if not opposite else -90.0
        elif direct == OrthogonalDirection.TOP:
            self.pitch = -90.0 if not opposite else 90.0

    def focus(self, point: list | tuple | None = None):
        """Focus camera on a specific point"""
        if isinstance(point, (list, tuple)) and len(point) >= 3:
            focus_point = QVector4D(point[0], point[1], point[2], 1.0)
        else:
            focus_point = QVector4D(0.0, 0.0, 0.0, 1.0)

        self._pos = focus_point
        self.dist = math.sqrt(
            (self._pos.x() - focus_point.x()) ** 2
            + (self._pos.y() - focus_point.y()) ** 2
            + (self._pos.z() - focus_point.z()) ** 2
        )

    def set_aspect_ratio(self, width, height):
        """Set camera aspect ratio"""
        self._vp_size = (width, height)
        self.aspect_ratio = width / height
