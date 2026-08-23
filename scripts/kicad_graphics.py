#!/usr/bin/env python3
"""Fail-closed semantic serialization for KiCad board/footprint graphics.

The module deliberately avoids importing ``pcbnew`` so pure tests can exercise
the dispatch.  Callers provide the coordinate converter appropriate to their
identity (exact or explicitly quantized).
"""

from __future__ import annotations

import json
from pathlib import Path


class GraphicError(ValueError):
    """A graphic cannot be serialized completely enough to guard geometry."""


_SHAPES = {
    0: "segment",
    1: "rectangle",
    2: "arc",
    3: "circle",
    4: "polygon",
    5: "bezier",
}

_DIRECT_SHAPE_NODES = {
    "gr_line", "gr_rect", "gr_arc", "gr_circle", "gr_poly", "gr_curve",
}
_FOOTPRINT_SHAPE_NODES = {
    "fp_line", "fp_rect", "fp_arc", "fp_circle", "fp_poly", "fp_curve",
}


def _call(item, name):
    if not hasattr(item, name):
        raise GraphicError("%s has no %s" % (type(item).__name__, name))
    try:
        return getattr(item, name)()
    except Exception as exc:
        raise GraphicError(
            "%s.%s failed: %s" % (type(item).__name__, name, exc)
        ) from exc


def _sexpr_tokens(text):
    tokens = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
        elif char in "()":
            tokens.append(char)
            index += 1
        elif char == '"':
            start = index
            index += 1
            escaped = False
            while index < len(text):
                current = text[index]
                index += 1
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    break
            else:
                raise GraphicError("unterminated string in KiCad board")
            tokens.append(text[start:index])
        else:
            start = index
            while (index < len(text) and not text[index].isspace()
                   and text[index] not in "()"):
                index += 1
            tokens.append(text[start:index])
    return tokens


def _parse_sexpr(text):
    root = []
    stack = []
    current = root
    for token in _sexpr_tokens(text):
        if token == "(":
            child = []
            current.append(child)
            stack.append(current)
            current = child
        elif token == ")":
            if not stack:
                raise GraphicError("unbalanced ')' in KiCad board")
            current = stack.pop()
        else:
            current.append(token)
    if stack:
        raise GraphicError("unbalanced '(' in KiCad board")
    if len(root) != 1 or not isinstance(root[0], list):
        raise GraphicError("KiCad board has no single root expression")
    return root[0]


def _children(node, head):
    return [
        child for child in node[1:]
        if isinstance(child, list) and child and child[0] == head
    ]


def _atom(node, head, where):
    matches = _children(node, head)
    if len(matches) != 1 or len(matches[0]) != 2:
        raise GraphicError("%s has no exact %s atom" % (where, head))
    value = matches[0][1]
    if not isinstance(value, str):
        raise GraphicError("%s %s atom is invalid" % (where, head))
    if value.startswith('"'):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise GraphicError("%s %s string is invalid" % (where, head)) from exc
    return str(value)


def _shape_persistence(node, where):
    uuid = _atom(node, "uuid", where)
    strokes = _children(node, "stroke")
    if len(strokes) != 1:
        raise GraphicError("%s %s has no exact stroke" % (where, uuid))
    return uuid, {"stroke_type": _atom(strokes[0], "type", where + " stroke")}


def graphic_persistence(path):
    """Bind saved stroke semantics to PCB_SHAPE UUIDs without SWIG pointers."""
    path = Path(path)
    try:
        root = _parse_sexpr(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise GraphicError("cannot read KiCad board %s: %s" % (path, exc)) from exc
    if not root or root[0] != "kicad_pcb":
        raise GraphicError("%s is not a KiCad PCB S-expression" % path)
    result = {}

    def add(node, where):
        uuid, value = _shape_persistence(node, where)
        if uuid in result:
            raise GraphicError("duplicate persisted graphic UUID %s" % uuid)
        result[uuid] = value

    for index, node in enumerate(root[1:]):
        if not isinstance(node, list) or not node:
            continue
        if node[0] in _DIRECT_SHAPE_NODES:
            add(node, "direct graphic %d" % index)
        elif node[0] == "footprint":
            for child_index, child in enumerate(node[1:]):
                if (isinstance(child, list) and child
                        and child[0] in _FOOTPRINT_SHAPE_NODES):
                    add(child, "footprint graphic %d:%d" % (index, child_index))
    return result


def _point(item, name, convert):
    try:
        return convert(_call(item, name))
    except Exception as exc:
        if isinstance(exc, GraphicError):
            raise
        raise GraphicError(
            "%s.%s returned an invalid point: %s"
            % (type(item).__name__, name, exc)
        ) from exc


def _canonical_path(points, *, closed):
    values = [tuple(int(v) for v in point) for point in points]
    if closed and len(values) > 1 and values[0] == values[-1]:
        values.pop()
    if not values:
        raise GraphicError("polygon contour has no points")
    if closed:
        variants = []
        for direction in (values, list(reversed(values))):
            variants.extend(
                tuple(direction[i:] + direction[:i])
                for i in range(len(direction))
            )
        values = list(min(variants))
    return {
        "closed": bool(closed),
        "points_nm": [list(point) for point in values],
    }


def _chain(chain, convert):
    count = int(_call(chain, "PointCount"))
    points = [convert(chain.CPoint(i)) for i in range(count)]
    return _canonical_path(points, closed=bool(_call(chain, "IsClosed")))


def _polygon(item, convert):
    poly = _call(item, "GetPolyShape")
    outlines = []
    count = int(_call(poly, "OutlineCount"))
    for outline_index in range(count):
        outline = _chain(poly.COutline(outline_index), convert)
        holes = [
            _chain(poly.CHole(outline_index, hole_index), convert)
            for hole_index in range(int(poly.HoleCount(outline_index)))
        ]
        holes.sort(key=lambda value: repr(value))
        outlines.append({"outline": outline, "holes": holes})
    if not outlines:
        points = list(_call(item, "GetPolyPoints"))
        if not points:
            raise GraphicError("polygon graphic has no outline")
        outlines.append({
            "outline": _canonical_path(
                [convert(point) for point in points], closed=True
            ),
            "holes": [],
        })
    outlines.sort(key=lambda value: repr(value))
    return outlines


def shape_geometry(item, convert, persistence):
    """Return complete, shape-dispatched geometry for a KiCad PCB_SHAPE."""
    if not isinstance(persistence, dict) or set(persistence) != {"stroke_type"}:
        raise GraphicError("graphic has no exact persisted stroke binding")
    if not isinstance(persistence["stroke_type"], str) or not persistence["stroke_type"]:
        raise GraphicError("graphic persisted stroke type is invalid")
    shape = int(_call(item, "GetShape"))
    kind = _SHAPES.get(shape)
    if kind is None:
        raise GraphicError("unsupported graphic shape %r" % shape)
    data = {
        "shape": shape,
        "shape_kind": kind,
        "width_nm": int(_call(item, "GetWidth")),
        "stroke_type": persistence["stroke_type"],
        "fill_mode": int(_call(item, "GetFillMode")),
        "hatch_line_width_nm": int(_call(item, "GetHatchLineWidth")),
        "hatch_line_spacing_nm": int(_call(item, "GetHatchLineSpacing")),
    }
    if kind == "segment":
        ends = sorted((_point(item, "GetStart", convert),
                       _point(item, "GetEnd", convert)))
        data.update({"start_nm": ends[0], "end_nm": ends[1]})
    elif kind == "rectangle":
        corners = [convert(point) for point in _call(item, "GetRectCorners")]
        data.update({
            "corners": _canonical_path(corners, closed=True),
            "corner_radius_nm": int(_call(item, "GetCornerRadius")),
        })
    elif kind == "arc":
        data.update({
            "start_nm": _point(item, "GetStart", convert),
            "mid_nm": _point(item, "GetArcMid", convert),
            "end_nm": _point(item, "GetEnd", convert),
            "center_nm": _point(item, "GetCenter", convert),
            "radius_nm": int(_call(item, "GetRadius")),
            "angle_deg": round(float(_call(item, "GetArcAngle").AsDegrees()), 9),
            "clockwise": bool(_call(item, "IsClockwiseArc")),
        })
    elif kind == "circle":
        data.update({
            "center_nm": _point(item, "GetCenter", convert),
            "radius_nm": int(_call(item, "GetRadius")),
        })
    elif kind == "polygon":
        data["polygons"] = _polygon(item, convert)
    elif kind == "bezier":
        forward = (
            _point(item, "GetStart", convert),
            _point(item, "GetBezierC1", convert),
            _point(item, "GetBezierC2", convert),
            _point(item, "GetEnd", convert),
        )
        reverse = tuple(reversed(forward))
        start, c1, c2, end = min(forward, reverse)
        data.update({
            "start_nm": start,
            "control1_nm": c1,
            "control2_nm": c2,
            "end_nm": end,
        })
    return data


def text_geometry(item, convert):
    """Return layout-affecting text properties, including mirror/justification."""
    return {
        "text": str(_call(item, "GetText")),
        "position_nm": _point(item, "GetPosition", convert),
        "size_nm": convert(_call(item, "GetTextSize")),
        "thickness_nm": int(_call(item, "GetTextThickness")),
        "angle_deg": round(float(_call(item, "GetTextAngleDegrees")), 9),
        "horizontal_justify": int(_call(item, "GetHorizJustify")),
        "vertical_justify": int(_call(item, "GetVertJustify")),
        "mirrored": bool(_call(item, "IsMirrored")),
        "bold": bool(_call(item, "IsBold")),
        "italic": bool(_call(item, "IsItalic")),
        "knockout": bool(_call(item, "IsKnockout")),
        "font_name": str(_call(item, "GetFontName")),
        "style_name": str(_call(item, "GetTextStyleName")),
        "line_spacing": round(float(_call(item, "GetLineSpacing")), 9),
        "keep_upright": bool(_call(item, "IsKeepUpright")),
    }


def complete_graphic_geometry(item, convert, *, persistence=None):
    """Serialize every supported geometric mechanism or fail closed."""
    data = {}
    if hasattr(item, "GetShape"):
        data["shape_geometry"] = shape_geometry(item, convert, persistence)
    if hasattr(item, "GetText"):
        data["text_geometry"] = text_geometry(item, convert)
    if not data:
        raise GraphicError(
            "unsupported board graphic class %s" % type(item).__name__
        )
    return data
