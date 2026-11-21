"""The game's data tables (dbx_new, inside the root res.npk) and what they say about character meshes.

A .dbxz is zlib-compressed records (uint32 size + an LZ4 block holding one MessagePack row); a .dbxhz indexes
them. role_dress, item and item_text_os_en together give each mesh its item icon and English name. The result
is cached next to the app.
"""

from __future__ import annotations

import json
import os
import re
import struct
import threading
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path
from core.logger import get_logger

CACHE_VERSION = 4                     # 4: text tables of the Chinese release (item_text_en)
ROOT_PACK = "res.npk"
PATCH_FOLDER = "Documents"
# Generic placeholders some faces get: not a real item, so not worth naming or copying
GENERIC_ICONS = {"image/clothes/pic_maozi_001.png"}
# Placeholder pictures some items use instead of their own icon
GENERIC_ICON_PARTS = ("pic_maozi_001", "icon_weixianshidaoju", "pic_item_face")
# Item sub_type of each wardrobe kind (measured on the dress rows' items): a dress row's id is shared by items of
# other kinds (an eye colour, a face part), so only items of the row's own kind count
KIND_SUBTYPES = {"hair": {1}, "body": {2}, "clothes": {3}, "scarf": {4}, "hat": {11}, "glasses": {16},
                 "earring": {23}, "brooch": {24}, "bag": {27}}


def is_generic_icon(icon) -> bool:
    return not icon or icon in GENERIC_ICONS or any(part in icon for part in GENERIC_ICON_PARTS)


def items_by_dress(tables) -> dict:
    """Wardrobe items (type 200) by the dress row they put on (mini_type), in item-id order."""
    found: dict = {}
    for item_id, row in sorted(tables.keyed_rows("item").items(), key=lambda kv: str(kv[0])):
        if isinstance(row, dict) and row.get("type") == 200 and row.get("mini_type"):
            found.setdefault(row["mini_type"], []).append(dict(row, _id=item_id))
    return found


def best_item(candidates, kind: str, english: dict):
    """The item that sells a dress row of this kind: right sub_type, then a real English name, then a real icon."""
    wanted = KIND_SUBTYPES.get(kind)
    best, best_score = None, None
    for row in candidates or ():
        if wanted and row.get("sub_type") not in wanted:
            continue
        name = (english.get(row.get("name", ""), "") or "").strip()
        score = (name.lower() not in ("", "null!", "null"), not is_generic_icon(row.get("icon_path")))
        if best_score is None or score > best_score:
            best, best_score = row, score
    return best

_lock = threading.Lock()
_memory: dict = {}


# -- decoding ------------------------------------------------------------------------------------
def lz4_block(src: bytes, size: int) -> bytes:
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]
        i += 1
        literal = token >> 4
        if literal == 15:
            while True:
                extra = src[i]
                i += 1
                literal += extra
                if extra != 255:
                    break
        out += src[i:i + literal]
        i += literal
        if i >= n or len(out) >= size:
            break
        offset = src[i] | src[i + 1] << 8
        i += 2
        match = token & 15
        if match == 15:
            while True:
                extra = src[i]
                i += 1
                match += extra
                if extra != 255:
                    break
        match += 4
        start = len(out) - offset
        if offset >= match:
            out += out[start:start + match]
        else:
            for k in range(match):                      # overlapping copy
                out.append(out[start + k])
    return bytes(out)


class _MsgPack:
    def __init__(self, data):
        self.d, self.p = data, 0

    def _take(self, n):
        value = self.d[self.p:self.p + n]
        self.p += n
        return value

    def read(self):
        b = self.d[self.p]
        self.p += 1
        if b <= 0x7f:
            return b
        if b >= 0xe0:
            return b - 256
        if b <= 0x8f:
            return self._map(b & 15)
        if b <= 0x9f:
            return [self.read() for _ in range(b & 15)]
        if b <= 0xbf:
            return self._take(b & 31).decode("utf-8", "replace")
        if b == 0xc0:
            return None
        if b in (0xc2, 0xc3):
            return b == 0xc3
        fixed = {0xcc: "B", 0xcd: ">H", 0xce: ">I", 0xcf: ">Q", 0xd0: "b", 0xd1: ">h", 0xd2: ">i", 0xd3: ">q",
                 0xca: ">f", 0xcb: ">d"}
        if b in fixed:
            return struct.unpack(fixed[b], self._take(struct.calcsize(fixed[b])))[0]
        if b in (0xd9, 0xda, 0xdb):
            n = int.from_bytes(self._take({0xd9: 1, 0xda: 2, 0xdb: 4}[b]), "big")
            return self._take(n).decode("utf-8", "replace")
        if b in (0xc4, 0xc5, 0xc6):
            n = int.from_bytes(self._take({0xc4: 1, 0xc5: 2, 0xc6: 4}[b]), "big")
            return self._take(n)
        if b in (0xdc, 0xdd):
            n = int.from_bytes(self._take(2 if b == 0xdc else 4), "big")
            return [self.read() for _ in range(n)]
        if b in (0xde, 0xdf):
            return self._map(int.from_bytes(self._take(2 if b == 0xde else 4), "big"))
        if 0xd4 <= b <= 0xd8:
            self._take(1)
            return self._take({0xd4: 1, 0xd5: 2, 0xd6: 4, 0xd7: 8, 0xd8: 16}[b])
        if b in (0xc7, 0xc8, 0xc9):
            n = int.from_bytes(self._take({0xc7: 1, 0xc8: 2, 0xc9: 4}[b]), "big")
            self._take(1)
            return self._take(n)
        raise ValueError(f"unexpected MessagePack byte 0x{b:02x}")

    def _map(self, n):
        out = {}
        for _ in range(n):
            key = self.read()
            out[tuple(key) if isinstance(key, list) else key] = self.read()
        return out


class RootTables:
    """Reads dbx_new tables from the root res.npk packs (Documents patch layer first)."""

    def __init__(self, game: Path, options):
        from core.npk.npk_file import NPKFile
        from core.npk.stage_export import murmur3_32
        self._hash = murmur3_32
        self.packs = []
        self._rows: dict[str, dict] = {}                # decoded tables, shared by every caller (read-only)
        for pack in (Path(game) / PATCH_FOLDER / ROOT_PACK, Path(game) / ROOT_PACK):
            if pack.is_file():
                npk = NPKFile(str(pack), options)
                self.packs.append((pack, npk, {index.file_signature: i for i, index in enumerate(npk.indices)}))

    def stamp(self) -> list:
        return [[str(pack), os.path.getmtime(pack), os.path.getsize(pack)] for pack, _n, _s in self.packs]

    def read(self, path: str) -> bytes | None:
        for pack, npk, signatures in self.packs:
            for name in (path, path.replace("\\", "/")):
                index = signatures.get(self._hash(name))
                if index is not None:
                    with open(pack, "rb") as stream:
                        npk.load_entry(index, stream)
                    return bytes(npk.entries.pop(index).data)
        return None

    def keyed_rows(self, table: str) -> dict:
        """
        Returns a table's rows by key. Each table is decoded once and shared, so don't modify the result. It was first
        made for global (item_text_os_en) so if it is missing, the Chinese server one will work (item_text_en).
        """
        if table not in self._rows:
            rows = self._decode_rows(table)
            if not rows and "_os_" in table:
                rows = self._decode_rows(table.replace("_os_", "_", 1))
            self._rows[table] = rows
        return self._rows[table]

    def _decode_rows(self, table: str) -> dict:
        header = self.read(f"dbx_new\\{table}.dbxhz")
        if header is None:
            return {}
        index = _MsgPack(zlib.decompress(header)).read()
        streams, out = {}, {}
        for key, entry in index.items():
            part = entry[2] if len(entry) > 2 else 0
            if part not in streams:
                data = self.read(f"dbx_new\\{table if part == 0 else f'{table}_{part}'}.dbxz")
                streams[part] = zlib.decompress(data) if data else None
            raw = streams[part]
            if raw is None:
                continue
            offset, length = entry[0], entry[1]
            size = struct.unpack_from("<I", raw, offset)[0]
            try:
                out[key] = _MsgPack(lz4_block(raw[offset + 4:offset + 4 + length], size)).read()
            except Exception:
                continue
        return out


# -- links ---------------------------------------------------------------------------------------
def build_links(tables: RootTables, read_game_file) -> dict[str, dict]:
    """{mesh path (lower case): {'icon', 'name', 'item'}} for character meshes that are wardrobe items."""
    english = {key: row.get("text_en", "") for key, row in tables.keyed_rows("item_text_os_en").items()
               if isinstance(row, dict)}
    items = tables.keyed_rows("item")
    dresses = tables.keyed_rows("role_dress")
    by_dress = items_by_dress(tables)
    meshes_of_gim: dict[str, str] = {}

    def mesh_for(gim):
        if gim not in meshes_of_gim:
            mesh = gim.rsplit(".", 1)[0] + ".mesh"
            data = read_game_file(gim)
            if data:
                try:
                    attribute = ET.fromstring(data).get("Mesh")
                    if attribute and attribute.lower() != "false":
                        mesh = attribute
                except ET.ParseError:
                    pass
            meshes_of_gim[gim] = mesh.replace("/", "\\").lower()
        return meshes_of_gim[gim]

    links: dict[str, dict] = {}
    for key, dress in dresses.items():
        if not isinstance(dress, dict) or not dress.get("model"):
            continue
        parts = dress["model"].replace("\\", "/").split("/")
        kind = parts[5].lower() if len(parts) > 5 else ""
        item = best_item(by_dress.get(dress.get("id", key)), kind, english)
        if item is None or is_generic_icon(item.get("icon_path")):
            continue
        name = english.get(item.get("name", ""), "")
        if name.strip().lower() in ("null!", "null"):
            name = ""
        mesh = mesh_for(dress["model"].replace("/", "\\"))
        link = {"icon": item["icon_path"], "name": name, "item": item.get("_id")}
        if mesh not in links or (name and not links[mesh]["name"]):
            links[mesh] = link                         # a named link beats an unnamed one for the same mesh

    # Basic hairstyles: color_hair maps each named style item (Spring Curls...) to its colour items, whose dress
    # rows hold the real model (female_hair_08). The style item's own dress row points at a placeholder
    # (female_hair_01_0N.gim for every female style), so these links replace whatever that gave.
    try:
        styles = tables.keyed_rows("color_hair")
    except Exception:
        styles = {}
    for style_id, style in styles.items():
        item = items.get(style_id)
        if not isinstance(style, dict) or not isinstance(item, dict) or is_generic_icon(item.get("icon_path")):
            continue
        name = english.get(item.get("name", ""), "")
        link = {"icon": item["icon_path"], "name": "" if name.strip().lower() in ("null!", "null") else name,
                "item": style_id}
        for colour_id in style.get("color_hair_items") or ():
            colour = items.get(colour_id)
            dress = dresses.get(colour.get("mini_type")) if isinstance(colour, dict) else None
            if isinstance(dress, dict) and dress.get("model"):
                links[mesh_for(dress["model"].replace("/", "\\"))] = link
    return links


def _cache_path() -> Path:
    try:
        from core.utils import get_application_path
        return Path(get_application_path()) / "item_links_cache.json"
    except Exception:
        return Path(os.getcwd()) / "item_links_cache.json"


def mesh_links(game, options, read_game_file) -> dict[str, dict]:
    """The mesh links for a game folder, from memory, the disk cache (if the packs are unchanged) or built."""
    game = Path(game)
    with _lock:
        if str(game) in _memory:
            return _memory[str(game)]
        tables = RootTables(game, options)
        stamp = tables.stamp()
        cache = _cache_path()
        try:
            saved = json.loads(cache.read_text(encoding="utf-8"))
            if saved.get("version") == CACHE_VERSION and saved.get("stamp") == stamp:
                _memory[str(game)] = saved["links"]
                return saved["links"]
        except FileNotFoundError:
            pass
        except Exception:
            get_logger().warning("Ignoring an unreadable item link cache %s", cache, exc_info=True)
        links = build_links(tables, read_game_file) if tables.packs else {}
        try:
            cache.write_text(json.dumps({"version": CACHE_VERSION, "stamp": stamp, "links": links}, ensure_ascii=False),
                             encoding="utf-8")
        except OSError:
            get_logger().warning("Could not save the item link cache %s", cache, exc_info=True)
        _memory[str(game)] = links
        return links


# Item rarity (the item table's rarity field, 1 to 5) and the name the Main Character Creator shows for each.
RARITY_NAMES = {1: "Basic", 2: "Rare", 3: "Epic", 4: "Legendary", 5: "Mythic"}


# The traits the game's Filter offers, in its order, with their English names.
TRAITS = (("yifu_qd", "Ceremony"), ("yifu_zc", "Premium"), ("yifu_jx", "Classic"), ("jiaju_dunhuang", "Dunhuang Museum"),
          ("yifu_ccgb", "Unrivalled"), ("yifu_syzp", "Prestige"))
_TRAIT_KEYS = tuple(key for key, _name in TRAITS)


# Text tables that hold the tag names (such as yifu_faxing being the "Hairstyle"), searched in this order.
TAG_TEXT_TABLES = ("item_text_os_en", "language_text_os_en", "language_text_old_os_en", "old_language_text_os_en",
                   "ui_text_old_os_en", "old_ui_text_os_en", "system_text_os_en", "ui_text_os_en")
_TAG_NAMES: dict = {}


def item_tags(tables, item_id) -> tuple:
    """The item's tags as the game lists them under its name (type first, then traits); () for none."""
    if item_id is None:
        return ()
    try:
        row = tables.keyed_rows("item").get(item_id)
    except Exception:
        return ()
    return tuple(str(t) for t in (row.get("item_tags") or ())) if isinstance(row, dict) else ()


def tag_names(tables) -> dict:
    """{tag key: English name} for every tag the items use (read once per tables object)."""
    key = id(tables)
    if key not in _TAG_NAMES:
        wanted = set()
        try:
            for row in tables.keyed_rows("item").values():
                if isinstance(row, dict):
                    wanted.update(str(t) for t in (row.get("item_tags") or ()))
        except Exception:
            pass
        names = {}
        for table in TAG_TEXT_TABLES:
            try:
                rows = tables.keyed_rows(table)
            except Exception:
                continue
            for tag in wanted - set(names):
                row = rows.get(tag)
                text = row.get("text_en") if isinstance(row, dict) else None
                if text:
                    names[tag] = text
        _TAG_NAMES.clear()
        _TAG_NAMES[key] = names
    return _TAG_NAMES[key]


def item_traits(tables, item_id) -> tuple:
    """Returns the item's filter traits in the order of TRAITS, or an empty tuple if it has none."""
    if item_id is None:
        return ()
    try:
        row = tables.keyed_rows("item").get(item_id)
    except Exception:
        return ()
    tags = set(row.get("item_tags") or ()) if isinstance(row, dict) else set()
    return tuple(key for key in _TRAIT_KEYS if key in tags)


def item_rarity(tables, item_id) -> int | None:
    """An item's rarity (1 Basic ... 5 Mythic), or None for no item or no rarity."""
    if item_id is None:
        return None
    try:
        row = tables.keyed_rows("item").get(item_id)
    except Exception:
        return None
    rarity = row.get("rarity") if isinstance(row, dict) else None
    return rarity if rarity in RARITY_NAMES else None


def link_for(links: dict, mesh_path: str) -> dict | None:
    """A mesh's link; hairstyle cuts (<hair>_a) fall back to their hairstyle."""
    key = mesh_path.replace("/", "\\").lower()
    if key in links:
        return links[key]
    match = re.match(r"^(.*\\)([^\\]+)_a\\([^\\]+)_a\.mesh$", key)
    if match:
        return links.get(f"{match.group(1)}{match.group(2)}\\{match.group(3)}.mesh")
    if key.endswith("_a.mesh"):
        return links.get(key[:-7] + ".mesh")
    return None
