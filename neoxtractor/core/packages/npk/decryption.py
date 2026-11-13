"""NPK entry payload decryption: XOR variants keyed by index metadata.

Every function takes plain values (bytes plus index fields) and returns new
bytes.
"""

from neoxtractor.core.packages.npk.enums import DecryptionType

from .eggyparty_codes import decrypt_mode3_block


def decrypt_eggparty_index(index_data: bytes | bytearray):
    """Decrypt whole 16-byte blocks; a short tail is left unchanged."""
    round_keys = [
        1466294906,
        1460669224,
        2458039086,
        3599020919,
        687260292,
        2570908058,
        1885258245,
        245923009,
        1693573352,
        2982818590,
        3915527071,
        2130099908,
        448182585,
        3577467894,
        1487405185,
        2543095131,
        909181480,
        3482151631,
        2375275383,
        3476852186,
        4146422541,
        4189874407,
        1109309880,
        1118789293,
        1149249532,
        244911082,
        3148009823,
        11659029,
        2003874988,
        1243163670,
        3040598709,
        3138598474,
        3914532381,
        1030330554,
        4280481443,
        237563135,
        1147416806,
        3560611495,
        3259723289,
        4043971164,
        2557374349,
        813845727,
        4047979994,
        2489865706,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        10,
    ]

    buffer = bytearray(index_data)
    block_count = len(buffer) >> 4
    for block_index in range(block_count):
        offset = block_index * 16
        buffer[offset : offset + 16] = decrypt_mode3_block(
            bytes(buffer[offset : offset + 16]), round_keys
        )

    return bytes(buffer)


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
