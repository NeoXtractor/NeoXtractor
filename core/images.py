"""Image conversion functions for various formats."""

import io
import math
from typing import cast

import texture2ddecoder
from bitstring import ConstBitStream
from PIL import Image, ImageFile, UnidentifiedImageError
from PySide6.QtGui import QImage

from core.binary_readers import read_uintle32, read_uintle64


def _dds_dxgi_fallback(data: bytes):
    """Fallback DDS decoder for simple DX10 BGRA/BGRX formats unsupported by Pillow."""
    if len(data) < 148 or data[:4] != b"DDS ":
        raise ValueError("Not a DDS file")

    header_size = int.from_bytes(data[4:8], "little")
    if header_size != 124:
        raise ValueError(f"Unsupported DDS header size: {header_size}")

    height = int.from_bytes(data[12:16], "little")
    width = int.from_bytes(data[16:20], "little")
    fourcc = data[84:88]

    if fourcc != b"DX10":
        raise NotImplementedError("DDS fallback only handles DX10 DDS files")

    dxgi_format = int.from_bytes(data[128:132], "little")
    pixel_data = data[148:]

    if dxgi_format in (87, 91):  # B8G8R8A8_UNORM / _SRGB
        expected = width * height * 4
        if len(pixel_data) < expected:
            raise ValueError(
                f"DDS pixel data truncated for DXGI {dxgi_format}: expected {expected}, got {len(pixel_data)}"
            )
        return Image.frombytes(
            "RGBA", (width, height), pixel_data[:expected], "raw", "BGRA"
        )

    if dxgi_format in (88, 93):  # B8G8R8X8_UNORM / _SRGB
        expected = width * height * 4
        if len(pixel_data) < expected:
            raise ValueError(
                f"DDS pixel data truncated for DXGI {dxgi_format}: expected {expected}, got {len(pixel_data)}"
            )
        image = Image.frombytes(
            "RGBX", (width, height), pixel_data[:expected], "raw", "BGRX"
        )
        return image.convert("RGBA")

    raise NotImplementedError(f"Unsupported DDS DXGI fallback format {dxgi_format}")


# tga, ico, tiff, dds, psd
def _pillow_image_conversion(data, fmt):
    try:
        return Image.open(io.BytesIO(data), "r", (fmt.upper(), "PNG"))
    except (NotImplementedError, UnidentifiedImageError):
        # Pillow reports a DDS it cannot parse (bad header, volume texture,
        # unsupported DXGI format) as an unidentified image, because Image.open
        # swallows the SyntaxError raised by its own DDS plugin.
        if fmt.lower() == "dds":
            return _dds_dxgi_fallback(data)
        raise


def image_to_png_data(img: Image.Image | ImageFile.ImageFile) -> bytes:
    """Convert an image to PNG data."""
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def image_to_qimage(image: Image.Image) -> QImage:
    return image.toqimage()


def bgra_to_rgba(image: bytes) -> QImage:
    return Image.open(io.BytesIO(image), "r", ("PNG", "DDS")).toqimage()


def _get_pitch(width):
    return max(1, ((width + 3) // 4)) * 16


def _get_astc_file_size(width, height, block_x, block_y):
    return math.ceil(width / block_x) * math.ceil(height / block_y) * 16


def _validate_astc_payload(
    data: bytes, width: int, height: int, block_x: int, block_y: int
):
    expected = _get_astc_file_size(width, height, block_x, block_y)
    if len(data) < expected:
        raise ValueError(
            f"ASTC payload truncated for {block_x}x{block_y}: expected {expected}, got {len(data)}"
        )
    return data[:expected]


def _decode_correct_format(
    fmt, data, width, height, block_x=4, block_y=4
) -> Image.Image:
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
            return Image.frombytes("RGBA", (width, height), data, "raw", ("RGBA"))
    return Image.frombytes("RGBA", (width, height), data, "raw", ("BGRA"))


# this code was derived from TeaEffTeu's works and guidance, thank you very much for sharing!!
def compblks_convert(data: bytes) -> Image.Image:
    """Convert CompBlks to Image."""

    fmt = data[8:10]
    width = int.from_bytes(data[16:18], "little")
    height = int.from_bytes(data[18:20], "little")

    image_data = data[28:]
    if fmt == bytes([0xF3, 0x83]):
        return _decode_correct_format("BC3", image_data, width, height)
    elif fmt == bytes([0x78, 0x92]):
        return _decode_correct_format("ETC2A8", image_data, width, height)
    elif fmt == bytes([0x74, 0x92]):
        return _decode_correct_format("ETC2", image_data, width, height)
    else:
        raise ValueError(f"Unsupported CBK payload format {fmt.hex()}")


def pvr_convert(data: bytes):
    """Convert PVR to Image."""

    f = ConstBitStream(io.BytesIO(data))
    f.read("pad64")
    pixel_format = read_uintle64(f)
    f.read("pad64")
    height, width, depth = f.readlist("3*uintle32")
    f.read("pad96")
    meta_data_size = read_uintle32(f)
    f.pos += meta_data_size * 8

    image_data = f.read("bytes")
    match pixel_format:
        case 3:
            return _decode_correct_format("PVRTC", image_data, width, height)
        case 7:
            return _decode_correct_format("BC1", image_data, width, height)
        case 11:
            return _decode_correct_format("BC3", image_data, width, height)
        case 12:
            return _decode_correct_format("BC4", image_data, width, height)
        case 27:
            return _decode_correct_format("ASTC", image_data, width, height, 4, 4)
        case 28:
            return _decode_correct_format("ASTC", image_data, width, height, 5, 4)
        case 29:
            return _decode_correct_format("ASTC", image_data, width, height, 5, 5)
        case 30:
            return _decode_correct_format("ASTC", image_data, width, height, 6, 5)
        case 31:
            return _decode_correct_format("ASTC", image_data, width, height, 6, 6)
        case 32:
            return _decode_correct_format("ASTC", image_data, width, height, 8, 5)
        case 33:
            return _decode_correct_format("ASTC", image_data, width, height, 8, 6)
        case 34:
            return _decode_correct_format("ASTC", image_data, width, height, 8, 8)
        case 35:
            return _decode_correct_format("ASTC", image_data, width, height, 10, 5)
        case 36:
            return _decode_correct_format("ASTC", image_data, width, height, 10, 6)
        case 37:
            return _decode_correct_format("ASTC", image_data, width, height, 10, 8)
        case 38:
            return _decode_correct_format("ASTC", image_data, width, height, 10, 10)
        case 39:
            return _decode_correct_format("ASTC", image_data, width, height, 12, 10)
        case 40:
            return _decode_correct_format("ASTC", image_data, width, height, 12, 12)
    raise ValueError(f"Unsupported PVR pixel format: {pixel_format}")


# https://registry.khronos.org/KTX/specs/1.0/ktxspec.v1.html
# https://github.com/KhronosGroup/KTX-Software/blob/main/lib/src/gl_format.h
def ktx_convert(data: bytes):
    """Convert KTX to Image."""
    f = ConstBitStream(io.BytesIO(data))
    f.read("pad224")
    glInternalFormat = read_uintle32(f)
    f.read("pad32")
    width, height = f.readlist("2*uintle32")
    f.read("pad128")
    bytesOfKeyValueData = read_uintle32(f)
    f.read(f"pad{bytesOfKeyValueData * 8}")
    image_size = read_uintle32(f)

    image_data = f.read(f"bytes{image_size}")
    match glInternalFormat:
        case 0x8058:
            return _decode_correct_format("RGBA8", image_data, width, height)
        case 0x8D64:
            return _decode_correct_format("ETC1", image_data, width, height)
        case 0x9274:
            return _decode_correct_format("ETC2", image_data, width, height)
        case 0x9276:
            return _decode_correct_format("ETC2A1", image_data, width, height)
        case 0x9278:
            return _decode_correct_format("ETC2A8", image_data, width, height)
        case 0x93B0 | 0x93D0:
            return _decode_correct_format("ASTC", image_data, width, height, 4, 4)
        case 0x93B1 | 0x93D1:
            return _decode_correct_format("ASTC", image_data, width, height, 5, 4)
        case 0x93B2 | 0x93D2:
            return _decode_correct_format("ASTC", image_data, width, height, 5, 5)
        case 0x93B3 | 0x93D3:
            return _decode_correct_format("ASTC", image_data, width, height, 6, 5)
        case 0x93B4 | 0x93D4:
            return _decode_correct_format("ASTC", image_data, width, height, 6, 6)
        case 0x93B5 | 0x93D5:
            return _decode_correct_format("ASTC", image_data, width, height, 8, 5)
        case 0x93B6 | 0x93D6:
            return _decode_correct_format("ASTC", image_data, width, height, 8, 6)
        case 0x93B7 | 0x93D7:
            return _decode_correct_format("ASTC", image_data, width, height, 8, 8)
        case 0x93B8 | 0x93D8:
            return _decode_correct_format("ASTC", image_data, width, height, 10, 5)
        case 0x93B9 | 0x93D9:
            return _decode_correct_format("ASTC", image_data, width, height, 10, 6)
        case 0x93BA | 0x93DA:
            return _decode_correct_format("ASTC", image_data, width, height, 10, 8)
        case 0x93BB | 0x93DB:
            return _decode_correct_format("ASTC", image_data, width, height, 10, 10)
        case 0x93BC | 0x93DC:
            return _decode_correct_format("ASTC", image_data, width, height, 12, 10)
        case 0x93BD | 0x93DD:
            return _decode_correct_format("ASTC", image_data, width, height, 12, 12)
    raise ValueError(f"Unknown KTX format: {glInternalFormat:#x}")


def astc_convert(data: bytes):
    """Convert ASTC to Image."""
    f = ConstBitStream(io.BytesIO(data))
    block_x, block_y = cast(tuple[int, int], f.readlist("pad32, 2*uintle8 , pad8"))
    width, height = f.readlist("2*uintle24, pad24")
    return _decode_correct_format(
        "ASTC", f.read("bytes"), width, height, block_x, block_y
    )


def convert_image(data, extension):
    """Identify and convert image data to Image."""
    if data[:8] == b"CompBlks":
        # The packaging pipeline re-encodes textures as CompBlks blocks while
        # the restored path keeps the original `.dds` name, so decode by
        # content instead of trusting the extension.
        return compblks_convert(data)
    if extension in ("dds", "psd"):
        image = _pillow_image_conversion(data, extension)
        if extension == "psd":
            image = image.convert("RGBA")
        return image
    if extension == "pvr":
        return pvr_convert(data)
    if extension in ("ktx", "ktx_low"):
        return ktx_convert(data)
    if extension == "astc":
        return astc_convert(data)
    if extension == "cbk":
        return compblks_convert(data)
    raise ValueError(f"Unsupported image format: {extension}")
