"""Logging utilities (Qt-free at import time).

Core code uses :func:`get_logger` and :func:`setup_logger` only; importing
this module never pulls in Qt.  The optional bridge that routes Qt's own
debug/warning output into the same log stream is installed explicitly via
:func:`enable_qt_log_handling` (GUI bootstrap), which supports either
PySide6 or PyQt6 bindings.
"""

import functools
import inspect
import importlib
import logging
import os
import sys

LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _coerce_level(level):
    """Normalize a level given as name, numeric string, or int.

    Returns ``(level, warning)`` where *warning* is a human-readable note
    (or None).  Side-effect free: :func:`setup_logger` emits the note through
    logging once handlers exist.  Numeric strings parse via ``int()``
    (``"20"`` -> DEBUG).
    """
    if isinstance(level, int):
        return level, None
    text = str(level).strip()
    if text.lstrip("-").isdigit():
        return int(text), None
    upper = text.upper()
    if upper in LEVEL_MAP:
        return LEVEL_MAP[upper], None
    return logging.INFO, f"Invalid log level: {level}. Using default (INFO)."


@functools.lru_cache(maxsize=512)
def _logger_of_call_site(code):
    """Resolve a call-site code object to its logger, cached.

    Walking the stack with ``inspect.stack()`` is expensive (it builds
    frame records for the whole stack and probes the filesystem); reading
    the caller's code object directly and caching the resolution keeps
    repeated ``get_logger()`` calls at the same site cheap.
    """
    module = inspect.getmodule(code)
    module_name = module.__name__ if module else "unknown"
    # Entry points run as "__main__"; keep the application logger name there.
    # ``default_logger`` is defined below, but it is only touched at call time.
    if module_name == "__main__":
        return default_logger
    return logging.getLogger(module_name)


def get_logger(module_name=None):
    """
    Get a logger with a specific module name.

    :param module_name: The name of the module. If None, uses the caller's module name.
    :return: A logger instance with the specified name.
    """
    if module_name is None:
        return _logger_of_call_site(sys._getframe(1).f_code)

    if module_name == "__main__":
        return default_logger

    return logging.getLogger(module_name)


# Default logger with the main application name
default_logger = get_logger("NeoXtractor")


def setup_logger(level=None):
    """Setup logging for NeoXtractor.

    :param level: Optional level override (name, numeric string, or int).
        Resolution order: *level* argument, then ``LOG_LEVEL`` environment
        variable, then INFO.  Callers that parse CLI arguments (main.py)
        pass their value in explicitly; this module never reads them.
    """
    resolved = level if level is not None else os.environ.get("LOG_LEVEL")
    warning = None
    if resolved is not None:
        log_level, warning = _coerce_level(resolved)
    else:
        log_level = logging.INFO

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        handlers=[
            logging.StreamHandler()  # StreamHandler defaults to stderr
        ],
    )

    if warning:
        get_logger().warning(warning)

    get_logger().debug("Logger initialized with level: %s", log_level)


# ----------------------------------------------------------------------
# Optional Qt message bridge (installed lazily, binding-agnostic)
# ----------------------------------------------------------------------

# QtMsgType values are identical across PySide6/PyQt6:
# 0 debug, 1 info, 2 warning, 3 critical, 4 fatal.
_QT_LEVELS = {
    0: logging.DEBUG,
    1: logging.INFO,
    2: logging.WARNING,
    3: logging.CRITICAL,
    4: logging.FATAL,
}


def _qt_message_handler(mode, _context, message):
    """Route a Qt log message into the project log stream (logger 'Qt')."""
    qt_logger = logging.getLogger("Qt")
    level = _QT_LEVELS.get(int(mode))
    if level is None:
        level = logging.INFO
    qt_logger.log(level, message)


_qt_installed_binding = None


def enable_qt_log_handling(binding: str = "PySide6"):
    """Install the Qt -> logging bridge using the given Qt binding.

    Kept out of module import on purpose: core stays Qt-free unless the GUI
    layer explicitly opts in.  ``binding`` may be ``"PySide6"`` (default) or
    ``"PyQt6"``; both expose ``QtCore.qInstallMessageHandler`` and share the
    same QtMsgType numbering, so the handler itself is binding-agnostic.

    :return: True if installed (or already installed with the same binding).
    """
    global _qt_installed_binding
    if _qt_installed_binding == binding:
        return True
    qtcore = importlib.import_module(f"{binding}.QtCore")
    qtcore.qInstallMessageHandler(_qt_message_handler)
    _qt_installed_binding = binding
    return True
