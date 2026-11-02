"""Resolve Wwise event names for the entries of an opened package.

Runtime pipeline (all data comes from the user's own installation):

  * shipped hash index: `FNV-1(event name) -> event name`
  * the banks found inside the loaded package give `event id -> media shortIDs`
    (event ids *are* FNV-1 hashes of the event names, so names come from the
    hash index)
  * `wwise.thy` turns every external shortID into the md5 used by the .idx,
    so package entries can be labelled without ever storing offsets
"""

from __future__ import annotations

import os

from core.logger import get_logger

from .bank_scan import HIRC_EVENT, scan_bank_dir
from .hashes import xxh32
from .index_builder import bundled_index_path, load_index_events
from .thy import guess_thy_path, load_thy_keys

VFS_KEY_SEED = 163


def _thy_pair_from_thy(thy_path: str) -> list[str]:
    """Return `[base thy, hot thy]` for a given `.thy`, skipping missing files."""
    thy_path = os.path.abspath(thy_path)
    thy_dir = os.path.dirname(thy_path)
    name = os.path.basename(thy_path)
    parent = os.path.dirname(thy_dir)
    if os.path.basename(parent).lower() == "documents":
        base_thy = os.path.join(os.path.dirname(parent), os.path.basename(thy_dir), name)
        hot_thy = thy_path
    else:
        base_thy = thy_path
        hot_thy = os.path.join(parent, "Documents", os.path.basename(thy_dir), name)
    return [path for path in (base_thy, hot_thy) if os.path.exists(path)]


def _thy_pair(idx_path: str) -> list[str]:
    """Return `[base thy, hot thy]` for an IDX, skipping missing files."""
    idx_path = os.path.abspath(idx_path)
    idx_dir = os.path.dirname(idx_path)
    name = os.path.basename(idx_path)
    parent = os.path.dirname(idx_dir)
    if os.path.basename(parent).lower() == "documents":
        base_idx = os.path.join(os.path.dirname(parent), os.path.basename(idx_dir), name)
        hot_idx = idx_path
    else:
        base_idx = idx_path
        hot_idx = os.path.join(parent, "Documents", os.path.basename(idx_dir), name)

    paths: list[str] = []
    for candidate_idx in (base_idx, hot_idx):
        if not os.path.exists(candidate_idx):
            continue
        guess = guess_thy_path(candidate_idx)
        if guess and os.path.exists(guess):
            paths.append(guess)
    return paths


class WwiseEventIndex:
    """Event-name lookup backed by the banks found in the loaded package."""

    def __init__(self):
        self.events: dict[str, None] = {}
        self.name_by_hash: dict[int, str] = {}
        self.short_id_events: dict[int, list[str]] = {}
        self.md5_events: dict[str, list[str]] = {}
        self.thy_path: str | None = None
        self._thy_keys: dict[int, str] = {}

    # ---------------------------------------------------------------- loading

    def load_hash_index(self, path: str) -> int:
        """Load the distributable event hash index (binary or JSON)."""
        path = os.path.abspath(path)
        events = load_index_events(path)

        self.name_by_hash = dict(events)
        self.events = {}
        for name in events.values():
            self.events[name] = None

        get_logger().info(
            "Wwise event hash index: %d names from %s", len(self.name_by_hash), path
        )
        return len(self.name_by_hash)

    def load_bundled_index(self) -> int:
        """Load the hash index shipped in `id5mapping/wwise/`, if available."""
        path = bundled_index_path()
        if not path:
            get_logger().warning(
                "Wwise: bundled event hash index not found in id5mapping/wwise/"
            )
            return 0
        return self.load_hash_index(path)

    @property
    def thy_key_count(self) -> int:
        """Number of shortID → md5 records currently loaded from `.thy`."""
        return len(self._thy_keys)

    def load_thy(self, path: str | None) -> int:
        """Load a .thy table and resolve mounted shortIDs to package md5 names."""
        if not path:
            return 0
        path = os.path.abspath(path)
        self._thy_keys = load_thy_keys(path)
        self.thy_path = path
        get_logger().info("Wwise .thy: %d keys loaded from %s", len(self._thy_keys), path)
        self._rebuild()
        return len(self._thy_keys)

    def load_thy_files(self, paths: list[str]) -> int:
        """Load several `.thy` tables; later files override earlier ones."""
        merged: dict[int, str] = {}
        loaded: list[str] = []
        for path in paths:
            if not path or not os.path.exists(path):
                continue
            merged.update(load_thy_keys(path))
            loaded.append(os.path.abspath(path))
        if not merged:
            return 0
        self._thy_keys = merged
        self.thy_path = loaded[-1]
        get_logger().info(
            "Wwise .thy: %d keys merged from %s", len(self._thy_keys), ", ".join(loaded)
        )
        self._rebuild()
        return len(self._thy_keys)

    def load_thy_pair(self, path: str) -> int:
        """Load a `.thy` and its base/hot counterpart (the hot table wins)."""
        return self.load_thy_files(_thy_pair_from_thy(path))

    def autodetect_thy(self, idx_path: str | None) -> str | None:
        """Load the `.thy` tables belonging to an opened `.idx`, if they can be guessed.

        A hot update package only carries changed files, so the base table is
        loaded first and the `Documents` copy overrides it.
        """
        if self._thy_keys:
            return self.thy_path
        paths = _thy_pair(idx_path or "")
        if not paths:
            guess = guess_thy_path(idx_path or "")
            paths = [guess] if guess else []
        merged: dict[int, str] = {}
        for path in paths:
            if not os.path.exists(path):
                continue
            merged.update(load_thy_keys(path))
        if not merged:
            return None
        self._thy_keys = merged
        self.thy_path = paths[-1] if paths else None
        get_logger().info(
            "Wwise .thy: %d keys merged from %s", len(self._thy_keys), ", ".join(paths)
        )
        self._rebuild()
        return self.thy_path

    # ---------------------------------------------------------------- mounting

    def mount_banks(self, bank_data: list[bytes]) -> int:
        """Walk the banks of the loaded package and mount their events."""
        from .bank_scan import BankDatabase  # local import keeps the module light

        database = BankDatabase()
        for raw in bank_data:
            database.add_bank_bytes(raw)
        return self._mount_database(database)

    def mount_bank_folder(self, bank_dir: str) -> int:
        """Mount events from a folder of extracted banks (optional helper)."""
        return self._mount_database(scan_bank_dir(bank_dir))

    def _mount_database(self, database) -> int:
        """Map event ids found in the banks onto their media shortIDs."""
        mounted = 0
        for obj_id, obj in database.objects.items():
            if obj.hirc_type != HIRC_EVENT:
                continue
            name = self.name_by_hash.get(obj_id)
            if not name:
                continue
            sids = database.sources(obj_id)
            if not sids:
                continue
            mounted += 1
            for sid in sids:
                names = self.short_id_events.setdefault(sid, [])
                if name not in names:
                    names.append(name)

        get_logger().info(
            "Wwise: mounted %d bank event(s) -> %d media shortID(s)",
            mounted,
            len(self.short_id_events),
        )
        self._rebuild()
        return mounted

    # ---------------------------------------------------------------- lookup

    def _rebuild(self) -> None:
        self.md5_events = {}
        self._sid_md5 = {}
        if self._thy_keys:
            for sid, names in self.short_id_events.items():
                key = xxh32(f"{sid}.wem".encode(), VFS_KEY_SEED)
                md5 = self._thy_keys.get(key)
                if md5:
                    self._sid_md5[sid] = md5
                    merged = self.md5_events.setdefault(md5, [])
                    for name in names:
                        if name not in merged:
                            merged.append(name)
        self._build_unique_labels()
        get_logger().info(
            "Wwise mapping: %d shortIDs resolved to %d package entries",
            len(self.short_id_events),
            len(self.md5_events),
        )

    def _build_unique_labels(self) -> None:
        """Give every media a distinct label (`name_01`, `name_02`, ...).

        Several media can be driven by one event, and two media that share the
        first event name would collide when exported, so a two-digit suffix is
        appended as soon as a label refers to more than one media.
        """
        groups: dict[str, set[str]] = {}
        for sid, names in self.short_id_events.items():
            if not names:
                continue
            groups.setdefault(names[0], set()).add(
                self._sid_md5.get(sid) or f"sid:{sid}"
            )
        for md5, names in self.md5_events.items():
            if names:
                groups.setdefault(names[0], set()).add(md5)

        key_labels: dict[str, str] = {}
        for label, keys in groups.items():
            ordered = sorted(keys)
            if len(ordered) == 1:
                key_labels[ordered[0]] = label
            else:
                for index, key in enumerate(ordered):
                    key_labels[key] = f"{label}_{index + 1:02d}"

        self._sid_labels = {
            sid: key_labels[key]
            for sid in self.short_id_events
            if (key := self._sid_md5.get(sid) or f"sid:{sid}") in key_labels
        }
        self._md5_labels = {
            md5: key_labels[md5]
            for md5 in self.md5_events
            if md5 in key_labels
        }

    def events_for_md5(self, md5_hex: str) -> list[str]:
        """Event names whose media lives in the package entry with this md5."""
        return self.md5_events.get((md5_hex or "").lower(), [])

    def events_for_sid(self, sid: int) -> list[str]:
        """Event names that use the given Wwise media shortID."""
        return self.short_id_events.get(int(sid), [])

    def label_for_sid(self, sid: int) -> str | None:
        """Unique display label for a bank media shortID."""
        return getattr(self, "_sid_labels", {}).get(int(sid))

    def label_for_md5(self, md5_hex: str) -> str | None:
        """Unique display label for a package entry."""
        return getattr(self, "_md5_labels", {}).get((md5_hex or "").lower())
