"""Consistent theme colors for native file-list and character-tree viewports."""

from PySide6 import QtCore, QtGui, QtWidgets


def apply_file_list_theme(view, theme):
    app = QtWidgets.QApplication.instance()
    system_theme = theme is None or theme.get_current_theme() in (None, "system")
    # Follow the OS palette when the app uses the system theme.
    system_dark = system_theme and app is not None and (
        app.palette().color(QtGui.QPalette.ColorRole.Window).lightness() < 128
        or app.styleHints().colorScheme() == QtCore.Qt.ColorScheme.Dark)
    system_dark_colors = {
        "palette.surface": "#2d2d2d",
        "palette.text": "#ffffff",
        "palette.selected": "#404040",
        "palette.hover": "#383838",
    }

    def color(path, fallback):
        if system_dark:
            return system_dark_colors.get(path, fallback)
        if theme is None:
            return fallback
        return theme.get_color(path, fallback) or fallback

    background = color("palette.surface", "#f8f9fa")
    text = color("palette.text", "#212121")
    selected = color("palette.selected", "#e3f2fd")
    hover = color("palette.hover", "#f5f5f5")
    palette = view.palette()
    for role, value in ((QtGui.QPalette.ColorRole.Base, background),
                        (QtGui.QPalette.ColorRole.AlternateBase, background),
                        (QtGui.QPalette.ColorRole.Window, background),
                        (QtGui.QPalette.ColorRole.Text, text),
                        (QtGui.QPalette.ColorRole.WindowText, text),
                        (QtGui.QPalette.ColorRole.Highlight, selected),
                        (QtGui.QPalette.ColorRole.HighlightedText, text)):
        palette.setColor(role, QtGui.QColor(value))
    view.setAlternatingRowColors(False)
    view.setPalette(palette)
    view.setAutoFillBackground(True)
    view.viewport().setPalette(palette)
    view.viewport().setAutoFillBackground(True)
    # Native QStyle painters can ignore palette roles; the stylesheet keeps
    # rows, branches and the empty viewport the same color.
    view.setStyleSheet(f"""
        QListView, QTreeWidget {{ background-color: {background};
            alternate-background-color: {background}; color: {text};
            selection-background-color: {selected}; selection-color: {text}; }}
        QListView::item:selected, QTreeWidget::item:selected {{
            background-color: {selected}; color: {text}; }}
        QListView::item:hover:!selected, QTreeWidget::item:hover:!selected {{
            background-color: {hover}; }}
        QTreeWidget::branch {{ background-color: {background}; }}
        QTreeWidget::branch:selected {{ background-color: {selected}; }}
    """)
    view.viewport().setStyleSheet(f"background-color: {background}; color: {text};")
    view.viewport().update()
