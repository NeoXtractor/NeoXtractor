"""NeoX BXML decoder."""
# Source / attribution:
# Original script provided by Discord user JohnSmith.
# Modified and integrated for this project.

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from io import BytesIO
from xml.dom import minidom

from .base import FormatDecodeResult, FormatProcessor

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
    strings: list[str] = []
    for _ in range(count):
        strings.append(_read_null_terminated_utf8(buf))
    return strings


def _read_bxml_value(buf: BytesIO, type_tag: int) -> str:
    if type_tag == 0:
        return ""
    if type_tag == 1:
        return _read_null_terminated_utf8(buf)
    if type_tag in (2, 4):
        data = buf.read(4)
        if len(data) != 4:
            return ""
        return str(struct.unpack("<i", data)[0])
    if type_tag == 5:
        data = buf.read(4)
        if len(data) != 4:
            return ""
        return str(round(struct.unpack("<f", data)[0], 4))
    if type_tag == 3:
        data = buf.read(1)
        if len(data) != 1:
            return ""
        return str(struct.unpack("<B", data)[0])
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
        if len(data) != 8:
            return ""
        return str(struct.unpack("<q", data)[0])
    return ""


def parse_bxml_bytes(data: bytes) -> str:
    buf = BytesIO(data)
    magic = buf.read(4)
    if magic != BXML_MAGIC:
        raise ValueError("Not a valid NeoX BXML payload.")

    total_size_raw = buf.read(8)
    if len(total_size_raw) != 8:
        raise ValueError("Truncated BXML header.")
    _total_size = struct.unpack("<Q", total_size_raw)[0]
    ## 校验total_size
    # if _total_size != len(data):
    #     raise ValueError(f"Header size mismatch: {_total_size} != {len(data)}")
    # payload_start = buf.tell()

    tag_names = _read_string_pool(buf)
    attr_names = _read_string_pool(buf)

    attr_data_offset_raw = buf.read(8)
    if len(attr_data_offset_raw) != 8:
        raise ValueError("Missing BXML attribute data offset.")
    attr_data_offset = struct.unpack("<Q", attr_data_offset_raw)[0]

    node_count = _read_leb128(buf)
    nodes_info: list[dict[str, object]] = []
    for i in range(node_count):
        tag_idx = _read_leb128(buf)
        relation_count = _read_leb128(buf)
        tag_name = tag_names[tag_idx] if 0 <= tag_idx < len(tag_names) else f"node_{i}"
        nodes_info.append({"tag": tag_name, "relation_count": relation_count})

    ## 校验元数据区和数据区切分点
    # meta_end = buf.tell()
    # expected_data_start = payload_start + attr_data_offset
    # if meta_end != expected_data_start:
    #     raise ValueError(
    #         f"Metadata/data split mismatch: meta_end={meta_end}, "
    #         f"expected_data_start={expected_data_start}"
    #     )
    # buf.seek(payload_start + attr_data_offset)

    for i in range(node_count):
        attr_count = _read_leb128(buf)
        attrs: dict[str, str] = {}
        for j in range(attr_count):
            attr_idx = _read_leb128(buf)
            type_tag_raw = buf.read(1)
            if not type_tag_raw:
                raise ValueError(
                    "Unexpected end of file while reading BXML attributes."
                )
            type_tag = type_tag_raw[0]
            key = (
                attr_names[attr_idx] if 0 <= attr_idx < len(attr_names) else f"attr_{j}"
            )
            attrs[key] = _read_bxml_value(buf, type_tag)
        nodes_info[i]["attrs"] = attrs

        node_type_tag_raw = buf.read(1)
        if not node_type_tag_raw:
            raise ValueError("Unexpected end of file while reading BXML node value.")
        node_type_tag = node_type_tag_raw[0]
        nodes_info[i]["value"] = _read_bxml_value(buf, node_type_tag)

    # 先创建所有节点对象
    elements: list[ET.Element] = []
    for info in nodes_info:
        elem = ET.Element(str(info["tag"]), info.get("attrs", {}))
        value = info.get("value")
        if value not in ("", None):
            elem.text = str(value)
        elements.append(elem)

    # 按“连续子节点区间”分配孩子
    # 假设节点 0 是根，后续节点按层次/顺序排列
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

    # 连通树应满足：总孩子数 = node_count - 1
    if cursor != node_count:
        raise ValueError(
            f"Tree layout mismatch: cursor={cursor}, expected={node_count}"
        )

    # 真正挂接子节点
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


class NeoXBXMLProcessor(FormatProcessor):
    name = "BXML"
    priority = 10

    def probe(self, data: bytes, entry) -> bool:
        return data[:4] == BXML_MAGIC

    def decode(self, data: bytes, entry) -> FormatDecodeResult | None:
        xml_text = parse_bxml_bytes(data)
        return FormatDecodeResult(
            data=xml_text,
            is_text=True,
            processor_name=self.name,
            metadata={"input_extension": getattr(entry, "extension", "")},
        )
