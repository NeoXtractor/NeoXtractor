import json
import math
import re
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from neoxtractor.core.file import IFile
from neoxtractor.gui.widgets.viewer import Viewer

from . import cocos_runtime as runtime
from .cocos_file_resolver import ResourceResolver
from .cocos_parser import CocosParser


def _dict_float(value: Any, key: str, default: float) -> float:
    if isinstance(value, dict):
        try:
            return float(value.get(key, default) or default)
        except (TypeError, ValueError):
            return default
    return default


Affine2D = tuple[float, float, float, float, float, float]


def _affine_identity() -> Affine2D:
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _affine_translate(tx: float, ty: float) -> Affine2D:
    return (1.0, 0.0, 0.0, 1.0, tx, ty)


def _affine_scale(sx: float, sy: float) -> Affine2D:
    return (sx, 0.0, 0.0, sy, 0.0, 0.0)


def _affine_multiply(left: Affine2D, right: Affine2D) -> Affine2D:
    a1, b1, c1, d1, tx1, ty1 = left
    a2, b2, c2, d2, tx2, ty2 = right
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * tx2 + c1 * ty2 + tx1,
        b1 * tx2 + d1 * ty2 + ty1,
    )


def _affine_to_qtransform(transform: Affine2D) -> QtGui.QTransform:
    a, b, c, d, tx, ty = transform
    return QtGui.QTransform(a, b, c, d, tx, ty)


def _affine_map_point(transform: Affine2D, x: float, y: float) -> QtCore.QPointF:
    a, b, c, d, tx, ty = transform
    return QtCore.QPointF(a * x + c * y + tx, b * x + d * y + ty)


def _affine_map_rect(transform: Affine2D, width: float, height: float) -> QtCore.QRectF:
    corners = [
        _affine_map_point(transform, 0.0, 0.0),
        _affine_map_point(transform, width, 0.0),
        _affine_map_point(transform, 0.0, height),
        _affine_map_point(transform, width, height),
    ]
    min_x = min(point.x() for point in corners)
    max_x = max(point.x() for point in corners)
    min_y = min(point.y() for point in corners)
    max_y = max(point.y() for point in corners)
    return QtCore.QRectF(min_x, min_y, max_x - min_x, max_y - min_y)


def _cocos_to_scene_transform(root_height: float) -> Affine2D:
    return (1.0, 0.0, 0.0, -1.0, 0.0, root_height)


def _qt_local_to_cocos_transform(local_height: float) -> Affine2D:
    return (1.0, 0.0, 0.0, -1.0, 0.0, local_height)


def tint_qimage(image: QtGui.QImage, color: dict[str, Any] | None) -> QtGui.QImage:
    if not isinstance(color, dict):
        return image
    red = int(color.get("r", 255))
    green = int(color.get("g", 255))
    blue = int(color.get("b", 255))
    alpha = int(color.get("a", 255))
    if (red, green, blue, alpha) == (255, 255, 255, 255):
        return image

    tinted = image.convertToFormat(QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    painter = QtGui.QPainter()
    if not painter.begin(tinted):
        return image
    try:
        painter.setCompositionMode(
            QtGui.QPainter.CompositionMode.CompositionMode_Multiply
        )
        painter.fillRect(tinted.rect(), QtGui.QColor(red, green, blue, 255))
    finally:
        painter.end()
    return tinted


def _node_ignore_anchor(node: dict[str, Any]) -> bool:
    value = node.get("ignoreAnchorPointForPosition")
    if value is None:
        classname = str(node.get("classname") or "")
        return classname in {"Scene", "Layer", "LayerColor", "LayerGradient"}
    return bool(value)


def _node_anchor_pixels(
    width: float, height: float, anchor: dict[str, Any]
) -> tuple[float, float]:
    return (
        width * _dict_float(anchor, "x", 0.0),
        height * _dict_float(anchor, "y", 0.0),
    )


def _node_rotation_basis(node: dict[str, Any]) -> Affine2D:
    rotation = node.get("rotationSkew") or {}
    rotation_x = math.radians(_dict_float(rotation, "x", 0.0))
    rotation_y = math.radians(_dict_float(rotation, "y", 0.0))
    return (
        math.cos(rotation_y),
        -math.sin(rotation_y),
        math.sin(rotation_x),
        math.cos(rotation_x),
        0.0,
        0.0,
    )


def _node_local_cocos_transform(
    node: dict[str, Any], width: float, height: float
) -> Affine2D:
    position = node.get("position") or {}
    scale = node.get("scale") or {}
    anchor = node.get("anchorPoint") or {}
    pos_x = _dict_float(position, "x", 0.0)
    pos_y = _dict_float(position, "y", 0.0)
    scale_x = _dict_float(scale, "x", 1.0)
    scale_y = _dict_float(scale, "y", 1.0)
    anchor_px_x, anchor_px_y = _node_anchor_pixels(width, height, anchor)

    if _node_ignore_anchor(node):
        pos_x += anchor_px_x
        pos_y += anchor_px_y

    transform = _affine_translate(pos_x, pos_y)
    transform = _affine_multiply(transform, _node_rotation_basis(node))
    transform = _affine_multiply(transform, _affine_scale(scale_x, scale_y))
    return _affine_multiply(transform, _affine_translate(-anchor_px_x, -anchor_px_y))


class NodeHandleItem(QtWidgets.QGraphicsObject):
    node_selected = QtCore.Signal(object)
    node_dragged = QtCore.Signal(object, float, float)

    def __init__(self, node: dict[str, Any], rect: QtCore.QRectF):
        super().__init__()
        self.node = node
        self.rect = QtCore.QRectF(rect)
        self.hovered = False
        self._last_scene_pos: QtCore.QPointF | None = None
        self.setAcceptedMouseButtons(QtCore.Qt.MouseButton.LeftButton)
        self.setAcceptHoverEvents(True)
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        self.setZValue(9_000)

    def boundingRect(self) -> QtCore.QRectF:
        width = max(self.rect.width(), 12.0)
        height = max(self.rect.height(), 12.0)
        return QtCore.QRectF(self.rect.x(), self.rect.y(), width, height).adjusted(
            -6.0, -6.0, 6.0, 6.0
        )

    def paint(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionGraphicsItem,
        widget: QtWidgets.QWidget | None = None,
    ) -> None:
        del option, widget
        if self.hovered:
            painter.setPen(
                QtGui.QPen(QtGui.QColor("#38bdf8"), 1, QtCore.Qt.PenStyle.DashLine)
            )
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect)

    def mousePressEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        self._last_scene_pos = event.scenePos()
        self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
        self.node_selected.emit(self.node)
        event.accept()

    def mouseMoveEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if self._last_scene_pos is None:
            self._last_scene_pos = event.scenePos()
        delta = event.scenePos() - self._last_scene_pos
        if abs(delta.x()) > 0.01 or abs(delta.y()) > 0.01:
            self.node_dragged.emit(self.node, float(delta.x()), float(delta.y()))
            self._last_scene_pos = event.scenePos()
        event.accept()

    def mouseReleaseEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        self._last_scene_pos = None
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        event.accept()

    def hoverEnterEvent(self, event: QtWidgets.QGraphicsSceneHoverEvent) -> None:
        self.hovered = True
        self.update()
        event.accept()

    def hoverLeaveEvent(self, event: QtWidgets.QGraphicsSceneHoverEvent) -> None:
        self.hovered = False
        self.update()
        event.accept()


class NodeInspector(QtWidgets.QWidget):
    node_changed = QtCore.Signal(object)

    def __init__(self):
        super().__init__()
        self.current_node: dict[str, Any] | None = None
        self._updating = False

        self.type_label = QtWidgets.QLabel("-")
        self.name_edit = QtWidgets.QLineEdit()
        self.visible_check = QtWidgets.QCheckBox("Visible")
        self.alpha_spin = QtWidgets.QSpinBox()
        self.alpha_spin.setRange(0, 255)
        self.resource_edit = QtWidgets.QLineEdit()
        self.resource_edit.setReadOnly(True)
        self.json_text = QtWidgets.QPlainTextEdit()
        self.json_text.setReadOnly(True)

        self.pos_x = self._make_double_spin()
        self.pos_y = self._make_double_spin()
        self.size_w = self._make_double_spin(minimum=0.0)
        self.size_h = self._make_double_spin(minimum=0.0)
        self.scale_x = self._make_double_spin(default=1.0)
        self.scale_y = self._make_double_spin(default=1.0)
        self.rot_x = self._make_double_spin()
        self.rot_y = self._make_double_spin()
        self.anchor_x = self._make_double_spin()
        self.anchor_y = self._make_double_spin()

        form = QtWidgets.QFormLayout()
        form.addRow("Type", self.type_label)
        form.addRow("Name", self.name_edit)
        form.addRow("Position X", self.pos_x)
        form.addRow("Position Y", self.pos_y)
        form.addRow("Width", self.size_w)
        form.addRow("Height", self.size_h)
        form.addRow("Scale X", self.scale_x)
        form.addRow("Scale Y", self.scale_y)
        form.addRow("Rotation X", self.rot_x)
        form.addRow("Rotation Y", self.rot_y)
        form.addRow("Anchor X", self.anchor_x)
        form.addRow("Anchor Y", self.anchor_y)
        form.addRow("Alpha", self.alpha_spin)
        form.addRow("", self.visible_check)
        form.addRow("Resource", self.resource_edit)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QtWidgets.QLabel("Node JSON"))
        layout.addWidget(self.json_text, 1)

        for widget in (
            self.name_edit,
            self.pos_x,
            self.pos_y,
            self.size_w,
            self.size_h,
            self.scale_x,
            self.scale_y,
            self.rot_x,
            self.rot_y,
            self.anchor_x,
            self.anchor_y,
            self.alpha_spin,
            self.visible_check,
        ):
            if isinstance(widget, QtWidgets.QLineEdit):
                widget.editingFinished.connect(self._apply_changes)
            elif isinstance(widget, QtWidgets.QAbstractButton):
                widget.toggled.connect(self._apply_changes)
            else:
                widget.valueChanged.connect(self._apply_changes)

        self.setEnabled(False)

    @staticmethod
    def _make_double_spin(
        *, minimum: float = -999999.0, maximum: float = 999999.0, default: float = 0.0
    ) -> QtWidgets.QDoubleSpinBox:
        spin = QtWidgets.QDoubleSpinBox()
        spin.setDecimals(4)
        spin.setRange(minimum, maximum)
        spin.setValue(default)
        return spin

    def set_node(self, node: dict[str, Any] | None) -> None:
        self.current_node = node
        self._updating = True
        self.setEnabled(node is not None)
        if node is None:
            self.type_label.setText("-")
            self.name_edit.clear()
            self.resource_edit.clear()
            self.json_text.clear()
            self.visible_check.setChecked(False)
            self.alpha_spin.setValue(255)
            for spin in (
                self.pos_x,
                self.pos_y,
                self.size_w,
                self.size_h,
                self.scale_x,
                self.scale_y,
                self.rot_x,
                self.rot_y,
                self.anchor_x,
                self.anchor_y,
            ):
                spin.setValue(0.0)
            self._updating = False
            return

        position = node.get("position") or {}
        size = node.get("size") or {}
        scale = node.get("scale") or {}
        rotation = node.get("rotationSkew") or {}
        anchor = node.get("anchorPoint") or {}
        details = node.get("details") or {}

        self.type_label.setText(str(node.get("classname") or "Unknown"))
        self.name_edit.setText(str(node.get("name") or ""))
        self.pos_x.setValue(_dict_float(position, "x", 0.0))
        self.pos_y.setValue(_dict_float(position, "y", 0.0))
        self.size_w.setValue(_dict_float(size, "width", 0.0))
        self.size_h.setValue(_dict_float(size, "height", 0.0))
        self.scale_x.setValue(_dict_float(scale, "x", 1.0))
        self.scale_y.setValue(_dict_float(scale, "y", 1.0))
        self.rot_x.setValue(_dict_float(rotation, "x", 0.0))
        self.rot_y.setValue(_dict_float(rotation, "y", 0.0))
        self.anchor_x.setValue(_dict_float(anchor, "x", 0.0))
        self.anchor_y.setValue(_dict_float(anchor, "y", 0.0))
        self.visible_check.setChecked(bool(node.get("visible", True)))
        self.alpha_spin.setValue(int(node.get("alpha", 255) or 255))
        resource = PreviewScene._resource_from_details(details)
        if isinstance(resource, dict):
            self.resource_edit.setText(
                resource.get("path") or resource.get("plist") or ""
            )
        elif isinstance(resource, str):
            self.resource_edit.setText(resource)
        else:
            self.resource_edit.clear()
        self.json_text.setPlainText(json.dumps(node, ensure_ascii=False, indent=2))
        self._updating = False

    def _apply_changes(self, *args: Any) -> None:
        del args
        if self._updating or self.current_node is None:
            return
        node = self.current_node
        node["name"] = self.name_edit.text()
        node["position"] = {
            "x": round(self.pos_x.value(), 4),
            "y": round(self.pos_y.value(), 4),
        }
        node["size"] = {
            "width": round(self.size_w.value(), 4),
            "height": round(self.size_h.value(), 4),
        }
        node["scale"] = {
            "x": round(self.scale_x.value(), 4),
            "y": round(self.scale_y.value(), 4),
        }
        node["rotationSkew"] = {
            "x": round(self.rot_x.value(), 4),
            "y": round(self.rot_y.value(), 4),
        }
        node["anchorPoint"] = {
            "x": round(self.anchor_x.value(), 4),
            "y": round(self.anchor_y.value(), 4),
        }
        node["visible"] = self.visible_check.isChecked()
        node["alpha"] = int(self.alpha_spin.value())
        self.json_text.setPlainText(json.dumps(node, ensure_ascii=False, indent=2))
        self.node_changed.emit(node)


class PreviewScene(QtWidgets.QGraphicsScene):
    node_selected = QtCore.Signal(object)
    node_dragged = QtCore.Signal(object, float, float)

    def __init__(self):
        super().__init__()
        self.node_items: dict[str, list[QtWidgets.QGraphicsItem]] = {}
        self.node_handles: dict[str, NodeHandleItem] = {}
        self.highlight_items: list[QtWidgets.QGraphicsRectItem] = []
        self.game_rect = QtCore.QRectF(0, 0, 1280, 720)
        self.root_cocos_height = 720.0
        self.show_panel_fills = False
        self.show_hidden_nodes = False
        self.playback_frame_cursor = 0.0

    def load_document(
        self,
        document: dict[str, Any],
        resolver: ResourceResolver,
        view_mode: str = "game",
        playback_frame_cursor: float = 0.0,
    ) -> None:
        self.clear()
        self.node_items.clear()
        self.node_handles.clear()
        self.highlight_items.clear()
        self.setBackgroundBrush(QtGui.QColor("#0f1720"))  # 0f1720
        self.playback_frame_cursor = playback_frame_cursor

        if document.get("format") == "spine-json":
            self._render_spine_document(document)
            return

        root = document.get("root", {})
        canvas_size = self._document_canvas_size(root)
        width = max(1.0, float(canvas_size.get("width", 1280)))
        height = max(1.0, float(canvas_size.get("height", 720)))
        self.game_rect = QtCore.QRectF(0, 0, width, height)
        self.root_cocos_height = height
        self.setSceneRect(self.game_rect)
        self._render_node(root, resolver, _affine_identity(), set(), None)
        bounds = self.itemsBoundingRect()
        if bounds.isValid() and bounds.width() > 0 and bounds.height() > 0:
            margin = 24.0
            if view_mode == "content":
                if width <= 1.0 or height <= 1.0:
                    self.setSceneRect(bounds.adjusted(-margin, -margin, margin, margin))
                else:
                    self.setSceneRect(
                        self.game_rect.united(
                            bounds.adjusted(-margin, -margin, margin, margin)
                        )
                    )
            elif width <= 1.0 or height <= 1.0:
                self.setSceneRect(bounds.adjusted(-margin, -margin, margin, margin))
            else:
                self.setSceneRect(self.game_rect)
        elif view_mode == "game":
            self.setSceneRect(self.game_rect)

        if (
            view_mode == "content"
            and self.game_rect.width() > 1
            and self.game_rect.height() > 1
        ):
            self.addRect(
                self.game_rect,
                QtGui.QPen(
                    QtGui.QColor("#22c55e"), 2, QtCore.Qt.PenStyle.DashLine
                ),  # "#22c55e"
            )

    @staticmethod
    def _document_canvas_size(root: dict[str, Any]) -> dict[str, float]:
        size = root.get("size") or {}
        width = float(size.get("width", 0) or 0)
        height = float(size.get("height", 0) or 0)
        if width > 1 and height > 1:
            return {"width": width, "height": height}
        for child in root.get("children", []):
            child_size = child.get("size") or {}
            child_width = float(child_size.get("width", 0) or 0)
            child_height = float(child_size.get("height", 0) or 0)
            if child_width > 1 and child_height > 1:
                return {"width": child_width, "height": child_height}
        return {"width": 1280.0, "height": 720.0}

    def focus_view_rect(self, view_mode: str) -> QtCore.QRectF:
        if (
            view_mode == "game"
            and self.game_rect.width() > 1
            and self.game_rect.height() > 1
        ):
            return self.game_rect
        return self.sceneRect()

    @staticmethod
    def _subtree_bounds(node: dict[str, Any]) -> QtCore.QRectF | None:
        rects: list[QtCore.QRectF] = []

        def visit(current: dict[str, Any], parent_transform: Affine2D) -> None:
            size = current.get("size") or {"width": 0.0, "height": 0.0}
            width = max(0.0, float(size.get("width", 0.0) or 0.0))
            height = max(0.0, float(size.get("height", 0.0) or 0.0))
            transform = _affine_multiply(
                parent_transform, _node_local_cocos_transform(current, width, height)
            )

            if width > 0.0 and height > 0.0:
                rects.append(_affine_map_rect(transform, width, height))

            for child in current.get("children", []):
                visit(child, transform)

        visit(node, _affine_identity())
        if not rects:
            return None
        bounds = rects[0]
        for rect in rects[1:]:
            bounds = bounds.united(rect)
        return bounds

    @staticmethod
    def _collect_descendant_rects(node: dict[str, Any]) -> list[QtCore.QRectF]:
        rects: list[QtCore.QRectF] = []

        def visit(current: dict[str, Any], parent_transform: Affine2D) -> None:
            size = current.get("size") or {"width": 0.0, "height": 0.0}
            width = max(0.0, float(size.get("width", 0.0) or 0.0))
            height = max(0.0, float(size.get("height", 0.0) or 0.0))
            transform = _affine_multiply(
                parent_transform, _node_local_cocos_transform(current, width, height)
            )

            if width > 0.0 and height > 0.0:
                rects.append(_affine_map_rect(transform, width, height))

            for child in current.get("children", []):
                visit(child, transform)

        for child in node.get("children", []):
            visit(child, _affine_identity())
        return rects

    def _project_render_context(
        self, root: dict[str, Any]
    ) -> tuple[dict[str, float], QtCore.QPointF]:
        root_size = root.get("size") or {}
        width = float(root_size.get("width", 0.0) or 0.0)
        height = float(root_size.get("height", 0.0) or 0.0)
        if width > 1.0 and height > 1.0:
            return {"width": width, "height": height}, QtCore.QPointF(0.0, 0.0)

        rects = self._collect_descendant_rects(root)
        if not rects:
            return {"width": 0.0, "height": 0.0}, QtCore.QPointF(0.0, 0.0)

        largest = max(rects, key=lambda rect: rect.width() * rect.height())
        center = largest.center()
        tolerance = max(largest.width(), largest.height(), 64.0) * 0.4
        if abs(center.x()) <= tolerance and abs(center.y()) <= tolerance:
            return {"width": 0.0, "height": 0.0}, QtCore.QPointF(0.0, 0.0)

        bounds = rects[0]
        for rect in rects[1:]:
            bounds = bounds.united(rect)
        return {"width": 0.0, "height": 0.0}, bounds.center()

    def _render_spine_document(self, document: dict[str, Any]) -> None:
        self.setSceneRect(0, 0, 960, 540)
        panel = QtCore.QRectF(48, 48, 864, 444)
        self.addRect(
            panel,
            QtGui.QPen(QtGui.QColor("#334155"), 1),
            QtGui.QBrush(QtGui.QColor("#111827")),
        )

        root = document.get("root", {})
        details = root.get("details", {})
        animation = document.get("animation", {})
        title = self.addText(root.get("name") or "Spine Animation")
        title_font = title.font()
        title_font.setPixelSize(28)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setDefaultTextColor(QtGui.QColor("#f8fafc"))
        title.setPos(76, 76)

        summary_lines = [
            f"Format: {document.get('format')}",
            f"Spine: {details.get('spineVersion', '-')}",
            f"Bones: {details.get('boneCount', 0)}",
            f"Slots: {details.get('slotCount', 0)}",
            f"Skins: {details.get('skinCount', 0)}",
            f"Animations: {animation.get('animationCount', 0)}",
        ]
        summary = self.addText("\n".join(summary_lines))
        summary_font = summary.font()
        summary_font.setPixelSize(18)
        summary.setFont(summary_font)
        summary.setDefaultTextColor(QtGui.QColor("#cbd5e1"))
        summary.setPos(76, 136)

        anim_names = animation.get("animationNames") or []
        if anim_names:
            label = self.addText("Animations")
            label_font = label.font()
            label_font.setPixelSize(18)
            label_font.setBold(True)
            label.setFont(label_font)
            label.setDefaultTextColor(QtGui.QColor("#fbbf24"))
            label.setPos(460, 136)

            for index, name in enumerate(anim_names[:12]):
                item = self.addText(f"{index + 1}. {name}")
                item_font = item.font()
                item_font.setPixelSize(16)
                item.setFont(item_font)
                item.setDefaultTextColor(QtGui.QColor("#e5e7eb"))
                item.setPos(460, 172 + index * 26)

    def highlight_node(self, node: dict[str, Any] | str | None) -> None:
        for item in self.highlight_items:
            self.removeItem(item)
        self.highlight_items.clear()

        if node is None:
            return

        node_path = node if isinstance(node, str) else node.get("__editor_path")
        if not node_path:
            return

        for item in self.node_items.get(str(node_path), []):
            rect = item.sceneBoundingRect()
            border = self.addRect(rect, QtGui.QPen(QtGui.QColor("#f59e0b"), 2))
            border.setZValue(10_000)
            self.highlight_items.append(border)

    def _render_node(
        self,
        node: dict[str, Any],
        resolver: ResourceResolver,
        parent_transform: Affine2D,
        project_stack: set[int],
        parent_node: dict[str, Any] | None = None,
        inherited_opacity: float = 1.0,
    ) -> None:
        is_hidden = not node.get("visible", True)
        if is_hidden and not self.show_hidden_nodes:
            return
        if self._is_template_node(node, parent_node) and not self.show_hidden_nodes:
            return

        size = node.get("size") or {"width": 0.0, "height": 0.0}
        width = max(0.0, float(size.get("width", 0.0)))
        height = max(0.0, float(size.get("height", 0.0)))

        items: list[QtWidgets.QGraphicsItem] = []
        node_path = str(node.get("__editor_path") or "")
        details = node.get("details") or {}
        resource = self._resource_from_details(details)
        image = resolver.load_resource(resource)

        intrinsic_width = float(image.width()) if image is not None else 0.0
        intrinsic_height = float(image.height()) if image is not None else 0.0
        base_width = width if width > 0 else intrinsic_width
        base_height = height if height > 0 else intrinsic_height
        rect_width = max(
            base_width, 1.0 if image is not None or base_width > 0 else 0.0
        )
        rect_height = max(
            base_height, 1.0 if image is not None or base_height > 0 else 0.0
        )
        local_rect = QtCore.QRectF(0.0, 0.0, rect_width, rect_height)
        cocos_transform = _affine_multiply(
            parent_transform, _node_local_cocos_transform(node, base_width, base_height)
        )
        node_transform = _affine_to_qtransform(
            _affine_multiply(
                _cocos_to_scene_transform(self.root_cocos_height),
                _affine_multiply(
                    cocos_transform, _qt_local_to_cocos_transform(base_height)
                ),
            )
        )
        color = node.get("color") or {}
        local_opacity = inherited_opacity * (
            float(node.get("alpha", 255) or 255) / 255.0
        )
        if isinstance(color, dict):
            local_opacity *= float(color.get("a", 255) or 255) / 255.0
        if is_hidden:
            local_opacity *= 0.35
        if local_opacity <= 0.0001 and not self.show_hidden_nodes:
            return

        fill_color = self._panel_fill_color(details) if self.show_panel_fills else None
        if (
            image is None
            and fill_color is not None
            and local_rect.width() > 1.0
            and local_rect.height() > 1.0
        ):
            fill = self.addRect(
                local_rect,
                QtGui.QPen(QtCore.Qt.PenStyle.NoPen),
                QtGui.QBrush(fill_color),
            )
            fill.setTransform(node_transform)
            fill.setOpacity(local_opacity)
            items.append(fill)

        hitbox = NodeHandleItem(node, local_rect)
        hitbox.setTransform(node_transform)
        hitbox.node_selected.connect(self.node_selected.emit)
        hitbox.node_dragged.connect(self.node_dragged.emit)
        self.addItem(hitbox)
        items.append(hitbox)
        if node_path:
            self.node_handles[node_path] = hitbox

        if image is not None:
            tinted_image = tint_qimage(image, node.get("color"))
            pixmap = QtGui.QPixmap.fromImage(tinted_image)
            pixmap = pixmap.scaled(
                max(1, int(round(local_rect.width()))),
                max(1, int(round(local_rect.height()))),
                QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
            item = self.addPixmap(pixmap)
            item.setTransform(node_transform)
            item.setOpacity(local_opacity)
            items.append(item)

        project_file = details.get("projectFile")
        if isinstance(project_file, str) and project_file:
            project_source = resolver.resolve(project_file)
            if (
                project_source is not None
                and project_source.file_signature not in project_stack
            ):
                nested_document = resolver.load_project(project_file)
                if nested_document is not None:
                    nested_animation = nested_document.get("animation")
                    if isinstance(nested_animation, dict) and nested_animation.get(
                        "timelines"
                    ):
                        nested_clip_name = details.get(
                            "currentAnimation"
                        ) or nested_animation.get("currentAnimationName")
                        nested_cursor = self.playback_frame_cursor * float(
                            details.get("innerActionSpeed", 1.0) or 1.0
                        )
                        _, nested_overrides, _ = runtime.build_animation_state(
                            nested_animation,
                            nested_clip_name
                            if isinstance(nested_clip_name, str)
                            else None,
                            nested_cursor,
                            nested_document.get("root", {}),
                        )
                        nested_document = runtime.apply_animation_state(
                            nested_document, nested_overrides
                        )
                    nested_root = nested_document.get("root", {})
                    nested_stack = set(project_stack)
                    nested_stack.add(project_source.file_signature)
                    _, nested_origin = self._project_render_context(nested_root)
                    nested_transform = _affine_multiply(
                        cocos_transform,
                        _affine_translate(-nested_origin.x(), -nested_origin.y()),
                    )
                    for child in nested_root.get("children", []):
                        self._render_node(
                            child,
                            resolver,
                            nested_transform,
                            nested_stack,
                            nested_root,
                            local_opacity,
                        )

        text = details.get("text")
        if isinstance(text, str) and text:
            text_item = self.addText(text)
            font_size = int(details.get("fontSize") or 16)
            font = text_item.font()
            # Cocos font sizes behave much closer to pixel sizes than Qt point sizes.
            font.setPixelSize(max(font_size, 6))
            text_item.setFont(font)
            color = (
                details.get("textColor")
                or node.get("color")
                or {"r": 255, "g": 255, "b": 255, "a": 255}
            )
            text_item.setDefaultTextColor(
                QtGui.QColor(
                    int(color.get("r", 255)),
                    int(color.get("g", 255)),
                    int(color.get("b", 255)),
                    int(color.get("a", 255)),
                )
            )
            self._fit_text_item(text_item, local_rect)
            text_item.setTransform(node_transform)
            text_item.setOpacity(local_opacity)
            items.append(text_item)

        if node_path:
            self.node_items[node_path] = items

        for child in node.get("children", []):
            self._render_node(
                child, resolver, cocos_transform, project_stack, node, local_opacity
            )

    @staticmethod
    def _is_template_node(
        node: dict[str, Any], parent_node: dict[str, Any] | None
    ) -> bool:
        if parent_node is None:
            return False
        name = str(node.get("name") or "")
        if not name:
            return False
        containers = [
            child
            for child in parent_node.get("children", [])
            if str(child.get("class") or child.get("classname") or "")
            in {"ListView", "ScrollView", "PageView"}
        ]
        if not containers:
            return False
        if not re.fullmatch(
            r"(ListViewItem|ListItem\w*|Item_List|ItemList|TemplateItem|TplItem)",
            name,
            re.IGNORECASE,
        ):
            return False
        node_bounds = PreviewScene._local_bounds(node)
        if node_bounds is None:
            return False
        for container in containers:
            container_bounds = PreviewScene._local_bounds(container)
            if container_bounds is not None and node_bounds.intersects(
                container_bounds
            ):
                return False
        return True

    @staticmethod
    def _local_bounds(node: dict[str, Any]) -> QtCore.QRectF | None:
        size = node.get("size") or {}
        position = node.get("position") or {}
        anchor = node.get("anchorPoint") or {}
        width = float(size.get("width", 0.0) or 0.0)
        height = float(size.get("height", 0.0) or 0.0)
        if width <= 0.0 or height <= 0.0:
            return None
        anchor_x = float(anchor.get("x", 0.0) or 0.0)
        anchor_y = float(anchor.get("y", 0.0) or 0.0)
        pos_x = float(position.get("x", 0.0) or 0.0)
        pos_y = float(position.get("y", 0.0) or 0.0)
        left = pos_x - width * anchor_x
        bottom = pos_y - height * anchor_y
        return QtCore.QRectF(left, bottom, width, height)

    @staticmethod
    def _resource_from_details(details: dict[str, Any]) -> dict[str, Any] | str | None:
        return runtime.resolve_primary_resource(details)

    @staticmethod
    def _panel_fill_color(details: dict[str, Any]) -> QtGui.QColor | None:
        color = details.get("backgroundColor")
        if not isinstance(color, dict):
            return None
        color_type = int(details.get("colorType", 0) or 0)
        if color_type == 0:
            return None
        alpha = int(details.get("backgroundColorOpacity", color.get("a", 0)))
        if alpha <= 0:
            return None
        return QtGui.QColor(
            int(color.get("r", 0)),
            int(color.get("g", 0)),
            int(color.get("b", 0)),
            alpha,
        )

    @staticmethod
    def _fit_text_item(
        text_item: QtWidgets.QGraphicsTextItem, rect: QtCore.QRectF
    ) -> None:
        max_width = max(rect.width(), 1.0)
        max_height = max(rect.height(), 1.0)
        text_item.setTextWidth(max_width)
        font = text_item.font()
        pixel_size = max(font.pixelSize(), 6)

        while pixel_size > 6:
            text_item.setFont(font)
            bounds = text_item.boundingRect()
            if bounds.width() <= max_width + 1 and bounds.height() <= max_height + 1:
                break
            pixel_size -= 1
            font.setPixelSize(pixel_size)

        text_item.setFont(font)


class CocosViewer(Viewer):
    name = "Cocos UI Viewer"
    accepted_extensions = {"cjson", "csb"}

    def __init__(self):
        super().__init__()
        self.current_document: dict[str, Any] | None = None
        self.current_resolver: ResourceResolver | None = None
        self.node_lookup: dict[QtWidgets.QTreeWidgetItem, dict[str, Any]] = {}
        self.path_lookup: dict[str, QtWidgets.QTreeWidgetItem] = {}
        self.animation_document: dict[str, Any] | None = None
        self.active_clip_name: str | None = None
        self.selected_node_path: str | None = None
        self._file: IFile | None = None
        self.playback_frame_cursor = 0.0
        self.playback_fps = 60.0
        self.playback_timer = QtCore.QTimer(self)
        self.playback_timer.setInterval(33)
        self.playback_timer.timeout.connect(self.on_playback_tick)

        self.play_button = QtWidgets.QPushButton("Pause")
        self.play_button.setEnabled(False)
        self.animation_combo = QtWidgets.QComboBox()
        self.animation_combo.setMinimumWidth(180)
        self.animation_combo.setEnabled(False)
        self.view_mode_combo = QtWidgets.QComboBox()
        self.view_mode_combo.addItem("Game View", "game")
        self.view_mode_combo.addItem("Content View", "content")
        self.view_mode_combo.setCurrentIndex(0)
        self.panel_fill_check = QtWidgets.QCheckBox("Panel Fill")
        self.panel_fill_check.setChecked(False)
        self.show_hidden_check = QtWidgets.QCheckBox("Show Hidden")
        self.show_hidden_check.setChecked(False)

        top_bar = QtWidgets.QHBoxLayout()
        top_bar.addWidget(self.view_mode_combo)
        top_bar.addWidget(self.panel_fill_check)
        top_bar.addWidget(self.show_hidden_check)
        top_bar.addWidget(self.animation_combo)
        top_bar.addWidget(self.play_button)

        self.node_tree = QtWidgets.QTreeWidget()
        self.node_tree.setHeaderLabels(["Node", "Type", "Pos", "Size"])
        self.inspector = NodeInspector()
        self.detail_text = QtWidgets.QPlainTextEdit()
        self.detail_text.setReadOnly(True)
        self.bottom_tabs = QtWidgets.QTabWidget()
        self.bottom_tabs.addTab(self.inspector, "Inspector")
        self.bottom_tabs.addTab(self.detail_text, "JSON")

        self.scene = PreviewScene()
        self.preview = QtWidgets.QGraphicsView(self.scene)
        self.preview.setRenderHints(
            QtGui.QPainter.RenderHint.Antialiasing
            | QtGui.QPainter.RenderHint.SmoothPixmapTransform
            | QtGui.QPainter.RenderHint.TextAntialiasing
        )

        bottom_split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        bottom_split.addWidget(self.node_tree)
        bottom_split.addWidget(self.bottom_tabs)

        right_split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        right_split.addWidget(self.preview)
        right_split.addWidget(bottom_split)
        right_split.setSizes([700, 260])

        main_split = QtWidgets.QSplitter()
        main_split.addWidget(right_split)
        main_split.setSizes([560, 1040])

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(top_bar)
        layout.addWidget(main_split, 1)

        self.playback_timer.stop()

        self.play_button.clicked.connect(self.toggle_playback)
        self.animation_combo.currentIndexChanged.connect(self.on_animation_changed)
        self.view_mode_combo.currentIndexChanged.connect(self.on_view_mode_changed)
        self.panel_fill_check.toggled.connect(self.on_panel_fill_toggled)
        self.show_hidden_check.toggled.connect(self.on_show_hidden_toggled)
        self.node_tree.currentItemChanged.connect(self.on_node_selected)
        self.scene.node_selected.connect(self.on_scene_node_selected)
        self.scene.node_dragged.connect(self.on_scene_node_dragged)
        self.inspector.node_changed.connect(self.on_inspector_changed)

    def set_file(self, file: IFile) -> None:
        try:
            document = CocosParser().parse_file(file)
            if document is None:
                return None
            runtime.assign_editor_paths(document.get("root", {}))
            runtime.capture_editor_originals(document.get("root", {}))
            resolver = ResourceResolver()
        except Exception as exc:
            self.detail_text.setPlainText(f"Failed to parse {file.name}:\n{exc}")
            return

        self.current_document = document
        self.current_resolver = resolver
        self._file = file
        self.animation_document = document
        self.node_tree.clear()
        self.node_lookup.clear()
        self.path_lookup.clear()
        self.selected_node_path = document.get("root", {}).get("__editor_path")
        root = document.get("root", {})
        self._add_tree_item(None, root)
        self.node_tree.expandToDepth(3)
        root_item = self.path_lookup.get(self.selected_node_path or "")
        if root_item is not None:
            self.node_tree.setCurrentItem(root_item)
            self.inspector.set_node(root)
        self._setup_animation_controls(document)
        self.render_current_document()
        summary = self._document_summary(document)
        self.detail_text.setPlainText(json.dumps(summary, ensure_ascii=False, indent=2))

    def get_file(self) -> IFile | None:
        return self._file

    def unload_file(self):
        self._file = None
        self.clear_all()

    def clear_all(self):
        """Clear the list widget and reset the container."""
        if hasattr(self, "container") and self.container:
            self.container = None
        if self.current_resolver is not None:
            self.current_resolver.unload_caches()

    def _setup_animation_controls(self, document: dict[str, Any]) -> None:
        self.playback_timer.stop()
        self.playback_frame_cursor = 0.0
        self.active_clip_name = None

        animation = document.get("animation")
        clips = animation.get("clips") if isinstance(animation, dict) else None
        has_timelines = bool(isinstance(animation, dict) and animation.get("timelines"))
        clip_names = [
            clip.get("name")
            for clip in clips or []
            if isinstance(clip, dict) and clip.get("name")
        ]
        clip_lookup = {
            clip.get("name"): clip
            for clip in clips or []
            if isinstance(clip, dict) and clip.get("name")
        }

        self.animation_combo.blockSignals(True)
        self.animation_combo.clear()
        if clip_names:
            self.animation_combo.addItems(clip_names)
        self.animation_combo.blockSignals(False)
        self.animation_combo.setEnabled(bool(clip_names))

        active_name: str | None = None
        if isinstance(animation, dict):
            active_name = animation.get("activeAnimationName") or animation.get(
                "currentAnimationName"
            )
        if active_name and active_name in clip_names:
            clip = clip_lookup.get(active_name, {})
            start_index = float(clip.get("startIndex", 0) or 0)
            end_index = float(clip.get("endIndex", 0) or 0)
            if end_index <= start_index:
                active_name = None
        if active_name and active_name in clip_names:
            self.animation_combo.setCurrentText(active_name)
            self.active_clip_name = active_name
        elif clip_names:
            default_name = clip_names[0]
            for candidate in clip_names:
                clip = clip_lookup.get(candidate, {})
                if float(clip.get("endIndex", 0) or 0) > float(
                    clip.get("startIndex", 0) or 0
                ):
                    default_name = candidate
                    break
            self.animation_combo.setCurrentText(default_name)
            self.active_clip_name = default_name

        can_play = bool(has_timelines and document.get("format") != "spine-json")
        self.play_button.setEnabled(can_play)
        self.play_button.setText("Pause" if can_play else "Play")
        if can_play:
            self.playback_timer.start()

    def render_current_document(self) -> None:
        if self.current_document is None or self.current_resolver is None:
            return
        document = self.current_document
        animation = document.get("animation")
        if (
            isinstance(animation, dict)
            and animation.get("timelines")
            and document.get("format") != "spine-json"
        ):
            _, overrides, _ = runtime.build_animation_state(
                animation,
                self.active_clip_name,
                self.playback_frame_cursor,
                document.get("root", {}),
            )
            document = runtime.apply_animation_state(document, overrides)
        view_mode = self.current_view_mode()
        self.scene.show_panel_fills = self.panel_fill_check.isChecked()
        self.scene.show_hidden_nodes = self.show_hidden_check.isChecked()
        self.scene.load_document(
            document,
            self.current_resolver,
            view_mode=view_mode,
            playback_frame_cursor=self.playback_frame_cursor,
        )
        self.preview.fitInView(
            self.scene.focus_view_rect(view_mode),
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
        )
        if self.selected_node_path:
            self.scene.highlight_node(self.selected_node_path)

    def toggle_playback(self) -> None:
        if not self.play_button.isEnabled():
            return
        if self.playback_timer.isActive():
            self.playback_timer.stop()
            self.play_button.setText("Play")
        else:
            self.playback_timer.start()
            self.play_button.setText("Pause")

    def on_animation_changed(self, index: int) -> None:
        if index < 0:
            return
        name = self.animation_combo.itemText(index)
        self.active_clip_name = name or None
        self.playback_frame_cursor = 0.0
        self.render_current_document()

    def on_playback_tick(self) -> None:
        if self.current_document is None:
            return
        animation = self.current_document.get("animation")
        if not isinstance(animation, dict) or not animation.get("timelines"):
            self.playback_timer.stop()
            self.play_button.setText("Play")
            return
        speed = float(animation.get("speed", 1.0) or 1.0)
        self.playback_frame_cursor += (
            (self.playback_timer.interval() / 1000.0) * self.playback_fps * speed
        )
        self.render_current_document()

    def on_view_mode_changed(self, index: int) -> None:
        del index
        self.render_current_document()

    def on_panel_fill_toggled(self, checked: bool) -> None:
        del checked
        self.render_current_document()

    def on_show_hidden_toggled(self, checked: bool) -> None:
        del checked
        self.render_current_document()

    def current_view_mode(self) -> str:
        return str(self.view_mode_combo.currentData() or "game")

    @staticmethod
    def _document_summary(document: dict[str, Any]) -> dict[str, Any]:
        return {
            "format": document.get("format"),
            "source": document.get("source"),
            "version": document.get("version"),
            "animation": document.get("animation"),
            "stats": document.get("stats"),
        }

    def _add_tree_item(
        self, parent: QtWidgets.QTreeWidgetItem | None, node: dict[str, Any]
    ) -> None:
        position = node.get("position") or {}
        size = node.get("size") or {}
        item = QtWidgets.QTreeWidgetItem(
            [
                node.get("name") or "<unnamed>",
                node.get("classname") or "Unknown",
                f"{position.get('x', 0)}, {position.get('y', 0)}",
                f"{size.get('width', 0)} x {size.get('height', 0)}",
            ]
        )
        self.node_lookup[item] = node
        node_path = str(node.get("__editor_path") or "")
        if node_path:
            self.path_lookup[node_path] = item
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, node_path)
        if parent is None:
            self.node_tree.addTopLevelItem(item)
        else:
            parent.addChild(item)
        for child in node.get("children", []):
            self._add_tree_item(item, child)

    def on_node_selected(
        self,
        current: QtWidgets.QTreeWidgetItem | None,
        previous: QtWidgets.QTreeWidgetItem | None,
    ) -> None:
        del previous
        if current is None:
            return
        node = self.node_lookup.get(current)
        if node is None:
            return
        self.selected_node_path = str(node.get("__editor_path") or "")
        self.inspector.set_node(node)
        self.detail_text.setPlainText(json.dumps(node, ensure_ascii=False, indent=2))
        self.scene.highlight_node(self.selected_node_path)

    def on_scene_node_selected(self, rendered_node: dict[str, Any]) -> None:
        if self.current_document is None:
            return
        node_path = str(rendered_node.get("__editor_path") or "")
        target = runtime.find_node_by_path(
            self.current_document.get("root", {}), node_path
        )
        if target is None:
            return
        self.selected_node_path = node_path
        tree_item = self.path_lookup.get(node_path)
        if tree_item is not None:
            blocker = QtCore.QSignalBlocker(self.node_tree)
            self.node_tree.setCurrentItem(tree_item)
            del blocker
        self.inspector.set_node(target)
        self.detail_text.setPlainText(json.dumps(target, ensure_ascii=False, indent=2))
        self.scene.highlight_node(node_path)

    def on_scene_node_dragged(
        self, rendered_node: dict[str, Any], delta_x: float, delta_y: float
    ) -> None:
        if self.current_document is None:
            return
        node_path = str(rendered_node.get("__editor_path") or "")
        target = runtime.find_node_by_path(
            self.current_document.get("root", {}), node_path
        )
        if target is None:
            return
        position = dict(target.get("position") or {})
        position["x"] = round(float(position.get("x", 0.0) or 0.0) + delta_x, 4)
        position["y"] = round(float(position.get("y", 0.0) or 0.0) - delta_y, 4)
        target["position"] = position
        self.selected_node_path = node_path
        self.inspector.set_node(target)
        self._refresh_tree_item(node_path)
        self.render_current_document()

    def on_inspector_changed(self, node: dict[str, Any]) -> None:
        self.selected_node_path = str(node.get("__editor_path") or "")
        self.detail_text.setPlainText(json.dumps(node, ensure_ascii=False, indent=2))
        self._refresh_tree_item(self.selected_node_path)
        self.render_current_document()

    def _refresh_tree_item(self, node_path: str | None) -> None:
        if not node_path:
            return
        item = self.path_lookup.get(node_path)
        if item is None:
            return
        node = self.node_lookup.get(item)
        if node is None:
            return
        position = node.get("position") or {}
        size = node.get("size") or {}
        item.setText(0, node.get("name") or "<unnamed>")
        item.setText(1, node.get("classname") or "Unknown")
        item.setText(2, f"{position.get('x', 0)}, {position.get('y', 0)}")
        item.setText(3, f"{size.get('width', 0)} x {size.get('height', 0)}")
