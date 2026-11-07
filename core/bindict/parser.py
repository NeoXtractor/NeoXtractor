#!/usr/bin/env python3
"""
Custom Binary File Parser - reimplemented by wuqu223 on 2026.4.18
Currently, Neox is only tested in Identity V and Seven Days World. Files related to the Messiah engine can also be loaded using files. The parser will support them synchronously because they are the same files.
Note that there may still be unknown types in the file, depending on whether the version is updated. If you encounter it, please ignore it, because it is not necessarily an error caused by an unknown type, but more likely because of special circumstances and positioning problems. Of course, this situation is rare, you can ignore it, you can also analyze it, this script is updated to support any effective contribution to it
"""

import struct
from typing import Any, Dict, List, Optional


class BindictParser:
    """Binary dictionary parser class"""

    def __init__(self, debug: bool = False):
        self.debug = debug
        self.strings: List[str] = []
        self.has_string_pool: bool = False
        self.jump_base: int = 0

    def _read_varint(self, data: bytes, offset: int):
        """Read variable-length integer (VLQ encoding)"""
        result = 0
        shift = 0
        while True:
            if offset >= len(data):
                return None, offset
            byte = data[offset]
            offset += 1
            result |= (byte & 0x7F) << shift
            shift += 7
            if (byte & 0x80) == 0:
                break
        return result, offset

    def _decode_zigzag(self, value: int) -> int:
        """ZigZag decoding"""
        return (value >> 1) ^ (-(value & 1))

    def _parse_any_data(self, data: bytes, offset: int):
        """Parse any data type (for jumps)"""
        result, _ = self._parse_any_data_with_size(data, offset)
        return result

    def _parse_any_data_with_size(self, data: bytes, offset: int):
        if offset >= len(data):
            return None, 0

        marker = data[offset]

        if marker == 0x06:
            return self._parse_mapnode_type1(data, offset)
        elif marker == 0x16:
            return self._parse_mapnode_type16(data, offset)
        elif marker == 0x26:
            return self._parse_mapnode_type2(data, offset)
        elif marker == 0x66:
            return self._parse_mapnode_type2(data, offset)
        elif marker == 0x36:
            return self._parse_mapnode_type3(data, offset)
        elif marker == 0x56:
            return self._parse_inline_56(data, offset)
        elif marker == 0x46:
            return self._parse_inline_46(data, offset)
        elif marker == 0x86:
            return self._parse_mapnode_type4(data, offset)
        elif marker == 0xC6:
            return self._parse_mapnode_type5(data, offset)
        elif marker == 0xD6:
            return self._parse_mapnode_type5(data, offset)
        elif marker == 0x96:
            return self._parse_mapnode_type4(data, offset)
        elif marker == 0x27:
            return self._parse_tuple(data, offset)
        elif marker == 0x07:
            return self._parse_array(data, offset)
        elif marker == 0x08:
            return self._parse_set(data, offset)
        elif marker == 0x28:
            return self._parse_set_fixed(data, offset)
        elif marker == 0x0C:
            return self._parse_key_value_container(data, offset)
        elif marker == 0x76:
            return self._parse_inline_dict(data, offset)
        else:
            hex_bytes = []
            for i in range(offset, min(offset + 8, len(data))):
                hex_bytes.append(data[i])
            return f"<unknown_marker:0x{marker:02X}>", 1

    def _parse_set(self, data: bytes, offset: int):
        """Parse set (0x08) - each element has its own type"""
        start_offset = offset
        current_offset = offset + 1

        element_count, current_offset = self._read_varint(data, current_offset)
        if element_count is None:
            return "<error: invalid element count>", 1

        if element_count == 0:
            return set(), 1

        elements = []
        for i in range(element_count):
            element_type = data[current_offset]
            current_offset += 1

            if element_type == 0x0B:
                jump_offset, current_offset = self._read_varint(data, current_offset)
                if jump_offset is None:
                    break
                jump_abs_offset = self.jump_base + jump_offset
                jump_data, _ = self._parse_any_data_with_size(data, jump_abs_offset)
                elements.append(jump_data)
            else:
                element_value, current_offset, _ = self._read_value_by_type(
                    data, current_offset, element_type
                )
                elements.append(element_value)

        bytes_used = current_offset - start_offset
        return set(elements), bytes_used

    def _parse_set_fixed(self, data: bytes, offset: int):
        """Parse set (0x28) - all elements share the same type"""
        start_offset = offset
        current_offset = offset + 1

        element_type = data[current_offset]
        current_offset += 1

        element_count, current_offset = self._read_varint(data, current_offset)
        if element_count is None:
            return "<error: invalid element count>", 1

        if element_count == 0:
            return set(), 1

        elements = []
        for i in range(element_count):
            if element_type == 0x0B:
                jump_offset, current_offset = self._read_varint(data, current_offset)
                if jump_offset is None:
                    break
                jump_abs_offset = self.jump_base + jump_offset
                jump_data, _ = self._parse_any_data_with_size(data, jump_abs_offset)
                elements.append(jump_data)
            else:
                element_value, current_offset, _ = self._read_value_by_type(
                    data, current_offset, element_type
                )
                elements.append(element_value)

        bytes_used = current_offset - start_offset
        return set(elements), bytes_used

    def _parse_inline_46(self, data: bytes, offset: int):
        """Inline 46 format - alternating keys and values"""
        start_offset = offset
        current_offset = offset + 1

        total_count, current_offset = self._read_varint(data, current_offset)
        if total_count is None:
            return "<error: invalid total count>", 1

        pair_count = total_count // 2

        result_dict = {}
        for i in range(pair_count):
            key_type = data[current_offset]
            current_offset += 1
            key_value, current_offset, _ = self._read_value_by_type(
                data, current_offset, key_type
            )

            value_type = data[current_offset]
            current_offset += 1
            value_value, current_offset, _ = self._read_value_by_type(
                data, current_offset, value_type
            )

            if isinstance(key_value, dict):
                key_value = "<dict_key>"
            result_dict[key_value] = value_value

        bytes_used = current_offset - start_offset
        return result_dict, bytes_used

    def _parse_inline_56(self, data: bytes, offset: int):
        """Inline 56 format - fixed key type dictionary"""
        start_offset = offset
        current_offset = offset + 1

        key_type = data[current_offset]
        current_offset += 1

        pair_count, current_offset = self._read_varint(data, current_offset)
        if pair_count is None:
            return {}, 1

        result_dict = {}
        for i in range(pair_count):
            key_value, current_offset, _ = self._read_value_by_type(
                data, current_offset, key_type
            )

            value_type = data[current_offset]
            current_offset += 1

            value_value, current_offset, _ = self._read_value_by_type(
                data, current_offset, value_type
            )

            if isinstance(key_value, dict):
                key_value = "<dict_key>"
            result_dict[key_value] = value_value

        bytes_used = current_offset - start_offset
        return result_dict, bytes_used

    def _parse_inline_dict(self, data: bytes, offset: int):
        """Parse inline dictionary (0x76) - for jump internals"""
        start_offset = offset
        current_offset = offset + 1

        key_type = data[current_offset]
        current_offset += 1

        value_type = data[current_offset]
        current_offset += 1

        pair_count, current_offset = self._read_varint(data, current_offset)
        if pair_count is None:
            return "<error: invalid pair count>", 1

        result_dict = {}
        for i in range(pair_count):
            key_value, current_offset, _ = self._read_value_by_type(
                data, current_offset, key_type
            )
            value_value, current_offset, _ = self._read_value_by_type(
                data, current_offset, value_type
            )
            result_dict[key_value] = value_value

        bytes_used = current_offset - start_offset
        return result_dict, bytes_used

    def _parse_key_value_container(self, data: bytes, offset: int):
        """Parse key-value container (0x0C) - multiple key-value pairs"""
        start_offset = offset
        current_offset = offset + 1

        pair_count, current_offset = self._read_varint(data, current_offset)
        if pair_count is None:
            return "<error: invalid pair count>", 1

        actual_pairs = pair_count // 2

        if actual_pairs == 0:
            return [], 1

        elements = []
        for i in range(actual_pairs):
            key_type = data[current_offset]
            current_offset += 1

            key_value, current_offset, _ = self._read_value_by_type(
                data, current_offset, key_type
            )

            value_type = data[current_offset]
            current_offset += 1

            value_value, current_offset, _ = self._read_value_by_type(
                data, current_offset, value_type
            )

            elements.append((key_value, value_value))

        if len(elements) == 1:
            result = {elements[0][0]: elements[0][1]}
        else:
            result = elements

        bytes_used = current_offset - start_offset
        return result, bytes_used

    def _parse_tuple(self, data: bytes, offset: int):
        """Parse tuple (0x27) - all elements share the same type"""
        start_offset = offset
        current_offset = offset + 1

        element_type = data[current_offset]
        current_offset += 1

        element_count, current_offset = self._read_varint(data, current_offset)
        if element_count is None:
            return "<error: invalid element count>", 1

        elements = []
        for i in range(element_count):
            if element_type == 0x0B:
                jump_offset, current_offset = self._read_varint(data, current_offset)
                if jump_offset is None:
                    break
                jump_abs_offset = self.jump_base + jump_offset
                jump_data, _ = self._parse_any_data_with_size(data, jump_abs_offset)
                elements.append(jump_data)
            else:
                element_value, current_offset, _ = self._read_value_by_type(
                    data, current_offset, element_type
                )
                elements.append(element_value)

        bytes_used = current_offset - start_offset
        return tuple(elements), bytes_used

    def _parse_array(self, data: bytes, offset: int):
        """Parse array (0x07) - each element has its own type"""
        start_offset = offset
        current_offset = offset + 1

        element_count, current_offset = self._read_varint(data, current_offset)
        if element_count is None:
            return "<error: invalid element count>", 1

        elements = []
        for i in range(element_count):
            element_type = data[current_offset]
            current_offset += 1

            if element_type == 0x0B:
                jump_offset, current_offset = self._read_varint(data, current_offset)
                if jump_offset is None:
                    break
                jump_abs_offset = self.jump_base + jump_offset
                jump_data, _ = self._parse_any_data_with_size(data, jump_abs_offset)
                elements.append(jump_data)
            else:
                element_value, current_offset, _ = self._read_value_by_type(
                    data, current_offset, element_type
                )
                elements.append(element_value)

        bytes_used = current_offset - start_offset
        return elements, bytes_used

    def _parse_mapnode_type1(self, data: bytes, offset: int):
        """Type 1: 0x06 - each key-value pair has types"""
        start_offset = offset
        current_offset = offset + 1

        pair_count, current_offset = self._read_varint(data, current_offset)
        if pair_count is None:
            return {}, 1

        result_dict = {}
        for i in range(pair_count):
            key_type = data[current_offset]
            current_offset += 1

            key_value, current_offset, _ = self._read_value_by_type(
                data, current_offset, key_type
            )

            value_type = data[current_offset]
            current_offset += 1

            value_value, current_offset, _ = self._read_value_by_type(
                data, current_offset, value_type
            )

            if isinstance(key_value, dict):
                key_value = "<dict_key>"
            result_dict[key_value] = value_value

        bytes_used = current_offset - start_offset
        return result_dict, bytes_used

    def _parse_mapnode_type16(self, data: bytes, offset: int):
        """Type 0x16: fixed key type, variable value type"""
        start_offset = offset
        current_offset = offset + 1

        key_type = data[current_offset]
        current_offset += 1

        pair_count, current_offset = self._read_varint(data, current_offset)
        if pair_count is None:
            return {}, 1

        result_dict = {}
        for i in range(pair_count):
            key_value, current_offset, _ = self._read_value_by_type(
                data, current_offset, key_type
            )

            value_type = data[current_offset]
            current_offset += 1

            value_value, current_offset, _ = self._read_value_by_type(
                data, current_offset, value_type
            )

            if isinstance(key_value, dict):
                key_value = "<dict_key>"
            result_dict[key_value] = value_value

        bytes_used = current_offset - start_offset
        return result_dict, bytes_used

    def _parse_mapnode_type2(self, data: bytes, offset: int):
        """Type 2: 0x26 - fixed value type"""
        start_offset = offset
        current_offset = offset + 1

        value_type = data[current_offset]
        current_offset += 1

        pair_count, current_offset = self._read_varint(data, current_offset)
        if pair_count is None:
            return {}, 1

        result_dict = {}
        for i in range(pair_count):
            key_type = data[current_offset]
            current_offset += 1

            key_value, current_offset, _ = self._read_value_by_type(
                data, current_offset, key_type
            )

            value_value, current_offset, _ = self._read_value_by_type(
                data, current_offset, value_type
            )

            if isinstance(key_value, dict):
                key_value = "<dict_key>"
            result_dict[key_value] = value_value

        bytes_used = current_offset - start_offset
        return result_dict, bytes_used

    def _parse_mapnode_type3(self, data: bytes, offset: int):
        """Type 3: 0x36 - fixed key type and value type"""
        start_offset = offset
        current_offset = offset + 1

        key_type = data[current_offset]
        current_offset += 1
        value_type = data[current_offset]
        current_offset += 1

        pair_count, current_offset = self._read_varint(data, current_offset)
        if pair_count is None:
            return {}, 1

        result_dict = {}
        for i in range(pair_count):
            key_value, current_offset, _ = self._read_value_by_type(
                data, current_offset, key_type
            )
            value_value, current_offset, _ = self._read_value_by_type(
                data, current_offset, value_type
            )

            if isinstance(key_value, dict):
                key_value = "<dict_key>"
            result_dict[key_value] = value_value

        bytes_used = current_offset - start_offset
        return result_dict, bytes_used

    def _parse_key_defs(self, data: bytes, offset: int):
        """Parse key definition area (for 86 and C6 formats)"""
        current_offset = offset

        key_count, current_offset = self._read_varint(data, current_offset)
        if key_count is None:
            return None, 0, 0

        bitmap_bit_size, current_offset = self._read_varint(data, current_offset)
        if bitmap_bit_size is None:
            return None, 0, 0

        key_defs = []
        for i in range(key_count):
            if self.has_string_pool:
                name_index, current_offset = self._read_varint(data, current_offset)
                if name_index is None:
                    break
                if name_index < len(self.strings):
                    key_name = self.strings[name_index]
                else:
                    key_name = f"<string_ref:{name_index}>"
                key_type = data[current_offset]
                current_offset += 1
            else:
                key_type = data[current_offset]
                current_offset += 1
                key_name, current_offset, _ = self._read_value_by_type(
                    data, current_offset, key_type
                )

            key_defs.append({"index": i, "name": key_name, "type": key_type})

        return key_defs, key_count, bitmap_bit_size

    def _parse_mapnode_type4(self, data: bytes, offset: int):
        """Type 4: 0x86 - bitmap embedded"""
        start_offset = offset
        current_offset = offset + 1

        key_index_offset, current_offset = self._read_varint(data, current_offset)
        if key_index_offset is None:
            return {}, 1

        key_index_abs_offset = self.jump_base + key_index_offset
        key_defs, key_count, bitmap_bit_size = self._parse_key_defs(
            data, key_index_abs_offset
        )

        if not key_defs:
            return {}, current_offset - start_offset

        if bitmap_bit_size == 0:
            used_key_indices = list(range(key_count))
        else:
            bitmap_byte_size = (bitmap_bit_size + 7) // 8
            bitmap = []
            for i in range(bitmap_byte_size):
                if current_offset >= len(data):
                    break
                bitmap.append(data[current_offset])
                current_offset += 1

            used_key_indices = []
            byte_idx = 0
            bit_idx = 0
            for i in range(bitmap_bit_size):
                if i < key_count:
                    if byte_idx < len(bitmap):
                        if (bitmap[byte_idx] & (1 << bit_idx)) != 0:
                            used_key_indices.append(i)
                    bit_idx += 1
                    if bit_idx >= 8:
                        byte_idx += 1
                        bit_idx = 0

            if key_count > bitmap_bit_size:
                for i in range(bitmap_bit_size, key_count):
                    used_key_indices.append(i)

        result_dict = {}
        for key_idx in used_key_indices:
            if key_idx >= len(key_defs):
                continue

            key_def = key_defs[key_idx]
            key_name = key_def["name"]
            key_type = key_def["type"]

            if current_offset >= len(data):
                break

            value_value, current_offset, _ = self._read_value_by_type(
                data, current_offset, key_type
            )
            result_dict[key_name] = value_value

        bytes_used = current_offset - start_offset
        return result_dict, bytes_used

    def _parse_mapnode_type5(self, data: bytes, offset: int):
        """Type 5: 0xC6 - bitmap jump"""
        start_offset = offset
        current_offset = offset + 1

        key_index_offset, current_offset = self._read_varint(data, current_offset)
        if key_index_offset is None:
            return {}, 1

        bitmap_offset, current_offset = self._read_varint(data, current_offset)
        if bitmap_offset is None:
            return {}, 1

        key_index_abs_offset = self.jump_base + key_index_offset
        key_defs, key_count, bitmap_bit_size = self._parse_key_defs(
            data, key_index_abs_offset
        )

        if not key_defs:
            return {}, current_offset - start_offset

        if bitmap_bit_size == 0:
            used_key_indices = list(range(key_count))
        else:
            bitmap_abs_offset = self.jump_base + bitmap_offset
            bitmap_byte_size = (bitmap_bit_size + 7) // 8
            bitmap = []
            temp_offset = bitmap_abs_offset
            for i in range(bitmap_byte_size):
                if temp_offset >= len(data):
                    break
                bitmap.append(data[temp_offset])
                temp_offset += 1

            used_key_indices = []
            byte_idx = 0
            bit_idx = 0
            for i in range(bitmap_bit_size):
                if i < key_count:
                    if byte_idx < len(bitmap):
                        if (bitmap[byte_idx] & (1 << bit_idx)) != 0:
                            used_key_indices.append(i)
                    bit_idx += 1
                    if bit_idx >= 8:
                        byte_idx += 1
                        bit_idx = 0

            if key_count > bitmap_bit_size:
                for i in range(bitmap_bit_size, key_count):
                    used_key_indices.append(i)

        result_dict = {}
        for key_idx in used_key_indices:
            if key_idx >= len(key_defs):
                continue

            key_def = key_defs[key_idx]
            key_name = key_def["name"]
            key_type = key_def["type"]

            if current_offset >= len(data):
                break

            value_value, current_offset, _ = self._read_value_by_type(
                data, current_offset, key_type
            )
            result_dict[key_name] = value_value

        bytes_used = current_offset - start_offset
        return result_dict, bytes_used

    def _read_value_by_type(self, data: bytes, offset: int, value_type: int):
        if value_type == 0x00:
            return {}, offset, []
        elif value_type == 0x04:
            return None, offset, []
        elif value_type == 0x06:
            result, bytes_used = self._parse_mapnode_type1(data, offset)
            return result, offset + bytes_used, []
        elif value_type == 0x16:
            result, bytes_used = self._parse_mapnode_type16(data, offset)
            return result, offset + bytes_used, []
        elif value_type == 0x26:
            result, bytes_used = self._parse_mapnode_type2(data, offset)
            return result, offset + bytes_used, []
        elif value_type == 0x66:
            result, bytes_used = self._parse_mapnode_type2(data, offset)
            return result, offset + bytes_used, []
        elif value_type == 0x36:
            result, bytes_used = self._parse_mapnode_type3(data, offset)
            return result, offset + bytes_used, []
        elif value_type == 0x86:
            result, bytes_used = self._parse_mapnode_type4(data, offset)
            return result, offset + bytes_used, []
        elif value_type == 0x46:
            result, bytes_used = self._parse_inline_46(data, offset)
            return result, offset + bytes_used, []
        elif value_type == 0x56:
            result, bytes_used = self._parse_inline_56(data, offset)
            return result, offset + bytes_used, []
        elif value_type == 0xC6:
            result, bytes_used = self._parse_mapnode_type5(data, offset)
            return result, offset + bytes_used, []
        elif value_type == 0xD6:
            result, bytes_used = self._parse_mapnode_type5(data, offset)
            return result, offset + bytes_used, []
        elif value_type == 0x96:
            result, bytes_used = self._parse_mapnode_type4(data, offset)
            return result, offset + bytes_used, []
        elif value_type == 0x27:
            result, bytes_used = self._parse_tuple(data, offset)
            return result, offset + bytes_used, []
        elif value_type == 0x07:
            result, bytes_used = self._parse_array(data, offset)
            return result, offset + bytes_used, []
        elif value_type == 0x08:
            result, bytes_used = self._parse_set(data, offset)
            return result, offset + bytes_used, []
        elif value_type == 0x28:
            result, bytes_used = self._parse_set_fixed(data, offset)
            return result, offset + bytes_used, []
        elif value_type == 0x0C:
            result, bytes_used = self._parse_key_value_container(data, offset)
            return result, offset + bytes_used, []
        elif value_type == 0x76:
            result, bytes_used = self._parse_inline_dict(data, offset)
            return result, offset + bytes_used, []
        elif value_type == 0x11:
            value, new_offset = self._read_varint(data, offset)
            if value is None:
                return None, offset, []
            signed_value = self._decode_zigzag(value)
            return signed_value, new_offset, []
        elif value_type == 0x12:
            if offset + 4 > len(data):
                return None, offset, []
            value = struct.unpack("<f", data[offset : offset + 4])[0]
            return value, offset + 4, []
        elif value_type == 0x22:
            if offset + 8 > len(data):
                return None, offset, []
            value = struct.unpack("<d", data[offset : offset + 8])[0]
            return value, offset + 8, []

        base_type = value_type & 0x0F

        if base_type == 0x01:
            value, new_offset = self._read_varint(data, offset)
            if value is None:
                return None, offset, []
            return value, new_offset, []
        elif base_type == 0x02:
            if offset + 4 > len(data):
                return None, offset, []
            value = struct.unpack("<f", data[offset : offset + 4])[0]
            return value, offset + 4, []
        elif base_type == 0x03:
            if offset >= len(data):
                return None, offset, []
            value = data[offset]
            return bool(value), offset + 1, []
        elif base_type == 0x05:
            if self.has_string_pool:
                str_index, new_offset = self._read_varint(data, offset)
                if str_index is None:
                    return None, offset, []
                if str_index < len(self.strings):
                    return self.strings[str_index], new_offset, []
                else:
                    return f"<string_ref:{str_index}>", new_offset, []
            else:
                str_len, new_offset = self._read_varint(data, offset)
                if str_len is None:
                    return None, offset, []
                string_bytes = data[new_offset : new_offset + str_len]
                try:
                    string_value = string_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    string_value = string_bytes.hex()
                return string_value, new_offset + str_len, []
        elif base_type == 0x0B:
            jump_offset, new_offset = self._read_varint(data, offset)
            if jump_offset is None:
                return None, offset, []
            jump_abs_offset = self.jump_base + jump_offset
            jump_data = self._parse_any_data(data, jump_abs_offset)
            return jump_data, new_offset, []
        else:
            value, new_offset = self._read_varint(data, offset)
            if value is None:
                return None, offset, []
            return f"<unknown_type_0x{value_type:02X}:{value}>", new_offset, []

    def _parse_hash_region(self, data: bytes):
        """Parse hash region"""
        if self.jump_base + 4 > len(data):
            return None

        hash_offset_rel = struct.unpack(
            "<I", data[self.jump_base : self.jump_base + 4]
        )[0]
        hash_abs_offset = self.jump_base + hash_offset_rel

        if hash_abs_offset >= len(data):
            return None

        marker = data[hash_abs_offset]
        current_offset = hash_abs_offset + 1

        if marker == 0x56:
            key_type = data[current_offset]
            current_offset += 1

            dict_count, current_offset = self._read_varint(data, current_offset)
            if dict_count is None:
                return None

            items = []
            for i in range(dict_count):
                hash_value = struct.unpack(
                    "<I", data[current_offset : current_offset + 4]
                )[0]
                offset_value = struct.unpack(
                    "<I", data[current_offset + 4 : current_offset + 8]
                )[0]
                current_offset += 8
                items.append({"hash": hash_value, "offset": offset_value})

            for item in items:
                data_abs_offset = self.jump_base + item["offset"]
                key_value, new_offset, _ = self._read_value_by_type(
                    data, data_abs_offset, key_type
                )
                value_type = data[new_offset] if new_offset < len(data) else 0
                value_value, _, _ = self._read_value_by_type(
                    data, new_offset + 1, value_type
                )
                item["data"] = {key_value: value_value}

            return {"marker": marker, "items": items}

        elif marker == 0x66:
            value_type = data[current_offset]
            current_offset += 1

            dict_count, current_offset = self._read_varint(data, current_offset)
            if dict_count is None:
                return None

            items = []
            for i in range(dict_count):
                hash_value = struct.unpack(
                    "<I", data[current_offset : current_offset + 4]
                )[0]
                offset_value = struct.unpack(
                    "<I", data[current_offset + 4 : current_offset + 8]
                )[0]
                current_offset += 8
                items.append({"hash": hash_value, "offset": offset_value})

            for item in items:
                data_abs_offset = self.jump_base + item["offset"]
                key_type = data[data_abs_offset] if data_abs_offset < len(data) else 0
                key_value, new_offset, _ = self._read_value_by_type(
                    data, data_abs_offset + 1, key_type
                )
                value_value, _, _ = self._read_value_by_type(
                    data, new_offset, value_type
                )
                item["data"] = {key_value: value_value}

            return {"marker": marker, "items": items}

        elif marker == 0x76:
            key_type = data[current_offset]
            current_offset += 1
            value_type = data[current_offset]
            current_offset += 1

            dict_count, current_offset = self._read_varint(data, current_offset)
            if dict_count is None:
                return None

            items = []
            for i in range(dict_count):
                hash_value = struct.unpack(
                    "<I", data[current_offset : current_offset + 4]
                )[0]
                offset_value = struct.unpack(
                    "<I", data[current_offset + 4 : current_offset + 8]
                )[0]
                current_offset += 8
                items.append({"hash": hash_value, "offset": offset_value})

            for item in items:
                data_abs_offset = self.jump_base + item["offset"]
                key_value, new_offset, _ = self._read_value_by_type(
                    data, data_abs_offset, key_type
                )
                value_value, _, _ = self._read_value_by_type(
                    data, new_offset, value_type
                )
                item["data"] = {key_value: value_value}

            return {"marker": marker, "items": items}

        else:
            return None

    def _parse_dictionary_data(self, dict_data: bytes):
        """Parse binary data of a single dictionary"""
        try:
            offset = 0

            if len(dict_data) < 8:
                return None

            string_count = struct.unpack("<I", dict_data[offset : offset + 4])[0]
            offset += 4

            struct.unpack("<I", dict_data[offset : offset + 4])[0]  # padding
            offset += 4

            self.has_string_pool = string_count > 0
            self.strings = []

            if self.has_string_pool:
                raw_lengths = []
                for i in range(string_count):
                    if offset + 4 > len(dict_data):
                        break
                    length_value = struct.unpack("<I", dict_data[offset : offset + 4])[
                        0
                    ]
                    raw_lengths.append(length_value)
                    offset += 4

                lengths = []
                for i in range(len(raw_lengths)):
                    if i == 0:
                        lengths.append(raw_lengths[i])
                    else:
                        lengths.append(raw_lengths[i] - raw_lengths[i - 1])

                for i, length in enumerate(lengths):
                    if offset + length > len(dict_data):
                        break
                    string_bytes = dict_data[offset : offset + length]
                    try:
                        string_text = string_bytes.decode("utf-8")
                    except UnicodeDecodeError:
                        string_text = string_bytes.hex()
                    self.strings.append(string_text)
                    offset += length

            self.jump_base = offset

            result = {}
            if self.jump_base + 4 <= len(dict_data):
                hash_info = self._parse_hash_region(dict_data)
                if hash_info:
                    for item in hash_info["items"]:
                        if "data" in item:
                            result.update(item["data"])

            if result:
                result = self._sort_dict_keys(result)

            return result

        except Exception as e:
            if self.debug:
                print(f"Parse error: {e}")
            return None

    def _sort_dict_keys(self, data: Any):
        """Sort dictionary keys recursively"""
        if not isinstance(data, dict):
            return data

        int_keys = []
        other_keys = []

        for key in data.keys():
            if isinstance(key, dict):
                key_str = "<dict_key>"
                other_keys.append(key_str)
                data[key_str] = data.pop(key)
                key = key_str

            if isinstance(key, (int, float)):
                int_keys.append(key)
            else:
                other_keys.append(key)

        int_keys.sort(key=lambda x: (x is not None, x))

        def other_key_sort_key(k):
            if isinstance(k, tuple):
                return (0, k)
            elif isinstance(k, str):
                return (1, k)
            else:
                return (2, str(k))

        other_keys.sort(key=other_key_sort_key)

        sorted_dict = {}
        for key in int_keys + other_keys:
            sorted_dict[key] = self._sort_dict_keys(data[key])

        return sorted_dict

    def is_bindict_pyc(self, data: bytes) -> bool:
        """Check if data is a pyc file containing bindict data"""
        if len(data) < 4:
            return False

        magic = data[0:4]
        valid_magic = [
            b"\xa7\x0d\x0d\x0a",
            b"\xcb\x0d\x0d\x0a",
            b"\x03\xf3\x0d\x0a",
            b"\xa8\x0d\x0d\x0a",
        ]

        if magic not in valid_magic:
            return False

        patterns = [
            b"\xe9\x00\x00\x00\x00\x4e",
            b"LangConvertDirectTN",
            b"taggeddictTN",
            b"data_define",
        ]

        for pattern in patterns:
            offset = data.find(pattern)
            if offset != -1:
                check_offset = offset + len(pattern)
                if check_offset < len(data) and data[check_offset] == 0x73:
                    return True

        return False

    def extract_from_pyc(self, pyc_data: bytes) -> Optional[Dict[str, Any]]:
        """Extract all dictionary data from pyc file"""
        dict_names = ["data", "extra"]

        if len(pyc_data) < 4:
            return None

        magic = pyc_data[0:4]
        valid_magic = [
            b"\xa7\x0d\x0d\x0a",
            b"\xcb\x0d\x0d\x0a",
            b"\x03\xf3\x0d\x0a",
            b"\xa8\x0d\x0d\x0a",
        ]

        # If not a valid pyc magic, try parsing directly
        if magic not in valid_magic:
            result = self._parse_dictionary_data(pyc_data)
            if result:
                return {"data": result}
            return None

        patterns = [
            (b"\xe9\x00\x00\x00\x00\x4e", 6),
            (b"LangConvertDirectTN", 19),
            (b"taggeddictTN", 12),
            (b"data_define", 11),
        ]

        dict_index = 0
        result_dict = {}

        for pattern, pattern_len in patterns:
            offset = 0
            while True:
                offset = pyc_data.find(pattern, offset)
                if offset == -1:
                    break

                check_offset = offset + pattern_len
                if check_offset + 1 <= len(pyc_data) and pyc_data[check_offset] == 0x73:
                    str_offset = check_offset
                    if str_offset + 5 > len(pyc_data):
                        offset += 1
                        continue

                    dict_len = struct.unpack(
                        "<I", pyc_data[str_offset + 1 : str_offset + 5]
                    )[0]
                    dict_start = str_offset + 5

                    if dict_start + dict_len <= len(pyc_data):
                        dict_data = pyc_data[dict_start : dict_start + dict_len]
                        result = self._parse_dictionary_data(dict_data)
                        if result:
                            name = (
                                dict_names[dict_index]
                                if dict_index < len(dict_names)
                                else f"dict_{dict_index}"
                            )
                            result_dict[name] = result
                            dict_index += 1

                    offset = dict_start + dict_len
                else:
                    offset += 1

        return result_dict if result_dict else None


def is_bindict_pyc(data: bytes) -> bool:
    """Check if data is a pyc file containing bindict data"""
    parser = BindictParser()
    return parser.is_bindict_pyc(data)


def parse_pyc_file(file_path: str) -> Optional[Dict[str, Any]]:
    """Parse a pyc file and extract bindict data if present"""
    parser = BindictParser()
    with open(file_path, "rb") as f:
        data = f.read()
    return parser.extract_from_pyc(data)
