"""
Character creator background work: loading the game data, sprites, icons and pieces off the UI thread, and
the export job.
"""

from __future__ import annotations

import ntpath
import os
import re
from PySide6 import QtCore, QtGui
from core.logger import get_logger
from gui.widgets.face_makeup_data import (  # noqa: F401
    _hair_data,
    _library,
    _wardrobe,
    _wardrobe_choice,
    bake_hair,
    choice_key,
    dress_face_items,
    dress_hair_items,
    dress_wardrobe_items,
    face_presets,
    load_face_items,
    load_wardrobe_items,
    offered_shapes,
    skin_icons,
)
from gui.widgets.face_makeup_widgets import (  # noqa: F401
    LOOSE_SPRITES,
    RARITY_GEM_FOLDER,
    RARITY_GEMS,
    SKIN_SPRITE_PREFIX,
    UI_SHEETS,
    UI_SPRITES,
)

ICON_SIZE = 48


class _Signals(QtCore.QObject):
    loaded = QtCore.Signal(object, str)
    icons = QtCore.Signal(object)
    baked = QtCore.Signal(int, object, object, str)
    meshes = QtCore.Signal(object, object, str)
    hair = QtCore.Signal(object, object, str)
    part = QtCore.Signal(object, object, str)
    item_icons = QtCore.Signal(object)
    ui_sprites = QtCore.Signal(object)


def _load(game, signals, read_options=None):
    try:
        library, faces, files = _library(game, read_options)
        options = {sex: library.options(sex) for sex in ("male", "female")}
        options["hair"] = _hair_data(game, library)
        options["wardrobe"] = _wardrobe(library, options["hair"]["links"])
        options["shapes"] = offered_shapes(library)
        options["presets"] = face_presets(library, faces, files)
        options["skin_icons"] = skin_icons(library)
        payload, error = (library, faces, files, options), ""
    except Exception as exc:
        payload, error = None, str(exc)
    try:
        signals.loaded.emit(payload, error)
    except RuntimeError:
        pass


def _load_ui_sprites(index, signals):
    """The wardrobe screen's own sprites (slot icons, round frames, buttons) from UI.npk, cut from their atlases."""
    import io
    import plistlib
    from PIL import Image

    sprites = {}
    for stem in UI_SHEETS:
        try:
            data = index.read(stem + ".dds")
            atlas = Image.open(io.BytesIO(data[0] if isinstance(data, tuple) else data)).convert("RGBA")
            plist = index.read(stem + ".plist")
            frames = plistlib.loads(plist[0] if isinstance(plist, tuple) else plist).get("frames", {})
        except Exception:
            get_logger().debug("Could not read the sprite sheet %s", stem, exc_info=True)
            continue
        for name, info in frames.items():
            short = name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            if (short not in UI_SPRITES and not short.startswith(SKIN_SPRITE_PREFIX)) or short in sprites:
                continue
            try:
                x, y, w, h = (
                    int(float(v)) for v in re.findall(r"-?\d+(?:\.\d+)?", info.get("frame") or info.get("textureRect"))
                )
                rotated = info.get("rotated") or info.get("textureRotated")
                sprite = atlas.crop((x, y, x + (h if rotated else w), y + (w if rotated else h)))
                sprites[short] = sprite.rotate(90, expand=True) if rotated else sprite
            except Exception:
                get_logger().debug("Skipping a sprite with an unreadable frame in %s", stem, exc_info=True)
                continue
    loose = {name: RARITY_GEM_FOLDER + name for name in RARITY_GEMS.values()}  # the filter's rarity gems
    loose.update(LOOSE_SPRITES)
    for name, stem in loose.items():
        for extension in (".dds", ".png"):
            try:
                data = index.read(stem + extension)
                if data:
                    sprites[name] = Image.open(io.BytesIO(data[0] if isinstance(data, tuple) else data)).convert("RGBA")
                    break
            except Exception:
                get_logger().debug("Could not read the sprite image %s", stem, exc_info=True)
                continue
    try:
        signals.ui_sprites.emit(dict(sprites))  # rail icons, buttons, discs: show them right away
    except RuntimeError:
        return
    sprites = {}
    for path in index.manifest():  # skin tone icons (image\\face\\skins\\icon_fuse_N)
        lower = path.lower()
        if lower.startswith("image\\face\\skins\\icon_fuse_") and lower.endswith((".dds", ".png")):
            try:
                data = index.read(path)
                sprites[path.rsplit("\\", 1)[-1].rsplit(".", 1)[0]] = Image.open(
                    io.BytesIO(data[0] if isinstance(data, tuple) else data)
                ).convert("RGBA")
            except Exception:
                get_logger().debug("Could not read the icon %s", path, exc_info=True)
                continue
    try:
        signals.ui_sprites.emit(sprites)
    except RuntimeError:
        pass


def _load_ui_sprites_early(game, signals):
    """The UI sprites as soon as the window opens: they need only the pack index, not the game data."""
    from gui.widgets.character_anim_export import _index

    try:
        _load_ui_sprites(_index(game), signals)
    except Exception:
        get_logger().exception("Could not load the creator's UI sprites")


def _load_icons(library, options, signals):
    icons = {}
    for sex_key, per_sex in options.items():
        if sex_key not in ("male", "female") or not isinstance(per_sex, dict):
            continue
        for items in per_sex.values():
            for option in items:
                try:
                    image = library.icon(option)
                except Exception:
                    image = None
                if image is not None:
                    image = image.convert("RGBA")
                    image.thumbnail((ICON_SIZE * 2, ICON_SIZE * 2))
                    icons[(option.kind, option.id)] = QtGui.QImage(
                        image.tobytes("raw", "RGBA"), image.width, image.height, QtGui.QImage.Format.Format_RGBA8888
                    ).copy()
    try:
        signals.icons.emit(icons)
    except RuntimeError:
        pass


def _load_item_icons(index, paths, signals):
    """Item icons (image/clothes/*.png) as small QImages, sent in batches as they decode."""
    from core.npk.stage_export import decode_texture

    batch = {}
    for number, path in enumerate(paths):
        try:
            found = index.read(path.replace("/", "\\"))
            image = decode_texture(found[0]).convert("RGBA") if found else None
        except Exception:
            image = None
        if image is not None:
            image.thumbnail((ICON_SIZE * 2, ICON_SIZE * 2))
            batch[path] = QtGui.QImage(
                image.tobytes("raw", "RGBA"), image.width, image.height, QtGui.QImage.Format.Format_RGBA8888
            ).copy()
        if len(batch) >= 60 or number == len(paths) - 1:
            try:
                signals.item_icons.emit(batch)
            except RuntimeError:
                return
            batch = {}


def _load_part_worker(library, path, texture, sockets, signals, sex=None, key=None):
    report: list[str] = []
    try:
        items, error = load_wardrobe_items(library, path, report, texture, sockets, sex), ""
    except Exception as exc:
        items, error = None, str(exc)
    try:
        signals.part.emit(key or choice_key(path, texture), items, error)
    except RuntimeError:
        pass


def _load_hair_worker(library, path, signals):
    report: list[str] = []
    try:
        items, error = load_face_items(library, [path], [], None, report), ""
    except Exception as exc:
        items, error = None, str(exc)
    try:
        signals.hair.emit(path, items, error)
    except RuntimeError:
        pass


def _load_meshes_worker(library, key, mesh_paths, files, shape_path, signals):
    report: list[str] = []
    try:
        items, error = load_face_items(library, mesh_paths, files, shape_path, report), ""
    except Exception as exc:
        items, error = None, str(exc)
    try:
        signals.meshes.emit(key, items, error)
    except RuntimeError:
        pass


def _bake_worker(generation, library, folder, sex, look, side, face_texture, hair_data, hair_choice, signals):
    notes: list[str] = []
    try:
        image = library.bake(folder, sex, look, base_texture=face_texture, side=side, report=notes)
        split = library.needs_split(look, folder, sex)
        info = {
            "split": split,
            "notes": notes,
            "eye": library.bake_eye(look, notes),
            "eyebrow": library.bake_eyebrow(folder, sex, look, notes),
        }
        info["hair"] = bake_hair(hair_data, hair_choice, notes)
        if split:
            info["sides"] = {
                s: (image if s == side else library.bake(folder, sex, look, base_texture=face_texture, side=s))
                for s in ("left", "right")
            }
        else:
            info["sides"] = {None: image}
        result = (generation, image, info, "")
    except Exception as exc:
        result = (generation, None, {}, f"Could not bake this look: {exc}")
    try:
        signals.baked.emit(*result)
    except RuntimeError:
        pass


def export_face(
    main_window,
    game,
    library,
    folder,
    mesh_paths,
    hair_data,
    hair_choice,
    hair_path,
    wardrobe_paths,
    files,
    sex,
    look,
    shape_path,
    face_texture,
    fmt,
    hidden=(),
):
    """Write <output>/<face>_makeup/model.<fmt>: face (+ eyes, lashes, eyebrows) with the makeup baked in."""
    from gui.widgets.character_exports import start_export

    face = folder.rsplit("\\", 1)[-1]

    def build(progress, cancelled, report, target):
        from core.mesh_converter import model_export

        writer = {"glb": model_export.write_glb, "pmx": model_export.write_pmx, "obj": model_export.write_obj}[fmt]
        items = load_face_items(library, mesh_paths, files, shape_path, report, progress, cancelled)
        progress("Baking the makeup…", len(mesh_paths), len(mesh_paths) + 2)
        notes: list[str] = []
        split = library.needs_split(look, folder, sex)
        sides = ("left", "right") if split else (None,)
        baked = {
            side: library.bake(folder, sex, look, base_texture=face_texture, side=side, report=notes) for side in sides
        }
        eye = library.bake_eye(look, notes)
        brow = library.bake_eyebrow(folder, sex, look, notes)
        report.extend(dict.fromkeys(notes))
        out_folder = os.path.join(target, f"{face}_makeup")
        os.makedirs(out_folder, exist_ok=True)
        output, eye_meshes = dress_face_items(library, items, sex, face, look, baked, eye, brow)
        if hair_choice:
            hair = hair_choice[0]
            dyed = bake_hair(hair_data, hair_choice, notes)
            output += dress_hair_items(
                load_face_items(library, [hair_path or hair["mesh"]], [], None, report), hair, dyed
            )
            if dyed is not None:
                dyed.save(os.path.join(out_folder, f"{ntpath.basename(hair['folder'])}_dyed.png"))
            report.extend(dict.fromkeys(n for n in notes if n.startswith("Hair dye")))
        for side, image in baked.items():
            image.save(os.path.join(out_folder, f"{face}_makeup{'_' + side if side else ''}.png"))
        if eye is not None:
            eye.save(os.path.join(out_folder, f"{face}_makeup_eye.png"))
        if brow is not None:
            brow.save(os.path.join(out_folder, f"{face}_makeup_eyebrow.png"))
            if not eye_meshes:
                report.append(
                    "This face draws its eyes on the face texture (no separate eye meshes), "
                    "so the eye type and colour are not applied to it."
                )
        skin = baked.get(None) or baked.get("left")
        choices = [_wardrobe_choice(c) for c in wardrobe_paths]
        for path, texture, sockets in choices:
            if cancelled():
                raise InterruptedError
            progress(f"Loading {ntpath.basename(path)}", len(mesh_paths), len(mesh_paths) + 2)
            output += dress_wardrobe_items(load_wardrobe_items(library, path, report, texture, sockets, sex), skin)
        from gui.widgets.character_exports import with_body_size
        from core.npk.accessory_sockets import apply_bind_offsets

        worn = [hair_choice[0].get("mesh") if hair_choice and isinstance(hair_choice[0], dict) else None] + [
            c[0] for c in choices if "\\clothes\\" in c[0].lower()
        ]
        output = apply_bind_offsets(library, output, worn)
        output = with_body_size(
            library.index, output, [c[0] for c in choices] + [os.path.join(folder, face + ".mesh")], report
        )
        hidden_names = set(hidden)  # pieces hidden in the creator's Layers box
        output = [item for item in output if item.get("name") not in hidden_names]
        progress(f"Writing model.{fmt}", len(mesh_paths) + 1, len(mesh_paths) + 2)
        written = writer(output, os.path.join(out_folder, f"model.{fmt}"))
        return f"{written}"

    start_export(main_window, f"each:{fmt}", f"{face}_makeup", build)
