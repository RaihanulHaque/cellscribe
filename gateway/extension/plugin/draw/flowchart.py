# Libre Thinker Agent - flowchart tools for the Draw/Impress sidebar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Main-chat tools: draw a whole flowchart in one call, and change one step.

WriterAgent's own route sent flowcharts to a nested agent that placed shapes
one by one (slow, and each box got its own size, colour and auto-shrunk
font). These tools are on the main tool list in Draw and Impress, so the
model draws the diagram in one call and the layout/style is done in code.
"""

from __future__ import annotations

import logging
from typing import Any

from plugin.draw import flowchart_core as core
from plugin.draw.bridge import resolve_shape_page
from plugin.framework.errors import is_real_disposal
from plugin.framework.tool import ToolBase, ToolContext

log = logging.getLogger(__name__)

_DRAW_UNO = ["com.sun.star.drawing.DrawingDocument", "com.sun.star.presentation.PresentationDocument"]
_COLOR_HELP = (
    "Only when the user asks for a colour or a highlight: " + ", ".join(core.COLORS) + ", or #RRGGBB. "
    "Otherwise omit it: the house style already colours steps, decisions and start/end consistently."
)


class DrawFlowchart(ToolBase):
    name: str | None = "draw_flowchart"
    description: str = (
        "Draw a complete flowchart / process / workflow / approval / decision diagram in ONE call. "
        "Give the steps (nodes) and arrows (edges) only; layout, sizes, fonts, colours and connectors "
        "are done automatically in a consistent professional style, fitted to the page. "
        "Use kind='decision' for yes/no questions and label its outgoing edges ('Yes'/'No'). "
        "Loops (an edge back to an earlier step) are fine. Calling it again on the same page replaces "
        "the previous flowchart, so to add/remove/reorder steps call it again with the full list."
    )
    parameters: dict[str, Any] | None = {
        "type": "object",
        "properties": {
            "nodes": {
                "type": "array",
                "description": "Steps in reading order (first = where the process starts).",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Short unique id, e.g. 'review'"},
                        "text": {"type": "string", "description": "Label shown in the shape (keep it short: 2-6 words)"},
                        "kind": {"type": "string", "enum": list(core.KINDS), "description": "start/end = rounded terminator, process = step, decision = diamond, data = input/output, document, subprocess, database"},
                        "color": {"type": "string", "description": _COLOR_HELP},
                    },
                    "required": ["id", "text"],
                },
            },
            "edges": {
                "type": "array",
                "description": "Arrows between steps.",
                "items": {
                    "type": "object",
                    "properties": {
                        "from": {"type": "string"},
                        "to": {"type": "string"},
                        "label": {"type": "string", "description": "Optional, e.g. 'Yes' / 'No'"},
                    },
                    "required": ["from", "to"],
                },
            },
            "title": {"type": "string", "description": "Optional heading above the diagram"},
            "direction": {"type": "string", "enum": ["auto", "top_to_bottom", "left_to_right"], "description": "Default auto: whichever fits the page best"},
            "page": {"type": "integer", "description": "0-based page index (active page if omitted)"},
        },
        "required": ["nodes", "edges"],
    }
    uno_services: list[str] | None = _DRAW_UNO
    tier: str = "core"
    is_mutation: bool | None = True

    def execute(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        page, idx, err = resolve_shape_page(ctx, kwargs)
        if err or page is None:
            return self._tool_error(err or "No draw page available.")
        try:
            result = core.draw(
                ctx.doc, page, kwargs.get("nodes"), kwargs.get("edges") or [],
                direction=str(kwargs.get("direction") or "auto"), title=str(kwargs.get("title") or ""),
            )
        except core.FlowchartError as exc:
            return self._tool_error(str(exc), code="FLOWCHART_INVALID")
        except Exception as exc:
            if is_real_disposal(exc):
                raise
            log.exception("draw_flowchart failed")
            return self._tool_error("Drawing the flowchart failed: %s" % exc, code="FLOWCHART_FAILED")
        result["page"] = idx
        return result


class UpdateFlowchartStep(ToolBase):
    name: str | None = "flowchart_update_step"
    description: str = (
        "Change ONE step of a flowchart drawn with draw_flowchart, in place: its text, highlight colour, "
        "or kind. Nothing else on the page moves. Use it for requests like 'make the tender step red' "
        "or 'rename Finance check'. Use flowchart_read to see the step ids."
    )
    parameters: dict[str, Any] | None = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "Step id given to draw_flowchart"},
            "text": {"type": "string"},
            "color": {"type": "string", "description": _COLOR_HELP + " Use 'default' to restore the house style."},
            "kind": {"type": "string", "enum": list(core.KINDS)},
            "page": {"type": "integer", "description": "0-based page index (active page if omitted)"},
        },
        "required": ["id"],
    }
    uno_services: list[str] | None = _DRAW_UNO
    tier: str = "core"
    is_mutation: bool | None = True

    def execute(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        page, _idx, err = resolve_shape_page(ctx, kwargs)
        if err or page is None:
            return self._tool_error(err or "No draw page available.")
        color = kwargs.get("color")
        if isinstance(color, str) and color.strip().lower() in ("default", "none", "reset"):
            color = ""
        try:
            return core.update_node(page, str(kwargs.get("id") or ""), text=kwargs.get("text"), color=color, kind=kwargs.get("kind"))
        except core.FlowchartError as exc:
            return self._tool_error(str(exc), code="FLOWCHART_INVALID")
        except Exception as exc:
            if is_real_disposal(exc):
                raise
            log.exception("flowchart_update_step failed")
            return self._tool_error("Updating the step failed: %s" % exc, code="FLOWCHART_FAILED")


class ReadFlowchart(ToolBase):
    name: str | None = "flowchart_read"
    description: str = (
        "Return the steps and arrows of the flowchart on the page (as drawn with draw_flowchart), "
        "so it can be changed: edit the list and call draw_flowchart again, or change one step with flowchart_update_step."
    )
    parameters: dict[str, Any] | None = {
        "type": "object",
        "properties": {"page": {"type": "integer", "description": "0-based page index (active page if omitted)"}},
        "required": [],
    }
    uno_services: list[str] | None = _DRAW_UNO
    tier: str = "core"
    is_mutation: bool | None = False

    def execute(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        page, idx, err = resolve_shape_page(ctx, kwargs)
        if err or page is None:
            return self._tool_error(err or "No draw page available.")
        spec = core.read_spec(page)
        if spec is None:
            return {"status": "ok", "page": idx, "flowchart": None, "message": "No flowchart drawn with draw_flowchart on this page."}
        return {"status": "ok", "page": idx, "flowchart": spec}
