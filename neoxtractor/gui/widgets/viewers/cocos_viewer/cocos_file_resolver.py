import plistlib
import re
from typing import Any

from PySide6 import QtCore, QtGui

from neoxtractor.core.images import bgra_to_rgba, compblks_convert, image_to_qimage
from neoxtractor.core.npk.class_types import NPKEntry, State
from neoxtractor.gui.utils.npk import get_npk_file

from .cocos_parser import CocosParser


class ResourceResolver:
    def __init__(self):
        self.file_cache: dict[str, int | None] = {}
        self.cache: dict[int, QtGui.QImage | None] = {}
        self.sprite_cache: dict[tuple[int, str], QtGui.QImage | None] = {}
        self.plist_cache: dict[int, dict[str, Any] | None] = {}
        self.document_cache: dict[int, dict[str, Any] | None] = {}

    def resolve(self, relative_path: str) -> NPKEntry | None:
        npk_file = get_npk_file()

        x = self.file_cache.get(relative_path)
        if x is None:
            if relative_path is not None and npk_file is not None:
                file, index = npk_file.find_entry_by_name(relative_path)
                if file is not None:
                    file.state = State.SECONDARY_LOAD
                self.file_cache[relative_path] = index
                return file
            return None

        if npk_file is not None:
            return npk_file.find_entry_by_id(x)
        return None

    def unload_caches(self) -> None:
        npk_file = get_npk_file()

        if npk_file is not None:
            for index in self.file_cache.values():
                if index is not None:
                    npk_file.find_entry_by_id(index).state = State.CACHED

    def load(self, relative_path: str) -> QtGui.QImage | None:
        resource_id = self.resolve(relative_path)
        if resource_id is None:
            return None
        cached = self.cache.get(resource_id.file_signature)
        if cached is not None:
            return cached

        if resource_id.extension == "cbk":
            image = image_to_qimage(compblks_convert(resource_id.data))
        elif resource_id.extension == "png":
            image = QtGui.QImage(resource_id.data)
            if image.isNull:
                image = bgra_to_rgba(resource_id.data)
        else:
            return None

        self.cache[resource_id.file_signature] = image
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
        file = self.resolve(plist_relative_path)
        if file is None:
            return None
        cache_key = (file.file_signature, sprite_name)
        if cache_key in self.sprite_cache:
            return self.sprite_cache[cache_key]

        atlas_data = self._load_plist(file)
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
            or file.name
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

    def _load_plist(self, plist_file: NPKEntry) -> dict[str, Any] | None:
        if plist_file.file_signature in self.plist_cache:
            return self.plist_cache[plist_file.file_signature]
        try:
            data = plistlib.loads(plist_file.data)
        except Exception:
            data = None
        self.plist_cache[plist_file.file_signature] = data
        return data

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
            painter.drawImage(source_rect[0], source_rect[1], crop)
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
        project_source = self.resolve(relative_path)
        if project_source is None:
            return None
        if project_source.file_signature not in self.document_cache:
            try:
                self.document_cache[project_source.file_signature] = (
                    CocosParser().parse_file(project_source)
                )
            except Exception:
                self.document_cache[project_source.file_signature] = None
        return self.document_cache[project_source.file_signature]
