"""Provides HexArea widget."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from PySide6 import QtCore, QtGui, QtWidgets


@dataclass
class HexAreaColors:
    """Colors used in HexArea widget."""

    header_color_begin: QtGui.QColor = field(
        default_factory=lambda: QtGui.QColor(240, 240, 240)
    )
    header_color_end: QtGui.QColor = field(
        default_factory=lambda: QtGui.QColor(220, 220, 220)
    )
    header_separator_color: QtGui.QColor = field(
        default_factory=lambda: QtGui.QColor(180, 180, 180)
    )
    header_text_color: QtGui.QColor = field(
        default_factory=lambda: QtGui.QColor(80, 80, 80)
    )

    address_color: QtGui.QColor = field(
        default_factory=lambda: QtGui.QColor(80, 80, 80)
    )
    hex_color: QtGui.QColor = field(default_factory=lambda: QtGui.QColor(0, 0, 0))
    ascii_color: QtGui.QColor = field(default_factory=lambda: QtGui.QColor(0, 0, 180))
    highlight_bg_color: QtGui.QColor = field(
        default_factory=lambda: QtGui.QColor(220, 240, 255)
    )
    highlight_fg_color: QtGui.QColor = field(
        default_factory=lambda: QtGui.QColor(0, 0, 0)
    )
    selection_bg_color: QtGui.QColor = field(
        default_factory=lambda: QtGui.QColor(180, 220, 255)
    )
    selection_fg_color: QtGui.QColor = field(
        default_factory=lambda: QtGui.QColor(0, 0, 0)
    )
    row_color: QtGui.QColor = field(default_factory=lambda: QtGui.QColor(255, 255, 255))
    alternate_row_color: QtGui.QColor = field(
        default_factory=lambda: QtGui.QColor(245, 245, 245)
    )


class HexArea(QtWidgets.QWidget):
    """
    A widget that displays a hex view of the given data.
    """

    cursorPositionChanged = QtCore.Signal(int)  # Offset in bytes
    selectionChanged = QtCore.Signal(int, int)  # Start and end offsets

    # Data
    _data = bytearray()
    _addressing_base = 16
    _bytes_per_line = 16
    _bytes_per_group = 4
    _show_ascii = True

    # State
    _cursor_pos = 0
    _selection_start = -1
    _selection_end = -1
    _visible_lines = 0
    _total_width = 0
    _total_lines = 0

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)

        self.colors = HexAreaColors()

        self.setFocusPolicy(QtGui.Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

        self._main_layout = QtWidgets.QGridLayout(self)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        self._hex_widget = QtWidgets.QWidget(self)
        self._hex_widget.setStyleSheet("background-color: transparent;")

        self._main_layout.addWidget(self._hex_widget, 0, 0)

        self._v_scrollbar = QtWidgets.QScrollBar(QtCore.Qt.Orientation.Vertical, self)
        self._h_scrollbar = QtWidgets.QScrollBar(QtCore.Qt.Orientation.Horizontal, self)
        self._v_scrollbar.setVisible(False)
        self._v_scrollbar.valueChanged.connect(self.update)
        self._h_scrollbar.setVisible(False)
        self._h_scrollbar.valueChanged.connect(self.update)
        self._main_layout.addWidget(self._v_scrollbar, 0, 1)
        self._main_layout.addWidget(self._h_scrollbar, 1, 0)

        self._corner_widget = QtWidgets.QWidget()
        self._corner_widget.setFixedSize(
            self._v_scrollbar.width(), self._h_scrollbar.height()
        )
        self._corner_widget.setAutoFillBackground(True)
        self._main_layout.addWidget(self._corner_widget, 1, 1)

        self._measure_font_metrics()
        self._update_scrollbar()

    @property
    def data(self) -> bytearray:
        """Get the data being displayed."""
        return self._data

    @data.setter
    def data(self, value: bytearray):
        """Set the data to be displayed."""
        self._data = value
        self._cursor_pos = 0
        self._selection_start = -1
        self._selection_end = -1
        self._update_scrollbar()
        self.update()

    @property
    def addressing_base(self) -> int:
        """Get the addressing base."""
        return self._addressing_base

    @addressing_base.setter
    def addressing_base(self, value: int):
        """Set the addressing base."""
        if value not in (16, 10, 8):
            raise ValueError(
                "Addressing base must be 16 (hex), 10 (decimal), or 8 (octal)."
            )
        self._addressing_base = value
        self.update()

    @property
    def bytes_per_line(self) -> int:
        """Get the number of bytes per line."""
        return self._bytes_per_line

    @bytes_per_line.setter
    def bytes_per_line(self, value: int):
        """Set the number of bytes per line."""
        if value not in (8, 16, 32, 64):
            raise ValueError("Bytes per line must be 8, 16, 32, or 64.")
        self._bytes_per_line = value
        self._update_scrollbar()
        self.update()

    @property
    def bytes_per_group(self) -> int:
        """Get the number of bytes per group."""
        return self._bytes_per_group

    @bytes_per_group.setter
    def bytes_per_group(self, value: int):
        """Set the number of bytes per group."""
        self._bytes_per_group = value

    @property
    def show_ascii(self) -> bool:
        """Get whether to show ASCII representation."""
        return self._show_ascii

    @show_ascii.setter
    def show_ascii(self, value: bool):
        """Set whether to show ASCII representation."""
        self._show_ascii = value
        self._update_scrollbar()
        self.update()

    @property
    def cursor_byte_pos(self):
        """Get the cursor position in bytes."""
        return self._cursor_pos

    @property
    def cursor_pos(self):
        """Get the cursor position in the hex viewer."""
        column = self._cursor_pos // self._bytes_per_line
        row = self._cursor_pos - column * self._bytes_per_line
        return (row + 1, column + 1)

    def _update_scrollbar(self):
        """Update the vertical and horizontal scrollbars based on data size and viewport."""
        if len(self._data) == 0:
            self._v_scrollbar.setVisible(False)
            self._h_scrollbar.setVisible(False)
            self._corner_widget.setVisible(False)
            return

        # Calculate the width needed for full display
        address_width = self._calculate_address_width()
        hex_width = (
            self._char_width * 4
        ) * self._bytes_per_line  # Width for hex columns
        if self._bytes_per_group > 1:
            hex_width += (
                self._bytes_per_line // self._bytes_per_group - 1
            ) * self._char_width

        ascii_width = 0
        if self._show_ascii:
            ascii_width = int(self._char_width * 1.5) * self._bytes_per_line

        # Total content width with padding
        self._total_width = total_width = (
            address_width + hex_width + ascii_width + self._char_width * 2 + 40
        )

        # Calculate available width accounting for the vertical scrollbar
        available_width = self._hex_widget.width()

        # Horizontal scrollbar visibility and range
        if total_width <= available_width:
            self._h_scrollbar.setVisible(False)
            self._h_scrollbar.setValue(0)
        else:
            self._h_scrollbar.setRange(0, total_width - available_width)
            self._h_scrollbar.setPageStep(available_width)
            self._h_scrollbar.setSingleStep(
                self._char_width * 4
            )  # Scroll by 4 character widths
            self._h_scrollbar.setVisible(True)

            # Ensure current position is valid
            if self._h_scrollbar.value() > total_width - available_width:
                self._h_scrollbar.setValue(total_width - available_width)

        # Calculate how many lines can be displayed
        self._visible_lines = max(1, self._hex_widget.height() // self._char_height)

        # Calculate total lines needed to display all data
        self._total_lines = total_lines = (
            len(self._data) + self._bytes_per_line - 1
        ) // self._bytes_per_line + 2

        # Vertical scrollbar visibility and range
        if total_lines <= self._visible_lines:
            # All data fits in view, no need for scrollbar
            self._v_scrollbar.setVisible(False)
            self._v_scrollbar.setValue(0)
        else:
            # Configure scrollbar for the data
            self._v_scrollbar.setRange(0, total_lines - self._visible_lines)
            self._v_scrollbar.setPageStep(self._visible_lines)
            self._v_scrollbar.setSingleStep(1)
            self._v_scrollbar.setVisible(True)

            # Ensure current position is valid
            if self._v_scrollbar.value() > total_lines - self._visible_lines:
                self._v_scrollbar.setValue(total_lines - self._visible_lines)

        if self._v_scrollbar.isVisible() and self._h_scrollbar.isVisible():
            self._corner_widget.setFixedSize(
                self._v_scrollbar.width(), self._h_scrollbar.height()
            )
            self._corner_widget.setVisible(True)

    def _measure_font_metrics(self):
        """Measure font metrics for the current font."""
        self._font_metrics = QtGui.QFontMetrics(self.font())
        # Find the widest character among 0-9, A-F, and 'Address' letters
        chars = "0123456789ABCDEFdres"
        self._char_width = max(self._font_metrics.horizontalAdvance(c) for c in chars)
        self._char_height = self._font_metrics.height()

    def _calculate_address_width(self) -> int:
        """Calculate the width needed for address display."""
        if len(self._data) == 0:
            return 8 * self._char_width  # Minimum width

        # Calculate required width based on addressing mode and data size
        max_address = len(self._data) - 1
        min_width = len("Address") - 2

        if self._addressing_base == 16:  # Hex
            addr_chars = max(min_width, len(f"{max_address:X}"))
            return (addr_chars + 2) * self._char_width  # +2 for "0x"
        if self._addressing_base == 8:  # Octal
            addr_chars = max(min_width, len(f"{max_address:o}"))
            return (addr_chars + 2) * self._char_width  # +2 for "0o"
        # Decimal
        addr_chars = max(min_width + 2, len(str(max_address)))
        return addr_chars * self._char_width

    def _draw_header(
        self,
        painter: QtGui.QPainter,
        rect: QtCore.QRect,
        addr_width: int,
        hex_width: int,
        ascii_width: int,
    ):
        """Draw the header with column addresses."""
        header_rect = QtCore.QRect(
            rect.left(), rect.top(), rect.width(), self._char_height + 8
        )

        # Draw header background
        header_bg = QtGui.QLinearGradient(
            header_rect.topLeft(), header_rect.bottomLeft()
        )
        header_bg.setColorAt(0, self.colors.header_color_begin)
        header_bg.setColorAt(1, self.colors.header_color_end)
        painter.fillRect(header_rect, header_bg)

        # Draw header separator line
        painter.setPen(self.colors.header_separator_color)
        painter.drawLine(
            header_rect.left(),
            header_rect.bottom(),
            header_rect.right(),
            header_rect.bottom(),
        )

        # Draw address column header
        painter.setPen(self.colors.header_text_color)
        addr_rect = QtCore.QRect(
            rect.left(), rect.top(), addr_width, self._char_height + 8
        )  # Increased height
        painter.drawText(addr_rect, QtCore.Qt.AlignmentFlag.AlignCenter, "Address")

        # Draw column separators
        painter.setPen(self.colors.header_separator_color)
        painter.drawLine(
            addr_rect.right(),
            header_rect.top(),
            addr_rect.right(),
            header_rect.bottom(),
        )

        # Draw hex column headers (00-0F)
        hex_start_x = addr_rect.right() + 10

        painter.setPen(self.colors.header_text_color)
        col_width = self._char_width * 4  # Increased width for hex columns
        x_offset = 0

        for i in range(self._bytes_per_line):
            # Add extra space for group separator
            if i > 0 and i % self._bytes_per_group == 0:
                x_offset += self._char_width

            column_text = f"{i:02X}"
            col_rect = QtCore.QRect(
                hex_start_x + i * col_width + x_offset,
                rect.top(),
                col_width,
                self._char_height + 8,
            )

            painter.drawText(col_rect, QtCore.Qt.AlignmentFlag.AlignCenter, column_text)

        # Draw hex/ASCII separator
        if self._show_ascii:
            painter.setPen(self.colors.header_separator_color)
            ascii_start_x = hex_start_x + hex_width + x_offset + 5
            painter.drawLine(
                ascii_start_x - 5,
                header_rect.top(),
                ascii_start_x - 5,
                header_rect.bottom(),
            )

            # Draw ASCII header
            ascii_header_rect = QtCore.QRect(
                ascii_start_x, rect.top(), ascii_width, self._char_height + 8
            )
            painter.setPen(self.colors.header_text_color)
            painter.drawText(
                ascii_header_rect, QtCore.Qt.AlignmentFlag.AlignCenter, "ASCII"
            )

    def _draw_hex_content(
        self,
        painter: QtGui.QPainter,
        rect: QtCore.QRect,
        addr_width: int,
        hex_width: int,
        ascii_width: int,
    ):
        """Draw the hex content and ASCII representation."""
        if len(self._data) == 0:
            return

        # Calculate positions
        addr_x = rect.left()
        hex_x = addr_x + addr_width + 10

        row_height = self._char_height
        bytes_per_row = self._bytes_per_line

        # Calculate visible range
        first_row = self._v_scrollbar.value()
        visible_rows = min(
            self._visible_lines,
            (len(self._data) + bytes_per_row - 1) // bytes_per_row - first_row,
        )

        # For each visible row
        for row in range(visible_rows):
            y = rect.top() + row * row_height
            row_addr = (first_row + row) * bytes_per_row

            # Draw alternating row backgrounds
            if (first_row + row) % 2 == 0:
                painter.fillRect(
                    QtCore.QRect(rect.left(), y, rect.width(), row_height),
                    self.colors.row_color,
                )
            else:
                painter.fillRect(
                    QtCore.QRect(rect.left(), y, rect.width(), row_height),
                    self.colors.alternate_row_color,
                )

            # Draw address
            painter.setPen(self.colors.address_color)
            addr_rect = QtCore.QRect(addr_x, y, addr_width, row_height)

            # Use minimum width of "Address" (7 chars)
            min_width = max(5, len("Address") - 2)  # -2 for the "0x" or "0o" prefix

            if self._addressing_base == 16:  # Hex
                addr_text = f"0x{row_addr:0{min_width}X}"
            elif self._addressing_base == 8:  # Octal
                addr_text = f"0o{row_addr:0{min_width}o}"
            else:  # Decimal
                addr_text = (
                    f"{row_addr:0{min_width + 2}d}"  # +2 to account for missing prefix
                )

            painter.drawText(addr_rect, QtCore.Qt.AlignmentFlag.AlignCenter, addr_text)

            # Draw bytes for this row
            max_bytes = min(bytes_per_row, len(self._data) - row_addr)
            col_width = self._char_width * 4
            x_offset = 0

            for col in range(max_bytes):
                byte_addr = row_addr + col
                byte_val = self._data[byte_addr]

                # Calculate position for this byte
                byte_x = hex_x + col * col_width

                # Add extra space for group separator
                if col > 0 and col % self._bytes_per_group == 0:
                    x_offset += self._char_width

                byte_rect = QtCore.QRect(
                    int(byte_x + self._char_width / 2 + x_offset),
                    y,
                    col_width - self._char_width,
                    row_height,
                )

                # Check if this byte is selected or cursor is here
                is_selected = (
                    self._selection_start >= 0
                    and byte_addr >= min(self._selection_start, self._selection_end)
                    and byte_addr <= max(self._selection_start, self._selection_end)
                )
                is_cursor = byte_addr == self._cursor_pos

                # Draw background if selected or cursor is here
                if is_selected:
                    painter.fillRect(byte_rect, self.colors.selection_bg_color)
                    painter.setPen(self.colors.selection_fg_color)
                elif is_cursor:
                    painter.fillRect(byte_rect, self.colors.highlight_bg_color)
                    painter.setPen(self.colors.highlight_fg_color)
                else:
                    painter.setPen(self.colors.hex_color)

                byte_text = f"{byte_val:02X}"
                painter.drawText(
                    byte_rect, QtCore.Qt.AlignmentFlag.AlignCenter, byte_text
                )

            ascii_x = (
                hex_x
                + hex_width
                + (self._bytes_per_line // self._bytes_per_group - 1) * self._char_width
                + 15
            )

            # Draw ASCII representation
            if self._show_ascii:
                for col in range(max_bytes):
                    byte_addr = row_addr + col
                    byte_val = self._data[byte_addr]

                    # Calculate position for this ASCII char
                    char_x = ascii_x + col * int(self._char_width * 1.5)
                    char_rect = QtCore.QRect(
                        char_x, y, int(self._char_width * 1.5), row_height
                    )

                    # Check if this byte is selected or cursor is here
                    is_selected = (
                        self._selection_start >= 0
                        and byte_addr >= min(self._selection_start, self._selection_end)
                        and byte_addr <= max(self._selection_start, self._selection_end)
                    )
                    is_cursor = byte_addr == self._cursor_pos

                    # Draw background if selected or cursor is here
                    if is_selected:
                        painter.fillRect(char_rect, self.colors.selection_bg_color)
                        painter.setPen(self.colors.selection_fg_color)
                    elif is_cursor:
                        painter.fillRect(char_rect, self.colors.highlight_bg_color)
                        painter.setPen(self.colors.highlight_fg_color)
                    else:
                        painter.setPen(self.colors.ascii_color)

                    # Get printable character or dot for control chars
                    if 32 <= byte_val <= 126:  # Printable ASCII
                        char = chr(byte_val)
                    else:
                        char = "."

                    painter.drawText(
                        char_rect, QtCore.Qt.AlignmentFlag.AlignCenter, char
                    )

    def _ensure_cursor_visible(self):
        """Ensure the cursor is visible by scrolling if necessary."""
        if len(self._data) == 0:
            return

        cursor_line = self._cursor_pos // self._bytes_per_line

        if cursor_line < self._v_scrollbar.value():
            # Cursor is above visible area, scroll up
            self._v_scrollbar.setValue(cursor_line)
        elif cursor_line >= self._v_scrollbar.value() + self._visible_lines:
            # Cursor is below visible area, scroll down
            # TODO: Fix the bottom issue
            self._v_scrollbar.setValue(cursor_line - self._visible_lines + 1)

    def setFont(self, font: QtGui.QFont | str | Sequence[str]):
        super().setFont(font)
        self._measure_font_metrics()

    def resizeEvent(self, event: QtGui.QResizeEvent):
        """Handle resize events to update the scrollbar and layout."""
        self._update_scrollbar()
        self.update()

    def mousePressEvent(self, event: QtGui.QMouseEvent):
        """Handle mouse press events."""
        if len(self._data) == 0 or event.button() != QtCore.Qt.MouseButton.LeftButton:
            return

        # Calculate which byte was clicked, if any
        byte_addr = self._byte_at_position(event.position().toPoint())
        if byte_addr >= 0 and byte_addr < len(self._data):
            self._cursor_pos = byte_addr

            if event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier:
                # Extend selection
                if self._selection_start < 0:
                    self._selection_start = byte_addr
                self._selection_end = byte_addr
            else:
                # Start new selection
                self._selection_start = byte_addr
                self._selection_end = byte_addr

            self.update()
            self.cursorPositionChanged.emit(byte_addr)
            self.selectionChanged.emit(
                min(self._selection_start, self._selection_end),
                max(self._selection_start, self._selection_end),
            )

    def mouseMoveEvent(self, event: QtGui.QMouseEvent):
        """Handle mouse move events."""
        if (
            len(self._data) == 0
            or not event.buttons() & QtCore.Qt.MouseButton.LeftButton
        ):
            return

        # Extend selection if button is pressed
        byte_addr = self._byte_at_position(event.position().toPoint())
        if byte_addr >= 0 and byte_addr < len(self._data):
            self._cursor_pos = byte_addr
            self._selection_end = byte_addr

            self.update()
            self.selectionChanged.emit(
                min(self._selection_start, self._selection_end),
                max(self._selection_start, self._selection_end),
            )

    def _byte_at_position(self, pos: QtCore.QPoint) -> int:
        """Get the byte address at the given position."""
        # Adjust position for horizontal scrolling
        pos.setX(pos.x() + self._h_scrollbar.value())

        # Ignore clicks outside the hex area - fully account for scrollbars
        rect = self._hex_widget.geometry()
        rect.setWidth(max(self._total_width, rect.width()))

        if not rect.contains(pos):
            return -1

        # Skip header
        rect.setTop(rect.top() + self._char_height + 8)  # Increased header height

        if pos.y() < rect.top():
            return -1

        # Calculate layout
        address_width = self._calculate_address_width()
        hex_width = (
            self._char_width * 4
        ) * self._bytes_per_line  # Increased width for hex columns
        if self._bytes_per_group > 1:
            hex_width += (
                self._bytes_per_line // self._bytes_per_group - 1
            ) * self._char_width

        # Check if click is in hex area or ASCII area
        hex_start_x = rect.left() + address_width + 10  # Increased spacing
        hex_end_x = hex_start_x + hex_width

        in_hex_area = pos.x() >= hex_start_x and pos.x() < hex_end_x

        in_ascii_area = False
        ascii_start_x = 0
        if self._show_ascii:
            ascii_start_x = (
                hex_end_x
                + (self._bytes_per_line // self._bytes_per_group - 1) * self._char_width
                + 15
            )
            ascii_end_x = (
                ascii_start_x + int(self._char_width * 1.5) * self._bytes_per_line
            )
            in_ascii_area = pos.x() >= ascii_start_x and pos.x() < ascii_end_x

        if not (in_hex_area or in_ascii_area):
            return -1

        # Calculate row and column
        row = (pos.y() - rect.top()) // self._char_height
        row += self._v_scrollbar.value()

        col = -1

        if in_hex_area:
            # Calculate column in hex area
            rel_x = pos.x() - hex_start_x
            col_width = self._char_width * 4  # Increased width for hex columns

            for i in range(self._bytes_per_line):
                col_x = i * col_width

                # Add extra space for group separator
                if i > 0 and i % self._bytes_per_group == 0:
                    col_x += self._char_width

                if rel_x >= col_x and rel_x < col_x + col_width:
                    col = i
                    break

        elif in_ascii_area:
            # Calculate column in ASCII area
            rel_x = pos.x() - ascii_start_x
            col = rel_x // int(self._char_width * 1.5)

        if col >= 0 and col < self._bytes_per_line:
            byte_addr = row * self._bytes_per_line + col
            if byte_addr < len(self._data):
                return byte_addr

        return -1

    def wheelEvent(self, event: QtGui.QWheelEvent):
        """Handle mouse wheel events."""
        if len(self._data) == 0:
            return

        # Check if we should scroll horizontally (with Shift key)
        if event.modifiers() & QtGui.Qt.KeyboardModifier.ShiftModifier:
            # Horizontal scrolling
            if not self._h_scrollbar.isVisible():
                return

            # Calculate scroll amount
            delta = (
                event.angleDelta().x()
                if event.angleDelta().x() != 0
                else event.angleDelta().y()
            )
            pixels_to_scroll = max(self._char_width * 2, abs(delta) // 4)

            if delta > 0:
                # Scroll left
                self._h_scrollbar.setValue(
                    max(0, self._h_scrollbar.value() - pixels_to_scroll)
                )
            else:
                # Scroll right
                max_scroll = self._h_scrollbar.maximum()
                self._h_scrollbar.setValue(
                    min(max_scroll, self._h_scrollbar.value() + pixels_to_scroll)
                )

        else:
            # Vertical scrolling
            if not self._v_scrollbar.isVisible():
                return

            # Calculate scroll amount
            delta = event.angleDelta().y()
            lines_to_scroll = max(1, abs(delta) // 40)

            if delta > 0:
                # Scroll up
                self._v_scrollbar.setValue(
                    max(0, self._v_scrollbar.value() - lines_to_scroll)
                )
            else:
                # Scroll down
                max_first_line = self._total_lines - self._visible_lines
                self._v_scrollbar.setValue(
                    min(max_first_line, self._v_scrollbar.value() + lines_to_scroll)
                )

        self.update()

    def keyPressEvent(self, event: QtGui.QKeyEvent):
        """Handle key press events."""
        if len(self._data) == 0:
            return

        if event.key() == QtGui.Qt.Key.Key_Left:
            # Move cursor left
            if self._cursor_pos > 0:
                self._cursor_pos -= 1

                # Update selection if shift is pressed
                if event.modifiers() & QtGui.Qt.KeyboardModifier.ShiftModifier:
                    if self._selection_start < 0:
                        self._selection_start = self._cursor_pos + 1
                    self._selection_end = self._cursor_pos
                else:
                    self._selection_start = self._selection_end = self._cursor_pos

                # Ensure cursor is visible
                self._ensure_cursor_visible()
                self.update()
                self.cursorPositionChanged.emit(self._cursor_pos)
                self.selectionChanged.emit(
                    min(self._selection_start, self._selection_end),
                    max(self._selection_start, self._selection_end),
                )

        elif event.key() == QtGui.Qt.Key.Key_Right:
            # Move cursor right
            if self._cursor_pos < len(self._data) - 1:
                self._cursor_pos += 1

                # Update selection if shift is pressed
                if event.modifiers() & QtGui.Qt.KeyboardModifier.ShiftModifier:
                    if self._selection_start < 0:
                        self._selection_start = self._cursor_pos - 1
                    self._selection_end = self._cursor_pos
                else:
                    self._selection_start = self._selection_end = self._cursor_pos

                # Ensure cursor is visible
                self._ensure_cursor_visible()
                self.update()
                self.cursorPositionChanged.emit(self._cursor_pos)
                self.selectionChanged.emit(
                    min(self._selection_start, self._selection_end),
                    max(self._selection_start, self._selection_end),
                )

        elif event.key() == QtGui.Qt.Key.Key_Up:
            # Move cursor up
            if self._cursor_pos >= self._bytes_per_line:
                self._cursor_pos -= self._bytes_per_line

                # Update selection if shift is pressed
                if event.modifiers() & QtGui.Qt.KeyboardModifier.ShiftModifier:
                    if self._selection_start < 0:
                        self._selection_start = self._cursor_pos + self._bytes_per_line
                    self._selection_end = self._cursor_pos
                else:
                    self._selection_start = self._selection_end = self._cursor_pos

                # Ensure cursor is visible
                self._ensure_cursor_visible()
                self.update()
                self.cursorPositionChanged.emit(self._cursor_pos)
                self.selectionChanged.emit(
                    min(self._selection_start, self._selection_end),
                    max(self._selection_start, self._selection_end),
                )

        elif event.key() == QtGui.Qt.Key.Key_Down:
            # Move cursor down
            if self._cursor_pos + self._bytes_per_line < len(self._data):
                self._cursor_pos += self._bytes_per_line

                # Update selection if shift is pressed
                if event.modifiers() & QtGui.Qt.KeyboardModifier.ShiftModifier:
                    if self._selection_start < 0:
                        self._selection_start = self._cursor_pos - self._bytes_per_line
                    self._selection_end = self._cursor_pos
                else:
                    self._selection_start = self._selection_end = self._cursor_pos

                # Ensure cursor is visible
                self._ensure_cursor_visible()
                self.update()
                self.cursorPositionChanged.emit(self._cursor_pos)
                self.selectionChanged.emit(
                    min(self._selection_start, self._selection_end),
                    max(self._selection_start, self._selection_end),
                )

        elif event.key() == QtGui.Qt.Key.Key_Home:
            # Move cursor to start of line
            line_start = (
                self._cursor_pos // self._bytes_per_line
            ) * self._bytes_per_line
            self._cursor_pos = line_start

            # Update selection if shift is pressed
            if event.modifiers() & QtGui.Qt.KeyboardModifier.ShiftModifier:
                if self._selection_start < 0:
                    self._selection_start = line_start + self._bytes_per_line - 1
                self._selection_end = self._cursor_pos
            else:
                self._selection_start = self._selection_end = self._cursor_pos

            # Ensure cursor is visible
            self._ensure_cursor_visible()
            self.update()
            self.cursorPositionChanged.emit(self._cursor_pos)
            self.selectionChanged.emit(
                min(self._selection_start, self._selection_end),
                max(self._selection_start, self._selection_end),
            )

        elif event.key() == QtGui.Qt.Key.Key_End:
            # Move cursor to end of line
            line_start = (
                self._cursor_pos // self._bytes_per_line
            ) * self._bytes_per_line
            line_end = min(line_start + self._bytes_per_line - 1, len(self._data) - 1)
            self._cursor_pos = line_end

            # Update selection if shift is pressed
            if event.modifiers() & QtGui.Qt.KeyboardModifier.ShiftModifier:
                if self._selection_start < 0:
                    self._selection_start = line_start
                self._selection_end = self._cursor_pos
            else:
                self._selection_start = self._selection_end = self._cursor_pos

            # Ensure cursor is visible
            self._ensure_cursor_visible()
            self.update()
            self.cursorPositionChanged.emit(self._cursor_pos)
            self.selectionChanged.emit(
                min(self._selection_start, self._selection_end),
                max(self._selection_start, self._selection_end),
            )

        elif event.key() == QtGui.Qt.Key.Key_PageUp:
            # Move cursor up one page
            old_pos = self._cursor_pos
            lines_to_move = min(
                self._visible_lines, self._cursor_pos // self._bytes_per_line
            )
            self._cursor_pos -= lines_to_move * self._bytes_per_line

            # Update selection if shift is pressed
            if event.modifiers() & QtGui.Qt.KeyboardModifier.ShiftModifier:
                if self._selection_start < 0:
                    self._selection_start = old_pos
                self._selection_end = self._cursor_pos
            else:
                self._selection_start = self._selection_end = self._cursor_pos

            # Ensure cursor is visible
            self._ensure_cursor_visible()
            self.update()
            self.cursorPositionChanged.emit(self._cursor_pos)
            self.selectionChanged.emit(
                min(self._selection_start, self._selection_end),
                max(self._selection_start, self._selection_end),
            )

        elif event.key() == QtGui.Qt.Key.Key_PageDown:
            # Move cursor down one page
            old_pos = self._cursor_pos
            bytes_per_page = self._visible_lines * self._bytes_per_line

            if self._cursor_pos + bytes_per_page < len(self._data):
                self._cursor_pos += bytes_per_page
            else:
                self._cursor_pos = len(self._data) - 1

            # Update selection if shift is pressed
            if event.modifiers() & QtGui.Qt.KeyboardModifier.ShiftModifier:
                if self._selection_start < 0:
                    self._selection_start = old_pos
                self._selection_end = self._cursor_pos
            else:
                self._selection_start = self._selection_end = self._cursor_pos

            # Ensure cursor is visible
            self._ensure_cursor_visible()
            self.update()
            self.cursorPositionChanged.emit(self._cursor_pos)
            self.selectionChanged.emit(
                min(self._selection_start, self._selection_end),
                max(self._selection_start, self._selection_end),
            )

        elif event.key() == QtGui.Qt.Key.Key_Escape:
            # Clear selection
            self._selection_start = self._selection_end = -1
            self.update()
            self.selectionChanged.emit(-1, -1)

        else:
            super().keyPressEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent):
        """Paint the hex viewer."""
        h_offset = self._h_scrollbar.value()

        painter = QtGui.QPainter(self)
        painter.setFont(self.font())

        # Calculate basic layout measurements - avoid drawing in scrollbar area
        rect = self._hex_widget.geometry()

        # Adjust for horizontal scrolling
        painter.translate(-h_offset, 0)

        # If no data, just display a message
        if len(self._data) == 0:
            painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, "No data")
            return

        # Calculate layout
        address_width = self._calculate_address_width()
        hex_width = (
            self._char_width * 4
        ) * self._bytes_per_line  # Increased width for hex columns
        if self._bytes_per_group > 1:
            # Extra space between groups
            hex_width += (
                self._bytes_per_line // self._bytes_per_group - 1
            ) * self._char_width

        ascii_width = 0
        if self._show_ascii:
            ascii_width = (
                int(self._char_width * 1.5) * self._bytes_per_line
            )  # Increased spacing for ASCII chars

        rect.setWidth(max(self._total_width, rect.width()))

        # Draw the header
        self._draw_header(painter, rect, address_width, hex_width, ascii_width)

        # Adjusted rect for content
        header_height = self._char_height + 8  # Increased header height
        rect.setTop(rect.top() + header_height)

        # Draw content rows
        self._draw_hex_content(painter, rect, address_width, hex_width, ascii_width)

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent):
        """Handle context menu events."""
        menu = QtWidgets.QMenu(self)

        # Copy actions
        copy_hex_action = QtGui.QAction("Copy as Hex", self)
        copy_hex_action.triggered.connect(self._copy_selection_as_hex)
        menu.addAction(copy_hex_action)

        copy_ascii_action = QtGui.QAction("Copy as ASCII", self)
        copy_ascii_action.triggered.connect(self._copy_selection_as_ascii)
        menu.addAction(copy_ascii_action)

        # Selection actions
        menu.addSeparator()

        select_all_action = QtGui.QAction("Select All", self)
        select_all_action.triggered.connect(self._select_all)
        menu.addAction(select_all_action)

        # Navigation actions
        menu.addSeparator()

        go_to_action = QtGui.QAction("Go to Address...", self)
        go_to_action.triggered.connect(self._go_to_address)
        menu.addAction(go_to_action)

        # Execute the menu
        menu.exec(event.globalPos())

    def _copy_selection_as_hex(self):
        """Copy the selected bytes as hex values."""
        if self._selection_start < 0 or len(self._data) == 0:
            return

        start = min(self._selection_start, self._selection_end)
        end = max(self._selection_start, self._selection_end)

        if start >= len(self._data):
            start = len(self._data) - 1
        if end >= len(self._data):
            end = len(self._data) - 1

        selected_bytes = self._data[start : end + 1]
        hex_text = " ".join(f"{b:02X}" for b in selected_bytes)

        QtWidgets.QApplication.clipboard().setText(hex_text)

    def _copy_selection_as_ascii(self):
        """Copy the selected bytes as ASCII text."""
        if self._selection_start < 0 or len(self._data) == 0:
            return

        start = min(self._selection_start, self._selection_end)
        end = max(self._selection_start, self._selection_end)

        if start >= len(self._data):
            start = len(self._data) - 1
        if end >= len(self._data):
            end = len(self._data) - 1

        selected_bytes = self._data[start : end + 1]
        ascii_text = "".join(chr(b) if 32 <= b <= 126 else "." for b in selected_bytes)

        QtWidgets.QApplication.clipboard().setText(ascii_text)

    def _select_all(self):
        """Select all bytes."""
        if len(self._data) == 0:
            return

        self._selection_start = 0
        self._selection_end = len(self._data) - 1
        self._cursor_pos = self._selection_end

        self.update()
        self.cursorPositionChanged.emit(self._cursor_pos)
        self.selectionChanged.emit(self._selection_start, self._selection_end)

    def _go_to_address(self):
        """Go to a specific address."""
        address, ok = QtWidgets.QInputDialog.getText(
            self, "Go to Address", "Enter address (decimal or 0x prefix for hex):"
        )

        if ok and address:
            try:
                # Try to parse as hex if it has 0x prefix
                if address.lower().startswith("0x"):
                    addr = int(address[2:], 16)
                else:
                    addr = int(address)

                # Ensure address is in range
                if 0 <= addr < len(self._data):
                    self._cursor_pos = addr
                    self._selection_start = self._selection_end = addr

                    self._ensure_cursor_visible()
                    self.update()
                    self.cursorPositionChanged.emit(self._cursor_pos)
                    self.selectionChanged.emit(addr, addr)
                else:
                    QtWidgets.QMessageBox.warning(
                        self,
                        "Invalid Address",
                        f"Address must be between 0 and {len(self._data) - 1}",
                    )
            except ValueError:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Invalid Address",
                    "Please enter a valid decimal or hexadecimal address",
                )
