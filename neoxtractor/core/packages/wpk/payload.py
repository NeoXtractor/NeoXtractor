"""WPK payload wrapper unwrapping.

The nested-wrapper loop a WPK entry runs after stage-1 decoding, as a pure
function over plain ``bytes`` with no archive state.  Shared NONE/LZ4-like/
ROTOR/NXS3 primitives come from ``neoxtractor.core.utils.wrappers``; WPK-only
wrappers (ENON, DTSZ, COBL) live here.  Stage-1 slot-file auto-selection
belongs to the IDX pkg=255 read path, not to WPK.
"""

from __future__ import annotations

import struct
import zlib

from neoxtractor.core.packages.errors import PayloadDecodeError
from neoxtractor.core.utils.logger import get_logger
from neoxtractor.core.utils.wrappers import (
    check_lz4_like,
    check_nxs3,
    check_rotor,
    strip_none_wrapper,
    unpack_lz4_like,
    unpack_nxs3,
    unpack_rotor,
)

# COBL block codec tags (LE uint32; comments show the stored byte order).
_COBL_TAG_NONE = 0x4E4F4E45      # "ENON"
_COBL_TAG_ZLIB = 0x5A4C4942      # "BILZ" (zlib)
_COBL_TAG_ZSTD = 0x5A535444      # "DTSZ" (zstandard)
_COBL_TAG_LZ4F = 0x4C5A3446      # "F4ZL" (lz4 frame)
_COBL_TAG_OODLE = 0x4F4F444C     # "LDOO" (Oodle, unsupported here)


def maybe_strip_enon_header(data: bytes, *, context: str) -> tuple[bytes, bool]:
    if len(data) < 4 or data[:4] != b"ENON":
        return data, False

    stripped = data[4:]
    get_logger().debug(
        "ENON header stripped: %s in=%d out=%d", context, len(data), len(stripped)
    )
    return stripped, True


def maybe_unpack_dtsz(data: bytes, *, context: str) -> tuple[bytes, bool]:
    if len(data) < 8 or data[:4] != b"DTSZ" or data[4:8] != b"\x28\xb5\x2f\xfd":
        return data, False

    # Magic present means this is a zstd stream: decode failures raise
    # instead of returning the bytes unchanged.
    try:
        import zstandard
    except Exception as exc:
        raise PayloadDecodeError(
            "wpk", context, "missing-dependency", f"DTSZ requires zstandard: {exc}"
        ) from exc

    try:
        unpacked = zstandard.ZstdDecompressor().decompress(data[4:])
    except Exception as exc:
        raise PayloadDecodeError(
            "wpk", context, "corrupt", f"DTSZ decompress failed: {exc}"
        ) from exc
    get_logger().debug(
        "DTSZ unpacked: %s in=%d out=%d", context, len(data), len(unpacked)
    )
    return unpacked, True


def deobfuscate_cobl_probe_region(data: bytes) -> tuple[bytes, int]:
    """Undo the 0x5A obfuscation over the leading probe region.

    The first ``min(64, len(data))`` bytes are stored reversed and XORed with
    0x5A; returns (fixed_bytes, probe_len), or ``(data, 0)`` when the region
    is too short to carry the transform.
    """
    if not data:
        return data, 0

    probe_len = min(64, len(data))
    if probe_len <= 3:
        return data, 0

    chunk = data[:probe_len]
    fixed = bytes((b ^ 0x5A) for b in chunk[::-1])

    patched = bytearray(data)
    patched[:probe_len] = fixed
    return bytes(patched), probe_len


def decode_cobl_block(data: bytes, *, context: str) -> bytes:
    if not data:
        return b""

    patched, probe_len = deobfuscate_cobl_probe_region(data)
    if probe_len < 4 or len(patched) < 4:
        raise PayloadDecodeError(
            "wpk",
            context,
            "corrupt",
            f"COBL block too short to carry a codec tag: {len(data)} bytes",
        )

    tag = struct.unpack_from("<I", patched, 0)[0]
    payload = patched[4:]

    if tag == _COBL_TAG_NONE:
        return payload

    if tag == _COBL_TAG_ZLIB:
        try:
            return zlib.decompress(payload)
        except Exception as exc:
            raise PayloadDecodeError(
                "wpk", context, "corrupt", f"COBL zlib decompress failed: {exc}"
            ) from exc

    if tag == _COBL_TAG_ZSTD:
        try:
            import zstandard
        except Exception as exc:
            raise PayloadDecodeError(
                "wpk",
                context,
                "missing-dependency",
                f"COBL block requires zstandard: {exc}",
            ) from exc
        try:
            return zstandard.ZstdDecompressor().decompress(payload)
        except Exception as exc:
            raise PayloadDecodeError(
                "wpk", context, "corrupt", f"COBL zstd decompress failed: {exc}"
            ) from exc

    if tag == _COBL_TAG_LZ4F:
        try:
            import lz4.frame as lz4f
        except Exception as exc:
            raise PayloadDecodeError(
                "wpk",
                context,
                "missing-dependency",
                f"COBL block requires lz4.frame: {exc}",
            ) from exc
        try:
            return lz4f.decompress(payload)
        except Exception as exc:
            raise PayloadDecodeError(
                "wpk", context, "corrupt", f"COBL lz4 decompress failed: {exc}"
            ) from exc

    if tag == _COBL_TAG_OODLE:
        raise PayloadDecodeError(
            "wpk",
            context,
            "unknown-algorithm",
            "COBL block uses an unsupported Oodle codec",
        )

    raise PayloadDecodeError(
        "wpk",
        context,
        "unknown-algorithm",
        f"COBL block uses unknown codec tag 0x{tag:08X}",
    )


def decode_cobl_concat(data: bytes, *, context: str) -> bytes:
    magic, _field04, _field08, block_count = struct.unpack_from("<4I", data, 0)
    # The concat header only accepts the LE orientation: 0x434F424C stores as
    # the bytes "LBOC" (maybe_unpack_cobl gates on either byte-order spelling).
    if magic != 0x434F424C:
        raise PayloadDecodeError(
            "wpk", context, "corrupt", f"bad COBL magic 0x{magic:08X}"
        )

    data_base = 16 + block_count * 8
    if len(data) < data_base:
        raise PayloadDecodeError(
            "wpk",
            context,
            "corrupt",
            f"COBL truncated block table: need >= {data_base}, got {len(data)}",
        )

    rel_offset = 0
    out = bytearray()

    for block_index in range(block_count):
        size, extra, _unk = struct.unpack_from("<IHH", data, 16 + block_index * 8)
        start = data_base + rel_offset
        end = start + size
        if end > len(data):
            raise PayloadDecodeError(
                "wpk",
                context,
                "corrupt",
                f"COBL block {block_index} out of range: start={start} "
                f"end={end} size={len(data)}",
            )

        payload = data[start:end]
        decoded = decode_cobl_block(
            payload, context=f"{context} block={block_index}"
        )
        out.extend(decoded)
        rel_offset += size + extra

    return bytes(out)


def maybe_unpack_cobl(data: bytes, *, context: str) -> tuple[bytes, bool]:
    if len(data) < 16 or data[:4] not in (b"LBOC", b"COBL"):
        return data, False

    # Magic present means this is a COBL container: a broken block table, a
    # missing codec, or an unsupported inner tag raises instead of returning
    # the container bytes unchanged.
    unpacked = decode_cobl_concat(data, context=context)
    get_logger().debug(
        "COBL unpacked: %s in=%d out=%d", context, len(data), len(unpacked)
    )
    return unpacked, True


def unwrap_nested_payloads(data: bytes, *, context: str) -> bytes:
    """Strip nested wrappers until the payload stops changing.

    Layer order: NONE, ENON, DTSZ, COBL, LZ4-like, ROTOR, NXS3.  Stop guards:
    a repeated (length, head) signature, and a hard layer cap.  A layer whose
    magic matches but whose decode fails raises instead of returning the
    bytes undecoded; a layer that does not match is skipped, and the loop
    ends when nothing more applies.
    """
    seen_signatures: set[tuple[int, bytes]] = set()
    max_layers = 32
    for _ in range(max_layers):
        signature = (len(data), data[:16])
        if signature in seen_signatures:
            get_logger().debug(
                "Nested payload unwrap stopped on repeated signature: %s "
                "len=%d head=%s",
                context,
                len(data),
                data[:8].hex().upper(),
            )
            break
        seen_signatures.add(signature)

        stripped = strip_none_wrapper(data)
        if stripped != data:
            get_logger().debug(
                "NONE header stripped: %s in=%d out=%d",
                context,
                len(data),
                len(stripped),
            )
            data = stripped
            continue

        stripped, did_strip = maybe_strip_enon_header(data, context=context)
        if did_strip:
            data = stripped
            continue

        unpacked, did_unpack = maybe_unpack_dtsz(data, context=context)
        if did_unpack:
            data = unpacked
            continue

        unpacked, did_unpack = maybe_unpack_cobl(data, context=context)
        if did_unpack:
            data = unpacked
            continue

        if check_lz4_like(data):
            before_len = len(data)
            try:
                unpacked = unpack_lz4_like(data)
            except Exception as exc:
                raise PayloadDecodeError(
                    "wpk",
                    context,
                    "corrupt",
                    f"LZ4-like wrapper detected but unpack failed: {exc}",
                ) from exc
            if unpacked and unpacked != data:
                get_logger().debug(
                    "LZ4-like unpacked: %s in=%d out=%d",
                    context,
                    before_len,
                    len(unpacked),
                )
                data = unpacked
                continue

        if check_rotor(data):
            before_len = len(data)
            data = unpack_rotor(data)
            get_logger().debug(
                "ROTOR unpacked: %s in=%d out=%d", context, before_len, len(data)
            )
            continue

        if check_nxs3(data):
            before_len = len(data)
            data = unpack_nxs3(data)
            get_logger().debug(
                "NXS3 unpacked: %s in=%d out=%d", context, before_len, len(data)
            )
            continue

        break
    else:
        get_logger().warning(
            "Nested payload unwrap hit layer limit: %s final_len=%d head=%s",
            context,
            len(data),
            data[:8].hex().upper(),
        )
    return data
