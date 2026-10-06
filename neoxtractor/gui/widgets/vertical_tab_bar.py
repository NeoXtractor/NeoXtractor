"""Provides a custom vertical tab bar for use in a tab widget."""

from PySide6 import QtCore, QtWidgets


class VerticalTabBar(QtWidgets.QTabBar):
    """A custom tab bar that displays tabs vertically."""

    def __init__(self, parent=None):
        super().__init__(parent)

    def tabSizeHint(self, index):
        size = super().tabSizeHint(index)
        size.transpose()
        return size

    def paintEvent(self, event):
        painter = QtWidgets.QStylePainter(self)
        opt = QtWidgets.QStyleOptionTab()
        for index in range(self.count()):
            self.initStyleOption(opt, index)
            painter.drawControl(QtWidgets.QStyle.ControlElement.CE_TabBarTabShape, opt)
            painter.save()

            # PySide6 has incorrect typing
            size: QtCore.QSize = opt.rect.size()  # type: ignore[attr-defined]
            size.transpose()
            rect = QtCore.QRect(QtCore.QPoint(), size)
            rect.moveCenter(opt.rect.center())  # type: ignore[attr-defined]
            opt.rect = rect  # type: ignore[attr-defined]

            center = self.tabRect(index).center()
            painter.translate(center)
            painter.rotate(90)
            painter.translate(-center)
            painter.drawControl(QtWidgets.QStyle.ControlElement.CE_TabBarTabLabel, opt)
            painter.restore()
        return
