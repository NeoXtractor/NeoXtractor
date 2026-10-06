"""Registry for built-in and external format processors."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

from neoxtractor.core.logger import get_logger

from .base import FormatDecodeResult, FormatProcessor
from .bxml import NeoXBXMLProcessor


class FunctionFormatProcessor(FormatProcessor):
    def __init__(self, name: str, probe_fn, decode_fn, priority: int = 100):
        self.name = name
        self.priority = priority
        self._probe = probe_fn
        self._decode = decode_fn

    def probe(self, data: bytes, entry) -> bool:
        return bool(self._probe(data, entry))

    def decode(self, data: bytes, entry) -> FormatDecodeResult | None:
        result = self._decode(data, entry)
        if result is None:
            return None
        if isinstance(result, FormatDecodeResult):
            if not result.processor_name:
                result.processor_name = self.name
            return result
        if isinstance(result, dict):
            return FormatDecodeResult(
                data=result.get("data", b""),
                is_text=bool(result.get("is_text", False)),
                processor_name=result.get("processor_name", self.name),
                metadata=dict(result.get("metadata", {})),
            )
        raise TypeError(
            f"Unsupported decode result type from plugin {self.name}: {type(result)!r}"
        )


_BUILTIN_PROCESSORS: list[FormatProcessor] = [NeoXBXMLProcessor()]
_EXTERNAL_PROCESSORS: list[FormatProcessor] | None = None


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _candidate_plugin_dirs() -> list[Path]:
    project_root = _project_root()
    dirs = [project_root / "plugins" / "format_processors"]
    env_dir = os.environ.get("NEOXTRACTOR_PLUGIN_DIR")
    if env_dir:
        dirs.insert(0, Path(env_dir))
    exe_dir = Path(sys.argv[0]).resolve().parent / "plugins" / "format_processors"
    if exe_dir not in dirs:
        dirs.append(exe_dir)
    unique_dirs: list[Path] = []
    seen: set[str] = set()
    for directory in dirs:
        key = str(directory)
        if key in seen:
            continue
        seen.add(key)
        unique_dirs.append(directory)
    return unique_dirs


def _load_module_from_path(path: Path) -> ModuleType | None:
    module_name = f"neoxtractor_plugin_{path.stem}_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _module_to_processor(
    module: ModuleType, default_name: str
) -> FormatProcessor | None:
    if hasattr(module, "PROCESSOR"):
        processor = getattr(module, "PROCESSOR")
        if isinstance(processor, FormatProcessor):
            return processor
    if hasattr(module, "get_processor"):
        processor = module.get_processor()
        if isinstance(processor, FormatProcessor):
            return processor
    if hasattr(module, "probe") and hasattr(module, "decode"):
        return FunctionFormatProcessor(
            name=getattr(module, "NAME", default_name),
            probe_fn=getattr(module, "probe"),
            decode_fn=getattr(module, "decode"),
            priority=int(getattr(module, "PRIORITY", 100)),
        )
    return None


def load_external_processors(force_reload: bool = False) -> list[FormatProcessor]:
    global _EXTERNAL_PROCESSORS
    if _EXTERNAL_PROCESSORS is not None and not force_reload:
        return _EXTERNAL_PROCESSORS

    processors: list[FormatProcessor] = []
    for directory in _candidate_plugin_dirs():
        if not directory.exists() or not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                module = _load_module_from_path(path)
                if module is None:
                    continue
                processor = _module_to_processor(module, default_name=path.stem)
                if processor is None:
                    get_logger().warning(
                        "Skipping plugin without valid processor API: %s", path
                    )
                    continue
                processors.append(processor)
                get_logger().info(
                    "Loaded external format processor: %s (%s)", processor.name, path
                )
            except Exception as exc:
                get_logger().exception(
                    "Failed to load format processor plugin %s: %s", path, exc
                )
    _EXTERNAL_PROCESSORS = sorted(processors, key=lambda p: getattr(p, "priority", 100))
    return _EXTERNAL_PROCESSORS


def get_all_processors() -> list[FormatProcessor]:
    return sorted(
        [*_BUILTIN_PROCESSORS, *load_external_processors()],
        key=lambda p: getattr(p, "priority", 100),
    )


def try_process_data(data: bytes, entry) -> FormatDecodeResult | None:
    for processor in get_all_processors():
        try:
            if not processor.probe(data, entry):
                continue
            result = processor.decode(data, entry)
            if result is None:
                continue
            if not result.processor_name:
                result.processor_name = processor.name
            return result
        except Exception as exc:
            get_logger().exception(
                "Format processor %s failed on %s: %s",
                getattr(processor, "name", processor.__class__.__name__),
                getattr(entry, "filename", "<unknown>"),
                exc,
            )
    return None


def process_entry_with_processors(entry) -> bool:
    from neoxtractor.core.packages.npk.class_types import NPKEntryDataFlags

    result = try_process_data(entry.data, entry)
    if result is None:
        return False

    original_data = entry.data
    entry.source_data = original_data
    entry.data = result.as_bytes()
    entry.processed_by = result.processor_name
    entry.has_decoded_view = True
    entry.format_metadata = dict(result.metadata)
    if result.is_text:
        entry.data_flags |= NPKEntryDataFlags.TEXT
    return True
