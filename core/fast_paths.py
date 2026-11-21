"""
Faster resource path helpers, with identical results.

normalize_path and murmur3_32 (core.npk.asset_paths) and mesh_hash (core.npk.npkhash_v1) are pure Python and
run 0.5-3.6 million times each when the Characters tab scans a pack. The bodies here compute the same values
with less work per call:

- normalize_path skips the separator regex when the path has no '/' and no doubled '\\' (the regex would not
  change it);
- murmur3_32 reads all 4-byte blocks in one go and has the mixing steps inline instead of a nested function;
- mesh_hash is the same arithmetic with its constants inline;
- both hashes remember their results (one scan hashes the same path several times over: 1.1 million murmur3 and
  0.56 million mesh hashes for some 0.2 million distinct paths), up to HASH_MEMO entries each.

install() gives the existing functions these bodies in place (their globals and defaults stay), so modules
that imported them by name use the new bodies too. _tools/check_fast_paths.py compares them with the
originals on every resource path of the install (967k inputs: identical); tests/test_fast_paths.py on a sample.
"""

import re
import struct
import sys

from core.npk import asset_paths as _asset_paths
from core.npk import npkhash_v1 as _npkhash


def normalize_path(path):
    text = str(path).strip()
    if "/" in text or "\\\\" in text:
        text = re.sub("[\\\\/]+", "\\\\", text)
    return text.lstrip(".\\").lower()


HASH_MEMO = 600_000                               # entries per hash memo (~70 MB at most for both)
_murmur_memo: dict = {}
_mesh_memo: dict = {}


def murmur3_32(text, seed=2538058380):            # the original's default seed
    memo = _murmur_memo if seed == 2538058380 else None
    if memo is not None:
        hit = memo.get(text)
        if hit is not None:
            return hit
    data = text.encode("utf-8")
    size = len(data)
    end = size & -4
    h = seed
    if end:
        for k in memoryview(data)[:end].cast("I"):      # little-endian 32-bit blocks (install() checks)
            k = (k * 0xCC9E2D51) & 0xFFFFFFFF
            k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
            h ^= (k * 0x1B873593) & 0xFFFFFFFF
            h = ((h << 13) | (h >> 19)) & 0xFFFFFFFF
            h = (h * 5 + 0xE6546B64) & 0xFFFFFFFF
    if end < size:
        k = (int.from_bytes(data[end:], "little") * 0xCC9E2D51) & 0xFFFFFFFF
        k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
        h ^= (k * 0x1B873593) & 0xFFFFFFFF
    h ^= size
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & 0xFFFFFFFF
    h ^= h >> 16
    if memo is not None and len(memo) < 600_000:  # HASH_MEMO (a constant here: the body runs in another module)
        memo[text] = h
    return h


def mesh_hash(text):
    hit = _mesh_memo.get(text)
    if hit is not None:
        return hit
    raw = text.lower().encode("ascii", "ignore")
    length = (len(raw) + 3) >> 2
    data = list(struct.unpack("<" + "I" * length, raw + b"\x00" * (length * 4 - len(raw))))
    data += [2615624776, 1727278152]
    hash_, state, tweak = 4110059816, 933775118, 2002301995
    for chunk in data:
        hash_ = ((hash_ << 1) | (hash_ >> 31)) & 0xFFFFFFFF
        e = 645597969 ^ hash_
        a = chunk & 0xFFFFFFFF
        state ^= a
        tweak ^= a
        f = ((((e + tweak) | 33818625) & 3220144095) * state) & 0xFFFFFFFFFFFFFFFF
        a = f & 0xFFFFFFFF
        b = f >> 32
        if b:
            a = (a + 1) & 0xFFFFFFFF
        f = (a + b) & 0xFFFFFFFFFFFFFFFF
        a = f & 0xFFFFFFFF
        if f >> 32:
            a = (a + 1) & 0xFFFFFFFF
        b = ((e + state) | 8405025) & 2113862655
        state = a
        f = (tweak * b) & 0xFFFFFFFFFFFFFFFF
        a = f & 0xFFFFFFFF
        b = f >> 32
        f = (b + b) & 0xFFFFFFFFFFFFFFFF
        b = f & 0xFFFFFFFF
        if f >> 32:
            a = (a + 1) & 0xFFFFFFFF
        f = (a + b) & 0xFFFFFFFFFFFFFFFF
        a = f & 0xFFFFFFFF
        if f >> 32:
            a = (a + 2) & 0xFFFFFFFF
        tweak = a
    value = (state ^ tweak) & 0xFFFFFFFF
    if len(_mesh_memo) < 600_000:                  # HASH_MEMO
        _mesh_memo[text] = value
    return value


# name -> the original body, kept so tests can compare against it
ORIGINAL_CODE: dict = {}

REPLACEMENTS = ((_asset_paths, "normalize_path", normalize_path, ("re",)),
                (_asset_paths, "murmur3_32", murmur3_32, ()),
                (_npkhash, "mesh_hash", mesh_hash, ("struct",)))
# globals the new bodies use, given to the modules they run in (the same dicts, so tests see the memos)
SHARED = {_asset_paths: {"_murmur_memo": _murmur_memo}, _npkhash: {"_mesh_memo": _mesh_memo}}


def install() -> bool:
    """False when already installed, on a big-endian machine, or the helpers aren't the expected functions."""
    if sys.byteorder != "little":
        return False
    targets = []
    for module, name, fast, needs in REPLACEMENTS:
        target = getattr(module, name, None)
        code = getattr(target, "__code__", None)
        if code is None or getattr(target, "_fast", False):
            return False
        if code.co_argcount != fast.__code__.co_argcount or code.co_freevars:
            return False
        if not all(n in vars(module) for n in needs):
            return False
        if any(name in vars(module) and vars(module)[name] is not value for name, value in SHARED.get(module, {}).items()):
            return False                               # a name the body needs is taken in that module
        targets.append((target, fast))
    for module, names in SHARED.items():
        vars(module).update(names)
    for target, fast in targets:
        ORIGINAL_CODE[target.__name__] = target.__code__
        target.__code__ = fast.__code__
        target._fast = True  # pylint: disable=protected-access
    return True
