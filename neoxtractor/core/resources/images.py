"""Resource protocol entry for image / texture files.

FORMAT  -> "image"
probe   -> header-only magic sniff, returns an AVPROBE-style score
read    -> canonical ``PIL.Image.Image`` (standard formats decode lazily from
           the stream; packed GPU textures -- PVR/KTX/ASTC/CBK -- decode from
           their full block)
convert -> target strings in ``TARGETS`` (PNG/JPEG/BMP bytes, numpy arrays)

File-like input contract (shared by probe/read): the argument is a seekable
binary stream exposing ``read``/``seek``/``tell`` (``seek`` supports
``io.SEEK_END``) or plain ``bytes``.  ``probe`` reads only a header.  All
heavy lifting lives in the private ``_images`` package.
"""

from __future__ import annotations

import io
from typing import Optional

from PIL import Image

from neoxtractor.core.utils.filelike import read_all, read_head
from neoxtractor.core.resources._images import decode as _d
from neoxtractor.core.utils.result import Category, ProbeResult
from neoxtractor.core.utils.target import Target

FORMAT = "image"
CANONICAL_TYPE = "PIL.Image.Image"
TARGETS = (
    Target("png", "file", ".png", "PNG image"),
    Target("jpeg", "file", ".jpg", "JPEG image"),
    Target("bmp", "file", ".bmp", "Bitmap image"),
    Target("np_rgb", "view", desc="RGB numpy array (H, W, 3)"),
    Target("np_rgba", "view", desc="RGBA numpy array (H, W, 4)"),
)

# (prefix, offset, codec key).  Score is constant (exact magic) except RIFF
# which needs the WEBP fourcc to confirm.
_MAGIC: list[tuple[bytes, int, str]] = [
    (b"\x89PNG\r\n\x1a\n", 0, "png"),
    (b"\xff\xd8\xff", 0, "jpg"),
    (b"GIF87a", 0, "gif"),
    (b"GIF89a", 0, "gif"),
    (b"BM", 0, "bmp"),
    (b"II*\x00", 0, "tiff"),
    (b"MM\x00*", 0, "tiff"),
    (b"\x00\x00\x01\x00", 0, "ico"),
    (b"DDS ", 0, "dds"),
    (b"8BPS", 0, "psd"),
    (b"\x13\xab\xa1\x5c", 0, "astc"),
    (b"PVR", 0, "pvr"),
    (b"\xabKTX ", 0, "ktx"),
    (b"CompBlks", 0, "cbk"),
]

_CODECS = {
    "pvr": _d.pvr_convert,
    "ktx": _d.ktx_convert,
    "astc": _d.astc_convert,
    "cbk": _d.compblks_convert,
}


def _is_tga(head: bytes, f) -> bool:
    """Heuristic TGA check (TGA has no leading magic).

    Validates the header fields only; scored lower than exact-magic formats
    in :func:`probe` so a competing real magic wins any tie.  ``f`` is
    reserved for a future footer check (v2 ``TRUEVISION-XFILE`` via SEEK_END).
    """
    if len(head) < 18:
        return False
    color_map_type = head[1]
    image_type = head[2]
    if color_map_type not in (0, 1) or image_type not in (1, 2, 3, 9, 10, 11):
        return False
    depth = head[16]
    if depth not in (8, 15, 16, 24, 32):
        return False
    width = head[12] | (head[13] << 8)
    height = head[14] | (head[15] << 8)
    if width == 0 or height == 0:
        return False
    image_desc = head[17]
    if (image_desc >> 4) > 8:  # alpha depth nibble must be 0..8
        return False
    return True


def _sniff(f) -> Optional[str]:
    """Return the codec key for a stream/bytes by inspecting its header."""
    head = read_head(f, 18)  # 18 bytes: TGA header fields reach offset 17
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    for prefix, offset, key in _MAGIC:
        if head[offset:offset + len(prefix)] == prefix:
            return key
    if _is_tga(head, f):
        return "tga"
    return None


def probe(f) -> ProbeResult:
    """Probe *f* as a known image/texture (header only).

    Exact-magic formats score 100; the TGA heuristic (no magic exists)
    scores 50 so a real magic from another protocol can win a tie.  The
    guessed extension is the codec key itself (png/jpg/tga/...); every
    image is category TEXTURE.
    """
    key = _sniff(f)
    if key is None:
        return ProbeResult(0)
    score = 50 if key == "tga" else 100
    return ProbeResult(score, key, Category.TEXTURE)


def read(f) -> Optional[Image.Image]:
    """Decode *f* to a canonical PIL image, or None if not an image."""
    key = _sniff(f)
    if key is None:
        return None
    if key in _CODECS:
        image = _CODECS[key](read_all(f))
    else:
        image = _d.pillow_open(f, key)
        if key == "psd":
            image = image.convert("RGBA")
    image.load()  # force decode so the canonical image is self-contained
    return image


def convert(image: Image.Image, target: str):
    """Convert a canonical PIL image to a specific output form.

    Targets: ``png``/``jpeg``/``bmp`` -> encoded bytes;
    ``np_rgb``/``np_rgba`` -> numpy arrays of shape (H, W, 3) / (H, W, 4).
    """
    match target:
        case "png":
            return _d.image_to_png_data(image)
        case "jpeg":
            buf = io.BytesIO()
            image.convert("RGB").save(buf, "JPEG")
            return buf.getvalue()
        case "bmp":
            buf = io.BytesIO()
            image.save(buf, "BMP")
            return buf.getvalue()
        case "np_rgb" | "np_rgba":
            import numpy as np

            mode = "RGB" if target == "np_rgb" else "RGBA"
            return np.asarray(image.convert(mode))
        case _:
            raise ValueError(f"Unsupported image target: {target!r}")
