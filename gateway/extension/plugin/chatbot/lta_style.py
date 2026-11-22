# Libre Thinker Agent - modern look for the sidebar chat transcript
# SPDX-License-Identifier: GPL-3.0-or-later
"""Chat-app style formatting for the sidebar RichText control.

Runs after WriterAgent has painted the transcript. It only changes paragraph
and character attributes (alignment, spacing, indents, colours); it never
inserts or deletes characters, because WriterAgent cuts and re-appends the
streamed tail by character offset.

Layout:
- a small coloured speaker label on its own line ("You" / "Libre Thinker Agent")
  with space above each message;
- your messages right-aligned and inset from the left, like a chat bubble;
- replies left-aligned with paragraph spacing and hanging list indents;
- no character background, so lines never paint over the line above.
"""

from __future__ import annotations

import re
from typing import Any

LABEL_USER = "You"
LABEL_AGENT = "Libre Thinker Agent"
_USER_LABELS = (LABEL_USER, "You:")
_AGENT_LABELS = (LABEL_AGENT, "Assistant:")

# 1/100 mm
SIDE = 250
USER_INSET = 1400  # your messages start this far in from the left
MESSAGE_GAP = 350  # space above each speaker label
PARA_GAP = 140  # space after each paragraph of a message
LIST_INDENT = 420  # hanging indent for "1." / "•" items

_LIST_RE = re.compile(r"^\s*(?:[•◦▪–\-*]|\d{1,3}[.)]|[a-zA-Z][.)])\s")

# (label colour) per role, light / dark
_LABEL_COLORS = {
    ("user", False): 0x6B7280,
    ("user", True): 0x9CA3AF,
    ("assistant", False): 0xC9211E,  # brand red, matches the logo
    ("assistant", True): 0xF28B82,
}


def label_for(role: str) -> str:
    return LABEL_USER if role == "user" else LABEL_AGENT


def role_of_label(text: str) -> str | None:
    t = (text or "").strip()
    if t in _USER_LABELS:
        return "user"
    if t in _AGENT_LABELS:
        return "assistant"
    return None


def _is_dark(color: Any) -> bool:
    if not isinstance(color, int):
        return False
    r, g, b = (color >> 16) & 255, (color >> 8) & 255, color & 255
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < 128


def _set(obj: Any, name: str, value: Any) -> None:
    try:
        obj.setPropertyValue(name, value)
    except Exception:
        try:
            setattr(obj, name, value)
        except Exception:
            pass


def _paragraphs(model: Any) -> list[Any]:
    try:
        enum = model.createEnumeration()
    except Exception:
        try:
            enum = model.getText().createEnumeration()
        except Exception:
            return []
    out = []
    while enum.hasMoreElements():
        out.append(enum.nextElement())
    return out


def _fixed_line(height: int) -> Any:
    import uno

    ls = uno.createUnoStruct("com.sun.star.style.LineSpacing")
    ls.Mode = 3  # FIX
    ls.Height = height
    return ls


def _proportional_line() -> Any:
    import uno

    ls = uno.createUnoStruct("com.sun.star.style.LineSpacing")
    ls.Mode = 0  # PROP
    ls.Height = 100
    return ls


def _range(model: Any, para: Any) -> Any:
    try:
        return model.createTextCursorByRange(para)
    except Exception:
        return para


def polish_transcript(control: Any, tail: int | None = None) -> None:
    """Apply the chat layout. *tail* limits styling to the last N paragraphs (streaming)."""
    try:
        model = control.getModel() if control is not None else None
    except Exception:
        return
    if model is None:
        return
    from com.sun.star.style.ParagraphAdjust import LEFT, RIGHT
    from com.sun.star.awt.FontWeight import BOLD

    dark = _is_dark(getattr(model, "BackgroundColor", None))
    paras = _paragraphs(model)
    if not paras:
        return
    texts = []
    for p in paras:
        try:
            texts.append(p.getString())
        except Exception:
            texts.append("")
    # Role of every paragraph: the most recent label above it.
    roles: list[str] = []
    is_label: list[bool] = []
    role = "assistant"
    for t in texts:
        r = role_of_label(t)
        if r is not None:
            role = r
        roles.append(role)
        is_label.append(r is not None)
    start = max(0, len(paras) - tail) if tail else 0
    first_label = next((i for i, lab in enumerate(is_label) if lab), -1)
    for i in range(start, len(paras)):
        p, t, r = paras[i], texts[i], roles[i]
        user = r == "user"
        _set(p, "CharBackTransparent", True)
        _set(p, "ParaAdjust", RIGHT if user else LEFT)
        _set(p, "ParaRightMargin", SIDE)
        if is_label[i]:
            _set(p, "ParaLeftMargin", SIDE)
            _set(p, "ParaFirstLineIndent", 0)
            _set(p, "ParaTopMargin", 0 if i == first_label else MESSAGE_GAP)
            _set(p, "ParaBottomMargin", 40)
            # The label text carries its own colour/weight from the insert, which
            # wins over paragraph-level attributes: style the text range itself.
            rng = _range(model, p)
            for name, value in (("CharHeight", 8.5), ("CharWeight", BOLD), ("CharColor", _LABEL_COLORS[(r, dark)])):
                _set(rng, name, value)
            continue
        _set(p, "ParaTopMargin", 0)
        if not t.strip():
            # Blank separator rows between messages: the label carries the gap.
            _set(p, "ParaBottomMargin", 0)
            _set(p, "ParaLineSpacing", _fixed_line(80))  # an empty line still takes a full line height otherwise
            _set(p, "ParaLeftMargin", SIDE)
            _set(p, "ParaFirstLineIndent", 0)
            continue
        _set(p, "ParaBottomMargin", PARA_GAP)
        _set(p, "ParaLineSpacing", _proportional_line())
        left = SIDE + (USER_INSET if user else 0)
        if _LIST_RE.match(t) and not user:
            _set(p, "ParaLeftMargin", left + LIST_INDENT)
            _set(p, "ParaFirstLineIndent", -LIST_INDENT)
        else:
            _set(p, "ParaLeftMargin", left)
            _set(p, "ParaFirstLineIndent", 0)


def subtle_border(model: Any, bg_color: Any) -> None:
    """Thin flat border in a soft colour instead of the 3D frame."""
    dark = _is_dark(bg_color)
    _set(model, "Border", 2)
    _set(model, "BorderColor", 0x3A3F46 if dark else 0xD6DAE0)
