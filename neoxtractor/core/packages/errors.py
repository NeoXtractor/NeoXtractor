"""Exception types for the packages facade.

A base :class:`PackageError` lets callers catch the whole family; each
subclass carries the structured fields a UI or CLI needs to explain the
failure without string-matching the message.

When a reference's *kind* is valid for the format but no entry matches it,
that is :class:`NoSuchEntry`; when the format cannot resolve that kind at
all, that is :class:`UnsupportedRef`.  :class:`OperationCancelled` is a
control signal, not a fault.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from neoxtractor.core.packages.base import RefKind


class PackageError(Exception):
    """Base class for all packages-facade errors."""


class UnknownPackageFormat(PackageError):
    """A forced ``format=`` name is not present in the registry."""

    def __init__(self, format_name: str, known: list[str] | None = None):
        self.format_name = format_name
        self.known = sorted(known or [])
        hint = f" Known formats: {', '.join(self.known)}." if self.known else ""
        super().__init__(f"Unknown package format: {format_name!r}.{hint}")


class NoPackageReader(PackageError):
    """No registered container module claimed the file in automatic mode."""

    def __init__(self, path: str, tried: list[str] | None = None):
        self.path = path
        self.tried = sorted(tried or [])
        hint = f" Modules asked: {', '.join(self.tried)}." if self.tried else ""
        super().__init__(f"No package reader for {path!r}.{hint}")


class PackageFormatMismatch(PackageError):
    """A forced format was requested but its matches() rejected the file."""

    def __init__(self, format_name: str, path: str):
        self.format_name = format_name
        self.path = path
        super().__init__(
            f"{path!r} does not match forced format {format_name!r}."
        )


class OptionsTypeMismatch(PackageError):
    """Supplied options are not an instance of the format's OPTIONS_TYPE."""

    def __init__(self, format_name: str, expected: str, got: str):
        self.format_name = format_name
        self.expected = expected
        self.got = got
        super().__init__(
            f"Format {format_name!r} expects options of type {expected!r}, "
            f"got {got!r}."
        )


class ProtocolError(PackageError):
    """A container module declared something the protocol forbids.

    Raised by a value-object constructor that validates at build time (for
    example :class:`Want` rejecting a '..' hop in the middle of a glob) or by
    companion derivation; it always indicates a bug in the module, never bad
    input data.
    """


class UnsupportedRef(PackageError):
    """The format cannot resolve get_res references of the given kind."""

    def __init__(
        self, format_name: str, kind: "RefKind", supported: list["RefKind"]
    ):
        self.format_name = format_name
        self.kind = kind
        self.supported = list(supported)
        super().__init__(
            f"Format {format_name!r} does not support get_res kind "
            f"{kind!r}; supported: {', '.join(str(k.value) for k in self.supported)}."
        )


class NoSuchEntry(PackageError):
    """The reference kind is valid but no entry matches this ref."""

    def __init__(self, ref: object, kind: "RefKind"):
        self.ref = ref
        self.kind = kind
        super().__init__(f"No entry for ref {ref!r} of kind {kind!r}.")


class IncompleteUnit(PackageError):
    """load refuses a unit whose required companion is absent.

    Raised from load(), never from matches(): companion completeness is a
    per-format load-time policy.
    """

    def __init__(self, format_name: str, entry: str, missing: list[str]):
        self.format_name = format_name
        self.entry = entry
        self.missing = list(missing)
        super().__init__(
            f"Cannot load {entry!r} as {format_name!r}: missing required "
            f"companion(s): {', '.join(self.missing)}."
        )


class MissingIndexedResourceError(EOFError, PackageError):
    """A record exists but its payload is not locally available (status MISSING).

    Subclasses EOFError so callers guarding reads with ``except EOFError``
    catch it; MISSING is an availability gap, not a content fault.
    """

    def __init__(self, ref: object = None, container: str = ""):
        self.ref = ref
        self.container = container
        where = f" in {container!r}" if container else ""
        super().__init__(f"Indexed resource {ref!r} is not present{where}.")


class OperationCancelled(PackageError):
    """The driver observed a cancel signal and stopped at a safe point."""

    def __init__(self, stage: str = ""):
        self.stage = stage
        where = f" during stage {stage!r}" if stage else ""
        super().__init__(f"Operation cancelled{where}.")


class PartnerDeclined(PackageError):
    """Reserved: a child's attach to a parent was refused.

    The contract is named before a format needs it; attach currently
    reports ``complete=False`` plus a note instead of raising.
    """


# --- content faults: the bytes exist, the content does not -----------------
#
# Three user remedies live here, and the UI tells them apart through
# structured fields, not messages: supply an option (or drop the request),
# rename or re-download a file, or upgrade this build.  Within
# PayloadDecodeError, ``reason`` carries the distinction.


DecodeReason = Literal[
    "missing-option",      # a ReadOptions field the payload needs was withheld
    "unknown-algorithm",   # the index/header names a codec this build lacks
    "corrupt",             # recognized as this layer, but the bytes do not decode
    "missing-dependency",  # the codec is known and its Python package is absent
]


class PayloadDecodeError(PackageError):
    """Bytes that cannot be turned into decoded content.

    Raised by :meth:`Archive.get_res` with ``decoded=True`` when the pipeline
    cannot deliver -- a withheld option, an unknown algorithm, a corrupt
    payload, or a missing dependency -- and by a container's load path when the
    store's own bytes are damaged or truncated (``entry`` stays ``None``).
    The stored bytes stay available through ``decoded=False``, so this names a
    content fault, never a lost file.

    ``reason`` is machine-readable: a caller that can offer a remedy (fill in a
    key, install a package) branches on it instead of pattern-matching the text.
    """

    def __init__(
        self,
        format_name: str,
        container: str,
        reason: DecodeReason,
        detail: str,
        *,
        entry: int | str | None = None,
    ):
        self.format = format_name
        self.container = container
        self.reason = reason
        self.detail = detail
        self.entry = entry
        where = f" entry {entry}" if entry is not None else ""
        super().__init__(
            f"{container}{where} cannot be decoded ({reason}): {detail}"
        )


class IntegrityMismatch(PackageError):
    """A file's own header contradicts the identity vouching for it.

    Raised at load, never at read: the store is claimed on its magic, then its
    header is honoured against a second source -- its filename, or an index
    record that points at it.  The bytes are fine, the name or index is wrong,
    so the remedy is rename or repair.  ``expected_filename`` names the file
    that *would* be correct whenever the fix is a rename.
    """

    def __init__(
        self,
        format_name: str,
        container: str,
        field: str,
        reported: object,
        expected: object,
        *,
        expected_filename: str | None = None,
        offset: int | None = None,
    ):
        self.format = format_name
        self.container = container
        self.field = field
        self.reported = reported
        self.expected = expected
        self.expected_filename = expected_filename
        self.offset = offset
        where = f" at 0x{offset:X}" if offset is not None else ""
        parts = [
            f"{container}{where}: {field} contradicts its identity -- "
            f"the file says {reported!r} but it must be {expected!r}"
        ]
        if expected_filename is not None:
            parts.append(f"; expected filename '{expected_filename}'")
        super().__init__("".join(parts))


class VersionUnsupported(PackageError):
    """A container's version is beyond what this build reads.

    Raised at load: the whole store is unreadable, not one payload.  The
    remedy is a newer build, so the message says which versions are readable.
    """

    def __init__(
        self, format_name: str, container: str, version: object, supported: str
    ):
        self.format = format_name
        self.container = container
        self.version = version
        self.supported = supported
        super().__init__(
            f"{container}: unsupported {format_name.upper()} version {version} "
            f"(this build reads {supported} only)"
        )
