"""Sanitise SVG written by an LLM before it is saved or shown: allow-list of tags/attributes, no scripts or external refs."""
import re
import xml.etree.ElementTree as ET

TAGS = {"svg", "g", "path", "circle", "ellipse", "rect", "line", "polyline", "polygon", "defs", "linearGradient",
        "radialGradient", "stop", "title"}
ATTRS = {"viewBox", "d", "cx", "cy", "r", "rx", "ry", "x", "y", "x1", "y1", "x2", "y2", "width", "height", "fill",
         "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "opacity", "transform", "points", "offset",
         "stop-color", "id", "gradientUnits", "fill-opacity", "stroke-opacity", "stop-opacity"}
SAFE_VALUE = re.compile(r"^[\w\s#.,()%:\-+]*$")


def sanitize_svg(text, max_bytes=20000):
    """Return clean SVG text, or None if the input is unusable or unsafe."""
    if not text or len(text.encode()) > max_bytes:
        return None
    text = re.sub(r"```(?:svg|xml)?", "", text).strip()
    start, end = text.find("<svg"), text.rfind("</svg>")
    if start == -1 or end == -1:
        return None
    try:
        root = ET.fromstring(text[start:end + 6].replace('xmlns="http://www.w3.org/2000/svg"', ""))
    except ET.ParseError:
        return None
    for el in root.iter():
        el.tag = el.tag.split("}")[-1]
        if el.tag not in TAGS:
            return None                       # reject the whole file rather than silently repairing it
        for name in list(el.attrib):
            value = el.attrib[name]
            if name not in ATTRS or not SAFE_VALUE.match(value) or ("url(" in value and not re.search(r"url\(#[\w-]+\)", value)):
                del el.attrib[name]
        if el.text and el.tag != "title":
            el.text = None
    root.set("xmlns", "http://www.w3.org/2000/svg")
    if "viewBox" not in root.attrib:
        root.set("viewBox", "0 0 100 100")
    return ET.tostring(root, encoding="unicode")
