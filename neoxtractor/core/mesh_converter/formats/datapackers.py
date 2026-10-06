import struct

# Utility functions for writing various data types to binary streams


def write_uint64(value: int) -> bytes:
    """Write unsigned 64-bit integer to binary stream."""
    return struct.pack("Q", value)


def write_uint32(value: int) -> bytes:
    """Write unsigned 32-bit integer to binary stream."""
    return struct.pack("I", value)


def write_uint16(value: int) -> bytes:
    """Write unsigned 16-bit integer to binary stream."""
    return struct.pack("H", value)


def write_sint16(value: int) -> bytes:
    """Write signed 16-bit integer to binary stream."""
    return struct.pack("h", value)


def write_uint8(value: int) -> bytes:
    """Write unsigned 8-bit integer to binary stream."""
    return struct.pack("B", value)


def write_float(value: float) -> bytes:
    """Write float to binary stream."""
    return struct.pack("<f", value)


def write_half_float(value: float) -> bytes:
    """Write half-precision floating-point to binary stream."""
    return struct.pack("e", value)
