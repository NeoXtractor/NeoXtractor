"""Utilities for rendering."""

import numpy as np
from PySide6 import QtGui, QtWidgets


def is_d3d(widget: QtWidgets.QRhiWidget) -> bool:
    """
    Check if the QRhiWidget is using Direct3D API.

    Args:
        widget (QtWidgets.QRhiWidget): The widget to check.

    Returns:
        bool: True if the widget is using Direct3D, False otherwise.
    """
    return (
        widget.api() == QtWidgets.QRhiWidget.Api.Direct3D11
        or widget.api() == QtWidgets.QRhiWidget.Api.Direct3D12
    )


def static_uniform_buffer_type(widget: QtWidgets.QRhiWidget) -> QtGui.QRhiBuffer.Type:
    """
    Shorthand function for using static uniform buffer when possible.

    In Direct3D, uniform buffers must use Dynamic buffers.
    """
    return (
        QtGui.QRhiBuffer.Type.Dynamic
        if is_d3d(widget)
        else QtGui.QRhiBuffer.Type.Immutable
    )


def grid(size: int, steps: int):
    """
    Generate a 3D grid of line segments for visualization purposes.
    Creates a grid pattern by generating horizontal and vertical line segments
    that form a square grid in the XZ plane (Y=0).
    Args:
        size (float): Half the size of the grid (distance from center to edge).
                     The grid will extend from -size to +size in both X and Z directions.
        steps (int): Number of grid lines in each direction. Total lines will be 2*steps
                    (steps horizontal + steps vertical).
    Returns:
        numpy.ndarray: Array of shape (2*steps, 2, 3) containing line segment endpoints.
                      Each line segment is represented by two 3D points (start and end).
                      The grid lies in the XZ plane with Y coordinates set to 0.
    Example:
        >>> grid_lines = grid(10.0, 5)
        >>> print(grid_lines.shape)
        (10, 2, 3)
    """

    u = np.repeat(np.linspace(-size, size, steps), 2)
    v = np.tile([-size, size], steps)
    w = np.zeros(steps * 2)
    return np.concatenate([np.dstack([u, w, v]), np.dstack([v, w, u])])
