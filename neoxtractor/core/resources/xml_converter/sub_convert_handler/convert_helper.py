from typing import Literal


def deduplicate_definitions(
    definitions: list[tuple[str, int, dict[str, str]]],
    *,
    _type: Literal["element", "attribute"],
) -> list:
    output = []

    if _type == "element":
        for item in definitions:
            if item[0] not in output:
                output.append(item[0])
    elif _type == "attribute":
        for item in definitions:
            for key in item[2]:
                if key not in output:
                    output.append(key)

    return output
