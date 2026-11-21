"""
Names for the game's Wwise sounds in the file list.

wwise.npk holds ~22 000 sounds named only by their Wwise media id and ~3 500 sound banks with real names
(vo_emotion_ronweasley.bnk). Two things name them better:

- Opened on its own, wwise.npk names its entries by their hash (0x2245e0.wem): the murmur3 hash of the file's path
  inside the pack. The install's file list (resdb) has every such path, often with a telling folder
  (english\\vo_commonphrases_cassandra\\164289208.wem), so each hashed name gets its path back.
- A bank's HIRC Sound objects (type 2) name the media id they play, so a numbered sound can also be labelled with
  the bank that uses it (about 94% of them are, almost always by one bank).

The file list shows "0x2245e0.wem · english\\vo_commonphrases_cassandra\\164289208.wem" (plus the bank when the path
doesn't already say it), or "325694.wem · vo_emotion_ronweasley" for a numbered name, and its search matches all of
that. The entry's own name (what extracting and renaming use) is unchanged. The tables are built in the background
the first time such a name is shown and kept in scan_cache\\wwise_banks.json under a stamp of the wwise packs (path,
size, modification time): rebuilt only when they change.
"""

from __future__ import annotations

import json
import os
import re
import struct
import threading
import time
from pathlib import Path

CACHE_VERSION = 2                     # 2: the paths of hashed names
SEPARATOR = " · "
_NUMBERED = re.compile(r"(?:^|[\\/])(\d+)\.wem$", re.IGNORECASE)
_HASHED = re.compile(r"(?:^|[\\/])0x([0-9a-f]+)\.(?:wem|bnk)$", re.IGNORECASE)
_LABELS: dict[int, str] = {}          # media id -> the bank(s) that play it
_PATHS: dict[int, str] = {}           # entry hash -> its path inside the wwise packs
_STATE = {"started": False, "ready": False, "retry_at": 0.0}
RETRY_SECONDS = 10.0                  # with no game folder known yet, look again at most this often
_LOCK = threading.Lock()
_NOTIFY = []                          # callables run (on the Qt thread) once the tables are ready


# -- reading the banks -------------------------------------------------------------------------------------------
def _chunks(data: bytes) -> dict:
    out, pos = {}, 0
    while pos + 8 <= len(data):
        tag, size = data[pos:pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        out.setdefault(tag, data[pos + 8:pos + 8 + size])
        pos += 8 + size
    return out


def _sound_media(hirc: bytes, known: set) -> set:
    """The media ids (from known) the bank's Sound objects play."""
    found = set()
    if len(hirc) < 4:
        return found
    count, pos = struct.unpack_from("<I", hirc, 0)[0], 4
    for _ in range(count):
        if pos + 5 > len(hirc):
            break
        kind, size = hirc[pos], struct.unpack_from("<I", hirc, pos + 1)[0]
        if kind == 2:                                  # Sound: its source data (with the media id) comes first
            body = hirc[pos + 5:pos + 5 + size]
            for offset in range(4, min(len(body) - 3, 24)):
                value = struct.unpack_from("<I", body, offset)[0]
                if value in known:
                    found.add(value)
                    break
        pos += 5 + size
    return found


def label_text(banks) -> str:
    names = sorted(banks)
    return names[0] if len(names) == 1 else f"{names[0]} +{len(names) - 1}"


def _wwise_paths(index) -> list[str]:
    return [p for p in index.manifest() if p.lower().startswith("wwise\\")]


def build_labels(index) -> dict[int, str]:
    """{media id: bank label} from every wwise\\*.bnk of a PackIndex."""
    manifest = _wwise_paths(index)
    known = set()
    for path in manifest:
        match = _NUMBERED.search(path)
        if match:
            known.add(int(match.group(1)))
    users: dict[int, set] = {}
    for path in manifest:
        if not path.lower().endswith(".bnk"):
            continue
        try:
            data = index.read(path)
            data = data[0] if isinstance(data, tuple) else data
        except Exception:  # pylint: disable=broad-except
            continue
        if not data:
            continue
        bank = path.rsplit("\\", 1)[-1][:-4]
        for media in _sound_media(_chunks(data).get(b"HIRC", b""), known):
            users.setdefault(media, set()).add(bank)
    return {media: label_text(banks) for media, banks in users.items()}


def build_paths(index) -> dict[int, str]:
    """{entry hash: path inside the pack} for every wwise\\ file of the install (murmur3 of that path)."""
    from core.npk.asset_paths import murmur3_32  # pylint: disable=import-outside-toplevel
    return {murmur3_32(inner): inner for inner in (p.split("\\", 1)[1] for p in _wwise_paths(index))}


# -- cache -------------------------------------------------------------------------------------------------------
def _cache_file() -> Path:
    try:
        from core.utils import get_application_path  # pylint: disable=import-outside-toplevel
        return Path(get_application_path()) / "scan_cache" / "wwise_banks.json"
    except Exception:  # pylint: disable=broad-except
        return Path(os.getcwd()) / "scan_cache" / "wwise_banks.json"


def _stamp(index) -> list:
    out = []
    for pack in sorted(str(p) for p in index.packs.get("wwise", [])):
        try:
            stat = os.stat(pack)
            out.append([pack, stat.st_size, stat.st_mtime_ns])
        except OSError:
            pass
    return out


def load_or_build(index) -> tuple[dict[int, str], dict[int, str]]:
    """(media id -> bank label, entry hash -> path), from the cache when the wwise packs haven't changed."""
    stamp, path = _stamp(index), _cache_file()
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved.get("version") == CACHE_VERSION and saved.get("stamp") == stamp and stamp:
            return ({int(k): v for k, v in saved.get("labels", {}).items()},
                    {int(k): v for k, v in saved.get("paths", {}).items()})
    except (OSError, ValueError):
        pass
    labels, paths = build_labels(index), build_paths(index)
    if stamp and (labels or paths):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps({"version": CACHE_VERSION, "stamp": stamp, "labels": labels, "paths": paths},
                                       separators=(",", ":")), encoding="utf-8")
            os.replace(temp, path)
        except OSError:
            pass
    return labels, paths


def _build_in_background():
    try:
        from gui.widgets.character_anim_export import _game_folder, _index  # pylint: disable=import-outside-toplevel
        game = _game_folder()
        if game is None:
            with _LOCK:
                _STATE["started"] = False          # try again once a game pack is open (not on every row drawn)
                _STATE["retry_at"] = time.monotonic() + RETRY_SECONDS
            return
        labels, paths = load_or_build(_index(game))
    except Exception:  # pylint: disable=broad-except
        from core.logger import get_logger  # pylint: disable=import-outside-toplevel
        get_logger().exception("Could not read the sound banks for sound names")
        return
    with _LOCK:
        _LABELS.update(labels)
        _PATHS.update(paths)
        _STATE["ready"] = True
    for notify in list(_NOTIFY):
        try:
            notify()
        except Exception:  # pylint: disable=broad-except
            pass


def label_for(filename: str):
    """
    What a sound file's name doesn't say: for a hashed name its path (and bank), for a numbered .wem its bank;
    None for other names or while the tables are being read.
    """
    text = str(filename or "")
    hashed, numbered = _HASHED.search(text), _NUMBERED.search(text)
    if hashed is None and numbered is None:
        return None
    if not _STATE["ready"]:
        with _LOCK:
            if not _STATE["started"] and time.monotonic() >= _STATE["retry_at"]:
                _STATE["started"] = True
                threading.Thread(target=_build_in_background, daemon=True).start()
        return None
    if hashed is not None:
        inner = _PATHS.get(int(hashed.group(1), 16))
        if inner is None:
            return None
        media = _NUMBERED.search(inner)
        bank = _LABELS.get(int(media.group(1))) if media else None
        folder = inner.rsplit("\\", 1)[0].lower() if "\\" in inner else ""
        if bank and bank.split(" +")[0].lower() not in folder:
            return f"{inner}{SEPARATOR}{bank}"
        return inner
    return _LABELS.get(int(numbered.group(1)))


def labelled(filename):
    label = label_for(filename)
    return f"{filename}{SEPARATOR}{label}" if label else filename


# -- the file list -----------------------------------------------------------------------------------------------
def install(app=None) -> bool:
    """Name sound rows in the file list (display and search only); False if already done."""
    from PySide6 import QtCore, QtWidgets  # pylint: disable=import-outside-toplevel
    from gui.models import npk_file_model  # pylint: disable=import-outside-toplevel
    from gui import npk_entry_filter  # pylint: disable=import-outside-toplevel
    model_cls, filter_cls = npk_file_model.NPKFileModel, npk_entry_filter.NPKEntryFilter
    if getattr(model_cls.data, "_wwise_names", False):
        return False
    original_data, original_name, original_filter = model_cls.data, model_cls.get_filename, filter_cls.apply_filter
    display = QtCore.Qt.ItemDataRole.DisplayRole

    def data(self, index, role=display):
        value = original_data(self, index, role)
        if role == display and isinstance(value, str) and value.lower().endswith((".wem", ".bnk")):
            return labelled(value)
        return value

    def get_filename(self, index):
        name = original_name(self, index)
        return labelled(name) if getattr(self, "_search_labels", False) and isinstance(name, str) else name

    def apply_filter(self, *args, **kwargs):
        model = self._list_view.model() if getattr(self, "_list_view", None) is not None else None
        if model is not None:
            model._search_labels = True                # the search also matches paths and bank names
        try:
            return original_filter(self, *args, **kwargs)
        finally:
            if model is not None:
                model._search_labels = False

    data._wwise_names = True
    model_cls.data, model_cls.get_filename, filter_cls.apply_filter = data, get_filename, apply_filter

    class _Relay(QtCore.QObject):                      # carries "tables ready" from the reading thread to Qt's
        ready = QtCore.Signal()

    relay = _Relay()
    relay.setParent(app or QtWidgets.QApplication.instance())

    def repaint():
        from gui.widgets.npk_file_list import NPKFileList  # pylint: disable=import-outside-toplevel
        for window in QtWidgets.QApplication.topLevelWidgets():
            for view in window.findChildren(NPKFileList):
                view.viewport().update()

    relay.ready.connect(repaint)
    _NOTIFY.append(relay.ready.emit)
    return True
