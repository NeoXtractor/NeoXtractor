"""Structured target descriptors for the resource convert protocol.

A ``Target`` declares one output form a resource module can convert its
canonical object into.  ``convert()`` is still dispatched by ``name``; the
extra fields tell callers *what to do* with the result:

- ``kind="file"``  -> convert() returns bytes/str that can be saved as a
  standalone file; ``ext`` suggests the file extension for save dialogs.
- ``kind="view"``  -> convert() returns an in-memory object (e.g. a numpy
  array) meant for direct UI consumption, not for saving.

Plain strings may be used anywhere a Target is expected; they are coerced
via :func:`as_target` with ``kind="file"`` and ``ext="." + name``.
"""

from __future__ import annotations

from typing import NamedTuple, Union


class Target(NamedTuple):
    name: str
    kind: str = "file"  # "file" | "view"
    ext: str = ""
    desc: str = ""


def as_target(t: Union[str, Target]) -> Target:
    """Coerce a bare target name into a file-kind Target."""
    if isinstance(t, Target):
        return t
    return Target(name=t, kind="file", ext="." + t)


def target_names(targets) -> list[str]:
    """Extract bare names from a mixed iterable of str/Target."""
    return [as_target(t).name for t in targets]
