"""
Character face makeup, baked into the face texture the way the game's face shader (shader/cartoon_face_common.hlsl)
draws it at runtime.

The game keeps the face texture plain and layers the makeup on in the shader:
  skin tone   the face's .mvar variants: another base texture (old tones 1-8) or base + BaseFaceColorTintMap
              tinted with material_plus/new_skin_data.json (new tones 9+)
  lips        TintColorB through the face's tint mask (<face>_eyes_NN: blue = lips, alpha = eyebrows),
              colours in material_plus/mouth_color_data.json (face_lips.lips_color)
  eyebrows    TintColorA through the same mask, material_plus/eyebrow_color_data.json (face_eyebrow)
  blush/decal FaceMaskTex2: a small image stamped in UV space (centre SX/SY, width W, height W*HW_Ratio),
              material_plus/blush_color / face_decal_to_blush / old_face_decal_to_blush _data.json
  eyeshadow   FaceMaskTex: same, placement per face in eye_shadow_data.json, image named by face_edit.res_path
Brightness of both stamps follows the skin tone (bright_face_tex / bright_face2_tex).

Face UVs are mirrored (both cheeks share the texture), but some stamps are for one side only
(FaceMaskOnlyLeft/Right): such looks bake one texture per side, and the face is split in two materials.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field

import numpy as np

SEXES = {"male": 1, "female": 2}
BLUSH_TYPES = ("blush_color", "face_decal_to_blush", "old_face_decal_to_blush")
TRAIT_KEYS = ("yifu_qd", "yifu_zc", "yifu_jx", "jiaju_dunhuang", "yifu_ccgb", "yifu_syzp")   # game_tables.TRAITS
ITEM_SUB_TYPES = {"decal": 15, "blush": 28, "lips": 20, "eyebrow": 26, "eyecolor": 18, "eyetype": 22}


@dataclass
class Option:
    kind: str                 # eyeshadow / blush / lips / eyebrow
    id: int
    name: str
    icon: str | None
    row: dict = field(default_factory=dict)
    rarity: int | None = None  # the item's rarity, 1 Basic ... 5 Mythic
    traits: tuple = ()         # the item's filter traits (game_tables.TRAITS keys)
    tags: tuple = ()           # the item's tags as the game shows them (item.item_tags)


@dataclass
class Look:
    skin: int | None = None           # .mvar variant number; None keeps the face's own texture
    eyeshadow: Option | None = None
    blush: Option | None = None
    lips: Option | None = None
    eyebrow: Option | None = None
    eyetype: Option | None = None      # eye meshes (*_eye_l / *_eye_r): iris pattern
    eyecolor: Option | None = None     # eye meshes: iris colour


def srgb_to_linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def hard_light(base, blend):
    return np.where(blend < 0.5, 2 * base * blend, 1 - 2 * (1 - base) * (1 - blend))


def blend_color(src, dst, blend_type):
    return hard_light(src, dst) if blend_type == 0 else src * dst


def face_number(folder: str) -> int | None:
    found = re.search(r"_body_(\d+)", folder.replace("/", "\\").rstrip("\\").rsplit("\\", 1)[-1])
    return int(found.group(1)) if found else None


def face_sex(folder: str) -> str:
    name = folder.replace("/", "\\").rstrip("\\").rsplit("\\", 1)[-1].lower()
    return "female" if name.startswith("female") or "\\girl\\" in folder.lower() else "male"


class MakeupLibrary:
    """Makeup options and data from the game files; `index` reads game files, `tables` the root packs."""

    def __init__(self, index, tables):
        self.index = index
        self.tables = tables
        self._json: dict[str, dict] = {}
        self._rows: dict[str, dict] = {}
        self._images: dict[str, object] = {}
        self._english = None
        self._items = None
        self._manifest = None

    # -- data --------------------------------------------------------------------------------
    def mp(self, name: str) -> dict:
        if name not in self._json:
            data = self.tables.read(f"material_plus\\{name}_data.json")
            self._json[name] = json.loads(data.decode("utf-8")) if data else {}
        return self._json[name]

    def rows(self, table: str) -> list[dict]:
        if table not in self._rows:
            self._rows[table] = self.tables.keyed_rows(table)
        return list(self._rows[table].values())

    def _names(self):
        if self._items is None:
            self._english = {key: row.get("text_en", "") for key, row in self.tables.keyed_rows("item_text_os_en").items()
                             if isinstance(row, dict)}
            self._items = {}
            for row in self.tables.keyed_rows("item").values():
                if isinstance(row, dict) and row.get("type") == 200 and row.get("sub_type") in ITEM_SUB_TYPES.values():
                    self._items.setdefault((row["sub_type"], row.get("mini_type")), row)
        return self._items, self._english

    def _label(self, sub_type, mini_type, fallback):
        items, english = self._names()
        item = items.get((sub_type, mini_type))
        name = english.get(item.get("name"), "") if item else ""
        icon = item.get("icon_path") if item else None
        return (name.strip() or fallback), icon

    def image(self, path: str | None):
        """RGBA float32 array (0..1) of a game texture, or None."""
        from core.npk.stage_export import decode_texture  # pylint: disable=import-outside-toplevel
        if not path:
            return None
        path = path.replace("/", "\\")
        if path not in self._images:
            found = self.index.read(path)
            image = None
            if found is not None:
                try:
                    image = np.asarray(decode_texture(found[0]).convert("RGBA"), np.float32) / 255.0
                except Exception:  # pylint: disable=broad-except
                    image = None
            self._images[path] = image
        return self._images[path]

    def icon(self, option: Option):
        """PIL image of an option's icon, or None."""
        from core.npk.stage_export import decode_texture  # pylint: disable=import-outside-toplevel
        for path in filter(None, [option.icon]):
            found = self.index.read(path.replace("/", "\\"))
            if found is not None:
                try:
                    return decode_texture(found[0])
                except Exception:  # pylint: disable=broad-except
                    return None
        return None

    # -- options -----------------------------------------------------------------------------
    def options(self, sex: str) -> dict[str, list[Option]]:
        s = SEXES[sex]
        result = {"eyeshadow": [], "blush": [], "lips": [], "eyebrow": [], "eyetype": [], "eyecolor": []}
        for row in sorted(self.rows("face_edit"), key=lambda r: (r.get("sort", 0), r.get("id", 0))):
            if row.get("sex") != s:
                continue
            kind = "eyeshadow" if row.get("mp_type") == "eye_shadow" else "blush" if row.get("mp_type") in BLUSH_TYPES else None
            if kind is None:
                continue
            fallback = str(row.get("res_path", "")).rsplit("/", 1)[-1] or f"#{row['id']}"
            first, second = (ITEM_SUB_TYPES["blush"], ITEM_SUB_TYPES["decal"]) if row.get("mp_type") == "blush_color"                 else (ITEM_SUB_TYPES["decal"], ITEM_SUB_TYPES["blush"])
            name, icon = self._label(first, row["id"], fallback)
            if name == fallback:
                name, icon = self._label(second, row["id"], fallback)
            if name == fallback:
                name = _decal_label(fallback)
            if icon is None or "maozi" in str(icon):
                icon = f"image/face/face_detail/{row.get('icon_path')}.png" if row.get("icon_path") else None
            result[kind].append(Option(kind, row["id"], name, icon, row))
        for row in sorted(self.rows("face_lips"), key=lambda r: (r.get("sort", 0), r.get("id", 0))):
            if row.get("sex") == s:
                name, icon = self._label(ITEM_SUB_TYPES["lips"], row["id"], f"Lip colour {row.get('lips_color')}")
                result["lips"].append(Option("lips", row["id"], name, icon or f"image/face/lips/{row.get('icon')}.png", row))
        for row in sorted(self.rows("face_eyebrow"), key=lambda r: (r.get("sort", 0), r.get("id", 0))):
            if row.get("sex") == s:
                fallback = self.mp("eyebrow_color").get(str(row.get("eyebrow_color")), [f"Brow colour {row.get('eyebrow_color')}"])[0]
                name, icon = self._label(ITEM_SUB_TYPES["eyebrow"], row["id"], fallback)
                result["eyebrow"].append(Option("eyebrow", row["id"], name, icon or f"image/face/eyebrow/{row.get('icon')}.png", row))
        masks, colors = self.mp("eye_mask_split"), self.mp("eye_color_split")
        for row in sorted(self.rows("face_eyetype"), key=lambda r: (r.get("sort", 0), r.get("id", 0))):
            if row.get("sex") == s and not row.get("hide_in_ui") and str(int(row.get("eye_mask_mat") or 1)) in masks:
                name, icon = self._label(ITEM_SUB_TYPES["eyetype"], row["id"], f"Eye type {row.get('eye_mask_mat')}")
                result["eyetype"].append(Option("eyetype", row["id"], name,
                                                icon or f"image/face/eyes/{row.get('icon')}.png", row))
        for row in sorted(self.rows("face_eyecolor"), key=lambda r: (r.get("sort", 0), r.get("id", 0))):
            if row.get("sex") == s and not row.get("hide_in_ui") and str(row.get("eye_color")) in colors:
                name, icon = self._label(ITEM_SUB_TYPES["eyecolor"], row["id"], f"Eye colour {row.get('eye_color')}")
                result["eyecolor"].append(Option("eyecolor", row["id"], name,
                                                 icon or f"image/face/eyes/eyescolor/{row.get('icon')}.png", row))
        items, _english = self._names()
        sub_types = {"eyeshadow": (ITEM_SUB_TYPES["decal"], ITEM_SUB_TYPES["blush"]),
                     "blush": (ITEM_SUB_TYPES["blush"], ITEM_SUB_TYPES["decal"]),
                     **{kind: (ITEM_SUB_TYPES[kind],) for kind in ("lips", "eyebrow", "eyetype", "eyecolor")}}
        for kind, options in result.items():
            for option in options:
                item = next((items[(sub, option.id)] for sub in sub_types[kind] if (sub, option.id) in items), None)
                rarity = item.get("rarity") if item else None
                option.rarity = rarity if rarity in (1, 2, 3, 4, 5) else None
                tags = set(item.get("item_tags") or ()) if item else set()
                option.traits = tuple(key for key in TRAIT_KEYS if key in tags)
                option.tags = tuple(str(t) for t in (item.get("item_tags") or ())) if item else ()
        return result

    # -- eyes (shader/cartoon_eye_common.hlsl) ------------------------------------------------
    def bake_eye(self, look: Look, report: list | None = None):
        """
        The iris texture of the separate eye meshes (*_eye_l / *_eye_r) for the look's eye type and colour,
        or None when the look sets neither. Animated iris patterns are baked at their first frame.
        """
        from PIL import Image  # pylint: disable=import-outside-toplevel
        if look.eyetype is None and look.eyecolor is None:
            return None
        report = report if report is not None else []
        masks = self.mp("eye_mask_split")
        mask_key = str(int(look.eyetype.row.get("eye_mask_mat") or 1)) if look.eyetype is not None else "1"
        mask = masks.get(mask_key) or masks.get("1")
        color_key = "1"
        if look.eyecolor is not None:
            color_key = str(look.eyecolor.row.get("eye_color"))
        else:
            defaults = look.eyetype.row.get("default_eyecolor") or []
            for row in self.rows("face_eyecolor"):
                if defaults and row.get("id") == defaults[0]:
                    color_key = str(row.get("eye_color"))
        color = self.mp("eye_color_split").get(color_key)
        if not mask or not color:
            report.append("eye type / colour data not found")
            return None
        base = self.image(mask[10])
        if base is None:
            report.append(f"eye texture not found: {mask[10]}")
            return None
        height, width = base.shape[:2]
        v, u = np.mgrid[0:height, 0:width]
        u = (u + 0.5) / width
        v = (v + 0.5) / height
        albedo = srgb_to_linear(base[..., :3].astype(np.float64))
        tint_mask = self.image(mask[12])
        tint_mask = _resize(tint_mask, width, height) if tint_mask is not None else np.zeros((height, width, 4))
        coloration = mask[1:5]
        # R, G, B: TintColor + blend + gradient; A: the default black tint (pupil) with its gradient
        channels = [(13, 14, 15, 16, 17, 18, 19, 20, 21), (22, 23, 24, 25, 26, 27, 28, 29, 30),
                    (31, 32, 33, 34, 35, 36, 37, 38, 39), (None, 40, 41, 42, 43, 44, 45, 46, 47)]
        src = albedo
        for channel, (tint_i, blend_i, gtype_i, gdir_i, grange_i, gsoft_i, gcolor_i, gx_i, gy_i) in enumerate(channels):
            if not coloration[channel]:
                continue
            tint = np.asarray(color[tint_i][:3] if tint_i is not None else [0.0, 0.0, 0.0], float)
            tint = _gradient(tint, u, v, color[gtype_i], color[grange_i], color[gsoft_i],
                             np.asarray(color[gcolor_i][:3], float), color[gdir_i], (color[gx_i], color[gy_i]))
            src = src + (blend_color(src, tint, color[blend_i]) - src) * tint_mask[..., channel:channel + 1]
        albedo = src                                             # TintAlbedoPower 0, TintAlbedoFactor 1
        pattern = self.image(mask[14])                           # dynamic iris patterns, first frame
        if pattern is not None:
            ring = self.image(mask[15])
            ring = _resize(ring, width, height) if ring is not None else np.ones((height, width, 4))
            for channel in range(4):
                if not mask[5 + channel]:
                    continue
                dtype, tilling, tilling2, _speed, offset_x, offset_y = mask[20 + channel * 6:26 + channel * 6]
                pu, pv = _pattern_uv(dtype, u, v, tilling, tilling2, (offset_x, offset_y))
                amount = _sample(pattern, pu, pv)[..., channel]
                mask_type = int(mask[44 + channel] or 0)
                donut = ring[..., mask_type - 1] if 1 <= mask_type <= 4 else 1.0
                pattern_color = np.asarray(color[3 + channel * 3][:3], float)
                blend_type = int(color[2 + channel * 3] or 0)
                weight = (amount * donut)[..., None]
                if blend_type == 0:
                    target = hard_light(albedo, pattern_color)
                elif blend_type == 1:
                    target = albedo * pattern_color
                else:
                    target = np.broadcast_to(pattern_color, albedo.shape)
                albedo = albedo + (target - albedo) * weight
        out = np.dstack([linear_to_srgb(albedo), base[..., 3:4]])
        return Image.fromarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8), "RGBA")

    # -- per face ----------------------------------------------------------------------------
    def face_files(self, folder: str) -> list[str]:
        prefix = folder.lower().rstrip("\\") + "\\"
        if self._manifest is None:
            self._manifest = [p for p in self.index.manifest() if p.lower().endswith((".mvar", ".tga", ".dds", ".png"))
                              and "\\body\\" in p.lower()]
        return [p for p in self._manifest if p.lower().startswith(prefix) and "\\" not in p[len(prefix):]]

    def skins(self, folder: str) -> list[tuple[int, str, str, str | None]]:
        """(variant number, label, base texture, tint map or None) from the face's .mvar."""
        # The face's standard list is <face>_all.mvar; a few faces also have another .mvar in a different layout
        mvars = [p for p in self.face_files(folder) if p.lower().endswith(".mvar")]
        mvar = next((p for p in mvars if p.lower().endswith("_all.mvar")), mvars[0] if mvars else None)
        found = self.index.read(mvar) if mvar else None
        if found is None:
            return []
        try:
            data = json.loads(found[0].decode("utf-8"))
        except ValueError:
            return []
        material = next(iter(data.values()), {})
        result = []
        for number, (label, values) in sorted(((int(k), v) for k, v in material.get("variants", {}).items()), key=lambda kv: kv[0]):
            if number == 0:
                continue
            base, tint = values.get("0"), values.get("1")
            if not isinstance(base, str) or not base:
                continue
            if not isinstance(tint, str) or "black_with_alpha" in tint:
                tint = None
            result.append((number, _skin_label(label, number), base, tint))
        return result

    def tint_mask(self, folder: str, sex: str):
        """The face's lip/eyebrow tint mask (AlbedoTintMaskTex, default eye type 1), from eye_mask_data.json."""
        number = face_number(folder)
        if number is None:
            return None
        path = (self.mp("eye_mask").get(f"1{SEXES[sex]}{number:04d}") or [None, None, ""])[2]
        candidates = [path] if path else []
        stem = f"{sex}_body_{number:02d}"
        candidates += [f"{folder}\\{stem}_eyes_01.tga"]
        for candidate in candidates:
            image = self.image(candidate)
            if image is not None:
                return image
        return None

    def eyebrow_texture(self, folder: str, sex: str) -> str | None:
        """The separate eyebrow mesh's own texture (...\\<sex>_eyebrow_NN_h.dds)."""
        pattern = re.compile(rf"{sex}_eyebrow_\d+_h\.(dds|tga|png)$")
        return next((p for p in self.face_files(folder) if pattern.search(p.lower())), None)

    def bake_eyebrow(self, folder: str, sex: str, look: Look, report: list | None = None):
        """
        The separate eyebrow mesh's texture recoloured as the game's eyebrow shader does
        (shader/cartoon_eyebrow_common2.hlsl): brightness/contrast, then four tints through the face's
        eyebrow mask (<face>_eyebrow_01, eyebrow_mask_split_data.json) with colours from
        eyebrow_color_split_data.json. None when the look has no eyebrow colour or the face has no eyebrow mesh.
        """
        from PIL import Image  # pylint: disable=import-outside-toplevel
        report = report if report is not None else []
        if look.eyebrow is None:
            return None
        number = face_number(folder)
        texture = self.eyebrow_texture(folder, sex)
        base = self.image(texture)
        if base is None or number is None:
            report.append("this face has no separate eyebrow mesh texture")
            return None
        label = f"1->{'男' if SEXES[sex] == 1 else '女'}->{number:02d}"
        mask_entry = next((v for v in self.mp("eyebrow_mask_split").values() if v and v[0] == label), None)
        color = self.mp("eyebrow_color_split").get(str(look.eyebrow.row.get("eyebrow_color")))
        if not mask_entry or not color:
            report.append("no eyebrow colour data for this face")
            return None
        mask = self.image(mask_entry[5]) if mask_entry[5] else None
        if mask is None:
            report.append("this face has no eyebrow colour mask in the game")
            return None
        height, width = base.shape[:2]
        mask = _resize(mask, width, height)
        v, u = np.mgrid[0:height, 0:width]
        u = (u + 0.5) / width
        v = (v + 0.5) / height
        contrast, brightness = (color[29], color[30]) if len(color) > 30 else (0.0, 1.0)
        albedo = srgb_to_linear(base[..., :3].astype(np.float64)) * brightness
        albedo = 0.5 + (albedo - 0.5) * (1.0 + contrast)
        src = albedo
        for channel in range(4):
            if not mask_entry[1 + channel]:
                continue
            first = 1 + channel * 7
            tint_c, blend, gtype, gdir, grange, gsoft, gcolor = color[first:first + 7]
            tint = _gradient(np.asarray(tint_c[:3], float), u, v, gtype, grange, gsoft, np.asarray(gcolor[:3], float), gdir, (0, 0))
            src = src + (blend_color(src, tint, blend) - src) * mask[..., channel:channel + 1]
        out = np.dstack([linear_to_srgb(src), base[..., 3:4]])          # TintAlbedoPower 0, TintAlbedoFactor 1
        return Image.fromarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8), "RGBA")

    def tint_eyebrow_texture(self, image, option: Option):
        """
        Eyebrow colour on a separate eyebrow mesh's texture (PIL), approximated with the face shader's
        eyebrow tint (the game's split-eyebrow shader recolours by mask channels).
        """
        from PIL import Image  # pylint: disable=import-outside-toplevel
        values = self.mp("eyebrow_color").get(str(option.row.get("eyebrow_color")))
        if image is None or not values:
            return image
        _label, _lut, tint_a, type_a, desaturate, contrast, intensity = values[:7]
        rgba = np.asarray(image.convert("RGBA"), np.float64) / 255.0
        albedo = srgb_to_linear(rgba[..., :3])
        base = albedo
        if desaturate:
            luma = (albedo * [0.2125, 0.7145, 0.0721]).sum(-1, keepdims=True)
            base = np.repeat(np.clip(np.clip(np.power(luma + 0.5, contrast) - 0.5, 0, 1) * intensity, 0, 1), 3, -1)
        out = np.dstack([linear_to_srgb(blend_color(base, np.asarray(tint_a[:3]), type_a)), rgba[..., 3:4]])
        return Image.fromarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8), "RGBA")

    def scheme(self, row: dict, number: int) -> int:
        solution = row.get("special_solution") or {}
        return int(solution.get(number, solution.get(str(number), 1)) or 1)

    def stamps(self, look: Look, folder: str, sex: str) -> list[dict]:
        """The blush/decal stamp (FaceMaskTex2) then the eyeshadow (FaceMaskTex), as the shader draws them."""
        s, number = SEXES[sex], face_number(folder) or 1
        skin = look.skin or 1
        stamps = []
        if look.blush is not None:
            row = look.blush.row
            kind, decal = row.get("mp_type"), int(row.get("mp_decal_id") or 0)
            if kind == "old_face_decal_to_blush":
                values = self.mp(kind).get(str(decal))
                if values:
                    _label, _on, tex, left, right, sx, sy, w, hw, bright = values[:10]
                    stamps.append(dict(tex=tex, left=left, right=right, sx=sx, sy=sy, w=w, hw=hw, bright=bright,
                                       blend=False, clip=False))
            else:
                data = self.mp(kind)
                values = data.get(f"{decal}{s:03d}{self.scheme(row, number):02d}") or data.get(f"{decal}{s:03d}01")
                if values:
                    _label, tex, left, right, sx, sy, w, hw, _on, blend, clip = values[:11]
                    bright = self.mp("bright_face2_tex").get(str(skin), [None, 1.0])[1]
                    stamps.append(dict(tex=tex, left=left, right=right, sx=sx, sy=sy, w=w, hw=hw, bright=bright,
                                       blend=bool(blend), clip=bool(clip)))
        if look.eyeshadow is not None:
            # Keyed by position in the game's face list, which skips numbers (no male 30), so match the label
            label = f"{'男' if s == 1 else '女'}->{number:02d}"
            values = next((v for v in self.mp("eye_shadow").values() if v and v[0] == label), None)
            if values:
                _label, _enable, left, right, sx, sy, w, hw, _macro, blend = values[:10]
                res = str(look.eyeshadow.row.get("res_path", "")).replace("/", "\\")
                bright = self.mp("bright_face_tex").get(str(skin), [None, look.eyeshadow.row.get("bright", 1.0)])[1]
                stamps.append(dict(tex=f"model\\texture\\tiehua\\{res}.png", left=left, right=right,
                                   sx=sx, sy=sy, w=w, hw=hw, bright=bright, blend=bool(blend), clip=False))
        return stamps

    def needs_split(self, look: Look, folder: str, sex: str) -> bool:
        return any(stamp["left"] or stamp["right"] for stamp in self.stamps(look, folder, sex))

    # -- baking ------------------------------------------------------------------------------
    def bake(self, folder: str, sex: str, look: Look, base_texture: str | None = None, side: str | None = None,
             report: list | None = None):
        """
        Returns the face texture to user with the makeup baked in. side can be None for both cheeks, or "left" / "right"
        to apply cheek stamps to one side only ("left" is the face's +X half).
        """
        from PIL import Image  # pylint: disable=import-outside-toplevel
        report = report if report is not None else []
        s, number = SEXES[sex], face_number(folder) or 1
        skins = {n: (base, tint) for n, _label, base, tint in self.skins(folder)}
        base_path, tint_path = base_texture, None
        if look.skin is not None and look.skin in skins:
            base_path, tint_path = skins[look.skin]
        base = self.image(base_path)
        if base is None:
            raise ValueError(f"face texture not found: {base_path}")
        height, width = base.shape[:2]
        rgb = base[..., :3].astype(np.float64)

        # New skin tones: BaseFaceColorTintMap (GetAlbedo, BASE_FACE_COLOR_ADJUST)
        if tint_path and look.skin is not None and look.skin >= 9:
            params = self.mp("new_skin").get(str(look.skin - 8))
            tint = self.image(tint_path)
            if params and tint is not None:
                _label, _macro, bright, power, tint_color = params[:5]
                tint = _resize(tint, width, height)
                adjusted = np.power(tint[..., :3], power) * bright * np.asarray(tint_color[:3])
                adjusted = np.clip(adjusted - 0.5 + 0.5, 0, 1)              # BaseFaceMapContrast 1
                rgb = np.where(tint[..., 3:4] > 0.0001, adjusted, rgb)
        albedo = srgb_to_linear(rgb)

        # Lips / eyebrows through the tint mask (LUT_TINT)
        if look.lips is not None or look.eyebrow is not None:
            mask = self.tint_mask(folder, sex)
            if mask is None:
                report.append("this face has no lip/eyebrow tint mask in the game, so lip and eyebrow colours do not apply to it")
            else:
                mask = _resize(mask, width, height)
                src, power, factor = albedo.copy(), 0.0, 1.0
                if look.lips is not None:
                    color = int(look.lips.row.get("lips_color") or 1)
                    data = self.mp("mouth_color")
                    values = (data.get(f"{s}{color:08d}{self.scheme(look.lips.row, number):03d}")
                              or data.get(f"{s}{color:08d}001"))
                    if values:
                        tint_b, type_b, power, factor = np.asarray(values[2][:3]), values[3], values[4], values[5]
                        src = src + (blend_color(src, tint_b, type_b) - src) * mask[..., 2:3]
                        src = self._lip_texture(src, values, width, height)
                if look.eyebrow is not None:
                    values = self.mp("eyebrow_color").get(str(look.eyebrow.row.get("eyebrow_color")))
                    if values:
                        _label, _lut, tint_a, type_a, desaturate, contrast, intensity = values[:7]
                        base_brow = src
                        if desaturate:
                            luma = (albedo * [0.2125, 0.7145, 0.0721]).sum(-1, keepdims=True)
                            base_brow = np.clip(np.clip(np.power(luma + 0.5, contrast) - 0.5, 0, 1) * intensity, 0, 1)
                            base_brow = np.repeat(base_brow, 3, -1)
                        src = src + (blend_color(base_brow, np.asarray(tint_a[:3]), type_a) - src) * mask[..., 3:4]
                albedo = src * np.power(np.maximum(albedo, 1e-6), power) * factor

        # Stamps (FaceMaskTex2 then FaceMaskTex)
        v, u = np.mgrid[0:height, 0:width]
        u = (u + 0.5) / width
        v = (v + 0.5) / height
        for stamp in self.stamps(look, folder, sex):
            if side is None and (stamp["left"] or stamp["right"]):
                pass                                      # a preview of both sides still shows it
            elif side == "left" and stamp["right"] and not stamp["left"]:
                continue
            elif side == "right" and stamp["left"] and not stamp["right"]:
                continue
            tex = self.image(stamp["tex"])
            if tex is None:
                report.append(f"makeup image not found: {stamp['tex']}")
                continue
            w, h = stamp["w"], stamp["w"] * stamp["hw"]
            su = (u - (stamp["sx"] - w * 0.5)) / w
            sv = (v - (stamp["sy"] - h * 0.5)) / h
            inside = (su >= 0) & (su <= 1) & (sv >= 0) & (sv <= 1)
            if not inside.any():
                continue
            th, tw = tex.shape[:2]
            px = np.clip((su * tw).astype(int), 0, tw - 1)
            py = np.clip((sv * th).astype(int), 0, th - 1)
            sample = tex[py, px]
            k = inside[..., None].astype(np.float64)
            color = sample[..., :3] * stamp["bright"]
            if not stamp["blend"]:
                color = albedo * (1 + (color - 1) * k)
            elif stamp["clip"]:
                color = albedo + (color - albedo) * k
            albedo = albedo + (color - albedo) * (sample[..., 3:4] * k)

        out = np.dstack([linear_to_srgb(albedo), base[..., 3:4]])
        return Image.fromarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8), "RGBA")

    def _lip_texture(self, src, values, width, height):
        """LIPS_BLEND_TEX: a lip texture over the mouth (most lip colours use an empty one)."""
        if len(values) < 22 or not values[16] or not values[17] or "black_with_alpha" in str(values[17]):
            return src
        tex = self.image(values[17])
        if tex is None:
            return src
        scale_u, scale_v, offset_u, offset_v = values[18:22]
        v, u = np.mgrid[0:height, 0:width]
        u = (u + 0.5) / width
        v = (v + 0.5) / height
        mu = u * scale_u + offset_u * 0.01
        sv = scale_v * 0.01
        mv = (v - (offset_v - sv * 0.5)) / sv
        inside = ((mu >= 0) & (mu <= 1) & (mv >= 0) & (mv <= 1))[..., None]
        th, tw = tex.shape[:2]
        sample = tex[np.clip((mv * th).astype(int), 0, th - 1), np.clip((mu * tw).astype(int), 0, tw - 1)]
        color = srgb_to_linear(sample[..., :3])
        return src + (color - src) * sample[..., 3:4] * inside


def _decal_label(res_name: str) -> str:
    """A readable name for a makeup image without an item name: female_blusher_hzxax -> Blusher hzxax."""
    name = re.sub(r"^(male|female)_", "", res_name)
    name = name.replace("queban", "freckles").replace("tiehua", "decal ").replace("_", " ").strip()
    return name[:1].upper() + name[1:]


def _skin_label(label: str, number: int) -> str:
    match = re.search(r"(\d+)$", label)
    n = match.group(1) if match else str(number)
    if label.startswith("旧肤色"):
        return f"Classic tone {n}"
    if label.startswith("新肤色"):
        return f"Tone {n}"
    if label.startswith("模拟老肤色"):
        return f"Classic-style tone {n}"
    return f"Tone {number}"


def _gradient(tint, u, v, gradient_type, gradient_range, soft, gradient_color, direction, offset):
    """GradientColorFunction: 1 = linear along `direction` degrees, 2 = from the centre (+offset)."""
    kind = int(np.floor(gradient_type or 0))
    if kind not in (1, 2):
        return tint
    if kind == 1:
        angle = np.radians(direction or 0)
        x, y = u - 0.5, v - 0.5
        d = np.clip((x * np.cos(angle) - y * np.sin(angle)) * 0.5 + 0.5, 0, 1)
    else:
        d = np.hypot(u - (0.5 + offset[0]), v - (0.5 + offset[1]))
    edge = max(float(soft or 0), 1e-6)
    t = np.clip((d - (gradient_range or 0)) / edge, 0, 1)
    t = (t * t * (3 - 2 * t))[..., None]
    return tint + (gradient_color - tint) * t


def _pattern_uv(kind, u, v, tilling, tilling2, offset):
    """DynamicPatternFunction at FrameTime 0."""
    kind = int(np.floor(kind or 0))
    tilling, tilling2 = float(tilling or 0), float(tilling2 or 0)
    pivot_u, pivot_v = 0.5 + (offset[0] or 0), 0.5 + (offset[1] or 0)
    if kind == 0:                                        # rotating: no rotation yet at time 0
        return np.clip(u * tilling - pivot_u * tilling + 0.5, 0, 1), np.clip(v * tilling - pivot_v * tilling + 0.5, 0, 1)
    if kind == 1:                                        # polar spread
        x, y = (u + (offset[0] or 0)) * 2 - 1, (v + (offset[1] or 0)) * 2 - 1
        r = np.mod(np.clip(np.hypot(x, y), 0, 1) * tilling, 1.0)
        theta = np.mod((np.arctan2(y, x) / (2 * np.pi) + 0.5) * tilling2, 1.0)
        return theta, r
    if kind == 2:                                        # flowing
        return np.mod(u * tilling, 1.0), np.mod(v * tilling, 1.0)
    scale = (tilling + tilling2) * 0.5                   # pulsing size: halfway at time 0
    if abs(scale) < 1e-6:
        return np.full_like(u, 0.5), np.full_like(v, 0.5)
    return (np.clip(u * scale - (pivot_u - 0.5 / scale) * scale, 0, 1),
            np.clip(v * scale - (pivot_v - 0.5 / scale) * scale, 0, 1))


def _sample(image, u, v):
    h, w = image.shape[:2]
    return image[np.clip((np.mod(v, 1.0) * h).astype(int), 0, h - 1), np.clip((np.mod(u, 1.0) * w).astype(int), 0, w - 1)]


def _resize(image, width, height):
    if image.shape[0] == height and image.shape[1] == width:
        return image
    from PIL import Image  # pylint: disable=import-outside-toplevel
    pil = Image.fromarray((np.clip(image, 0, 1) * 255).astype(np.uint8), "RGBA").resize((width, height), Image.BILINEAR)
    return np.asarray(pil, np.float32) / 255.0


def split_by_side(mesh):
    """Two copies of a mesh: triangles on the +X side ("left") and the rest ("right")."""
    positions = np.asarray(mesh.position, float)
    faces = np.asarray(mesh.face, np.int64).reshape(-1, 3)
    centre_x = positions[faces, 0].mean(1)
    counts = [part[1] for part in mesh.mesh] if mesh.mesh else [len(faces)]
    halves = []
    for keep in (centre_x >= 0, centre_x < 0):
        start, new_faces, new_parts = 0, [], []
        for number, count in enumerate(counts):
            count = max(0, min(count, len(faces) - start))
            chosen = faces[start:start + count][keep[start:start + count]]
            new_faces.extend(tuple(int(i) for i in tri) for tri in chosen)
            if mesh.mesh:
                part = list(mesh.mesh[number])
                part[1] = len(chosen)
                new_parts.append(tuple(part))
            start += count
        half = copy.copy(mesh)
        half.face = new_faces
        if mesh.mesh:
            half.mesh = new_parts
        halves.append(half)
    return halves
