# default npk hashing algorithm reimplemented by aexadev on 22/06/25

import struct

MASK32 = 0xFFFFFFFF


def mesh_hash(text: str) -> int:
    raw = text.encode("ascii", "ignore")
    length = (len(raw) + 3) >> 2
    padded = raw + b"\x00" * (length * 4 - len(raw))

    data = list(struct.unpack("<" + "I" * length, padded))
    data += [0x9BE74448, 0x66F42C48]
    hash_ = 0xF4FA8928
    state = 0x37A8470E
    tweak = 0x7758B42B

    for chunk in data:
        e = 0x267B0B11
        hash_ = ((hash_ << 1) | (hash_ >> 31)) & MASK32
        e ^= hash_

        a = chunk & MASK32
        state ^= a
        tweak ^= a

        b = ((e + tweak) | 0x02040801) & 0xBFEF7FDF
        f = (b * state) & 0xFFFFFFFFFFFFFFFF
        a = f & MASK32
        b = f >> 32
        if b:
            a = (a + 1) & MASK32

        f = (a + b) & 0xFFFFFFFFFFFFFFFF
        a = f & MASK32
        g = f >> 32
        if g:
            a = (a + 1) & MASK32

        b = ((e + state) | 0x00804021) & 0x7DFEFBFF
        state = a
        f = (tweak * b) & 0xFFFFFFFFFFFFFFFF
        a = f & MASK32
        b = f >> 32

        f = (b + b) & 0xFFFFFFFFFFFFFFFF
        b = f & MASK32
        g = f >> 32
        if g:
            a = (a + 1) & MASK32

        f = (a + b) & 0xFFFFFFFFFFFFFFFF
        a = f & MASK32
        g = f >> 32
        if g:
            a = (a + 2) & MASK32

        tweak = a

    return (state ^ tweak) & MASK32
