"""Hashes used by Wwise: event-name FNV-1 and vfs path key xxh32."""

from __future__ import annotations

import struct

MASK32 = 0xFFFFFFFF
FNV_OFFSET = 2166136261
FNV_PRIME = 16777619

_P1 = 0x9E3779B1
_P2 = 0x85EBCA77
_P3 = 0xC2B2AE3D
_P4 = 0x27D4EB2F
_P5 = 0x165667B1


def fnv1(name: str) -> int:
    """Wwise/ak FNV-1 32-bit hash of an event name (case-insensitive)."""
    name = name.lower()
    value = FNV_OFFSET
    for byte in name.encode("utf-8"):
        value = (value * FNV_PRIME) & MASK32
        value ^= byte
    return value & MASK32


def _rotl(value: int, bits: int) -> int:
    return ((value << bits) | (value >> (32 - bits))) & MASK32


def xxh32(data: bytes, seed: int = 0) -> int:
    """xxHash32, used for Wwise vfs path keys (seed 163 in this game)."""
    length = len(data)
    index = 0

    if length >= 16:
        v1 = (seed + _P1 + _P2) & MASK32
        v2 = (seed + _P2) & MASK32
        v3 = seed & MASK32
        v4 = (seed - _P1) & MASK32
        while index <= length - 16:
            v1 = (_rotl((v1 + struct.unpack_from("<I", data, index)[0] * _P2) & MASK32, 13) * _P1) & MASK32
            v2 = (_rotl((v2 + struct.unpack_from("<I", data, index + 4)[0] * _P2) & MASK32, 13) * _P1) & MASK32
            v3 = (_rotl((v3 + struct.unpack_from("<I", data, index + 8)[0] * _P2) & MASK32, 13) * _P1) & MASK32
            v4 = (_rotl((v4 + struct.unpack_from("<I", data, index + 12)[0] * _P2) & MASK32, 13) * _P1) & MASK32
            index += 16
        value = (_rotl(v1, 1) + _rotl(v2, 7) + _rotl(v3, 12) + _rotl(v4, 18)) & MASK32
    else:
        value = (seed + _P5) & MASK32

    value = (value + length) & MASK32

    while index <= length - 4:
        value = (value + struct.unpack_from("<I", data, index)[0] * _P3) & MASK32
        value = (_rotl(value, 17) * _P4) & MASK32
        index += 4

    while index < length:
        value = (value + data[index] * _P5) & MASK32
        value = (_rotl(value, 11) * _P1) & MASK32
        index += 1

    value ^= value >> 15
    value = (value * _P2) & MASK32
    value ^= value >> 13
    value = (value * _P3) & MASK32
    value ^= value >> 16
    return value & MASK32
