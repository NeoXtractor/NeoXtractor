"""HPMA same-name resource sets, with explicit-reference-only external lookup."""

from collections import OrderedDict
from copy import copy
from pathlib import Path
from threading import RLock
from types import SimpleNamespace

from core.npk.asset_paths import (
    AssetPathIndex,
    archive_root,
    normalize_path,
    murmur3_32,
    compatible_extension,
    RESOURCE_FOLDER,
)
from core.npk.npkhash_v1 import mesh_hash
from core.npk.class_types import NPKEntry, NPKEntryDataFlags, State
from core.npk.detection import get_file_category
from core.npk.npk_file import NPKFile
from core.npk.scan_control import checkpoint, report_progress
from core.npk.metadata_references import MetadataReferences
from core.process_memory import check_scan_memory

METADATA_EXTENSIONS = {"mtl", "dat", "xml", "gim", "mtg"}
BINARY_CACHE_BYTES = 32 * 1024 * 1024
PATH_CACHE_ENTRIES = 16384


def resource_roots(game):
    """Inspect only immediate resource directories, never the whole game tree."""
    roots = [
        child
        for child in game.iterdir()
        if RESOURCE_FOLDER.fullmatch(child.name) and child.is_dir() and child.resolve().is_relative_to(game)
    ]
    return sorted(roots, key=lambda root: (int(root.name[3:] or "0"), root.name.lower()))


def resource_location(path):
    path = Path(path).resolve()
    for parent in path.parents:
        if RESOURCE_FOLDER.fullmatch(parent.name):
            return parent.parent, parent, path.relative_to(parent)
    return None


def related_archive_paths(path):
    """Only the exact same relative filename under res/resN; no tree scan."""
    path = Path(path).resolve()
    location = resource_location(path)
    if location is None:
        return [path]
    game, _resource, relative = location
    result = [path]
    for resource in resource_roots(game):
        candidate = (resource / relative).resolve()
        if candidate != path and candidate.is_file() and candidate.is_relative_to(game):
            result.append(candidate)
    return result


class RelatedArchiveSet:
    """Global integer IDs preserve (archive path, local index), not just hashes.

    Only initial same-name packs are enumerated. Referenced packs are opened by
    exact mount path; they are never scanned for unrelated entries. Binary
    payloads have a small LRU cache. Metadata is retained as compact dependency
    names, not decoded documents; requested previews reread the source entry.
    """

    def __init__(self, path, options=None, reader=NPKFile):
        self.file_path, self.options, self.reader = str(Path(path).resolve()), options, reader
        self.archives, self.indices, self.entries, self.records = [], [], {}, {}
        self._paths, self._signatures, self._manifests = {}, {}, {}
        self._source_labels, self._locations = {}, {}
        self._mounts, self._path_signatures = {}, OrderedDict()
        self._source_offsets, self._external_ids = {}, {}
        self._loaded, self._lock = OrderedDict(), RLock()
        self._resolution_cache = OrderedDict()
        self._metadata_references = {}
        self.warnings, self._missing_mounts = [], set()
        location = resource_location(self.file_path)
        self._resource_roots = resource_roots(location[0]) if location else []
        for candidate in related_archive_paths(path):
            try:
                self._add_archive(candidate, primary=True)
            except (OSError, ValueError) as exc:
                if candidate == Path(self.file_path):
                    raise
                self.warnings.append(f"{candidate}: {exc}")
        self.primary_sources = tuple(range(len(self.archives)))
        self.file_count = len(self.indices)
        self.prepared = False

    def _add_archive(self, path, *, primary=False):
        check_scan_memory(force=True)
        key = str(Path(path).resolve()).lower()
        if key in self._paths:
            return self._paths[key]
        archive = self.reader(str(path), self.options)
        source = len(self.archives)
        self._paths[key] = source
        self.archives.append(archive)
        self._mounts[source] = archive_root(archive)
        source_path = str(Path(archive.file_path).resolve())
        location = resource_location(source_path)
        self._locations[source] = location
        self._source_labels[source] = (
            str(Path(source_path).relative_to(location[0])) if location else Path(source_path).name
        )
        signatures = self._signatures[source] = {}
        self._source_offsets[source] = len(self.indices) if primary else None
        for local, index in enumerate(archive.indices):
            if local % 1024 == 0:
                checkpoint()
            signatures.setdefault(index.file_signature, []).append(local)
            if primary:
                self._entry_id(source, local)
        return source

    def _entry_id(self, source, local):
        offset = self._source_offsets[source]
        key = (source, local)
        global_id = offset + local if offset is not None else self._external_ids.get(key)
        if global_id is not None and global_id in self.entries:
            return global_id
        global_id = len(self.indices)
        if offset is None:
            self._external_ids[key] = global_id
        index = self.archives[source].indices[local]
        entry = NPKEntry()
        entry.__dict__.update(vars(index))
        entry.source_archive = self.archives[source].file_path
        entry.source_entry_index = local
        self.indices.append(index)
        self.entries[global_id] = entry
        self.records[global_id] = key
        return global_id

    @staticmethod
    def _remember(cache, key, value):
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > PATH_CACHE_ENTRIES:
            cache.popitem(last=False)

    def source_label(self, index):
        source, _local = self.records[index]
        return self._source_labels[source]

    def is_entry_loaded(self, index):
        # Descriptors exist up front; their data is read on demand.
        return index in self.entries and self.prepared

    def peek_entry(self, index):
        return self.entries[index]

    def export_path(self, index, path):
        # Keep exports from different resource folders apart.
        label = self.source_label(index).replace("/", "\\").split("\\")[0]
        return label + "\\" + str(path or self.entries[index].filename)

    def _read(self, index, *, cache=False, stream=None):
        source, local = self.records[index]
        archive = self.archives[source]
        if stream is None:
            with open(archive.file_path, "rb") as handle:
                return self._read(index, cache=cache, stream=handle)
        archive.load_entry(local, stream)
        entry = archive.entries.pop(local)
        entry.source_archive = self.entries[index].source_archive
        entry.source_entry_index = local
        previous = self.entries[index]
        if previous.filename and not previous.filename.startswith("0x"):
            entry.filename = previous.filename
        self.entries[index] = entry
        if entry.extension in METADATA_EXTENSIONS or entry.data_flags & NPKEntryDataFlags.TEXT:
            self._metadata_references[index] = MetadataReferences.from_bytes(entry.data)
        if cache:
            size = len(entry.data) + (
                len(entry.source_data) if entry.source_data is not None and entry.source_data is not entry.data else 0
            )
            self._loaded[index] = size
            while len(self._loaded) > 8 or sum(self._loaded.values()) > BINARY_CACHE_BYTES:
                old, _ = self._loaded.popitem(last=False)
                # Keep the descriptor (name, flags, sizes) and drop only the
                # payload of the least recently used entry.
                descriptor = copy(self.entries[old])
                descriptor.data = b""
                descriptor.source_data = None
                self.entries[old] = descriptor
        else:
            descriptor = copy(entry)
            descriptor.data = b""
            descriptor.source_data = None
            self.entries[index] = descriptor
        return entry

    def _references(self, index):
        with self._lock:
            if index not in self._metadata_references:
                entry = self.entries[index]
                if entry.data:
                    self._metadata_references[index] = MetadataReferences.from_bytes(entry.data)
                else:
                    # _read records the references of metadata entries.
                    self._read(index)
            return self._metadata_references.get(index, MetadataReferences((), ()))

    def get_metadata_paths(self, index):
        return self._references(index).assets

    def get_character_folder_paths(self, index):
        return self._references(index).folders

    def find_entry_by_id(self, index):
        with self._lock:
            entry = self.entries[index]
            if not entry.data and entry.file_original_length:
                entry = self._read(index, cache=True)
            if index in self._loaded:
                self._loaded.move_to_end(index)
            return entry

    def manifest_paths_for(self, source, *, progress=None, cancelled=None):
        if source not in self._manifests:
            archive = self.archives[source]
            paths = AssetPathIndex(archive).manifest_paths(progress=progress, cancelled=cancelled)
            if not paths:
                for sibling in related_archive_paths(archive.file_path):
                    if str(sibling).lower() == str(Path(archive.file_path).resolve()).lower():
                        continue
                    paths = AssetPathIndex(
                        SimpleNamespace(file_path=str(sibling), entries={}, indices=[])
                    ).manifest_paths(progress=progress, cancelled=cancelled)
                    if paths:
                        break
            self._manifests[source] = paths
        return self._manifests[source]

    def _hint_names(self, source, progress=None, cancelled=None):
        mount = self._mounts[source]
        for current, path in enumerate(self.manifest_paths_for(source, progress=progress, cancelled=cancelled)):
            if current % 256 == 0:
                checkpoint(cancelled)
            if not path.startswith(mount + "\\"):
                continue
            for local in self._signatures[source].get(murmur3_32(path[len(mount) + 1 :]), ()):
                index = self._entry_id(source, local)
                entry = self.entries[index]
                entry.filename = path
                entry.extension = path.rsplit(".", 1)[-1]
                entry.category = get_file_category(entry.extension)
                if entry.extension in METADATA_EXTENSIONS:
                    entry.data_flags |= NPKEntryDataFlags.TEXT

    def prepare(self, *, progress=None, cancelled=None):
        for source in self.primary_sources:
            self._hint_names(source, progress, cancelled)
        total, completed = self.file_count, 0
        report_progress(progress, "Scanning same-name archive metadata", 0, total)
        for source in self.primary_sources:
            archive = self.archives[source]
            ids = [i for i, (s, _) in self.records.items() if s == source]
            with open(archive.file_path, "rb") as stream:
                for index in ids:
                    checkpoint(cancelled)
                    entry = self.entries[index]
                    # Named metadata is read now; unnamed entries only when small,
                    # since their type is unknown until read.

                    if index not in self._metadata_references and (
                        entry.extension in METADATA_EXTENSIONS
                        or not entry.extension
                        and entry.file_original_length <= 1048576
                    ):
                        with self._lock:
                            self._read(index, stream=stream)
                    completed += 1
                    if completed % 128 == 0 or completed == total:
                        report_progress(progress, "Scanning same-name archive metadata", completed, total)
        self.prepared = True
        for entry in self.entries.values():
            entry.state = State.CACHED

    def _target_sources(self, path, preferred):
        matches = [s for s, mount in self._mounts.items() if mount and path.startswith(mount + "\\")]
        if matches:
            return matches
        location = resource_location(self.file_path)
        if location is None:
            return []
        game = location[0]
        parts = path.split("\\")[:-1]
        # Only plain relative folder names can name a mount.
        if len(parts) < 2 or any(p in {"", ".", ".."} or ":" in p for p in parts):
            return []
        preferred_resource = self._locations[preferred][1]
        for depth in range(len(parts), 0, -1):
            mount = "\\".join(parts[:depth])
            if mount in self._missing_mounts:
                continue
            found = []
            for resource in dict.fromkeys((preferred_resource, *self._resource_roots)):
                candidate = resource.joinpath(*parts[:depth]).with_suffix(".npk").resolve()
                if not candidate.is_relative_to(game) or not candidate.is_file():
                    continue
                try:
                    found.append(self._add_archive(candidate))
                except (OSError, ValueError) as exc:
                    self.warnings.append(f"{candidate}: {exc}")
            if found:
                return found
            self._missing_mounts.add(mount)
        return []

    def resolve_asset_path(self, path, preferred, *, cancelled=None):
        checkpoint(cancelled)
        path = normalize_path(path)
        key = (preferred, path)
        if key in self._resolution_cache:
            self._resolution_cache.move_to_end(key)
            return self._resolution_cache[key]
        variants = {path}
        if path.endswith(".tga"):
            variants.add(path[:-4] + ".dds")
        if path.endswith(".mtl"):
            variants.add(path[:-4] + ".mtg")
        candidates = []
        for source in self._target_sources(path, preferred):
            mount = self._mounts[source]
            hash_key = (mount, path)
            if hash_key not in self._path_signatures:
                signatures = set()
                for actual in variants:
                    if actual.startswith(mount + "\\"):
                        signatures.add(murmur3_32(actual[len(mount) + 1 :]))
                    signatures.update((murmur3_32(actual), mesh_hash(actual)))
                self._remember(self._path_signatures, hash_key, signatures)
            else:
                # Recently used.
                self._path_signatures.move_to_end(hash_key)
            signatures = self._path_signatures[hash_key]
            for signature in signatures:
                for local in self._signatures[source].get(signature, ()):
                    index = self._entry_id(source, local)
                    entry = self.entries[index]
                    if not entry.extension:
                        with self._lock:
                            entry = self._read(index)
                    if compatible_extension(path, entry.extension):
                        candidates.append(index)
        candidates = list(dict.fromkeys(candidates))
        preferred_resource = self._locations[preferred]
        local = [i for i in candidates if self.records[i][0] == preferred]
        if not local and preferred_resource:
            local = [i for i in candidates if self._locations[self.records[i][0]][1] == preferred_resource[1]]
        selected = local or candidates
        if len(selected) != 1:
            self._remember(self._resolution_cache, key, None)
            return None
        index = selected[0]
        result = (index, "Archive-relative path / type matched · " + self.source_label(index))
        self._remember(self._resolution_cache, key, result)
        return result

    def build_catalog(self, roots=None, names=None, *, progress=None, cancelled=None):
        from core.npk.character_catalog import build_character_catalog, CharacterCatalog

        if not self.prepared:
            self.prepare(progress=progress, cancelled=cancelled)
        assets, folders, mapped = {}, {}, set()
        manifest_used = False
        for source in self.primary_sources:
            checkpoint(cancelled)
            scope = SimpleNamespace(
                file_path=self.archives[source].file_path,
                entries=self.entries,
                indices=[],
                metadata_entries={i: entry for i, entry in self.entries.items() if self.records[i][0] == source},
                get_metadata_paths=self.get_metadata_paths,
                get_character_folder_paths=self.get_character_folder_paths,
                resolve_asset_path=lambda path, s=source: self.resolve_asset_path(path, s, cancelled=cancelled),
                get_manifest_paths=lambda **kw: self.manifest_paths_for(source, **kw),
            )
            catalog = build_character_catalog(scope, roots, names, progress=progress, cancelled=cancelled)
            manifest_used |= catalog.manifest_used
            keys = {
                path: f"{asset.entry_index if asset.entry_index is not None else 'missing' + str(source)}:{path}"
                for path, asset in catalog.assets.items()
            }
            for path, asset in catalog.assets.items():
                asset.textures = [keys[p] for p in asset.textures]
                asset.metadata = [keys[p] for p in asset.metadata]
                if asset.entry_index is not None:
                    asset.source_archive = self.entries[asset.entry_index].source_archive
                    mapped.add(asset.entry_index)
                key = keys[path]
                if key not in assets:
                    assets[key] = asset
                    continue
                # The same entry found through another pack: merge the links.
                existing = assets[key]
                existing.sources.update(asset.sources)
                existing.textures = list(dict.fromkeys(existing.textures + asset.textures))
                existing.metadata = list(dict.fromkeys(existing.metadata + asset.metadata))
            for folder, paths in catalog.folders.items():
                folders[folder] = list(dict.fromkeys(folders.get(folder, []) + [keys[p] for p in paths]))
        unmapped = [
            i
            for i, e in self.entries.items()
            if i < self.file_count and i not in mapped and e.extension in {"mesh", "dds", "tga"}
        ]
        return CharacterCatalog(assets, folders, unmapped, len(self.entries), manifest_used)
