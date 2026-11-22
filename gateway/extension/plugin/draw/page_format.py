# Libre Thinker Agent - page size and orientation for Draw/Impress
# SPDX-License-Identifier: GPL-3.0-or-later
"""set_page_format: landscape/portrait and paper size, optionally fitting content.

WriterAgent had no tool for the page format, so "make page 2 landscape" ended
with the model explaining how to do it by hand. Units are 1/100 mm.
"""

from __future__ import annotations

import logging
from typing import Any

from plugin.framework.errors import is_real_disposal
from plugin.framework.tool import ToolBase, ToolContext

log = logging.getLogger(__name__)

_DRAW_UNO = ["com.sun.star.drawing.DrawingDocument", "com.sun.star.presentation.PresentationDocument"]
# Portrait width x height in 1/100 mm.
PAPER = {
    "a3": (29700, 42000),
    "a4": (21000, 29700),
    "a5": (14800, 21000),
    "letter": (21590, 27940),
    "legal": (21590, 35560),
    "tabloid": (27940, 43180),
    "16:9": (28000, 15750),
    "4:3": (28000, 21000),
}


def _struct(name: str, **fields: Any) -> Any:
    import uno

    obj = uno.createUnoStruct(name)
    for k, v in fields.items():
        setattr(obj, k, v)
    return obj


def _content_box(page: Any) -> tuple[int, int, int, int] | None:
    xs0, ys0, xs1, ys1 = [], [], [], []
    for i in range(page.getCount()):
        s = page.getByIndex(i)
        try:
            p, z = s.getPosition(), s.getSize()
        except Exception:
            continue
        xs0.append(p.X)
        ys0.append(p.Y)
        xs1.append(p.X + z.Width)
        ys1.append(p.Y + z.Height)
    if not xs0:
        return None
    return min(xs0), min(ys0), max(xs1), max(ys1)


def fit_content(doc: Any, page: Any, uno_ctx: Any = None) -> str:
    """Shrink (never enlarge) and centre everything on the page inside its margins."""
    box = _content_box(page)
    if box is None:
        return ""
    x0, y0, x1, y1 = box
    bl, br = int(getattr(page, "BorderLeft", 1000) or 0), int(getattr(page, "BorderRight", 1000) or 0)
    bt, bb = int(getattr(page, "BorderTop", 1000) or 0), int(getattr(page, "BorderBottom", 1000) or 0)
    area_w, area_h = page.Width - bl - br, page.Height - bt - bb
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    scale = min(1.0, area_w / w, area_h / h)
    if scale >= 1.0 and x0 >= bl and y0 >= bt and x1 <= page.Width - br and y1 <= page.Height - bb:
        return ""
    if uno_ctx is None:
        import uno

        uno_ctx = uno.getComponentContext()
    shapes = uno_ctx.ServiceManager.createInstanceWithContext("com.sun.star.drawing.ShapeCollection", uno_ctx)
    for i in range(page.getCount()):
        shapes.add(page.getByIndex(i))
    if shapes.getCount() == 1:
        group, single = shapes.getByIndex(0), True
    else:
        group, single = page.group(shapes), False
    nw, nh = int(w * scale), int(h * scale)
    group.setSize(_struct("com.sun.star.awt.Size", Width=nw, Height=nh))
    group.setPosition(_struct("com.sun.star.awt.Point", X=int(bl + (area_w - nw) / 2), Y=int(bt + (area_h - nh) / 2)))
    if not single:
        page.ungroup(group)
    return " Content was scaled to %d%% and centred to fit." % round(scale * 100) if scale < 1.0 else " Content was centred."


class SetPageFormat(ToolBase):
    name: str | None = "set_page_format"
    description: str = (
        "Set page orientation (landscape/portrait) and/or paper size (A3, A4, A5, Letter, Legal, Tabloid, 16:9, 4:3, "
        "or custom width/height in mm) for one page or all pages. fit_content shrinks and centres existing shapes "
        "so nothing falls off the page (default true). Use it for requests like 'make page 2 landscape' or 'A3 poster'."
    )
    parameters: dict[str, Any] | None = {
        "type": "object",
        "properties": {
            "orientation": {"type": "string", "enum": ["landscape", "portrait"]},
            "paper": {"type": "string", "enum": list(PAPER), "description": "Paper size (keeps the current one if omitted)"},
            "width_mm": {"type": "number", "description": "Custom page width in mm (with height_mm)"},
            "height_mm": {"type": "number", "description": "Custom page height in mm (with width_mm)"},
            "pages": {"type": "string", "description": "'current' (default), 'all', or 0-based page numbers like '1' or '0,2'"},
            "fit_content": {"type": "boolean", "description": "Shrink/centre shapes to the new page (default true)"},
        },
        "required": [],
    }
    uno_services: list[str] | None = _DRAW_UNO
    tier: str = "core"
    is_mutation: bool | None = True

    def execute(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        doc = ctx.doc
        pages = doc.getDrawPages()
        n = pages.getCount()
        which = str(kwargs.get("pages") or "current").strip().lower()
        if which == "all":
            idxs = list(range(n))
        elif which == "current":
            cur = getattr(ctx, "active_page_index", None)
            if cur is None:
                try:
                    cur_page = doc.getCurrentController().getCurrentPage()
                    cur = next(i for i in range(n) if pages.getByIndex(i) == cur_page)
                except Exception:
                    cur = 0
            idxs = [int(cur)]
        else:
            try:
                idxs = sorted({int(x) for x in which.replace(" ", "").split(",") if x != ""})
            except ValueError:
                return self._tool_error("pages must be 'current', 'all' or 0-based numbers like '1' or '0,2'.")
        if not idxs or any(i < 0 or i >= n for i in idxs):
            return self._tool_error("Page index out of range: the document has %d page(s), numbered 0-%d." % (n, n - 1))
        orient = kwargs.get("orientation")
        paper = str(kwargs.get("paper") or "").strip().lower() or None
        wmm, hmm = kwargs.get("width_mm"), kwargs.get("height_mm")
        if not (orient or paper or (wmm and hmm)):
            return self._tool_error("Give orientation, paper, or width_mm and height_mm.")
        if paper and paper not in PAPER:
            return self._tool_error("paper must be one of: %s" % ", ".join(PAPER))
        fit = kwargs.get("fit_content", True) is not False
        from com.sun.star.view.PaperOrientation import LANDSCAPE, PORTRAIT

        undo = None
        try:
            undo = doc.getUndoManager()
            undo.enterUndoContext("Page format")
        except Exception:
            undo = None
        notes: list[str] = []
        others = [i for i in range(n) if i not in idxs]
        before = {i: (pages.getByIndex(i).Width, pages.getByIndex(i).Height) for i in others}
        try:
            for i in idxs:
                page = pages.getByIndex(i)
                w, h = int(page.Width), int(page.Height)
                if wmm and hmm:
                    w, h = int(float(wmm) * 100), int(float(hmm) * 100)
                elif paper:
                    w, h = PAPER[paper]
                    if (w > h) != (int(page.Width) > int(page.Height)) and not orient:
                        w, h = h, w  # keep the current orientation
                if orient == "landscape" and w < h or orient == "portrait" and w > h:
                    w, h = h, w
                page.Width, page.Height = w, h
                try:
                    page.Orientation = LANDSCAPE if w > h else PORTRAIT
                except Exception:
                    pass
                note = fit_content(doc, page, getattr(ctx, "ctx", None)) if fit else ""
                notes.append("page %d: %.0f x %.0f mm %s.%s" % (i, w / 100, h / 100, "landscape" if w > h else "portrait", note))
        except Exception as exc:
            if is_real_disposal(exc):
                raise
            log.exception("set_page_format failed")
            return self._tool_error("Changing the page format failed: %s" % exc)
        finally:
            if undo is not None:
                try:
                    undo.leaveUndoContext()
                except Exception:
                    pass
        # Some documents keep one size for all pages; say so if others followed.
        changed = [i for i in others if (pages.getByIndex(i).Width, pages.getByIndex(i).Height) != before[i]]
        msg = "Page format set: " + " ".join(notes)
        if changed:
            msg += " Note: this document keeps one size for all pages, so page(s) %s changed too." % ", ".join(map(str, changed))
        return {"status": "ok", "message": msg}
