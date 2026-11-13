"""Exception types for the processors facade.

The exception contract distinguishes "this is not my format" from "this is
my format but decoding failed":

- automatic mode with no match -> ``read``/``convert`` return ``None``; the
  caller treats it as "unrecognised".
- a forced ``format=`` that is not registered -> :class:`UnknownFormat`.
- a forced ``format=`` whose probe rejects the source -> :class:`FormatMismatch`
  (no fall-back to the runner-up).
- best (or asserted) claimant is probe-only -- recognised but with no
  ``read()`` -> :class:`ProcessorNoReader` ("we know what this is, but cannot
  decode it"), so callers branch on the type instead of string-matching a
  generic :class:`ProcessorError`.
- :func:`convert` with a target absent from ``TARGETS`` ->
  :class:`UnknownTarget`.  Formats without ``TARGETS`` are not pre-checked;
  their ``convert`` raises on its own.
- a plugin that cannot be imported or fails protocol validation ->
  :class:`PluginError` (explicit loading entry point only; the passive
  environment scan logs and skips).
- a claimed format whose payload is corrupt -> the underlying ``read``
  exception propagates untouched for the caller to record.
"""

from __future__ import annotations


class ProcessorError(Exception):
    """Base class for all processors-facade errors."""


class UnknownFormat(ProcessorError):
    """A forced ``format=`` name is not present in the registry."""

    def __init__(self, format_name: str, known: list[str] | None = None):
        self.format_name = format_name
        self.known = sorted(known or [])
        hint = f" Known formats: {', '.join(self.known)}." if self.known else ""
        super().__init__(f"Unknown format: {format_name!r}.{hint}")


class FormatMismatch(ProcessorError):
    """A forced format was requested but its probe rejected the source."""

    def __init__(self, format_name: str, score: int = 0):
        self.format_name = format_name
        self.score = score
        super().__init__(
            f"Source does not match forced format {format_name!r} "
            f"(probe score {score})."
        )


class ProcessorNoReader(ProcessorError):
    """The source was identified, but the winning format cannot decode it.

    Raised when the asserted (forced mode) or highest-scoring (automatic
    mode) candidate has no ``read()`` -- a probe-only format such as
    ``unsupported``.  Carries the identification so the caller can label the
    payload (format/ext/score) even though decoding is impossible.
    """

    def __init__(self, format_name: str, ext: str = "", score: int = 0):
        self.format_name = format_name
        self.ext = ext
        self.score = score
        label = f" (ext {ext!r}, score {score})" if ext else f" (score {score})"
        super().__init__(
            f"Source identified as {format_name!r}{label}, but that format "
            f"is probe-only (no read()); it can label the payload, not decode it."
        )


class UnknownTarget(ProcessorError):
    """``convert`` was asked for a target the format does not declare."""

    def __init__(self, format_name: str, target: str, available: list[str]):
        self.format_name = format_name
        self.target = target
        self.available = list(available)
        hint = f" Available targets: {', '.join(self.available)}." if self.available else ""
        super().__init__(
            f"Format {format_name!r} does not provide target {target!r}.{hint}"
        )


class PluginError(ProcessorError):
    """A plugin file failed to load or does not satisfy the protocol."""

    def __init__(self, reason: str, path: str | None = None):
        self.reason = reason
        self.path = path
        loc = f" ({path})" if path else ""
        super().__init__(f"Plugin error{loc}: {reason}")
