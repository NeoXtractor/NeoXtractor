"""Provides decompression functions."""

import struct
import zlib
from typing import cast

import lz4.block
import zstandard
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from core.logger import get_logger
from core.npk.class_types import NPKEntry
from core.npk.enums import CompressionType
from core.rotor import Rotor

# The ROTOR marker is a two-byte hint, so one false positive on a whole pack
# can fail thousands of entries. Warn on the first few, then drop to debug.
_ROTOR_FAILURE_WARNING_LIMIT = 3
_rotor_failure_count = 0


def init_rotor() -> Rotor:
    """Initializes the rotor instance."""
    asdf_dn = "j2h56ogodh3se"
    asdf_dt = "=dziaq."
    asdf_df = '|os=5v7!"-234'
    asdf_tm = (
        asdf_dn * 4
        + (asdf_dt + asdf_dn + asdf_df) * 5
        + "!"
        + "#"
        + asdf_dt * 7
        + asdf_df * 2
        + "*"
        + "&"
        + "'"
    )
    rot = Rotor(asdf_tm)
    return rot


def _reverse_string(s: bytes) -> bytes:
    spin = list(s)
    spin = list(map(lambda x: x ^ 154, spin[0:128])) + spin[128:]
    spin.reverse()
    return bytes(spin)


def decompress_entry(entry: NPKEntry):
    """
    This function determines the compression type specified in the `zip_flag` attribute
    of the provided `entry` and applies the corresponding decompression algorithm.
    The decompressed data is returned as the output.

    Returns:
        bytes: The decompressed data.

        The decompressed data is returned as a new object and does not modify the original
        `data` attribute of the `entry` object.
    """

    if entry.zip_flag == CompressionType.ZLIB:
        return zlib.decompress(entry.data, bufsize=entry.file_original_length)

    if entry.zip_flag == CompressionType.LZ4:
        return lz4.block.decompress(
            entry.data, uncompressed_size=entry.file_original_length
        )

    if entry.zip_flag == CompressionType.ZSTANDARD:
        return zstandard.ZstdDecompressor().decompress(entry.data)

    # No matched compression.
    return entry.data


def strip_none_wrapper(data: bytes) -> bytes:
    """Strip a simple NONE wrapper header if present."""
    if data[:4] == b"NONE":
        return data[4:]
    return data


def check_lz4_like(data: bytes) -> bool:
    """Check for the custom LZ4-like stream seen in some payloads."""
    return len(data) >= 4 and data[:4] == b"\x27\xe3\x00\x01"


def unpack_lz4_like(data: bytes) -> bytes:
    """Decode the custom LZ4-like stream used by some payloads."""
    import struct

    if not data:
        return b""

    in_ptr = 0
    out_buf = bytearray()
    data_len = len(data)

    while in_ptr < data_len:
        token = data[in_ptr]
        in_ptr += 1

        literal_len = token >> 4
        match_len = token & 0x0F

        if literal_len == 15:
            while True:
                if in_ptr >= data_len:
                    break
                byte_val = data[in_ptr]
                in_ptr += 1
                literal_len += byte_val
                if byte_val != 0xFF:
                    break

        if in_ptr + literal_len > data_len:
            break

        out_buf.extend(data[in_ptr : in_ptr + literal_len])
        in_ptr += literal_len

        if in_ptr >= data_len:
            break

        if in_ptr + 2 > data_len:
            break

        offset = struct.unpack("<H", data[in_ptr : in_ptr + 2])[0]
        in_ptr += 2

        if match_len == 15:
            while True:
                if in_ptr >= data_len:
                    break
                byte_val = data[in_ptr]
                in_ptr += 1
                match_len += byte_val
                if byte_val != 0xFF:
                    break

        match_len += 4

        start_pos = len(out_buf) - offset
        if start_pos < 0:
            break

        for i in range(match_len):
            if start_pos + i >= len(out_buf):
                break
            out_buf.append(out_buf[start_pos + i])

    return bytes(out_buf)


def check_nxs3(entry: NPKEntry) -> bool:
    """Check if the data is wrapped in any NXS format (old NXS3 or new NXS)."""
    return (
        entry.data[:8] == b"NXS3\x03\x00\x00\x01"
        or entry.data[:8] == b"\x4e\x58\x5a\x00\x47\x38\x36\x00"
    )


def check_rotor(entry: NPKEntry) -> bool:
    """Check whether the payload starts with a ROTOR marker.

    This is only a 2-byte hint and can collide with an ordinary payload, so
    callers must treat an ``unpack_rotor`` failure as "not ROTOR" and keep the
    original bytes instead of failing the entry.
    """
    return entry.data[:2] == bytes([0x1D, 0x04]) or entry.data[:2] == bytes(
        [0x15, 0x23]
    )


def log_rotor_failure(context: str, exc: Exception) -> str:
    """Report a failed ``unpack_rotor`` attempt without flooding the log.

    Callers keep the original bytes (a false positive is more likely than a
    broken pack), so this is informational: the first failures are warnings
    and the remainder are debug. Returns the message that was emitted, for
    call sites that need it elsewhere.
    """
    global _rotor_failure_count

    _rotor_failure_count += 1
    message = f"ROTOR unpack failed for {context}: {exc}"
    if _rotor_failure_count <= _ROTOR_FAILURE_WARNING_LIMIT:
        get_logger().warning("%s", message)
    else:
        get_logger().debug("%s (failure #%d)", message, _rotor_failure_count)
    return message


def check_stzb(entry: NPKEntry) -> bool:
    return entry.data[:4] == b"STZB"


def unpack_rotor(data: bytes) -> bytes:
    """Unpacks the ROTOR decryption with the RSA public key and zlib decompression"""
    return _reverse_string(zlib.decompress(init_rotor().decrypt(data)))


def unpack_stzb(data: bytes) -> bytes:
    """Unpacks STZB encrypted data - completely aligned with the second script"""
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


def rsa_public_decrypt(signature: bytes, key: rsa.RSAPublicKey) -> bytes:
    """Converts a signature to an integer and decrypts it using the RSA public key."""
    public_numbers = key.public_numbers()
    e = public_numbers.e
    n = public_numbers.n

    k = (n.bit_length() + 7) // 8  # key length in bytes
    if len(signature) != k:
        raise ValueError("Signature length does not match key size")

    # Convert signature to integer
    sig_int = int.from_bytes(signature, byteorder="big")

    # RSA public operation: m = sig^e mod n
    m_int = pow(sig_int, e, n)

    # Convert back to bytes
    decrypted = m_int.to_bytes(k, byteorder="big")

    # Remove PKCS#1 v1.5 padding
    if decrypted[0] != 0x00 or decrypted[1] != 0x01:
        raise ValueError("Incorrect padding")

    # Padding is 0xFF ... 0x00, then the message
    try:
        padding_end = decrypted.index(0x00, 2)
    except ValueError as e:
        raise ValueError("Padding end not found") from e

    return decrypted[padding_end + 1 :]


def _unpack_nxs3_old(data: bytes) -> bytes:
    """
    Decrypts and unpacks data encrypted with original NXS3 format (1024-bit RSA + LZ4).
    """
    # Parse the RSA public key
    pem_key = b"""-----BEGIN RSA PUBLIC KEY-----
MIGJAoGBAOZAaZe2qB7dpT9Y8WfZIdDv+ooS1HsFEDW2hFnnvcuFJ4vIuPgKhISm
pY4/jT3aipwPNVTjM6yHbzOLhrnGJh7Ec3CQG/FZu6VKoCqVEtCeh15hjcu6QYtn
YWIEf8qgkylqsOQ3IIn76udV6m0AWC2jDlmLeRcR04w9NNw7+9t9AgMBAAE=
-----END RSA PUBLIC KEY-----"""

    rsa_key = cast(
        rsa.RSAPublicKey,
        serialization.load_pem_public_key(pem_key, backend=default_backend()),
    )

    decrypted = _nxs3_logic(data, rsa_key, 128)

    return decrypted


def _unpack_nxs_new(data: bytes) -> bytes:
    """
    Add NXS decryption support for endless Lagrange
    """

    # 4096-bit RSA public key
    pem_key = b"""-----BEGIN RSA PUBLIC KEY-----
MIICCgKCAgEAu5/HBdUwY37hJbm3ri9h/fHJqsx6PeLTEqP2tIYoV3+qn0lI4Kht
wi03S2wf6CrwWXuf8Dp4L/MRsFi/Cxqe53m6Dhx8Zy9nzStaBUzp0DeL/M+HWI+r
fDUPybKfJx9qlTNxUyvIQZkSh83YdkhVC4pqiOt0nGCS44Xs88DEkYOjRydLa4uK
JQIZAuUSsC5Cu9FjBzGHW3Pc9ene9HJai+8ipvi8bhLc1hnvlER7GtzQce/Ubjq2
D79KXLCjZKYr0L+9h7hfOQk+R2VqVthRvuf2ql9H13Wbnukm6ijg8+mamB6esNTo
OPdjQkuMj5wUEfPqRK3GZibW92QilOvFt9cx0JBjjs3k8ax7u9iOnsVEqUqgX9bE
FoZiwUfV1wJAcfEzJqJ4/wMe8FIV35Pg9UE/tQ4M9YX+PDUTnaWXksK8kDqa96NG
d9xqy+MntsUcKf7UsEExtkm6GDxtpIokUYplUAMPQDo/04eBOP6J5YdjOv2Dxjd5
OM832KIu1uYdO81xRGmyiSsavtzkQJbePWVFq1iW/1+nmaodzgi/esbLFM5T6xan
iOvQK1rRaJgE2NdU0EOAOhDAJu+1JfiB60nJw20gSM6Wl3s9N+UmXrR+xJxxcgnK
P0VB60qOgnlYmNwld5muJazI9P7sbtFRuEVLoN5Y+P9PCIXQ/RrZVLMCAwEAAQ==
-----END RSA PUBLIC KEY-----"""

    rsa_key = cast(
        rsa.RSAPublicKey,
        serialization.load_pem_public_key(pem_key, backend=default_backend()),
    )

    decrypted = _nxs3_logic(data, rsa_key, 512)

    # Check if Zstd compressed and decompress
    if decrypted[:4] == b"\x28\xb5\x2f\xfd":
        dctx = zstandard.ZstdDecompressor()
        return dctx.decompress(decrypted)

    return decrypted


def _nxs3_logic(data: bytes, key: rsa.RSAPublicKey, size: int) -> bytes:
    wrapped_key = rsa_public_decrypt(data[20 : 20 + size], key)[:4]

    if wrapped_key is None or len(wrapped_key) != 4:
        raise ValueError("Decryption of the encrypted key failed.")

    # Convert to int (little endian)
    ephemeral_key = int.from_bytes(wrapped_key, "little")

    # Stream cipher decryption
    decrypted = bytearray()
    payload = data[20 + size :]

    for i, x in enumerate(payload):
        val = x ^ ((ephemeral_key >> (i % 4 * 8)) & 0xFF)
        decrypted.append(val)
        if i % 4 == 3:
            ror = (ephemeral_key >> 19) | ((ephemeral_key << 13) & 0xFFFFFFFF)
            ephemeral_key = (ror + ((ror << 2) & 0xFFFFFFFF) + 0xE6546B64) & 0xFFFFFFFF

    return bytes(decrypted)


def unpack_nxs3(data: bytes) -> bytes:
    """Unpack any NXS format (old NXS3 or new NXS)."""
    if data[:8] == b"NXS3\x03\x00\x00\x01":
        return _unpack_nxs3_old(data)
    elif data[:8] == b"\x4e\x58\x5a\x00\x47\x38\x36\x00":
        return _unpack_nxs_new(data)
    return data
