"""
Code highlighting functionality for the NeoXtractor GUI.
Provides a flexible syntax highlighting system with JSON-based rules.
"""

import json
import os

from PySide6.QtCore import QRect, QRegularExpression, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPaintEvent,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from neoxtractor.core.file import IFile
from neoxtractor.core.logger import get_logger
from neoxtractor.core.utils import get_application_path
from neoxtractor.gui.theme.theme_manager import ThemeManager
from neoxtractor.gui.widgets.viewer import Viewer


class LineNumberArea(QWidget):
    """
    Widget that displays line numbers for a QPlainTextEdit.

    This widget is displayed in the left margin of the CodeViewer and
    shows the line numbers for the visible text.
    """

    def __init__(self, editor: "CodeViewer"):
        """
        Initialize the line number area.

        Args:
            editor: The CodeViewer instance this line number area belongs to
        """
        super().__init__(editor)
        self.editor = editor

        # Initialize theme colors
        self.background_color = QColor("#f5f5f5")
        self.current_line_color = QColor("#e8e8e8")
        self.text_color = QColor("#666666")
        self.font_size = 10

        # Connect to theme manager
        self._theme_manager = ThemeManager.instance()
        self._theme_manager.theme_changed.connect(self._update_theme)
        self._update_theme(self._theme_manager.get_current_theme())

    def _update_theme(self, theme):
        """Update colors based on current theme."""

        self.background_color = QColor(
            self._theme_manager.get_color("code_viewer.line_number_background")
            or "#f5f5f5"
        )
        self.current_line_color = QColor(
            self._theme_manager.get_color("code_viewer.current_line_background")
            or "#e8e8e8"
        )
        self.text_color = QColor(
            self._theme_manager.get_color("code_viewer.line_number_text") or "#666666"
        )
        self.update()

    def sizeHint(self):
        """Calculate the appropriate width for the line number area."""
        return QSize(self.editor.line_number_area_width(), 0)

    def paintEvent(self, event: QPaintEvent):
        """Paint the line numbers."""
        painter = QPainter(self)
        painter.fillRect(event.rect(), self.background_color)

        block = self.editor.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(
            self.editor.blockBoundingGeometry(block)
            .translated(self.editor.contentOffset())
            .top()
        )
        bottom = top + round(self.editor.blockBoundingRect(block).height())

        # Font for line numbers
        font = painter.font()
        font.setPointSize(self.font_size)
        font.setBold(True)
        painter.setFont(font)

        # Draw line numbers
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                if number == str(self.editor.textCursor().blockNumber() + 1):
                    painter.fillRect(
                        0,
                        top,
                        self.width(),
                        self.editor.fontMetrics().height(),
                        self.current_line_color,
                    )
                painter.setPen(self.text_color)
                painter.drawText(
                    0,
                    top,
                    self.width() - 5,
                    self.editor.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    number,
                )

            block = block.next()
            top = bottom
            bottom = top + round(self.editor.blockBoundingRect(block).height())
            block_number += 1


class CodeHighlighter(QSyntaxHighlighter):
    """
    Flexible syntax highlighter that loads highlighting rules from JSON files.

    This highlighter supports loading rules for different programming languages
    from external JSON configuration files, making it easy to add support for
    new languages without modifying the code.
    """

    def __init__(self, document: QTextDocument, language: str = "text"):
        """
        Initialize the highlighter with a document and language.

        Args:
            document: The QTextDocument to highlight
            language: The language identifier (corresponds to JSON rule file)
        """
        super().__init__(document)
        self.highlighting_rules = []
        self.formats = {}

        # Set default language to plain text
        self.language = language

        # Connect to theme manager
        self._theme_manager = ThemeManager.instance()
        self._theme_manager.theme_changed.connect(self._on_theme_changed)

        # Load language rules
        self.load_language(language)

    def _on_theme_changed(self):
        """Handle theme changes by reloading colors and rehighlighting."""
        self.load_language(self.language)
        self.rehighlight()

    def _parse_color(self, color_string: str) -> QColor:
        """
        Parse color string that can be in format '#ffffff' or '#ffffff@keyword'.

        Args:
            color_string: Color in format '#ffffff' or '#ffffff@keyword'

        Returns:
            QColor: The resolved color
        """
        if "@" in color_string:
            # Format: #ffffff@keyword - check theme first, fallback to color
            color_hex, theme_key = color_string.split("@", 1)

            if theme_key.startswith("."):
                theme_color = self._theme_manager.get_color(f"palette{theme_key}")
            else:
                theme_color = self._theme_manager.get_color(
                    f"code_viewer.syntax.{theme_key}"
                )
            if theme_color:
                return QColor(theme_color)

            return QColor(color_hex)

        # Format: #ffffff - use color directly
        return QColor(color_string)

    def load_language(self, language: str) -> bool:
        """
        Load highlighting rules for the specified language from a JSON file.

        Args:
            language: The language identifier

        Returns:
            bool: True if rules were loaded successfully, False otherwise
        """
        self.highlighting_rules = []
        self.formats = {}
        self.string_patterns = []
        self.comment_patterns = []
        self.language = language
        self.language_name: str | None = None

        # Get the syntax file path using get_application_path
        rules_file = os.path.join(
            get_application_path(), "data", "hlsyntax", f"{language}.json"
        )
        if not os.path.exists(rules_file):
            get_logger().warning("Language rules file not found: %s", rules_file)
            return False

        try:
            with open(rules_file, "r", encoding="utf-8") as f:
                rules_data = json.load(f)

            if "name" in rules_data:
                self.language_name = rules_data["name"]

            # Load string patterns if defined
            if "string_patterns" in rules_data:
                self.string_patterns = rules_data["string_patterns"]

            # Load comment patterns if defined
            if "comment_patterns" in rules_data:
                self.comment_patterns = rules_data["comment_patterns"]

            # Create formats first
            if "formats" in rules_data:
                for format_name, format_data in rules_data["formats"].items():
                    text_format = QTextCharFormat()

                    # Set foreground color
                    if "foreground" in format_data:
                        text_format.setForeground(
                            self._parse_color(format_data["foreground"])
                        )

                    # Set background color
                    if "background" in format_data:
                        text_format.setBackground(
                            self._parse_color(format_data["background"])
                        )

                    # Set font weight
                    if "bold" in format_data and format_data["bold"]:
                        text_format.setFontWeight(QFont.Weight.Bold)

                    # Set font style
                    if "italic" in format_data and format_data["italic"]:
                        text_format.setFontItalic(True)

                    # Set underline
                    if "underline" in format_data and format_data["underline"]:
                        text_format.setUnderlineStyle(
                            QTextCharFormat.UnderlineStyle.SingleUnderline
                        )

                    self.formats[format_name] = text_format

            # Create highlighting rules
            if "rules" in rules_data:
                for rule in rules_data["rules"]:
                    if (
                        "pattern" in rule
                        and "format" in rule
                        and rule["format"] in self.formats
                    ):
                        pattern = QRegularExpression(rule["pattern"])
                        format_name = rule["format"]
                        capture_group = rule.get(
                            "capture_group", 0
                        )  # Default to entire match
                        self.highlighting_rules.append(
                            (pattern, self.formats[format_name], capture_group)
                        )

            return True
        except (json.JSONDecodeError, IOError, KeyError) as e:
            get_logger().error(
                "Error loading language rules for %s: %s", language, str(e)
            )
            return False

    def highlightBlock(self, text: str) -> None:
        """
        Highlight a block of text according to the loaded rules.

        Args:
            text: The text to highlight
        """
        # Find all string and comment regions
        string_regions = self._findStringRegions(text)
        comment_regions = self._findCommentRegions(text)

        # Merge and sort all protected regions
        protected_regions = string_regions + comment_regions
        protected_regions.sort()

        # Apply string formatting first
        string_formats = [
            fmt for name, fmt in self.formats.items() if "string" in name.lower()
        ]
        if string_formats:
            for start, end in string_regions:
                self.setFormat(start, end - start, string_formats[0])

        # Apply comment formatting
        comment_formats = [
            fmt for name, fmt in self.formats.items() if "comment" in name.lower()
        ]
        if comment_formats:
            for start, end in comment_regions:
                self.setFormat(start, end - start, comment_formats[0])

        # Apply other rules, but skip protected regions
        for pattern, format_data, capture_group in self.highlighting_rules:
            # Skip string and comment formats as we handled them above
            if format_data in string_formats or format_data in comment_formats:
                continue

            match_iterator = pattern.globalMatch(text)
            while match_iterator.hasNext():
                match = match_iterator.next()

                # Use capture group if specified, otherwise use entire match
                if 0 < capture_group <= match.lastCapturedIndex():
                    match_start = match.capturedStart(capture_group)
                    match_length = match.capturedLength(capture_group)
                else:
                    match_start = match.capturedStart()
                    match_length = match.capturedLength()

                match_end = match_start + match_length

                # Check if this match overlaps with any protected region
                overlaps_protected = any(
                    not (match_end <= region_start or match_start >= region_end)
                    for region_start, region_end in protected_regions
                )

                # Only apply formatting if not inside a protected region
                if not overlaps_protected:
                    self.setFormat(match_start, match_length, format_data)

    def _findStringRegions(self, text: str) -> list[tuple[int, int]]:
        """
        Find all string regions in the text based on the loaded language rules.

        Args:
            text: The text to analyze

        Returns:
            List of (start, end) tuples representing string regions
        """
        # Use string patterns from the loaded rules if available
        if self.string_patterns:
            patterns = self.string_patterns
        else:
            # Fallback to basic patterns if no string patterns defined
            patterns = [
                {"pattern": r'"(?:[^"\\]|\\.)*"', "multiline": False},  # Double quotes
                {"pattern": r"'(?:[^'\\]|\\.)*'", "multiline": False},  # Single quotes
            ]

        return self._findRegionsFromPatterns(text, patterns)

    def _findCommentRegions(self, text: str) -> list[tuple[int, int]]:
        """
        Find all comment regions in the text based on the loaded language rules.

        Args:
            text: The text to analyze

        Returns:
            List of (start, end) tuples representing comment regions
        """
        # Use comment patterns from the loaded rules if available
        if self.comment_patterns:
            patterns = self.comment_patterns
        else:
            # No fallback patterns for comments since they vary too much between languages
            patterns = []

        return self._findRegionsFromPatterns(text, patterns)

    def _findRegionsFromPatterns(
        self, text: str, patterns: list[dict]
    ) -> list[tuple[int, int]]:
        """
        Find all regions in text that match the given patterns.

        Args:
            text: The text to analyze
            patterns: List of pattern dictionaries with 'pattern' and optional 'multiline' keys

        Returns:
            List of (start, end) tuples representing matching regions
        """
        regions = []

        # Apply patterns to find regions
        for pattern_info in patterns:
            pattern = QRegularExpression(pattern_info["pattern"])

            # Set multiline option if specified
            if pattern_info.get("multiline", False):
                pattern.setPatternOptions(
                    QRegularExpression.PatternOption.DotMatchesEverythingOption
                )

            match_iterator = pattern.globalMatch(text)
            while match_iterator.hasNext():
                match = match_iterator.next()
                regions.append((match.capturedStart(), match.capturedEnd()))

        # Sort regions by start position and merge overlapping ones
        if regions:
            regions.sort()
            merged = [regions[0]]
            for start, end in regions[1:]:
                last_start, last_end = merged[-1]
                if start <= last_end:
                    # Overlapping or adjacent, merge them
                    merged[-1] = (last_start, max(last_end, end))
                else:
                    merged.append((start, end))
            regions = merged

        return regions


class CodeViewer(QPlainTextEdit):
    """
    A code viewer widget with syntax highlighting support.

    This widget is a read-only code viewer by default but can be switched
    to edit mode if needed.
    """

    languageChanged = Signal(str)

    def __init__(self, parent=None, language: str = "text"):
        """
        Initialize the code viewer.

        Args:
            parent: Parent widget
            language: The programming language to highlight
        """
        super().__init__(parent)

        self.setReadOnly(True)  # Read-only by default
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))

        # Set a monospace font
        font = QFont("Space Mono")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setFixedPitch(True)
        font.setPointSize(12)
        self.setFont(font)

        # Create line number area
        self.line_number_area = LineNumberArea(self)
        self.line_number_area.setFont(font)

        # Connect signals for line numbers
        self.blockCountChanged.connect(self.update_line_number_area_width)
        self.updateRequest.connect(self.update_line_number_area)

        # Initialize the line number area width
        self.update_line_number_area_width()

        # Create and apply the syntax highlighter
        self.highlighter = CodeHighlighter(self.document(), language)

    def setReadOnly(self, read_only: bool):
        super().setReadOnly(read_only)
        if read_only:
            self.setTextInteractionFlags(
                self.textInteractionFlags()
                | Qt.TextInteractionFlag.TextSelectableByKeyboard
            )

    def set_language(self, language: str) -> bool:
        """
        Set the highlighting language.

        Args:
            language: The language identifier

        Returns:
            bool: True if language rules were loaded successfully
        """
        result = self.highlighter.load_language(language)

        if result:
            self.highlighter.rehighlight()
            # Emit signal to notify about the language change
            self.languageChanged.emit(language)

        return result

    def line_number_area_width(self):
        """Calculate the width of the line number area."""
        digits = 1
        max_num = max(1, self.blockCount())
        while max_num >= 10:
            max_num //= 10
            digits += 1

        space = 2 * 3 + self.fontMetrics().horizontalAdvance("9") * digits
        return space

    def update_line_number_area_width(self):
        """Update the width of the editor to accommodate line numbers."""
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def update_line_number_area(self, rect: QRect, dy):
        """Update the line number area when the editor's viewport scrolls."""
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(
                0, rect.y(), self.line_number_area.width(), rect.height()
            )

        if rect.contains(self.viewport().rect()):
            self.update_line_number_area_width()

    def resizeEvent(self, event):
        """Handle resize events to adjust the line number area."""
        super().resizeEvent(event)

        cr = self.contentsRect()
        self.line_number_area.setGeometry(
            QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height())
        )


class CodeEditor(Viewer):
    """
    CodeEditor is a widget for displaying and editing code with syntax highlighting.
    This widget combines a CodeViewer for text editing with a status bar that shows
    the cursor position and allows language selection for syntax highlighting.
    Attributes:
        viewer (CodeViewer): The text editing component with syntax highlighting.
        status_bar (QFrame): A frame containing status information.
        cursor_position_label (QLabel): Label showing the current cursor position.
        language_selector (QComboBox): Dropdown for selecting the syntax highlighting language.
        main_layout (QVBoxLayout): The main layout of the widget.
    """

    name = "Code Viewer"
    accepted_extensions = {
        "txt",
        "h",
        "json",
        "py",
        "xml",
        "gim",
        "mtg",
        "ags",
        "unknown1",
    }
    allow_unsupported_extensions = True

    _ext_lang_map: dict[str, str] = {}

    def __init__(self, parent=None):
        super().__init__(parent)

        self._file: IFile | None = None

        self.viewer = CodeViewer(self)
        self.viewer.languageChanged.connect(self._on_language_changed)

        self.set_language = self.viewer.set_language

        # Create bottom status bar
        self.status_bar = QFrame()
        self.status_bar.setFrameShape(QFrame.Shape.StyledPanel)

        # Setup status bar layout
        status_layout = QHBoxLayout(self.status_bar)
        status_layout.setContentsMargins(5, 2, 5, 2)

        # Cursor position label
        self.cursor_position_label = QLabel("Line: 1, Column: 1")

        # Language selector
        self.language_selector = QComboBox()
        self.language_selector.setToolTip("Select syntax highlighting language")
        self.language_selector.currentIndexChanged.connect(self._on_language_selected)

        # Add widgets to status bar
        status_layout.addWidget(self.cursor_position_label)
        status_layout.addStretch()
        status_layout.addWidget(QLabel("Language:"))
        status_layout.addWidget(self.language_selector)

        # Create main layout
        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # Add editor and status bar to main layout
        self.main_layout.addWidget(self.viewer)
        self.main_layout.addWidget(self.status_bar)

        self.setLayout(self.main_layout)

        # Update cursor position when cursor changes
        self.viewer.cursorPositionChanged.connect(self._update_cursor_position)

        data_dir = os.path.join(get_application_path(), "data")

        with open(
            os.path.join(data_dir, "ext_lang_map.json"), "r", encoding="utf-8"
        ) as f:
            self._ext_lang_map = json.load(f)

        # Add available languages
        hlsyntax_dir = os.path.join(data_dir, "hlsyntax")

        for file in os.listdir(hlsyntax_dir):
            if file.endswith(".json"):
                language = file[:-5]
                with open(os.path.join(hlsyntax_dir, file), "r", encoding="utf-8") as f:
                    rules_data = json.load(f)
                    self.language_selector.addItem(
                        rules_data["name"] if "name" in rules_data else language,
                        language,
                    )

        self.language_selector.setCurrentText(
            self.viewer.highlighter.language_name or "Unknown"
        )

    def _on_language_selected(self, index: int):
        """Handle language selection changes."""
        self.set_language(self.language_selector.itemData(index))

    def _on_language_changed(self, language):
        """Handle language changes in the viewer."""
        self.language_selector.setCurrentText(
            self.viewer.highlighter.language_name or "Unknown"
        )

    def _update_cursor_position(self):
        """Update the cursor position label."""
        cursor = self.viewer.textCursor()
        line = cursor.blockNumber() + 1
        column = cursor.columnNumber() + 1
        self.cursor_position_label.setText(f"Line: {line}, Column: {column}")

    def set_content(self, content: str):
        """Set content of editor."""
        self.viewer.setPlainText(content)

    def get_file(self) -> IFile | None:
        return self._file

    def set_file(self, file: IFile) -> None:
        """Set the content of the code editor."""
        self._file = file
        self.set_language(self._ext_lang_map.get(file.extension, "text"))
        self.set_content(file.data.decode(errors="replace"))

    def unload_file(self):
        self._file = None
        self.set_content("")
