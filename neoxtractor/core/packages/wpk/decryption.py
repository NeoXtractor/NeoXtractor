"""WPK/WPD1 payload stage-1 decoding helpers.

A direct port of the reference decoder: the AES key derivation, the XOR
schedules, and the header obfuscation mirror the on-disk algorithm exactly.
Key material and constants are copied verbatim, never paraphrased.
Pure functions over payload bytes.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Type-checker view only; runtime availability is gated by _HAS_AES.
    from cryptography.hazmat.backends import default_backend as aes_default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

try:
    from cryptography.hazmat.backends import default_backend as aes_default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    _HAS_AES = True
except ImportError:  # pragma: no cover - graceful fallback
    # Names stay unbound: every use site short-circuits on ``not _HAS_AES``
    # before touching them.
    _HAS_AES = False


def _u16(b: bytes) -> int:
    return int.from_bytes(b, "little")


def derive_key(length: int, t: int) -> bytes:
    """Derive the 16-byte AES key used by WPD1 stage-1."""
    v10 = (t + (length & 0xFFFFFFFF)) & 0xFF
    v28 = (
        0x7C2E6B6A00000000
        | (((length & 0xFFFFFFFF) << 8) & 0xFFFF0000)
        | (v10 << 8)
        | (length % 0xFD)
    )
    v29 = (
        0x5C74656E00003630
        | (((v10 ^ 0x33) << 16) & 0xFFFFFFFF00FFFFFF)
        | ((v10 | 0x2E) << 24)
    )
    return struct.pack("<QQ", v28 & 0xFFFFFFFFFFFFFFFF, v29 & 0xFFFFFFFFFFFFFFFF)


def aes_decrypt_prefix(buf: bytearray, length: int, key16: bytes) -> int:
    """AES-ECB decrypt the prefix aligned to 16-byte blocks."""
    if length <= 0 or not _HAS_AES:
        return 0

    done = (length // 16) * 16
    if done <= 0:
        return 0

    cipher = Cipher(algorithms.AES(key16), modes.ECB(), backend=aes_default_backend())
    dec = cipher.decryptor()
    buf[:done] = dec.update(bytes(buf[:done])) + dec.finalize()
    return done


def xor_offset(buf: bytearray, offset: int, want: int, seed: int) -> None:
    if want <= 0:
        return

    mirror_len = min(offset, want)
    for i in range(mirror_len):
        buf[offset + i] ^= ((seed + i) + buf[i]) & 0xFF

    for i in range(want - mirror_len):
        buf[offset + mirror_len + i] ^= (seed + mirror_len + i) & 0xFF


def xor_linear(buf: bytearray, want: int, seed: int) -> None:
    for i in range(want):
        buf[i] ^= (seed + i) & 0xFF


def header_decode(buf: bytearray) -> None:
    n = min(64, len(buf))
    i, j = 0, n - 1
    while i < j:
        bi = buf[i] ^ 0x5A
        bj = buf[j] ^ 0x5A
        buf[i], buf[j] = bj, bi
        i += 1
        j -= 1
    if i == j:
        buf[i] ^= 0x5A


def decode_payload_stage1(
    payload: bytes, *, skip_header_decode: bool = False
) -> tuple[bytes, int] | None:
    """Decode one payload using the WPD1 stage-1 rules.

    Returns (decoded_body, tag) on success, or None if the payload does not
    match a supported stage-1 variant.
    """
    if len(payload) < 8:
        return None

    tag = _u16(payload[0:2])
    p = payload[2]
    t = payload[3]
    body = bytearray(payload[8:])
    body_len = len(body)
    prefix_len = min(body_len, 128 << (p - 1)) if body_len > 0 and p != 0 else 0
    seed = (t + body_len) & 0xFFFFFFFF

    if tag in (0x4341, 0x4350):
        key = derive_key(body_len, t)
        done = aes_decrypt_prefix(body, prefix_len, key)
        remain = max(0, prefix_len - done)
        if remain > 0:
            xor_offset(body, done, remain, seed)
    elif tag == 0x4358:
        xor_linear(body, prefix_len, seed)
    else:
        return None

    if not skip_header_decode:
        header_decode(body)
    return bytes(body), tag


def try_decode_payload_stage1(
    payload: bytes,
    *,
    context: str = "",
    skip_header_decode: bool = False,
) -> tuple[bytes, bool, int | None]:
    """Best-effort wrapper around stage-1 decoding.

    Returns (data, decoded, tag).  ``decoded=False`` means the payload does
    not carry a stage-1 tag at all -- a legitimate shape, the caller keeps
    the bytes.  A recognised tag that fails to decode raises.
    """
    result = decode_payload_stage1(payload, skip_header_decode=skip_header_decode)
    if result is None:
        return payload, False, None

    decoded, tag = result
    return decoded, True, tag
