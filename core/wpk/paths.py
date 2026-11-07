"""Path and file-handle helpers for IDX/WPK archives."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import BinaryIO

from core.logger import get_logger


def pkg_name(pkg_id: int) -> str:
    """Return the file-name spelling of a package id.

    NeoX names payload packs in hex, so package ``0x10`` is ``<stem>10.wpk``
    and package ``0x0A`` is ``<stem>a.wpk`` -- not ``<stem>10.wpk``.
    """
    return format(int(pkg_id), "x")


def parse_wpk_suffix(file_name: str, stem: str) -> int | None:
    """Return the package id encoded in a WPK file name, or None.

    Accepts ``<stem><hex>``, ``<stem>_<hex>`` and zero-padded spellings.
    """
    low = file_name.lower()
    if not low.endswith(".wpk") or not low.startswith(stem.lower()):
        return None
    suffix = low[len(stem) :].removesuffix(".wpk").lstrip("_").lstrip("0")
    if not suffix:
        return 0
    try:
        return int(suffix, 16)
    except ValueError:
        return None


class WPKPathResolver:
    """Resolve package ids to WPK files and keep open-file handles cached."""

    def __init__(self, reader):
        self.reader = reader
        self._slot_dir_cache: dict[Path, Path] = {}

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

        # Package names are hex: with ``script.idx`` the payload of package
        # 0x30 lives in ``script30.wpk``. The decimal spelling is *not* probed
        # because every decimal name in 10..25 is also a valid hex name of a
        # different package (decimal 10 == hex 0x0A vs <stem>10.wpk == 0x10),
        # so a fallback would silently read the wrong package.
        name = pkg_name(pkg_id)
        stem = re.escape(self.reader.base_stem)
        regex = re.compile(rf"^{stem}_?0*{re.escape(name)}\.wpk$", re.IGNORECASE)

        direct_candidates = [
            os.path.join(self.reader.base_dir, f"{self.reader.base_stem}{name}.wpk"),
            os.path.join(self.reader.base_dir, f"{self.reader.base_stem}_{name}.wpk"),
        ]
        for candidate in direct_candidates:
            yield from push(candidate)

        try:
            for file_name in sorted(os.listdir(self.reader.base_dir)):
                if regex.fullmatch(file_name):
                    yield from push(os.path.join(self.reader.base_dir, file_name))
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
            self.reader.base_dir, f"{self.reader.base_stem}{pkg_name(pkg_id)}.wpk"
        )

    def is_slot_file_pkg(self, pkg_id: int) -> bool:
        return not (0 <= int(pkg_id) <= 15)

    def get_slot_file_dir(self, base_dir: str | None = None) -> Path:
        root = Path(base_dir or self.reader.base_dir)
        cached = self._slot_dir_cache.get(root)
        if cached is not None:
            return cached

        stem = self.reader.base_stem
        # A trailing digit is not always a package index: packs such as
        # script3.idx keep their slot files in script3/ while the payload
        # packs are script30.wpk/script31.wpk. Probe the exact stem first and
        # only fall back to the digit-stripped name used by older packs.
        candidates = [stem]
        stripped = re.sub(r"\d+$", "", stem)
        if stripped and stripped != stem:
            candidates.append(stripped)

        resolved = root / (stripped or stem)
        for name in candidates:
            candidate = root / name
            if candidate.is_dir():
                resolved = candidate
                break

        self._slot_dir_cache[root] = resolved
        return resolved

    def get_wpk_handle(self, pkg_id: int) -> BinaryIO | None:
        if pkg_id in self.reader._wpk_cache:
            return self.reader._wpk_cache[pkg_id]

        wpk_path = self.find_wpk_path(pkg_id)
        if not os.path.exists(wpk_path):
            hint = ""
            lookalike = os.path.join(
                self.reader.base_dir, f"{self.reader.base_stem}{int(pkg_id)}.wpk"
            )
            if os.path.exists(lookalike):
                wrong_id = parse_wpk_suffix(
                    os.path.basename(lookalike), self.reader.base_stem
                )
                if wrong_id is not None and wrong_id != int(pkg_id):
                    hint = (
                        f" (not used: {os.path.basename(lookalike)} is package"
                        f" 0x{wrong_id:X} under hex naming)"
                    )
            get_logger().warning(
                "WPK not found for package 0x%X: %s%s", int(pkg_id), wpk_path, hint
            )
            self.reader._wpk_cache[pkg_id] = None
            return None

        handle = self.reader._open_wpk_mapped(wpk_path)
        self.reader._wpk_cache[pkg_id] = handle
        return handle
