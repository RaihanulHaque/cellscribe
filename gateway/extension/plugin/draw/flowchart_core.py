# Libre Thinker Agent - flowchart layout and drawing for LibreOffice Draw/Impress
# SPDX-License-Identifier: GPL-3.0-or-later
"""Draw a whole flowchart in one call, in one consistent house style.

The model describes steps (nodes) and arrows (edges); this module does the
layout (layered, top-to-bottom or left-to-right, picked to fit the page),
sizing (one font size everywhere, boxes sized to the text), colours, and
glued connectors with optional labels ("Yes"/"No").

Only LibreOffice's UNO API is used here (no WriterAgent imports), so the
module can be exercised directly against a headless LibreOffice.

Units are 1/100 mm, LibreOffice's drawing unit.
"""

from __future__ import annotations

import json
import re
from typing import Any

NAME_PREFIX = "flow:"
EDGE_PREFIX = "flow-edge:"
TITLE_NAME = "flow-title"

KINDS = ("process", "decision", "start", "end", "data", "document", "subprocess", "database")
_GEOMETRY = {
    "process": "round-rectangle",
    "decision": "flowchart-decision",
    "start": "flowchart-terminator",
    "end": "flowchart-terminator",
    "data": "flowchart-data",
    "document": "flowchart-document",
    "subprocess": "flowchart-predefined-process",
    "database": "can",
}

# House style: (fill, line, text) as 0xRRGGBB.
_STYLE = {
    "process": (0x7AA3D6, 0x3465A4, 0x14243D),
    "decision": (0xFFE699, 0xC9A227, 0x3D2E00),
    "start": (0x3465A4, 0x23497A, 0xFFFFFF),
    "end": (0x3465A4, 0x23497A, 0xFFFFFF),
    "data": (0xD5E3F5, 0x3465A4, 0x14243D),
    "document": (0xD5E3F5, 0x3465A4, 0x14243D),
    "subprocess": (0xD5E3F5, 0x3465A4, 0x14243D),
    "database": (0xD5E3F5, 0x3465A4, 0x14243D),
}
# Named highlight colours the model may use: (fill, line, text).
COLORS = {
    "blue": (0x7AA3D6, 0x3465A4, 0x14243D),
    "red": (0xC9211E, 0x8E1210, 0xFFFFFF),
    "green": (0x3FA066, 0x26704A, 0xFFFFFF),
    "orange": (0xF4A340, 0xB86E10, 0x2B1A00),
    "yellow": (0xFFE699, 0xC9A227, 0x3D2E00),
    "purple": (0x8E6CC8, 0x5E3F99, 0xFFFFFF),
    "gray": (0xC8CDD3, 0x7A828C, 0x1F2933),
    "teal": (0x3BA7A0, 0x24706B, 0xFFFFFF),
}
EDGE_COLOR = 0x3465A4
LABEL_COLOR = 0x23497A
FONT_NAME = "Liberation Sans"
FONT_PT = 12.0
LABEL_PT = 10.0
MIN_FONT_PT = 8.0

_PT_TO_HMM = 35.28  # 1 pt in 1/100 mm
_CHAR_EM = 0.60  # average bold sans character width in em
_LINE_EM = 1.25


class FlowchartError(ValueError):
    """A problem with the diagram description the model can fix."""


# ---------------------------------------------------------------- validation
def _color_triplet(value: Any) -> tuple[int, int, int] | None:
    if value is None or value == "":
        return None
    text = str(value).strip().lower()
    if text in COLORS:
        return COLORS[text]
    m = re.fullmatch(r"#?([0-9a-f]{6})", text)
    if not m:
        raise FlowchartError("Unknown color %r: use one of %s or #RRGGBB" % (value, ", ".join(COLORS)))
    fill = int(m.group(1), 16)
    r, g, b = fill >> 16, (fill >> 8) & 255, fill & 255
    line = (int(r * 0.7) << 16) | (int(g * 0.7) << 8) | int(b * 0.7)
    light = (0.299 * r + 0.587 * g + 0.114 * b) > 150
    return fill, line, (0x14243D if light else 0xFFFFFF)


def normalize(nodes: Any, edges: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Check and clean the model's description; raise FlowchartError with a fixable message."""
    if not isinstance(nodes, list) or not nodes:
        raise FlowchartError("nodes must be a non-empty list of {id, text, kind}.")
    if len(nodes) > 40:
        raise FlowchartError("At most 40 nodes fit a readable page; split the process into several diagrams.")
    out_nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, n in enumerate(nodes):
        if not isinstance(n, dict):
            raise FlowchartError("Node %d is not an object." % i)
        nid = str(n.get("id") or "").strip() or "n%d" % (i + 1)
        if nid in seen:
            raise FlowchartError("Duplicate node id %r." % nid)
        seen.add(nid)
        kind = str(n.get("kind") or "process").strip().lower()
        kind = {"terminator": "start", "begin": "start", "stop": "end", "finish": "end", "if": "decision",
                "condition": "decision", "question": "decision", "step": "process", "task": "process",
                "action": "process", "input": "data", "output": "data", "io": "data", "doc": "document",
                "db": "database", "storage": "database", "sub": "subprocess"}.get(kind, kind)
        if kind not in KINDS:
            kind = "process"
        text = str(n.get("text") or n.get("label") or nid).strip()
        out_nodes.append({"id": nid, "text": text, "kind": kind, "color": n.get("color") or None})
        _color_triplet(out_nodes[-1]["color"])  # validate early
    out_edges: list[dict[str, Any]] = []
    for j, e in enumerate(edges or []):
        if not isinstance(e, dict):
            raise FlowchartError("Edge %d is not an object." % j)
        a, b = str(e.get("from") or "").strip(), str(e.get("to") or "").strip()
        if a not in seen or b not in seen:
            raise FlowchartError("Edge %d refers to unknown node(s): %r -> %r. Known ids: %s" % (j, a, b, ", ".join(sorted(seen))))
        if a == b:
            continue
        out_edges.append({"from": a, "to": b, "label": str(e.get("label") or "").strip()})
    return out_nodes, out_edges


# ---------------------------------------------------------------- sizing
def _wrap(text: str, max_chars: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            while len(word) > max_chars:  # very long word: hard split
                if cur:
                    lines.append(cur)
                    cur = ""
                lines.append(word[:max_chars])
                word = word[max_chars:]
            if not cur:
                cur = word
            elif len(cur) + 1 + len(word) <= max_chars:
                cur += " " + word
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return [line for line in lines if line] or [""]


def _sizes(nodes: list[dict[str, Any]], font_pt: float) -> dict[str, tuple[int, int]]:
    """Box sizes; nodes of the same family share one size so the chart looks uniform."""
    char_w = font_pt * _PT_TO_HMM * _CHAR_EM
    line_h = font_pt * _PT_TO_HMM * _LINE_EM
    fams = {"decision": "decision"}
    need: dict[str, list[tuple[float, int]]] = {}
    for n in nodes:
        fam = fams.get(n["kind"], "box")
        max_chars = 14 if fam == "decision" else 22
        lines = _wrap(n["text"], max_chars)
        need.setdefault(fam, []).append((max(len(x) for x in lines) * char_w, len(lines)))
    out_fam: dict[str, tuple[int, int]] = {}
    for fam, items in need.items():
        text_w = max(w for w, _ in items)
        n_lines = max(c for _, c in items)
        if fam == "decision":  # text sits in the middle half of a diamond
            w = max(text_w * 2.2 + 900, 4800 * font_pt / FONT_PT)
            h = max(n_lines * line_h * 2.0 + 900, 2600 * font_pt / FONT_PT)
        else:
            w = max(text_w + 1200, 4200 * font_pt / FONT_PT)
            h = max(n_lines * line_h + 900, 1500 * font_pt / FONT_PT)
        out_fam[fam] = (int(w), int(h))
    return {n["id"]: out_fam[fams.get(n["kind"], "box")] for n in nodes}


# ---------------------------------------------------------------- layout
def _ranks(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> tuple[dict[str, int], set[int]]:
    """Longest-path layering after removing cycle edges. Returns ranks and the indices of back edges."""
    ids = [n["id"] for n in nodes]
    out: dict[str, list[tuple[str, int]]] = {i: [] for i in ids}
    indeg: dict[str, int] = {i: 0 for i in ids}
    for k, e in enumerate(edges):
        out[e["from"]].append((e["to"], k))
        indeg[e["to"]] += 1
    back: set[int] = set()
    state: dict[str, int] = {}

    def dfs(start: str) -> None:  # iterative DFS; an edge into the current path is a back edge
        stack = [(start, iter(out[start]))]
        state[start] = 1
        while stack:
            node, it = stack[-1]
            for nxt, k in it:
                if state.get(nxt) == 1:
                    back.add(k)
                elif nxt not in state:
                    state[nxt] = 1
                    stack.append((nxt, iter(out[nxt])))
                    break
            else:
                state[node] = 2
                stack.pop()

    for i in [i for i in ids if indeg[i] == 0] + ids:  # sources first, in the model's order
        if i not in state:
            dfs(i)
    rank = {i: 0 for i in ids}
    order = _topo(ids, [(e["from"], e["to"]) for k, e in enumerate(edges) if k not in back])
    for u in order:
        for v, k in out[u]:
            if k not in back and rank[v] < rank[u] + 1:
                rank[v] = rank[u] + 1
    return rank, back


def _topo(ids: list[str], pairs: list[tuple[str, str]]) -> list[str]:
    indeg = {i: 0 for i in ids}
    nxt: dict[str, list[str]] = {i: [] for i in ids}
    for a, b in pairs:
        nxt[a].append(b)
        indeg[b] += 1
    queue = [i for i in ids if indeg[i] == 0]
    result: list[str] = []
    while queue:
        u = queue.pop(0)
        result.append(u)
        for v in nxt[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    return result + [i for i in ids if i not in result]


def _layout(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], vertical: bool, font_pt: float) -> dict[str, Any]:
    """Positions (top-left, 1/100 mm, origin 0,0) for one direction."""
    sizes = _sizes(nodes, font_pt)
    rank, back = _ranks(nodes, edges)
    scale = font_pt / FONT_PT
    gap_rank = int((1700 if vertical else 1900) * scale)
    gap_side = int((1400 if vertical else 1100) * scale)
    n_ranks = max(rank.values()) + 1
    layers: list[list[str]] = [[] for _ in range(n_ranks)]
    for n in nodes:
        layers[rank[n["id"]]].append(n["id"])
    parents: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    children: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    for k, e in enumerate(edges):
        if k not in back:
            parents[e["to"]].append(e["from"])
            children[e["from"]].append(e["to"])
    # Order inside layers: barycenter sweeps (fewer crossings).
    pos = {i: float(j) for layer in layers for j, i in enumerate(layer)}
    for sweep in range(6):
        rng = range(1, n_ranks) if sweep % 2 == 0 else range(n_ranks - 2, -1, -1)
        for r in rng:
            nb = parents if sweep % 2 == 0 else children
            def key(i: str) -> float:
                ref = [pos[p] for p in nb[i]]
                return sum(ref) / len(ref) if ref else pos[i]
            layers[r].sort(key=key)
            for j, i in enumerate(layers[r]):
                pos[i] = float(j)

    def across(i: str) -> int:  # size across the flow
        return sizes[i][0] if vertical else sizes[i][1]

    def along(i: str) -> int:  # size along the flow
        return sizes[i][1] if vertical else sizes[i][0]

    # Coordinate across the flow: centre each node under its parents, then
    # push apart to remove overlaps (a few passes in both directions).
    c: dict[str, float] = {}
    for layer in layers:
        x = 0.0
        for i in layer:
            c[i] = x + across(i) / 2
            x += across(i) + gap_side
    for sweep in range(8):
        rng = range(1, n_ranks) if sweep % 2 == 0 else range(n_ranks - 2, -1, -1)
        nb = parents if sweep % 2 == 0 else children
        for r in rng:
            layer = layers[r]
            for i in layer:
                if nb[i]:
                    c[i] = sum(c[p] for p in nb[i]) / len(nb[i])
            # Siblings of one parent fan out around it.
            for j in range(1, len(layer)):
                a, b = layer[j - 1], layer[j]
                min_d = (across(a) + across(b)) / 2 + gap_side
                if c[b] - c[a] < min_d:
                    c[b] = c[a] + min_d
            # Keep the layer centred where its nodes wanted to be.
            if layer:
                want = [sum(c[p] for p in nb[i]) / len(nb[i]) for i in layer if nb[i]]
                if want:
                    shift = sum(want) / len(want) - (c[layer[0]] + c[layer[-1]]) / 2
                    for i in layer:
                        c[i] += shift
    lo = min(c[i] - across(i) / 2 for i in c)
    rank_size = [max(along(i) for i in layer) if layer else 0 for layer in layers]
    boxes: dict[str, tuple[int, int, int, int]] = {}
    offset = 0
    for r, layer in enumerate(layers):
        for i in layer:
            w, h = sizes[i]
            a = offset + (rank_size[r] - along(i)) / 2
            b = c[i] - lo - across(i) / 2
            x, y = (b, a) if vertical else (a, b)
            boxes[i] = (int(x), int(y), w, h)
        offset += rank_size[r] + gap_rank
    width = max(x + w for x, y, w, h in boxes.values())
    height = max(y + h for x, y, w, h in boxes.values())
    # Cycle edges route around the outside; leave room for them.
    if back or any(rank[e["to"]] > rank[e["from"]] + 1 for e in edges):
        if vertical:
            width += 1200
        else:
            height += 1200
    return {"boxes": boxes, "width": width, "height": height, "rank": rank, "back": back, "vertical": vertical}


def plan(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], area_w: int, area_h: int, direction: str = "auto") -> dict[str, Any]:
    """Pick direction and font size so the diagram fits area_w x area_h."""
    options = {"top_to_bottom": [True], "left_to_right": [False]}.get(direction, [True, False])
    best: dict[str, Any] | None = None
    for vertical in options:
        lay = _layout(nodes, edges, vertical, FONT_PT)
        fit = min(1.0, area_w / lay["width"], area_h / lay["height"])
        if best is None or fit > best["fit"] + 0.02:
            best = dict(lay, fit=fit)
    assert best is not None
    if best["fit"] < 1.0:
        font = max(MIN_FONT_PT, round(FONT_PT * best["fit"] * 2) / 2)
        while True:
            lay = _layout(nodes, edges, best["vertical"], font)
            if (lay["width"] <= area_w and lay["height"] <= area_h) or font <= MIN_FONT_PT:
                break
            font -= 0.5
        best = dict(lay, fit=1.0 if font > MIN_FONT_PT else min(area_w / lay["width"], area_h / lay["height"]))
        best["font_pt"] = font
    else:
        best["font_pt"] = FONT_PT
    return best


# Default glue points of every shape: 0 top, 1 right, 2 bottom, 3 left.
TOP, RIGHT, BOTTOM, LEFT = 0, 1, 2, 3


def _glue(e: dict[str, Any], k: int, lay: dict[str, Any], kinds: dict[str, str]) -> tuple[int, int]:
    boxes, rank, vertical = lay["boxes"], lay["rank"], lay["vertical"]
    a, b = e["from"], e["to"]
    ax, ay, aw, ah = boxes[a]
    bx, by, bw, bh = boxes[b]
    acx, acy, bcx, bcy = ax + aw / 2, ay + ah / 2, bx + bw / 2, by + bh / 2
    mid = lay["width"] / 2 if vertical else lay["height"] / 2
    if k in lay["back"] or rank[b] <= rank[a]:
        if vertical:
            side = LEFT if acx < mid and bcx < mid else RIGHT
        else:
            side = TOP if acy < mid and bcy < mid else BOTTOM
        return side, side
    skip = rank[b] > rank[a] + 1
    if vertical:
        d = bcx - acx
        if skip:  # around the rows in between, on the side the target lies (outside when aligned)
            side = (RIGHT if d > 0 else LEFT) if abs(d) >= min(aw, bw) / 4 else (RIGHT if acx >= mid else LEFT)
            return side, side
        if abs(d) < min(aw, bw) / 4:
            return BOTTOM, TOP
        if kinds[a] == "decision":
            return (RIGHT if d > 0 else LEFT), TOP
        return BOTTOM, TOP
    d = bcy - acy
    if skip:
        side = (BOTTOM if d > 0 else TOP) if abs(d) >= min(ah, bh) / 4 else (BOTTOM if acy >= mid else TOP)
        return side, side
    if abs(d) < min(ah, bh) / 4:
        return RIGHT, LEFT
    if kinds[a] == "decision":
        return (BOTTOM if d > 0 else TOP), LEFT
    return RIGHT, LEFT


# ---------------------------------------------------------------- drawing
def _struct(name: str, **fields: Any) -> Any:
    import uno

    obj = uno.createUnoStruct(name)
    for k, v in fields.items():
        setattr(obj, k, v)
    return obj


def _set(shape: Any, name: str, value: Any) -> bool:
    try:
        shape.setPropertyValue(name, value)
        return True
    except Exception:
        return False


def _custom_geometry(shape: Any, geo_type: str) -> None:
    import uno
    from com.sun.star.beans import PropertyValue

    prop = PropertyValue()
    prop.Name = "Type"
    prop.Value = geo_type
    _set(shape, "CustomShapeEngine", "com.sun.star.drawing.EnhancedCustomShapeEngine")
    seq = uno.Any("[]com.sun.star.beans.PropertyValue", (prop,))
    uno.invoke(shape, "setPropertyValue", ("CustomShapeGeometry", seq))


def _style_text(shape: Any, color: int, pt: float, bold: bool = True) -> None:
    from com.sun.star.awt.FontWeight import BOLD, NORMAL

    _set(shape, "CharFontName", FONT_NAME)
    _set(shape, "CharHeight", float(pt))
    _set(shape, "CharWeight", BOLD if bold else NORMAL)
    _set(shape, "CharColor", color)


def _style_node(shape: Any, kind: str, color: Any, pt: float) -> None:
    from com.sun.star.drawing.FillStyle import SOLID
    from com.sun.star.drawing.LineStyle import SOLID as LINE_SOLID

    fill, line, text = _color_triplet(color) or _STYLE[kind]
    _set(shape, "FillStyle", SOLID)
    _set(shape, "FillColor", fill)
    _set(shape, "LineStyle", LINE_SOLID)
    _set(shape, "LineColor", line)
    _set(shape, "LineWidth", 35)
    _set(shape, "Shadow", False)
    _style_text(shape, text, pt)


def _text_layout(shape: Any) -> None:
    from com.sun.star.drawing.TextFitToSizeType import NONE
    from com.sun.star.drawing.TextHorizontalAdjust import CENTER as H_CENTER
    from com.sun.star.drawing.TextVerticalAdjust import CENTER as V_CENTER
    from com.sun.star.style.ParagraphAdjust import CENTER as P_CENTER

    _set(shape, "TextAutoGrowHeight", False)
    _set(shape, "TextAutoGrowWidth", False)
    _set(shape, "TextFitToSize", NONE)  # one font size everywhere: never shrink text per box
    _set(shape, "TextWordWrap", True)
    _set(shape, "TextHorizontalAdjust", H_CENTER)
    _set(shape, "TextVerticalAdjust", V_CENTER)
    _set(shape, "ParaAdjust", P_CENTER)
    for side in ("TextLeftDistance", "TextRightDistance"):
        _set(shape, side, 150)


def remove_existing(page: Any) -> int:
    """Remove a flowchart this module drew earlier on the page. Other shapes stay."""
    victims = []
    for i in range(page.getCount()):
        shape = page.getByIndex(i)
        name = getattr(shape, "Name", "") or ""
        if name.startswith((NAME_PREFIX, EDGE_PREFIX)) or name == TITLE_NAME:
            victims.append(shape)
    for shape in victims:
        page.remove(shape)
    return len(victims)


def read_spec(page: Any) -> dict[str, Any] | None:
    """The description stored with a flowchart drawn by this module, if any."""
    for i in range(page.getCount()):
        shape = page.getByIndex(i)
        if (getattr(shape, "Name", "") or "").startswith(NAME_PREFIX):
            try:
                spec = json.loads(shape.Description or "")
            except Exception:
                continue
            if isinstance(spec, dict) and "nodes" in spec:
                # Colours changed later with update_node live on the shapes.
                current = {}
                for j in range(page.getCount()):
                    s = page.getByIndex(j)
                    nm = getattr(s, "Name", "") or ""
                    if nm.startswith(NAME_PREFIX):
                        current[nm[len(NAME_PREFIX):]] = s
                for n in spec["nodes"]:
                    s = current.get(n["id"])
                    if s is not None:
                        n["text"] = s.getString()
                spec["nodes"] = [n for n in spec["nodes"] if n["id"] in current]
                return spec
    return None


def draw(doc: Any, page: Any, nodes: Any, edges: Any, *, direction: str = "auto", title: str = "") -> dict[str, Any]:
    """Draw the flowchart on *page*, replacing one drawn earlier. Returns a summary."""
    nodes, edges = normalize(nodes, edges)
    kinds = {n["id"]: n["kind"] for n in nodes}
    pw, ph = int(page.Width), int(page.Height)
    bl, br = int(getattr(page, "BorderLeft", 1000) or 1000), int(getattr(page, "BorderRight", 1000) or 1000)
    bt, bb = int(getattr(page, "BorderTop", 1000) or 1000), int(getattr(page, "BorderBottom", 1000) or 1000)
    title = (title or "").strip()
    title_h = 1600 if title else 0
    area_w, area_h = pw - bl - br - 400, ph - bt - bb - 400 - title_h
    lay = plan(nodes, edges, area_w, area_h, direction)
    font = lay["font_pt"]
    scale = lay["fit"] if lay["fit"] < 1.0 else 1.0
    ox = bl + (pw - bl - br - lay["width"] * scale) / 2
    oy = bt + title_h + (ph - bt - bb - title_h - lay["height"] * scale) / 2
    if title:
        oy = max(oy, bt + title_h)

    removed = remove_existing(page)
    spec = {"nodes": nodes, "edges": edges, "direction": "top_to_bottom" if lay["vertical"] else "left_to_right", "title": title}
    spec_json = json.dumps(spec, ensure_ascii=False)
    undo = None
    try:
        undo = doc.getUndoManager()
        undo.enterUndoContext("Draw flowchart")
    except Exception:
        undo = None
    shapes: dict[str, Any] = {}
    try:
        try:
            doc.lockControllers()
        except Exception:
            pass
        if title:
            t = doc.createInstance("com.sun.star.drawing.TextShape")
            t.setPosition(_struct("com.sun.star.awt.Point", X=int(bl), Y=int(oy - title_h)))
            t.setSize(_struct("com.sun.star.awt.Size", Width=int(pw - bl - br), Height=1200))
            page.add(t)
            t.setString(title)
            t.Name = TITLE_NAME
            _set(t, "TextAutoGrowHeight", False)
            from com.sun.star.drawing.TextHorizontalAdjust import CENTER as H_CENTER
            _set(t, "TextHorizontalAdjust", H_CENTER)
            from com.sun.star.style.ParagraphAdjust import CENTER as P_CENTER
            _set(t, "ParaAdjust", P_CENTER)
            _style_text(t, 0x14243D, max(14.0, font + 6))
        for n in nodes:
            x, y, w, h = lay["boxes"][n["id"]]
            s = doc.createInstance("com.sun.star.drawing.CustomShape")
            s.setPosition(_struct("com.sun.star.awt.Point", X=int(ox + x * scale), Y=int(oy + y * scale)))
            s.setSize(_struct("com.sun.star.awt.Size", Width=int(w * scale), Height=int(h * scale)))
            _custom_geometry(s, _GEOMETRY[n["kind"]])  # before add: some LibreOffice versions abort otherwise
            page.add(s)
            _custom_geometry(s, _GEOMETRY[n["kind"]])
            s.Name = NAME_PREFIX + n["id"]
            _text_layout(s)  # before the text: LibreOffice grows custom shapes to fit text otherwise
            _style_node(s, n["kind"], n["color"], font)
            s.setString(n["text"])
            _text_layout(s)  # again: paragraph centring applies to existing text only
            _style_node(s, n["kind"], n["color"], font)
            s.setSize(_struct("com.sun.star.awt.Size", Width=int(w * scale), Height=int(h * scale)))
            s.setPosition(_struct("com.sun.star.awt.Point", X=int(ox + x * scale), Y=int(oy + y * scale)))
            _set(s, "Description", spec_json)
            shapes[n["id"]] = s
        from com.sun.star.drawing.ConnectorType import STANDARD
        from com.sun.star.drawing.LineStyle import SOLID as LINE_SOLID

        for k, e in enumerate(edges):
            g_from, g_to = _glue(e, k, lay, kinds)
            c = doc.createInstance("com.sun.star.drawing.ConnectorShape")
            page.add(c)
            c.Name = "%s%s->%s" % (EDGE_PREFIX, e["from"], e["to"])
            _set(c, "EdgeKind", STANDARD)
            c.StartShape = shapes[e["from"]]
            c.StartGluePointIndex = g_from
            c.EndShape = shapes[e["to"]]
            c.EndGluePointIndex = g_to
            _set(c, "LineStyle", LINE_SOLID)
            _set(c, "LineColor", EDGE_COLOR)
            _set(c, "LineWidth", 50)
            _set(c, "LineEndName", "Arrow")
            _set(c, "LineEndWidth", int(350 * max(0.7, font / FONT_PT)))
            _set(c, "LineEndCenter", True)
            if e["label"]:
                c.setString(e["label"])
                _style_text(c, LABEL_COLOR, max(MIN_FONT_PT, LABEL_PT * font / FONT_PT))
    finally:
        try:
            doc.unlockControllers()
        except Exception:
            pass
        if undo is not None:
            try:
                undo.leaveUndoContext()
            except Exception:
                pass
    return {
        "status": "ok",
        "message": "Flowchart drawn: %d steps, %d arrows (%s, %gpt text)%s." % (
            len(nodes), len(edges), spec["direction"].replace("_", " "), font,
            "; replaced the previous flowchart" if removed else ""),
        "node_shape_names": {n["id"]: NAME_PREFIX + n["id"] for n in nodes},
        "direction": spec["direction"],
        "font_pt": font,
    }


def update_node(page: Any, node_id: str, *, text: str | None = None, color: Any = None, kind: str | None = None) -> dict[str, Any]:
    """Change one step in place (text, colour, or shape kind), keeping the house style."""
    target = None
    for i in range(page.getCount()):
        s = page.getByIndex(i)
        if (getattr(s, "Name", "") or "") == NAME_PREFIX + node_id:
            target = s
            break
    if target is None:
        spec = read_spec(page)
        known = ", ".join(n["id"] for n in spec["nodes"]) if spec else "none (no flowchart on this page)"
        raise FlowchartError("No step with id %r on this page. Step ids: %s" % (node_id, known))
    spec = {}
    try:
        spec = json.loads(target.Description or "{}")
    except Exception:
        pass
    node = next((n for n in spec.get("nodes", []) if n.get("id") == node_id), {"kind": "process"})
    new_kind = (kind or node.get("kind") or "process").lower()
    if new_kind not in KINDS:
        raise FlowchartError("kind must be one of: %s" % ", ".join(KINDS))
    pt = float(getattr(target, "CharHeight", FONT_PT) or FONT_PT)
    if kind:
        _custom_geometry(target, _GEOMETRY[new_kind])
    if text is not None:
        size, pos = target.getSize(), target.getPosition()
        _text_layout(target)
        target.setString(text)
        _text_layout(target)
        target.setSize(size)
        target.setPosition(pos)
    if color is not None or kind or text is not None:
        _style_node(target, new_kind, color if color is not None else node.get("color"), pt)
    # Keep the stored description in step with the page.
    node.update({"kind": new_kind})
    if color is not None:
        node["color"] = color
    if text is not None:
        node["text"] = text
    payload = json.dumps(spec, ensure_ascii=False) if spec else None
    if payload:
        for i in range(page.getCount()):
            s = page.getByIndex(i)
            if (getattr(s, "Name", "") or "").startswith(NAME_PREFIX):
                _set(s, "Description", payload)
    return {"status": "ok", "message": "Step %r updated; nothing else changed." % node_id}
