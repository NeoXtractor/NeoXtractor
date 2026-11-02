"""Self-contained Wwise bank reader used at runtime and by the index tools.

Only the fields needed to walk `event -> action -> node -> media source` are
parsed, so the tool can mount the events of the banks found inside a loaded
package without any third-party dependency.

Supported layout is the one used by Wwise 135-era banks (`BKHD` version 135),
which is also the layout shared by the game this tool targets.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field

from core.logger import get_logger

HIRC_EVENT = 4
HIRC_ACTION = 3
HIRC_SOUND = 2
HIRC_MUSIC_TRACK = 11
HIRC_CONTAINERS = {5, 6, 9, 10, 11, 12, 13}

# Offsets inside an object payload, which always starts with `ulID` (u32).
ACTION_TARGET_OFFSET = 6  # ulID + ulActionType (u16) -> idExt
SOUND_SOURCE_OFFSET = 9  # ulID + ulPluginID (u32) + StreamType (u8) -> sourceID

# Music tracks (HIRC type 11), layout taken from wwiser
# (`CAkMusicTrack::SetInitialValues`): ulID, u8 flags, u32 numSources, then
# AkBankSourceData per source (u32 pluginID, u8 streamType, u32 sourceID...).
MUSIC_TRACK_SOURCES_OFFSET = 5
MUSIC_TRACK_FIRST_SOURCE_OFFSET = 9
MUSIC_TRACK_SOURCE_ID_OFFSET = 14  # 9 + pluginID (4) + streamType (1)


@dataclass
class HircObject:
    """A single HIRC object: type, owning bank, and raw payload."""

    hirc_type: int
    bank: str
    data: bytes


@dataclass
class BankDatabase:
    """All HIRC objects of a bank folder, addressable by their 32-bit id."""

    version: int = 0
    objects: dict[int, HircObject] = field(default_factory=dict)
    media_sids: set[int] = field(default_factory=set)
    _sources_cache: dict[int, set[int]] = field(default_factory=dict)

    def add_bank(self, path: str) -> int:
        """Scan a .bnk file and merge its objects into the database."""
        bank = os.path.splitext(os.path.basename(path))[0]
        return self.add_bank_bytes(open(path, "rb").read(), bank)

    def add_bank_bytes(self, raw: bytes, bank: str = "") -> int:
        """Scan bank data already in memory and merge its HIRC objects."""
        count = 0
        offset = 0
        while offset + 8 <= len(raw):
            tag = raw[offset:offset + 4]
            size = _u32(raw, offset + 4)
            if tag == b'BKHD' and size >= 8:
                self.version = _u32(raw, offset + 8)
            elif tag == b'DIDX' and size >= 12:
                # header of the inline media table, also the media ids of this
                # bank (used to recognise music track sources)
                for pos in range(offset + 8, offset + 8 + size - 11, 12):
                    self.media_sids.add(_u32(raw, pos))
            elif tag == b'HIRC' and size >= 4:
                base = offset + 8
                items = _u32(raw, base)
                pos = base + 4
                for _ in range(items):
                    if pos + 5 > len(raw):
                        break
                    hirc_type = raw[pos]
                    item_size = _u32(raw, pos + 1)
                    body = pos + 5
                    if body + 4 > len(raw):
                        break
                    obj_id = _u32(raw, body)
                    self.objects.setdefault(
                        obj_id,
                        HircObject(hirc_type, bank, raw[body:body + item_size]),
                    )
                    count += 1
                    pos = body + item_size
            offset += 8 + size
        return count

    def sources(self, obj_id: int) -> set[int]:
        """Collect every media source id reachable from a HIRC object."""
        return self._walk(obj_id, frozenset())

    def _walk(self, obj_id: int, seen: frozenset[int]) -> set[int]:
        cached = self._sources_cache.get(obj_id)
        if cached is not None:
            return cached
        if obj_id in seen:
            return set()

        obj = self.objects.get(obj_id)
        if obj is None:
            return set()

        data = obj.data
        result: set[int] = set()
        if obj.hirc_type == HIRC_EVENT:
            for action_id in event_action_ids(self, obj_id):
                result |= self._walk(action_id, seen | {obj_id})
        elif obj.hirc_type == HIRC_SOUND:
            sid = _u32(data, SOUND_SOURCE_OFFSET)
            if sid:
                result.add(sid)
        elif obj.hirc_type == HIRC_ACTION:
            target = _u32(data, ACTION_TARGET_OFFSET)
            if target:
                result |= self._walk(target, seen | {obj_id})
        elif obj.hirc_type == HIRC_MUSIC_TRACK:
            result |= _music_track_sources(data, self.media_sids)
        elif obj.hirc_type in HIRC_CONTAINERS:
            # Container children are stored in a version dependent position,
            # so match any 32-bit value that names another container / sound.
            for pos in range(4, len(data) - 3):
                value = _u32(data, pos)
                if value == obj_id:
                    continue
                child = self.objects.get(value)
                if child is None:
                    continue
                if child.hirc_type == HIRC_SOUND or child.hirc_type in HIRC_CONTAINERS:
                    result |= self._walk(value, seen | {obj_id})

        self._sources_cache[obj_id] = result
        return result


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from('<I', data, offset)[0]


def _music_track_sources(data: bytes, media_sids: set[int]) -> set[int]:
    """Media source ids of a music track (layout from wwiser's parser).

    The first source sits at a fixed offset. Any further source or playlist
    entry has variable length (plugin params), so the remaining payload is
    scanned for ids that are actually present in the bank's media table.
    """
    sids: set[int] = set()
    if len(data) >= MUSIC_TRACK_SOURCE_ID_OFFSET + 4:
        sid = _u32(data, MUSIC_TRACK_SOURCE_ID_OFFSET)
        if sid:
            sids.add(sid)
    for pos in range(MUSIC_TRACK_FIRST_SOURCE_OFFSET, len(data) - 3):
        value = _u32(data, pos)
        if value in media_sids:
            sids.add(value)
    return sids


def event_action_ids(database: BankDatabase, event_id: int) -> list[int]:
    """Action ids of an event object (v135 layout: u8 count + u32 ids)."""
    obj = database.objects.get(event_id)
    if obj is None or obj.hirc_type != HIRC_EVENT:
        return []
    count = obj.data[4]
    ids = [_u32(obj.data, 5 + 4 * index) for index in range(count)]
    return ids


def scan_bank_dir(bank_dir: str) -> BankDatabase:
    """Scan every `*.bnk` in a folder (recursively) into a database."""
    database = BankDatabase()
    paths = []
    for root, _dirs, files in os.walk(bank_dir):
        for name in files:
            if name.lower().endswith('.bnk'):
                paths.append(os.path.join(root, name))
    paths.sort()
    for path in paths:
        try:
            database.add_bank(path)
        except Exception:  # noqa: BLE001
            get_logger().exception("Wwise: failed to scan bank %s", path)
    get_logger().info(
        "Wwise: scanned %d bank(s), %d HIRC objects (bank version %d)",
        len(paths),
        len(database.objects),
        database.version,
    )
    return database
