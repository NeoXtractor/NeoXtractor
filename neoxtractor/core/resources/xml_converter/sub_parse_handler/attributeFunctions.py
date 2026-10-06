from typing import BinaryIO

from neoxtractor.core.logger import get_logger
from neoxtractor.core.resources.xml_converter import byte_handler


# \x01
def stringAttribute(file: BinaryIO):
    collected_data = bytearray()

    while collected_data == b"" or collected_data[-1] != 0:
        collected_data += file.read(1)

    try:
        return collected_data[:-1].decode(encoding="utf-8")
    except Exception as e:
        get_logger().error(f"Could not decode: {collected_data} : {e}")
        raise Exception(f"Could not decode: {collected_data} : {e}")


# \x02
def unsignedInteger32Attribute(file: BinaryIO):
    return str(byte_handler.readuint32(file.read(4)))


# \x05
def signedInteger32Attribute(file: BinaryIO):
    return str(byte_handler.readint32(file.read(4)))


# \x06
def matrixAttribute(file: BinaryIO):
    matrix_size = byte_handler.readuint32(file.read(4))
    matrix = []

    for _ in range(matrix_size):
        matrix.append(f"{byte_handler.readfloat32(file.read(4)):.4f}")

    return ",".join(matrix)


# \x08
def unsignedInteger64Attribute(file: BinaryIO):
    return str(byte_handler.readuint64(file.read(8)))
