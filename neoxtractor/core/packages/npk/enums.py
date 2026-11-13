"""Enums for NPK file types, compression, and encryption."""

from enum import IntEnum


def is_unknown(flag: IntEnum) -> bool:
    """True when *flag* was fabricated by ``_missing_`` from an unrecognized value.

    Invariant: fabricated members are named ``UNKNOWN(n)`` and no defined
    member name starts with that prefix, so the name is the recognition test.
    Alternatives fail: ``cls(value)`` never raises (``_missing_`` fabricates a
    member), and ``value in cls`` does not consult ``_missing_``.
    """
    return flag.name.startswith("UNKNOWN")


class NPKFileType(IntEnum):
    """Enum defining the NPK file types."""

    NXPK = 0
    EXPK = 1

    @classmethod
    def get_name(cls, flag: int) -> str:
        """Get the human-readable name of the NPK file type."""
        try:
            return cls(flag).name
        except ValueError:
            return f"UNKNOWN({flag})"


class CompressionType(IntEnum):
    """Enum defining the compression types used in NPK files."""

    NONE = 0
    ZLIB = 1
    LZ4 = 2
    ZSTANDARD = 3

    @classmethod
    def get_name(cls, flag: int) -> str:
        """Get the human-readable name of the compression algorithm."""
        try:
            return cls(flag).name
        except ValueError:
            return f"UNKNOWN({flag})"

    @classmethod
    def _missing_(cls, value):
        if not isinstance(value, int):
            raise ValueError(f"Expected an integer value, got {type(value).__name__}")
        obj = int.__new__(cls, int(value))
        obj._name_ = f"UNKNOWN({value})"
        obj._value_ = int(value)
        return obj


class DecryptionType(IntEnum):
    """Enum defining the file flag encryption types."""

    NONE = 0
    BASIC_XOR = 1
    ADVANCED_XOR = 2
    INCREMENTAL_XOR = 4

    @classmethod
    def get_name(cls, flag: int) -> str:
        """Get the human-readable name of the encryption algorithm."""
        try:
            return cls(flag).name
        except ValueError:
            return f"UNKNOWN({flag})"

    @classmethod
    def _missing_(cls, value):
        if not isinstance(value, int):
            raise ValueError(f"Expected an integer value, got {type(value).__name__}")
        obj = int.__new__(cls, int(value))
        obj._name_ = f"UNKNOWN({value})"
        obj._value_ = int(value)
        return obj
