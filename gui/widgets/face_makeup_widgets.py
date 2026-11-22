"""
Character creator widgets: the game-style tile grids, round buttons, swatch popup and the window's style.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets
from core.npk.game_tables import TRAITS

ICON_ROLE = QtCore.Qt.ItemDataRole.UserRole + 61


TEXTURE_ROLE = QtCore.Qt.ItemDataRole.UserRole + 62  # a recolour's own texture (same mesh, other colours)


RAIL_WIDTH = 80


TILE = QtCore.QSize(132, 132)


LIST_WIDTH = 2 * TILE.width() + 34


LIST_WIDTH_EXPANDED = 5 * TILE.width() + 34  # the expanded list, five tiles across


GOLD = "#d6aa5a"


UI_FOLDER = "ui\\storeroom_wardrobe_new_panel\\resource\\"


UI_SHEETS = tuple(
    UI_FOLDER + name for name in ("custom", "b_storeroom_wardrobe_panel1", "b_storeroom_wardrobe_panel2", "plist1")
) + (
    "ui\\create_role_panel_new\\resource\\create_role_panel1",
    "ui\\left_tab_list\\resource\\plist1",
    "ui\\com_tag\\resource\\plist1",
    "ui\\com_details_right\\resource\\plist1",
    "ui\\_com_image\\dropdown\\plist",
    "ui\\_com_image\\button\\plist",
    "ui\\_com_image\\icon\\plist",
    "ui\\spontaneous_move_chess\\resource\\spontaneous_move_chess_guide1",
    "ui\\senior_tailor\\resource\\senior_tailor_main1",
)  # the wardrobe filter's ticks and frame


SKIN_SPRITE_PREFIX = "pic_chuangjue_fuse_"


UI_SPRITES = {
    "icon_lianxing",
    "icon_mianbuxijie",
    "icon_chunse",
    "icon_meimao",
    "pic_zr_tongkong_01",
    "pic_zr_yanzhu_01",
    "pic_fuzhuangdian_shaixuan_toufa",
    "pic_duijiaoxiang_tubiao1",
    "pic_fuzhuangdian_shaixuan_maozi",
    "icon_ershi",
    "icon_yanjing",
    "pic_fuzhuangdian_shaixuan_weijin",
    "icon_huizhang",
    "pic_fuzhuangdian_shaixuan_beibao",
    "pic_fushi_diban",
    "pic_xuanzhong",
    "pic_chuangjue_wu",
    "pic_moyao",
    "pic_jd_tianjia",
    "pic_rukou_chongzhi",
    "pic_waiguan_yulan1",
    "pic_zhiboxin_jiantou",
    "pic_tixing_weixuanzhong",
    "pic_tixing_xuanzhong",
    "pic_chuangjue_xuanzhong",
    "pic_yimaojian_quanbu",
    "pic_fuzhuangdian_shaixuan_yifu",
    "icon_tongkongxijie",
    "pic_selected_arrow",
    "pic_wskj_shurukuang",
    "pic_fuzhuangdian_xuanzhongguang",
    "bg_huixiang_pingzhidi_00",
    "bg_huixiang_pingzhidi_01",
    "bg_huixiang_pingzhidi_02",
    "bg_huixiang_pingzhidi_03",
    "bg_huixiang_pingzhidi_04",
    "pic_bg_sift",
    "pic_ktjs_gou",
    "pic_duijue_gouxuandiban",
    "pic_tongyong_xialadi",
    "pic_tongyong_xialaxuanzhong01",
    "pro_huixiang_line_huang",
    "pro_huixiang_line2_huang",
    "pic_zhuangrong_xuanzhong",
    "pic_zhuangrong_xian",
    "icon_fuzhuang_tiaosepan",
    "pic_tongyong_xialafenxian",
    "pic_wushihuzhu_shaixuan_jiantou",
    "pic_shaixuan_di",
    "pic_gou",
    "btn_mfxj_diban",
    "icon_zhiboxin_sousuo",
    "icon_shengxu",
    "icon_jiangxu",
    "icon_shejiao_guanbi",
    "pic_tongyong_biaoqian",
    "pic_tongyong_xian",
}


HALO_TURN = 0.0
HALO_CROP = 0.8  # the middle of pic_xuanzhong: its ring and the bloom around it
HALO_SCALE = 1.02 / (0.5 / HALO_CROP)  # the ring is half the sprite: sized to lie on the disc's edge


# Sprites the wardrobe takes from loose files rather than its atlases: {name: path without extension}
LOOSE_SPRITES = {
    "icon_beibao_01": "image\\storeroom\\icon_beibao_01",
    "bg_huixiang_youdi": "ui\\_bigbg\\bg_huixiang_youdi",
}


RARITY_ROLE = QtCore.Qt.ItemDataRole.UserRole + 65  # the item's rarity, 1 Basic ... 5 Mythic


# The wardrobe's rarity discs: grey Basic, teal Rare, purple Epic, gold Legendary, iridescent Mythic
RARITY_SPRITES = {
    1: "bg_huixiang_pingzhidi_03",
    2: "bg_huixiang_pingzhidi_02",
    3: "bg_huixiang_pingzhidi_01",
    4: "bg_huixiang_pingzhidi_00",
    5: "bg_huixiang_pingzhidi_04",
}
RARITY_NAMES = {1: "Basic", 2: "Rare", 3: "Epic", 4: "Legendary", 5: "Mythic"}
RARITY_COLORS = {1: "#b9b2a4", 2: "#62c9be", 3: "#b893e6", 4: "#e9c162", 5: "#eef0ff"}
# The rarity gems shown in the Filter. The game keeps them as individual images in image/echo/echo_rarity/.
RARITY_GEMS = {number: f"pic_shangdian_pinjie_{number}" for number in RARITY_NAMES}
RARITY_GEM_FOLDER = "image\\echo\\echo_rarity\\"


TRAIT_ROLE = QtCore.Qt.ItemDataRole.UserRole + 66  # the item's filter traits (game_tables.TRAITS keys)
ORDER_ROLE = QtCore.Qt.ItemDataRole.UserRole + 67  # the item's place in the game data (ties keep it)
SORT_ROLE = QtCore.Qt.ItemDataRole.UserRole + 68  # the sort key the lists are ordered by
TAGS_ROLE = QtCore.Qt.ItemDataRole.UserRole + 69  # the item's tags (item.item_tags keys)
FILTER_TRAITS = (("none", "None"),) + TRAITS  # the filter's Trait column after "All", in the game's order


SHORT_LABELS = {"Blush / face decal": "Blush"}


# The game's fonts (res\\ui\\font, the Documents patch layer first): the decorative small-caps face of its
# titles and buttons, and the book serif of its other text. Read from the install, never copied.
# The fonts by file, in preference order per role. Their internal names can be Chinese ("Source Han Serif CN",
# "uifont_cn") even though the Latin letters are the game's own, so files are picked by name, not by family.
GAME_FONT_FILES = {
    "title": ("uifont_en_title_2.ttf", "uifont_en_title.ttf"),
    "body": ("uifont_en.ttf", "uifont_en_title.ttf"),
}
TITLE_FACE_FILE = "uifont_en_title_2.ttf"  # the decorative title face (its tall line spacing is tightened)
_GAME_FONTS: dict[str, str] = {}


def game_fonts(game) -> dict[str, str]:
    """
    {"title": family, "body": family} for the fonts found (registered once per run); {} when none are.
    "decorative" is set when the title is the game's decorative face.
    """
    import os
    from core.npk.stage_export import game_layers

    if _GAME_FONTS:
        return dict(_GAME_FONTS)
    # every layer: the game folder is <install>\Documents when an archive was opened from the patch copy
    folders = [os.path.join(str(layer), "res", "ui", "font") for layer in game_layers(game)]
    for role, names in GAME_FONT_FILES.items():
        for name in names:
            path = next((os.path.join(f, name) for f in folders if os.path.isfile(os.path.join(f, name))), None)
            if path is None:
                continue
            font_id = QtGui.QFontDatabase.addApplicationFont(path)
            families = QtGui.QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
            if families:
                _GAME_FONTS[role] = families[0]
                if role == "title" and name == TITLE_FACE_FILE:
                    _GAME_FONTS["decorative"] = "1"
                break
    return dict(_GAME_FONTS)


def game_font_style(fonts: dict[str, str]) -> str:
    """
    Style rules for the game's faces: the book serif on everything (a style rule reaches widgets that have a
    style sheet of their own, which an inherited font does not), the title face on titles and main buttons.
    """
    rules = []
    if fonts.get("body"):
        rules.append(f'* {{ font-family: "{fonts["body"]}"; }}')
    if fonts.get("title"):
        rules.append(
            f'#creatorTitle, #creatorExport, #creatorViewBar QToolButton, #creatorActionBar QToolButton '
            f'{{ font-family: "{fonts["title"]}"; }}'
        )
        rules.append('#creatorTitle { font-size: 21px; font-weight: normal; letter-spacing: 1px; }')
    return "\n" + "\n".join(rules) + "\n" if rules else ""


CREATOR_STYLE = """
#creatorViewBar, #creatorActionBar { background: rgba(29, 26, 23, 225); border: 1px solid #3a3226; border-radius: 8px; }
#creatorViewBar QToolButton, #creatorActionBar QToolButton { padding: 4px 10px; border: none; border-radius: 5px;
    color: #eadfc8; background: transparent; }
#creatorViewBar QToolButton:hover, #creatorActionBar QToolButton:hover { background: rgba(214, 170, 90, 35); }
#creatorViewBar QToolButton:checked { background: #6b5530; color: white; }
#creatorViewBar QToolButton::menu-indicator { image: none; width: 0; }
#creatorExport { font-weight: bold; background: #6b5530; color: white; }
#creatorExport:hover { background: #80673a; }
QMenu#creatorPopover { background: #1d1a17; border: 1px solid #6b5838; border-radius: 8px; padding: 6px; }
QMenu#creatorPopover::item { padding: 5px 22px 5px 22px; border-radius: 4px; color: #eadfc8; }
QMenu#creatorPopover::item:selected { background: rgba(214, 170, 90, 55); }
QMenu#creatorPopover::section { color: #d6aa5a; font-weight: bold; padding: 6px 8px 2px 8px; }
QMenu#creatorPopover::separator { height: 1px; background: #3a3226; margin: 4px 6px; }
#creatorRail { background: #161412; border-right: 1px solid #3a3226; }
#creatorRail QToolButton { border: none; border-radius: 6px; padding: 2px; color: #cbb68e; }
#creatorRail QToolButton:hover { background: transparent; }
#creatorRail QToolButton:checked { background: rgba(214, 170, 90, 55); border-left: 3px solid #d6aa5a; }
#creatorPanel { background: #1d1a17; border-right: 1px solid #3a3226; }
#creatorTitle { font-size: 18px; font-weight: bold; color: #e6cf9c; padding: 2px 0 4px 2px; }
#creatorPanel QScrollArea, #creatorPanel QScrollArea > QWidget > QWidget { background: transparent; }
#creatorPanel QLineEdit { background: #2a2520; border: 1px solid #4a3f2e; border-radius: 10px; padding: 3px 8px; }
#creatorItemTitle { background: transparent; }
#creatorNotes { background: rgba(30, 30, 30, 200); color: #bbb; border-radius: 4px; padding: 4px 8px; }
#creatorViewControls { background: transparent; border: none; }
#creatorPopup { background: #1d1a17; border: 1px solid #6b5838; border-radius: 8px; }
#creatorFilter { border: none; background: transparent; font-size: 17px; }
#creatorFilterPanel { background: transparent; border: none; }
#creatorFilterBar { background: transparent; }
#creatorLayers { background: rgba(29, 26, 23, 225); border: 1px solid #3a3226; border-radius: 8px; }
#creatorLayers QToolButton { border: none; background: transparent; color: #e6cf9c; padding: 5px 8px;
    text-align: left; font-size: 15px; }
#creatorLayers QToolButton:hover { background: rgba(214, 170, 90, 30); }
#creatorLayers QTreeWidget { background: transparent; border: none; color: #eadfc8; outline: none; font-size: 15px; }
#creatorLayers QTreeWidget::item { padding: 3px 0; border: none; }
#creatorLayers QTreeWidget::item:hover { background: rgba(214, 170, 90, 30); }
#creatorLayers QTreeWidget::item:selected { background: transparent; color: #eadfc8; }
#creatorFilterHeading { color: #e6cf9c; font-size: 21px; padding: 6px 0 10px 0; background: transparent; }
QListView#creatorTiles { background: transparent; border: none; }
"""


def _sprite(sprites, name):
    pixmap = sprites.get(name) if sprites else None
    return pixmap if pixmap is not None and not pixmap.isNull() else None


def _draw_fit(painter, rect: QtCore.QRectF, pixmap):
    """pixmap scaled into rect, centred, keeping its shape."""
    scaled = pixmap.size().scaled(rect.size().toSize(), QtCore.Qt.AspectRatioMode.KeepAspectRatio)
    target = QtCore.QRectF(
        rect.center().x() - scaled.width() / 2, rect.center().y() - scaled.height() / 2, scaled.width(), scaled.height()
    )
    painter.drawPixmap(target, pixmap, QtCore.QRectF(pixmap.rect()))


def _draw_nine(painter, rect: QtCore.QRectF, pixmap, edge: int):
    """pixmap stretched over rect in nine slices, so its corners keep their size."""
    w, h = pixmap.width(), pixmap.height()
    edge = max(1, min(edge, w // 2 - 1, h // 2 - 1, int(rect.width() // 2), int(rect.height() // 2)))
    xs = (rect.left(), rect.left() + edge, rect.right() - edge, rect.right())
    ys = (rect.top(), rect.top() + edge, rect.bottom() - edge, rect.bottom())
    sx, sy = (0, edge, w - edge, w), (0, edge, h - edge, h)
    for i in range(3):
        for j in range(3):
            painter.drawPixmap(
                QtCore.QRectF(xs[i], ys[j], xs[i + 1] - xs[i], ys[j + 1] - ys[j]),
                pixmap,
                QtCore.QRectF(sx[i], sy[j], sx[i + 1] - sx[i], sy[j + 1] - sy[j]),
            )


def _draw_check(painter, center: QtCore.QPointF, radius=9.0):
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.setBrush(QtGui.QColor(GOLD))
    painter.drawEllipse(center, radius, radius)
    pen = QtGui.QPen(QtGui.QColor("#2a1f10"), 2.2)
    pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    path = QtGui.QPainterPath(QtCore.QPointF(center.x() - radius * 0.45, center.y()))
    path.lineTo(center.x() - radius * 0.1, center.y() + radius * 0.38)
    path.lineTo(center.x() + radius * 0.5, center.y() - radius * 0.4)
    painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
    painter.drawPath(path)


def _draw_round(painter, circle: QtCore.QRectF, sprites, active=False, hover=False, rarity=None, ring=True):
    """The round frame of a tile or button: the game's disc (coloured by rarity), a gold ring when chosen."""
    disc = _sprite(sprites, RARITY_SPRITES[rarity]) if rarity in RARITY_SPRITES else None
    background = _sprite(sprites, "pic_fushi_diban")
    if disc is not None:
        painter.drawPixmap(circle, disc, QtCore.QRectF(disc.rect()))
    elif background is not None:
        painter.drawPixmap(circle, background, QtCore.QRectF(background.rect()))
    else:
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor(34, 29, 24))
        painter.drawEllipse(circle)
    if not ring:
        return
    painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
    painter.setPen(
        QtGui.QPen(QtGui.QColor(GOLD if active else ("#9c8355" if hover else "#5a4a32")), 2.4 if active else 1.2)
    )
    painter.drawEllipse(circle.adjusted(1, 1, -1, -1))


def _tile_circle(rect) -> QtCore.QRectF:
    rect = QtCore.QRectF(rect)
    side = min(rect.width(), rect.height()) - 12
    return QtCore.QRectF(rect.center().x() - side / 2, rect.center().y() - side / 2, side, side)


def _draw_halo(painter, circle: QtCore.QRectF, sprites, seconds: float):
    """The game's picked-item halo (pic_xuanzhong), slowly turning, centred on a tile's disc."""
    glow = _sprite(sprites, "pic_xuanzhong")
    if glow is None:
        return
    size = circle.width() * HALO_SCALE
    painter.save()
    painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
    painter.translate(circle.center())
    painter.rotate((seconds * HALO_TURN) % 360.0)
    w, h = glow.width(), glow.height()
    source = QtCore.QRectF(w * (1 - HALO_CROP) / 2, h * (1 - HALO_CROP) / 2, w * HALO_CROP, h * HALO_CROP)
    painter.drawPixmap(QtCore.QRectF(-size / 2, -size / 2, size, size), glow, source)
    painter.restore()


class _RoundTileDelegate(QtWidgets.QStyledItemDelegate):
    """Round wardrobe tiles: the item icon in a disc; the worn one on the game's halo (drawn by the view) and tick."""

    def __init__(self, parent, sprites):
        super().__init__(parent)
        self.sprites = sprites

    def sizeHint(self, option, index):
        return QtCore.QSize(TILE.width() - 2, TILE.height() - 2)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        circle = _tile_circle(option.rect)
        view = self.parent()
        selected = bool(option.state & QtWidgets.QStyle.StateFlag.State_Selected) or (
            hasattr(view, "is_chosen") and view.is_chosen(index)
        )
        hover = bool(option.state & QtWidgets.QStyle.StateFlag.State_MouseOver)
        # the game's tiles have no drawn ring: the disc art alone, the halo (painted by the view) when picked
        _draw_round(
            painter,
            circle,
            self.sprites,
            selected,
            hover,
            index.data(RARITY_ROLE),
            ring=_sprite(self.sprites, "pic_fushi_diban") is None,
        )
        icon = index.data(QtCore.Qt.ItemDataRole.DecorationRole)
        pixmap = icon.pixmap(96, 96) if isinstance(icon, QtGui.QIcon) and not icon.isNull() else None
        none_item = (
            index.row() == 0
            and index.data(QtCore.Qt.ItemDataRole.UserRole) is None
            and not hasattr(self.parent(), "is_chosen")
        )
        if pixmap is not None and not pixmap.isNull():
            clip = QtGui.QPainterPath()
            clip.addEllipse(circle.adjusted(3, 3, -3, -3))
            painter.setClipPath(clip)
            _draw_fit(painter, circle.adjusted(7, 7, -7, -7), pixmap)
            painter.setClipping(False)
        elif none_item and _sprite(self.sprites, "pic_chuangjue_wu") is not None:
            _draw_fit(painter, circle.adjusted(10, 10, -10, -10), self.sprites["pic_chuangjue_wu"])
        else:
            text = (index.data() or "").split("·")[0].strip()
            painter.setPen(QtGui.QColor("#cbb68e"))
            font = painter.font()
            font.setPointSizeF(max(7.0, font.pointSizeF() - 1.5))
            painter.setFont(font)
            painter.drawText(
                circle.adjusted(10, 10, -10, -10),
                QtCore.Qt.AlignmentFlag.AlignCenter | QtCore.Qt.TextFlag.TextWordWrap,
                text,
            )
        if selected and (_sprite(self.sprites, "pic_xuanzhong") is None or _sprite(self.sprites, "pic_gou") is None):
            _draw_check(painter, QtCore.QPointF(circle.right() - 8, circle.top() + 8))  # until the sprites arrive
        painter.restore()  # with the sprites, the halo and tick are drawn above the tiles by _HaloLayer


def _draw_tick(painter, circle: QtCore.QRectF, sprites):
    tick = _sprite(sprites, "pic_gou")
    if tick is not None:
        painter.drawPixmap(
            QtCore.QRectF(circle.right() - 22, circle.top() - 2, 24, 24), tick, QtCore.QRectF(tick.rect())
        )


class _HaloLayer(QtWidgets.QWidget):
    """
    A clear layer over a list page that draws the picked tiles' halo (turning) and tick above the tiles, as
    the game does, free of the grids' own edges. It repaints only the halos' spots, the old ones included.
    """

    def __init__(self, host, sprites):
        super().__init__(host)
        self.sprites = sprites
        self.views: list = []
        self._last: list = []
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setGeometry(host.rect())
        host.installEventFilter(self)
        self._clock = QtCore.QElapsedTimer()
        self._clock.start()
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(120)  # only to follow the pick (and clear its old spot)
        self._timer.timeout.connect(self._turn)
        self._timer.start()
        self.raise_()
        self.show()

    @classmethod
    def of(cls, host, sprites):
        layer = getattr(host, "_halo_layer", None)
        if layer is None:
            layer = host._halo_layer = cls(host, sprites)
        return layer

    def add(self, view):
        if view not in self.views:
            self.views.append(view)
        self.raise_()

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QtCore.QEvent.Type.Resize:
            self.setGeometry(obj.rect())
            self.raise_()
        return False

    def circles(self) -> list:
        out = []
        for view in self.views:
            try:
                if not view.isVisible():
                    continue
                for rect in view.halo_rects():
                    top_left = view.viewport().mapTo(
                        self.parentWidget(), rect.topLeft()
                    )  # the page (the layer covers it)
                    out.append(_tile_circle(QtCore.QRect(top_left, rect.size())))
            except RuntimeError:  # a view already deleted
                continue
        return out

    def _spot(self, circle) -> QtCore.QRect:
        grow = circle.width() * (HALO_SCALE - 1) / 2 + 4
        return circle.adjusted(-grow, -grow, grow, grow).toAlignedRect()

    def _turn(self):
        if not self.isVisible() or self.visibleRegion().isEmpty():
            return
        spots = [self._spot(c) for c in self.circles()]
        if spots == self._last:  # a still halo: repaint only when the pick moves
            return
        for spot in spots + [s for s in self._last if s not in spots]:
            self.update(spot)
        self._last = spots

    def paintEvent(self, _event):
        circles = self.circles()
        if not circles:
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        seconds = self._clock.elapsed() / 1000.0
        for circle in circles:
            _draw_halo(painter, circle, self.sprites, seconds)
            _draw_tick(painter, circle, self.sprites)


class _Tiles(QtWidgets.QListView):
    """Round tiles over a combo box's own item list: clicking a tile picks that item."""

    item_filter = None  # index -> bool: the Filter's rarity / trait choice, or None

    def __init__(self, combo: QtWidgets.QComboBox, sprites):
        super().__init__()
        self.combo = combo
        self._filter = ""
        self.setObjectName("creatorTiles")
        self.setModel(combo.model())
        self.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
        self.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        self.setMovement(QtWidgets.QListView.Movement.Static)
        self.setWrapping(True)
        self.setUniformItemSizes(True)
        self.setGridSize(TILE)
        self.setSpacing(0)
        self.setMouseTracking(True)
        self.setItemDelegate(_RoundTileDelegate(self, sprites))
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        self.clicked.connect(lambda index: self.combo.setCurrentIndex(index.row()))
        combo.currentIndexChanged.connect(self._sync)
        model = combo.model()
        for signal in (model.rowsInserted, model.rowsRemoved, model.modelReset, model.layoutChanged):
            signal.connect(self._later)
        self._fit()
        self._start_halo(sprites)

    def _start_halo(self, sprites):
        self.sprites = sprites
        QtCore.QTimer.singleShot(0, self._join_halo_layer)  # once the grid sits in its list page

    def _join_halo_layer(self):
        widget = self.parentWidget()
        while widget is not None and not isinstance(widget.parentWidget(), QtWidgets.QStackedWidget):
            widget = widget.parentWidget()
        if widget is not None:
            _HaloLayer.of(widget, self.sprites).add(self)

    def chosen_rows(self) -> list[int]:
        row = self.combo.currentIndex()
        return [row] if row >= 0 and not self.isRowHidden(row) else []

    def halo_rects(self):
        rects = []
        for row in self.chosen_rows():
            rect = self.visualRect(self.model().index(row, 0))
            if rect.isValid() and rect.intersects(self.viewport().rect()):
                rects.append(rect)
        return rects

    def _later(self, *_args):
        QtCore.QTimer.singleShot(0, self._refresh)

    def _refresh(self):
        self.apply_filter(self._filter)
        self._sync()

    def _sync(self, *_args):
        row = self.combo.currentIndex()
        if row >= 0:
            self.setCurrentIndex(self.model().index(row, 0))
        else:
            self.clearSelection()

    def apply_filter(self, text: str):
        """Only the tiles whose name or file contains text and that pass the Filter ("None" always stays)."""
        self._filter = text.strip().lower()
        model = self.model()
        passes = self.item_filter
        for row in range(model.rowCount()):
            index = model.index(row, 0)
            # the "None" tile always stays; a list whose first tile is a real item (the faces) filters it too
            keep = (
                row == 0 and getattr(self, "keep_first", True) and index.data(QtCore.Qt.ItemDataRole.UserRole) is None
            )
            shown = keep or passes is None or passes(index)
            if shown and self._filter and not keep:
                haystack = f"{index.data() or ''} {index.data(QtCore.Qt.ItemDataRole.UserRole) or ''}".lower()
                shown = self._filter in haystack
            self.setRowHidden(row, not shown)
        self._fit()

    def reflow(self):
        """Lay the tiles out again for the current width (after the list is expanded or folded back)."""
        self.doItemsLayout()
        self._fit()
        self.viewport().update()

    def _fit(self):
        shown = sum(1 for row in range(self.model().rowCount()) if not self.isRowHidden(row))
        columns = max(1, self.viewport().width() // TILE.width()) if self.viewport().width() > 10 else 2
        rows = max(1, -(-shown // columns))
        self.setFixedHeight(rows * TILE.height() + 4)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def wheelEvent(self, event):
        event.ignore()  # the panel scrolls, not each grid

    def viewportEvent(self, event):
        if event.type() == QtCore.QEvent.Type.ToolTip:
            index = self.indexAt(event.pos())
            if index.isValid():
                rarity = RARITY_NAMES.get(index.data(RARITY_ROLE))
                text = index.data() or ""
                QtWidgets.QToolTip.showText(event.globalPos(), f"{text}\n{rarity}" if rarity else text, self)
                return True
        return super().viewportEvent(event)


SOURCE_ROLE = QtCore.Qt.ItemDataRole.UserRole + 63  # (source number, row) of an "All" tile


SOCKETS_ROLE = QtCore.Qt.ItemDataRole.UserRole + 64  # attachments table ids the game hangs on rig sockets


class _AllTiles(_Tiles):
    """
    Every wearable item in one grid, like the game's "All" tab: what is worn comes first (ringed, checked),
    then everything else in the categories' order. Clicking a tile wears it; clicking a worn one takes it off.
    """

    keep_first = False

    def __init__(self, sources, sprites):
        QtWidgets.QListView.__init__(self)
        self.sources = sources  # [(combo, can be taken off)]
        self._filter = ""
        self.setObjectName("creatorTiles")
        self.item_model = QtGui.QStandardItemModel(self)
        self.setModel(self.item_model)
        self.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
        self.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        self.setMovement(QtWidgets.QListView.Movement.Static)
        self.setWrapping(True)
        self.setUniformItemSizes(True)
        self.setGridSize(TILE)
        self.setSpacing(0)
        self.setMouseTracking(True)
        self.setItemDelegate(_RoundTileDelegate(self, sprites))
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        self.clicked.connect(self._clicked)
        self._start_halo(sprites)
        self._pending = False
        for combo, _optional in sources:
            combo.currentIndexChanged.connect(self.schedule)
            model = combo.model()
            for signal in (
                model.rowsInserted,
                model.rowsRemoved,
                model.modelReset,
                model.dataChanged,
                model.layoutChanged,
            ):
                signal.connect(self.schedule)
        self.rebuild()

    def schedule(self, *_args):
        if not self._pending:
            self._pending = True
            QtCore.QTimer.singleShot(0, self.rebuild)

    def rebuild(self):
        self._pending = False
        worn, rest = [], []
        for number, (combo, _optional) in enumerate(self.sources):
            current = combo.currentIndex()
            for row in range(combo.count()):
                data = combo.itemData(row)
                if data is None:
                    continue  # the "None" tiles stay on their own pages
                (worn if row == current else rest).append((number, row, combo, data))
        # Filled through the model's own indexes, never Python-made QStandardItems: those stay registered with
        # PySide after clear() frees them, and an event Qt later puts at the same address arrives as a stale
        # QStandardItem (that crashed the app on face changes, which rebuild this grid)
        model = self.item_model
        entries = [(True, e) for e in worn] + [(False, e) for e in rest]
        model.clear()
        model.setColumnCount(1)
        model.setRowCount(len(entries))
        display, decoration = QtCore.Qt.ItemDataRole.DisplayRole, QtCore.Qt.ItemDataRole.DecorationRole
        for position, (chosen, (number, row, combo, data)) in enumerate(entries):
            index = model.index(position, 0)
            model.setData(index, combo.itemText(row), display)
            icon = combo.itemIcon(row)
            if not icon.isNull():
                model.setData(index, icon, decoration)
            model.setData(index, data if isinstance(data, str) else str(data), QtCore.Qt.ItemDataRole.UserRole)
            model.setData(index, (number, row, chosen), SOURCE_ROLE)
            model.setData(index, combo.itemData(row, RARITY_ROLE), RARITY_ROLE)
            model.setData(index, combo.itemData(row, TRAIT_ROLE), TRAIT_ROLE)
            model.setData(index, combo.itemData(row, TAGS_ROLE), TAGS_ROLE)
        self.apply_filter(self._filter)

    def is_chosen(self, index) -> bool:
        source = index.data(SOURCE_ROLE)
        return bool(source and source[2])

    def chosen_rows(self) -> list[int]:
        return [
            row
            for row in range(self.item_model.rowCount())
            if not self.isRowHidden(row) and self.is_chosen(self.item_model.index(row, 0))
        ]

    def _clicked(self, index):
        source = index.data(SOURCE_ROLE)
        if not source:
            return
        number, row, chosen = source
        combo, optional = self.sources[number]
        if chosen:
            if optional and combo.count() and combo.itemData(0) is None:
                combo.setCurrentIndex(0)  # take it off
        else:
            combo.setCurrentIndex(row)

    def _sync(self, *_args):
        pass

    def _refresh(self):
        self.rebuild()


class _DragScroll(QtCore.QObject):
    """
    Scrolls a scroll area by dragging anywhere in it (tiles included), like the game's lists: a press that
    moves more than a few pixels becomes a drag and its release is not a click; a press that stays put is
    an ordinary click. The wheel keeps working through the scroll area itself.
    """

    THRESHOLD = 6

    def __init__(self, area: QtWidgets.QAbstractScrollArea):
        super().__init__(area)
        self.area = area
        self._start = None
        self._value = 0
        self._dragging = False
        self.watch(area.viewport())

    def watch(self, widget):
        widget.removeEventFilter(self)  # once per widget, however often it is asked
        widget.installEventFilter(self)

    def watch_children(self):
        """Every item view and button inside the area (they get the mouse before the area does)."""
        for view in self.area.findChildren(QtWidgets.QAbstractScrollArea):
            if view is not self.area:
                self.watch(view.viewport())
        for button in self.area.findChildren(QtWidgets.QAbstractButton):
            self.watch(button)

    def eventFilter(self, obj, event):
        kind = event.type()
        try:
            bar = self.area.verticalScrollBar()
        except RuntimeError:  # the list is gone (window closing)
            return False
        if kind == QtCore.QEvent.Type.MouseButtonPress and event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._start = event.globalPosition().y()
            self._value = bar.value()
            self._dragging = False
        elif (
            kind == QtCore.QEvent.Type.MouseMove
            and self._start is not None
            and event.buttons() & QtCore.Qt.MouseButton.LeftButton
        ):
            moved = event.globalPosition().y() - self._start
            if not self._dragging and abs(moved) > self.THRESHOLD and bar.maximum() > 0:
                self._dragging = True
                self.area.viewport().setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            if self._dragging:
                bar.setValue(int(self._value - moved))
                return True
        elif kind == QtCore.QEvent.Type.MouseButtonRelease and event.button() == QtCore.Qt.MouseButton.LeftButton:
            dragged = self._dragging
            self._start, self._dragging = None, False
            if dragged:
                self.area.viewport().unsetCursor()
                if isinstance(obj, QtWidgets.QAbstractButton):
                    obj.setDown(False)
                view = obj.parent()
                if hasattr(view, "_sync"):
                    view._sync()  # put the selection back on the worn item
                return True  # the end of a drag is not a click on a tile
        return False


class _ExpandArrow(QtWidgets.QAbstractButton):
    """The game's golden arrowhead on the edge of the item list: points right to expand, left to fold back."""

    def __init__(self, sprites, parent=None):
        super().__init__(parent)
        self.sprites = sprites
        self.expanded = False
        self.setFixedSize(30, 64)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Show more items at once")

    def set_expanded(self, expanded):
        self.expanded = expanded
        self.setToolTip("Show fewer items" if expanded else "Show more items at once")
        self.update()

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        rect = QtCore.QRectF(self.rect())
        painter.setOpacity(1.0 if self.underMouse() else 0.85)
        arrow = _sprite(self.sprites, "pic_selected_arrow")
        if arrow is not None:
            if getattr(self, "_piece_of", None) is not arrow:
                # the sprite points down in a mostly empty square: crop it once to what is drawn
                image = arrow.toImage()
                bounds = QtCore.QRect()
                for y in range(0, image.height(), 2):
                    for x in range(0, image.width(), 2):
                        if image.pixelColor(x, y).alpha() > 24:
                            bounds = bounds.united(QtCore.QRect(x - 1, y - 1, 3, 3))
                self._piece = arrow.copy(bounds.intersected(arrow.rect())) if bounds.isValid() else arrow
                self._piece_of = arrow
            turned = self._piece.transformed(QtGui.QTransform().rotate(90 if self.expanded else -90))
            _draw_fit(painter, rect.adjusted(4, 14, -6, -14), turned)
        else:
            painter.setPen(QtGui.QPen(QtGui.QColor(GOLD), 2.4))
            cx, cy = rect.center().x() - 1, rect.center().y()
            dx = -5 if self.expanded else 5
            painter.drawPolyline(
                [QtCore.QPointF(cx - dx, cy - 9), QtCore.QPointF(cx + dx, cy), QtCore.QPointF(cx - dx, cy + 9)]
            )


class _SearchBox(QtWidgets.QFrame):
    """The game's search bar: its bordered dark box with a line edit in it and a magnifier on the right."""

    def __init__(self, line_edit, sprites, parent=None):
        super().__init__(parent)
        self.sprites = sprites
        self.setFixedHeight(46)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(14, 4, 44, 4)
        line_edit.setFrame(False)
        line_edit.setStyleSheet("background: transparent; border: none; color: #eadfc8; font-size: 20px;")
        layout.addWidget(line_edit)

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        rect = self.rect()
        box = _sprite(self.sprites, "pic_wskj_shurukuang")
        if box is not None:
            # three slices, so the corners keep their shape at any width
            edge = min(18, box.width() // 3)
            source_h = box.height()
            painter.drawPixmap(QtCore.QRectF(0, 0, edge, rect.height()), box, QtCore.QRectF(0, 0, edge, source_h))
            painter.drawPixmap(
                QtCore.QRectF(edge, 0, rect.width() - 2 * edge, rect.height()),
                box,
                QtCore.QRectF(edge, 0, box.width() - 2 * edge, source_h),
            )
            painter.drawPixmap(
                QtCore.QRectF(rect.width() - edge, 0, edge, rect.height()),
                box,
                QtCore.QRectF(box.width() - edge, 0, edge, source_h),
            )
        else:
            painter.setPen(QtGui.QPen(QtGui.QColor("#6b5838"), 1.2))
            painter.setBrush(QtGui.QColor(30, 27, 24))
            painter.drawRoundedRect(QtCore.QRectF(rect).adjusted(1, 1, -1, -1), 6, 6)
        glass = _sprite(self.sprites, "icon_zhiboxin_sousuo")
        if glass is not None:
            side = min(34.0, rect.height() - 8.0)
            _draw_fit(painter, QtCore.QRectF(rect.width() - side - 6, (rect.height() - side) / 2, side, side), glass)


ROW = 48  # a Filter line's height, as the game spaces them


class _FilterRow(QtWidgets.QAbstractButton):
    """One line of the Filter: a box that takes the game's gold tick, a rarity gem (if any) and the name."""

    def __init__(self, text, sprites, gem=None, color=None):
        super().__init__()
        self.sprites, self.gem, self.color = sprites, gem, color
        self.on = False
        self.setText(text)
        self.setFixedHeight(ROW)
        self.setMinimumWidth(120)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_Hover)

    def sizeHint(self):
        return QtCore.QSize(180, ROW)

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        rect = QtCore.QRectF(self.rect())
        middle = rect.center().y()
        scale = max(1.0, min(1.45, self.height() / ROW))  # taller rows (a tall window) get bigger marks and text
        glow = _sprite(self.sprites, "pic_tongyong_xialaxuanzhong01")
        if glow is not None and self.underMouse():
            painter.setOpacity(0.55)
            painter.drawPixmap(
                rect.adjusted(0, rect.height() * 0.15, 0, -rect.height() * 0.15), glow, QtCore.QRectF(glow.rect())
            )
            painter.setOpacity(1.0)
        side = 30 * scale
        box = QtCore.QRectF(10, middle - side / 2, side, side)
        tick = _sprite(self.sprites, "pic_ktjs_gou")
        base = _sprite(self.sprites, "pic_duijue_gouxuandiban")
        if self.on and tick is not None:
            _draw_fit(painter, box.adjusted(-2, -5, 6, 1), tick)
        elif base is not None:
            painter.drawPixmap(box.adjusted(3, 3, -3, -3), base, QtCore.QRectF(base.rect()))
        x = box.right() + 14 * scale
        gem = _sprite(self.sprites, self.gem) if self.gem else None
        gem_side = 28 * scale
        if gem is not None:
            _draw_fit(painter, QtCore.QRectF(x, middle - gem_side / 2, gem_side, gem_side), gem)
            x += gem_side + 8
        elif self.gem:
            x += gem_side + 8  # keep the names lined up until the gems arrive
        font = painter.font()
        font.setPixelSize(int(18 * scale))
        painter.setFont(font)
        painter.setPen(QtGui.QColor("#f3e6c6" if self.on else "#b8a988"))
        painter.drawText(
            QtCore.QRectF(x, 0, rect.width() - x - 4, rect.height()),
            int(
                QtCore.Qt.AlignmentFlag.AlignVCenter
                | QtCore.Qt.AlignmentFlag.AlignLeft
                | QtCore.Qt.TextFlag.TextWordWrap
            ),
            self.text(),
        )


class _FilterPanel(QtWidgets.QFrame):
    """
    The wardrobe's Filter: a Rarity and a Trait checklist: every line starts ticked; clicking a line
    ticks or unticks just that line; "All" ticks every line again (or unticks them all when they all are).
    """

    changed = QtCore.Signal()

    def __init__(self, sprites):
        super().__init__()
        self.setObjectName("creatorFilterPanel")
        self.columns = {}  # "rarity" / "trait" -> (heading, "All" row, {key: row})
        entries = {
            "rarity": [
                (number, RARITY_NAMES[number], RARITY_GEMS[number], RARITY_COLORS[number])
                for number in sorted(RARITY_NAMES)
            ],
            "trait": [(key, name, None, None) for key, name in FILTER_TRAITS],
        }
        for name, title in (("rarity", "Rarity"), ("trait", "Trait")):
            heading = QtWidgets.QLabel(title)
            heading.setObjectName("creatorFilterHeading")
            heading.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            all_row = _FilterRow("All", sprites)
            all_row.clicked.connect(lambda _checked=False, name=name: self._pick(name, None))
            rows = {}
            for key, label, gem, color in entries[name]:
                row = _FilterRow(label, sprites, gem, color)
                row.clicked.connect(lambda _checked=False, name=name, key=key: self._pick(name, key))
                rows[key] = row
            self.columns[name] = (heading, all_row, rows)
        self.selected = {name: set(rows) for name, (_heading, _all, rows) in self.columns.items()}
        self.grid = QtWidgets.QGridLayout(self)
        self.grid.setContentsMargins(16, 12, 16, 12)
        self.grid.setHorizontalSpacing(28)
        self.grid.setVerticalSpacing(2)
        self.sprites = sprites
        self.wide = None
        self.set_wide(False)
        self._show()

    def paintEvent(self, _event):
        """The game's fine rules under the headings and between the columns (the overlay draws the backdrop)."""
        line = _sprite(self.sprites, "pic_tongyong_xialafenxian")
        if line is None:
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        for heading, _all, _rows in self.columns.values():
            g = heading.geometry()
            painter.drawPixmap(
                QtCore.QRectF(g.left() + 10, g.bottom() - 2, g.width() - 20, 1.5), line, QtCore.QRectF(line.rect())
            )
        if self.wide:
            first, second = (h.geometry() for h, _a, _r in self.columns.values())
            x = (first.right() + second.left()) / 2
            turned = line.transformed(QtGui.QTransform().rotate(90))
            bottom = max(r.geometry().bottom() for _h, _a, rows in self.columns.values() for r in rows.values())
            painter.drawPixmap(
                QtCore.QRectF(x - 0.75, first.top() + 6, 1.5, bottom - first.top()),
                turned,
                QtCore.QRectF(turned.rect()),
            )

    def set_wide(self, wide: bool):
        """Two columns side by side (the expanded list), or Rarity above Trait (the narrow one)."""
        if wide == self.wide:
            return
        self.wide = wide
        for row in range(self.grid.rowCount()):
            self.grid.setRowStretch(row, 0)
        line = 0
        for column, (heading, all_row, rows) in enumerate(self.columns.values()):
            col, top = (column, 0) if wide else (0, line)
            for offset, widget in enumerate([heading, all_row] + list(rows.values())):
                self.grid.addWidget(widget, top + offset, col)
            line = top + 2 + len(rows) + (0 if wide else 1)
        self.grid.setRowStretch(self.grid.rowCount(), 1)

    def set_row_height(self, height: int):
        """The lines' height (the game spreads them down the whole list)."""
        height = max(ROW, min(80, int(height)))
        for _heading, all_row, rows in self.columns.values():
            for row in [all_row] + list(rows.values()):
                if row.height() != height:
                    row.setFixedHeight(height)

    def _pick(self, name, key):
        everything = set(self.columns[name][2])
        chosen = self.selected[name]
        if key is None:
            chosen = set() if chosen == everything else everything
        else:
            chosen = chosen ^ {key}
        self.selected[name] = set(chosen)
        self._show()
        self.changed.emit()

    def _show(self):
        for name, (_heading, all_row, rows) in self.columns.items():
            everything = self.selected[name] == set(rows)
            all_row.on = everything
            all_row.update()
            for key, row in rows.items():
                row.on = key in self.selected[name]
                row.update()
        self._rarities = self.wanted("rarity")
        self._traits = self.wanted("trait")

    def wanted(self, name):
        """The keys picked in a column; None when it is on "All"."""
        rows = self.columns[name][2]
        return None if self.selected[name] == set(rows) else set(self.selected[name])

    def active(self) -> bool:
        return self._rarities is not None or self._traits is not None

    def reset(self):
        self.selected = {name: set(rows) for name, (_heading, _all, rows) in self.columns.items()}
        self._show()
        self.changed.emit()

    def passes(self, index) -> bool:
        """Whether a tile's item fits the picked rarities and traits (items without a rarity count as Basic)."""
        if self._rarities is not None and (index.data(RARITY_ROLE) or 1) not in self._rarities:
            return False
        if self._traits is not None:
            traits = index.data(TRAIT_ROLE) or ()
            if not (self._traits.intersection(traits) or (not traits and "none" in self._traits)):
                return False
        return True


class _FilterButton(QtWidgets.QToolButton):
    """The game's "Filter" under the list: its gold-edged box and gold chevron, down when shut, up when open."""

    def __init__(self, sprites):
        super().__init__()
        self.sprites = sprites
        self.active = False  # gold text while the Filter hides something
        self.setFixedSize(180, 36)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_Hover)

    def paintEvent(self, event):
        box = _sprite(self.sprites, "pic_shaixuan_di")
        if box is not None:
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
            _draw_nine(painter, QtCore.QRectF(self.rect()), box, 17)
            painter.end()
        arrow = _sprite(self.sprites, "pic_wushihuzhu_shaixuan_jiantou")  # the filter chevron, up as stored
        if arrow is not None:
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
            turned = arrow.transformed(
                QtGui.QTransform().rotate(0 if self.isChecked() else 180),
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
            middle = self.height() / 2
            _draw_fit(painter, QtCore.QRectF(8, middle - 12, 26, 24), turned)
            painter.end()
        painter = QtGui.QPainter(self)
        painter.setPen(QtGui.QColor(GOLD if self.active else ("#ffffff" if self.underMouse() else "#d9cdb4")))
        painter.drawText(
            QtCore.QRectF(38, 0, self.width() - 40, self.height()),
            int(QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignLeft),
            self.text(),
        )
        del event


class _ScrollMarker(QtWidgets.QWidget):
    """
    The game's list scroller: its thin gold line with the compass knob, shown while the list scrolls and faded
    out a moment after it stops. While shown, the knob can be dragged (the list follows it up and down) and a
    click on the line jumps there; once faded out it is hidden, so it never covers the tiles.
    """

    WIDTH = 40

    def __init__(self, area: QtWidgets.QAbstractScrollArea, sprites, parent):
        super().__init__(parent)
        self.area, self.sprites = area, sprites
        self._line_of = self._knob_of = None
        self._grab = None  # where on the knob the drag took hold, while dragging
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.opacity = QtWidgets.QGraphicsOpacityEffect(self)
        self.opacity.setOpacity(0.0)
        self.setGraphicsEffect(self.opacity)
        self.fade = QtCore.QPropertyAnimation(self.opacity, b"opacity", self)
        self.fade.setDuration(450)
        self.rest = QtCore.QTimer(self)
        self.rest.setSingleShot(True)
        self.rest.setInterval(1400)
        self.rest.timeout.connect(self._rest_over)
        self.fade.finished.connect(lambda: self.hide() if self.opacity.opacity() <= 0.01 else None)
        area.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.hide()

    def _fade_to(self, value):
        self.fade.stop()
        self.fade.setStartValue(self.opacity.opacity())
        self.fade.setEndValue(value)
        self.fade.start()

    def place(self):
        rect = self.area.geometry()
        self.setGeometry(rect.right() - self.WIDTH + 6, rect.top(), self.WIDTH, rect.height())
        self.raise_()

    def _scrolled(self, *_args):
        if self.area.verticalScrollBar().maximum() <= 0 or not self.area.isVisible():
            return
        self.place()
        self.show()
        if self.opacity.opacity() < 1.0 and self.fade.endValue() != 1.0:
            self._fade_to(1.0)
        self.rest.start()
        self.update()

    def _rest_over(self):
        if self._grab is None and not self.underMouse():  # kept while held or hovered
            self._fade_to(0.0)

    def _knob_span(self):
        """(knob height, the height the knob travels over) in this widget."""
        knob = self._turned("pro_huixiang_line2_huang", "_knob")
        knob_h = self.width() * knob.height() / max(1, knob.width()) if knob is not None else self.width()
        return knob_h, max(1.0, self.height() - knob_h)

    def _scroll_to(self, knob_top):
        bar = self.area.verticalScrollBar()
        _knob_h, travel = self._knob_span()
        bar.setValue(round(max(0.0, min(1.0, knob_top / travel)) * bar.maximum()))

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        bar = self.area.verticalScrollBar()
        knob_h, travel = self._knob_span()
        top = (bar.value() / bar.maximum() if bar.maximum() > 0 else 0.0) * travel
        y = event.position().y()
        if not top <= y <= top + knob_h:  # on the line: the knob jumps there first
            top = y - knob_h / 2
            self._scroll_to(top)
        self._grab = y - top
        self.rest.stop()

    def mouseMoveEvent(self, event):
        if self._grab is not None:
            self._scroll_to(event.position().y() - self._grab)

    def mouseReleaseEvent(self, _event):
        self._grab = None
        self.rest.start()

    def leaveEvent(self, _event):
        if self._grab is None:
            self.rest.start()

    def _turned(self, name, attr):
        sprite = _sprite(self.sprites, name)
        if sprite is None:
            return None
        if getattr(self, attr + "_of") is not sprite:  # stored lying down: stand it up once
            setattr(self, attr, sprite.transformed(QtGui.QTransform().rotate(90)))
            setattr(self, attr + "_of", sprite)
        return getattr(self, attr)

    def paintEvent(self, _event):
        line = self._turned("pro_huixiang_line_huang", "_line")
        knob = self._turned("pro_huixiang_line2_huang", "_knob")
        if line is None or knob is None:
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        rect = QtCore.QRectF(self.rect())
        painter.drawPixmap(rect, line, QtCore.QRectF(line.rect()))
        bar = self.area.verticalScrollBar()
        knob_w = rect.width()
        knob_h = knob_w * knob.height() / max(1, knob.width())
        share = bar.value() / bar.maximum() if bar.maximum() > 0 else 0.0
        top = share * (rect.height() - knob_h)
        painter.drawPixmap(QtCore.QRectF(0, top, knob_w, knob_h), knob, QtCore.QRectF(knob.rect()))


class _SlotTabs(QtWidgets.QWidget):
    """
    The icon tabs over a paired list (glasses | earrings, scarf | brooch, ...): the chosen one bright on the game's
    gold glow, the other dimmed, a dotted divider between them and a fine line under the row.
    """

    currentChanged = QtCore.Signal(int)

    def __init__(self, sprites):
        super().__init__()
        self.sprites = sprites
        self.tabs: list[tuple[str, str | None]] = []
        self._current = 0
        self.setFixedHeight(58)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

    def addTab(self, label, sprite=None):
        self.tabs.append((label, sprite))
        self.update()

    def count(self):
        return len(self.tabs)

    def currentIndex(self):
        return self._current

    def setCurrentIndex(self, index):
        if index != self._current and 0 <= index < len(self.tabs):
            self._current = index
            self.update()
            self.currentChanged.emit(index)

    def tab_rect(self, index) -> QtCore.QRectF:
        width = self.width() / max(1, len(self.tabs))
        return QtCore.QRectF(index * width, 0, width, self.height() - 4)

    def mousePressEvent(self, event):
        for index in range(len(self.tabs)):
            if self.tab_rect(index).contains(event.position()):
                self.setCurrentIndex(index)
                return

    def event(self, event):
        if event.type() == QtCore.QEvent.Type.ToolTip:
            for index, (label, _sprite) in enumerate(self.tabs):
                if self.tab_rect(index).contains(QtCore.QPointF(event.pos())):
                    QtWidgets.QToolTip.showText(event.globalPos(), label, self)
                    return True
        return super().event(event)

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        line = _sprite(self.sprites, "pic_tongyong_xialafenxian")
        if line is not None:
            painter.drawPixmap(
                QtCore.QRectF(4, self.height() - 3, self.width() - 8, 1.5), line, QtCore.QRectF(line.rect())
            )
        glow = _sprite(self.sprites, "pic_zhuangrong_xuanzhong")
        divider = _sprite(self.sprites, "pic_zhuangrong_xian")
        for index, (label, sprite) in enumerate(self.tabs):
            rect = self.tab_rect(index)
            chosen = index == self._current
            if chosen and glow is not None:
                _draw_fit(painter, rect.adjusted(2, 4, -2, -2), glow)
            if index and divider is not None:
                painter.drawPixmap(
                    QtCore.QRectF(rect.left() - 3, rect.top() + 8, 6, rect.height() - 14),
                    divider,
                    QtCore.QRectF(divider.rect()),
                )
            icon = _sprite(self.sprites, sprite) if sprite else None
            painter.setOpacity(1.0 if chosen else 0.5)
            if icon is not None:
                _draw_fit(painter, QtCore.QRectF(rect.center().x() - 20, rect.center().y() - 20, 40, 40), icon)
            else:
                painter.setPen(QtGui.QColor("#e6cf9c"))
                painter.drawText(rect, int(QtCore.Qt.AlignmentFlag.AlignCenter), label)
            painter.setOpacity(1.0)


class _LayerPanel(QtWidgets.QFrame):
    """
    The character's pieces as a small outliner, like Blender's: groups (Face, Hair, Outfit, Hat...) and their
    meshes, each with the game's eye (pic_waiguan_yulan1) to hide or show it. Click the title to fold it away.
    """

    changed = QtCore.Signal()
    EYE = 1  # the eye column

    def __init__(self, sprites, parent=None):
        super().__init__(parent)
        self.setObjectName("creatorLayers")
        self.sprites = sprites
        self.hidden: set[str] = set()
        self._groups: list = []
        self._eyes = None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)  # Blender's outliner: header strip, then the rows
        layout.setSpacing(0)
        self.title = QtWidgets.QToolButton()
        self.title.setCheckable(True)
        self.title.setChecked(True)
        self.title.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.title.toggled.connect(self._fold)
        self.title.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        layout.addWidget(self.title)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(16)
        self.tree.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self.tree.setIconSize(QtCore.QSize(20, 20))
        self.tree.setTextElideMode(QtCore.Qt.TextElideMode.ElideMiddle)  # keep the telling end (..._pijian)
        self.tree.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # the wheel scrolls
        self.tree.setAutoScroll(False)  # a click on an eye must not slide the list sideways
        self.tree.horizontalScrollBar().rangeChanged.connect(lambda *_: self.tree.horizontalScrollBar().setValue(0))
        self.tree.horizontalScrollBar().valueChanged.connect(
            lambda value: self.tree.horizontalScrollBar().setValue(0) if value else None
        )
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        self.tree.header().setSectionResizeMode(self.EYE, QtWidgets.QHeaderView.ResizeMode.Fixed)
        self.tree.header().resizeSection(self.EYE, 30)
        self.tree.itemClicked.connect(self._clicked)
        self.tree.viewport().installEventFilter(self)  # keeps the eyes on the right edge
        layout.addWidget(self.tree)
        self.setFixedWidth(270)
        self._fold(True)

    def eventFilter(self, obj, event):
        if obj is self.tree.viewport() and event.type() == QtCore.QEvent.Type.Resize:
            self.tree.setColumnWidth(0, max(40, obj.width() - self.tree.columnWidth(self.EYE)))
        return False

    def _fold(self, shown):
        self.title.setText(("\u25bc" if shown else "\u25b6") + "  Layers")
        self.tree.setVisible(shown)
        self.adjustSize()

    def _eye(self, shown: bool):
        """The game's eye, full for shown pieces, faded for hidden ones."""
        eye = _sprite(self.sprites, "pic_waiguan_yulan1")
        if eye is None:
            return QtGui.QIcon()
        if self._eyes is None or self._eyes[0] is not eye:
            faded = QtGui.QPixmap(eye.size())
            faded.fill(QtCore.Qt.GlobalColor.transparent)
            painter = QtGui.QPainter(faded)
            painter.setOpacity(0.22)
            painter.drawPixmap(0, 0, eye)
            painter.end()
            self._eyes = (eye, QtGui.QIcon(eye), QtGui.QIcon(faded))
        return self._eyes[1] if shown else self._eyes[2]

    def set_layers(self, groups):
        """groups: [(title, [piece name, ...])]; the tree is rebuilt only when the pieces change."""
        groups = [(title, list(dict.fromkeys(names))) for title, names in groups if names]
        if groups == self._groups:
            return
        folded = {
            self.tree.topLevelItem(i).text(0)
            for i in range(self.tree.topLevelItemCount())
            if not self.tree.topLevelItem(i).isExpanded()
        }
        self._groups = groups
        self.tree.clear()
        for title, names in groups:
            top = QtWidgets.QTreeWidgetItem([title, ""])
            for name in names:
                child = QtWidgets.QTreeWidgetItem([name, ""])
                child.setData(0, QtCore.Qt.ItemDataRole.UserRole, name)
                child.setToolTip(0, name)
                top.addChild(child)
            self.tree.addTopLevelItem(top)
            top.setExpanded(title not in folded)
        self.refresh()

    def refresh(self):
        """The eyes (and the dimmed names) for the current hidden set."""
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            any_shown = False
            for j in range(top.childCount()):
                child = top.child(j)
                shown = child.data(0, QtCore.Qt.ItemDataRole.UserRole) not in self.hidden
                any_shown |= shown
                child.setIcon(self.EYE, self._eye(shown))
                child.setForeground(0, QtGui.QColor("#eadfc8" if shown else "#6f6658"))
            top.setIcon(self.EYE, self._eye(any_shown))
            top.setForeground(0, QtGui.QColor("#e6cf9c" if any_shown else "#6f6658"))

    def _clicked(self, item, column):
        if column != self.EYE:
            return
        names = (
            [item.child(j).data(0, QtCore.Qt.ItemDataRole.UserRole) for j in range(item.childCount())]
            if item.childCount()
            else [item.data(0, QtCore.Qt.ItemDataRole.UserRole)]
        )
        if all(name in self.hidden for name in names):
            self.hidden.difference_update(names)
        else:
            self.hidden.update(names)
        self.refresh()
        self.changed.emit()

    def sizeHint(self):
        rows = self.tree.topLevelItemCount() + sum(
            self.tree.topLevelItem(i).childCount()
            for i in range(self.tree.topLevelItemCount())
            if self.tree.topLevelItem(i).isExpanded()
        )
        tree = (rows * 28 + 6) if self.title.isChecked() else 0  # unfolded, even while the panel is hidden
        return QtCore.QSize(270, 36 + tree)


class _Backdrop(QtWidgets.QWidget):
    """The game's Filter backdrop (ui\\_bigbg\\bg_huixiang_youdi): dark, fading out at the right edge."""

    def __init__(self, sprites, parent=None):
        super().__init__(parent)
        self.sprites = sprites

    def paintEvent(self, _event):
        backdrop = _sprite(self.sprites, "bg_huixiang_youdi")
        if backdrop is None:
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        # its solid left part stretched, its fading right part kept at the right edge
        w, h = backdrop.width(), backdrop.height()
        split, fade = w * 0.55, min(200.0, self.width() * 0.4)
        solid_w = max(0.0, self.width() - fade)
        painter.drawPixmap(QtCore.QRectF(0, 0, solid_w, self.height()), backdrop, QtCore.QRectF(0, 0, split, h))
        painter.drawPixmap(
            QtCore.QRectF(solid_w, 0, fade, self.height()), backdrop, QtCore.QRectF(split, 0, w - split, h)
        )


class _Tag(QtWidgets.QLabel):
    """One of the game's tag boxes under an item's name (pic_tongyong_biaoqian, stretched to the text)."""

    def __init__(self, text, sprites, font_family=None):
        super().__init__(text)
        self.sprites = sprites
        self.setStyleSheet(
            "color: #f0e3c4; background: transparent; padding: 3px 14px 4px 14px; font-size: 17px;"
            + (f' font-family: "{font_family}";' if font_family else "")
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        box = _sprite(self.sprites, "pic_tongyong_biaoqian")
        if box is not None:
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
            _draw_nine(painter, QtCore.QRectF(self.rect()), box, 10)
            painter.end()
        super().paintEvent(event)


class _ItemTitle(QtWidgets.QWidget):
    """
    The picked item on the stage, as the game shows it: its name in the title face (wrapping onto more lines), the
    game's fine rule with its dots (pic_tongyong_xian), its tags in the game's boxes (wrapping like words), and a small
    line with the rarity and the file. Laid out by hand: fit(width) places everything and returns the height.
    """

    GAP = 10

    def __init__(self, sprites, fonts, parent=None):
        super().__init__(parent)
        self.sprites, self.fonts = sprites, fonts
        self.setObjectName("creatorItemTitle")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.name = QtWidgets.QLabel(self)
        self.name.setWordWrap(True)
        self.name.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop)
        face = f'font-family: "{fonts["title"]}"; letter-spacing: 1px;' if fonts.get("title") else "font-weight: bold;"
        self.name.setStyleSheet(f"color: #e6cf9c; font-size: 30px; background: transparent; {face}")
        self.note = QtWidgets.QLabel(self)
        self.note.setStyleSheet("color: #9a8f7c; background: transparent;")
        self.tags: list = []
        self._text = ""
        self._rule_y = None

    def text(self) -> str:
        return self._text

    def set_item(self, name: str, tags, note_html: str = ""):
        self._text = name
        safe = name.replace("&", "&amp;").replace("<", "&lt;")
        # the title face has tall built-in line spacing: a two-line name is drawn tighter
        tight = "line-height:62%" if self.fonts.get("decorative") else "line-height:100%"  # see TITLE_FACE_FILE
        self.name.setText(f"<div style='{tight}'>{safe}</div>" if name else "")
        for tag in self.tags:
            tag.hide()
            tag.setParent(None)
            tag.deleteLater()
        self.tags = [_Tag(tag, self.sprites, self.fonts.get("body")) for tag in tags]
        for tag in self.tags:
            tag.setParent(self)
            tag.show()
        self.note.setText(note_html)
        self.note.setVisible(bool(note_html))
        self.fit(self.width())

    def fit(self, width: int) -> int:
        """Place the name, rule, tags and note for this width; returns the height they need."""
        width = max(120, width)
        y = 0
        name_height = self.name.heightForWidth(width) if self._text else 0
        self.name.setGeometry(0, 0, width, max(0, name_height))
        y += name_height + 4
        self._rule_y = y
        y += 6 + self.GAP
        x, row_height = 0, 0
        for tag in self.tags:  # tags flow like words, onto more lines when needed
            hint = tag.sizeHint()
            tag_width = min(hint.width(), width)
            if x and x + tag_width > width:
                x, y = 0, y + row_height + 8
                row_height = 0
            tag.setGeometry(x, y, tag_width, hint.height())
            x += tag_width + 8
            row_height = max(row_height, hint.height())
        y += row_height + (self.GAP if self.tags else 0)
        if self.note.isVisibleTo(self):
            note_height = self.note.sizeHint().height()
            self.note.setGeometry(0, y, width, note_height)
            y += note_height
        self.update()
        return y

    def used_width(self, width: int) -> int:
        """The width the wrapped name and the tag rows actually take within `width` (their widest line)."""
        width = max(120, width)
        used = 0
        if self._text:
            self.name.ensurePolished()
            document = QtGui.QTextDocument()
            document.setDefaultFont(self.name.font())
            document.setDocumentMargin(0)
            document.setHtml(self.name.text())
            document.setTextWidth(width)
            used = int(document.idealWidth()) + 2
        x = row = 0
        for tag in self.tags:
            tag_width = min(tag.sizeHint().width(), width)
            if x and x + tag_width > width:
                x = 0
            x += tag_width
            row = max(row, x)
            x += 8
        return max(120, min(width, max(used, row)))

    def paintEvent(self, _event):
        line = _sprite(self.sprites, "pic_tongyong_xian")
        if line is not None and self._rule_y is not None and self._text:
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(QtCore.QRectF(0, self._rule_y, self.width(), 6), line, QtCore.QRectF(line.rect()))


class _ToggleRow(QtWidgets.QAbstractButton):
    """A line of the game's Hide popup: the name, and its round box (gold tick when on) on the right."""

    def __init__(self, text, sprites):
        super().__init__()
        self.sprites = sprites
        self.setText(text)
        self.setCheckable(True)
        self.setFixedHeight(54)
        self.setMinimumWidth(240)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_Hover)

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        rect = QtCore.QRectF(self.rect())
        middle = rect.center().y()
        box = QtCore.QRectF(rect.right() - 46, middle - 17, 34, 34)
        base = _sprite(self.sprites, "pic_duijue_gouxuandiban")
        if base is not None:
            painter.drawPixmap(box, base, QtCore.QRectF(base.rect()))
        tick = _sprite(self.sprites, "pic_ktjs_gou")
        if self.isChecked() and tick is not None:
            _draw_fit(painter, box.adjusted(-2, -6, 6, 0), tick)
        font = painter.font()
        font.setPixelSize(21)
        painter.setFont(font)
        painter.setPen(QtGui.QColor("#ffffff" if self.underMouse() else "#eadfc8"))
        painter.drawText(
            QtCore.QRectF(14, 0, box.left() - 20, rect.height()),
            int(QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignLeft),
            self.text(),
        )


class _HidePopup(QtWidgets.QFrame):
    """The game's eye-button popup: hide the hat or the scarf from the view (and the exports)."""

    toggled = QtCore.Signal(str, bool)

    def __init__(self, sprites, parent=None):
        super().__init__(parent, QtCore.Qt.WindowType.Popup | QtCore.Qt.WindowType.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.sprites = sprites
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(0)
        self.rows = {}
        for kind, label in (("hat", "Hide Hat"), ("scarf", "Hide Scarf")):
            row = _ToggleRow(label, sprites)
            row.toggled.connect(lambda on, kind=kind: self.toggled.emit(kind, on))
            layout.addWidget(row)
            self.rows[kind] = row

    def paintEvent(self, _event):
        frame = _sprite(self.sprites, "pic_tongyong_xialadi")
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        if frame is not None:
            _draw_nine(painter, QtCore.QRectF(self.rect()), frame, 18)
        else:
            painter.fillRect(self.rect(), QtGui.QColor(20, 18, 16, 235))

    def show_above(self, button):
        self.adjustSize()
        corner = button.mapToGlobal(QtCore.QPoint(button.width() // 2, 0))
        self.move(corner.x() - 40, corner.y() - self.height() - 8)
        self.show()
        for row in self.rows.values():
            row.update()


class _RoundButton(QtWidgets.QAbstractButton):
    """A round button like the game's: a disc with a sprite, a colour or a short label; gold ring when on."""

    def __init__(self, tip: str, sprites, size: int = 58, sprite: str | None = None, text: str = ""):
        super().__init__()
        self.sprites, self.sprite, self.label = sprites, sprite, text
        self.color = None
        self.badge = ""
        self.fill = False  # the sprite covers the whole disc (skin swatches)
        self.backdrop = None  # a game disc sprite under the icon, instead of the drawn round frame
        self.bare = False  # the icon alone, no disc (the game's collapse chevron)
        self.flipped = False  # the icon mirrored left-right
        self.setFixedSize(size, size)
        self.setToolTip(tip)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_Hover)

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        circle = QtCore.QRectF(self.rect()).adjusted(3, 3, -3, -3)
        icon = _sprite(self.sprites, self.sprite) if self.sprite else None
        backdrop = _sprite(self.sprites, self.backdrop) if self.backdrop else None
        if (self.bare or backdrop is not None) and icon is not None:
            painter.setOpacity(1.0 if self.underMouse() else 0.9)
            if backdrop is not None:
                painter.drawPixmap(QtCore.QRectF(self.rect()), backdrop, QtCore.QRectF(backdrop.rect()))
            if self.flipped:
                icon = icon.transformed(QtGui.QTransform().scale(-1, 1))
            share = 0.58 if backdrop is not None else 0.7
            side = self.width() * share
            _draw_fit(painter, QtCore.QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side), icon)
            return
        frame = _sprite(self.sprites, "pic_chuangjue_xuanzhong") if self.isChecked() else None
        if frame is not None:  # the game's "picked" frame: gold ring with corner brackets
            circle = QtCore.QRectF(self.rect()).adjusted(7, 7, -7, -7)
        _draw_round(painter, circle, self.sprites, self.isChecked() and frame is None, self.underMouse())
        if self.color is not None:
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(self.color))
            painter.drawEllipse(circle.adjusted(6, 6, -6, -6))
        pixmap = _sprite(self.sprites, self.sprite) if self.sprite else None
        if pixmap is not None and self.fill:
            inner = circle.adjusted(3, 3, -3, -3)
            clip = QtGui.QPainterPath()
            clip.addEllipse(inner)
            painter.setClipPath(clip)
            middle = pixmap.copy(
                pixmap.rect().adjusted(
                    pixmap.width() // 4, pixmap.height() // 4, -pixmap.width() // 4, -pixmap.height() // 4
                )
            )
            painter.drawPixmap(inner, middle, QtCore.QRectF(middle.rect()))  # the swatch's colour, edge to edge
            painter.setClipping(False)
        elif pixmap is not None:
            _draw_fit(painter, circle.adjusted(8, 8, -8, -8), pixmap)
        elif self.color is None and self.label:
            painter.setPen(QtGui.QColor("#e6cf9c"))
            painter.drawText(circle, QtCore.Qt.AlignmentFlag.AlignCenter, self.label)
        if frame is not None:
            painter.drawPixmap(QtCore.QRectF(self.rect()), frame, QtCore.QRectF(frame.rect()))
        if self.badge:
            font = painter.font()
            font.setBold(True)
            font.setPointSizeF(font.pointSizeF() + 1)
            painter.setFont(font)
            painter.setPen(QtGui.QColor(GOLD if self.isChecked() else "#9c8355"))
            painter.drawText(
                circle.adjusted(0, 0, -3, -1),
                QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignBottom,
                self.badge,
            )
        painter.end()


class _SwatchPopup(QtWidgets.QFrame):
    """Every dye of a hair colour list as round swatches; a click picks it."""

    def __init__(self, parent, combo: QtWidgets.QComboBox, sprites, first_tip: str):
        super().__init__(parent, QtCore.Qt.WindowType.Popup)
        from core.npk.hair_dye import hex_color

        self.setObjectName("creatorPopup")
        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setSpacing(2)
        columns = 7
        for row in range(combo.count()):
            rgb = combo.itemData(row)
            button = _RoundButton(
                first_tip if rgb is None else combo.itemText(row),
                sprites,
                46,
                sprite="pic_chuangjue_wu" if rgb is None else None,
                text="—" if rgb is None else "",
            )
            if rgb is not None:
                button.color = hex_color(rgb)
            button.setCheckable(True)
            button.setChecked(row == combo.currentIndex())
            button.clicked.connect(lambda _checked=False, row=row: (combo.setCurrentIndex(row), self.close()))
            grid.addWidget(button, row // columns, row % columns)
