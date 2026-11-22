"""Resolve HPMA resource paths to typed archive entries without fuzzy naming."""

import re
from pathlib import Path
import zlib

from core.npk.npkhash_v1 import mesh_hash
from core.npk.scan_control import checkpoint, report_progress

RESOURCE_FOLDER = re.compile(r"res(?:[0-9]+)?", re.IGNORECASE)
MANIFEST_LIMIT = 64 * 1024 * 1024
MANIFEST_CHUNK = 64 * 1024


def manifest_lines(manifest, *, progress=None, cancelled=None):
    """Stream plain/CCZ manifests: never hold the entire text or split-lines list."""
    size = manifest.stat().st_size
    if size > MANIFEST_LIMIT:
        raise ValueError("Resource manifest exceeds scan limit")
    with manifest.open("rb") as stream:
        header = stream.read(16)
        decoder = zlib.decompressobj() if header[:4] == b"CCZ!" else None
        chunk = stream.read(MANIFEST_CHUNK) if decoder else header
        pending, inflated = b"", 0
        while chunk:
            checkpoint(cancelled)
            data = decoder.decompress(chunk, MANIFEST_CHUNK) if decoder else chunk
            inflated += len(data)
            if inflated > MANIFEST_LIMIT:
                raise ValueError("Inflated resource manifest exceeds scan limit")
            lines = (pending + data).split(b"\n")
            pending = lines.pop()
            if len(pending) > MANIFEST_CHUNK:
                raise ValueError("Resource manifest line exceeds scan limit")
            for line in lines:
                if len(line) > MANIFEST_CHUNK:
                    raise ValueError("Resource manifest line exceeds scan limit")
                yield line.decode("utf-8", "replace")
            report_progress(progress, "Scanning resource manifest", stream.tell(), max(size, 1))
            chunk = decoder.unconsumed_tail if decoder and decoder.unconsumed_tail else stream.read(MANIFEST_CHUNK)
        if decoder and not decoder.eof:
            raise ValueError("Incomplete compressed resource manifest")
        if pending:
            yield pending.decode("utf-8", "replace")
        report_progress(progress, "Scanning resource manifest", size, max(size, 1))


def normalize_path(path: str) -> str:
    return re.sub(r"[\\/]+", r"\\", str(path).strip()).lstrip(".\\").lower()


def murmur3_32(text: str, seed: int = 0x9747B28C) -> int:
    """MurmurHash3 x86/32 as used by the supplied HPMA resource archives."""
    data = text.encode("utf-8")
    mask = 0xFFFFFFFF
    h = seed

    def mix(k):
        k = (k * 0xCC9E2D51) & mask
        k = ((k << 15) | (k >> 17)) & mask
        return (k * 0x1B873593) & mask

    end = len(data) & -4
    for offset in range(0, end, 4):
        h ^= mix(int.from_bytes(data[offset : offset + 4], "little"))
        h = ((h << 13) | (h >> 19)) & mask
        h = (h * 5 + 0xE6546B64) & mask
    if end < len(data):
        h ^= mix(int.from_bytes(data[end:], "little"))
    h ^= len(data)
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & mask
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & mask
    h ^= h >> 16
    return h


def archive_root(npk_file) -> str:
    """Recover the mount path from res/model/partner.npk, etc."""
    path = Path(getattr(npk_file, "file_path", ""))
    if not path.name:
        return ""
    parts = list(path.with_suffix("").parts)
    for i in range(len(parts) - 1, -1, -1):
        if RESOURCE_FOLDER.fullmatch(parts[i]):
            return normalize_path("\\".join(parts[i + 1 :]))
    return ""


def compatible_extension(path: str, extension: str) -> bool:
    expected = path.rsplit(".", 1)[-1]
    extension = str(extension).lower().lstrip(".")
    return extension in {
        "tga": {"tga", "dds"},
        "mtg": {"mtg", "mtl"},
        "mtl": {"mtl", "mtg"},
    }.get(expected, {expected})


class AssetPathIndex:
    def __init__(self, npk_file, signature_name_map=None, *, progress=None, cancelled=None):
        self.cancelled = cancelled
        self.archive = npk_file
        self.entries = getattr(npk_file, "entries", {})
        self.by_signature = {}
        self.by_name = {}
        self.cache = {}
        self.mount = archive_root(npk_file)
        # A loaded archive may carry its own index (shared with other views);
        # use it instead of building another.
        if hasattr(npk_file, "resolve_asset_path"):
            report_progress(progress, "Using shared archive path index", 1, 1)
            return
        indices = getattr(npk_file, "indices", ())
        report_progress(progress, "Indexing archive paths", 0, len(self.entries))
        for current, (index, entry) in enumerate(self.entries.items(), 1):
            checkpoint(cancelled)
            if current % 128 == 0 or current == len(self.entries):
                report_progress(progress, "Indexing archive paths", current, len(self.entries))
            self.by_signature.setdefault(entry.file_signature, []).append(index)
            names = [getattr(entry, "filename", ""), (signature_name_map or {}).get(hex(entry.file_signature), "")]
            if index < len(indices):
                names.append(getattr(indices[index], "filename", ""))
            for name in names:
                if name:
                    self.by_name.setdefault(normalize_path(name), set()).add(index)

    def resolve(self, path: str):
        checkpoint(self.cancelled)
        path = normalize_path(path)
        if hasattr(self.archive, "resolve_asset_path"):
            return self.archive.resolve_asset_path(path)
        if path in self.cache:
            return self.cache[path]
        matches = set(self.by_name.get(path, ()))
        stem = path.rsplit(".", 1)[0]
        # Names without an extension are stored under the bare stem.
        matches.update(self.by_name.get(stem, ()))
        actual_paths = {path}
        if path.endswith(".tga"):
            actual_paths.add(stem + ".dds")
        elif path.endswith(".mtl"):
            actual_paths.add(stem + ".mtg")
        for actual in actual_paths:
            relative = None
            if self.mount and actual.startswith(self.mount + "\\"):
                relative = actual[len(self.mount) + 1 :]
            elif not self.mount and actual.startswith("model\\"):
                relative = actual.split("\\", 2)[-1]
            if relative is not None:
                matches.update(self.by_signature.get(murmur3_32(relative), ()))
            matches.update(self.by_signature.get(murmur3_32(actual), ()))
            matches.update(self.by_signature.get(mesh_hash(actual), ()))
        matches = {i for i in matches if compatible_extension(path, self.entries[i].extension)}
        result = (next(iter(matches)), "Path / type verified") if len(matches) == 1 else None
        self.cache[path] = result
        return result

    def manifest_paths(self, *, progress=None, cancelled=None):
        """Read the local CCZ resource manifest, never execute game scripts."""
        if hasattr(self.archive, "get_manifest_paths"):
            return self.archive.get_manifest_paths(progress=progress, cancelled=cancelled)
        checkpoint(cancelled)
        report_progress(progress, "Reading resource manifest", 0, 1)
        if not self.mount:
            return []
        archive_path = Path(self.archive.file_path)
        manifest = None
        for parent in archive_path.parents:
            if RESOURCE_FOLDER.fullmatch(parent.name):
                manifest = parent / "resdb"
                break
        if manifest is None or not manifest.is_file():
            return []
        try:
            result = []
            prefix = self.mount + "\\"
            for current, line in enumerate(manifest_lines(manifest, progress=progress, cancelled=cancelled), 1):
                if current % 256 == 0:
                    checkpoint(cancelled)
                path = normalize_path(line.partition(" ")[2])
                if path.startswith(prefix) and path.endswith((".mesh", ".gim", ".mtg", ".mtl", ".dds", ".tga")):
                    result.append(path)
            return result
        except (OSError, zlib.error, ValueError):
            return []
