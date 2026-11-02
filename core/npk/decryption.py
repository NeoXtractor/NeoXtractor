"""Provides decompression functions."""

from core.logger import get_logger
from core.npk.class_types import NPKEntry, NPKEntryDataFlags
from core.npk.enums import DecryptionType

from .eggyparty_codes import decrypt_mode3_block


def decrypt_eggparty_index(index_data: bytes | bytearray):
    """Decrypt in 16-byte blocks; a trailing block shorter than 16 bytes is kept as is."""
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


def decrypt_entry(entry: NPKEntry, key: int | None = None) -> bytes:
    """
    Decrypts the data of an NPKEntry object based on its encryption type.
    Parameters:
        entry (NPKEntry): The entry containing the encrypted data and metadata.
        key (int | None, optional): The decryption key required for certain encryption types.
                                     Defaults to None.
    Returns:
        bytes: The decrypted data as a bytes object.
    Raises:
        ValueError: If the decryption key is required but not provided.
    Encryption Types:
        - BASIC_XOR: Performs a basic XOR decryption using a key array derived from the provided key.
                     Only the first 128 bytes (0x80) of the data are decrypted.
        - ADVANCED_XOR: Performs an advanced XOR decryption using a key array derived from the entry's
                        CRC and original file length. Decrypts a specific range of the data based on
                        the entry's metadata.
        - INCREMENTAL_XOR: Performs an incremental XOR decryption using a key derived from the entry's
                           CRC and original file length. Decrypts a specific range of the data with
                           an incrementing key.
    Notes:
        - The decryption process modifies the data in-place using a bytearray.
        - The decryption range and key generation depend on the entry's metadata, such as file length,
          original file length, and CRC.
    """

    # Convert data to bytearray for in-place modifications
    data = bytearray(entry.data)

    if entry.encrypt_flag == DecryptionType.BASIC_XOR:  # Basic XOR
        if key is None:
            get_logger().error(
                "Decryption key is not set for file using 'BASIC XOR' decryption,"
                + "did you use the correct config?"
            )
            entry.data_flags |= NPKEntryDataFlags.ENCRYPTED
            return entry.data

        size = min(entry.file_length, 0x80)

        # Generate key array
        key_array = [(key + x) & 0xFF for x in range(0, 0x100)]

        # Apply XOR decryption
        for j in range(size):
            data[j] ^= key_array[j % 0xFF]

    elif entry.encrypt_flag == DecryptionType.ADVANCED_XOR:  # Advanced XOR variants
        b = entry.crc ^ entry.file_original_length

        start = 0
        size = entry.file_length

        if size > 0x80:
            start = (entry.crc >> 1) % (size - 0x80)
            size = 2 * entry.file_original_length % 0x60 + 0x20

        # Generate key array
        key_array = [(x + b) & 0xFF for x in range(0, 0x81)]

        # Apply XOR decryption
        for j in range(size):
            data[start + j] ^= key_array[j % 0x80]

    elif entry.encrypt_flag == DecryptionType.INCREMENTAL_XOR:  # Incremental XOR
        v3 = int(entry.file_original_length)
        v4 = int(entry.crc)

        crc_key = (v3 ^ v4) & 0xFF
        offset = 0
        length = 0

        if entry.file_length <= 0x80:
            length = entry.file_length
        else:
            offset = (v3 >> 1) % (entry.file_length - 0x80)
            length = ((v4 << 1) & 0xFFFFFFFF) % 0x60 + 0x20

        # Apply incremental XOR decryption
        for xx in range(offset, offset + length):
            data[xx] ^= crc_key
            crc_key = (crc_key + 1) & 0xFF

    # Convert back to bytes
    return bytes(data)
