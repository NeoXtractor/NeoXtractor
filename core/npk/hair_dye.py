"""Hair dye baked into the hair texture, the way the game's cartoon shader (LUT_TINT) does it.

Dyeable hairstyles have a colourless texture (<hair>_qs) and a dye mask (<hair>_rs); the mask blends between
the two colours picked from the hair_color table.
"""

from __future__ import annotations

import numpy as np

from core.npk.face_makeup import hard_light, linear_to_srgb, srgb_to_linear, _resize


class HairDye:
    def __init__(self, index, tables):
        self.index = index
        self.tables = tables
        self._colors = None
        self._hairs = None
        self._images = {}

    def colors(self) -> list[tuple[int, tuple, int]]:
        """[(colour id, (r, g, b) 0..1, main colour id)] in the game's order."""
        if self._colors is None:
            rows = self.tables.keyed_rows("hair_color")
            colors = []
            for key, row in rows.items():
                value = row.get("color_value") if isinstance(row, dict) else None
                if not value:
                    continue
                colors.append((key, tuple(float(c) for c in value[:3]), row.get("main_color_id", key), row.get("sort_id", 0)))
            colors.sort(key=lambda c: (c[2] if isinstance(c[2], int) else 0, c[3] or 0, c[0] if isinstance(c[0], int) else 0))
            self._colors = [(c[0], c[1], c[2]) for c in colors]
        return self._colors

    def hairs(self) -> list[dict]:
        """Dyeable hairstyles: {folder, mesh, qs, rs, dress_id, sex} (high quality only)."""
        if self._hairs is None:
            hairs = []
            for key, row in self.tables.keyed_rows("hair_color_tex").items():
                if not isinstance(row, dict) or not row.get("high_rs_tex") or not row.get("high_qs_tex"):
                    continue
                rs = row["high_rs_tex"].replace("/", "\\")
                folder = rs.rsplit("\\", 1)[0]
                stem = folder.rsplit("\\", 1)[-1]
                sex = "female" if "\\girl\\" in folder.lower() else "male"
                hairs.append({"folder": folder, "mesh": f"{folder}\\{stem}.mesh", "qs": row["high_qs_tex"].replace("/", "\\"),
                              "rs": rs, "dress_id": key, "sex": sex})
            unique = {}
            for hair in hairs:
                unique.setdefault(hair["folder"].lower(), hair)
            self._hairs = sorted(unique.values(), key=lambda h: h["folder"].lower())
        return self._hairs

    def image(self, path):
        from core.npk.stage_export import decode_texture
        if path not in self._images:
            found = self.index.read(path)
            image = None
            if found is not None:
                try:
                    image = np.asarray(decode_texture(found[0]).convert("RGBA"), np.float32) / 255.0
                except Exception:
                    image = None
            self._images[path] = image
        return self._images[path]

    def bake(self, hair: dict, color1, color2, alpha_source=None):
        """
        Returns the dyed hair texture. alpha_source is the hair's original texture, which is used to keep its specific
        edges.
        """
        from PIL import Image
        base = self.image(hair["qs"])
        mask = self.image(hair["rs"])
        if base is None or mask is None:
            raise ValueError("this hairstyle's dye textures are missing")
        height, width = base.shape[:2]
        mask = _resize(mask, width, height)
        albedo = srgb_to_linear(base[..., :3].astype(np.float64))
        c1, c2 = np.asarray(color1[:3], float), np.asarray(color2[:3], float)
        mid = (c1 + c2) * 0.5
        r = mask[..., 0:1].astype(np.float64)
        low = c2 + (mid - c2) * np.clip(2 * r, 0, 1)
        high = mid + (c1 - mid) * np.clip(2 * r - 1, 0, 1)
        tint = np.where(r >= 0.5, high, low)
        dyed_area = ((mask[..., 0] + mask[..., 1] + mask[..., 2]) > 0.001)[..., None]
        albedo = np.where(dyed_area, hard_light(albedo, tint), albedo)
        alpha = base[..., 3:4]
        if alpha_source is not None:
            source = np.asarray(alpha_source.convert("RGBA"), np.float32) / 255.0
            alpha = _resize(source, width, height)[..., 3:4]
        out = np.dstack([linear_to_srgb(albedo), alpha])
        return Image.fromarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8), "RGBA")


def hex_color(rgb) -> str:
    return "#" + "".join(f"{int(round(max(0.0, min(1.0, c)) * 255)):02x}" for c in rgb[:3])
