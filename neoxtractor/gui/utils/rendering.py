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


def grid(size: float, steps: int, cell: float | None = None):
    """
    Generate a 3D grid of line segments for visualization purposes.
    Creates a grid pattern by generating horizontal and vertical line segments
    that form a square grid in the XZ plane (Y=0).
    Args:
        size (float): Half the size of the grid (distance from center to edge).
                     The grid will extend from -size to +size in both X and Z directions.
        steps (int): Number of grid lines in each direction when ``cell`` is None.
        cell (float | None): When given, lines are spaced exactly ``cell`` apart
                     (size is rounded up to a whole number of cells), so the grid
                     doubles as a fixed-unit scale reference.
    Returns:
        numpy.ndarray: Array of shape (2*steps, 2, 3) containing line segment endpoints.
                      Each line segment is represented by two 3D points (start and end).
                      The grid lies in the XZ plane with Y coordinates set to 0.
    Example:
        >>> grid_lines = grid(10.0, 5)
        >>> print(grid_lines.shape)
        (10, 2, 3)
    """
    if cell is not None and cell > 0:
        n = max(1, int(np.ceil(size / cell)))
        coords = np.arange(-n, n + 1) * cell
        extent = n * cell
    else:
        coords = np.linspace(-size, size, steps)
        extent = size
    u = np.repeat(coords, 2)
    v = np.tile([-extent, extent], len(coords))
    w = np.zeros(len(coords) * 2)
    return np.concatenate([np.dstack([u, w, v]), np.dstack([v, w, u])])
