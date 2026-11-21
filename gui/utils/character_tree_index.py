"""Small catalog-backed index: filtering/export must not require widget rows."""

from core.npk.class_types import NPKEntryDataFlags


class CharacterTreeIndex:
    def __init__(self, catalog, entries):
        self.catalog, self.entries = catalog, entries
        self.groups = {}
        for folder, keys in catalog.folders.items():
            for key in keys:
                kind = catalog.assets[key].kind
                self.groups.setdefault((folder, kind), []).append(key)
        self.visible_keys, self.shown_keys, self.visible_directories = set(), set(), set()
        self.visible_entries = set()
        self.text, self.kind, self.show_missing = "", "", False
        self.binary = self.text_files = self.slot = True

    def matches(self, asset, mesh_path=""):
        entry = self.entries.get(asset.entry_index)
        if entry is None and not self.show_missing:
            return False
        if self.text not in (mesh_path + " " + asset.path).lower():
            return False
        if self.kind and asset.kind != self.kind and not (mesh_path and self.kind == "Mesh"):
            return False
        return self.flags_match(entry)

    def flags_match(self, entry):
        if entry is None:
            return True
        include = self.text_files if entry.data_flags & NPKEntryDataFlags.TEXT else self.binary
        return include and (self.slot or entry.file_original_length != 0)

    def filter_steps(self, text="", kind="", show_missing=False, binary=True, text_files=True, slot=True):
        self.text, self.kind, self.show_missing = text.lower(), kind, show_missing
        self.binary, self.text_files, self.slot = binary, text_files, slot
        self.visible_keys, self.shown_keys, self.visible_directories = set(), set(), set()
        self.visible_entries = set()
        assets = self.catalog.assets
        for current, (key, asset) in enumerate(assets.items(), 1):
            own = self.matches(asset)
            shown = own
            if own:
                self.visible_keys.add(key)
                if asset.entry_index is not None:
                    self.visible_entries.add(asset.entry_index)
            if asset.kind == "Mesh":
                for linked in asset.textures + asset.metadata:
                    related = assets[linked]
                    if self.matches(related, asset.path):
                        shown = True
                        if related.entry_index is not None:
                            self.visible_entries.add(related.entry_index)
            if shown:
                self.shown_keys.add(key)
                parts = asset.path.rsplit("\\", 1)[0].split("\\")
                for depth in range(2, len(parts) + 1):
                    self.visible_directories.add("\\".join(parts[:depth]))
            if current % 128 == 0 or current == len(assets):
                yield "Filtering character catalog", current, max(len(assets), 1)
        for index in self.catalog.unmapped:
            if self.unmapped_matches(index):
                self.visible_entries.add(index)

    def unmapped_matches(self, index):
        entry = self.entries[index]
        kind = "Mesh" if entry.extension == "mesh" else "Texture"
        return (not self.kind or self.kind == kind) and self.flags_match(entry) and self.text in entry.filename.lower()

    def keys_for(self, spec, filtered=True):
        tag, path, kind = spec
        if tag == "group":
            keys = self.groups.get((path, kind), ())
            return [key for key in keys if not filtered or key in self.shown_keys]
        if tag == "links":
            mesh = self.catalog.assets[path]
            keys = mesh.textures if kind == "Texture" else mesh.metadata
            return [key for key in keys if not filtered or self.matches(self.catalog.assets[key], mesh.path)]
        return []

    def add_asset_entries(self, result, key, visible_only=False, mesh_path=""):
        asset = self.catalog.assets[key]
        if asset.entry_index is not None and (not visible_only or self.matches(asset, mesh_path)):
            result.setdefault(asset.entry_index, asset.path)
        if asset.kind == "Mesh":
            for linked in asset.textures + asset.metadata:
                self.add_asset_entries(result, linked, visible_only, asset.path)

    def folder_entries(self, result, folder="", visible_only=False, kind=""):
        for key, asset in self.catalog.assets.items():
            if folder and not asset.path.startswith(folder + "\\"):
                continue
            if kind and asset.kind != kind:
                continue
            self.add_asset_entries(result, key, visible_only)
        if not folder:
            for index in self.catalog.unmapped:
                if not visible_only or self.unmapped_matches(index):
                    result.setdefault(index, None)
