"""NPK entry payload decryption: XOR variants keyed by index metadata.

Every function takes plain values (bytes plus index fields) and returns new
bytes.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from neoxtractor.core.packages.npk.enums import DecryptionType

from .eggyparty_codes import (
    EGGPARTY_RAW_KEYS,
    decrypt_mode3_block,
    make_dec_schedule,
)

if TYPE_CHECKING:
    # Type-checker view only; runtime availability is gated by HAS_AES.
    from cryptography.hazmat.primitives.ciphers import (
        algorithms,
        modes,
    )

try:
    from cryptography.hazmat.primitives.ciphers import (
        Cipher,
        algorithms,
        modes,
    )

    HAS_AES = True
except ImportError:  # pragma: no cover - graceful fallback
    # Names stay unbound: every use site short-circuits on ``not HAS_AES``
    # before touching them.
    HAS_AES = False

# AES-128 key of the v2 encrypted index.
_V2_INDEX_KEY = b"g58i^C04SW!@e}ad"


def decrypt_v2_index(data: bytes) -> bytes:
    """AES-128-ECB decrypt a v2 index region.

    The engine decrypts whole 16-byte blocks in place, so only a length
    already aligned to the block size can be reversed; anything else is
    not this layout.
    """
    if not HAS_AES:
        raise ValueError("AES support requires the cryptography package")
    if len(data) % 16:
        raise ValueError("v2 index length is not block aligned")
    cipher = Cipher(algorithms.AES(_V2_INDEX_KEY), modes.ECB())
    decryptor = cipher.decryptor()
    return decryptor.update(data) + decryptor.finalize()


def _signatures_ascending(data: bytes | bytearray, info_size: int) -> bool:
    """Check whether record signatures are in non-decreasing order."""
    count = len(data) // info_size
    if count < 2:
        return True
    prev = struct.unpack_from("<I", data, 0)[0]
    for i in range(1, count):
        cur = struct.unpack_from("<I", data, i * info_size)[0]
        if cur < prev:
            return False
        prev = cur
    return True


def decrypt_eggparty_index(
    index_data: bytes | bytearray, info_size: int = 28
) -> bytes:
    """Decrypt enc3 index blocks, trying each known EggParty key in turn.

    The first key whose decryption produces non-decreasing signatures is
    accepted.  A short tail (not a multiple of 16) is left unchanged.
    Raises ``ValueError`` if no known key produces a valid index.
    """
    block_count = len(index_data) >> 4
    tail = index_data[block_count * 16 :]

    for raw_key in EGGPARTY_RAW_KEYS:
        schedule = make_dec_schedule(raw_key)
        buffer = bytearray(index_data[: block_count * 16])
        for bi in range(block_count):
            off = bi * 16
            buffer[off : off + 16] = decrypt_mode3_block(
                bytes(buffer[off : off + 16]), schedule
            )
        if _signatures_ascending(buffer, info_size):
            return bytes(buffer) + tail

    raise ValueError("no known EggParty key decrypts this index")


def decrypt_entry(
    data: bytes,
    encrypt_flag: DecryptionType,
    crc: int,
    file_length: int,
    file_original_length: int,
    key: int | None = None,
) -> bytes | None:
    """Decrypt one payload according to its encryption type.

    - BASIC_XOR: XOR with a key array derived from ``key``; only the first
      128 bytes (0x80) are decrypted.  Without ``key`` the payload cannot be
      decrypted and ``None`` is returned.
    - ADVANCED_XOR: XOR with a key array derived from ``crc`` and
      ``file_original_length``; the decrypted range is located from the
      index metadata.
    - INCREMENTAL_XOR: XOR with an incrementing key derived from ``crc`` and
      ``file_original_length``; the decrypted range is located from the
      index metadata.

    Returns the decrypted bytes, or ``None`` when the option ``key`` is
    required but missing.
    """

    buf = bytearray(data)

    if encrypt_flag == DecryptionType.BASIC_XOR:
        if key is None:
            return None

        size = min(file_length, 0x80)

        key_array = [(key + x) & 0xFF for x in range(0, 0x100)]

        for j in range(size):
            buf[j] ^= key_array[j % 0xFF]

    elif encrypt_flag == DecryptionType.ADVANCED_XOR:
        b = crc ^ file_original_length

        start = 0
        size = file_length

        if size > 0x80:
            start = (crc >> 1) % (size - 0x80)
            size = 2 * file_original_length % 0x60 + 0x20

        key_array = [(x + b) & 0xFF for x in range(0, 0x81)]

        for j in range(size):
            buf[start + j] ^= key_array[j % 0x80]

    elif encrypt_flag == DecryptionType.INCREMENTAL_XOR:
        v3 = int(file_original_length)
        v4 = int(crc)

        crc_key = (v3 ^ v4) & 0xFF
        offset = 0
        length = 0

        if file_length <= 0x80:
            length = file_length
        else:
            offset = (v3 >> 1) % (file_length - 0x80)
            length = ((v4 << 1) & 0xFFFFFFFF) % 0x60 + 0x20

        for xx in range(offset, offset + length):
            buf[xx] ^= crc_key
            crc_key = (crc_key + 1) & 0xFF

    return bytes(buf)
