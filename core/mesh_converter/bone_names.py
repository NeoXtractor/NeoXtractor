"""MMD bone names for HPMA skeletons, used when writing PMX.

A pmx_bone_names.xlsx, .csv or .txt next to NeoXtractor adds to the built-in table or overrides it: first
column the HPMA bone, second the PMX name (csv/txt: comma or tab separated, # for comments).
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
import zipfile

SETTING = "pmx_export.translate_bones"
LABEL = "Translate bones to MMD names on PMX export"
FILE_STEM = "pmx_bone_names"

DEFAULT_TABLE = {
    "e_lbone_000": "左目", "e_rbone_000": "右目",
    "bip001_head": "頭", "bip001_neck": "首",
    "bip001_spine1": "上半身2", "bip001_spine": "上半身", "waist": "下半身", "lower": "下半身",
    "bip001_pelvis": "下半身",            # NPCs with a full biped (bip001 → pelvis → thighs / spine)
    "bip001_l_thigh": "左足D", "bip001_l_calf": "左ひざD", "bip001_l_foot": "左足首D", "bip001_l_toe0": "左足先EX",
    "bip001_r_thigh": "右足D", "bip001_r_calf": "右ひざD", "bip001_r_foot": "右足首D", "bip001_r_toe0": "右足先EX",
    "bip001_l_clavicle": "左肩", "bip001_l_upperarm": "左腕", "bip001_l_forearm": "左ひじ", "bip001_l_hand": "左手首",
    "bip001_r_clavicle": "右肩", "bip001_r_upperarm": "右腕", "bip001_r_forearm": "右ひじ", "bip001_r_hand": "右手首",
    "bip001_l_finger0": "左親指０", "bip001_l_finger01": "左親指１",
    "bip001_l_finger1": "左人指１", "bip001_l_finger11": "左人指２",
    "bip001_l_finger2": "左中指１", "bip001_l_finger21": "左中指２",
    "bip001_l_finger3": "左薬指１", "bip001_l_finger31": "左薬指２",
    "bip001_l_finger4": "左小指１", "bip001_l_finger41": "左小指２",
    "bip001_l_finger02": "左親指２", "bip001_l_finger12": "左人指３", "bip001_l_finger22": "左中指３",
    "bip001_l_finger32": "左薬指３", "bip001_l_finger42": "左小指３",
    "bip001_r_finger0": "右親指０", "bip001_r_finger01": "右親指１",
    "bip001_r_finger1": "右人指１", "bip001_r_finger11": "右人指２",
    "bip001_r_finger2": "右中指１", "bip001_r_finger21": "右中指２",
    "bip001_r_finger3": "右薬指１", "bip001_r_finger31": "右薬指２",
    "bip001_r_finger4": "右小指１", "bip001_r_finger41": "右小指２",
    "bip001_r_finger02": "右親指２", "bip001_r_finger12": "右人指３", "bip001_r_finger22": "右中指３",
    "bip001_r_finger32": "右薬指３", "bip001_r_finger42": "右小指３",
}

_cache: dict = {"key": None, "table": None}


def _app_folder() -> str:
    try:
        from core.utils import get_application_path
        return str(get_application_path())
    except Exception:
        return os.getcwd()


def _read_xlsx(path: str) -> list[tuple[str, str]]:
    """First two columns of the first sheet (enough for a two-column name list)."""
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path) as book:
        shared = []
        if "xl/sharedStrings.xml" in book.namelist():
            for item in ET.fromstring(book.read("xl/sharedStrings.xml")).iter(ns + "si"):
                shared.append("".join(t.text or "" for t in item.iter(ns + "t")))
        sheets = sorted(n for n in book.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
        rows = []
        for row in ET.fromstring(book.read(sheets[0])).iter(ns + "row"):
            cells = {}
            for cell in row.iter(ns + "c"):
                column = re.match(r"[A-Z]+", cell.get("r", "")).group()
                value = cell.find(ns + "v")
                if cell.get("t") == "s" and value is not None:
                    cells[column] = shared[int(value.text)]
                elif cell.get("t") == "inlineStr":
                    cells[column] = "".join(t.text or "" for t in cell.iter(ns + "t"))
                elif value is not None:
                    cells[column] = value.text or ""
            rows.append((cells.get("A", ""), cells.get("B", "")))
        return rows


def _read_text(path: str) -> list[tuple[str, str]]:
    rows = []
    with open(path, encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.split("#", 1)[0].strip()
            if line:
                parts = re.split(r"\t|,", line, maxsplit=1)
                rows.append((parts[0], parts[1] if len(parts) > 1 else ""))
    return rows


def user_file() -> str | None:
    folder = _app_folder()
    for extension in (".xlsx", ".csv", ".txt"):
        path = os.path.join(folder, FILE_STEM + extension)
        if os.path.isfile(path):
            return path
    return None


def table() -> dict[str, str]:
    """The built-in table plus the user's file, keyed by lower-case HPMA bone name."""
    path = user_file()
    key = (path, os.path.getmtime(path) if path else None)
    if _cache["key"] != key:
        result = {source.lower(): target for source, target in DEFAULT_TABLE.items()}
        if path:
            try:
                rows = _read_xlsx(path) if path.lower().endswith(".xlsx") else _read_text(path)
                for source, target in rows:
                    source, target = source.strip(), target.strip()
                    if source and target:
                        result[source.lower()] = target
            except Exception:
                from core.logger import get_logger
                get_logger().exception("Could not read %s; using the built-in bone names", path)
        _cache["key"], _cache["table"] = key, result
    return _cache["table"]


def translate(names: list[str]) -> list[str]:
    """
    MMD names for a skeleton. Each PMX name is used once: when two HPMA bones map to the same
    name (waist and lower are both 下半身), the first keeps it and the other keeps its own name.
    """
    mapping = table()
    used = set()
    result = []
    for name in names:
        target = mapping.get(str(name).lower())
        if target and target not in used and target not in names:
            used.add(target)
            result.append(target)
        else:
            result.append(name)
    return result


def enabled(settings=None) -> bool:
    if settings is None:
        try:
            from PySide6 import QtWidgets
            app = QtWidgets.QApplication.instance()
            settings = app.property("settings_manager") if app is not None else None
        except Exception:
            settings = None
    if settings is None:
        return True
    try:
        return bool(settings.get(SETTING, True))
    except Exception:
        return True
