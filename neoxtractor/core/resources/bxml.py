"""Resource protocol entry for NeoX BXML (binary XML).

FORMAT -> "bxml"
probe  -> exact-magic header check (file-like contract)
read   -> canonical ``str`` (pretty-printed XML text)

BXML is the on-disk binary form of NeoX resource descriptors (materials,
prefabs, animation graphs, ...).  The canonical output is XML text, which is
directly saveable (kind="file" via the canonical-bytes rule in the processors
contract).

Source / attribution: original decoder script provided by Discord user
JohnSmith, modified and integrated for this project.

Exception contract: read() returns None only when *f* is not BXML; a
BXML-magic payload that fails to decode raises for the caller to record.
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from io import BytesIO
from typing import Any, Optional
from xml.dom import minidom

from neoxtractor.core.utils.filelike import FileLike, read_all, read_head
from neoxtractor.core.utils.result import Category, ProbeResult

FORMAT = "bxml"
CANONICAL_TYPE = "str"
TARGETS: tuple = ()  # canonical XML text is already a saveable form

BXML_MAGIC = b"\xc1\x59\x41\x0d"


def _read_leb128(buf: BytesIO) -> int:
    result = 0
    shift = 0
    while True:
        byte = buf.read(1)
        if not byte:
            break
        value = byte[0]
        result |= (value & 0x7F) << shift
        if (value & 0x80) == 0:
            break
        shift += 7
    return result


def _read_null_terminated_utf8(buf: BytesIO) -> str:
    chars = bytearray()
    while True:
        b = buf.read(1)
        if b == b"\x00" or not b:
            break
        chars.extend(b)
    return chars.decode("utf-8", errors="ignore")


def _read_string_pool(buf: BytesIO) -> list[str]:
    count = _read_leb128(buf)
    return [_read_null_terminated_utf8(buf) for _ in range(count)]


def _read_bxml_value(buf: BytesIO, type_tag: int) -> str:
    if type_tag == 0:
        return ""
    if type_tag == 1:
        return _read_null_terminated_utf8(buf)
    if type_tag in (2, 4):
        data = buf.read(4)
        return "" if len(data) != 4 else str(struct.unpack("<i", data)[0])
    if type_tag == 5:
        data = buf.read(4)
        return "" if len(data) != 4 else str(round(struct.unpack("<f", data)[0], 4))
    if type_tag == 3:
        data = buf.read(1)
        return "" if len(data) != 1 else str(struct.unpack("<B", data)[0])
    if type_tag == 6:
        data = buf.read(4)
        if len(data) != 4:
            return ""
        length = struct.unpack("<I", data)[0]
        float_data = buf.read(length * 4)
        if len(float_data) != length * 4:
            return float_data.hex()
        try:
            floats = struct.unpack("<" + "f" * length, float_data)
            return "[" + ", ".join(f"{round(v, 4)}" for v in floats) + "]"
        except Exception:
            return float_data.hex()
    if type_tag in (7, 8):
        data = buf.read(8)
        return "" if len(data) != 8 else str(struct.unpack("<q", data)[0])
    return ""


def parse_bxml_bytes(data: bytes) -> str:
    """Decode BXML bytes to pretty-printed XML text."""
    buf = BytesIO(data)
    magic = buf.read(4)
    if magic != BXML_MAGIC:
        raise ValueError("Not a valid NeoX BXML payload.")

    total_size_raw = buf.read(8)
    if len(total_size_raw) != 8:
        raise ValueError("Truncated BXML header.")

    tag_names = _read_string_pool(buf)
    attr_names = _read_string_pool(buf)

    attr_data_offset_raw = buf.read(8)
    if len(attr_data_offset_raw) != 8:
        raise ValueError("Missing BXML attribute data offset.")

    node_count = _read_leb128(buf)

    # Per-node scratch record, filled in passes: tag/relation_count first,
    # then attrs/value, then child_ids.  Heterogeneous by construction, so
    # the value type is Any.
    nodes_info: list[dict[str, Any]] = []
    for i in range(node_count):
        tag_idx = _read_leb128(buf)
        relation_count = _read_leb128(buf)
        tag_name = tag_names[tag_idx] if 0 <= tag_idx < len(tag_names) else f"node_{i}"
        nodes_info.append({"tag": tag_name, "relation_count": relation_count})

    for i in range(node_count):
        attr_count = _read_leb128(buf)
        attrs: dict[str, str] = {}
        for j in range(attr_count):
            attr_idx = _read_leb128(buf)
            type_tag_raw = buf.read(1)
            if not type_tag_raw:
                raise ValueError("Unexpected end of file while reading BXML attributes.")
            type_tag = type_tag_raw[0]
            key = (
                attr_names[attr_idx]
                if 0 <= attr_idx < len(attr_names)
                else f"attr_{j}"
            )
            attrs[key] = _read_bxml_value(buf, type_tag)
        nodes_info[i]["attrs"] = attrs

        node_type_tag_raw = buf.read(1)
        if not node_type_tag_raw:
            raise ValueError("Unexpected end of file while reading BXML node value.")
        node_type_tag = node_type_tag_raw[0]
        nodes_info[i]["value"] = _read_bxml_value(buf, node_type_tag)

    elements: list[ET.Element] = []
    for info in nodes_info:
        elem = ET.Element(str(info["tag"]), info.get("attrs", {}))
        value = info.get("value")
        if value not in ("", None):
            elem.text = str(value)
        elements.append(elem)

    cursor = 1
    for i in range(node_count):
        child_count = int(nodes_info[i].get("relation_count", 0))
        if child_count < 0:
            raise ValueError(f"Negative child_count at node {i}")
        if cursor + child_count > node_count:
            raise ValueError(
                f"Invalid child_count at node {i}: {child_count}, "
                f"cursor={cursor}, node_count={node_count}"
            )
        nodes_info[i]["child_ids"] = list(range(cursor, cursor + child_count))
        cursor += child_count

    if cursor != node_count:
        raise ValueError(
            f"Tree layout mismatch: cursor={cursor}, expected={node_count}"
        )

    for i, info in enumerate(nodes_info):
        for child_id in info.get("child_ids", []):
            elements[i].append(elements[child_id])

    root = elements[0] if elements else None
    if root is None:
        raise ValueError("BXML contains no nodes.")

    xml_bytes = ET.tostring(root, encoding="utf-8")
    pretty_xml = minidom.parseString(xml_bytes).toprettyxml(indent="  ")
    lines = pretty_xml.split("\n")
    if lines and lines[0].startswith("<?xml"):
        pretty_xml = "\n".join(lines[1:]).lstrip("\n")
    return pretty_xml


# Sub-type markers live in the BXML string pool, so sniffing them needs the
# full payload: deliberate exception to the probe header-only rule, applied
# only after the BXML magic matched.
_SUBTYPE_MARKERS: tuple[tuple[bytes, str], ...] = (
    (b"Material", "mtg"),
    (b"SubMesh", "gim"),
    (b"Anim", "ags"),
)


def probe(f) -> ProbeResult:
    """100 + sub-type extension if *f* starts with the BXML magic, else 0.

    Anything BXML-shaped that cannot be classified falls back to the parent
    extension "bxml" (marker sniffing: see ``_SUBTYPE_MARKERS``).
    """
    if read_head(f, 4) != BXML_MAGIC:
        return ProbeResult(0)
    data = read_all(f)
    ext = "bxml"
    for marker, name in _SUBTYPE_MARKERS:
        if marker in data:
            ext = name
            break
    return ProbeResult(100, ext, Category.BXML)


def read(f: FileLike) -> Optional[str]:
    """Decode *f* to XML text, or None if not BXML (raises if corrupt BXML)."""
    if read_head(f, 4) != BXML_MAGIC:
        return None
    return parse_bxml_bytes(read_all(f))
