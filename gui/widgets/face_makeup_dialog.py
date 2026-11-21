"""
Character creator: build a character like the game's editor (face, shape, skin tone, makeup, eyes, hair and
dye, outfit and accessories), preview it in 3D and export it as GLB / PMX / OBJ.
"""

from __future__ import annotations

import ntpath
import re
import threading
from PySide6 import QtCore, QtGui, QtWidgets
from core.logger import get_logger
from core.npk.accessory_sockets import apply_bind_offsets
from gui.widgets.face_makeup_data import (  # noqa: F401
    KINDS, WARDROBE, _hair_data, _library, _wardrobe, choice_key, dress_face_items, dress_hair_items,
    dress_wardrobe_items, face_folder_of, load_face_items, load_wardrobe_items, without_old_eye_faces,
)
from gui.widgets.face_makeup_widgets import (  # noqa: F401
    CREATOR_STYLE, GOLD, ICON_ROLE, LIST_WIDTH, LIST_WIDTH_EXPANDED, RAIL_WIDTH, RARITY_COLORS, RARITY_NAMES, RARITY_ROLE,
    SHORT_LABELS, SOCKETS_ROLE, SOURCE_ROLE, TEXTURE_ROLE, TRAIT_ROLE, UI_SPRITES, ORDER_ROLE, SORT_ROLE, TAGS_ROLE,
    _AllTiles, _DragScroll, _FilterButton, _FilterPanel, _FilterRow, _ScrollMarker, game_font_style, game_fonts, _ExpandArrow, _RoundButton, _SearchBox, _SlotTabs, _LayerPanel, _HidePopup, _Backdrop, _ItemTitle,
    _SwatchPopup, _Tiles, _sprite,
)
from gui.widgets.face_makeup_workers import (  # noqa: F401
    _Signals, _bake_worker, _load, _load_hair_worker, _load_icons, _load_item_icons, _load_meshes_worker,
    _load_part_worker, _load_ui_sprites_early, export_face,
)


RAIL_BUTTON, RAIL_GAP, RAIL_ICON = 62, 10, 40       # rail button height, the space between buttons, icon size


_TRIMMED: dict = {}


def _trimmed(pixmap):
    """
    The sprite cut to its visible pixels: the rail sprites carry different empty margins, so scaled as they are the
    coat looks big and the face small; trimmed, every icon fills the button's box the same way.
    """
    if pixmap is None:
        return None
    key = pixmap.cacheKey()
    if key not in _TRIMMED:
        image = pixmap.toImage()
        left, top, right, bottom = image.width(), image.height(), -1, -1
        for y in range(image.height()):
            for x in range(image.width()):
                if image.pixelColor(x, y).alpha() > 24:
                    left, top = min(left, x), min(top, y)
                    right, bottom = max(right, x), max(bottom, y)
        if right < left:
            _TRIMMED[key] = pixmap
        else:
            side = max(right - left, bottom - top) + 1           # square, centred: tall and wide icons stay in shape
            cx, cy = (left + right) / 2, (top + bottom) / 2
            box = QtCore.QRect(int(cx - side / 2), int(cy - side / 2), side, side)
            square = QtGui.QPixmap(side, side)
            square.fill(QtCore.Qt.GlobalColor.transparent)
            painter = QtGui.QPainter(square)
            painter.drawPixmap(0, 0, pixmap, box.x(), box.y(), side, side)
            painter.end()
            _TRIMMED[key] = square
    return _TRIMMED[key]


def game_tables_tag_names(library) -> dict:
    """{tag key: the game's name} (cached by game_tables); {} before the game data is loaded."""
    if library is None:
        return {}
    from core.npk import game_tables  # pylint: disable=import-outside-toplevel
    return game_tables.tag_names(library.tables)


class FaceMakeupDialog(QtWidgets.QDialog):
    def __init__(self, main_window, game, folder=None):
        # No owner, like NeoXtractor's viewer windows: its own taskbar button, minimised and switched to on its own
        super().__init__(None)
        self.main_window = main_window
        self.game = game
        self.wanted_folder = folder
        self.library = None
        self.faces: dict[str, list[str]] = {}
        self.files: dict[str, list[str]] = {}
        self.options: dict[str, dict] = {}
        self.generation = 0
        self.preview_image = None
        self.setWindowTitle("Main Character Creator")
        self.setWindowFlag(QtCore.Qt.WindowType.WindowMaximizeButtonHint, True)
        screen = QtGui.QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else QtCore.QRect(0, 0, 1600, 1000)
        self.resize(max(1280, int(area.width() * 0.94)), max(800, int(area.height() * 0.92)))
        self.fonts = game_fonts(game)                   # the game's own title and text faces, when installed
        if self.fonts.get("body"):
            body = QtGui.QFont(self.fonts["body"])
            body.setPointSizeF(max(self.font().pointSizeF(), 9.5))
            self.setFont(body)
        self.setStyleSheet(CREATOR_STYLE + game_font_style(self.fonts))

        # The choices live in combo boxes (the rest of the dialog reads them); the panels show most of them as
        # tile grids that share the combo's item list
        def combo(tip=None, visible=20):
            box = QtWidgets.QComboBox()
            box.setMaxVisibleItems(visible)
            if tip:
                box.setToolTip(tip)
            return box

        self.face = combo()
        self.shape = combo()
        self.skin = combo()
        self.preset = combo()                      # the game's face presets (face model + shape), shown as tiles
        self.choices: dict[str, QtWidgets.QComboBox] = {kind: combo() for kind, _label in KINDS}
        self.hair = combo("A dyeable hairstyle, shown on the head and exported with the face.")
        self.hair_color1 = combo("Main hair colour (where the dye mask is red).")
        self.hair_color2 = combo("Second colour: two-tone hair blends from it to the main colour. "
                                 "Leave on \"Same as colour 1\" for one colour.")
        self.wardrobe: dict[str, QtWidgets.QComboBox] = {kind: combo() for kind, _label in WARDROBE}

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Floating bars (put on the stage below): view switch + look popover (top right), scene / save / export
        self.view_bar = QtWidgets.QFrame()
        self.view_bar.setObjectName("creatorViewBar")
        bar = QtWidgets.QHBoxLayout(self.view_bar)
        bar.setContentsMargins(4, 4, 4, 4)
        bar.setSpacing(2)
        self.view_buttons = QtWidgets.QButtonGroup(self)
        for number, (label, tip) in enumerate((("3D", "The character in 3D"),
                                               ("Texture", "The baked face texture"))):
            button = QtWidgets.QToolButton()
            button.setText(label)
            button.setToolTip(tip)
            button.setCheckable(True)
            button.setChecked(number == 0)
            self.view_buttons.addButton(button, number)
            bar.addWidget(button)
        options = QtWidgets.QToolButton()
        options.setText("\u25bc")
        options.setToolTip("Look and preview options")
        options.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        popover = QtWidgets.QMenu(options)
        popover.setObjectName("creatorPopover")
        popover.addSection("Look")
        popover.addAction("Random look").triggered.connect(lambda _checked=False: self._randomize())
        popover.addAction("Clear makeup").triggered.connect(lambda _checked=False: self._clear())
        options.setMenu(popover)
        bar.addWidget(options)

        self.action_bar = QtWidgets.QFrame()
        self.action_bar.setObjectName("creatorActionBar")
        actions_row = QtWidgets.QHBoxLayout(self.action_bar)
        actions_row.setContentsMargins(4, 4, 4, 4)
        actions_row.setSpacing(4)
        add_scene = QtWidgets.QToolButton()
        add_scene.setText("Add to Scene Viewer")
        add_scene.setToolTip("Put this character (shape, makeup, eyes, eyebrows, dyed hair and outfit) into the "
                             "Scene Viewer, to export it together with other parts.")
        add_scene.clicked.connect(self._add_to_scene)
        save = QtWidgets.QToolButton()
        save.setText("Save texture \u25bc")
        save.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        save_menu = QtWidgets.QMenu(save)
        save_menu.setObjectName("creatorPopover")
        for label, tip, slot in (
                ("Face texture as PNG…", "The face texture with the makeup baked in.", self._save_texture),
                ("Eye texture as PNG…", "The iris texture (like eye_01.png) with the chosen eye type and colour.",
                 self._save_eye_texture),
                ("Eyebrow texture as PNG…", "The separate eyebrow mesh's texture in the chosen eyebrow colour.",
                 self._save_eyebrow_texture),
                ("Hair texture as PNG…", "The chosen hairstyle's texture in the chosen dye colours.",
                 self._save_hair_texture)):
            action = save_menu.addAction(label)
            action.setToolTip(tip)
            action.triggered.connect(lambda _checked=False, slot=slot: slot())
        save_menu.setToolTipsVisible(True)
        save.setMenu(save_menu)
        export = QtWidgets.QToolButton()
        export.setObjectName("creatorExport")
        export.setText("Export \u25bc")
        export.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        export_menu = QtWidgets.QMenu(export)
        export_menu.setObjectName("creatorPopover")
        for label, fmt in (("GLB…", "glb"), ("PMX…", "pmx"), ("OBJ…", "obj")):
            export_menu.addAction(label).triggered.connect(lambda _checked=False, fmt=fmt: self._export(fmt))
        try:                                       # the PMX switches only change PMX exports: in their own submenu
            from core.mesh_converter.formats.pmx import pmx_option_actions  # pylint: disable=import-outside-toplevel
            export_menu.addSeparator()
            pmx_menu = export_menu.addMenu("PMX options")
            pmx_menu.setObjectName("creatorPopover")
            self.pmx_actions = pmx_option_actions(pmx_menu)
            for action in self.pmx_actions:
                pmx_menu.addAction(action)
            pmx_menu.setToolTipsVisible(True)
        except Exception:  # pylint: disable=broad-except
            self.pmx_actions = ()
        export.setMenu(export_menu)
        for button in (add_scene, save, export):
            button.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0; }")
            actions_row.addWidget(button)
        self.export_buttons = [add_scene, save, export]
        for button in self.export_buttons:
            button.setEnabled(False)

        # Body: icon rail | item list | stage (3D view or texture, with floating buttons)
        self.sprites: dict[str, QtGui.QPixmap] = {}
        body = QtWidgets.QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        root.addLayout(body, 1)
        self.rail = QtWidgets.QFrame()
        self.rail.setObjectName("creatorRail")
        self.rail.setFixedWidth(RAIL_WIDTH)
        # The buttons keep the game's size and scroll (wheel or drag, no bar) when the window is too short
        rail_frame = QtWidgets.QVBoxLayout(self.rail)
        rail_frame.setContentsMargins(0, 0, 0, 0)
        self.rail_scroll = QtWidgets.QScrollArea()
        self.rail_scroll.setWidgetResizable(True)
        self.rail_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.rail_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rail_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rail_scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        self.rail_buttons = QtWidgets.QWidget()
        self.rail_scroll.setWidget(self.rail_buttons)
        rail_frame.addWidget(self.rail_scroll)
        rail_layout = QtWidgets.QVBoxLayout(self.rail_buttons)
        rail_layout.setContentsMargins(4, 6, 4, 6)
        rail_layout.setSpacing(RAIL_GAP)
        body.addWidget(self.rail)
        self.panel = QtWidgets.QFrame()
        self.panel.setObjectName("creatorPanel")
        self.panel.setFixedWidth(LIST_WIDTH)
        panel_layout = QtWidgets.QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(0, 8, 4, 8)          # the pages keep their own margin (room for halos)
        panel_layout.setSpacing(4)
        self.panel_title = QtWidgets.QLabel()
        self.panel_title.setObjectName("creatorTitle")
        self.panel_title.setContentsMargins(8, 0, 0, 0)
        self.panel_title.setText("New Look Mirror")
        panel_layout.addWidget(self.panel_title)
        self.filter = QtWidgets.QLineEdit()
        self.filter.setToolTip("Search by name or file")
        self.filter.setClearButtonEnabled(False)
        self.filter.textChanged.connect(self._filter_changed)
        self.search_box = _SearchBox(self.filter, self.sprites)
        self.search_box.hide()                         # shown with the expanded list
        self.search_row = QtWidgets.QHBoxLayout()
        self.search_row.setContentsMargins(8, 0, 0, 0)
        self.search_row.addWidget(self.search_box)
        panel_layout.addLayout(self.search_row)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)   # scrolled by drag or wheel
        self.pages = QtWidgets.QStackedWidget()
        scroll.setWidget(self.pages)
        panel_layout.addWidget(scroll, 1)
        self.panel_scroll = scroll
        # The game's Filter under the list: it opens the Rarity / Trait checklists in the list's place
        self.filter_panel = _FilterPanel(self.sprites)
        self.filter_panel.changed.connect(self._item_filter_changed)
        # The open Filter: one layer over the list and the scene from under the title, the search
        # bar on top (it moves in while the Filter is open), then the checklists, on the game's fading backdrop
        self.filter_overlay = _Backdrop(self.sprites, self)
        overlay_layout = QtWidgets.QVBoxLayout(self.filter_overlay)
        overlay_layout.setContentsMargins(0, 8, 0, 8)
        overlay_layout.setSpacing(4)
        self.filter_search_row = QtWidgets.QHBoxLayout()
        self.filter_search_row.setContentsMargins(8, 0, 70, 0)
        overlay_layout.addLayout(self.filter_search_row)
        self.filter_overlay.hide()
        self.filter_scroll = QtWidgets.QScrollArea()
        self.filter_scroll.setWidgetResizable(True)
        self.filter_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.filter_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.filter_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.filter_scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        self.filter_scroll.setWidget(self.filter_panel)
        overlay_layout.addWidget(self.filter_scroll, 1)
        self.filter_panel.set_wide(True)
        self.filter_button = _FilterButton(self.sprites)
        self.filter_button.setObjectName("creatorFilter")
        self.filter_button.setCheckable(True)
        self.filter_button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.filter_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.filter_button.setToolTip("Show only some rarities or traits")
        self.filter_button.toggled.connect(self._toggle_filter_panel)
        # As in the game: "Filtered" and an X to clear it while the Filter hides something, then the sort button
        self.filter_reset = _RoundButton("Clear the filter", self.sprites, 36, sprite="icon_shejiao_guanbi", text="×")
        self.filter_reset.backdrop = "pic_duijue_gouxuandiban"
        self.filter_reset.clicked.connect(self.filter_panel.reset)
        self.filter_reset.hide()
        self.rare_first = True                                 # Mythic first
        self.sort_button = _RoundButton("Rarest first (click for the most common first)", self.sprites, 56,
                                        sprite="icon_shengxu", text="⇅")
        self.sort_button.backdrop = "btn_mfxj_diban"
        self.sort_button.clicked.connect(self._flip_sort)
        self.filter_bar = QtWidgets.QWidget()
        self.filter_bar.setObjectName("creatorFilterBar")   # see-through: it also sits on the open Filter's gradient
        filter_row = QtWidgets.QHBoxLayout(self.filter_bar)
        filter_row.setContentsMargins(8, 0, 0, 0)
        filter_row.setSpacing(6)
        filter_row.addWidget(self.filter_button)
        filter_row.addWidget(self.filter_reset)
        filter_row.addWidget(self.sort_button)
        filter_row.addStretch(1)
        panel_layout.addWidget(self.filter_bar)
        self._update_filter_button()
        self.scroll_marker = _ScrollMarker(self.panel_scroll, self.sprites, self.panel)   # shown while scrolling
        body.addWidget(self.panel)
        self.stage = QtWidgets.QWidget()
        self.stage.installEventFilter(self)
        body.addWidget(self.stage, 1)

        self.tabs = QtWidgets.QStackedWidget(self.stage)
        self.view3d = None
        try:
            from gui.widgets.scene_render import SceneRenderWidget  # pylint: disable=import-outside-toplevel
            self.view3d = SceneRenderWidget()
            self.view3d.show_bones = False
            self.tabs.addWidget(self.view3d)
        except Exception as exc:  # pylint: disable=broad-except
            self.tabs.addWidget(QtWidgets.QLabel(f"3D preview unavailable: {exc}"))
        self.texture_page = QtWidgets.QWidget()
        texture_layout = QtWidgets.QVBoxLayout(self.texture_page)
        self.preview = QtWidgets.QLabel("…")
        self.preview.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(200, 200)
        self.preview.setStyleSheet("background: #202020; color: #888")
        texture_layout.addWidget(self.preview, 1)
        self.tabs.addWidget(self.texture_page)
        self.view_buttons.idClicked.connect(self.tabs.setCurrentIndex)
        self.view_bar.setParent(self.stage)
        self.action_bar.setParent(self.stage)
        self.tabs.currentChanged.connect(lambda _i: (self._layout_overlays(), self._show_preview()))

        # Categories: one rail button each, with the game's wardrobe icon
        labels = dict(KINDS)
        wardrobe_labels = dict(WARDROBE)
        # The rail follows the game's order. Glasses and earrings, scarf and brooch, iris and eye colour, and eyeshadow
        # and blush share a button each and switch with icon tabs.
        wardrobe_icons = {"clothes": "pic_duijiaoxiang_tubiao1", "hat": "pic_fuzhuangdian_shaixuan_maozi",
                          "earring": "icon_ershi", "glasses": "icon_yanjing", "scarf": "pic_fuzhuangdian_shaixuan_weijin",
                          "brooch": "icon_huizhang", "bag": "icon_beibao_01"}

        def ward(kind):
            return ("tiles", wardrobe_labels.get(kind, kind), self.wardrobe[kind], wardrobe_icons.get(kind))

        by_key = {
            "all": ("All", [("all", "All", None, None)], "pic_yimaojian_quanbu", None),
            "face": ("Face", [("tiles", "Face", self.preset, None)], "icon_lianxing", self.preset),
            "eyes": ("Eyes", [("tiles", labels["eyetype"], self.choices["eyetype"], "pic_zr_yanzhu_01"),
                              ("tiles", labels["eyecolor"], self.choices["eyecolor"], "icon_fuzhuang_tiaosepan")],
                     "icon_tongkongxijie", None),
            "makeup": ("Makeup", [("tiles", labels["eyeshadow"], self.choices["eyeshadow"], "pic_zr_tongkong_01"),
                                  ("tiles", labels["blush"], self.choices["blush"], "icon_mianbuxijie")],
                       "icon_mianbuxijie", None),
            "lips": (labels["lips"], [("tiles", labels["lips"], self.choices["lips"], None)], "icon_chunse", None),
            "brows": (labels["eyebrow"], [("tiles", labels["eyebrow"], self.choices["eyebrow"], None)], "icon_meimao",
                      None),
            "hair": ("Hairstyle", [("tiles", "Hairstyle", self.hair, None)], "pic_fuzhuangdian_shaixuan_toufa", None),
        }
        for kind in ("clothes", "hat", "bag"):
            if kind in self.wardrobe:
                by_key[kind] = (wardrobe_labels[kind], [ward(kind)], wardrobe_icons[kind], None)
        for first, second in (("glasses", "earring"), ("scarf", "brooch")):
            if first in self.wardrobe:
                rows = [ward(first)] + ([ward(second)] if second in self.wardrobe else [])
                by_key[first] = (wardrobe_labels[first], rows, wardrobe_icons[first], None)
        order = ("all", "clothes", "hair", "hat", "glasses", "scarf", "face", "eyes", "makeup", "lips", "brows", "bag")
        categories = [(key,) + by_key[key] for key in order if key in by_key]
        all_sources = [(self.wardrobe["clothes"], True), (self.hair, True)] + \
            [(self.wardrobe[kind], True) for kind in ("hat", "glasses", "scarf", "earring", "brooch", "bag")
             if kind in self.wardrobe] + [(self.preset, False)]
        notes_for = {"clothes": "An outfit brings the body piece it is worn with (neck, hands…) in the face's skin tone.",
                     "hat": "With a hat, the hairstyle's trimmed version from the hat is used.",
                     "face": "Face shapes are the game's edited versions of this face; newer faces have none."}
        self.category_buttons = QtWidgets.QButtonGroup(self)
        self.category_buttons.setExclusive(False)
        self.categories = []                       # (key, title, sprite, title combo)
        for number, (key, title, rows, sprite, title_combo) in enumerate(categories):
            page = QtWidgets.QWidget()
            page_layout = QtWidgets.QVBoxLayout(page)
            page_layout.setContentsMargins(16, 12, 4, 0)       # room for the picked tile's halo
            page_layout.setSpacing(4)
            tile_rows = [(label, box) for kind, label, box, _tab in rows if kind == "tiles"]
            slot_bar, sections, form = None, [], None
            if len(tile_rows) > 1:               # several slots: one grid at a time, picked by icon tabs on top
                slot_bar = _SlotTabs(self.sprites)
                page_layout.addWidget(slot_bar)
            for kind, label, box, tab in rows:
                if kind == "all":
                    self.all_tiles = _AllTiles(all_sources, self.sprites)
                    self.all_tiles.item_filter = self.filter_panel.passes
                    page_layout.addWidget(self.all_tiles)
                    continue
                if kind == "tiles":
                    form = None
                    section = QtWidgets.QWidget()
                    section_layout = QtWidgets.QVBoxLayout(section)
                    section_layout.setContentsMargins(0, 0, 0, 0)
                    if slot_bar is not None:                 # the tab names the slot, the stage title the item
                        slot_bar.addTab(SHORT_LABELS.get(label, label), tab)
                    tiles = _Tiles(box, self.sprites)
                    tiles.item_filter = self.filter_panel.passes
                    section_layout.addWidget(tiles)
                    page_layout.addWidget(section)
                    sections.append(section)
                else:
                    if form is None:
                        form = QtWidgets.QFormLayout()
                        page_layout.addLayout(form)
                    form.addRow(label, box)
            page_layout.addStretch(1)
            if slot_bar is not None:
                def pick_slot(index, sections=sections, number=number):
                    for i, section in enumerate(sections):
                        section.setVisible(i == index)
                    self.panel_scroll.verticalScrollBar().setValue(0)
                    self._update_title()
                slot_bar.currentChanged.connect(pick_slot)
                pick_slot(0)
            self.pages.addWidget(page)
            button = QtWidgets.QToolButton()
            button.setText(title[:5])
            button.setToolTip(" — ".join([title, notes_for[key]]) if key in notes_for else title)   # no text in the list
            button.setCheckable(True)
            button.setIconSize(QtCore.QSize(RAIL_ICON, RAIL_ICON))
            button.setFixedSize(RAIL_WIDTH - 8, RAIL_BUTTON)
            button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)   # no focus box: only the glow marks the choice
            self.category_buttons.addButton(button, number)
            rail_layout.addWidget(button)
            boxes = [box for kind, _label, box, _tab in rows if kind == "tiles"]
            self.categories.append({"key": key, "title": title, "sprite": sprite, "boxes": boxes, "slots": slot_bar,
                                    "title_combo": title_combo,
                                    "slot_titles": [label for kind, label, _box, _tab in rows if kind == "tiles"]})
            for box in boxes + ([title_combo] if title_combo is not None else []):
                box.currentIndexChanged.connect(self._update_title)
        rail_layout.addStretch(1)
        self.category_buttons.idClicked.connect(self._category_clicked)
        self.rail_glow = QtWidgets.QLabel(self.rail_buttons)
        self.rail_glow.setScaledContents(True)
        self.rail_glow.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.rail_glow.hide()

        # Floating over the stage: round buttons (hair dye, body size), the item's name, view controls, notes
        # Female / Male, drawn like the 3D / Texture switch: the face presets and every list follow it
        self.sex_bar = QtWidgets.QFrame(self.stage)
        self.sex_bar.setObjectName("creatorViewBar")
        sex_row = QtWidgets.QHBoxLayout(self.sex_bar)
        sex_row.setContentsMargins(4, 4, 4, 4)
        sex_row.setSpacing(2)
        self.sex_buttons = QtWidgets.QButtonGroup(self)
        for number, label in enumerate(("Female", "Male")):
            button = QtWidgets.QToolButton()
            button.setText(label)
            button.setToolTip(f"{label} characters (starts from a random look)")
            button.setCheckable(True)
            self.sex_buttons.addButton(button, number)
            sex_row.addWidget(button)
        self.sex_buttons.idClicked.connect(lambda number: self._switch_sex(("female", "male")[number]))
        self.side_column = QtWidgets.QWidget(self.stage)
        self.side_column.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.side_column.setAutoFillBackground(False)
        side = QtWidgets.QVBoxLayout(self.side_column)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(6)
        self.dye_original = _RoundButton("Original hair colour", self.sprites, sprite="pic_chuangjue_wu", text="—")
        self.dye_original.clicked.connect(self._dye_original)
        self.dye_buttons = []
        for number, (combo, tip) in enumerate(((self.hair_color1, "Hair colour (main)"),
                                               (self.hair_color2, "Second hair colour (two-tone)"))):
            button = _RoundButton(tip, self.sprites, sprite="pic_moyao", text=str(number + 1))
            button.clicked.connect(lambda _checked=False, number=number: self._pick_dye(number))
            self.dye_buttons.append(button)
        self.dye_add = _RoundButton("Add a second colour (two-tone hair)", self.sprites, sprite="pic_jd_tianjia", text="+")
        self.dye_add.clicked.connect(lambda: self._pick_dye(1))
        self.size_buttons = QtWidgets.QButtonGroup(self)
        size_widgets = []
        for number, (key, tip) in enumerate((("original", "Body size I (as modelled)"),
                                             ("adult", "Body size II (grown-up)"))):
            button = _RoundButton(tip, self.sprites, sprite="pic_tixing_xuanzhong", text="I" * (number + 1))
            button.badge = "I" * (number + 1)
            button.setCheckable(True)
            self.size_buttons.addButton(button, number)
            size_widgets.append(button)
        self.size_buttons.idClicked.connect(self._size_clicked)
        self.dye_widgets = [self.dye_original] + self.dye_buttons + [self.dye_add]
        self.size_widgets = size_widgets
        for widget in self.dye_widgets + self.size_widgets:
            side.addWidget(widget)
        for combo in (self.hair, self.hair_color1, self.hair_color2):
            combo.currentIndexChanged.connect(self._update_dye_buttons)

        self.skin_column = QtWidgets.QScrollArea(self.stage)       # skin tones, beside the face tiles
        self.skin_column.setObjectName("creatorSkins")
        self.skin_column.setWidgetResizable(True)
        self.skin_column.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.skin_column.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.skin_column.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.skin_column.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        self.skin_holder = QtWidgets.QWidget()
        self.skin_layout = QtWidgets.QVBoxLayout(self.skin_holder)
        self.skin_layout.setContentsMargins(0, 0, 0, 0)
        self.skin_layout.setSpacing(4)
        self.skin_column.setWidget(self.skin_holder)
        self.skin_buttons = QtWidgets.QButtonGroup(self)
        self.skin_buttons.idClicked.connect(lambda row: self.skin.setCurrentIndex(row))
        self.skin.currentIndexChanged.connect(self._update_skin_buttons)
        self.skin_column.hide()

        self.layers = _LayerPanel(self.sprites, self.stage)    # hide pieces from the view and the exports
        self.layers.changed.connect(self._update_3d)
        self.layers.title.toggled.connect(lambda _shown: self._layout_overlays())
        self.layers.hide()
        self.item_title = _ItemTitle(self.sprites, self.fonts, self.stage)
        self.view_controls = QtWidgets.QWidget(self.stage)
        self.view_controls.setObjectName("creatorViewControls")
        self.view_controls.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view_controls.setAutoFillBackground(False)
        controls = QtWidgets.QHBoxLayout(self.view_controls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(8)
        for tip, sprite, text, slot in (("Reset the view", "pic_rukou_chongzhi", "⟲", self._reset_view),
                                        ("Hide the hat or the scarf", "pic_waiguan_yulan1", "◉",
                                         self._show_hide_popup),
                                        ("Collapse the item list", "pic_zhiboxin_jiantou", "«", self._toggle_list)):
            button = _RoundButton(tip, self.sprites, 60, sprite=sprite, text=text)
            if sprite == "pic_zhiboxin_jiantou":
                button.bare = True
                self.collapse_button = button
            else:
                button.backdrop = "btn_mfxj_diban"
            button.clicked.connect(slot)
            controls.addWidget(button)

        self.hidden_slots: set[str] = set()              # worn pieces the Hide popup takes off the character
        self.hide_popup = _HidePopup(self.sprites, self)
        self.hide_popup.toggled.connect(self._hide_slot)
        self.expand_arrow = _ExpandArrow(self.sprites, self.stage)
        self.expand_arrow.clicked.connect(self._toggle_expanded)
        self.list_expanded = False

        self.notes = QtWidgets.QLabel("Loading the game's makeup data…", self.stage)
        self.notes.setObjectName("creatorNotes")
        self.notes.setWordWrap(True)
        self.notes.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._panels_hidden = False
        self._update_size_buttons()

        self.face_items = None          # (key, items) of the loaded face meshes
        self.dressed = None             # the last bake for the 3D view
        self.objects3d = []
        self._focused_face = None
        self.eye_preview = QtWidgets.QLabel(self.preview)         # iris inset, bottom-right of the preview
        self.eye_preview.setFixedSize(150, 150)
        self.eye_preview.setStyleSheet("background: #202020; border: 1px solid #444")
        self.eye_preview.hide()
        self.eye_image = None
        self.brow_preview = QtWidgets.QLabel(self.preview)        # eyebrow inset, above the iris
        self.brow_preview.setFixedSize(150, 150)
        self.brow_preview.setStyleSheet("background: #202020; border: 1px solid #444")
        self.brow_preview.hide()
        self.brow_image = None
        self.list_drag = _DragScroll(self.panel_scroll)
        self.filter_drag = _DragScroll(self.filter_scroll)
        self.list_drag.watch_children()
        self.skin_drag = _DragScroll(self.skin_column)
        self.rail_drag = _DragScroll(self.rail_scroll)
        self.rail_drag.watch_children()
        self._show_category(self.category_number("all"))

        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._bake)
        self._signals = _Signals(self)
        self._signals.loaded.connect(self._loaded)
        self._signals.icons.connect(self._set_icons)
        self._signals.baked.connect(self._baked)
        self._signals.meshes.connect(self._meshes_loaded)
        self._signals.hair.connect(self._hair_loaded)
        self._signals.part.connect(self._part_loaded)
        self._signals.item_icons.connect(self._item_icons_loaded)
        self._signals.ui_sprites.connect(self._ui_sprites_loaded)
        from gui.widgets.character_anim_export import _game_options  # pylint: disable=import-outside-toplevel
        threading.Thread(target=_load, args=(game, self._signals, _game_options()), daemon=True).start()
        threading.Thread(target=_load_ui_sprites_early, args=(game, self._signals), daemon=True).start()

    # -- rail, list and floating buttons -----------------------------------------------------------
    def eventFilter(self, obj, event):  # pylint: disable=invalid-name
        if obj is getattr(self, "stage", None) and event.type() == QtCore.QEvent.Type.Resize:
            self._layout_overlays()
        return super().eventFilter(obj, event)

    def _layout_overlays(self):
        if not hasattr(self, "notes"):
            return
        rect = self.stage.rect()
        self.tabs.setGeometry(rect)
        self.side_column.adjustSize()
        self.side_column.move(12, 12)
        side_right = self.side_column.geometry().right() + 8 if self.side_column.isVisibleTo(self.stage) else 0
        if hasattr(self, "skin_column"):
            needed = self.skin_holder.sizeHint().height()
            self.skin_column.setGeometry(12, 12, 76, max(60, min(needed + 2, rect.height() - 80)))
            if self.skin_column.isVisibleTo(self.stage):
                side_right = max(side_right, self.skin_column.geometry().right() + 8)
            self.skin_column.raise_()
        self.sex_bar.adjustSize()
        sex_left = 12
        for column in (self.side_column, getattr(self, "skin_column", None)):
            if column is not None and column.isVisibleTo(self.stage):
                sex_left = max(sex_left, column.geometry().right() + 10)
        self.sex_bar.move(sex_left, 12 + max(0, (58 - self.sex_bar.height()) // 2))
        self.action_bar.adjustSize()
        self.action_bar.move(rect.width() - self.action_bar.width() - 12, rect.height() - self.action_bar.height() - 12)
        self.view_controls.adjustSize()
        self.view_controls.move(12, rect.height() - self.view_controls.height() - 10)
        # the 3D / Texture switch at the bottom, in the middle (just above when the bottom row is too narrow)
        self.view_bar.adjustSize()
        left_edge = self.view_controls.geometry().right() + 16
        right_edge = self.action_bar.geometry().left() - 16
        x = max(left_edge, min((rect.width() - self.view_bar.width()) // 2, right_edge - self.view_bar.width()))
        if right_edge - left_edge >= self.view_bar.width():
            y = rect.height() - self.view_bar.height() - 12
        else:
            y = self.action_bar.geometry().top() - self.view_bar.height() - 10
        self.view_bar.move(x, y)
        self.texture_page.layout().setContentsMargins(side_right + 8, 20, 8, self.action_bar.height() + 20)
        title_width = max(240, min(340, rect.width() - side_right - 40))
        title_width = self.item_title.used_width(title_width)   # as wide as its widest line: flush with the corner
        self.item_title.resize(title_width, self.item_title.fit(title_width))
        self.item_title.move(rect.width() - self.item_title.width() - 16, 16)
        self.item_title.setVisible(self.tabs.currentIndex() == 0 and bool(self.item_title.text()))
        if hasattr(self, "layers"):
            top = self.item_title.geometry().bottom() + 12 if self.item_title.isVisible() else 16
            room = max(60, self.action_bar.geometry().top() - 12 - top)
            height = min(self.layers.sizeHint().height(), room)
            self.layers.setFixedHeight(height)
            self.layers.move(rect.width() - self.layers.width() - 12, self.action_bar.geometry().top() - 12 - height)
            self.layers.setVisible(self.tabs.currentIndex() == 0 and bool(self.layers._groups))  # pylint: disable=protected-access
            self.layers.raise_()
        left = self.view_controls.geometry().right() + 12
        self.notes.setFixedWidth(max(200, min(520, rect.width() - left - self.action_bar.width() - 36)))
        self.notes.adjustSize()
        self.notes.move(left, rect.height() - self.notes.height() - 12)
        self.notes.hide()
        if hasattr(self, "collapse_button"):                 # « while the list is open, » when it is folded away
            flipped = not self.panel.isHidden()
            if self.collapse_button.flipped != flipped:
                self.collapse_button.flipped = flipped
                self.collapse_button.update()
        if hasattr(self, "expand_arrow"):
            self.expand_arrow.setVisible(not self.panel.isHidden())
            self.expand_arrow.move(0, max(0, (rect.height() - self.expand_arrow.height()) // 2))
        for widget in (self.side_column, self.sex_bar, self.item_title, self.view_controls, self.notes, self.view_bar,
                       self.action_bar):
            widget.raise_()
        if hasattr(self, "expand_arrow"):
            self.expand_arrow.raise_()

    def _set_notes(self, text):
        """The creator's status messages (loading, missing pieces, errors) go to the log, not onto the 3D view."""
        if text and text != getattr(self, "_last_note", None):
            get_logger().info("Character creator: %s", text)
        self._last_note = text
        self.notes.setText(text)
        self._layout_overlays()

    def _category_clicked(self, number):
        if self.pages.currentIndex() == number and not self.panel.isHidden():
            self.panel.hide()                         # clicking the open category folds the list away
            self.category_buttons.button(number).setChecked(True)
            self._layout_overlays()
            return
        self._show_category(number)

    def _show_category(self, number):
        if number is None:
            number = max(0, self.pages.currentIndex())
        for button in self.category_buttons.buttons():
            button.setChecked(self.category_buttons.id(button) == number)
        QtCore.QTimer.singleShot(0, self._place_rail_glow)
        if self.filter.text():
            self.filter.clear()
        self.pages.setCurrentIndex(number)
        category = self.categories[number]
        self.panel_scroll.verticalScrollBar().setValue(0)
        if not self._panels_hidden:
            self.panel.show()
        key = category["key"]
        for widget in self.dye_widgets:
            widget.setVisible(key == "hair")
        for widget in self.size_widgets:
            widget.setVisible(key in dict(WARDROBE) or key == "all")
        self.side_column.setVisible(not self._panels_hidden and (key in ("hair", "all") or key in dict(WARDROBE)))
        self.skin_column.setVisible(not self._panels_hidden and key == "face")
        self.sex_bar.setVisible(not self._panels_hidden)
        self._update_dye_buttons()
        self._update_title()
        self._layout_overlays()
        QtCore.QTimer.singleShot(0, self._show_preview)

    def _place_rail_glow(self):
        """The game's selection glow behind the checked rail button, its bright bar on the side facing the list."""
        glow = getattr(self, "rail_glow", None)
        sprite = _sprite(self.sprites, "pic_fuzhuangdian_xuanzhongguang")
        button = next((b for b in self.category_buttons.buttons() if b.isChecked()), None)
        if glow is None or sprite is None or button is None:
            if glow is not None:
                glow.hide()
            return
        if getattr(self, "_rail_glow_of", None) is not sprite:
            # stored with the bar along the bottom: a quarter turn puts it on the right, pointer facing the list
            glow.setPixmap(sprite.transformed(QtGui.QTransform().rotate(-90)))
            self._rail_glow_of = sprite
            self.setStyleSheet(self.styleSheet() + "\n#creatorRail QToolButton:checked "
                                                   "{ background: transparent; border-left: none; }\n")
        rect = button.geometry()
        glow.setGeometry(0, rect.top() - 4, self.rail_buttons.width(), rect.height() + 8)
        glow.show()
        glow.lower()

    def category_number(self, key: str) -> int:
        """The rail position of a category ("all", "face", "hair", "clothes", ...)."""
        return next((number for number, category in enumerate(self.categories) if category["key"] == key), 0)

    def _filter_changed(self, text):
        page = self.pages.currentWidget()
        for tiles in page.findChildren(_Tiles) if page is not None else []:
            tiles.apply_filter(text)

    def _toggle_filter_panel(self, shown):
        if shown:
            self.filter_overlay.layout().insertWidget(0, self.panel_title)
            self.filter_search_row.addWidget(self.search_box)
            self.search_box.show()
            self.filter_overlay.layout().addWidget(self.filter_bar)
            self._place_filter()
            self.filter_overlay.show()
            self.filter_overlay.raise_()
        else:
            self.filter_overlay.hide()
            self.search_row.addWidget(self.search_box)
            self.search_box.setVisible(self.list_expanded)
            self.panel.layout().addWidget(self.filter_bar)
            self.panel.layout().insertWidget(0, self.panel_title)
        self._update_filter_button()

    def _place_filter(self):
        """From under the title to the bottom (behind the Filter button), as wide as the expanded list and a bit more."""
        if not hasattr(self, "filter_overlay") or not self.panel.isVisible():
            return
        top = self.panel.mapTo(self, QtCore.QPoint(0, 0)).y()
        left = self.panel.mapTo(self, QtCore.QPoint(0, 0)).x()
        bottom = self.panel.mapTo(self, QtCore.QPoint(0, self.panel.height())).y()
        self.filter_overlay.setGeometry(left, top, LIST_WIDTH_EXPANDED + 60, max(160, bottom - top))
        # the lines spread down the whole height (the Trait column has the most: All + 7)
        lines = 1 + max(len(rows) for _h, _a, rows in self.filter_panel.columns.values())
        self.filter_panel.set_row_height((bottom - top - self.panel_title.sizeHint().height() - 50 - 56 - 24
                                          - self.filter_bar.sizeHint().height() - 16) // max(1, lines))

    def _item_filter_changed(self):
        for tiles in self.pages.findChildren(_Tiles):
            tiles.apply_filter(tiles._filter)  # pylint: disable=protected-access
        self._update_filter_button()

    def _update_filter_button(self):
        """"Filter", or "Filtered" in gold with the X to clear it while it hides something (the arrow turns with it)."""
        active = self.filter_panel.active()
        self.filter_button.setText("Filtered" if active else "Filter")
        self.filter_button.active = active
        self.filter_button.update()
        if hasattr(self, "filter_reset"):
            self.filter_reset.setVisible(active)

    def _sorted_combos(self):
        return [self.preset, self.hair] + list(self.choices.values()) + list(self.wardrobe.values())

    def _sort_lists(self):
        """
        Sorting every item list by rarity. Mythic first (or Basic first if click on the sort button), keeping the hpma
        order within each rarity. Each list keeps its current pick.
        """
        for combo in self._sorted_combos():
            model = combo.model()
            if not hasattr(model, "setSortRole"):
                continue
            for row in range(model.rowCount()):
                index = model.index(row, 0)                    # indexes, not QStandardItem wrappers (see _AllTiles)
                if model.data(index, ORDER_ROLE) is None:      # first sort after filling: remember the data order
                    model.setData(index, row, ORDER_ROLE)
                if model.data(index, QtCore.Qt.ItemDataRole.UserRole) is None:
                    key = -1                                   # "None"
                else:
                    rarity = model.data(index, RARITY_ROLE)
                    rarity = rarity if rarity in (1, 2, 3, 4, 5) else 1
                    key = (5 - rarity if self.rare_first else rarity) * 1_000_000 + int(model.data(index, ORDER_ROLE))
                model.setData(index, key, SORT_ROLE)
            model.setSortRole(SORT_ROLE)
            model.sort(0, QtCore.Qt.SortOrder.AscendingOrder)

    def _flip_sort(self):
        self.rare_first = not self.rare_first
        self.sort_button.sprite = "icon_shengxu" if self.rare_first else "icon_jiangxu"
        self.sort_button.setToolTip("Rarest first (click for the most common first)" if self.rare_first
                                    else "Most common first (click for the rarest first)")
        self.sort_button.update()
        self._sort_lists()

    @staticmethod
    def _category_title(category) -> str:
        """The list's title: the category, or the picked half of a paired one (Glasses / Earrings ...)."""
        slots = category.get("slots")
        titles = category.get("slot_titles") or []
        if slots is not None and 0 <= slots.currentIndex() < len(titles):
            return titles[slots.currentIndex()]
        return category["title"]

    def _current_combo(self):
        if not getattr(self, "categories", None):
            return None
        category = self.categories[max(0, self.pages.currentIndex())]
        if category["title_combo"] is not None:
            return category["title_combo"]
        boxes = category["boxes"]
        slot = category["slots"].currentIndex() if category["slots"] is not None else 0
        return boxes[max(0, min(slot, len(boxes) - 1))] if boxes else None

    def _update_title(self, *_args):
        if not hasattr(self, "item_title"):
            return
        combo = self._current_combo()
        text = combo.currentText() if combo is not None else ""
        name, _sep, stem = text.partition("·")
        category = self.categories[max(0, self.pages.currentIndex())]
        if combo is not None and combo.currentData() is None and combo is not self.face:
            self.item_title.set_item("", ())
        else:
            # the game's tags (type, traits) by their own names; the category when the item has none
            keys = combo.itemData(combo.currentIndex(), TAGS_ROLE) if combo is not None else None
            names = game_tables_tag_names(self.library) if keys else {}
            tags = [names[k] for k in (keys or ()) if k in names] or [self._category_title(category)]
            self.item_title.set_item(name.strip().upper(), tags)
        self._layout_overlays()

    def _refresh_category_icons(self, *_args):
        self._update_title()

    def _reset_view(self):
        if self.view3d is not None and self.objects3d:
            self.view3d.focus(self.objects3d)
            self.view3d.update()

    def _toggle_panels(self):
        self._panels_hidden = not self._panels_hidden
        self.rail.setVisible(not self._panels_hidden)
        self.panel.setVisible(not self._panels_hidden)
        self.item_title.setVisible(not self._panels_hidden)
        self._show_category(self.pages.currentIndex())
        if self._panels_hidden:
            self.panel.hide()
            self.side_column.hide()
            self.skin_column.hide()
            self.sex_bar.hide()
        self._layout_overlays()

    def _toggle_expanded(self):
        """
        Switches between the wide list (five tiles across, with the search bar on top) and the narrow one (two across).
        Folding it back also clears the search.
        """
        self.list_expanded = not self.list_expanded
        self.panel.setFixedWidth(LIST_WIDTH_EXPANDED if self.list_expanded else LIST_WIDTH)
        self.search_box.setVisible(self.list_expanded or self.filter_overlay.isVisible())
        self.expand_arrow.set_expanded(self.list_expanded)
        if not self.list_expanded and self.filter.text():
            self.filter.clear()
        self._layout_overlays()
        QtCore.QTimer.singleShot(0, self._reflow_tiles)      # once the panel has its new width

    def _reflow_tiles(self):
        """Every grid laid out again for the panel's width, so none keeps its old columns."""
        self.pages.layout().activate() if self.pages.layout() is not None else None
        for tiles in self.pages.findChildren(_Tiles):
            tiles.reflow()

    def _show_hide_popup(self):
        self.hide_popup.show_above(self.sender() or self.view_controls)

    def _hide_slot(self, kind, hidden):
        """Hide Hat / Hide Scarf: the piece stays picked but leaves the view and the exports."""
        (self.hidden_slots.add if hidden else self.hidden_slots.discard)(kind)
        self._wardrobe_changed()

    def _toggle_list(self):
        if self._panels_hidden:
            self._toggle_panels()
            return
        self.panel.setVisible(self.panel.isHidden())
        self._layout_overlays()

    # -- hair dye circles and body size ------------------------------------------------------------
    def _update_dye_buttons(self, *_args):
        if not hasattr(self, "dye_buttons"):
            return
        from core.npk.hair_dye import hex_color  # pylint: disable=import-outside-toplevel
        for button, combo in zip(self.dye_buttons, (self.hair_color1, self.hair_color2)):
            rgb = combo.currentData()
            button.color = hex_color(rgb) if rgb is not None else None
            button.sprite = None if rgb is not None else "pic_moyao"
            button.setToolTip(combo.currentText())
            button.update()
        self.dye_original.setCheckable(True)
        self.dye_original.setChecked(self.hair_color1.currentData() is None and self.hair_color2.currentData() is None)
        hair = self.hair.currentData()
        dyeable = not isinstance(hair, dict) or bool(hair.get("qs"))   # basic hairstyles have preset colours
        hair_page = self.categories[max(0, self.pages.currentIndex())]["key"] == "hair" and dyeable
        for widget in [self.dye_original, self.dye_buttons[0]]:
            widget.setVisible(hair_page)
        two_tone = self.hair_color2.currentData() is not None
        self.dye_buttons[1].setVisible(hair_page and two_tone)
        self.dye_add.setVisible(hair_page and not two_tone)
        self._layout_overlays()

    def _rebuild_skin_buttons(self, *_args):
        """One round swatch per skin tone of this face, in the game's order, with the game's swatch icon."""
        for button in self.skin_buttons.buttons():
            self.skin_buttons.removeButton(button)
            # out of the column first: the drag scroll re-watches the column's buttons right after, and a filter
            # left on a button being deleted crashed the app (a face change rebuilds these buttons)
            if getattr(self, "skin_drag", None) is not None:
                button.removeEventFilter(self.skin_drag)
            button.hide()
            button.setParent(None)
            button.deleteLater()
        icons = (self.options or {}).get("skin_icons", {}).get(self._sex(), {}) if self.library else {}
        QtCore.QTimer.singleShot(0, lambda: getattr(self, "skin_drag", None) and self.skin_drag.watch_children())
        rows = []
        for row in range(self.skin.count()):
            number = self.skin.itemData(row)
            order, icon = icons.get(number, (10000 + row, "")) if number is not None else (-1, "")
            rows.append((order, row, icon))
        for _order, row, icon in sorted(rows):
            button = _RoundButton(self.skin.itemText(row), self.sprites, 54, sprite=icon or None,
                                  text=str(self.skin.itemData(row) or "·"))
            button.setCheckable(True)
            button.fill = True
            self.skin_buttons.addButton(button, row)
            self.skin_layout.addWidget(button)
        self._update_skin_buttons()
        self._layout_overlays()

    def _update_skin_buttons(self, *_args):
        button = self.skin_buttons.button(self.skin.currentIndex())
        if button is not None:
            button.setChecked(True)
            self.skin_column.ensureWidgetVisible(button)

    def _dye_original(self):
        self.hair_color2.setCurrentIndex(0)
        self.hair_color1.setCurrentIndex(0)
        self._update_dye_buttons()

    def _pick_dye(self, number):
        combo = (self.hair_color1, self.hair_color2)[number]
        if combo.count() <= 1:
            return
        popup = _SwatchPopup(self, combo, self.sprites, "Original colour" if number == 0 else "Same as colour 1 (one colour)")
        anchor = (self.dye_buttons[number] if number == 0 or self.dye_buttons[1].isVisible() else self.dye_add)
        popup.adjustSize()
        popup.move(anchor.mapToGlobal(QtCore.QPoint(anchor.width() + 8, 0)))
        popup.show()

    def _settings(self):
        app = QtWidgets.QApplication.instance()
        return app.property("settings_manager") if app is not None else None

    def _update_size_buttons(self):
        from gui.widgets.character_exports import body_size_adult  # pylint: disable=import-outside-toplevel
        self.size_buttons.button(1 if body_size_adult() else 0).setChecked(True)

    def _size_clicked(self, number):
        from gui.widgets.character_exports import BODY_SIZE_SETTING  # pylint: disable=import-outside-toplevel
        settings = self._settings()
        if settings is not None:
            settings.set(BODY_SIZE_SETTING, "adult" if number == 1 else "original", True)
        self._update_size_buttons()
        self._update_3d()

    def _sized_items(self, items):
        """The 3D pieces in body size II when it is chosen (the export's own deform, cached per mesh)."""
        from gui.widgets.character_exports import body_size_adult  # pylint: disable=import-outside-toplevel
        if not body_size_adult() or self.library is None:
            return items
        from core.mesh_converter import body_size  # pylint: disable=import-outside-toplevel
        paths = tuple(self.wardrobe_paths()) + tuple(self.faces.get(self.face.currentData(), []))
        cache = self.__dict__.setdefault("_size_cache", {})
        if cache.get("paths") != paths:
            notes = []
            try:
                deformer, _source = body_size.deformer_for(
                    lambda path: (self.library.index.read(path) or (None,))[0], list(paths), notes)
            except Exception as exc:  # pylint: disable=broad-except
                deformer = None
                notes.append(str(exc))
            cache.clear()
            cache.update(paths=paths, deformer=deformer, meshes={})
        deformer = cache["deformer"]
        if deformer is None:
            return items
        meshes = cache["meshes"]
        sized = []
        for item in items:
            key = id(item["mesh"])
            if key not in meshes:
                meshes[key] = (item["mesh"], deformer.apply(item["mesh"]))
            sized.append(dict(item, mesh=meshes[key][1]))
        live = {id(item["mesh"]) for item in items}
        for key in [k for k in meshes if k not in live]:
            del meshes[key]
        return sized

    # -- game UI sprites -----------------------------------------------------------------------------
    def _ui_sprites_loaded(self, sprites):
        from gui.widgets.scene_render import pil_to_qimage  # pylint: disable=import-outside-toplevel
        for name, image in sprites.items():
            self.sprites[name] = QtGui.QPixmap.fromImage(pil_to_qimage(image))
        QtCore.QTimer.singleShot(0, self._place_rail_glow)
        for number, category in enumerate(self.categories):
            pixmap = _trimmed(_sprite(self.sprites, category["sprite"]))
            button = self.category_buttons.button(number)
            if pixmap is not None:
                button.setIcon(QtGui.QIcon(pixmap))
                button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
        for widget in self.findChildren(QtWidgets.QWidget):
            if isinstance(widget, (_Tiles,)):
                widget.viewport().update()
            elif isinstance(widget, _ItemTitle):
                widget.update()
                for tag in widget.tags:
                    tag.update()
            elif isinstance(widget, _LayerPanel):
                widget.refresh()
            elif isinstance(widget, (_RoundButton, _ExpandArrow, _SearchBox, _FilterRow, _FilterButton, _SlotTabs,
                                     _FilterPanel)):
                widget.update()

    # -- loading -----------------------------------------------------------------------------
    def _loaded(self, payload, error):
        if error:
            self._set_notes(f"Could not read the makeup data: {error}")
            return
        self.library, self.faces, self.files, self.options = payload
        for folder in self.faces:
            self.face.addItem(folder.rsplit("\\", 1)[-1], folder)
        wanted = False
        if self.wanted_folder:
            found = self.face.findData(next((f for f in self.faces if f.lower() == self.wanted_folder.lower()), None))
            if found >= 0:
                self.face.setCurrentIndex(found)
                wanted = True
        from core.npk.hair_dye import hex_color  # pylint: disable=import-outside-toplevel
        for combo, first in ((self.hair_color1, "Original colour"), (self.hair_color2, "Same as colour 1")):
            combo.addItem(first, None)
            for color_id, rgb, _main in self.options["hair"]["colors"]:
                swatch = QtGui.QPixmap(28, 16)
                swatch.fill(QtGui.QColor(hex_color(rgb)))
                combo.addItem(QtGui.QIcon(swatch), f"Dye {color_id}  {hex_color(rgb)}", rgb)
        self._all_presets = list(self.options.get("presets", []))     # shown for the chosen sex (_fill_presets)
        self._sort_lists()
        self.face.currentIndexChanged.connect(self._face_changed)
        self.preset.currentIndexChanged.connect(self._preset_changed)
        self.face.currentIndexChanged.connect(self._sync_preset)
        self.face.currentIndexChanged.connect(self._rebuild_skin_buttons)
        self.shape.currentIndexChanged.connect(self._sync_preset)
        for combo in [self.shape, self.skin, self.hair, self.hair_color1, self.hair_color2] + \
                list(self.choices.values()):
            combo.currentIndexChanged.connect(self._schedule)
        for combo in self.wardrobe.values():                # no re-bake: only the changed piece reloads
            combo.currentIndexChanged.connect(self._wardrobe_changed)
        self._face_changed()
        for button in self.export_buttons:
            button.setEnabled(True)
        threading.Thread(target=_load_icons, args=(self.library, self.options, self._signals), daemon=True).start()
        self._sync_preset()
        self._rebuild_skin_buttons()
        self._update_dye_buttons()
        self._update_title()
        if not wanted:
            self._random_look("female")

    def _set_icons(self, icons):
        self._icons_cache = icons
        for kind, combo in self.choices.items():
            for row in range(combo.count()):
                option = combo.itemData(row)
                image = icons.get((option.kind, option.id)) if option is not None else None
                if image is not None:
                    combo.setItemIcon(row, QtGui.QIcon(QtGui.QPixmap.fromImage(image)))
        self._refresh_category_icons()

    # -- item icons (hairstyles, outfits, accessories) ----------------------------------------
    def _preset_changed(self, _index=None):
        self._apply_preset(self.preset.currentData())

    def _apply_preset(self, key):
        """A face preset: its face model, then its shape (the face change refills the shape list first)."""
        if not key:
            return
        folder, shape = key.split("|", 1)
        row = self.face.findData(folder)
        if row < 0:
            return
        if self.face.currentIndex() != row:
            self.face.setCurrentIndex(row)              # -> _face_changed fills this face's shapes
        target = 0
        for number in range(self.shape.count()):
            if (self.shape.itemData(number) or "") == shape:
                target = number
                break
        self.shape.setCurrentIndex(target)

    def _sync_preset(self, _index=None):
        """Keep the preset tile on the face and shape shown (also when they were set another way)."""
        key = f"{self.face.currentData() or ''}|{self.shape.currentData() or ''}"
        row = self.preset.findData(key)
        if row >= 0 and row != self.preset.currentIndex():
            blocker = QtCore.QSignalBlocker(self.preset)
            self.preset.setCurrentIndex(row)
            del blocker
            self._update_title()

    def _item_combos(self):
        return [self.hair, self.preset] + list(self.wardrobe.values())

    def _request_item_icons(self):
        cache = self.__dict__.setdefault("item_icons", {})
        asked = self.__dict__.setdefault("_icons_asked", set())
        wanted = []
        for combo in self._item_combos():
            for row in range(combo.count()):
                icon = combo.itemData(row, ICON_ROLE)
                if icon and icon not in cache and icon not in asked:
                    asked.add(icon)
                    wanted.append(icon)
        if wanted:
            threading.Thread(target=_load_item_icons, args=(self.library.index, wanted, self._signals), daemon=True).start()

    def _item_icons_loaded(self, icons):
        self.item_icons.update(icons)
        self._apply_item_icons()

    def _apply_item_icons(self):
        cache = getattr(self, "item_icons", {})
        for combo in self._item_combos():
            for row in range(combo.count()):
                icon = combo.itemData(row, ICON_ROLE)
                image = cache.get(icon) if icon else None
                if image is not None and combo.itemIcon(row).isNull():
                    combo.setItemIcon(row, QtGui.QIcon(QtGui.QPixmap.fromImage(image)))
        self._refresh_category_icons()
        if getattr(self, "all_tiles", None) is not None:
            self.all_tiles.schedule()

    def _sex(self):
        from core.npk.face_makeup import face_sex  # pylint: disable=import-outside-toplevel
        return face_sex(self.face.currentData() or "")

    def _switch_sex(self, sex):
        if sex != self._sex():
            self._random_look(sex)

    def _random_look(self, sex):
        """A random face preset, hairstyle and outfit of this sex (named items only, when there are any)."""
        import random  # pylint: disable=import-outside-toplevel
        from core.npk.face_makeup import face_sex  # pylint: disable=import-outside-toplevel
        keys = [p["key"] for p in getattr(self, "_all_presets", []) if face_sex(p["key"].split("|", 1)[0]) == sex]
        if not keys:
            return
        self._apply_preset(random.choice(keys))
        for combo in (self.hair, self.wardrobe.get("clothes")):
            if combo is None or combo.count() < 2:
                continue
            rows = [row for row in range(1, combo.count()) if combo.itemData(row, ICON_ROLE)] or \
                list(range(1, combo.count()))
            combo.setCurrentIndex(random.choice(rows))

    def _fill_presets(self, sex):
        from core.npk.face_makeup import face_sex  # pylint: disable=import-outside-toplevel
        blocker = QtCore.QSignalBlocker(self.preset)
        self.preset.clear()
        for preset in getattr(self, "_all_presets", []):
            if face_sex(preset["key"].split("|", 1)[0]) != sex:
                continue
            self.preset.addItem(preset["name"], preset["key"])
            self.preset.setItemData(self.preset.count() - 1, preset.get("icon"), ICON_ROLE)
            self.preset.setItemData(self.preset.count() - 1, preset.get("rarity"), RARITY_ROLE)
            self.preset.setItemData(self.preset.count() - 1, tuple(preset.get("traits") or ()), TRAIT_ROLE)
            self.preset.setItemData(self.preset.count() - 1, tuple(preset.get("tags") or ()), TAGS_ROLE)
        del blocker
        self._preset_sex = sex

    def _face_changed(self):
        folder = self.face.currentData()
        if not folder:
            return
        sex = self._sex()
        self.sex_buttons.button(0 if sex == "female" else 1).setChecked(True)
        if getattr(self, "_preset_sex", None) != sex:
            self._fill_presets(sex)
        blockers = [QtCore.QSignalBlocker(w) for w in [self.shape, self.skin] + list(self.choices.values())]
        previous = {kind: (combo.currentData().row.get("id") if combo.currentData() is not None else None)
                    for kind, combo in self.choices.items()}
        self.shape.clear()
        self.shape.addItem("Base face", None)
        shape_files = {p.rsplit("\\", 1)[-1][:-3].rsplit("_", 1)[-1]: p
                       for p in sorted(p for p in self.files.get(folder, []) if p.lower().endswith(".mb"))}
        offered = [s for s in self.options.get("shapes", {}).get(folder.rsplit("\\", 1)[-1], []) if s in shape_files]
        if not self.options.get("shapes"):
            offered = list(shape_files)              # no table: offer every shape file
        for suffix in offered:                       # shape files the game never offers are left out
            self.shape.addItem(f"Shape {suffix}", shape_files[suffix])
        previous_skin = self.skin.currentData()
        self.skin.clear()
        offered = (self.options or {}).get("skin_icons", {}).get(sex, {})     # the tones the game offers
        skins = [skin for skin in self.library.skins(folder) if not offered or skin[0] in offered]
        if not skins:
            self.skin.addItem("Face's own texture", None)
        for number, label, _base, _tint in skins:
            self.skin.addItem(label, number)
        numbers = [self.skin.itemData(row) for row in range(self.skin.count())]
        if previous_skin in numbers:
            self.skin.setCurrentIndex(numbers.index(previous_skin))
        elif offered and numbers:                    # else the first tone in the game's order
            self.skin.setCurrentIndex(min(range(len(numbers)), key=lambda r: offered.get(numbers[r], (9999,))[0]))
        for kind, combo in self.choices.items():
            combo.clear()
            combo.addItem("None", None)
            for option in self.options[sex][kind]:
                combo.addItem(option.name, option)
                combo.setItemData(combo.count() - 1, getattr(option, "rarity", None), RARITY_ROLE)
                combo.setItemData(combo.count() - 1, tuple(getattr(option, "traits", ()) or ()), TRAIT_ROLE)
                combo.setItemData(combo.count() - 1, tuple(getattr(option, "tags", ()) or ()), TAGS_ROLE)
            # keep the same look when switching between faces of the same sex
            if previous[kind] is not None:
                for row in range(1, combo.count()):
                    if combo.itemData(row).row.get("id") == previous[kind]:
                        combo.setCurrentIndex(row)
        if getattr(self, "_wardrobe_sex", None) != sex:
            for kind, combo in self.wardrobe.items():
                ward_blocker = QtCore.QSignalBlocker(combo)
                combo.clear()
                combo.addItem("None", None)
                for entry in self.options["wardrobe"]["items"][sex][kind]:
                    combo.addItem(entry["name"], entry["path"])
                    combo.setItemData(combo.count() - 1, entry.get("icon"), ICON_ROLE)
                    combo.setItemData(combo.count() - 1, entry.get("texture"), TEXTURE_ROLE)
                    combo.setItemData(combo.count() - 1, entry.get("sockets") or [], SOCKETS_ROLE)
                    combo.setItemData(combo.count() - 1, entry.get("rarity"), RARITY_ROLE)
                    combo.setItemData(combo.count() - 1, tuple(entry.get("traits") or ()), TRAIT_ROLE)
                    combo.setItemData(combo.count() - 1, tuple(entry.get("tags") or ()), TAGS_ROLE)
                del ward_blocker
            self._wardrobe_sex = sex
        if getattr(self, "_hair_sex", None) != sex:
            hair_blocker = QtCore.QSignalBlocker(self.hair)
            self.hair.clear()
            self.hair.addItem("None", None)
            for hair in self.options["hair"]["hairs"]:
                if hair["sex"] == sex:
                    self.hair.addItem(hair["name"], hair)
                    self.hair.setItemData(self.hair.count() - 1, hair.get("icon"), ICON_ROLE)
                    self.hair.setItemData(self.hair.count() - 1, hair.get("rarity"), RARITY_ROLE)
                    self.hair.setItemData(self.hair.count() - 1, tuple(hair.get("traits") or ()), TRAIT_ROLE)
                    self.hair.setItemData(self.hair.count() - 1, tuple(hair.get("tags") or ()), TAGS_ROLE)
            self._hair_sex = sex
            del hair_blocker
        self._sort_lists()
        if hasattr(self, "_icons_cache"):
            self._set_icons(self._icons_cache)
        del blockers
        self._apply_item_icons()
        self._request_item_icons()
        self._schedule()

    # -- look --------------------------------------------------------------------------------
    def look(self):
        from core.npk.face_makeup import Look  # pylint: disable=import-outside-toplevel
        return Look(skin=self.skin.currentData(), **{kind: combo.currentData() for kind, combo in self.choices.items()})

    def _randomize(self):
        import random  # pylint: disable=import-outside-toplevel
        for combo in [self.skin] + list(self.choices.values()):
            if combo.count() > 1:
                combo.setCurrentIndex(random.randrange(1 if combo is not self.skin else 0, combo.count()))

    def _clear(self):
        for combo in list(self.choices.values()) + [self.hair_color1, self.hair_color2]:
            combo.setCurrentIndex(0)

    # -- wardrobe ----------------------------------------------------------------------------
    def _worn(self):
        """The wardrobe slots with a piece on, without those the Hide popup hides."""
        hidden = getattr(self, "hidden_slots", ())
        return [combo for kind, combo in self.wardrobe.items() if combo.currentData() and kind not in hidden]

    def wardrobe_paths(self) -> list[str]:
        return [combo.currentData() for combo in self._worn()]

    def wardrobe_choices(self) -> list[tuple[str, str | None, tuple]]:
        """(mesh path, recolour texture or None, attachment ids) of each piece worn (and not hidden)."""
        return [(combo.currentData(), combo.itemData(combo.currentIndex(), TEXTURE_ROLE),
                 tuple(combo.itemData(combo.currentIndex(), SOCKETS_ROLE) or ()))
                for combo in self._worn()]

    def hair_path(self):
        """The hairstyle's mesh, or its trimmed version from the chosen hat."""
        hair = self.hair.currentData()
        if hair is None:
            return None
        hat = self.wardrobe["hat"].currentData() if "hat" not in getattr(self, "hidden_slots", ()) else None
        if hat:
            masks = self.options["wardrobe"]["masks"].get(hat.rsplit("\\", 1)[0].lower(), {})
            stem = hair["folder"].rsplit("\\", 1)[-1].lower()
            if stem in masks:
                return masks[stem]
        return hair["mesh"]

    def _wardrobe_changed(self, _index=None):
        if self.dressed is None:
            self._schedule()
            return
        self._ensure_meshes()
        self._update_3d()

    def _ensure_parts(self):
        cache = self.__dict__.setdefault("part_items", {})
        loading = self.__dict__.setdefault("_loading_parts", set())
        for path, texture, sockets in self.wardrobe_choices():
            key = self._part_key(path, texture)
            if key not in cache and key not in loading:
                loading.add(key)
                threading.Thread(target=_load_part_worker, daemon=True,
                                 args=(self.library, path, texture, sockets, self._signals, self._sex(), key)).start()

    def _part_key(self, path, texture) -> str:
        """A loaded piece is the wearer's: socketed pieces hang on that sex's rig (shared brooches, ...)."""
        return f"{choice_key(path, texture)}|{self._sex()}"

    def _part_loaded(self, path, items, error):
        self._loading_parts.discard(path)
        if error:
            self._set_notes(f"{ntpath.basename(path.split('|')[0])}: {error}")
            items = []
        self.part_items[path] = items
        self._update_3d()

    def wardrobe_items(self, sides, complete=True):
        """
        Returns the dressed items for the chosen pieces. With complete=True, it returns None until every piece has
        loaded; or else it returns only the pieces that are ready.
        """
        cache = getattr(self, "part_items", {})
        skin = sides.get(None) or sides.get("left")
        items = []
        for path, texture, _sockets in self.wardrobe_choices():
            key = self._part_key(path, texture)
            if key not in cache:
                if complete:
                    return None
                continue
            for item in dress_wardrobe_items(cache[key], skin):
                item.setdefault("path", path if not item.get("body_piece") else None)
                items.append(item)
        return items

    def worn_for_offsets(self) -> list[str]:
        """The hairstyle (its own model, not a hat's trimmed copy) and outfit the game's accessory offsets key on."""
        hair = self.hair.currentData()
        clothes = self.wardrobe["clothes"].currentData() if "clothes" in self.wardrobe else None
        return [path for path in ((hair or {}).get("mesh") if isinstance(hair, dict) else None, clothes) if path]

    def hair_choice(self):
        hair = self.hair.currentData()
        if hair is None:
            return None
        return (hair, self.hair_color1.currentData(), self.hair_color2.currentData())

    def _schedule(self):
        self._timer.start()

    def _bake(self):
        folder = self.face.currentData()
        if not folder or self.library is None:
            return
        self.generation += 1
        args = (self.generation, self.library, folder, self._sex(), self.look(), None,
                self._face_texture(folder), self.options["hair"], self.hair_choice())
        threading.Thread(target=_bake_worker, args=args + (self._signals,), daemon=True).start()

    def _face_texture(self, folder):
        """The face's own texture, for faces without skin-tone variants."""
        stem = folder.rsplit("\\", 1)[-1]
        for path in self.files.get(folder, []):
            name = path.rsplit("\\", 1)[-1].lower()
            if name.startswith(stem.lower()) and name.endswith((".dds", ".tga", ".png")) and "_ao" not in name \
                    and "mask" not in name and "eyes" not in name and "eyebrow" not in name:
                return path
        return None

    def _baked(self, generation, image, split, error):
        if generation != self.generation:
            return
        if error:
            self.preview.setText(error)
            self._set_notes(error)
            self._ensure_meshes()               # the shape and outfit still load; the last good look stays on
            self._update_3d()
            return
        self.preview_image = image
        self.dressed = split
        self._ensure_meshes()
        self._update_3d()
        self.eye_image = split.get("eye")
        self.brow_image = split.get("eyebrow")
        self._show_preview()
        notes = split.get("notes", [])
        if split.get("split"):
            notes = ["This look has a one-cheek decal: the export gives the face a left and a right material, "
                     "each with its own texture."] + notes
        self._set_notes("\n".join(notes))

    # -- 3D --------------------------------------------------------------------------------------
    def _mesh_key(self):
        return (self.face.currentData(), self.shape.currentData())

    def _ensure_hair(self):
        path = self.hair_path()
        if self.view3d is None or path is None or (getattr(self, "hair_items", None) or (None,))[0] == path \
                or getattr(self, "_loading_hair", None) == path:
            return
        self._loading_hair = path
        threading.Thread(target=_load_hair_worker, args=(self.library, path, self._signals), daemon=True).start()

    def _hair_loaded(self, path, items, error):
        self._loading_hair = None
        if error:
            self._set_notes(f"Hairstyle: {error}")
            return
        self.hair_items = (path, items)
        self._update_3d()

    def _ensure_meshes(self):
        self._ensure_hair()
        self._ensure_parts()
        key = self._mesh_key()
        if self.view3d is None or not key[0] or (self.face_items is not None and self.face_items[0] == key) \
                or getattr(self, "_loading_key", None) == key:
            return
        self._loading_key = key
        folder, shape = key
        threading.Thread(target=_load_meshes_worker, args=(self.library, key, self.faces.get(folder, []),
                                                           self.files.get(folder, []), shape, self._signals),
                         daemon=True).start()

    def _meshes_loaded(self, key, items, error):
        if getattr(self, "_loading_key", None) == key:
            self._loading_key = None
        if key != self._mesh_key():
            self._ensure_meshes()
            return
        if error:
            self._set_notes(f"3D preview: {error}")
            return
        self.face_items = (key, items)
        self._update_3d()

    def _update_3d(self):
        if self.view3d is None or self.face_items is None or self.dressed is None or self.face_items[0] != self._mesh_key():
            return
        from gui.widgets.scene_render import SceneObject, pil_to_qimage  # pylint: disable=import-outside-toplevel
        folder = self.face.currentData()
        face = folder.rsplit("\\", 1)[-1]
        look = self.look()
        sides = self.dressed.get("sides") or {None: self.preview_image}
        items, _eyes = dress_face_items(self.library, self.face_items[1], self._sex(), face, look, sides,
                                        self.dressed.get("eye"), self.dressed.get("eyebrow"))
        hair = self.hair.currentData()
        hair_items = getattr(self, "hair_items", None)
        face_count = len(items)
        hair_list = []
        if hair is not None and hair_items is not None and hair_items[0] == self.hair_path():
            hair_list = dress_hair_items(hair_items[1], hair, self.dressed.get("hair"))
            items += hair_list
        wardrobe = self.wardrobe_items(sides, complete=False)
        items += wardrobe
        self._set_layers(items[:face_count], hair_list, wardrobe)
        items = apply_bind_offsets(self.library, items, self.worn_for_offsets())
        items = self._sized_items(items)
        items = [item for item in items if item["name"] not in self.layers.hidden]
        # Keep the pieces that did not change (same mesh, same textures): only new ones are uploaded,
        # so the view never goes blank while one piece is swapped
        cache = self.__dict__.setdefault("_object_cache", {})
        textures = self.__dict__.setdefault("_texture_keys", {})
        objects, keep = [], set()
        for item in items:
            images = [part.get("image") for part in item["parts"]]
            key = (id(item["mesh"]), item["name"], tuple(id(i) if i is not None else 0 for i in images))
            obj = cache.get(key)
            if obj is None:
                obj = SceneObject(item["name"], item["mesh"])
                for number, part in enumerate(obj.parts):
                    info = item["parts"][number] if number < len(item["parts"]) else None
                    image = info.get("image") if info else None
                    if image is None:
                        continue
                    if id(image) not in textures:
                        self.generation3d = getattr(self, "generation3d", 0) + 1
                        textures[id(image)] = (f"makeup:{self.generation3d}", image)   # held, so ids stay unique
                    texture_key = textures[id(image)][0]
                    qimage = pil_to_qimage(image) if texture_key not in self.view3d.textures else None
                    part.texture_key = self.view3d.set_texture(texture_key, qimage, info.get("texture") or "")
                    part.alpha = info.get("alpha", "OPAQUE")
                    part.cutoff = info.get("cutoff", 0.5)
                cache[key] = obj
                self.view3d.add_object(obj)
            objects.append(obj)
            keep.add(key)
        stale = [key for key in cache if key not in keep]
        if stale:
            self.view3d.remove_objects([cache.pop(key) for key in stale])
            used = {id(part.get("image")) for item in items for part in item["parts"] if part.get("image") is not None}
            for image_id in [i for i in textures if i not in used]:
                del textures[image_id]
        self.objects3d = objects
        if self._focused_face is None:
            # Framed once, when the first character appears; switching faces, outfits or sizes keeps the
            # camera where it is (the reset button and F frame the character again)
            self._focused_face = folder
            self.view3d.focus(objects)
        self.view3d.update()

    def _set_layers(self, face_items, hair_items, wardrobe):
        """The Layers box: the face's meshes, the hair's, then each worn piece under its slot's name."""
        groups = [("Face", [item["name"] for item in face_items]), ("Hair", [item["name"] for item in hair_items])]
        slots = {self.wardrobe[kind].currentData(): label for kind, label in WARDROBE
                 if kind in self.wardrobe and self.wardrobe[kind].currentData()}
        by_slot: dict[str, list[str]] = {}
        for item in wardrobe:
            by_slot.setdefault(slots.get(item.get("path"), dict(WARDROBE).get("clothes", "Outfit")), []).append(item["name"])
        groups += list(by_slot.items())
        self.layers.set_layers(groups)
        self._layout_overlays()

    def _show_preview(self):
        image = self.preview_image
        if image is None:
            return
        qimage = QtGui.QImage(image.tobytes("raw", "RGBA"), image.width, image.height, QtGui.QImage.Format.Format_RGBA8888).copy()
        self.preview.setPixmap(QtGui.QPixmap.fromImage(qimage).scaled(
            self.preview.size() - QtCore.QSize(8, 8), QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation))
        if self.eye_image is not None:
            eye = self.eye_image
            qeye = QtGui.QImage(eye.tobytes("raw", "RGBA"), eye.width, eye.height, QtGui.QImage.Format.Format_RGBA8888).copy()
            self.eye_preview.setPixmap(QtGui.QPixmap.fromImage(qeye).scaled(
                148, 148, QtCore.Qt.AspectRatioMode.KeepAspectRatio, QtCore.Qt.TransformationMode.SmoothTransformation))
            self.eye_preview.move(self.preview.width() - 158, self.preview.height() - 158)
            self.eye_preview.show()
        else:
            self.eye_preview.hide()
        if self.brow_image is not None:
            brow = self.brow_image
            brow = brow.crop((brow.width // 2, 0, brow.width, brow.height))      # the mesh uses the right half
            qbrow = QtGui.QImage(brow.tobytes("raw", "RGBA"), brow.width, brow.height, QtGui.QImage.Format.Format_RGBA8888).copy()
            self.brow_preview.setPixmap(QtGui.QPixmap.fromImage(qbrow).scaled(
                148, 148, QtCore.Qt.AspectRatioMode.KeepAspectRatio, QtCore.Qt.TransformationMode.SmoothTransformation))
            below = 158 if self.eye_image is not None else 0
            self.brow_preview.move(self.preview.width() - 158, self.preview.height() - 158 - below)
            self.brow_preview.show()
        else:
            self.brow_preview.hide()

    def resizeEvent(self, event):  # pylint: disable=invalid-name
        QtCore.QTimer.singleShot(0, self._place_rail_glow)
        QtCore.QTimer.singleShot(0, self._place_filter)
        super().resizeEvent(event)
        self._show_preview()

    def showEvent(self, event):  # pylint: disable=invalid-name
        super().showEvent(event)
        self._layout_overlays()

    # -- output ------------------------------------------------------------------------------
    def _save_texture(self):
        if self.preview_image is None:
            return
        name = self.face.currentText() + "_makeup.png"
        target, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save texture", name, "PNG image (*.png)")
        if target:
            self.preview_image.save(target)

    def _save_eye_texture(self):
        if self.eye_image is None:
            QtWidgets.QMessageBox.information(self, "Save eye texture",
                                              "Pick an eye type or an eye colour first: the plain eye texture is the "
                                              "game's own eye_01.png.")
            return
        parts = [self.choices["eyetype"].currentText(), self.choices["eyecolor"].currentText()]
        name = "_".join(re.sub(r"[^A-Za-z0-9]+", "_", p).strip("_") for p in parts if p and p != "None") or "eye"
        target, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save eye texture", f"eye_{name}.png", "PNG image (*.png)")
        if target:
            self.eye_image.save(target)

    def _add_to_scene(self):
        """The face as shown in 3D (shape, makeup, eyes, brows, dyed hair) into the Scene Viewer."""
        folder = self.face.currentData()
        if not folder or self.dressed is None or self.face_items is None or self.face_items[0] != self._mesh_key():
            QtWidgets.QMessageBox.information(self, "Add to Scene Viewer",
                                              "Wait a moment for the 3D preview to finish loading, then try again.")
            return
        hair = self.hair.currentData()
        hair_items = getattr(self, "hair_items", None)
        sides_now = self.dressed.get("sides") or {None: self.preview_image}
        wardrobe = self.wardrobe_items(sides_now)
        if (hair is not None and (hair_items is None or hair_items[0] != self.hair_path())) or wardrobe is None:
            QtWidgets.QMessageBox.information(self, "Add to Scene Viewer",
                                              "Some pieces are still loading; try again in a moment.")
            return
        from gui.widgets.scene_viewer import show_viewer  # pylint: disable=import-outside-toplevel
        face = folder.rsplit("\\", 1)[-1]
        sides = self.dressed.get("sides") or {None: self.preview_image}
        items, _eyes = dress_face_items(self.library, self.face_items[1], self._sex(), face, self.look(), sides,
                                        self.dressed.get("eye"), self.dressed.get("eyebrow"))
        if hair is not None:
            items += dress_hair_items(hair_items[1], hair, self.dressed.get("hair"))
        shape = self.shape.currentText()
        outfit = self.wardrobe["clothes"].currentText().split("  ·  ")[0] if self.wardrobe["clothes"].currentData() else ""
        group = f"{face}" + (f" ({shape})" if self.shape.currentData() else "") + (f" + {outfit}" if outfit else "")
        for item in items:
            item["path"] = folder + "\\" + f"{item['name']}.mesh"
            item["source"] = "Main Character Creator"
        for item in wardrobe:
            item["source"] = "Main Character Creator"
        items += wardrobe
        items = [item for item in items if item["name"] not in self.layers.hidden]
        viewer = show_viewer(self.main_window)
        viewer.add_baked_items(items, group)

    def _save_hair_texture(self):
        dyed = (self.dressed or {}).get("hair")
        if dyed is None:
            QtWidgets.QMessageBox.information(self, "Save hair texture",
                                              "Pick a hairstyle and at least one hair colour first.")
            return
        stem = self.hair.currentData()["folder"].rsplit("\\", 1)[-1]
        target, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save hair texture", f"{stem}_dyed.png", "PNG image (*.png)")
        if target:
            dyed.save(target)

    def _save_eyebrow_texture(self):
        if self.brow_image is None:
            QtWidgets.QMessageBox.information(self, "Save eyebrow texture",
                                              "Pick an eyebrow colour first. (Some faces have no separate eyebrow "
                                              "mesh or no eyebrow colour mask in the game.)")
            return
        colour = re.sub(r"[^A-Za-z0-9]+", "_", self.choices["eyebrow"].currentText()).strip("_")
        name = f"{self.face.currentText().replace('_body_', '_eyebrow_')}_h_{colour}.png"
        target, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save eyebrow texture", name, "PNG image (*.png)")
        if target:
            self.brow_image.save(target)

    def _export(self, fmt):
        folder = self.face.currentData()
        if not folder:
            return
        export_face(self.main_window, self.game, self.library, folder, self.faces.get(folder, []),
                    self.options["hair"], self.hair_choice(), self.hair_path(), self.wardrobe_choices(),
                    self.files.get(folder, []), self._sex(), self.look(), self.shape.currentData(),
                    self._face_texture(folder), fmt, hidden=tuple(self.layers.hidden))


def open_dialog(main_window, folder=None) -> None:
    from gui.widgets.character_anim_export import _game_folder  # pylint: disable=import-outside-toplevel
    game = _game_folder()
    if game is None:
        QtWidgets.QMessageBox.information(main_window, "Face makeup",
                                          "Open any Harry Potter: Magic Awakened .npk first, so the game folder is known.")
        return
    dialog = FaceMakeupDialog(main_window, game, folder)
    dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
    _keep_with(main_window, dialog)
    dialog.show()


def _keep_with(main_window, window) -> None:
    """Hold an ownerless window on the main window, and close it once the main window closes (as its viewer windows)."""
    windows = main_window.__dict__.setdefault("_creator_windows", [])
    windows.append(window)
    window.destroyed.connect(lambda *_args, w=window: w in windows and windows.remove(w))
    if getattr(main_window, "_creator_close_watch", None) is None:
        class _CloseWatch(QtCore.QObject):
            def eventFilter(self, obj, event):  # pylint: disable=invalid-name
                if event.type() == QtCore.QEvent.Type.Close:
                    # after the main window's own close handling, which may still cancel the close
                    QtCore.QTimer.singleShot(0, lambda: None if obj.isVisible() else
                                             [w.close() for w in list(obj.__dict__.get("_creator_windows", []))])
                return False
        main_window._creator_close_watch = _CloseWatch(main_window)
        main_window.installEventFilter(main_window._creator_close_watch)
