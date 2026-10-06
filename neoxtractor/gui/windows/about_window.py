"""Provides about window."""

from PySide6 import QtCore, QtGui, QtWidgets

from neoxtractor.core.build_info import BuildInfo


class LogoWidget(QtWidgets.QWidget):
    """Custom widget that draws the NeoXtractor logo with perfect text positioning"""

    def __init__(self, parent=None):
        super().__init__(parent)

        # Create fonts
        self.normal_font = QtGui.QFont()
        self.normal_font.setFamily("Orbitron")
        self.normal_font.setPointSize(24)
        self.normal_font.setWeight(QtGui.QFont.Weight.ExtraLight)

        self.x_font = QtGui.QFont()
        self.x_font.setFamily("Noto Sans SemiBold")
        self.x_font.setPointSize(64)
        self.x_font.setWeight(QtGui.QFont.Weight.DemiBold)
        self.x_font.setItalic(True)

        self.ver_font = QtGui.QFont()
        self.ver_font.setFamily("Roboto")
        self.ver_font.setPointSize(10)
        self.ver_font.setWeight(QtGui.QFont.Weight.ExtraLight)

        self.version_text = f"v{BuildInfo.version}" if BuildInfo.version else "DEV"

        # Pre-calculate text widths for optimal positioning
        self.update_metrics()

    def update_metrics(self):
        """Calculate text metrics for precise positioning"""
        fm_normal = QtGui.QFontMetrics(self.normal_font)
        fm_x = QtGui.QFontMetrics(self.x_font)
        fm_ver = QtGui.QFontMetrics(self.ver_font)

        self.neo_width = fm_normal.horizontalAdvance("Neo")
        self.x_width = fm_x.horizontalAdvance("X") + 10  # italic
        self.x_height = fm_x.height()
        self.tractor_width = fm_normal.horizontalAdvance("tractor")
        self.normal_height = fm_normal.height()
        self.ver_width = fm_ver.horizontalAdvance(self.version_text)
        self.ver_height = fm_ver.height()

        # Calculate total width needed
        self.total_width = self.neo_width + self.x_width + self.tractor_width - 25

    def sizeHint(self):
        """Return the preferred size of the widget"""
        return QtCore.QSize(self.total_width, max(self.normal_height, self.x_height))

    def paintEvent(self, event):
        """Custom paint event to draw the logo text"""
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)

        # Center the text horizontally in the widget
        total_width = self.width()
        x_start = (total_width - self.total_width) // 2

        # Neo
        painter.setFont(self.normal_font)
        neo_rect = QtCore.QRect(x_start, 20, self.neo_width, self.normal_height)
        painter.drawText(
            neo_rect,
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop,
            "Neo",
        )
        # painter.drawText(neo_rect, QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignBottom, "Neo")
        text_color = painter.pen().color()

        # X
        painter.setFont(self.x_font)
        painter.setPen(QtGui.QColor("#EE0000"))
        x_rect = QtCore.QRect(
            x_start + self.neo_width - 10, -15, self.x_width, self.x_height
        )
        painter.drawText(
            x_rect,
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop,
            "X",
        )

        # tractor
        painter.setFont(self.normal_font)
        painter.setPen(text_color)
        tractor_rect = QtCore.QRect(
            x_start + self.neo_width + self.x_width - 25,
            30,
            self.tractor_width,
            self.normal_height,
        )
        painter.drawText(
            tractor_rect,
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop,
            "tractor",
        )

        # Version
        painter.setFont(self.ver_font)
        ver_rect = QtCore.QRect(
            x_start
            + self.neo_width
            + self.x_width
            - 30
            + self.tractor_width
            - self.ver_width,
            30 - self.ver_height + 8,
            self.ver_width,
            self.ver_height,
        )
        painter.drawText(
            ver_rect,
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignBottom,
            self.version_text,
        )


class AboutWindow(QtWidgets.QDialog):
    """About window that displays information about the application."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About NeoXtractor")
        self.setFixedSize(450, 300)
        self.setWindowFlags(
            self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint
        )

        layout = QtWidgets.QVBoxLayout()

        app_name_layout = QtWidgets.QHBoxLayout()
        app_name_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        logo_widget = LogoWidget()
        app_name_layout.addWidget(logo_widget)

        # Description
        description = QtWidgets.QLabel("A tool for extracting data from NPK files.")
        description.setWordWrap(True)
        description.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        # Copyright info
        copyright_info = QtWidgets.QLabel(
            "© 2026 MarcosVLl2 & contributors\nNeoX is an game engine developed by Netease. NeoXtractor is not affiliated with Netease.\nThis project is limited for educational purposes."
        )
        copyright_info.setWordWrap(True)
        copyright_info.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        # Add widgets to main layout
        layout.addSpacing(10)
        layout.addLayout(app_name_layout)
        layout.addSpacing(10)
        layout.addWidget(description)
        layout.addStretch()

        if (
            BuildInfo.commit_hash is not None and BuildInfo.branch is not None
        ) or BuildInfo.build_time is not None:
            if BuildInfo.commit_hash is not None and BuildInfo.branch is not None:
                build = QtWidgets.QLabel(
                    f"Build: {BuildInfo.commit_hash[:7]} (at {BuildInfo.branch})"
                )
                build.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                layout.addWidget(build)
            if BuildInfo.build_time is not None:
                build_time = QtWidgets.QLabel(
                    f"Build time: {BuildInfo.build_time.strftime('%Y-%m-%d %H:%M:%S')}"
                )
                build_time.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                layout.addWidget(build_time)
            layout.addStretch()

        layout.addWidget(copyright_info)

        close_button = QtWidgets.QPushButton("Close")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)

        self.setLayout(layout)
