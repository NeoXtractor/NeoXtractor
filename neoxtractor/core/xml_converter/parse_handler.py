import io
from typing import BinaryIO

from neoxtractor.core.logger import get_logger
from neoxtractor.core.xml_converter import byte_handler
from neoxtractor.core.xml_converter.sub_parse_handler import attributeFunctions


def readUnknownLenInt(value: list[bytes]) -> int:
    bytes_value = b"".join(value)

    readFunctions = {
        1: byte_handler.readuint8,
        2: byte_handler.readuint16,
        4: byte_handler.readuint32,
        8: byte_handler.readuint64,
    }

    data_size = len(bytes_value)

    if data_size in readFunctions:
        return readFunctions[data_size](bytes_value)
    else:
        raise ValueError("Unsupported parameter amount format")


def getParameters(parameter_amount: int, file: BinaryIO) -> list:
    parameter_found = 0
    theParameterName = []
    parameter_list = []

    while parameter_found < parameter_amount:
        while theParameterName == [] or theParameterName[-1] != b"\x00":
            theParameterName.append(file.read(1))
        for n, byte in enumerate(theParameterName[:-1]):
            theParameterName[n] = byte.decode(encoding="utf-8")
        parameter_list.append("".join(theParameterName[:-1]))
        theParameterName.clear()
        parameter_found += 1

    return parameter_list


def getElementTags(element_list: list, element_amount: int, file: BinaryIO) -> list:
    element_tags = []

    for _ in range(element_amount):
        element_ID, child_count = (
            byte_handler.readLEB128(file),
            byte_handler.readLEB128(file),
        )
        element_tags.append(
            (element_list[element_ID], child_count)
        )  # {element_name : child_count}

    return element_tags


def getAttributes(element_list_len: int, attribute_list: list, file: BinaryIO):
    data_types = {
        b"\x01": attributeFunctions.stringAttribute,
        b"\x02": attributeFunctions.unsignedInteger32Attribute,
        b"\x03": attributeFunctions.stringAttribute,
        b"\x05": attributeFunctions.signedInteger32Attribute,
        b"\x06": attributeFunctions.matrixAttribute,
        b"\x08": attributeFunctions.unsignedInteger64Attribute,
    }
    collected_attributes = []
    for element_number in range(element_list_len):
        attribute_amount = file.read(1)[0]
        collected_attributes.append({})
        for attribute_number in range(attribute_amount):
            attribute_ID = file.read(1)[0]
            data_type = file.read(1)
            if data_type in data_types:
                collected_attributes[element_number][attribute_list[attribute_ID]] = (
                    data_types[data_type](file)
                )
            else:
                get_logger().error(
                    f"Unknown data type code: {data_type.hex().upper()} // Skipping.."
                )
                raise Exception("")
        if file.read(2) == b"\x01\x00":
            continue
        else:
            raise Exception(f"Unexpected tag ending flag on offset: {file.tell()}")

    return collected_attributes


def parseCustomBinFormat(data: bytes) -> tuple:

    f = io.BytesIO(data)

    if not f.read(4) == b"\xc1\x59\x41\x0d":
        raise ValueError("Invalid file format")

    f.read(8)  # uint64 - file_size

    # element_list = getParameters(getParameterAmount(f), f)
    element_def_amount = byte_handler.readLEB128(f)

    element_list = getParameters(element_def_amount, f)

    # attribute_list = getParameters(getParameterAmount(f), f)
    attribute_def_amount = byte_handler.readLEB128(f)
    attribute_list = getParameters(attribute_def_amount, f)

    # uint64 | starts from 12th index (attributeFunctionster header), so you can reach attributes if you go to 12 + {attributes_offset}th index.
    f.read(8)  # attribute_offset

    tag_amount = byte_handler.readLEB128(f)
    # tag_amount = byte_handler.readuint8(f.read(1))

    element_tags = getElementTags(element_list, tag_amount, f)

    attribute_map = getAttributes(tag_amount, attribute_list, f)

    return element_tags, attribute_map
