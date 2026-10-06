import json
import struct
from collections import Counter
from typing import Any, Dict, Optional

from flatbuffers import encode
from flatbuffers import number_types as N
from flatbuffers.table import Table

from neoxtractor.core.file import IFile


def _prune(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            pruned = _prune(item)
            if pruned is None:
                continue
            if pruned == {} or pruned == []:
                continue
            cleaned[key] = pruned
        return cleaned
    if isinstance(value, list):
        cleaned = [_prune(item) for item in value]
        return [item for item in cleaned if item is not None]
    return value


def _float_dict(
    keys: tuple[str, ...], values: tuple[float, ...] | None
) -> dict[str, float] | None:
    if values is None:
        return None
    return {key: round(value, 4) for key, value in zip(keys, values, strict=True)}


def _collect_resource_paths(value: Any) -> list[str]:
    results: list[str] = []
    if isinstance(value, dict):
        if isinstance(value.get("path"), str):
            results.append(value["path"])
        for item in value.values():
            results.extend(_collect_resource_paths(item))
    elif isinstance(value, list):
        for item in value:
            results.extend(_collect_resource_paths(item))
    return results


def _scan_max_time(value: Any) -> float:
    max_time = 0.0
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "time":
                try:
                    max_time = max(max_time, float(item))
                except (TypeError, ValueError):
                    pass
            else:
                max_time = max(max_time, _scan_max_time(item))
    elif isinstance(value, list):
        for item in value:
            max_time = max(max_time, _scan_max_time(item))
    return round(max_time, 4)


class FlatBufferReader:
    def __init__(self, payload: bytes):
        self.payload = payload

    def root(self) -> Table:
        root_pos = encode.Get(N.UOffsetTFlags.packer_type, self.payload, 0)
        return Table(self.payload, root_pos)

    def string(self, table: Table | None, slot: int) -> str | None:
        if table is None:
            return None
        offset = table.Offset(slot)
        if not offset:
            return None
        return table.String(table.Pos + offset).decode("utf-8", errors="replace")

    def table(self, table: Table | None, slot: int) -> Table | None:
        if table is None:
            return None
        offset = table.Offset(slot)
        if not offset:
            return None
        return Table(self.payload, table.Indirect(table.Pos + offset))

    def vector_tables(self, table: Table | None, slot: int) -> list[Table]:
        if table is None:
            return []
        offset = table.Offset(slot)
        if not offset:
            return []
        vector_start = table.Vector(offset)
        return [
            Table(
                self.payload,
                table.Indirect(vector_start + index * N.UOffsetTFlags.bytewidth),
            )
            for index in range(table.VectorLen(offset))
        ]

    def vector_strings(self, table: Table | None, slot: int) -> list[str]:
        if table is None:
            return []
        offset = table.Offset(slot)
        if not offset:
            return []
        vector_start = table.Vector(offset)
        result: list[str] = []
        for index in range(table.VectorLen(offset)):
            string_offset = vector_start + index * N.UOffsetTFlags.bytewidth
            string_table = Table(self.payload, string_offset)
            result.append(
                string_table.String(string_offset).decode("utf-8", errors="replace")
            )
        return result

    def int32(self, table: Table | None, slot: int, default: int = 0) -> int:
        if table is None:
            return default
        return int(table.GetSlot(slot, default, N.Int32Flags))

    def uint8(self, table: Table | None, slot: int, default: int = 0) -> int:
        if table is None:
            return default
        return int(table.GetSlot(slot, default, N.Uint8Flags))

    def bool(self, table: Table | None, slot: int, default: bool = False) -> bool:
        return bool(self.uint8(table, slot, 1 if default else 0))

    def float32(self, table: Table | None, slot: int, default: float = 0.0) -> float:
        if table is None:
            return default
        return round(float(table.GetSlot(slot, default, N.Float32Flags)), 4)

    def struct_floats(
        self, table: Table | None, slot: int, size: int, names: tuple[str, ...]
    ) -> dict[str, float] | None:
        if table is None:
            return None
        offset = table.Offset(slot)
        if not offset:
            return None
        values = struct.unpack_from(
            "<" + ("f" * size), self.payload, table.Pos + offset
        )
        return _float_dict(names, values)

    def color(self, table: Table | None, slot: int) -> dict[str, int] | None:
        if table is None:
            return None
        offset = table.Offset(slot)
        if not offset:
            return None
        a, r, g, b = struct.unpack_from("<BBBB", self.payload, table.Pos + offset)
        return {"a": a, "r": r, "g": g, "b": b}

    def resource(self, table: Table | None) -> dict[str, Any] | None:
        if table is None:
            return None
        path = self.string(table, 4)
        plist = self.string(table, 6)
        resource_type = self.int32(table, 8, 0)
        if path is None and plist is None:
            return None
        resource_kind = "plist" if plist else "normal"
        return _prune(
            {
                "path": path,
                "plist": plist,
                "resourceType": resource_type,
                "kind": resource_kind,
            }
        )

    def layout_component(self, table: Table | None) -> dict[str, Any] | None:
        if table is None:
            return None
        return _prune(
            {
                "positionXPercentEnabled": self.bool(table, 4),
                "positionYPercentEnabled": self.bool(table, 6),
                "positionXPercent": self.float32(table, 8),
                "positionYPercent": self.float32(table, 10),
                "sizeXPercentEnabled": self.bool(table, 12),
                "sizeYPercentEnabled": self.bool(table, 14),
                "sizeXPercent": self.float32(table, 16),
                "sizeYPercent": self.float32(table, 18),
                "stretchHorizontalEnabled": self.bool(table, 20),
                "stretchVerticalEnabled": self.bool(table, 22),
                "horizontalEdge": self.string(table, 24),
                "verticalEdge": self.string(table, 26),
                "leftMargin": self.float32(table, 28),
                "rightMargin": self.float32(table, 30),
                "topMargin": self.float32(table, 32),
                "bottomMargin": self.float32(table, 34),
            }
        )

    def widget(self, table: Table | None) -> dict[str, Any]:
        if table is None:
            return {}
        return _prune(
            {
                "name": self.string(table, 4),
                "actionTag": self.int32(table, 6),
                "rotationSkew": self.struct_floats(table, 8, 2, ("x", "y")),
                "zOrder": self.int32(table, 10),
                "visible": self.bool(table, 12, True),
                "alpha": self.uint8(table, 14, 255),
                "tag": self.int32(table, 16),
                "position": self.struct_floats(table, 18, 2, ("x", "y")),
                "scale": self.struct_floats(table, 20, 2, ("x", "y")),
                "anchorPoint": self.struct_floats(table, 22, 2, ("x", "y")),
                "color": self.color(table, 24),
                "size": self.struct_floats(table, 26, 2, ("width", "height")),
                "flipX": self.bool(table, 28),
                "flipY": self.bool(table, 30),
                "ignoreSize": self.bool(table, 32),
                "touchEnabled": self.bool(table, 34),
                "frameEvent": self.string(table, 36),
                "customProperty": self.string(table, 38),
                "callBackType": self.string(table, 40),
                "callBackName": self.string(table, 42),
                "layoutComponent": self.layout_component(self.table(table, 44)),
            }
        )


class CocosBinaryParser:
    def __init__(self, source: IFile):
        self.source = source
        self.reader = FlatBufferReader(source.data)

    def parse(self) -> dict[str, Any]:
        root = self.reader.root()
        node_tree = self.reader.table(root, 10)
        action = self.parse_action(root)
        document = _prune(
            {
                "format": "cocos-csb",
                "source": str(self.source),
                "version": self.reader.string(root, 4),
                "textures": self.reader.vector_strings(root, 6),
                "texturePngs": self.reader.vector_strings(root, 8),
                "animationCount": len(self.reader.vector_tables(root, 14)),
                "animation": action,
                "root": self.parse_node(node_tree),
            }
        )
        document["stats"] = self._stats(document["root"])
        return document

    def parse_action(self, root: Table | None) -> dict[str, Any] | None:
        action = self.reader.table(root, 12)
        if action is None:
            return None
        clips = []
        for item in self.reader.vector_tables(root, 14):
            clips.append(
                _prune(
                    {
                        "name": self.reader.string(item, 4),
                        "startIndex": self.reader.int32(item, 6),
                        "endIndex": self.reader.int32(item, 8),
                    }
                )
            )
        timelines = [
            timeline
            for item in self.reader.vector_tables(action, 8)
            if (timeline := self.parse_timeline(item))
        ]
        return _prune(
            {
                "duration": self.reader.int32(action, 4),
                "speed": self.reader.float32(action, 6),
                "currentAnimationName": self.reader.string(action, 10),
                "timelineCount": len(timelines),
                "animatedNodeCount": len(
                    {
                        item.get("actionTag")
                        for item in timelines
                        if item.get("actionTag") is not None
                    }
                ),
                "animationCount": len(clips),
                "animationNames": [
                    clip.get("name") for clip in clips if clip.get("name")
                ],
                "clips": clips,
                "timelines": timelines,
            }
        )

    def parse_timeline(self, table: Table | None) -> dict[str, Any] | None:
        if table is None:
            return None
        frames = [
            frame
            for item in self.reader.vector_tables(table, 8)
            if (frame := self.parse_frame(item))
        ]
        return _prune(
            {
                "property": self.reader.string(table, 4),
                "actionTag": self.reader.int32(table, 6),
                "frames": frames,
            }
        )

    def parse_frame(self, table: Table | None) -> dict[str, Any] | None:
        if table is None:
            return None

        point_frame = self.reader.table(table, 4)
        if point_frame is not None:
            return _prune(
                {
                    "frameIndex": self.reader.int32(point_frame, 4),
                    "tween": self.reader.bool(point_frame, 6, True),
                    "value": self.reader.struct_floats(point_frame, 8, 2, ("x", "y")),
                }
            )

        scale_frame = self.reader.table(table, 6)
        if scale_frame is not None:
            return _prune(
                {
                    "frameIndex": self.reader.int32(scale_frame, 4),
                    "tween": self.reader.bool(scale_frame, 6, True),
                    "value": self.reader.struct_floats(scale_frame, 8, 2, ("x", "y")),
                }
            )

        color_frame = self.reader.table(table, 8)
        if color_frame is not None:
            return _prune(
                {
                    "frameIndex": self.reader.int32(color_frame, 4),
                    "tween": self.reader.bool(color_frame, 6, True),
                    "value": self.reader.color(color_frame, 8),
                }
            )

        texture_frame = self.reader.table(table, 10)
        if texture_frame is not None:
            return _prune(
                {
                    "frameIndex": self.reader.int32(texture_frame, 4),
                    "tween": self.reader.bool(texture_frame, 6, True),
                    "value": self.reader.resource(self.reader.table(texture_frame, 8)),
                }
            )

        event_frame = self.reader.table(table, 12)
        if event_frame is not None:
            return _prune(
                {
                    "frameIndex": self.reader.int32(event_frame, 4),
                    "tween": self.reader.bool(event_frame, 6, True),
                    "value": self.reader.string(event_frame, 8),
                }
            )

        int_frame = self.reader.table(table, 14)
        if int_frame is not None:
            return _prune(
                {
                    "frameIndex": self.reader.int32(int_frame, 4),
                    "tween": self.reader.bool(int_frame, 6, True),
                    "value": self.reader.int32(int_frame, 8),
                }
            )

        bool_frame = self.reader.table(table, 16)
        if bool_frame is not None:
            return _prune(
                {
                    "frameIndex": self.reader.int32(bool_frame, 4),
                    "tween": self.reader.bool(bool_frame, 6, True),
                    "value": self.reader.bool(bool_frame, 8, True),
                }
            )

        inner_action_frame = self.reader.table(table, 18)
        if inner_action_frame is not None:
            return _prune(
                {
                    "frameIndex": self.reader.int32(inner_action_frame, 4),
                    "tween": self.reader.bool(inner_action_frame, 6, True),
                    "value": {
                        "innerActionType": self.reader.int32(inner_action_frame, 8),
                        "currentAnimationName": self.reader.string(
                            inner_action_frame, 10
                        ),
                        "singleFrameIndex": self.reader.int32(inner_action_frame, 12),
                    },
                }
            )

        blend_frame = self.reader.table(table, 20)
        if blend_frame is not None:
            return _prune(
                {
                    "frameIndex": self.reader.int32(blend_frame, 4),
                    "tween": self.reader.bool(blend_frame, 6, True),
                    "value": self.reader.struct_floats(
                        blend_frame, 8, 2, ("src", "dst")
                    ),
                }
            )
        return None

    def parse_node(self, node_tree: Table | None) -> dict[str, Any] | None:
        if node_tree is None:
            return None

        classname = self.reader.string(node_tree, 4) or "Unknown"
        options_wrapper = self.reader.table(node_tree, 8)
        options = self.reader.table(options_wrapper, 4)
        widget, details = self._parse_options(classname, options)
        children = [
            node
            for child in self.reader.vector_tables(node_tree, 6)
            if (node := self.parse_node(child))
        ]

        return _prune(
            {
                "classname": classname,
                "customClassName": self.reader.string(node_tree, 10),
                **widget,
                "details": details,
                "children": children,
            }
        )

    def _base_widget(self, classname: str, options: Table | None) -> dict[str, Any]:
        if options is None:
            return {}
        if classname in {"Node"}:
            return self.reader.widget(options)
        return self.reader.widget(self.reader.table(options, 4))

    def _parse_options(
        self, classname: str, options: Table | None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        widget = self._base_widget(classname, options)
        details = {}

        if classname == "Node":
            return widget, details
        if classname == "SingleNode":
            return widget, details
        if classname in {"Sprite", "Particle"}:
            details = {
                "resource": self.reader.resource(self.reader.table(options, 6)),
                "blendFunc": self.reader.struct_floats(options, 8, 2, ("src", "dst")),
            }
        elif classname == "Panel":
            details = {
                "backgroundResource": self.reader.resource(
                    self.reader.table(options, 6)
                ),
                "clipEnabled": self.reader.bool(options, 8),
                "backgroundColor": self.reader.color(options, 10),
                "backgroundStartColor": self.reader.color(options, 12),
                "backgroundEndColor": self.reader.color(options, 14),
                "colorType": self.reader.int32(options, 16),
                "backgroundColorOpacity": self.reader.uint8(options, 18, 255),
                "colorVector": self.reader.struct_floats(options, 20, 2, ("x", "y")),
                "capInsets": self.reader.struct_floats(
                    options, 22, 4, ("x", "y", "width", "height")
                ),
                "scale9Size": self.reader.struct_floats(
                    options, 24, 2, ("width", "height")
                ),
                "backgroundScale9Enabled": self.reader.bool(options, 26),
            }
        elif classname == "Button":
            details = {
                "normalResource": self.reader.resource(self.reader.table(options, 6)),
                "pressedResource": self.reader.resource(self.reader.table(options, 8)),
                "disabledResource": self.reader.resource(
                    self.reader.table(options, 10)
                ),
                "fontResource": self.reader.resource(self.reader.table(options, 12)),
                "text": self.reader.string(options, 14),
                "fontName": self.reader.string(options, 16),
                "fontSize": self.reader.int32(options, 18),
                "textColor": self.reader.color(options, 20),
                "capInsets": self.reader.struct_floats(
                    options, 22, 4, ("x", "y", "width", "height")
                ),
                "scale9Size": self.reader.struct_floats(
                    options, 24, 2, ("width", "height")
                ),
                "scale9Enabled": self.reader.bool(options, 26),
                "displayState": self.reader.uint8(options, 28, 1),
                "outlineEnabled": self.reader.bool(options, 30),
                "outlineColor": self.reader.color(options, 32),
                "outlineSize": self.reader.int32(options, 34, 1),
                "shadowEnabled": self.reader.bool(options, 36),
                "shadowColor": self.reader.color(options, 38),
                "shadowOffset": {
                    "x": self.reader.float32(options, 40, 2),
                    "y": self.reader.float32(options, 42, -2),
                },
                "shadowBlurRadius": self.reader.int32(options, 44),
                "localized": self.reader.bool(options, 46),
            }
        elif classname == "CheckBox":
            details = {
                "backgroundResource": self.reader.resource(
                    self.reader.table(options, 6)
                ),
                "backgroundSelectedResource": self.reader.resource(
                    self.reader.table(options, 8)
                ),
                "frontCrossResource": self.reader.resource(
                    self.reader.table(options, 10)
                ),
                "backgroundDisabledResource": self.reader.resource(
                    self.reader.table(options, 12)
                ),
                "frontCrossDisabledResource": self.reader.resource(
                    self.reader.table(options, 14)
                ),
                "selected": self.reader.bool(options, 16, True),
                "displayState": self.reader.uint8(options, 18, 1),
            }
        elif classname == "ImageView":
            details = {
                "resource": self.reader.resource(self.reader.table(options, 6)),
                "capInsets": self.reader.struct_floats(
                    options, 8, 4, ("x", "y", "width", "height")
                ),
                "scale9Size": self.reader.struct_floats(
                    options, 10, 2, ("width", "height")
                ),
                "scale9Enabled": self.reader.bool(options, 12),
            }
        elif classname == "TextAtlas":
            details = {
                "charMapResource": self.reader.resource(self.reader.table(options, 6)),
                "text": self.reader.string(options, 8),
                "startCharMap": self.reader.string(options, 10),
                "itemWidth": self.reader.int32(options, 12),
                "itemHeight": self.reader.int32(options, 14),
            }
        elif classname == "Text":
            details = {
                "fontResource": self.reader.resource(self.reader.table(options, 6)),
                "fontName": self.reader.string(options, 8),
                "fontSize": self.reader.int32(options, 10),
                "text": self.reader.string(options, 12),
                "areaSize": {
                    "width": self.reader.int32(options, 14),
                    "height": self.reader.int32(options, 16),
                },
                "horizontalAlignment": self.reader.int32(options, 18),
                "verticalAlignment": self.reader.int32(options, 20),
                "touchScaleEnable": self.reader.bool(options, 22),
                "customSize": self.reader.bool(options, 24),
                "outlineEnabled": self.reader.bool(options, 26),
                "outlineColor": self.reader.color(options, 28),
                "outlineSize": self.reader.int32(options, 30, 1),
                "shadowEnabled": self.reader.bool(options, 32),
                "shadowColor": self.reader.color(options, 34),
                "shadowOffset": {
                    "x": self.reader.float32(options, 36, 2),
                    "y": self.reader.float32(options, 38, -2),
                },
                "shadowBlurRadius": self.reader.int32(options, 40),
                "localized": self.reader.bool(options, 42),
            }
        elif classname == "TextField":
            details = {
                "fontResource": self.reader.resource(self.reader.table(options, 6)),
                "fontName": self.reader.string(options, 8),
                "fontSize": self.reader.int32(options, 10),
                "text": self.reader.string(options, 12),
                "placeholder": self.reader.string(options, 14),
                "passwordEnabled": self.reader.bool(options, 16),
                "passwordMask": self.reader.string(options, 18),
                "maxLengthEnabled": self.reader.bool(options, 20),
                "maxLength": self.reader.int32(options, 22),
                "areaSize": {
                    "width": self.reader.int32(options, 24),
                    "height": self.reader.int32(options, 26),
                },
                "customSize": self.reader.bool(options, 28),
                "localized": self.reader.bool(options, 30),
            }
        elif classname == "LoadingBar":
            details = {
                "resource": self.reader.resource(self.reader.table(options, 6)),
                "percent": self.reader.int32(options, 8, 80),
                "direction": self.reader.int32(options, 10),
            }
        elif classname == "Slider":
            details = {
                "barResource": self.reader.resource(self.reader.table(options, 6)),
                "ballNormalResource": self.reader.resource(
                    self.reader.table(options, 8)
                ),
                "ballPressedResource": self.reader.resource(
                    self.reader.table(options, 10)
                ),
                "ballDisabledResource": self.reader.resource(
                    self.reader.table(options, 12)
                ),
                "progressBarResource": self.reader.resource(
                    self.reader.table(options, 14)
                ),
                "percent": self.reader.int32(options, 16, 50),
                "displayState": self.reader.uint8(options, 18, 1),
            }
        elif classname == "ScrollView":
            details = {
                "backgroundResource": self.reader.resource(
                    self.reader.table(options, 6)
                ),
                "clipEnabled": self.reader.bool(options, 8),
                "backgroundColor": self.reader.color(options, 10),
                "backgroundStartColor": self.reader.color(options, 12),
                "backgroundEndColor": self.reader.color(options, 14),
                "colorType": self.reader.int32(options, 16),
                "backgroundColorOpacity": self.reader.uint8(options, 18, 255),
                "colorVector": self.reader.struct_floats(options, 20, 2, ("x", "y")),
                "capInsets": self.reader.struct_floats(
                    options, 22, 4, ("x", "y", "width", "height")
                ),
                "scale9Size": self.reader.struct_floats(
                    options, 24, 2, ("width", "height")
                ),
                "backgroundScale9Enabled": self.reader.bool(options, 26),
                "innerSize": self.reader.struct_floats(
                    options, 28, 2, ("width", "height")
                ),
                "direction": self.reader.int32(options, 30),
                "bounceEnabled": self.reader.bool(options, 32),
                "scrollbarEnabled": self.reader.bool(options, 34, True),
                "scrollbarAutoHide": self.reader.bool(options, 36, True),
                "scrollbarAutoHideTime": self.reader.float32(options, 38, 0.2),
            }
        elif classname == "ListView":
            details = {
                "backgroundResource": self.reader.resource(
                    self.reader.table(options, 6)
                ),
                "clipEnabled": self.reader.bool(options, 8),
                "backgroundColor": self.reader.color(options, 10),
                "backgroundStartColor": self.reader.color(options, 12),
                "backgroundEndColor": self.reader.color(options, 14),
                "colorType": self.reader.int32(options, 16),
                "backgroundColorOpacity": self.reader.uint8(options, 18, 255),
                "colorVector": self.reader.struct_floats(options, 20, 2, ("x", "y")),
                "capInsets": self.reader.struct_floats(
                    options, 22, 4, ("x", "y", "width", "height")
                ),
                "scale9Size": self.reader.struct_floats(
                    options, 24, 2, ("width", "height")
                ),
                "backgroundScale9Enabled": self.reader.bool(options, 26),
                "innerSize": self.reader.struct_floats(
                    options, 28, 2, ("width", "height")
                ),
                "direction": self.reader.int32(options, 30),
                "bounceEnabled": self.reader.bool(options, 32),
                "itemMargin": self.reader.int32(options, 34),
                "directionType": self.reader.string(options, 36),
                "horizontalType": self.reader.string(options, 38),
                "verticalType": self.reader.string(options, 40),
            }
        elif classname == "ProjectNode":
            details = {
                "projectFile": self.reader.string(options, 6),
                "innerActionSpeed": self.reader.float32(options, 8),
            }

        return widget, _prune(details)

    def _stats(self, root: dict[str, Any]) -> dict[str, Any]:
        counter: Counter[str] = Counter()
        resources: set[str] = set()

        def walk(node: dict[str, Any]) -> int:
            counter[node.get("classname", "Unknown")] += 1
            details = node.get("details", {})
            if isinstance(details, dict):
                for path in _collect_resource_paths(details):
                    resources.add(path)
            total = 1
            for child in node.get("children", []):
                total += walk(child)
            return total

        node_count = walk(root)
        return {
            "nodeCount": node_count,
            "classCounts": dict(counter.most_common()),
            "resourceCount": len(resources),
            "resources": sorted(resources),
        }


class CocosParser:
    """Parser for Cocos UI files"""

    def __init__(self):
        """Initialize the parser"""
        ...

    def parse_file(self, entry: IFile) -> Optional[Dict[str, Any]]:
        """
        Parse a Cocos UI file

        Args:
            entry: Path to the .csb or .json file

        Returns:
            Parsed data as dictionary, or None if parsing failed
        """

        if entry.extension == ".json":
            payload = json.loads(entry.data.decode())
            if isinstance(payload, dict) and {
                "skeleton",
                "bones",
                "slots",
                "animations",
            } & set(payload.keys()):
                return None  # SpineJsonParser(source, payload).parse()
            return None  # CocosJsonParser(source, payload).parse()
        else:
            return CocosBinaryParser(entry).parse()
