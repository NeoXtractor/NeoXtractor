from typing import Any

RESOURCE_DETAIL_KEYS = (
    "resource",
    "backgroundResource",
    "normalResource",
    "pressedResource",
    "disabledResource",
    "charMapResource",
    "barResource",
    "progressBarResource",
    "ballNormalResource",
    "ballPressedResource",
    "ballDisabledResource",
    "backgroundSelectedResource",
    "frontCrossResource",
)


def resolve_primary_resource(details: dict[str, Any]) -> dict[str, Any] | str | None:
    for key in RESOURCE_DETAIL_KEYS:
        value = details.get(key)
        if isinstance(value, dict) and (value.get("path") or value.get("plist")):
            return value
        if isinstance(value, str) and value:
            return value
    return None


def animation_clip_range(
    animation: dict[str, Any], clip_name: str | None
) -> tuple[float, float]:
    clips = animation.get("clips") or []
    if clip_name:
        for clip in clips:
            if clip.get("name") == clip_name:
                return float(clip.get("startIndex", 0) or 0), float(
                    clip.get("endIndex", animation.get("duration", 0)) or 0
                )
    duration = float(animation.get("duration", 0) or 0)
    if clips:
        first = clips[0]
        return float(first.get("startIndex", 0) or 0), float(
            first.get("endIndex", duration) or duration
        )
    return 0.0, duration


def sample_timeline_value(
    property_name: str,
    frames: list[dict[str, Any]],
    frame_index: float,
    default_value: Any = None,
) -> Any:
    if not frames:
        return None
    ordered = sorted(frames, key=lambda item: float(item.get("frameIndex", 0) or 0))
    first_index = float(ordered[0].get("frameIndex", 0) or 0)
    if frame_index < first_index:
        return default_value
    if frame_index == first_index:
        return ordered[0].get("value")

    previous = ordered[0]
    for current in ordered[1:]:
        previous_index = float(previous.get("frameIndex", 0) or 0)
        current_index = float(current.get("frameIndex", 0) or 0)
        if frame_index < current_index:
            if current_index <= previous_index:
                return current.get("value")
            factor = (frame_index - previous_index) / (current_index - previous_index)
            if property_name in {
                "Position",
                "Scale",
                "RotationSkew",
                "AnchorPoint",
            } and bool(previous.get("tween", True)):
                return _lerp_vec2(previous.get("value"), current.get("value"), factor)
            if property_name == "Alpha" and bool(previous.get("tween", True)):
                prev = float(previous.get("value", 255) or 255)
                curr = float(current.get("value", 255) or 255)
                return int(round(prev + (curr - prev) * factor))
            if property_name == "CColor" and bool(previous.get("tween", True)):
                return _lerp_color(previous.get("value"), current.get("value"), factor)
            return previous.get("value")
        if frame_index == current_index:
            return current.get("value")
        previous = current
    return ordered[-1].get("value")


def _lerp_vec2(start: Any, end: Any, factor: float) -> dict[str, float] | None:
    if not isinstance(start, dict) or not isinstance(end, dict):
        return (
            start if isinstance(start, dict) else end if isinstance(end, dict) else None
        )
    return {
        "x": round(
            float(start.get("x", 0))
            + (float(end.get("x", 0)) - float(start.get("x", 0))) * factor,
            4,
        ),
        "y": round(
            float(start.get("y", 0))
            + (float(end.get("y", 0)) - float(start.get("y", 0))) * factor,
            4,
        ),
    }


def _lerp_color(start: Any, end: Any, factor: float) -> dict[str, int] | None:
    if not isinstance(start, dict) or not isinstance(end, dict):
        return (
            start if isinstance(start, dict) else end if isinstance(end, dict) else None
        )
    return {
        channel: int(
            round(
                float(start.get(channel, 0))
                + (float(end.get(channel, 0)) - float(start.get(channel, 0))) * factor
            )
        )
        for channel in ("a", "r", "g", "b")
    }


def _default_timeline_value(node: dict[str, Any], property_name: str) -> Any:
    details = node.get("details") or {}
    if property_name == "Position":
        return node.get("position")
    if property_name == "Scale":
        return node.get("scale")
    if property_name == "RotationSkew":
        return node.get("rotationSkew")
    if property_name == "AnchorPoint":
        return node.get("anchorPoint")
    if property_name == "Alpha":
        return int(node.get("alpha", 255) or 255)
    if property_name == "VisibleForFrame":
        return bool(node.get("visible", True))
    if property_name == "CColor":
        return node.get("color")
    if property_name == "FileData":
        return resolve_primary_resource(details)
    if property_name == "ActionValue":
        return details.get("currentAnimation")
    if property_name == "BlendFunc":
        return details.get("blendFunc")
    return None


def _collect_action_tag_defaults(root: dict[str, Any]) -> dict[int, dict[str, Any]]:
    defaults: dict[int, dict[str, Any]] = {}

    def visit(node: dict[str, Any]) -> None:
        action_tag = node.get("actionTag")
        if action_tag is not None:
            defaults[int(action_tag)] = {
                property_name: _default_timeline_value(node, property_name)
                for property_name in (
                    "Position",
                    "Scale",
                    "RotationSkew",
                    "AnchorPoint",
                    "Alpha",
                    "VisibleForFrame",
                    "CColor",
                    "FileData",
                    "ActionValue",
                    "BlendFunc",
                )
            }
        for child in node.get("children", []):
            visit(child)

    if isinstance(root, dict):
        visit(root)
    return defaults


def build_animation_state(
    animation: dict[str, Any],
    clip_name: str | None,
    frame_cursor: float,
    root: dict[str, Any] | None = None,
) -> tuple[float, dict[int, dict[str, Any]], tuple[float, float]]:
    clip_start, clip_end = animation_clip_range(animation, clip_name)
    clip_length = max(
        clip_end - clip_start + (1.0 if clip_end >= clip_start else 0.0), 0.0
    )
    if clip_length <= 0:
        absolute_frame = clip_start
    else:
        absolute_frame = clip_start + (frame_cursor % clip_length)
    overrides: dict[int, dict[str, Any]] = {}
    default_lookup = _collect_action_tag_defaults(root or {})
    for timeline in animation.get("timelines") or []:
        if not isinstance(timeline, dict):
            continue
        action_tag = timeline.get("actionTag")
        property_name = timeline.get("property")
        if action_tag is None or not isinstance(property_name, str):
            continue
        value = sample_timeline_value(
            property_name,
            timeline.get("frames") or [],
            absolute_frame,
            default_lookup.get(int(action_tag), {}).get(property_name),
        )
        if value is None:
            continue
        overrides.setdefault(int(action_tag), {})[property_name] = value
    return absolute_frame, overrides, (clip_start, clip_end)


def apply_animation_state(
    document: dict[str, Any], overrides: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    if not overrides:
        return document
    cloned = dict(document)
    cloned["root"] = _apply_animation_to_node(document.get("root", {}), overrides)
    return cloned


def _apply_animation_to_node(
    node: dict[str, Any], overrides: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    action_tag = node.get("actionTag")
    node_overrides = overrides.get(int(action_tag)) if action_tag is not None else None
    changed = False
    result = dict(node)

    if node_overrides:
        changed = True
        details = dict(result.get("details") or {})
        for property_name, value in node_overrides.items():
            if property_name == "Position" and isinstance(value, dict):
                result["position"] = value
            elif property_name == "Scale" and isinstance(value, dict):
                result["scale"] = value
            elif property_name == "RotationSkew" and isinstance(value, dict):
                result["rotationSkew"] = value
            elif property_name == "AnchorPoint" and isinstance(value, dict):
                result["anchorPoint"] = value
            elif property_name == "Alpha":
                result["alpha"] = int(value)
            elif property_name == "VisibleForFrame":
                result["visible"] = bool(value)
            elif property_name == "CColor" and isinstance(value, dict):
                result["color"] = value
            elif property_name == "FileData":
                if isinstance(value, (dict, str)):
                    details["resource"] = value
            elif property_name == "ActionValue":
                details["currentAnimation"] = value
        result["details"] = details

    children = node.get("children", [])
    new_children = []
    for child in children:
        updated = _apply_animation_to_node(child, overrides)
        if updated is not child:
            changed = True
        new_children.append(updated)
    if changed:
        result["children"] = new_children
        return result
    return node


def assign_editor_paths(
    node: dict[str, Any], parent_path: str = "", index: int = 0
) -> None:
    label = str(node.get("name") or node.get("classname") or "Node")
    path = f"{parent_path}/{label}[{index}]"
    node["__editor_path"] = path
    for child_index, child in enumerate(node.get("children", [])):
        assign_editor_paths(child, path, child_index)


def capture_editor_originals(node: dict[str, Any]) -> None:
    node["__editor_original"] = {
        "name": node.get("name"),
        "position": dict(node.get("position") or {}),
        "size": dict(node.get("size") or {}),
        "scale": dict(node.get("scale") or {}),
        "rotationSkew": dict(node.get("rotationSkew") or {}),
        "anchorPoint": dict(node.get("anchorPoint") or {}),
        "visible": bool(node.get("visible", True)),
        "alpha": int(node.get("alpha", 255) or 255),
    }
    for child in node.get("children", []):
        capture_editor_originals(child)


def find_node_by_path(
    node: dict[str, Any], editor_path: str | None
) -> dict[str, Any] | None:
    if not editor_path:
        return None
    if node.get("__editor_path") == editor_path:
        return node
    for child in node.get("children", []):
        found = find_node_by_path(child, editor_path)
        if found is not None:
            return found
    return None


def _states_equal(left: Any, right: Any, tolerance: float = 0.0001) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        keys = set(left) | set(right)
        for key in keys:
            if not _states_equal(left.get(key), right.get(key), tolerance):
                return False
        return True
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        try:
            return abs(float(left or 0) - float(right or 0)) <= tolerance
        except (TypeError, ValueError):
            return left == right
    return left == right


def collect_editor_patches(node: dict[str, Any]) -> list[dict[str, Any]]:
    patches: list[dict[str, Any]] = []
    original = node.get("__editor_original") or {}
    patch: dict[str, Any] = {"path": node.get("__editor_path")}
    for key in (
        "name",
        "position",
        "size",
        "scale",
        "rotationSkew",
        "anchorPoint",
        "visible",
        "alpha",
    ):
        current_value = node.get(key)
        original_value = original.get(key)
        if not _states_equal(current_value, original_value):
            patch[key] = current_value
    if len(patch) > 1:
        patches.append(patch)
    for child in node.get("children", []):
        patches.extend(collect_editor_patches(child))
    return patches


def apply_editor_patches(
    document: dict[str, Any], patches: list[dict[str, Any]]
) -> None:
    root = document.get("root", {})
    for patch in patches:
        target = find_node_by_path(root, patch.get("path"))
        if target is None:
            continue
        for key in (
            "name",
            "position",
            "size",
            "scale",
            "rotationSkew",
            "anchorPoint",
            "visible",
            "alpha",
        ):
            if key in patch:
                target[key] = patch[key]
