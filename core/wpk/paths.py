"""Path and file-handle helpers for IDX/WPK archives."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import BinaryIO

from core.logger import get_logger


class WPKPathResolver:
    """Resolve package ids to WPK files and keep open-file handles cached."""

    def __init__(self, reader):
        self.reader = reader

    def iter_wpk_path_candidates(self, pkg_id: int):
        seen = set()

        def push(candidate: str):
            if not candidate:
                return
            candidate = os.path.normpath(candidate)
            if candidate in seen:
                return
            seen.add(candidate)
            yield candidate

        custom = self.reader._wpk_paths.get(pkg_id)
        if custom:
            yield from push(custom)

        stem = re.escape(self.reader.base_stem)
        regex = re.compile(rf"^{stem}_?0*{int(pkg_id)}\.wpk$", re.IGNORECASE)

        direct_candidates = [
            os.path.join(self.reader.base_dir, f"{self.reader.base_stem}{pkg_id}.wpk"),
            os.path.join(self.reader.base_dir, f"{self.reader.base_stem}_{pkg_id}.wpk"),
        ]
        for candidate in direct_candidates:
            yield from push(candidate)

        try:
            for name in sorted(os.listdir(self.reader.base_dir)):
                if regex.fullmatch(name):
                    yield from push(os.path.join(self.reader.base_dir, name))
        except OSError:
            return

    def find_wpk_path(self, pkg_id: int) -> str:
        if self.reader.mode == "wpk":
            assert self.reader.wpk_path is not None
            return self.reader.wpk_path

        for candidate in self.iter_wpk_path_candidates(pkg_id):
            if os.path.exists(candidate):
                self.reader._wpk_paths[pkg_id] = candidate
                return candidate

        custom = self.reader._wpk_paths.get(pkg_id)
        if custom:
            return custom

        return os.path.join(
            self.reader.base_dir, f"{self.reader.base_stem}{pkg_id}.wpk"
        )

    def is_slot_file_pkg(self, pkg_id: int) -> bool:
        return not (0 <= int(pkg_id) <= 15)

    def get_slot_file_dir(self, base_dir: str | None = None) -> Path:
        stem = self.reader.base_stem
        slot_name = re.sub(r"\d+$", "", stem) or stem
        return Path(base_dir or self.reader.base_dir) / slot_name

    def get_wpk_handle(self, pkg_id: int) -> BinaryIO | None:
        if pkg_id in self.reader._wpk_cache:
            return self.reader._wpk_cache[pkg_id]

        wpk_path = self.find_wpk_path(pkg_id)
        if not os.path.exists(wpk_path):
            get_logger().warning("WPK not found for pkg_id=%d: %s", pkg_id, wpk_path)
            self.reader._wpk_cache[pkg_id] = None
            return None

        handle = open(wpk_path, "rb")
        self.reader._wpk_cache[pkg_id] = handle
        return handle
