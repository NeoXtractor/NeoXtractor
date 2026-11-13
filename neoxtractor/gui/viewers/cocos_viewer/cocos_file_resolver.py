import io
import os
import plistlib
import re
from typing import Any

from PySide6 import QtCore, QtGui

from neoxtractor.core import processors
from neoxtractor.gui.viewers.base import SiblingAccess, ViewSource

from .cocos_parser import CocosParser


def _extension(name: str) -> str:
    """Lowercase extension of a display name, without a leading dot."""
    return os.path.splitext(name)[1].lstrip(".").lower()


class ResourceResolver:
    """Cross-entry resource access for one Cocos UI preview.

    Sibling entries are reached through the viewer-neutral
    :class:`SiblingAccess` face (name listing + lazy streams), never an
    archive: the resolver does not know what package format holds the
    resources.  Caches key on the display name.
    """

    def __init__(self, siblings: SiblingAccess | None):
        self.siblings = siblings
        self.file_cache: dict[str, str | None] = {}
        self.cache: dict[str, QtGui.QImage | None] = {}
        self.sprite_cache: dict[tuple[str, str], QtGui.QImage | None] = {}
        self.plist_cache: dict[str, dict[str, Any] | None] = {}
        self.document_cache: dict[str, dict[str, Any] | None] = {}

    def resolve(self, relative_path: str) -> str | None:
        """Find the sibling whose basename contains the path stem.

        Matching rule, per the viewer's display-name policy: normalize
        separators, drop everything from the first dot, substring-match
        against the basename.
        """
        if self.siblings is None or relative_path is None:
            return None
        if relative_path in self.file_cache:
            return self.file_cache[relative_path]

        needle = relative_path.replace("/", "\\").split(".", 1)[0]
        found: str | None = None
        for name in self.siblings.names():
            if needle in os.path.basename(name):
                found = name
                break
        self.file_cache[relative_path] = found
        return found

    def unload_caches(self) -> None:
        """Drop every resolved resource (the next preview re-reads)."""
        self.file_cache.clear()
        self.cache.clear()
        self.sprite_cache.clear()
        self.plist_cache.clear()
        self.document_cache.clear()

    def _read(self, name: str) -> bytes | None:
        """Read one sibling fully (small asset), closing the stream."""
        if self.siblings is None:
            return None
        stream = self.siblings.open(name)
        if stream is None:
            return None
        try:
            return stream.read()
        finally:
            stream.close()

    def load(self, relative_path: str) -> QtGui.QImage | None:
        name = self.resolve(relative_path)
        if name is None:
            return None
        cached = self.cache.get(name)
        if cached is not None:
            return cached

        extension = _extension(name)
        if extension not in ("cbk", "png"):
            return None
        data = self._read(name)
        if data is None:
            return None

        if extension == "cbk":
            result = processors.read(io.BytesIO(data), format="image")
            image = result.data.toqimage() if result is not None else None
        else:
            image = QtGui.QImage(data)
            if image.isNull():
                # Qt's PNG reader declined: PIL covers odd variants
                # (e.g. DDS-wrapped payloads).
                result = processors.read(io.BytesIO(data))
                image = result.data.toqimage() if result is not None else None

        self.cache[name] = image
        return image

    def load_resource(
        self, resource: dict[str, Any] | str | None
    ) -> QtGui.QImage | None:
        if isinstance(resource, dict):
            sprite_name = resource.get("path")
            plist_path = resource.get("plist")
            if (
                isinstance(sprite_name, str)
                and isinstance(plist_path, str)
                and sprite_name.split(".")[1] == "plist"
            ):
                sprite = self.load_sprite_frame(plist_path, sprite_name)
                if sprite is not None:
                    return sprite
            if isinstance(sprite_name, str):
                return self.load(sprite_name)
            return None
        if isinstance(resource, str):
            return self.load(resource)
        return None

    def load_sprite_frame(
        self, plist_relative_path: str, sprite_name: str
    ) -> QtGui.QImage | None:
        name = self.resolve(plist_relative_path)
        if name is None:
            return None
        cache_key = (name, sprite_name)
        if cache_key in self.sprite_cache:
            return self.sprite_cache[cache_key]

        atlas_data = self._load_plist(name)
        if atlas_data is None:
            self.sprite_cache[cache_key] = None
            return None

        frames = atlas_data.get("frames") or {}
        frame_info = frames.get(sprite_name)
        if not isinstance(frame_info, dict):
            self.sprite_cache[cache_key] = None
            return None

        metadata = atlas_data.get("metadata") or {}
        atlas_name = (
            metadata.get("realTextureFileName")
            or metadata.get("textureFileName")
            or name
        )
        atlas_image = self.load(plist_relative_path + atlas_name)
        if atlas_image is None:
            self.sprite_cache[cache_key] = None
            return None

        try:
            sprite = self._extract_sprite_frame(atlas_image, frame_info)
        except Exception:
            sprite = None
        self.sprite_cache[cache_key] = sprite
        return sprite

    def _load_plist(self, plist_name: str) -> dict[str, Any] | None:
        if plist_name in self.plist_cache:
            return self.plist_cache[plist_name]
        data = self._read(plist_name)
        try:
            parsed = plistlib.loads(data) if data is not None else None
        except Exception:
            parsed = None
        self.plist_cache[plist_name] = parsed
        return parsed

    @staticmethod
    def _extract_sprite_frame(
        atlas_image: QtGui.QImage, frame_info: dict[str, Any]
    ) -> QtGui.QImage | None:
        frame_rect = ResourceResolver._parse_rect(frame_info.get("frame"))
        source_size = ResourceResolver._parse_size(frame_info.get("sourceSize"))
        source_rect = ResourceResolver._parse_rect(frame_info.get("sourceColorRect"))
        rotated = bool(frame_info.get("rotated"))
        if frame_rect is None or source_size is None:
            return None

        crop = atlas_image.copy(frame_rect)
        if rotated:
            transform = QtGui.QTransform().rotate(90)
            crop = crop.transformed(
                transform, QtCore.Qt.TransformationMode.SmoothTransformation
            )

        if source_rect is None:
            return crop

        canvas = QtGui.QImage(
            source_size[0],
            source_size[1],
            QtGui.QImage.Format.Format_ARGB32_Premultiplied,
        )
        canvas.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter()
        if not painter.begin(canvas):
            return None
        try:
            painter.drawImage(source_rect.x(), source_rect.y(), crop)
        finally:
            painter.end()
        return canvas

    @staticmethod
    def _parse_rect(raw: Any) -> QtCore.QRect | None:
        if not isinstance(raw, str):
            return None
        values = [
            int(round(float(value))) for value in re.findall(r"-?\d+(?:\.\d+)?", raw)
        ]
        if len(values) != 4:
            return None
        return QtCore.QRect(values[0], values[1], values[2], values[3])

    @staticmethod
    def _parse_size(raw: Any) -> tuple[int, int] | None:
        if not isinstance(raw, str):
            return None
        values = [
            int(round(float(value))) for value in re.findall(r"-?\d+(?:\.\d+)?", raw)
        ]
        if len(values) != 2:
            return None
        return values[0], values[1]

    def load_project(self, relative_path: str | None) -> dict[str, Any] | None:
        if relative_path is None:
            return None
        # The document cache keys on the resolved display name; a None
        # entry is a cached load/parse failure.
        name = self.resolve(relative_path)
        if name is None:
            return None
        if name not in self.document_cache:
            data = self._read(name)
            if data is None:
                self.document_cache[name] = None
            else:
                # parse_file only reads source.extension()/content(), so a
                # stream-backed ViewSource over the sibling bytes suffices.
                project_source = ViewSource(
                    name=name, input="stream", stream=io.BytesIO(data)
                )
                try:
                    self.document_cache[name] = (
                        CocosParser().parse_file(project_source)
                    )
                except Exception:
                    self.document_cache[name] = None
        return self.document_cache[name]
