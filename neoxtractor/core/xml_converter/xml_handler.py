import xml.etree.ElementTree as ET

from neoxtractor.core.xml_converter import convert_handler


def ExportXML(element_tags: list, attribute_map: list) -> str:
    roots = convert_handler.tagWrapper(element_tags, attribute_map)

    output = ""

    for root in roots:
        ET.indent(root, space="    ")
        output += f"{ET.tostring(root, encoding='unicode')}\n"

    return output
