"""Character exports: the background export job, the export options, and the Characters tab's export commands."""

from __future__ import annotations

import itertools
import ntpath
import os
import re
import threading
import numpy as np
from PySide6 import QtCore, QtWidgets
from core.logger import get_logger
from core.mesh_loader.loader import MeshLoader
from gui.utils import scan_memory


def read_entry_data(archive, index: int) -> bytes:
    """Entry bytes from the open archive, loading the entry from disk if it is not cached yet."""
    entry = None
    try:
        entry = archive.find_entry_by_id(index)
    except (KeyError, IndexError):
        entry = None
    if (entry is None or not getattr(entry, "data", None)) and hasattr(archive, "load_entry") \
            and getattr(archive, "file_path", None) and not hasattr(archive, "records"):
        with open(archive.file_path, "rb") as stream:
            archive.load_entry(index, stream)
        entry = archive.entries[index]
    if entry is None or not getattr(entry, "data", None):
        raise ValueError("entry has no data")
    return bytes(entry.data)


def selected_mesh_specs(main_window) -> list[tuple[str, int, str | None]]:
    """Meshes selected in the main window: the Characters tree when it is showing, else the file list."""
    app = QtWidgets.QApplication.instance()
    archive = app.property("npk_file") if app is not None else None
    if main_window is None or archive is None:
        return []
    specs: list[tuple[str, int, str | None]] = []
    page = main_window.left_content.currentWidget() if hasattr(main_window, "left_content") else None
    characters = getattr(main_window, "_character_folders_window", None)
    if characters is not None and page is characters:
        # entry_items covers whole (lazily filled) folders: {entry index: game path}
        found = characters.entry_items(characters.tree.selectedItems(), True) or {}
        for index, path in sorted(found.items(), key=lambda item: str(item[1])):
            if index is not None and str(path or "").lower().endswith(".mesh"):
                specs.append((str(path).rsplit("\\", 1)[-1], int(index), str(path)))
    else:
        view = getattr(main_window, "list_widget", None)
        rows = sorted({index.row() for index in view.selectionModel().selectedIndexes()}) if view is not None else []
        for row in rows:
            entry = archive.peek_entry(row) if hasattr(archive, "peek_entry") else archive.entries.get(row)
            extension = (getattr(entry, "extension", "") or "").lower() if entry is not None else ""
            name = getattr(entry, "filename", "") or f"entry {row}"
            if extension == "mesh" or name.lower().endswith(".mesh"):
                path = name if "\\" in name and not name.startswith("0x") else None
                specs.append((name.rsplit("\\", 1)[-1], row, path))
    seen, unique = set(), []
    for spec in specs:
        if spec[1] not in seen:
            seen.add(spec[1])
            unique.append(spec)
    return unique


SETTING_EXPORT_FOLDER = "model_export.folder"


class _ExportSignals(QtCore.QObject):
    progress = QtCore.Signal(str, int, int)
    finished = QtCore.Signal(str, str, str)     # written path, report, error


# Qt objects of running exports, owned here so a worker thread never drops the
# last reference to one (destroying Qt wrappers off the GUI thread can deadlock)
_EXPORT_JOBS: dict[int, dict] = {}


_EXPORT_IDS = itertools.count(1)


def _emit(job_id: int, signal: str, *args) -> None:
    job = _EXPORT_JOBS.get(job_id)
    if job is not None:
        getattr(job["signals"], signal).emit(*args)


def start_export(parent, kind: str, default_name: str, build) -> None:
    """
    Ask for a target file, then run build(progress, cancelled, report) -> items and
    write one .glb or .pmx in the background with a progress dialog.
    """
    from core.mesh_converter import model_export  # pylint: disable=import-outside-toplevel
    app = QtWidgets.QApplication.instance()
    settings = app.property("settings_manager") if app is not None else None
    folder = ""
    try:
        folder = settings.get(SETTING_EXPORT_FOLDER, "") if settings is not None else ""
    except Exception:  # pylint: disable=broad-except
        folder = ""
    name = re.sub(r'[<>:"/\\|?*]+', "_", default_name).strip() or "export"
    each = kind.startswith("each:")                   # one folder per mesh, written as it loads
    title = f"Export as {kind[5:].upper()}" if each else f"Export as {kind.upper()}"
    if kind == "vmd" or each:                         # one folder per object: <name>.pmx, textures, <name>.vmd
        caption = "Export animations (PMX + VMD) to" if kind == "vmd" else f"{title}: choose the output folder"
        target = QtWidgets.QFileDialog.getExistingDirectory(parent, caption, folder or "")
        if not target:
            return
        if settings is not None:
            settings.set(SETTING_EXPORT_FOLDER, target, True)
    else:
        label = "glTF Binary (*.glb)" if kind == "glb" else "Polygon Model eXtended (*.pmx)"
        target, _ = QtWidgets.QFileDialog.getSaveFileName(parent, f"Export as {kind.upper()}",
                                                          os.path.join(folder or "", f"{name}.{kind}"), label)
        if not target:
            return
        if not target.lower().endswith(f".{kind}"):
            target += f".{kind}"
        if settings is not None:
            settings.set(SETTING_EXPORT_FOLDER, os.path.dirname(target), True)

    dialog = QtWidgets.QProgressDialog(f"Exporting {os.path.basename(target)}…", "Cancel", 0, 0, parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    cancel = threading.Event()
    signals = _ExportSignals()
    job_id = next(_EXPORT_IDS)
    job = {"signals": signals, "dialog": dialog, "thread": None, "cancel": cancel}
    _EXPORT_JOBS[job_id] = job
    # A plain function, not cancel.set: PySide only weakly tracks a bound method's owner and
    # disconnects when it dies, which would then happen on the worker thread and deadlock
    dialog.canceled.connect(lambda: _EXPORT_JOBS[job_id]["cancel"].set() if job_id in _EXPORT_JOBS else None)

    def progress(text, value, total):
        dialog.setLabelText(text)
        dialog.setRange(0, max(total, 1))
        dialog.setValue(value)

    def release():
        # Runs on the GUI thread once the worker has fully exited
        thread = job.get("thread")
        if thread is not None and thread.is_alive():
            QtCore.QTimer.singleShot(50, release)
            return
        _EXPORT_JOBS.pop(job_id, None)
        dialog.deleteLater()

    def finished(path, report, error):
        dialog.hide()
        scan_memory.end()
        QtCore.QTimer.singleShot(0, release)
        # Notes about the export (body size used, parts without a texture, ...) go to the log, not the dialog
        for line in report.splitlines() if report else ():
            get_logger().info("%s: %s", title, line)
        if error:
            get_logger().warning("%s: %s", title, error)
            QtWidgets.QMessageBox.warning(parent, title, error)
            return
        QtWidgets.QMessageBox.information(parent, title, f"Saved {path}")

    signals.progress.connect(progress)
    signals.finished.connect(finished)

    def work():
        # Only plain Python objects here: Qt objects are reached through _EXPORT_JOBS
        report: list[str] = []
        try:
            if each:                                  # the builder writes every file itself
                written = build(lambda text, value, total: _emit(job_id, "progress", text, value, total),
                                cancel.is_set, report, target)
                _emit(job_id, "finished", str(written), "\n".join(dict.fromkeys(report))[:3000], "")
                return
            items = build(lambda text, value, total: _emit(job_id, "progress", text, value, total), cancel.is_set, report)
            if not items:
                raise ValueError("No meshes to export.")
            _emit(job_id, "progress", f"Writing {os.path.basename(target)}…", len(items), len(items))
            if kind == "glb":
                written = model_export.write_glb(items, target)
            elif kind == "vmd":
                from core.mesh_converter import vmd_export  # pylint: disable=import-outside-toplevel
                pairs = []
                for number, item in enumerate(items):
                    if cancel.is_set():
                        raise InterruptedError
                    _emit(job_id, "progress", f"Writing {item['name']} (PMX + VMD)…", number, len(items))
                    pairs.append(vmd_export.write_pmx_vmd(item, os.path.join(target, re.sub(r'[<>:"/\\|?*\s]+', "_", item["name"]))))
                    clip = item["animation"]
                    if np.abs(clip.scales - 1.0).max() > 1e-3:
                        report.append(f"{item['name']}: its loop also scales bones, which VMD cannot store; "
                                      "that part (e.g. a pulsing shape) is left out")
                written = f"{len(pairs)} model(s) with motion in {target}"
            else:
                written = model_export.write_pmx(items, target)
            untextured = [f"{item['name']}: {part.get('material')}" for item in items
                          for part in item["parts"] if part.get("image") is None]
            notes = sorted(set(report))
            if untextured:
                notes.append(f"{len(untextured)} part(s) without a texture: " + ", ".join(untextured[:6])
                             + (" …" if len(untextured) > 6 else ""))
            _emit(job_id, "finished", str(written), "\n".join(notes)[:3000], "")
        except InterruptedError:
            _emit(job_id, "finished", "", "", "Export cancelled.")
        except Exception as exc:  # pylint: disable=broad-except
            _emit(job_id, "finished", "", "", f"Export failed: {exc}")

    dialog.show()
    scan_memory.begin()
    job["thread"] = threading.Thread(target=work, daemon=True)
    job["thread"].start()


BODY_SIZE_SETTING = "model_export.body_size"



def body_size_adult() -> bool:
    """Character exports and the Scene Viewer use the adult body (the outfit's plan1 skeleton deform)."""
    app = QtWidgets.QApplication.instance()
    settings = app.property("settings_manager") if app is not None else None
    try:
        return settings is not None and settings.get(BODY_SIZE_SETTING, "original") == "adult"
    except Exception:  # pylint: disable=broad-except
        return False


def is_character_path(path) -> bool:
    return bool(path) and str(path).lower().startswith("model\\character\\appearance\\")


def with_body_size(index, items, mesh_paths, report):
    """items in the chosen body size (unchanged for Original, or when nothing here is a character part)."""
    if index is None or not body_size_adult() or not any(is_character_path(p) for p in mesh_paths):
        return items
    from core.mesh_converter import body_size  # pylint: disable=import-outside-toplevel
    try:
        return body_size.adult_items(lambda p: (index.read(p) or (None,))[0], items, mesh_paths, report)
    except Exception as exc:  # pylint: disable=broad-except
        report.append(f"Adult size could not be applied: {exc}")
        return items


def outfit_body_items(loader, mesh_path: str, exclude=()) -> list[dict]:
    """
    The skin an outfit is worn with, as export items. Each outfit folder holds <outfit>_body.gim,
    which points at a mesh in only_body (body, hands, neck) and skins it with the outfit's own
    <outfit>_body.mtg, so the piece comes out with the right texture.
    """
    import xml.etree.ElementTree as ET  # pylint: disable=import-outside-toplevel
    if loader is None or "\\clothes\\" not in mesh_path.lower():
        return []
    gim_path = mesh_path.rsplit(".", 1)[0] + "_body.gim"
    found = loader.index.read(gim_path)
    if found is None:
        return []
    try:
        target = (ET.fromstring(found[0]).get("Mesh") or "").strip()
    except ET.ParseError:
        return []
    if not target or target.lower() in {path.lower() for path in exclude}:
        return []
    model = loader.model(gim_path)
    if model is None:
        return []
    return [{"name": target.rsplit("\\", 1)[-1].rsplit(".", 1)[0], "mesh": model[0], "parts": model[1]}]


def export_character_selection(main_window, kind: str) -> None:
    """Export the meshes selected in the Characters tree (whole folders included) as one file."""
    characters = getattr(main_window, "_character_folders_window", None)
    app = QtWidgets.QApplication.instance()
    archive = app.property("npk_file") if app is not None else None
    if characters is None or archive is None:
        return
    selected = characters.tree.selectedItems()
    found = characters.entry_items(selected, True) or {}
    meshes = sorted(((index, str(path)) for index, path in found.items() if str(path or "").lower().endswith(".mesh")),
                    key=lambda item: item[1])
    if not meshes:
        QtWidgets.QMessageBox.information(main_window, "Export", "The selection has no meshes.")
        return
    if len(meshes) == 1:
        default = meshes[0][1].rsplit("\\", 1)[-1].rsplit(".", 1)[0]
    else:
        # The deepest folder all meshes share, compared by whole folder names: commonprefix on the
        # text cut names in half (male_hair_01, male_hair_02 -> "male_hair_")
        folders = [path.lower().split("\\")[:-1] for _index, path in meshes]
        depth = 0
        while all(len(f) > depth and f[depth] == folders[0][depth] for f in folders):
            depth += 1
        common = meshes[0][1].split("\\")[:depth]
        default = common[-1] if common else (selected[0].text(0) if selected else "export")

    def build(progress, cancelled, report):
        from core.npk.stage_export import PackIndex, StageLoader, game_root_for, read_options_for  # pylint: disable=import-outside-toplevel
        game = game_root_for(archive.file_path) if getattr(archive, "file_path", None) else None
        loader = StageLoader(PackIndex(game, read_options_for(archive)), max_texture_size=8192) if game else None
        items, mesh_loader = [], MeshLoader()
        for number, (index, path) in enumerate(meshes):
            if cancelled():
                raise InterruptedError
            name = path.rsplit("\\", 1)[-1].rsplit(".", 1)[0]
            progress(f"Loading {name}…", number, len(meshes))
            model = textured_model(loader, path)
            if model is None:
                try:
                    mesh = mesh_loader.load_from_bytes(read_entry_data(archive, index))
                except Exception as exc:  # pylint: disable=broad-except
                    report.append(f"{name}: {exc}")
                    continue
                if mesh is None or not mesh.position:
                    report.append(f"{name}: not a readable mesh")
                    continue
                parts = loader.parts_for(path.rsplit(".", 1)[0] + ".gim", mesh) if loader is not None else []
                model = (mesh, parts)
            items.append({"name": name, "mesh": model[0], "parts": model[1]})
            for body in outfit_body_items(loader, path, [mesh_path for _entry, mesh_path in meshes]):
                if all(body["name"] != other["name"] for other in items):     # hands/neck shared by outfits: once
                    items.append(body)
        if loader is not None:
            report.extend(loader.report)
            items = with_body_size(loader.index, items, [path for _index, path in meshes], report)
        return items

    start_export(main_window, kind, default, build)


EACH_FORMATS = (("GLB", "glb"), ("PMX", "pmx"), ("OBJ", "obj"))


def textured_model(loader, path, metadata_files=()):
    """
    loader.model(path), but when none of its parts has a texture, the first variant .gim that
    points at the same mesh and does: faces have no material of their own, only skin-tone variants
    (male_body_03_01.gim to _08.gim, _all.gim) that reuse the one mesh.
    """
    before = len(loader.report) if loader is not None else 0
    model = loader.model(path) if loader is not None else None
    if loader is None or (model is not None and any(part.get("image") is not None for part in model[1])):
        return model
    stem = path.rsplit(".", 1)[0]
    name = stem.rsplit("\\", 1)[-1].lower()
    candidates = [f"{stem}_01.gim", f"{stem}_all.gim"] + sorted(
        m for m in metadata_files if m.lower().endswith(".gim") and m.rsplit("\\", 1)[-1].lower().startswith(name + "_"))
    for candidate in dict.fromkeys(candidates):
        if loader.index.locate(candidate) is None:
            continue
        variant = loader.model(candidate)
        if variant is not None and any(part.get("image") is not None for part in variant[1]) \
                and model is not None and len(variant[0].position) == len(model[0].position):
            del loader.report[before:]                    # the "missing material" note no longer applies
            loader.report.append(f"{name}: material taken from {ntpath.basename(candidate)} (it has none of its own)")
            return variant
    return model


ITEM_INFO_SETTING = "model_export.item_info"


ITEM_INFO_LABEL = "Add item names and icons to exports"


def include_item_info() -> bool:
    app = QtWidgets.QApplication.instance()
    settings = app.property("settings_manager") if app is not None else None
    try:
        return bool(settings.get(ITEM_INFO_SETTING, True)) if settings is not None else True
    except Exception:  # pylint: disable=broad-except
        return True


def _display_name(name: str) -> str:
    """An English item name made safe for a folder name (spaces kept)."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", name)
    return re.sub(r"\s+", " ", name).strip(" .")[:60]


def save_item_icon(index, link, target) -> bool:
    """Save the item's icon (game path like image/clothes/x.png) as a PNG at target."""
    from core.npk.stage_export import decode_texture  # pylint: disable=import-outside-toplevel
    found = index.read(link["icon"].replace("/", "\\"))
    if found is None:
        return False
    try:
        decode_texture(found[0]).save(target)
        return True
    except Exception:  # pylint: disable=broad-except
        return False


IMAGE_REFERENCE = re.compile(r'[\w\\/.\-]+\.(?:tga|png|dds|jpg|jpeg|bmp)', re.I)


def save_metadata_textures(index, metadata_paths, used_textures, folder) -> int:
    """
    Save every texture the metadata files name (skin tone variants, makeup masks, AO / mask maps)
    that the model itself does not use, as PNGs in folder. Returns how many were written.
    """
    from core.npk.stage_export import decode_texture  # pylint: disable=import-outside-toplevel
    used = {str(path).lower().rsplit(".", 1)[0] for path in used_textures if path}
    wanted = {}
    for path in metadata_paths:
        found = index.read(path)
        if found is None:
            continue
        for reference in IMAGE_REFERENCE.findall(found[0].decode("utf-8", "replace")):
            reference = reference.replace("\\\\", "\\").replace("/", "\\")
            if reference.lower().rsplit(".", 1)[0] not in used:
                wanted.setdefault(reference.lower(), reference)
    written, names = 0, set()
    for reference in wanted.values():
        found = index.read(reference)
        if found is None:
            continue
        try:
            image = decode_texture(found[0])
        except Exception:  # pylint: disable=broad-except
            continue
        name = _safe_folder(reference.rsplit("\\", 1)[-1].rsplit(".", 1)[0])
        while name.lower() in names:
            name += "_"
        names.add(name.lower())
        os.makedirs(folder, exist_ok=True)
        image.save(os.path.join(folder, f"{name}.png"))
        written += 1
    return written


def export_character_meshes_each(main_window, fmt: str, per_folder: bool = False, faces_only: bool = False,
                                 hats_only: bool = False, face_shapes: bool = False) -> None:
    """
    Export the meshes under the Characters selection, loaded, written and released a file at a time:
      per mesh:   <output>/<selected folder>/<mesh>/<mesh>.<fmt>
      per folder: <output>/<selected folder>/<folder>/<folder>.<fmt>, every mesh of that folder in one
                  model with its own materials (a face folder: face, eyes, eyebrows, lashes)
    Next to each model, <name>_textures/other holds the textures its metadata names but the model
    does not use (skin tones, makeup masks, AO / mask maps).
    faces_only: just the face folders (.../body/<face>/), each as one model: face, eyeballs, lashes, eyebrows.
    hats_only: just the hats (.../hat/...), without each hat's mask/ folder of hairstyles trimmed to fit under it.
    face_shapes: faces_only, plus one more model per face shape the game has for that face
                 (<face>_NN.mb): <face>_shape_NN.<fmt>, the face, eyes and eyebrows reshaped.
    """
    faces_only = faces_only or face_shapes
    from gui.windows.character_folders_window import FOLDER_PATH_ROLE  # pylint: disable=import-outside-toplevel
    characters = getattr(main_window, "_character_folders_window", None)
    app = QtWidgets.QApplication.instance()
    archive = app.property("npk_file") if app is not None else None
    if characters is None or archive is None:
        return
    selected = characters.tree.selectedItems()
    found = characters.entry_items(selected, True) or {}
    meshes = sorted(((index, str(path)) for index, path in found.items() if str(path or "").lower().endswith(".mesh")),
                    key=lambda item: item[1])
    if faces_only:
        # A face folder sits directly in a "body" folder (only_body and other folders are not faces)
        per_folder = True
        meshes = [(index, path) for index, path in meshes if path.lower().split("\\")[-3:-2] == ["body"]]
        # not the older face copy with built-in eyeballs (<face>_all_jr) next to the face itself
        from gui.widgets.face_makeup_data import without_old_eye_faces  # pylint: disable=import-outside-toplevel
        kept = set(without_old_eye_faces([path for _index, path in meshes]))
        meshes = [(index, path) for index, path in meshes if path in kept]
        if not meshes:
            QtWidgets.QMessageBox.information(main_window, "Export faces",
                                              "The selection has no face folders (folders inside a \"body\" folder).")
            return
    if hats_only:
        meshes = [(index, path) for index, path in meshes
                  if "hat" in path.lower().split("\\")[:-1] and "mask" not in path.lower().split("\\")[:-1]]
        if not meshes:
            QtWidgets.QMessageBox.information(main_window, "Export hats", "The selection has no hats (meshes in a \"hat\" folder).")
            return
    if not meshes:
        QtWidgets.QMessageBox.information(main_window, "Export", "The selection has no meshes.")
        return
    # Each model goes under the selected folder it belongs to (deepest match), named like that folder
    roots = sorted({str(item.data(0, FOLDER_PATH_ROLE)) for item in selected if item.data(0, FOLDER_PATH_ROLE)},
                   key=len, reverse=True)

    def folders_for(folder):
        """
        The game's folders from the selected folder down to `folder`, so the output mirrors them:
        hat/male_hat_100_ng/mask/hair, not a flat hat/ where same-named hair masks of different
        hats collide.
        """
        for root in roots:
            if folder.lower() == root.lower() or folder.lower().startswith(root.lower() + "\\"):
                return [root.rsplit("\\", 1)[-1]] + [part for part in folder[len(root):].split("\\") if part]
        return [folder.rsplit("\\", 1)[-1] or "meshes"]

    # Metadata files (.gim / .mtg / .mvar) of every folder involved, from the tab's catalog
    catalog = getattr(characters, "catalog", None)
    metadata: dict[str, list[str]] = {}
    for _entry, path in meshes:
        folder = path.rsplit("\\", 1)[0]
        if folder in metadata or catalog is None:
            continue
        keys = catalog.folders.get(folder, ())
        metadata[folder] = [str(catalog.assets[key].path) for key in keys
                            if key in catalog.assets and str(catalog.assets[key].path).lower().endswith((".gim", ".mtg", ".mvar"))]

    # Jobs: (output folder parts, file name, [(entry, path)], metadata files)
    jobs = []
    if per_folder:
        grouped: dict[str, list] = {}
        for entry, path in meshes:
            grouped.setdefault(path.rsplit("\\", 1)[0], []).append((entry, path))
        for folder, members in grouped.items():
            jobs.append((folders_for(folder), folder.rsplit("\\", 1)[-1], members, metadata.get(folder, [])))
    else:
        for entry, path in meshes:
            stem = path.rsplit("\\", 1)[-1].rsplit(".", 1)[0]
            folder = path.rsplit("\\", 1)[0]
            parts = folders_for(folder)
            if parts[-1].lower() != stem.lower():         # male_hat_100_ng/male_hat_100_ng.mesh: no extra level
                parts.append(stem)
            own = [m for m in metadata.get(folder, []) if m.rsplit("\\", 1)[-1].lower().startswith(stem.lower())]
            jobs.append((parts, stem, [(entry, path)], own))
    selected_paths = [path for _entry, path in meshes]
    unit = "folders" if per_folder else "meshes"

    def build(progress, cancelled, report, target):
        from core.mesh_converter import model_export  # pylint: disable=import-outside-toplevel
        from core.npk.stage_export import PackIndex, StageLoader, game_root_for, read_options_for  # pylint: disable=import-outside-toplevel
        game = game_root_for(archive.file_path) if getattr(archive, "file_path", None) else None
        options = read_options_for(archive)
        index = PackIndex(game, options) if game else None
        base_writer = {"glb": model_export.write_glb, "pmx": model_export.write_pmx, "obj": model_export.write_obj}[fmt]
        job_paths: list[str] = []

        def writer(items_to_write, target_path):
            return base_writer(with_body_size(index, items_to_write, job_paths, report), target_path)

        mesh_loader, used, done, untextured, extras = MeshLoader(), set(), 0, 0, 0
        links, named, icons = {}, 0, 0
        shape_files: dict[str, list[str]] = {}
        shapes_written = 0
        if face_shapes and index is not None:
            progress("Looking for face shapes…", 0, 0)
            for path in index.manifest():
                if path.lower().endswith(".mb") and "\\body\\" in path.lower():
                    shape_files.setdefault(path.rsplit("\\", 1)[0].lower(), []).append(path)
        if index is not None and include_item_info():
            from core.npk import game_tables  # pylint: disable=import-outside-toplevel
            progress("Reading the game's item tables (names and icons)…", 0, 0)
            try:
                links = game_tables.mesh_links(game, options,
                                               lambda path: (index.read(path) or (None,))[0])
            except Exception as exc:  # pylint: disable=broad-except
                report.append(f"Item names and icons unavailable: {exc}")
        for number, (folder_parts, name, members, metadata_files) in enumerate(jobs):
            if cancelled():
                raise InterruptedError
            progress(f"Exporting {name}  ({number + 1}/{len(jobs)})", number, len(jobs))
            # A fresh loader per file keeps nothing cached between files
            loader = StageLoader(index, max_texture_size=8192) if index is not None else None
            items = []
            job_paths[:] = [path for _entry, path in members]
            try:
                for entry, path in members:
                    stem = path.rsplit("\\", 1)[-1].rsplit(".", 1)[0]
                    try:
                        model = textured_model(loader, path, metadata_files)
                        if model is None:
                            mesh = mesh_loader.load_from_bytes(read_entry_data(archive, entry))
                            if mesh is None or not mesh.position:
                                raise ValueError("not a readable mesh")
                            parts = loader.parts_for(path.rsplit(".", 1)[0] + ".gim", mesh) if loader is not None else []
                            model = (mesh, parts)
                    except Exception as exc:  # pylint: disable=broad-except
                        report.append(f"{stem}: {exc}")
                        continue
                    items.append({"name": stem, "mesh": model[0], "parts": model[1]})
                    for body in outfit_body_items(loader, path, selected_paths):
                        if all(body["name"] != other["name"] for other in items):
                            items.append(body)
                if not items:
                    continue
                link = next((found for found in (game_tables_link(links, path) for _entry, path in members) if found), None)
                parts = [_safe_folder(part) for part in folder_parts]
                if link and link.get("name"):                 # e.g. male_hair_00_bmy (Golden Rings)
                    parts[-1] = f"{parts[-1]} ({_display_name(link['name'])})"
                folder = os.path.join(target, *parts)
                while folder.lower() in used:                # same name in two sub-folders
                    folder += "_"
                used.add(folder.lower())
                file_name = _safe_folder(name)
                writer(items, os.path.join(folder, f"{file_name}.{fmt}"))
                done += 1
                face_folder = members[0][1].rsplit("\\", 1)[0].lower()
                for shape_path in sorted(shape_files.get(face_folder, ())):
                    if cancelled():
                        raise InterruptedError
                    from core.mesh_converter import face_shapes as shapes  # pylint: disable=import-outside-toplevel
                    shape_name = shape_path.rsplit("\\", 1)[-1][:-3]
                    suffix = shape_name.rsplit("_", 1)[-1] if "_" in shape_name else shape_name
                    progress(f"Exporting {name}: face shape {suffix}  ({number + 1}/{len(jobs)})", number, len(jobs))
                    try:
                        found = index.read(shape_path)
                        if found is None:
                            raise ValueError("not found")
                        reference = shapes.reference_mesh([item["mesh"] for item in items])
                        bones = shapes.shape_by_name(shapes.read_mb(found[0]), reference)
                        shaped = [dict(item, mesh=(shapes.apply_shape_to_eyes if re.search(r"_eye_(l|r|jyq)_", item["name"].lower())
                                                   else shapes.apply_shape)(item["mesh"], bones)) for item in items]
                        writer(shaped, os.path.join(folder, f"{file_name}_shape_{_safe_folder(suffix)}.{fmt}"))
                        shapes_written += 1
                    except Exception as exc:  # pylint: disable=broad-except
                        report.append(f"{name}: face shape {shape_name}: {exc}")
                if link:
                    named += bool(link.get("name"))
                    icons += save_item_icon(index, link, os.path.join(folder, f"{file_name}_icon.png"))
                untextured += sum(1 for item in items for part in item["parts"] if part.get("image") is None)
                if index is not None and metadata_files:
                    textures = [part.get("texture") for item in items for part in item["parts"]]
                    extras += save_metadata_textures(index, metadata_files, textures,
                                                     os.path.join(folder, f"{file_name}_textures", "other"))
            except InterruptedError:
                raise
            except Exception as exc:  # pylint: disable=broad-except
                report.append(f"{name}: {exc}")
            if loader is not None:
                report.extend(f"{name}: {line}" for line in loader.report)
            items = loader = None
        if not done:
            raise ValueError("Nothing could be exported." + ("\n" + "\n".join(report[:5]) if report else ""))
        if untextured:
            report.insert(0, f"{untextured} part(s) without a texture (see below).")
        if face_shapes:
            report.insert(0, f"{shapes_written} face shape variant(s) saved as <face>_shape_NN next to each face "
                             "(skin tones and makeup textures are in each face's _textures/other folder).")
        if links:
            report.insert(0, f"{named} model(s) named after their in-game item, {icons} item icon(s) saved as <name>_icon.png.")
        if extras:
            report.insert(0, f"{extras} extra texture(s) from the metadata (skin tones, makeup, masks…) "
                             "saved in each model's _textures\\other folder.")
        return f"{done} of {len(jobs)} {unit}, each in its own folder, in {target}"

    start_export(main_window, f"each:{fmt}", unit, build)


def game_tables_link(links, path):
    if not links:
        return None
    from core.npk.game_tables import link_for  # pylint: disable=import-outside-toplevel
    return link_for(links, path)


def _safe_folder(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\s]+', "_", name).strip("._") or "mesh"


ICON_FOLDERS = ("image\\clothes", "image\\face", "image\\hat_design_icon")


ICON_EXTENSIONS = (".dds", ".png", ".tga", ".jpg", ".ktx", ".pvr", ".astc")


def export_item_icons(main_window) -> None:
    """
    Save all of the game's 2D icons for character items as PNGs, keeping their names and sub-folders:
    image/clothes (outfits, hats, hair, brooches), image/face (faces, eyes, earrings, glasses, lips,
    skins) and image/hat_design_icon. Every icon is saved under the game's own file name, including icons
    no item uses; to save the icons of the selected items under their item names, see
    export_selected_item_icons.
    """
    from core.npk.stage_export import game_root_for  # pylint: disable=import-outside-toplevel
    app = QtWidgets.QApplication.instance()
    archive = app.property("npk_file") if app is not None else None
    game = game_root_for(archive.file_path) if archive is not None and getattr(archive, "file_path", None) else None
    settings = app.property("settings_manager") if app is not None else None
    if game is None and settings is not None:
        try:
            saved = settings.get("stage_export.game_folder", "")
            game = game_root_for(os.path.join(saved, "res", "x")) if saved else None
        except Exception:  # pylint: disable=broad-except
            game = None
    if game is None:
        QtWidgets.QMessageBox.information(main_window, "Export icons",
                                          "Open any Harry Potter: Magic Awakened .npk first, so the game folder is known.")
        return

    def build(progress, cancelled, report, target):
        from core.npk.stage_export import PackIndex, decode_texture, read_options_for  # pylint: disable=import-outside-toplevel
        progress("Reading the game's file list…", 0, 0)
        index = PackIndex(game, read_options_for(archive))
        prefixes = tuple(folder.lower() + "\\" for folder in ICON_FOLDERS)
        icons = sorted(path for path in index.manifest()
                       if path.lower().startswith(prefixes) and path.lower().endswith(ICON_EXTENSIONS))
        # The same icon can be listed as .dds and .png: keep one file per name
        unique = {}
        for path in icons:
            unique.setdefault(path.rsplit(".", 1)[0].lower(), path)
        done = 0
        for number, path in enumerate(unique.values()):
            if cancelled():
                raise InterruptedError
            if number % 25 == 0:
                progress(f"Saving {ntpath.basename(path)}  ({number + 1}/{len(unique)})", number, len(unique))
            found = index.read(path)
            if found is None:
                report.append(f"missing: {path}")
                continue
            try:
                image = decode_texture(found[0])
            except Exception as exc:  # pylint: disable=broad-except
                report.append(f"unreadable: {path} ({exc})")
                continue
            relative = path.split("\\")[1:]                      # drop "image"
            out = os.path.join(target, *relative[:-1], relative[-1].rsplit(".", 1)[0] + ".png")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            image.save(out)
            done += 1
        if not done:
            raise ValueError("No icons could be saved." + ("\n" + "\n".join(report[:5]) if report else ""))
        folders = ", ".join(f.split("\\")[1] for f in ICON_FOLDERS)
        return f"{done} icons as PNG in {target} (folders: {folders})"

    start_export(main_window, "each:png", "icons", build)


def export_selected_item_icons(main_window) -> None:
    """The in-game icons of the items selected in the Characters tab, saved as <item name>.png."""
    characters = getattr(main_window, "_character_folders_window", None)
    if characters is None:
        return
    try:
        from gui.widgets import characters_items  # pylint: disable=import-outside-toplevel
        index = characters_items._state.get("index")
        link_of = characters_items._link
    except Exception:  # pylint: disable=broad-except
        index, link_of = None, None
    found = characters.entry_items(characters.tree.selectedItems(), True) or {}
    paths = sorted({str(path) for path in found.values() if str(path or "").lower().endswith(".mesh")})
    icons = {}
    for path in paths:
        link = link_of(path) if link_of is not None else None
        if link and link.get("icon"):
            icons.setdefault(link["icon"], link.get("name") or path.rsplit("\\", 1)[-1][:-5])
    if index is None or not icons:
        QtWidgets.QMessageBox.information(main_window, "Export item icons",
                                          "None of the selected meshes has an in-game item icon"
                                          + ("." if index is not None else " (the item list is still loading)."))
        return

    def build(progress, cancelled, report, target):
        from core.npk.stage_export import decode_texture  # pylint: disable=import-outside-toplevel
        done, used = 0, set()
        for number, (icon, name) in enumerate(sorted(icons.items(), key=lambda kv: kv[1].lower())):
            if cancelled():
                raise InterruptedError
            progress(f"Saving {name}", number, len(icons))
            found_icon = index.read(icon.replace("/", "\\"))
            if found_icon is None:
                report.append(f"missing: {icon}")
                continue
            try:
                image = decode_texture(found_icon[0])
            except Exception as exc:  # pylint: disable=broad-except
                report.append(f"unreadable: {icon} ({exc})")
                continue
            stem = re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "icon"
            unique, count = stem, 2
            while unique.lower() in used:
                unique, count = f"{stem} ({count})", count + 1
            used.add(unique.lower())
            image.save(os.path.join(target, unique + ".png"))
            done += 1
        if not done:
            raise ValueError("No icons could be saved." + ("\n" + "\n".join(report[:5]) if report else ""))
        return f"{done} item icons as PNG in {target}"

    start_export(main_window, "each:png", "icons", build)


def _selected_face_folder(main_window):
    """The face folder (.../body/male_body_10) of the Characters tab's current selection, or None."""
    try:
        from gui.widgets.face_makeup_data import face_folder_of  # pylint: disable=import-outside-toplevel
        from gui.windows.character_folders_window import FOLDER_PATH_ROLE, PATH_ROLE  # pylint: disable=import-outside-toplevel
        for item in main_window._character_folders_window.tree.selectedItems():
            for role in (FOLDER_PATH_ROLE, PATH_ROLE):
                folder = face_folder_of(item.data(0, role))
                if folder:
                    return folder
    except Exception:  # pylint: disable=broad-except
        return None
    return None
