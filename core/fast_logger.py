"""
A faster core.logger.get_logger.

The original finds the caller's module with inspect.stack(), which builds every frame of the call stack and
reads source lines for each one, on every call. Reading NPK files calls get_logger() thousands of times, so
that cost shows up when opening packs and building the wardrobe lists. core/logger.py exists only as
bytecode in the exe, so install() gives the existing function a new body in place: every module that already
did `from core.logger import get_logger` gets the faster version too. Loggers are the same, named after the
calling module (logging.basicConfig on the root logger handles them all).
"""

import inspect
import logging

from core import logger as _logger
from core.logger import default_logger


def _get_logger(module_name=None):
    # Once installed this runs with core.logger's globals; it imports the same three names, so it reads (and
    # checks) the same here
    if module_name is None:
        frame = inspect.currentframe().f_back
        module_name = frame.f_globals.get("__name__", "unknown") if frame is not None else "unknown"
    if module_name == "__main__":
        return default_logger
    return logging.getLogger(module_name)


def install() -> bool:
    """Swap in the fast body; False when get_logger isn't the function this was written for."""
    target = getattr(_logger, "get_logger", None)
    if target is None or getattr(target, "_fast", False):
        return False
    code = target.__code__
    names = vars(_logger)
    if code.co_argcount != 1 or code.co_freevars or not all(n in names for n in ("inspect", "logging", "default_logger")):
        return False
    target.__code__ = _get_logger.__code__
    target._fast = True  # pylint: disable=protected-access
    return True
