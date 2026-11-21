"""
Character creator data, without Qt: the game's face folders, hair dye, wardrobe lists (outfits, recolours,
socket attachments), face presets, and loading / dressing the pieces for the 3D view and the export.
"""

from __future__ import annotations

import ntpath
import os
import re
import threading

import numpy as np
from core.logger import get_logger


BODY_FOLDERS = ("model\\character\\appearance\\boy\\high\\body\\", "model\\character\\appearance\\girl\\high\\body\\")


KINDS = (("eyeshadow", "Eyeshadow"), ("blush", "Blush / face decal"), ("lips", "Lip colour"), ("eyebrow", "Eyebrow colour"),
         ("eyetype", "Eye type (iris)"), ("eyecolor", "Eye colour"))


FACE_TEXTURE = re.compile(r"^(male|female)_body_\d+(_\d+)?$")


_LIBRARY: dict[str, object] = {}


_LOCK = threading.Lock()


def _library(game, options=None):
    """(MakeupLibrary, face folders {folder: [mesh paths]}, files {folder: [all files]}), built once per game."""
    from core.npk.face_makeup import MakeupLibrary
    from core.npk.game_tables import RootTables
    from core.npk.stage_export import read_options_for
    from gui.widgets.character_anim_export import _index
    options = options or read_options_for(None)
    with _LOCK:
        key = str(game)
        if key not in _LIBRARY:
            index = _index(game, options)
            library = MakeupLibrary(index, RootTables(game, options))
            library.read_options = options
            files: dict[str, list[str]] = {}
            for path in index.manifest():
                lower = path.lower()
                if lower.startswith(BODY_FOLDERS):
                    folder = path.rsplit("\\", 1)[0]
                    if folder.count("\\") == 6:                # ...\body\<face>\file
                        files.setdefault(folder, []).append(path)
            faces = {folder: sorted(p for p in paths if p.lower().endswith(".mesh"))
                     for folder, paths in files.items()
                     if re.search(r"_body_\d+$", folder.lower()) and any(p.lower().endswith(".mesh") for p in paths)}
            _LIBRARY.clear()
            _LIBRARY[key] = (library, dict(sorted(faces.items(), key=lambda kv: _face_sort(kv[0]))), files)
        return _LIBRARY[key]


def _face_sort(folder):
    name = folder.rsplit("\\", 1)[-1]
    match = re.search(r"(\d+)$", name)
    return (0 if name.startswith("male") else 1, int(match.group(1)) if match else 0)


def _hair_data(game, library):
    """
    Hairstyles with their in-game English names: the dyeable ones (hair_color_tex), then the basic ones the
    game colours with preset variants instead of dye (color_hair: Spring Curls, Royal Grace...), and the dye
    colours.
    """
    from core.npk import game_tables, hair_dye
    from core.npk.stage_export import read_options_for
    dye = hair_dye.HairDye(library.index, library.tables)
    hairs = [dict(h) for h in dye.hairs()]
    try:
        links = game_tables.mesh_links(game, getattr(library, "read_options", None) or read_options_for(None),
                                       lambda path: (library.index.read(path) or (None,))[0])
    except Exception:
        links = {}
    for hair in hairs:
        link = game_tables.link_for(links, hair["mesh"]) if links else None
        stem = hair["folder"].rsplit("\\", 1)[-1]
        hair["name"] = f"{link['name']}  ·  {stem}" if link and link.get("name") else stem
        hair["icon"] = link.get("icon") if link else None
        hair["rarity"] = game_tables.item_rarity(library.tables, link.get("item")) if link else None
        hair["traits"] = game_tables.item_traits(library.tables, link.get("item")) if link else ()
        hair["tags"] = game_tables.item_tags(library.tables, link.get("item")) if link else ()
    hairs += _basic_hairs(library, {hair["folder"].lower() for hair in hairs})
    hairs.sort(key=lambda h: h["folder"].lower())
    return {"dye": dye, "hairs": hairs, "colors": dye.colors(), "originals": {}, "links": links}


def _basic_hairs(library, listed) -> list[dict]:
    """
    The basic hairstyles that are not dyeable, from color_hair: each named style item lists its colour items,
    whose dress rows hold the model (<folder>\\<folder>_NN.gim, one per preset colour). The first colour is
    the one shown; qs / rs stay None (no dye).
    """
    from core.npk import game_tables
    try:
        tables = library.tables
        styles, items, dresses = (tables.keyed_rows(name) for name in ("color_hair", "item", "role_dress"))
        english = {key: row.get("text_en", "") for key, row in tables.keyed_rows("item_text_os_en").items()
                   if isinstance(row, dict)}
    except Exception:
        return []
    found = []
    for style_id, style in sorted(styles.items(), key=lambda kv: str(kv[0])):
        item = items.get(style_id)
        colours = [items.get(c) for c in (style.get("color_hair_items") or ()) if isinstance(style, dict)]
        dress = next((dresses.get(c.get("mini_type")) for c in colours if isinstance(c, dict)
                      and isinstance(dresses.get(c.get("mini_type")), dict)), None)
        if not isinstance(item, dict) or not dress or not dress.get("model"):
            continue
        gim = dress["model"].replace("/", "\\")
        folder = gim.rsplit("\\", 1)[0]
        if folder.lower() in listed or library.index.read(gim) is None:
            continue
        listed.add(folder.lower())
        name = english.get(item.get("name", ""), "").strip()
        stem = folder.rsplit("\\", 1)[-1]
        icon = item.get("icon_path")
        found.append({"folder": folder, "mesh": gim, "qs": None, "rs": None, "dress_id": dress.get("id"),
                      "sex": "female" if "\\girl\\" in folder.lower() else "male",
                      "name": f"{name}  ·  {stem}" if name else stem,
                      "icon": None if game_tables.is_generic_icon(icon) else icon,
                      "rarity": game_tables.item_rarity(tables, style_id),
                      "traits": game_tables.item_traits(tables, style_id),
                      "tags": game_tables.item_tags(tables, style_id)})
    return found


WARDROBE = (("clothes", "Outfit"), ("hat", "Hat"), ("earring", "Earrings"), ("glasses", "Glasses"),
            ("scarf", "Scarf"), ("brooch", "Brooch"), ("bag", "Bag"))


DRESS_SEXES = {1: ("male",), 2: ("female",), 3: ("male", "female")}


def _outfit_group(parts) -> str:
    """The outfit a model belongs to: its number (male_clothes_182_wyds_mao -> 182), else its folder."""
    folder = parts[6].lower() if len(parts) > 6 else ""
    match = re.match(r"(?:male|female|common)_[a-z]+_(\d+)(?:_|$)", folder)
    return match.group(1) if match else folder


def _wardrobe(library, links):
    """
    Builds the wardrobe lists for each gender and slot from the game's dress rows, so each outfit and each of its
    recolours becomes one entry. Models with no dress row are added at the end, except incomplete remakes, which are
    hidden. Also returns, for each hat, the trimmed hairstyles it uses.
    """
    import json
    import xml.etree.ElementTree as ET
    from core.npk import game_tables
    kinds = {kind for kind, _label in WARDROBE}
    found = {"male": {k: [] for k in kinds}, "female": {k: [] for k in kinds}}
    masks: dict[str, dict[str, str]] = {}
    files: dict[str, list[str]] = {}
    for path in library.index.manifest():
        lower = path.lower()
        if not lower.endswith(".mesh") or not lower.startswith("model\\character\\appearance\\"):
            continue
        parts = path.split("\\")
        if len(parts) < 7 or parts[4].lower() != "high":
            continue
        kind = parts[5].lower()
        if len(parts) == 8 and kind in kinds:
            files.setdefault("\\".join(parts[:7]).lower(), []).append(path)
        elif kind == "hat" and len(parts) == 10 and parts[7].lower() == "mask" and parts[8].lower() == "hair":
            hat_folder = "\\".join(parts[:7]).lower()
            masks.setdefault(hat_folder, {})[parts[9][:-5].lower()] = path
    by_lower = {p.lower(): p for paths in files.values() for p in paths}

    # Find each dress row's name and icon from the shop item that belongs to it.
    tables = library.tables
    english = {key: row.get("text_en", "") for key, row in tables.keyed_rows("item_text_os_en").items()
               if isinstance(row, dict)}
    items_for = game_tables.items_by_dress(tables)
    mesh_of_gim: dict[str, str | None] = {}

    def mesh_for(gim):
        if gim not in mesh_of_gim:
            mesh = gim.rsplit(".", 1)[0] + ".mesh"
            found_gim = library.index.read(gim)
            if found_gim is not None:
                try:
                    attribute = ET.fromstring(found_gim[0]).get("Mesh")
                    if attribute and attribute.lower() != "false":
                        mesh = attribute
                except ET.ParseError:
                    pass
            mesh_of_gim[gim] = by_lower.get(mesh.replace("/", "\\").lower())
        return mesh_of_gim[gim]

    mvar_cache: dict = {}

    def variant_texture(gim, number):
        """The base texture (uniform_Tex0) of material variant `number` in the .mvar next to the model."""
        mvar = gim.rsplit(".", 1)[0] + ".mvar"
        if mvar not in mvar_cache:
            mvar_cache[mvar] = None
            found_mvar = library.index.read(mvar)
            if found_mvar is not None:
                try:
                    mvar_cache[mvar] = json.loads(found_mvar[0].decode("utf-8"))
                except ValueError:
                    pass
        for material in (mvar_cache[mvar] or {}).values():
            if not isinstance(material, dict):
                continue
            columns = [column[1] if isinstance(column, list) and len(column) > 1 else "" for column in
                       material.get("columns", [])]
            if "uniform_Tex0" not in columns:
                continue
            variant = material.get("variants", {}).get(str(number))
            values = variant[1] if isinstance(variant, list) and len(variant) > 1 else {}
            path = values.get(str(columns.index("uniform_Tex0"))) if isinstance(values, dict) else None
            if isinstance(path, str) and path:
                return path.replace("/", "\\")
        return None

    default_cache: dict = {}

    def default_texture(gim):
        """The model's own colour texture (Tex0 of the material next to it, else the one it points at)."""
        if gim not in default_cache:
            default_cache[gim] = None
            stem = gim.rsplit(".", 1)[0]
            for mtg in (stem + ".mtg",):
                found_mtg = library.index.read(mtg)
                if found_mtg is not None:
                    match = re.search(r'<Tex0 Value="([^"]+)"', found_mtg[0].decode("utf-8", "replace"))
                    default_cache[gim] = match.group(1) if match else None
                    break
        return default_cache[gim]

    # Models the game gives colour variants (model_mtg_id > 0 without a texture of their own)
    variant_models = {str(row["model"]).replace("/", "\\").lower() for row in tables.keyed_rows("role_dress").values()
                      if isinstance(row, dict) and row.get("model") and not row.get("model_tex")
                      and row.get("model_mtg_id") not in (None, 0)}
    incomplete_cache: dict[str, bool] = {}
    hidden: set = set()

    # Keep one dress row per model and texture: the one with the best name and icon. The game repeats outfits across
    # shop, transformation and event rows, and most of those copies have no item of their own.
    best: dict[tuple, dict] = {}
    has_item: set = set()
    used_folders = set()
    for key, dress in sorted(tables.keyed_rows("role_dress").items(), key=lambda kv: str(kv[0])):
        if not isinstance(dress, dict) or not dress.get("model"):
            continue
        mesh = mesh_for(dress["model"].replace("/", "\\"))
        if mesh is None:
            continue
        parts = mesh.split("\\")
        # the row's own sex (1 male, 2 female, 3 both): every brooch is modelled under girl, shared hats under common
        sexes = DRESS_SEXES.get(dress.get("sex")) or (
            ("male",) if parts[3].lower() == "boy" else ("female",) if parts[3].lower() == "girl" else ())
        kind = parts[5].lower()
        if not sexes or kind not in kinds:
            continue
        used_folders.add("\\".join(parts[:7]).lower())
        model = dress["model"].replace("/", "\\")
        if model.lower() in variant_models:
            if model.lower() not in incomplete_cache:
                incomplete_cache[model.lower()] = incomplete_remake(library, model)
            if incomplete_cache[model.lower()]:
                hidden.add(mesh)
                continue
        texture = (dress.get("model_tex") or "").replace("/", "\\") or None
        if texture is None and dress.get("model_mtg_id") is not None:
            # a colour variant of the model's own material set (.mvar): another base texture (variant 0 too: the
            # material the game uses for the mesh, unless it is just the model's default texture)
            gim = dress["model"].replace("/", "\\")
            texture = variant_texture(gim, int(dress["model_mtg_id"]))
            if texture and int(dress["model_mtg_id"]) == 0 and texture.lower() == (default_texture(gim) or "").lower():
                texture = None
        item = game_tables.best_item(items_for.get(dress.get("id", key), ()), kind, english)
        title = english.get(item.get("name", ""), "") if item else ""
        if title.strip().lower() in ("", "null!", "null"):
            title = ""
        icon = item.get("icon_path") if item else None
        if icon and game_tables.is_generic_icon(icon):
            icon = None
        score = (item is not None, bool(title), bool(icon))
        group = _outfit_group(parts)
        for sex in sexes:
            if item is not None:
                has_item.add((sex, kind, group))
            slot = (sex, mesh.lower(), (texture or "").lower())
            if slot not in best or score > best[slot]["score"]:
                best[slot] = {"score": score, "kind": kind, "group": group, "mesh": mesh, "texture": texture,
                              "title": title, "icon": icon, "sockets": list(dress.get("socket_list") or []),
                              "rarity": game_tables.item_rarity(tables, item.get("_id")) if item else None,
                              "traits": game_tables.item_traits(tables, item.get("_id")) if item else (),
                              "tags": game_tables.item_tags(tables, item.get("_id")) if item else ()}
    for (sex, _mesh, _texture), row in best.items():
        kind, mesh, texture = row["kind"], row["mesh"], row["texture"]
        if not row["score"][0] and (sex, kind, row["group"]) in has_item:
            continue                                   # a piece of a sold outfit (hood, effects, inner layer)
        stem = mesh.rsplit("\\", 1)[-1][:-5]
        if texture:
            tex_stem = texture.rsplit("\\", 1)[-1].rsplit(".", 1)[0]
            tex_stem = re.sub(r"_(af|h|d|diff)$", "", tex_stem, flags=re.I)
            if tex_stem.lower() != stem.lower() and tex_stem.lower().startswith(stem.lower()):
                stem = tex_stem                       # recolour: use its own name (..._02) for the filter
            elif tex_stem.lower() != stem.lower():
                stem = f"{stem} ({tex_stem})"         # a recolour texture named differently (aisidaila_02)
        title = row["title"]
        found[sex][kind].append({"name": f"{title}  ·  {stem}" if title else stem, "path": mesh, "icon": row["icon"],
                                 "texture": texture, "sockets": row["sockets"], "rarity": row["rarity"],
                                 "traits": row["traits"], "tags": row["tags"],
                                 "order": (mesh.lower(), stem.lower() != mesh.rsplit(
                                     "\\", 1)[-1][:-5].lower(), texture or "")})
    # Add the models that no dress row uses but a shop item still names (found through the item links) at the end;
    # the ones nothing names are cut or unreleased and are left out. In folders of listed outfits, keep only the
    # meshes the rows name; the other meshes are pieces the game swaps in by itself.
    for folder, paths in files.items():
        base_folder = re.sub(r"_a$", "", folder)
        if folder in used_folders or base_folder in used_folders:
            continue
        parts = folder.split("\\")
        sex = "male" if parts[3] == "boy" else "female" if parts[3] == "girl" else None
        if sex is None:
            continue
        link_paths = sorted(paths, key=lambda p: (len(p), p.lower()))
        for path in link_paths[:1]:
            link = game_tables.link_for(links, path) if links else None
            if not link or not link.get("name"):
                continue
            stem = path.rsplit("\\", 1)[-1][:-5]
            found[sex][parts[5]].append({"name": f"{link['name']}  ·  {stem}", "path": path, "icon": link.get("icon"),
                                         "texture": None, "sockets": [],
                                         "rarity": game_tables.item_rarity(tables, link.get("item")),
                                         "traits": game_tables.item_traits(tables, link.get("item")),
                                         "tags": game_tables.item_tags(tables, link.get("item")),
                                         "order": ("~" + path.lower(), False, "")})
    for per_sex in found.values():
        for entries in per_sex.values():
            entries.sort(key=lambda e: e["order"])
    return {"items": found, "masks": masks, "hidden": sorted(hidden)}


def _read_texture(library, texture):
    """A texture as RGBA (the game ships .dds for the .tga a material names), or None."""
    import io
    from PIL import Image
    for candidate in (texture.rsplit(".", 1)[0] + ".dds", texture, texture.rsplit(".", 1)[0] + ".png"):
        found = library.index.read(candidate)
        if found is None:
            continue
        try:
            return Image.open(io.BytesIO(found[0])).convert("RGBA")
        except Exception:
            continue
    return None


def _mvar_textures(library, path, texture) -> dict[str, str] | None:
    """
    {material name (lower case): Tex0} of the colour variant in the model's .mvar that uses `texture`, or None.
    A variant can give each material its own texture (a coat on one sheet, sleeves on another, a tassel on a
    third); materials a variant leaves out keep their own.
    """
    import json
    stem = path.rsplit(".", 1)[0]
    found = None
    for candidate in (stem + ".mvar", re.sub(r"_all$", "", stem) + ".mvar"):
        found = library.index.read(candidate)
        if found is not None:
            break
    if found is None:
        return None
    try:
        materials = json.loads(found[0].decode("utf-8"))
    except ValueError:
        return None
    wanted = texture.replace("/", "\\").lower()
    variants: dict[str, dict[str, str]] = {}
    first: dict[str, str] = {}                        # variant -> its first material's Tex0, as _wardrobe names it
    for name, material in materials.items() if isinstance(materials, dict) else ():
        if not isinstance(material, dict):
            continue
        columns = [column[1] if isinstance(column, list) and len(column) > 1 else "" for column in
                   material.get("columns", [])]
        if "uniform_Tex0" not in columns:
            continue
        column = str(columns.index("uniform_Tex0"))
        for number, variant in (material.get("variants") or {}).items():
            values = variant[1] if isinstance(variant, list) and len(variant) > 1 else {}
            tex = values.get(column) if isinstance(values, dict) else None
            if isinstance(tex, str) and tex:
                variants.setdefault(str(number), {})[str(name).lower()] = tex.replace("/", "\\")
                first.setdefault(str(number), tex.replace("/", "\\").lower())
    # the variant _wardrobe took this texture from (its first material's Tex0); a recolour, so not variant 0
    # when another one matches too
    matching = sorted((number for number, tex in first.items() if tex == wanted), key=lambda n: (n == "0", n))
    return variants[matching[0]] if matching else None


def incomplete_remake(library, gim) -> bool:
    """
    A remade model whose new files are only partly installed: its colour variants have no .mvar and its .gim
    asks for more materials than its .mtg defines (the new mesh arrived, the new material and textures did
    not), so the old texture lands on the new UV layout. Only checked for models with colour-variant rows.
    """
    import xml.etree.ElementTree as ET
    stem = gim.rsplit(".", 1)[0]
    if library.index.read(stem + ".mvar") is not None:
        return False
    found = library.index.read(gim)
    if found is None:
        return False
    try:
        root = ET.fromstring(found[0])
    except ET.ParseError:
        return False
    wanted = [int(sub.get("MtlIdx", 0)) for sub in root.iter() if re.fullmatch(r"Sub\d+", sub.tag)]
    if not wanted or max(wanted) == 0:
        return False
    node = root.find("MtgFile")
    mtg = ((node.get("MtgPath") if node is not None else None) or stem + ".mtg").replace("/", "\\")
    found_mtg = library.index.read(mtg)
    if found_mtg is None:
        return True
    try:
        group = ET.fromstring(found_mtg[0]).find("MaterialGroup")
        count = int(group.get("MaterialCount", 0)) if group is not None else 0
    except (ET.ParseError, ValueError):
        return False
    return max(wanted) >= count


def _recolour(library, items, texture, report, path=None):
    """
    A recolour: with the model's .mvar, each material gets the variant's own texture; otherwise the piece's
    colour texture (<stem>_af) is swapped for the row's (<stem>_02_af etc.).
    """
    texture = texture.replace("/", "\\")
    stem = texture.rsplit("\\", 1)[-1].rsplit(".", 1)[0].lower()
    by_material = _mvar_textures(library, path, texture) if path else None
    if by_material:
        images: dict[str, object] = {}
        swapped = 0
        for item in items:
            if item.get("body_piece"):
                continue
            for part in item["parts"]:
                tex = by_material.get(str(part.get("material") or "").lower())
                if tex is None:
                    continue
                if tex.lower() not in images:
                    images[tex.lower()] = _read_texture(library, tex)
                    if images[tex.lower()] is None:
                        report.append(f"Recolour texture {ntpath.basename(tex)} could not be read; that part keeps "
                                      "its first colours.")
                if images[tex.lower()] is not None:
                    part["image"], part["texture"] = images[tex.lower()], tex
                    swapped += 1
        if swapped:
            return items
    base_stem = re.sub(r"_\d+(?=(_[a-z]+)?$)", "", stem)          # female_x_lyzw_02_af -> female_x_lyzw_af
    image = _read_texture(library, texture)
    if image is None:
        report.append(f"Recolour texture {stem} could not be read; shown in its first colours.")
        return items
    def family(name):
        """male_clothes_146_hdyz_02_af and male_clothes_146_hdyz_04 -> male_clothes_146_hdyz (a colour map's set)."""
        return re.sub(r"(_?\d+)*(_(af|h|d|diff))?$", "", name)

    def is_colour_map(name):
        return not re.search(r"_(ao\d*|mask\w*|n|nm|normal|alpha|ramp|light\w*|spec\w*|emi\w*|fx\w*)$", name)

    swapped = 0
    for item in items:
        if item.get("body_piece"):
            continue
        for part in item["parts"]:
            part_stem = str(part.get("texture") or "").replace("/", "\\").rsplit("\\", 1)[-1].rsplit(".", 1)[0].lower()
            if not part_stem or not is_colour_map(part_stem):
                continue
            if part_stem in (base_stem, stem) or (base_stem.startswith(part_stem) and stem != part_stem) or                     family(part_stem) == family(stem):
                part["image"] = image
                part["texture"] = texture
                swapped += 1
    if not swapped:                                   # one colour map for the whole piece (one or more parts)
        main = [part for item in items if not item.get("body_piece") for part in item["parts"]]
        used = {str(part.get("texture") or "").lower() for part in main}
        if main and len(used) == 1:
            for part in main:
                part["image"], part["texture"] = image, texture
        else:
            report.append(f"Recolour {stem}: no part of the model uses a matching colour texture; shown in its first colours.")
    return items


def attachment_items(library, path, sockets, report):
    """
    The extra pieces the game hangs on rig sockets for an outfit: sockets is the dress row's socket_list (ids in
    the attachments table, each a model and a socket), e.g. the fur shawl of male_clothes_199_hgmdal on 199_pijian.
    """
    from core.npk import accessory_sockets
    from core.npk.stage_export import StageLoader
    ids = list(sockets or ())
    if not ids:
        return []
    try:
        table = library.tables.keyed_rows("attachments")
    except Exception:
        return []
    sex = "female" if "\\girl\\" in path.lower() else "male"
    sockets = accessory_sockets.for_library(library)
    socket_table, rest = sockets.sockets(sex), sockets.rest(sex)
    loader = StageLoader(library.index, max_texture_size=8192)
    pieces = []
    for number in ids:
        row = table.get(number)
        if not isinstance(row, dict) or not row.get("path"):
            continue
        gim = row["path"].replace("/", "\\")
        if not gim.lower().endswith((".gim", ".mesh")):
            continue                                   # particle effects (.sfx) and the like: nothing to show
        socket = socket_table.get(row.get("socket"))
        if socket is None or socket[0] not in rest:
            report.append(f"{ntpath.basename(gim)}: socket {row.get('socket')} not found")
            continue
        model = loader.model(gim)
        if model is None:
            report.append(f"{ntpath.basename(gim)}: not readable")
            continue
        bone, offset = socket
        place = offset @ rest[bone]
        name = gim.rsplit("\\", 1)[-1].rsplit(".", 1)[0]
        pieces.append({"name": name, "mesh": accessory_sockets._place(model[0], place, bone, rest[bone], name),
                       "parts": [dict(part) for part in model[1]]})
    report.extend(line for line in loader.report if "material taken from" not in line)
    return pieces


def load_wardrobe_items(library, path, report, texture=None, sockets=(), sex=None):
    """
    A wardrobe piece as export items; an outfit brings the body piece it is worn with (<outfit>_body.gim) and
    the pieces its dress row hangs on sockets (sockets: the wardrobe entry's attachment ids). sex: the wearer's,
    whose rig the socketed pieces hang on (brooches live in the girl folder for both sexes); by default the file's.
    """
    import xml.etree.ElementTree as ET
    from core.npk.stage_export import StageLoader
    items = load_face_items(library, [path], [], None, report)
    if texture:
        items = _recolour(library, items, texture, report, path)
    items += attachment_items(library, path, sockets, report)
    if "\\clothes\\" in path.lower():
        gim_path = path.rsplit(".", 1)[0] + "_body.gim"
        found = library.index.read(gim_path)
        if found is not None:
            try:
                target = (ET.fromstring(found[0]).get("Mesh") or "").strip()
            except ET.ParseError:
                target = ""
            model = StageLoader(library.index, max_texture_size=8192).model(gim_path)
            if model is not None:
                name = target.rsplit("\\", 1)[-1].rsplit(".", 1)[0] if target else "body"
                items.append({"name": name, "mesh": model[0], "parts": [dict(p) for p in model[1]], "body_piece": True})
        return items
    # Earrings, glasses, hats: the game hangs these on sockets of the character rig
    from core.npk.accessory_sockets import for_library
    sockets = for_library(library)
    sex = sex or ("female" if "\\girl\\" in path.lower() else "male")
    return sockets.attach(items, path, sex, report)


def dress_wardrobe_items(items, skin):
    """Body pieces wear the made-up face texture: their skin lives in the face's texture atlas."""
    output = []
    for source in items:
        item = dict(source, parts=[dict(part) for part in source["parts"]])
        if skin is not None:
            for part in item["parts"]:
                stem = os.path.basename(str(part.get("texture") or "")).rsplit(".", 1)[0].lower()
                if FACE_TEXTURE.match(stem):
                    part["image"] = skin
                    part["texture"] = "skin.png"
        output.append(item)
    return output


def hair_original(hair_data, hair):
    """The hairstyle's own texture (PIL) for its cut-out edges, cached."""
    originals = hair_data["originals"]
    if hair["folder"] not in originals:
        from core.npk.stage_export import StageLoader
        model = StageLoader(hair_data["dye"].index).model(hair["mesh"])
        originals[hair["folder"]] = next((part.get("image") for part in (model[1] if model else [])
                                          if part.get("image") is not None), None)
    return originals[hair["folder"]]


def bake_hair(hair_data, hair_choice, notes):
    """The dyed hair texture for (hair, colour 1, colour 2), or None when no dye is chosen."""
    if not hair_choice:
        return None
    hair, color1, color2 = hair_choice
    if hair is None or (color1 is None and color2 is None) or not hair.get("qs"):
        return None                                    # no dye chosen, or a basic hairstyle (preset colours)
    color1 = color1 if color1 is not None else color2
    color2 = color2 if color2 is not None else color1
    try:
        return hair_data["dye"].bake(hair, color1, color2, hair_original(hair_data, hair))
    except Exception as exc:
        notes.append(f"Hair dye: {exc}")
        return None


def offered_shapes(library) -> dict[str, list[str]]:
    """
    Face model -> the shape suffixes the game offers for it, in its order (role_face): "00"/"0" is the base face,
    "01", ... are <face>_<suffix>.mb. Shape files a face has but the game never offers are left out of this list.
    """
    try:
        models = {row["id"]: row.get("face_model") for row in library.tables.keyed_rows("face_shape").values()}
        offered: dict[str, list[str]] = {}
        rows = sorted(library.tables.keyed_rows("role_face").values(), key=lambda r: (r.get("sort", 0), r.get("id", 0)))
        for row in rows:
            model = models.get(row.get("shape_id"))
            suffix = str(row.get("bone_suffix", ""))
            if model and suffix not in offered.setdefault(model, []):
                offered[model].append(suffix)
        return offered
    except Exception:
        return {}


# Exclusive skin tones (face_skincolor.is_exclusive_skin) offered next to the standard ones
EXTRA_SKIN_TONES = (49,)


def skin_icons(library) -> dict[str, dict[int, tuple[int, str]]]:
    """
    Per sex: the face's skin variant number (model_mtg_id) -> (order in the game's list, swatch sprite), for the
    tones the game's creator offers (is_default_skin) and EXTRA_SKIN_TONES. The old first set (pic_chuangjue_fuse_*)
    and the other exclusive tones are left out.
    """
    result: dict[str, dict[int, tuple[int, str]]] = {"male": {}, "female": {}}
    try:
        for row in library.tables.keyed_rows("face_skincolor").values():
            if not row.get("is_default_skin") and int(row.get("model_mtg_id", 0)) not in EXTRA_SKIN_TONES:
                continue
            sex = "male" if row.get("sex") == 1 else "female"
            result[sex].setdefault(int(row.get("model_mtg_id", 0)), (int(row.get("order", 999)), str(row.get("icon", ""))))
    except Exception:
        get_logger().warning("Could not read the skin tone table (face_skincolor)", exc_info=True)
    return result


TRAIT_KEYS = ("yifu_qd", "yifu_zc", "yifu_jx", "jiaju_dunhuang", "yifu_ccgb", "yifu_syzp")   # game_tables.TRAITS


def face_presets(library, faces, files) -> list[dict]:
    """
    The face presets the game shows (role_face, in its order): {key "<folder>|<shape .mb or ''>", name, icon}, named
    and pictured by their own item ("Basic Face Shape-5", image/face/faces/<face>_<shape>_192.png). Then the faces
    and shape files it never offers, so they stay reachable.
    """
    presets, seen = [], set()

    def face_picture(stem):
        """The face list's picture of a face or face shape (image/face/faces/<stem>_192), if the game has one."""
        path = f"image/face/faces/{stem}_192.png"
        return path if library.index.locate(path.replace("/", "\\")) is not None else None

    folder_of = {folder.rsplit("\\", 1)[-1].lower(): folder for folder in faces}
    shape_file = {}
    for folder in faces:
        for path in files.get(folder, []):
            if path.lower().endswith(".mb"):
                shape_file[(folder, path.rsplit("\\", 1)[-1][:-3].rsplit("_", 1)[-1])] = path
    try:
        tables = library.tables
        models = {row["id"]: row.get("face_model") for row in tables.keyed_rows("face_shape").values()}
        english = {key: row.get("text_en", "") for key, row in tables.keyed_rows("item_text_os_en").items()
                   if isinstance(row, dict)}
        item_of = {}
        for item_id, row in sorted(tables.keyed_rows("item").items(), key=lambda kv: str(kv[0])):
            if isinstance(row, dict) and row.get("type") == 200 and row.get("mini_type"):
                item_of.setdefault(row["mini_type"], row)
        rows = sorted(tables.keyed_rows("role_face").values(),
                      key=lambda r: (r.get("sex", 0), r.get("hide_in_ui", 0), r.get("sort", 0), r.get("id", 0)))
    except Exception:
        rows, models, english, item_of = [], {}, {}, {}
    for row in rows:
        folder = folder_of.get(str(models.get(row.get("shape_id")) or "").lower())
        if folder is None:
            continue
        suffix = str(row.get("bone_suffix", ""))
        shape = shape_file.get((folder, suffix), "") if suffix.strip("0") else ""
        if suffix.strip("0") and not shape:
            continue                                   # the shape file is not in this install
        key = f"{folder}|{shape}"
        if key in seen:
            continue
        seen.add(key)
        face = folder.rsplit("\\", 1)[-1]
        stem = f"{face}_{suffix}" if shape else face
        item = item_of.get(row.get("id")) or {}
        title = english.get(item.get("name", ""), "")
        icon = item.get("icon_path")
        if icon and icon.endswith("pic_item_face.png"):
            icon = face_picture(stem)                  # the generic item icon: use the face's own picture
        rarity = item.get("rarity") if item.get("rarity") in (1, 2, 3, 4, 5) else None
        traits = tuple(key for key in TRAIT_KEYS if key in set(item.get("item_tags") or ()))
        presets.append({"key": key, "name": f"{title}  ·  {stem}" if title else stem, "icon": icon, "rarity": rarity,
                        "traits": traits, "tags": tuple(str(t) for t in (item.get("item_tags") or ()))})
    # Faces with no preset in the game are added as plain base faces. Their extra shape files are left out, since
    # the game never uses them.
    for folder in faces:
        face = folder.rsplit("\\", 1)[-1]
        if f"{folder}|" not in seen:
            presets.append({"key": f"{folder}|", "name": f"{face} (base face)", "icon": face_picture(face)})
            seen.add(f"{folder}|")
    return presets


def choice_key(path, texture):
    return f"{path}|{texture or ''}"


def dress_hair_items(items, hair, dyed):
    """Copies of the hair items wearing the dyed texture (when a dye is chosen)."""
    output = []
    for source in items:
        item = dict(source, parts=[dict(part) for part in source["parts"]])
        if dyed is not None:
            stem = hair["folder"].rsplit("\\", 1)[-1]
            for part in item["parts"]:
                texture = str(part.get("texture") or "")
                if not texture or texture.lower().startswith(hair["folder"].lower()):
                    part["image"] = dyed
                    part["texture"] = f"{stem}_dyed.png"
        output.append(item)
    return output


def _wardrobe_choice(choice) -> tuple:
    """(path, texture, sockets) from a path, (path, texture) or (path, texture, sockets)."""
    if not isinstance(choice, tuple):
        return choice, None, ()
    texture = choice[1] if len(choice) > 1 else None
    sockets = choice[2] if len(choice) > 2 else ()
    return choice[0], texture, tuple(sockets or ())


def face_folder_of(path: str | None) -> str | None:
    """The face folder (...\\body\\<face>) a Characters-tab path is in, or None."""
    if not path:
        return None
    lower = str(path).lower()
    for root in BODY_FOLDERS:
        if lower.startswith(root):
            rest = str(path)[len(root):].split("\\")
            if rest and rest[0] and re.search(r"_body_\d+$", rest[0].lower()):
                return str(path)[:len(root)] + rest[0]
    return None


def without_old_eye_faces(mesh_paths):
    """
    Leave out <face>_all_jr.mesh when the folder has its plain face mesh: male_body_01 / 02 and female_body_01
    keep an older copy of the face with one-piece eyeballs built in (painted from the face texture), which
    would cover the separate eyes and their iris texture.
    """
    lowered = {p.lower() for p in mesh_paths}
    kept = []
    for path in mesh_paths:
        lower = path.lower()
        if lower.endswith("_all_jr.mesh"):
            plain = lower[:-len("_all_jr.mesh")]
            if plain + ".mesh" in lowered or plain + "_all.mesh" in lowered:
                continue
        kept.append(path)
    return kept


def load_face_items(library, mesh_paths, files, shape_path, report, progress=None, cancelled=None):
    """The face folder's meshes as export items (face, eyes, lashes, eyebrows), in the chosen face shape."""
    from core.mesh_converter import face_shapes
    from core.npk.stage_export import StageLoader
    from gui.widgets.character_exports import textured_model
    metadata = [p for p in files if p.lower().endswith((".gim", ".mtg", ".mvar"))]
    mesh_paths = without_old_eye_faces(mesh_paths)
    loader = StageLoader(library.index, max_texture_size=8192)
    items = []
    for number, path in enumerate(mesh_paths):
        if cancelled is not None and cancelled():
            raise InterruptedError
        if progress is not None:
            progress(f"Loading {ntpath.basename(path)}", number, len(mesh_paths) + 2)
        model = textured_model(loader, path, metadata)
        if model is None:
            report.append(f"{ntpath.basename(path)}: not readable")
            continue
        mesh = model[0]
        if re.search(r"_body_\d+(_all)?\.mesh$", path.lower()):
            mesh = face_shapes.mirror_normal_fix(mesh)
        items.append({"name": path.rsplit("\\", 1)[-1].rsplit(".", 1)[0], "mesh": mesh,
                      "parts": [dict(part) for part in model[1]]})
    # The face mesh is listed twice in many folders (<face>.mesh and <face>_all.mesh): keep one
    seen, unique = set(), []
    for item in items:
        key = (len(item["mesh"].position), len(item["mesh"].face), item["name"].replace("_all", ""))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    items = unique
    if not items:
        raise ValueError("No meshes could be loaded for this face.")
    if shape_path:
        found = library.index.read(shape_path)
        if found is not None:
            bones = face_shapes.shape_by_name(face_shapes.read_mb(found[0]),
                                              face_shapes.reference_mesh([i["mesh"] for i in items]))
            for item in items:
                if re.search(r"_eye_(l|r|jyq)_", item["name"].lower()):
                    item["mesh"] = face_shapes.apply_shape_to_eyes(item["mesh"], bones)
                else:
                    item["mesh"] = face_shapes.apply_shape(item["mesh"], bones)
    report.extend(line for line in loader.report if "material taken from" not in line)
    return items


_HALVES: dict[int, tuple] = {}


def _halves(mesh):
    """The left/right halves of a face mesh, made once per mesh (so redraws reuse them)."""
    from core.npk import face_makeup
    found = _HALVES.get(id(mesh))
    if found is None or found[0] is not mesh:
        if len(_HALVES) > 32:
            _HALVES.clear()
        found = _HALVES[id(mesh)] = (mesh, face_makeup.split_by_side(mesh))
    return found[1]


def dress_face_items(library, items, sex, face, look, baked, eye, brow):
    """
    Copies of the face items wearing the look: the baked face texture (split into left/right halves
    when the look has a one-cheek decal), the iris and eyebrow textures. Returns (items, eye meshes dressed).
    """
    from core.npk import face_makeup
    sides = tuple(baked)
    output, eye_meshes = [], 0
    # Faces have either old one-piece eyeballs (<sex>_eye_jyq_NN) or separate left/right eyes. Prefer the
    # separate ones (the old eyeballs don't fit reshaped faces); keep the old ones only if there's nothing else.
    has_split = any(re.search(r"_eye_(l|r)_", i["name"].lower()) for i in items)
    has_old = any("_eye_jyq_" in i["name"].lower() for i in items)
    for source in items:
        lower_name = source["name"].lower()
        if has_split and "_eye_jyq_" in lower_name:
            continue
        if not has_split and has_old and re.search(r"_eye_(l|r)_", lower_name):
            continue
        item = dict(source, parts=[dict(part) for part in source["parts"]])
        lower = item["name"].lower()
        # The face, and anything else drawn with the face texture (the old one-piece eyeballs _eye_jyq_)
        is_face = any(FACE_TEXTURE.match(os.path.basename(str(part.get("texture") or "")).rsplit(".", 1)[0].lower())
                      for part in item["parts"]) or (lower.startswith(f"{sex}_body_") and "eye" not in lower)
        if "eyebrow" in lower and look.eyebrow is not None:
            for part in item["parts"]:
                if brow is not None:
                    part["image"] = brow
                    part["texture"] = f"{face}_makeup_eyebrow.png"
                else:
                    part["image"] = library.tint_eyebrow_texture(part.get("image"), look.eyebrow)
        if eye is not None and re.search(r"_eye_(l|r)_", lower):
            eye_meshes += 1
            for part in item["parts"]:
                part["image"] = eye
                part["texture"] = f"{face}_makeup_eye.png"
                part["material"] = f"{part.get('material') or 'eye'}_makeup"
        if not is_face:
            output.append(item)
            continue
        if sides == (None,):
            for part in item["parts"]:
                part["image"] = baked[None]
                part["texture"] = f"{face}_makeup.png"
                part["material"] = f"{part.get('material') or face}_makeup"
            output.append(item)
            continue
        for side, half in zip(("left", "right"), _halves(item["mesh"])):
            parts = [dict(part, image=baked[side], texture=f"{face}_makeup_{side}.png",
                          material=f"{part.get('material') or face}_makeup_{side}")
                     for part in item["parts"]]
            output.append({"name": f"{item['name']}_{side}", "mesh": half, "parts": parts})
    output += pupil_items(library, look, [i for i in output if re.search(r"_eye_(l|r)_", i["name"].lower())],
                          sex, face)
    return output, eye_meshes


_PUPIL_MODELS: dict = {}
_FACE_SOCKETS: dict = {}


def face_sockets(library, sex, face) -> dict:
    """Socket name -> (bone, MatrixToBone) from the face's <face>_all.gim (the pupil sockets tongkong_l / _r)."""
    import xml.etree.ElementTree as ET
    key = (sex, face)
    if key not in _FACE_SOCKETS:
        found = {}
        path = f"{BODY_FOLDERS[sex == 'female']}{face}\\{face}_all.gim"
        data = library.index.read(path)
        try:
            sockets = ET.fromstring(data[0]).find("Sockets") if data is not None else None
        except ET.ParseError:
            sockets = None
        for socket in (sockets if sockets is not None else []):
            try:
                matrix = np.array([float(v) for v in socket.get("MatrixToBone").strip("[]").split(",")]).reshape(4, 4)
            except (AttributeError, ValueError):
                continue
            found[socket.get("Name")] = (socket.get("BoneName") or "", matrix)
        _FACE_SOCKETS[key] = found
    return _FACE_SOCKETS[key]


def pupil_items(library, look, eyes, sex=None, face=None) -> list:
    """
    The small model some iris patterns hang on the pupils (face_eyetype.eye_bind_model, e.g. Stellar Gaze's star
    tongkong_02), one per eye, on the sockets the row names (eye_bind_param: tongkong_l / tongkong_r). The face's
    <face>_all.gim hangs those on the eye bones; the model goes on the eye bone, so it follows the eye and the
    face shape. Faces without the sockets get it at the front centre of the eye mesh.
    """
    path = (look.eyetype.row.get("eye_bind_model") if look.eyetype is not None else None) or ""
    if not path or not eyes:
        return []
    mesh_path = path.replace("/", "\\").rsplit(".", 1)[0] + ".mesh"
    if mesh_path not in _PUPIL_MODELS:
        try:
            _PUPIL_MODELS[mesh_path] = load_face_items(library, [mesh_path], [], None, [])
        except Exception:
            _PUPIL_MODELS[mesh_path] = []
    models = _PUPIL_MODELS[mesh_path]
    if not models:
        return []
    import copy
    sockets = face_sockets(library, sex, face) if sex and face else {}
    wanted = [n for n in (look.eyetype.row.get("eye_bind_param") or [])[1:] if isinstance(n, str)]
    output = []
    for eye in eyes:
        mesh = eye["mesh"]
        points = np.asarray(mesh.position, float)
        if not len(points):
            continue
        side = "l" if "_eye_l_" in eye["name"].lower() else "r"
        names = list(mesh.bone_name or [])
        socket = next((sockets[n] for n in wanted + [f"tongkong_{side}"]
                       if n.endswith(f"_{side}") and n in sockets), None)
        place, bone = None, None
        if socket is not None and socket[0] in names and mesh.bone_matrix:
            bone = names.index(socket[0])
            world = np.asarray(mesh.bone_matrix[bone], float).copy()
            scales = np.linalg.norm(world[:3, :3], axis=1, keepdims=True)
            world[:3, :3] *= float(scales.min()) / np.maximum(scales, 1e-9)     # evenly sized, as the eye itself
            place = socket[1] @ world
        else:
            middle = points.mean(axis=0)
            near = np.hypot(points[:, 0] - middle[0], points[:, 1] - middle[1])
            candidates = np.where(near <= max(near.min() * 4, 0.05))[0]
            front = candidates[np.argmax(points[candidates, 2])]
        for model in models:
            source = model["mesh"]
            placed = copy.copy(source)
            local = np.asarray(source.position, float)
            if place is not None:
                placed.position = [tuple(p) for p in (np.c_[local, np.ones(len(local))] @ place)[:, :3]]
                linear = place[:3, :3]
                if source.normal and len(source.normal) == len(local):
                    normals = np.asarray(source.normal, float) @ np.linalg.inv(linear).T
                    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-9)
                    placed.normal = [tuple(n) for n in normals]
                if np.linalg.det(linear) < 0:
                    placed.face = [(a, c, b) for a, b, c in source.face]
                skin = ([(bone, 0, 0, 0)], [(1.0, 0.0, 0.0, 0.0)])
            else:
                offset = local - np.array([local[:, 0].mean(), local[:, 1].mean(), local[:, 2].max()])
                anchor = np.array([middle[0], middle[1], points[front, 2] + 0.004])
                placed.position = [tuple(p) for p in offset + anchor]
                skin = None
                if mesh.vertex_bone and len(mesh.vertex_bone) == len(points):
                    skin = ([tuple(mesh.vertex_bone[front])], [tuple(mesh.vertex_weight[front])])
            placed.bone_name = names
            placed.bone_parent = list(mesh.bone_parent or [])
            placed.bone_matrix = list(mesh.bone_matrix or [])
            if skin is not None:
                placed.vertex_bone = skin[0] * len(local)
                placed.vertex_weight = skin[1] * len(local)
            try:
                placed.bone_count = len(placed.bone_name)
                placed.bone_exist = 1 if placed.bone_name else 0
            except AttributeError:
                pass
            parts = [dict(part, alpha="BLEND") for part in model["parts"]]
            output.append({"name": f"{model['name']}_{side}", "mesh": placed, "parts": parts})
    return output
