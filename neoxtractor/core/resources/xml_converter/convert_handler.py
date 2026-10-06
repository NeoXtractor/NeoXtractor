import xml.etree.ElementTree as ET
from collections import deque


# Multiple roots support
def tagWrapper(element_tags: list[tuple[str, int]], attribute_map: list):
    roots = []  # multiple roots support
    queue = deque()
    index = 0

    while index < len(element_tags):
        tag, child_number = element_tags[index]
        attributes = attribute_map[index]
        element_tag = ET.Element(tag, attributes)

        if not queue:  # no parent → new root
            roots.append(element_tag)
        else:
            while queue and queue[0][1] == 0:
                queue.popleft()
            parent, remain = queue[0]
            parent.append(element_tag)
            queue[0] = (parent, remain - 1)

        if child_number:
            queue.append((element_tag, child_number))

        index += 1

    return roots  # <-- now returns a list of roots
