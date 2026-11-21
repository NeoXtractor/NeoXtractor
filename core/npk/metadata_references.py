"""Compact HPMA dependency names, without retaining decoded documents.

Like MessiahBrowser's dependency index, these records contain names only.
Archive IDs and verification remain the responsibility of the HPMA resolver.
"""

from dataclasses import dataclass
import re

from core.npk.asset_paths import normalize_path


PATH_RE = re.compile(rb"(?<![A-Za-z0-9_.-])[A-Za-z0-9_.-]+(?:[\\/]+[A-Za-z0-9_.-]+)+\.(?:mesh|gim|mtg|mtl|tga|dds|png|jpg)\b", re.I)
MODEL_PATH_RE = re.compile(
    rb"model[\\/]+[A-Za-z0-9_.-]+[\\/]+[A-Za-z0-9_.-]+(?:[\\/]+[A-Za-z0-9_.-]+)*", re.I)


def metadata_bytes(data):
    if isinstance(data, str):
        return data.encode("utf-8", "ignore")
    if not isinstance(data, (bytes, bytearray, memoryview)):
        return b""
    data = bytes(data)
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        data = data.decode("utf-16", "replace").encode("utf-8")
    return data


def metadata_paths(data):
    return sorted({normalize_path(x.decode("utf-8", "replace"))
                   for x in PATH_RE.findall(metadata_bytes(data))})


def model_paths(data):
    return sorted({normalize_path(x.decode("utf-8", "ignore"))
                   for x in MODEL_PATH_RE.findall(metadata_bytes(data))})


@dataclass(frozen=True, slots=True)
class MetadataReferences:
    assets: tuple[str, ...]
    folders: tuple[str, ...]

    @classmethod
    def from_bytes(cls, data):
        data = metadata_bytes(data)
        assets = tuple(metadata_paths(data))
        # Reuse the asset spelling when a folder path also appears as an asset.
        canonical = {path: path for path in assets}
        folders = tuple(canonical.get(path, path) for path in model_paths(data))
        return cls(assets, folders)
