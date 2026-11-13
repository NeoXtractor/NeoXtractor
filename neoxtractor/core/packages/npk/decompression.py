"""NPK payload decompression: the zip-flag dispatch over stored entries.

Holds the npk-private pieces: the ``CompressionType``-keyed decompression
schedule and the STZB variant, which only the npk index names.
"""

import struct
import zlib

import lz4.block
import zstandard

from neoxtractor.core.packages.npk.enums import CompressionType


def decompress_entry(
    data: bytes,
    zip_flag: CompressionType,
    file_original_length: int,
) -> bytes:
    """Decompress one payload according to its `zip_flag`.

    The decompressed data is returned as a new object; the input bytes are
    never modified.  An unmatched compression type returns the input as-is.
    """

    if zip_flag == CompressionType.ZLIB:
        return zlib.decompress(data, bufsize=file_original_length)

    if zip_flag == CompressionType.LZ4:
        return lz4.block.decompress(
            data, uncompressed_size=file_original_length
        )

    if zip_flag == CompressionType.ZSTANDARD:
        return zstandard.ZstdDecompressor().decompress(data)

    # No matched compression.
    return data


def check_stzb(data: bytes) -> bool:
    return data[:4] == b"STZB"


def unpack_stzb(data: bytes) -> bytes:
    """Unpack STZB-encrypted data: XOR-decrypt the payload region.

    Data without the ``STZB`` magic is returned unchanged.
    """
    if data[:4] != b"STZB":
        return data

    # magic = data[0:4]
    # unknown = data[4:8]
    # compressed_len = struct.unpack('<I', data[8:12])[0]
    encrypted_len = struct.unpack("<I", data[12:16])[0]

    xor_key = b"\x8e\x50\x9f\xe8\x59\x67\x91\xfb"
    decrypted_data = bytearray()

    encrypted_data = data[16 : 16 + encrypted_len]
    for i, byte in enumerate(encrypted_data):
        key_byte = xor_key[i % len(xor_key)]
        decrypted_data.append(byte ^ key_byte)

    return bytes(decrypted_data)
