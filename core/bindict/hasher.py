"""
wuqu223 reappeared on 2026.4.18
Hash functions for binary dictionary
Currently commented out as not needed for display purposes

def bindict_int_hash(n: int) -> int:
    '''Hash function for integers'''
    multiplier = 0xCBF29CE484222325
    high_32 = (n * multiplier) >> 32
    return high_32 & 0xFFFFFFFF


def bindict_string_hash(s: str) -> int:
    '''Hash function for strings'''
    data = s.encode('utf-8')
    length = len(data)

    if length == 0:
        return 0

    h = (data[0] << 7) ^ 0x78DDE6E6
    for c in data:
        h = (h * 0xF4243) ^ c
    h = h ^ length ^ 0xF1BBCDCC
    h = h & 0xFFFFFFFF

    h = ((h & 0xFF) << 24) | \
        ((h & 0xFF00) << 8) | \
        ((h & 0xFF0000) >> 8) | \
        ((h & 0xFF000000) >> 24)
    return h


def bindict_tuple_hash(tup: tuple) -> int:
    '''Hash function for tuples'''
    if len(tup) == 0:
        return 0x3c70706e

    h = 0x3c6ef373
    multiplier = 0xf4243
    iVar9 = len(tup) * 2 + 0x14256

    for elem in tup:
        if isinstance(elem, int):
            elem_hash = bindict_int_hash(elem)
        elif isinstance(elem, str):
            elem_hash = bindict_string_hash(elem)
        elif isinstance(elem, tuple):
            elem_hash = bindict_tuple_hash(elem)
        else:
            elem_hash = bindict_int_hash(int(elem))

        h = ((elem_hash ^ h) * multiplier) & 0xFFFFFFFF
        multiplier = (multiplier + iVar9) & 0xFFFFFFFF
        iVar9 = (iVar9 - 2) & 0xFFFFFFFF

    h = (h + 0x17cfb) & 0xFFFFFFFF

    h = ((h & 0xFF) << 24) | \
        ((h & 0xFF00) << 8) | \
        ((h & 0xFF0000) >> 8) | \
        ((h & 0xFF000000) >> 24)

    return h


def bindict_hash(obj) -> int:
    '''Main hash function dispatcher'''
    if isinstance(obj, int):
        return bindict_int_hash(obj)
    elif isinstance(obj, str):
        return bindict_string_hash(obj)
    elif isinstance(obj, tuple):
        return bindict_tuple_hash(obj)
    else:
        return bindict_int_hash(int(obj))
"""
