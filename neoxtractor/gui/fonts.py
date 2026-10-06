"""Font management module."""

from PySide6.QtGui import QFontDatabase

from neoxtractor.core.logger import get_logger

fonts: dict[str, int] = {}

def load_font(font: str, path: str) -> int:
    """Load a font from the given path and return its ID."""
    fonts[font] = QFontDatabase.addApplicationFont(path)
    if fonts[font] == -1:
        get_logger().error("Failed to load font: %s from %s", font, path)
    else:
        get_logger().debug("Loaded font: %s from %s", font, path)
    return fonts[font]

def is_font_loaded(font: str) -> bool:
    """Check if a font is loaded."""
    fnt = fonts[font]
    return fnt is not None and font != -1
