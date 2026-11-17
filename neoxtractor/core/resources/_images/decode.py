"""Private image decoding for the ``image`` resource protocol (Qt-free).

``pillow_open`` accepts a file-like object exposing ``read``/``seek``/``tell``
(``seek`` supports ``io.SEEK_END``) or ``bytes``; the ``*_convert`` codecs take
the full payload as ``bytes``.  Standard formats (PNG/JPEG/GIF/BMP/TIFF/DDS/
PSD/ICO/WEBP) are opened lazily through Pillow, which keeps the handle and
decodes on demand; compressed container formats only read the bytes their
header layout requires.

Callers should go through ``neoxtractor.core.resources.images`` (the protocol
entry), not this module directly.
"""

from __future__ import annotations

import io
import math
from typing import IO, cast

import texture2ddecoder
from bitstring import ConstBitStream
from PIL import Image, UnidentifiedImageError

from neoxtractor.core.utils.binary_readers import read_uintle32, read_uintle64
from neoxtractor.core.utils.filelike import FileLike, as_stream, read_all

# Standard formats Pillow can open directly from a stream.
PILLOW_FORMATS = {
    "png": "PNG",
    "jpg": "JPEG",
    "jpeg": "JPEG",
    "gif": "GIF",
    "bmp": "BMP",
    "tiff": "TIFF",
    "tif": "TIFF",
    "ico": "ICO",
    "webp": "WEBP",
    "dds": "DDS",
    "psd": "PSD",
    "tga": "TGA",
}


def _dds_dxgi_fallback(data: bytes):
    """Fallback DDS decoder for DX10 BGRA/BGRX Pillow cannot open."""
    if len(data) < 148 or data[:4] != b"DDS ":
        raise ValueError("Not a DDS file")
    if int.from_bytes(data[4:8], "little") != 124:
        raise ValueError("Unsupported DDS header size")
    height = int.from_bytes(data[12:16], "little")
    width = int.from_bytes(data[16:20], "little")
    if data[84:88] != b"DX10":
        raise NotImplementedError("DDS fallback only handles DX10 DDS files")
    dxgi_format = int.from_bytes(data[128:132], "little")
    pixel_data = data[148:]
    if dxgi_format in (87, 91):  # B8G8R8A8_UNORM / _SRGB
        expected = width * height * 4
        if len(pixel_data) < expected:
            raise ValueError("DDS pixel data truncated for DX10 BGRA")
        return Image.frombytes(
            "RGBA", (width, height), pixel_data[:expected], "raw", "BGRA"
        )
    if dxgi_format in (88, 93):  # B8G8R8X8_UNORM / _SRGB
        expected = width * height * 4
        if len(pixel_data) < expected:
            raise ValueError("DDS pixel data truncated for DX10 BGRX")
        image = Image.frombytes(
            "RGBX", (width, height), pixel_data[:expected], "raw", "BGRX"
        )
        return image.convert("RGBA")
    raise NotImplementedError(f"Unsupported DDS DXGI fallback format {dxgi_format}")


def pillow_open(f: FileLike, fmt: str) -> Image.Image:
    """Open a Pillow-supported image lazily from a stream (no full read)."""
    stream = as_stream(f)
    stream.seek(0)
    try:
        # Cast is safe: Image.open only calls read/seek/tell at runtime.
        return Image.open(
            cast("IO[bytes]", stream), "r", (PILLOW_FORMATS[fmt].upper(), "PNG")
        )
    except (NotImplementedError, UnidentifiedImageError):
        # Pillow reports a DDS it cannot parse (bad header, volume texture,
        # unsupported DXGI format) as an unidentified image, because
        # Image.open swallows the SyntaxError raised by its own DDS plugin.
        if fmt == "dds":
            return _dds_dxgi_fallback(read_all(stream))
        raise


def _get_astc_file_size(width, height, block_x, block_y):
    """Total byte size of an ASTC payload: one 16-byte block per block cell."""
    return math.ceil(width / block_x) * math.ceil(height / block_y) * 16


def _validate_astc_payload(data, width, height, block_x, block_y):
    """Trim ``data`` to the exact ASTC size, raising if it is truncated."""
    expected = _get_astc_file_size(width, height, block_x, block_y)
    if len(data) < expected:
        raise ValueError(
            f"ASTC payload truncated for {block_x}x{block_y}: "
            f"expected {expected}, got {len(data)}"
        )
    return data[:expected]


def _decode_correct_format(fmt, data, width, height, block_x=4, block_y=4):
    """Decode raw texel data for ``fmt`` into an RGBA image.

    ``texture2ddecoder`` yields BGRA texels, so every compressed path
    interprets the buffer as ``"BGRA"``; only ``"RGBA8"`` is already RGBA.
    """
    match fmt:
        case "ASTC":
            data = _validate_astc_payload(data, width, height, block_x, block_y)
            data = texture2ddecoder.decode_astc(data, width, height, block_x, block_y)
        case "BC1":
            data = texture2ddecoder.decode_bc1(data, width, height)
        case "BC3":
            data = texture2ddecoder.decode_bc3(data, width, height)
        case "BC4":
            data = texture2ddecoder.decode_bc4(data, width, height)
        case "ETC1":
            data = texture2ddecoder.decode_etc1(data, width, height)
        case "ETC2":
            data = texture2ddecoder.decode_etc2(data, width, height)
        case "ETC2A1":
            data = texture2ddecoder.decode_etc2a1(data, width, height)
        case "ETC2A8":
            data = texture2ddecoder.decode_etc2a8(data, width, height)
        case "PVRTC":
            data = texture2ddecoder.decode_pvrtc(data, width, height, False)
        case "RGBA8":
            return Image.frombytes("RGBA", (width, height), data, "raw", "RGBA")
    return Image.frombytes("RGBA", (width, height), data, "raw", "BGRA")


# Derived from TeaEffTeu's works and guidance.
def compblks_convert(data: bytes) -> Image.Image:
    """Convert a CompBlks (CBK) container to an Image."""
    fmt = data[8:10]
    width = int.from_bytes(data[16:18], "little")
    height = int.from_bytes(data[18:20], "little")
    image_data = data[28:]
    if fmt == bytes([0xF3, 0x83]):
        return _decode_correct_format("BC3", image_data, width, height)
    if fmt == bytes([0x78, 0x92]):
        return _decode_correct_format("ETC2A8", image_data, width, height)
    if fmt == bytes([0x74, 0x92]):
        return _decode_correct_format("ETC2", image_data, width, height)
    raise ValueError(f"Unsupported CBK payload format {fmt.hex()}")


def pvr_convert(data: bytes) -> Image.Image:
    """Convert a PVR texture to an Image."""
    bitf = ConstBitStream(data)
    bitf.read("pad64")
    pixel_format = read_uintle64(bitf)
    bitf.read("pad64")
    height, width, _depth = bitf.readlist("3*uintle32")
    bitf.read("pad96")
    meta_data_size = read_uintle32(bitf)
    bitf.pos += meta_data_size * 8
    image_data = bitf.read("bytes")
    astc = {
        27: (4, 4), 28: (5, 4), 29: (5, 5), 30: (6, 5), 31: (6, 6),
        32: (8, 5), 33: (8, 6), 34: (8, 8), 35: (10, 5), 36: (10, 6),
        37: (10, 8), 38: (10, 10), 39: (12, 10), 40: (12, 12),
    }
    simple = {3: "PVRTC", 7: "BC1", 11: "BC3", 12: "BC4"}
    if pixel_format in simple:
        return _decode_correct_format(simple[pixel_format], image_data, width, height)
    if pixel_format in astc:
        bx, by = astc[pixel_format]
        return _decode_correct_format("ASTC", image_data, width, height, bx, by)
    raise ValueError(f"Unsupported PVR pixel format: {pixel_format}")


def ktx_convert(data: bytes) -> Image.Image:
    """Convert a KTX (v1) texture to an Image."""
    bitf = ConstBitStream(data)
    bitf.read("pad224")
    gl_internal_format = read_uintle32(bitf)
    bitf.read("pad32")
    width, height = bitf.readlist("2*uintle32")
    bitf.read("pad128")
    bytes_of_key_value_data = read_uintle32(bitf)
    bitf.read(f"pad{bytes_of_key_value_data * 8}")
    image_size = read_uintle32(bitf)
    image_data = bitf.read(f"bytes{image_size}")
    astc = {
        0x93B0: (4, 4), 0x93D0: (4, 4), 0x93B1: (5, 4), 0x93D1: (5, 4),
        0x93B2: (5, 5), 0x93D2: (5, 5), 0x93B3: (6, 5), 0x93D3: (6, 5),
        0x93B4: (6, 6), 0x93D4: (6, 6), 0x93B5: (8, 5), 0x93D5: (8, 5),
        0x93B6: (8, 6), 0x93D6: (8, 6), 0x93B7: (8, 8), 0x93D7: (8, 8),
        0x93B8: (10, 5), 0x93D8: (10, 5), 0x93B9: (10, 6), 0x93D9: (10, 6),
        0x93BA: (10, 8), 0x93DA: (10, 8), 0x93BB: (10, 10), 0x93DB: (10, 10),
        0x93BC: (12, 10), 0x93DC: (12, 10), 0x93BD: (12, 12), 0x93DD: (12, 12),
    }
    simple = {
        0x8058: "RGBA8", 0x8D64: "ETC1", 0x9274: "ETC2",
        0x9276: "ETC2A1", 0x9278: "ETC2A8",
    }
    if gl_internal_format in simple:
        return _decode_correct_format(simple[gl_internal_format], image_data, width, height)
    if gl_internal_format in astc:
        bx, by = astc[gl_internal_format]
        return _decode_correct_format("ASTC", image_data, width, height, bx, by)
    raise ValueError(f"Unknown KTX format: {gl_internal_format:#x}")


def astc_convert(data: bytes) -> Image.Image:
    """Convert a standalone ASTC file to an Image."""
    bitf = ConstBitStream(data)
    block_x, block_y = bitf.readlist("pad32, 2*uintle8, pad8")
    width, height = bitf.readlist("2*uintle24, pad24")
    return _decode_correct_format(
        "ASTC", bitf.read("bytes"), width, height, block_x, block_y
    )


def image_to_png_data(img: Image.Image) -> bytes:
    """Encode a Pillow image to PNG bytes."""
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
