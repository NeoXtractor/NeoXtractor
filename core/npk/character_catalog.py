"""Typed, metadata-backed character assets and material texture relationships."""

from dataclasses import dataclass, field, asdict

from core.npk.asset_paths import AssetPathIndex, normalize_path
from core.npk.character_folders import scan_character_folders, normalize_character_folder_roots
from core.npk.scan_control import checkpoint, report_progress
from core.npk.metadata_references import metadata_paths


def asset_kind(path):
    extension = path.rsplit(".", 1)[-1]
    if extension == "mesh":
        return "Mesh"
    if extension in {"png", "jpeg", "tga", "dds", "jpg"}:
        return "Texture"
    return "Metadata"


@dataclass
class CharacterAsset:
    path: str
    kind: str
    entry_index: int | None
    evidence: str
    sources: set[int] = field(default_factory=set)
    textures: list[str] = field(default_factory=list)
    metadata: list[str] = field(default_factory=list)
    source_archive: str = ""


@dataclass
class CharacterCatalog:
    assets: dict[str, CharacterAsset]
    folders: dict[str, list[str]]
    unmapped: list[int]
    scanned_entries: int
    manifest_used: bool

    def to_dict(self):
        return {
            "schema_version": 3,
            "scanned_entries": self.scanned_entries,
            "manifest_used": self.manifest_used,
            "folders": self.folders,
            "unmapped_entries": self.unmapped,
            "assets": [{**asdict(a), "sources": sorted(a.sources)} for a in self.assets.values()],
        }


def build_character_catalog(npk_file, roots=None, signature_name_map=None, *, progress=None, cancelled=None):
    if hasattr(npk_file, "build_catalog"):
        return npk_file.build_catalog(roots, signature_name_map, progress=progress, cancelled=cancelled)
    checkpoint(cancelled)
    index = AssetPathIndex(npk_file, signature_name_map, progress=progress, cancelled=cancelled)
    roots = list(normalize_character_folder_roots(roots))
    if index.mount.startswith("model\\") and index.mount not in roots:
        roots.append(index.mount)
    report = scan_character_folders(npk_file, roots, progress=progress, cancelled=cancelled)
    assets = {}
    parsed_paths = {}

    def entry_paths(entry_index):
        checkpoint(cancelled)
        if hasattr(npk_file, "get_metadata_paths"):
            return npk_file.get_metadata_paths(entry_index)
        if entry_index not in parsed_paths:
            parsed_paths[entry_index] = metadata_paths(index.entries[entry_index].data)
        return parsed_paths[entry_index]

    def allowed(path):
        return any(path.startswith(root + "\\") for root in roots)

    def add(path, source=None, require_match=False):
        checkpoint(cancelled)
        path = normalize_path(path)
        if not allowed(path) or path.rsplit(".", 1)[-1] not in {
            "png",
            "jpeg",
            "tga",
            "mtl",
            "gim",
            "mtg",
            "dds",
            "mesh",
            "jpg",
        }:
            return None
        resolved = index.resolve(path)
        if require_match and resolved is None:
            return None
        if resolved and path.endswith(".tga") and index.entries[resolved[0]].extension == "dds":
            path = path[:-4] + ".dds"
        if resolved and path.endswith(".mtl"):
            group = index.resolve(path[:-4] + ".mtg")
            if group and group[0] == resolved[0]:
                path = path[:-4] + ".mtg"
        asset = assets.setdefault(
            path,
            CharacterAsset(
                path,
                asset_kind(path),
                resolved[0] if resolved else None,
                resolved[1] if resolved else "Referenced; not in this archive",
            ),
        )
        if source is not None:
            asset.sources.add(source)
        return asset

    report_progress(progress, "Matching metadata references", 0, len(report.folders))
    for current, folder in enumerate(report.folders, 1):
        for ref in folder.references:
            add(ref.asset_path, ref.source_index)
        report_progress(progress, "Matching metadata references", current, len(report.folders))
    manifest_paths = index.manifest_paths(progress=progress, cancelled=cancelled)
    report_progress(progress, "Matching manifest paths", 0, len(manifest_paths))
    for current, path in enumerate(manifest_paths, 1):
        add(path, require_match=True)
        if current % 64 == 0 or current == len(manifest_paths):
            report_progress(progress, "Matching manifest paths", current, len(manifest_paths))

    # Model descriptors name their mesh and material sidecars.
    descriptors = list(assets.values())
    report_progress(progress, "Recovering model sidecars", 0, len(descriptors))
    for current, asset in enumerate(descriptors, 1):
        checkpoint(cancelled)
        report_progress(progress, "Recovering model sidecars", current, len(descriptors))
        if asset.path.endswith(".gim") and asset.entry_index is not None:
            stem = asset.path[:-4]
            for extension in (".mesh", ".mtg"):
                add(stem + extension, asset.entry_index, require_match=True)
            for path in entry_paths(asset.entry_index):
                add(path, asset.entry_index)
    models = list(assets.values())
    report_progress(progress, "Linking meshes and textures", 0, len(models))
    for current, asset in enumerate(models, 1):
        checkpoint(cancelled)
        report_progress(progress, "Linking meshes and textures", current, len(models))
        if asset.kind != "Mesh" or asset.entry_index is None:
            continue
        stem = asset.path[:-5]
        descriptor = index.resolve(stem + ".gim")
        material_paths = {stem + ".mtg", stem + ".mtl"}
        if descriptor:
            meta = add(stem + ".gim", require_match=True)
            if meta:
                asset.metadata.append(meta.path)
            material_paths.update(p for p in entry_paths(descriptor[0]) if p.endswith((".mtg", ".mtl")))
        for material_path in sorted(material_paths):
            material = add(material_path, require_match=True)
            if material is None:
                continue
            if material.path not in asset.metadata:
                asset.metadata.append(material.path)
            for path in entry_paths(material.entry_index):
                if asset_kind(path) == "Texture":
                    texture = add(path, material.entry_index)
                    if texture and texture.path not in asset.textures:
                        asset.textures.append(texture.path)
    folders = {}
    report_progress(progress, "Organizing recovered folders", 0, len(assets))
    for current, path in enumerate(sorted(assets), 1):
        checkpoint(cancelled)
        if current % 128 == 0 or current == len(assets):
            report_progress(progress, "Organizing recovered folders", current, len(assets))
        parts = path.split("\\")
        if len(parts) >= 3:
            # Group each asset under its containing folder.

            folders.setdefault(path.rsplit("\\", 1)[0], []).append(path)
    mapped = {a.entry_index for a in assets.values() if a.entry_index is not None}
    unmapped = [i for i, e in index.entries.items() if e.extension in {"dds", "mesh", "tga"} and i not in mapped]
    checkpoint(cancelled)
    return CharacterCatalog(assets, folders, unmapped, len(index.entries), bool(manifest_paths))
