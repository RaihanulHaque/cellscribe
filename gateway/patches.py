# CellScribe - build-time patches applied to the vendored WriterAgent package
# SPDX-License-Identifier: GPL-3.0-or-later
"""Small, exact patches to WriterAgent, applied while the gateway builds CellScribe.oxt.

vendor/WriterAgent.oxt itself is never modified. Each patch is an exact
search/replace that must match once; if a WriterAgent upgrade changes the
code underneath, building the extension fails loudly instead of shipping a
half-patched package.

Why these exist (measured on the gateway logs, Nov 2026): in Calc the model
could see only row 1 of the sheet and read at most 80 cells per tool call, so
a "summarise this attendance sheet" request either invented an answer from a
10x8 sample or spent ~17 rounds nibbling at the sheet (prompt 5.7k -> 28.7k
tokens). The patches put the sheet's values in front of the model, steer it
to answer questions directly, and bound the agent loop.
"""

from __future__ import annotations

# Writer/Calc chat context budget in characters (WriterAgent ships 8000, about
# one page). 60000 chars is ~15k tokens: a whole attendance sheet or a long
# Writer document, while staying far below any supported model's window.
CONTEXT_CHARS = 60000
# Cells read_cell_range returns before switching to a sample. WriterAgent ships
# 80; each cell is a verbose dict, so stay modest — the full sheet values are
# already in context.
READ_RANGE_MAX_CELLS = 400
# Tool rounds per user message (WriterAgent ships 15). The sidebar runs one
# tool per model call (it sends parallel_tool_calls=false), so a table plus a
# chart needs ~10 rounds; 8 cut such tasks off half-done in real use.
MAX_TOOL_ROUNDS = 20


_CALC_SHEET_CSV = '''

# --- CellScribe: include the sheet's values in chat context -----------------
# WriterAgent only sent row 1 as "headers", so the model never saw the data
# and either guessed or read it 80 cells at a time.
_CS_CSV_MAX_CELLS = 30000


def _cs_col_letters(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


import re as _cs_re

# Displayed text of a plain number: optional currency/prefix, digits with
# thousands separators, optional decimals, optional suffix such as %.
_CS_NUMERIC_TEXT = _cs_re.compile(r"[^\\d]*-?[\\d,]*\\.?\\d+[^\\d]*")
_CS_TOTAL_FORMULA = _cs_re.compile(r"^=.*\\b(SUM|SUBTOTAL|AGGREGATE)\\s*\\(")
_CS_TOTAL_LABEL = _cs_re.compile(r"^\\s*(grand\\s+|sub\\s*-?\\s*)?totals?\\b", _cs_re.I)


def _cs_num(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _cs_profile(data: Any, cell_range: Any, sr: int, sc: int, clipped: bool) -> str:
    """Column map and exact per-column figures, so the model never adds up rows itself.

    The header row is the first of the first 10 rows that is at least 60%
    filled (title rows above a table are mostly empty) and holds some text;
    figures cover the rows below it. Models miscount positions in wide CSV
    rows and get sums of 40 rows wrong, so both are computed here.
    """
    filled = [sum(1 for v in row if v not in (None, "")) for row in data[:10]]
    widest = max(filled, default=0)
    header_idx = None
    for i, row in enumerate(data[:10]):
        if widest >= 2 and filled[i] >= 0.6 * widest and any(isinstance(v, str) and v.strip() for v in row):
            header_idx = i
            break
    if header_idx is None or header_idx + 1 >= len(data):
        return ""
    header = []
    for j, val in enumerate(data[header_idx]):
        if isinstance(val, str):
            header.append(val.strip())
        elif val is None or val == "":
            header.append("")
        else:
            header.append(cell_range.getCellByPosition(j, header_idx).getString())
    body = data[header_idx + 1:]
    # Leave existing total/subtotal rows out of the figures, or every sum doubles.
    try:
        formulas = cell_range.getFormulaArray()[header_idx + 1:]
    except Exception:
        formulas = ()
    totals = set()
    for i, row in enumerate(body):
        frow = formulas[i] if i < len(formulas) else ()
        if any(isinstance(f, str) and _CS_TOTAL_FORMULA.search(f.upper()) for f in frow) or any(
            isinstance(v, str) and _CS_TOTAL_LABEL.match(v) for v in row
        ):
            totals.add(i)
    keep = [i for i in range(len(body)) if i not in totals]
    out = f"Header row {sr + header_idx + 1}. Column map: " + "; ".join(
        f"{_cs_col_letters(sc + j)}={h}" for j, h in enumerate(header) if h
    ) + "\\n"
    lines = []
    measures: list[tuple[float, int, str]] = []  # (abs total, column index, label)
    dims: list[tuple[int, str]] = []
    date_cols: list[tuple[int, str]] = []
    for j, name in enumerate(header):
        col = [body[i][j] for i in keep if j < len(body[i]) and body[i][j] not in (None, "")]
        if not col:
            continue
        label = f"{_cs_col_letters(sc + j)}" + (f" ({name})" if name else "")
        nums = [v for v in col if not isinstance(v, str)]
        texts = [v for v in col if isinstance(v, str)]
        if nums and len(nums) >= len(texts):
            # Dates and times are numbers underneath; summing serials is noise.
            num_rows = [i for i in keep if j < len(body[i]) and body[i][j] not in (None, "") and not isinstance(body[i][j], str)]
            lo = min(num_rows, key=lambda i: body[i][j])
            hi = max(num_rows, key=lambda i: body[i][j])
            shown_lo = cell_range.getCellByPosition(j, header_idx + 1 + lo).getString()
            if shown_lo and not _CS_NUMERIC_TEXT.fullmatch(shown_lo.strip()):
                shown_hi = cell_range.getCellByPosition(j, header_idx + 1 + hi).getString()
                lines.append(f"{label}: {len(nums)} dates/times, earliest {shown_lo}, latest {shown_hi}")
                date_cols.append((j, label))
                continue
            total = sum(nums)
            if len(set(nums)) > 1:
                measures.append((abs(total), j, label))
            lines.append(
                f"{label}: {len(nums)} numbers, sum={_cs_num(total)}, min={_cs_num(min(nums))}, "
                f"max={_cs_num(max(nums))}, mean={_cs_num(total / len(nums))}"
            )
        else:
            counts: dict[str, int] = {}
            for v in texts:
                counts[v] = counts.get(v, 0) + 1
            if len(counts) <= 12:
                pairs = ", ".join(f"{k}={n}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1]))
                lines.append(f"{label}: {len(texts)} text values; counts {pairs}")
                if len(counts) >= 2:
                    dims.append((j, label))
            else:
                lines.append(f"{label}: {len(texts)} text values, {len(counts)} distinct")
    if not lines:
        return out
    scope = "rows read" if clipped else "all data rows"
    if totals:
        scope += ", excluding total/subtotal row(s) " + ", ".join(str(sr + header_idx + 2 + i) for i in sorted(totals))
    out += f"Column profile (computed exactly over {scope} below the header; use these figures for totals/counts instead of adding rows yourself):\\n"
    out += "\\n".join(lines) + "\\n"
    return out + _cs_group_totals(body, keep, measures, dims, date_cols)


def _cs_group_totals(body: Any, keep: list[int], measures: Any, dims: Any, date_cols: Any) -> str:
    """Pivot-style sums of the two largest numeric columns by each category and by month.

    These are the questions offices ask most ("revenue by region", "by
    month"); computing them here keeps the model from adding rows in its head
    or writing helper tables into the sheet just to answer.
    """
    import datetime

    top = [(j, label) for _, j, label in sorted(measures, reverse=True)[:2]]
    if not top:
        return ""

    def month_of(v: Any) -> str | None:
        try:
            return (datetime.date(1899, 12, 30) + datetime.timedelta(days=int(v))).strftime("%Y-%m")
        except (TypeError, ValueError, OverflowError):
            return None

    groups = [(j, label, None) for j, label in dims] + [(j, label + " by month", month_of) for j, label in date_cols]
    lines = []
    for gj, glabel, keyfn in groups:
        for mj, mlabel in top:
            sums: dict[str, float] = {}
            for i in keep:
                row = body[i]
                if gj >= len(row) or mj >= len(row) or isinstance(row[mj], str) or row[mj] in (None, ""):
                    continue
                key = keyfn(row[gj]) if keyfn else row[gj]
                if key in (None, ""):
                    continue
                sums[str(key)] = sums.get(str(key), 0.0) + row[mj]
            if len(sums) < 2 or (keyfn and len(sums) > 24):
                continue
            ordered = sorted(sums.items()) if keyfn else sorted(sums.items(), key=lambda kv: -kv[1])
            lines.append(f"Sum of {mlabel} per {glabel}: " + ", ".join(f"{k}={_cs_num(v)}" for k, v in ordered))
    text = "\\n".join(lines)
    if len(text) > 6000:
        text = text[:6000].rsplit("\\n", 1)[0] + "\\n[more group totals omitted]"
    return ("Group totals (exact):\\n" + text + "\\n") if text else ""


def _cs_sheet_csv(bridge: Any, sheet_name: str, budget: int) -> str:
    """Used range of *sheet_name* as CSV lines prefixed with the row number.

    Values are the displayed text (dates, times and percentages as shown), not
    raw serial numbers. Stops at *budget* characters and says which rows were
    left out so the model can read them with a tool.
    """
    import csv
    import io

    if budget < 500:
        return ""
    sheet = bridge.get_sheet(sheet_name)
    addr = used_range_address(sheet)
    sc, sr, ec, er = addr.StartColumn, addr.StartRow, addr.EndColumn, addr.EndRow
    cols = ec - sc + 1
    last_row = min(er, sr + max(1, _CS_CSV_MAX_CELLS // max(cols, 1)) - 1)
    cell_range = sheet.getCellRangeByPosition(sc, sr, ec, last_row)
    data = cell_range.getDataArray()
    if not any(v not in (None, "") for row in data for v in row):
        return "(This sheet is empty.)\\n"

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\\n")
    writer.writerow(["Row"] + [_cs_col_letters(c) for c in range(sc, ec + 1)])
    lines = [buf.getvalue()]
    used = len(lines[0])
    shown_until = None
    for i, row in enumerate(data):
        values = []
        for j, val in enumerate(row):
            if val is None or val == "":
                values.append("")
            elif isinstance(val, str):
                values.append(val)
            else:
                values.append(cell_range.getCellByPosition(j, i).getString())
        while values and values[-1] == "":
            values.pop()
        if not values:
            continue
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\\n")
        writer.writerow([str(sr + i + 1)] + values)
        line = buf.getvalue()
        if used + len(line) > budget:
            break
        lines.append(line)
        used += len(line)
        shown_until = sr + i + 1
    else:
        shown_until = last_row + 1

    out = _cs_profile(data, cell_range, sr, sc, last_row < er)
    out += "Sheet Data (CSV; first field is the row number, header gives column letters; empty rows skipped):\\n"
    out += "".join(lines)
    if shown_until is None or shown_until < er + 1:
        first_missing = (shown_until or sr) + 1
        out += (
            f"[Rows {first_missing}-{er + 1} are NOT shown (too large for context). "
            f'Read them with read_cell_range, e.g. sheet="{sheet_name}" range A{first_missing}:{_cs_col_letters(ec)}{min(er + 1, first_missing + 40)}.]\\n'
        )
    else:
        out += "[All rows of this sheet are shown above.]\\n"
    return out
'''


# Plain-language progress for the sidebar, in place of "[Running tool: x...]".
_FRIENDLY_PROGRESS = '''# --- CellScribe: plain-language progress lines -------------------------------
_CS_TOOL_WORDS = {
    "write_formula_range": "Writing to {where}",
    "insert_cell_html": "Writing to {where}",
    "read_cell_range": "Reading {where}",
    "get_sheet_summary": "Looking at the sheet",
    "set_style": "Formatting {where}",
    "merge_cells": "Merging {where}",
    "delete_structure": "Removing rows or columns",
    "sort_range": "Sorting {where}",
    "undo": "Undoing the last change",
    "redo": "Redoing the change",
    "get_document_content": "Reading the document",
    "apply_document_content": "Writing into the document",
    "get_document_outline": "Looking at the document outline",
    "search_in_document": "Searching the document",
    "add_comment": "Adding a comment",
    "web_research": "Searching the web",
    "list_open_documents": "Checking open documents",
}
_CS_DOMAIN_WORDS = {
    "charts": "Creating the chart",
    "sheets": "Setting up the sheet",
    "ranges": "Rearranging the data",
    "conditional_formatting": "Setting up highlighting",
    "pivot": "Building the pivot table",
    "web_research": "Searching the web",
    "document_research": "Looking through your files",
    "tables": "Working on the table",
    "images": "Working on the image",
}


def _cs_where(func_args: Mapping[str, Any]) -> str:
    raw = func_args.get("range") or func_args.get("sheet") or ""
    if isinstance(raw, (list, tuple)):
        raw = ", ".join(str(r) for r in raw[:2]) + (" and more" if len(raw) > 2 else "")
    text = str(raw).strip().strip("[]").replace('"', "")
    return text if text and len(text) <= 40 else "the sheet"


def _cs_progress(func_name: str, func_args: Mapping[str, Any]) -> str:
    if func_name in DELEGATE_GATEWAY_TOOL_NAMES:
        return _CS_DOMAIN_WORDS.get(domain_from_delegate_args(func_args), "Working on it")
    words = _CS_TOOL_WORDS.get(func_name)
    if words is None:
        return "Working on it"
    return words.format(where=_cs_where(func_args)) if "{where}" in words else words


'''

_DROPPED_PASSAGES = '''
def _cs_dropped_passages(before, after):
    """Passages of *before* that a full rewrite lost, or [] when it was not an edit.

    Fuzzy line matching (so fixed typos or a reworded sentence count as kept).
    Only reports when most of the text survived: a translation or a full
    restructure changes nearly every line and is not flagged.
    """
    import difflib
    import re as _re

    if not before or after is None:
        return []

    def lines(text):
        out = []
        for raw in text.split("\\n"):
            norm = _re.sub(r"\\s+", " ", raw).strip()
            if len(norm) >= 12:
                out.append(norm)
        return out

    old, new = lines(before)[:400], lines(after)[:400]
    if not old or not new:
        return []
    new_low = [n.lower() for n in new]
    joined = " ".join(new_low)
    lost = []
    for line in old:
        low = line.lower()
        if low in joined:
            continue
        if any(difflib.SequenceMatcher(None, low, n).quick_ratio() >= 0.75 and difflib.SequenceMatcher(None, low, n).ratio() >= 0.75 for n in new_low):
            continue
        lost.append(line)
    kept = 1 - len(lost) / len(old)
    if not lost or kept < 0.6 or len(lost) > 30:
        return []
    return [l[:160] for l in lost]

'''

# (old, new) pairs for ChatPanelDialog.xdl: hidden controls move into the
# Send/Stop/Clear row (top 186) so the layout keeps no empty band below it.
_XDL_MOVES = [
    ('dlg:id="btn_hamburger" dlg:left="76" dlg:top="2"', 'dlg:id="btn_hamburger" dlg:left="22" dlg:top="2"'),
    ('dlg:id="chat_mode_selector" dlg:left="4" dlg:top="203"', 'dlg:id="chat_mode_selector" dlg:left="4" dlg:top="186"'),
    ('dlg:id="model_label" dlg:left="4" dlg:top="217"', 'dlg:id="model_label" dlg:left="4" dlg:top="186"'),
    ('dlg:id="model_selector" dlg:left="4" dlg:top="229"', 'dlg:id="model_selector" dlg:left="4" dlg:top="186"'),
    ('dlg:id="reasoning_effort_selector" dlg:left="111" dlg:top="229"', 'dlg:id="reasoning_effort_selector" dlg:left="111" dlg:top="186"'),
    ('dlg:id="image_model_selector" dlg:left="4" dlg:top="217"', 'dlg:id="image_model_selector" dlg:left="4" dlg:top="186"'),
    ('dlg:id="base_size_label" dlg:left="4" dlg:top="233"', 'dlg:id="base_size_label" dlg:left="4" dlg:top="186"'),
    ('dlg:id="base_size_input" dlg:left="25" dlg:top="231"', 'dlg:id="base_size_input" dlg:left="25" dlg:top="186"'),
    ('dlg:id="aspect_ratio_selector" dlg:left="70" dlg:top="231"', 'dlg:id="aspect_ratio_selector" dlg:left="70" dlg:top="186"'),
]


PATCHES: dict[str, list[tuple[str, str]]] = {
    # --- Bigger chat context (Writer and Calc) --------------------------------
    "plugin/framework/constants.py": [
        (
            "CHAT_DOCUMENT_CONTEXT_MAX_CHARS = 8000\n",
            f"CHAT_DOCUMENT_CONTEXT_MAX_CHARS = {CONTEXT_CHARS}  # CellScribe: WriterAgent ships 8000\n",
        ),
    ],
    # --- Calc: send the sheet's values ---------------------------------------
    "plugin/calc/analyzer.py": [
        (
            "\n\ndef _internal_tab_header(",
            _CALC_SHEET_CSV + "\n\ndef _internal_tab_header(",
        ),
        (
            "        ctx_str += f\"Columns: {_format_columns(summary['headers'])}\\n\"\n\n"
            "        # Add selection context if available\n",
            "        ctx_str += f\"Columns: {_format_columns(summary['headers'])}\\n\"\n"
            "        try:\n"
            "            ctx_str += _cs_sheet_csv(vs.bridge, summary[\"sheet_name\"], max_context - len(ctx_str) - 2500)\n"
            "            for _other in vs.names:\n"
            "                if _other == summary[\"sheet_name\"] or max_context - len(ctx_str) - 2500 < 1500:\n"
            "                    continue\n"
            "                _other_sheet = vs.bridge.get_sheet(_other)\n"
            "                _addr = used_range_address(_other_sheet)\n"
            "                ctx_str += f\"\\n--- Other sheet: {_other} (used range {format_range(_addr.StartColumn, _addr.StartRow, _addr.EndColumn, _addr.EndRow, collapse_single=False)}; pass sheet=\\\"{_other}\\\" to tools) ---\\n\"\n"
            "                ctx_str += _cs_sheet_csv(vs.bridge, _other, max_context - len(ctx_str) - 2500)\n"
            "        except Exception as exc:\n"
            "            if is_real_disposal(exc):\n"
            "                raise DocumentDisposedError(\"Calc document disposed during data read\", object_type=\"calc\") from exc\n"
            "            log.warning(\"CellScribe sheet CSV failed: %s\", exc)\n\n"
            "        # Add selection context if available\n",
        ),
    ],
    # --- Calc: larger reads, no =PY steering, forgiving range arguments ---------
    "plugin/calc/cells.py": [
        # Models often send a list as a JSON string ('["O1:P1","P2:P4"]'); each
        # rejection cost a full tool round in testing.
        (
            "def _range_args(raw: Any) -> list[Any]:\n"
            "    if not raw:\n"
            "        return []\n"
            "    if isinstance(raw, str):\n"
            "        return [raw]\n",
            "def _range_args(raw: Any) -> list[Any]:\n"
            "    if not raw:\n"
            "        return []\n"
            "    if isinstance(raw, str):\n"
            "        text = raw.strip()\n"
            "        if text.startswith(\"[\") and text.endswith(\"]\"):  # CellScribe: JSON list sent as a string\n"
            "            import json\n"
            "            try:\n"
            "                parsed = json.loads(text)\n"
            "            except ValueError:\n"
            "                parsed = None\n"
            "            if isinstance(parsed, list) and parsed and all(isinstance(p, str) for p in parsed):\n"
            "                return parsed\n"
            "        return [raw]\n"
            "    if isinstance(raw, (list, tuple)):  # CellScribe: ['[\"O1:P5\"]'] after schema coercion\n"
            "        flat: list[Any] = []\n"
            "        for item in raw:\n"
            "            flat.extend(_range_args(item) if isinstance(item, str) and item.strip().startswith(\"[\") else [item])\n"
            "        return flat\n",
        ),
        # Number formats and freeze panes: everyday office formatting that
        # WriterAgent's chat model could not do at all (number_format was
        # hidden from the model; there was no freeze tool).
        (
            '            "border_color": {"type": "string", "description": ("Border color (hex or name). Draws a frame around the cell/range.")},\n'
            "        },\n"
            '        "required": ["range"],\n'
            "    }\n",
            '            "border_color": {"type": "string", "description": ("Border color (hex or name). Draws a frame around the cell/range.")},\n'
            '            "number_format": {"type": "string", "description": ("Number format code, e.g. \\"#,##0\\" (thousands separator), \\"#,##0.00\\", \\"0.0%\\", \\"[$৳-445] #,##0\\" (Taka), \\"DD-MMM-YYYY\\". Only for numeric columns; never on date columns unless the user asks to change how dates look.")},\n'
            '            "freeze_at": {"type": "string", "description": ("Freeze panes above and left of this cell on the range\'s sheet: \\"A2\\" freezes the header row, \\"B2\\" freezes row 1 and column A.")},\n'
            "        },\n"
            '        "required": ["range"],\n'
            "    }\n",
        ),
        (
            '    scripting_only_parameters: frozenset[str] = frozenset({"number_format"})\n',
            "    scripting_only_parameters: frozenset[str] = frozenset()  # CellScribe: number_format exposed\n",
        ),
        (
            "        if len(rn) == 0:\n"
            '            return self._tool_error("range is required")\n\n'
            "        # Bugfix: Style calls with no properties performed empty mutations and opened\n",
            "        if len(rn) == 0:\n"
            '            return self._tool_error("range is required")\n\n'
            "        froze = None\n"
            '        freeze_at = kwargs.get("freeze_at")\n'
            "        if isinstance(freeze_at, str) and freeze_at.strip():  # CellScribe: freeze panes\n"
            "            import re as _re\n"
            '            m = _re.fullmatch(r"(?:.*\\.)?\\$?([A-Za-z]{1,3})\\$?(\\d+)", freeze_at.strip())\n'
            "            if not m:\n"
            '                return self._tool_error(f"freeze_at must be a cell like A2, got {freeze_at!r}")\n'
            "            col = 0\n"
            "            for ch in m.group(1).upper():\n"
            "                col = col * 26 + (ord(ch) - 64)\n"
            "            controller = ctx.doc.getCurrentController()\n"
            "            controller.freezeAtPosition(col - 1, int(m.group(2)) - 1)\n"
            '            froze = f"Froze panes at {m.group(1).upper()}{m.group(2)}"\n'
            "            if all(v is None for v in style_kwargs.values()):\n"
            '                return {"status": "ok", "message": froze}\n\n'
            "        # Bugfix: Style calls with no properties performed empty mutations and opened\n",
        ),
        # range=["N1","O1"], values=["VAT","Total"] means one value per cell.
        (
            "        single_cell = len(rn) == 1 and _a1_range_shape(rn[0]) == (1, 1, 1)\n",
            "        per_cell = fov if isinstance(fov, list) else None\n"
            "        if per_cell is None and isinstance(fov, str) and fov.lstrip().startswith(\"[\"):\n"
            "            import json\n"
            "            try:\n"
            "                per_cell = json.loads(fov)\n"
            "            except ValueError:\n"
            "                per_cell = None\n"
            "        if (\n"
            "            len(rn) > 1\n"
            "            and isinstance(per_cell, list)\n"
            "            and len(per_cell) == len(rn)\n"
            "            and all(not isinstance(v, (list, tuple, dict, bool)) and v is not None for v in per_cell)\n"
            "            and all(_a1_range_shape(r) == (1, 1, 1) for r in rn)\n"
            "        ):  # CellScribe: one value per single-cell range\n"
            "            values_iter = iter(str(v) for v in per_cell)\n"
            "            return _mutate_ranges(\n"
            "                self, ctx.doc, rn, \"WriterAgent: Write formulas\",\n"
            "                lambda r: manipulator.write_formula_range(r, next(values_iter)),\n"
            "                format_result=lambda results: {\"status\": \"ok\", \"message\": f\"Wrote one value into each of {len(rn)} cells\"},\n"
            "            )\n"
            "        single_cell = len(rn) == 1 and _a1_range_shape(rn[0]) == (1, 1, 1)\n",
        ),
        (
            "_READ_CELL_RANGE_MAX_CELLS = 80\n",
            f"_READ_CELL_RANGE_MAX_CELLS = {READ_RANGE_MAX_CELLS}  # CellScribe: WriterAgent ships 80\n",
        ),
        (
            '    "Reductions that spill a small result: write =PY into one empty cell "\n'
            '    "outside the data."\n',
            '    "For totals and counts, write an ordinary formula (SUM, COUNTIF, AVERAGE) "\n'
            '    "into one empty cell outside the data."\n',
        ),
    ],
    # --- No cross-document memory ---------------------------------------------
    # WriterAgent kept one global USER.md "memory" and put it in every prompt, so
    # a test essay preference ("rocket engines, deep blue headings") leaked into
    # unrelated documents. Chat history is already per document; memory is off.
    "plugin/chatbot/memory.py": [
        (
            '    name: str | None = "upsert_memory"\n',
            '    name: str | None = "upsert_memory"\n'
            "    # CellScribe: off the sidebar tool list (mcp tier is excluded by default).\n",
        ),
        (
            "    uno_services: list[str] | None = None\n"
            '    tier: str = "core"\n'
            "    is_mutation: bool | None = False\n\n"
            '    parameters: dict[str, Any] | None = {"type": "object", "properties": {"key"',
            "    uno_services: list[str] | None = None\n"
            '    tier: str = "mcp"\n'
            "    is_mutation: bool | None = False\n\n"
            '    parameters: dict[str, Any] | None = {"type": "object", "properties": {"key"',
        ),
    ],
    # --- Writer: the chat snapshot cut off everything after a table ----------
    # The excerpt is read by cursor steps, which undercount tables, so text
    # after them vanished from [DOCUMENT CONTENT] without a truncation note.
    # Models then "repaired" their own correct minutes by deleting the
    # sections they could no longer see (DeepSeek and Mistral Large alike).
    "plugin/doc/document_helpers.py": [
        (
            "            take = min(doc_len, max_context)\n"
            "            excerpt = _text_helpers._read_writer_text_slice(model, 0, take)\n",
            "            take = min(doc_len, max_context)\n"
            "            excerpt = _text_helpers._read_writer_text_slice(model, 0, take)\n"
            "            try:  # CellScribe: fall back to the full text when the cursor read came up short\n"
            "                _cs_full = model.getText().getString()\n"
            "                _cs_redlines = model.getRedlines().createEnumeration().hasMoreElements()\n"
            "            except Exception:\n"
            "                _cs_full, _cs_redlines = \"\", True\n"
            "            if not _cs_redlines and len(_cs_full) <= max_context and len(excerpt.strip()) + 5 < len(_cs_full.strip()):\n"
            "                excerpt = _cs_full\n"
            "                shown_len = len(_cs_full)\n"
            "                start_offset = end_offset = 0  # selection markers need cursor offsets\n",
        ),
    ],
    # --- Writer: full rewrites must not silently drop passages ---------------
    # Measured 4/4: after writing correct minutes, the model re-sent the whole
    # document without its last sections ("Decision", "Next meeting") and then
    # claimed nothing was lost. The tool now reports dropped passages.
    "plugin/writer/apply_document_content.py": [
        (
            '        if target == "full_document":\n'
            "            session = _open_session()\n"
            "            with session:\n",
            '        if target == "full_document":\n'
            "            try:  # CellScribe: remember the text to detect dropped passages\n"
            "                _cs_before = ctx.doc.getText().getString()\n"
            "            except Exception:\n"
            "                _cs_before = None\n"
            "            session = _open_session()\n"
            "            with session:\n",
        ),
        (
            '            return {"status": "ok", "message": "Replaced entire document."}, session\n'
            '        if target == "end":\n',
            "            _cs_result = {\"status\": \"ok\", \"message\": \"Replaced entire document.\"}\n"
            "            try:\n"
            "                _cs_lost = _cs_dropped_passages(_cs_before, ctx.doc.getText().getString())\n"
            "            except Exception:\n"
            "                _cs_lost = []\n"
            "            if _cs_lost:\n"
            "                _cs_result[\"removed_passages\"] = _cs_lost\n"
            "                _cs_result[\"message\"] = (\n"
            "                    \"Replaced entire document, but these passages from the previous version are no longer in it: \"\n"
            "                    + \" | \".join(_cs_lost)\n"
            "                    + \". Unless the user asked to remove them, restore them now (insert them where they belong) and do not report the task as done before they are back.\"\n"
            "                )\n"
            "            return _cs_result, session\n"
            '        if target == "end":\n',
        ),
        (
            "\nclass ",
            _DROPPED_PASSAGES + "\nclass ",
        ),
    ],
    # --- Calc: sort_range was a silent no-op ---------------------------------
    # pyuno passes a plain tuple of TableSortField structs as an untyped
    # sequence, which Calc ignores, so sort "succeeded" and changed nothing.
    "plugin/calc/manipulator.py": [
        (
            "                if p.Name == \"SortFields\":\n"
            "                    p.Value = (sort_field,)\n",
            "                if p.Name == \"SortFields\":\n"
            "                    import uno as _uno  # CellScribe: typed sequence, or Calc ignores it\n"
            "                    p.Value = _uno.Any(\"[]com.sun.star.table.TableSortField\", (sort_field,))\n",
        ),
        (
            "            cell_range.sort(tuple(sort_desc))\n",
            "            import uno as _uno\n"
            "            _uno.invoke(cell_range, \"sort\", (tuple(sort_desc),))\n",
        ),
    ],
    # --- Calc prompt: answer from the data, batch edits, ordinary formulas -----
    "plugin/framework/prompts.py": [
        # --- Writer: do the asked work in few calls, no decoration spiral -----
        # Measured: minutes took 20 rounds / 5 min of colours and custom styles
        # and replaced a heading with "[REDACTED]"; translation went paragraph
        # by paragraph (10 calls for a memo), which cuts long documents off.
        (
            'WRITER_CHAT_PERSONA = """You are a LibreOffice Writer assistant who produces polished, professional documents with thoughtful use of color and formatting.\n'
            'Honor any stated memory preferences for color, etc."""\n',
            'WRITER_CHAT_PERSONA = """You are a LibreOffice Writer assistant for busy office staff: you get exactly the requested work done quickly and correctly, '
            'in a clean professional style built from the document\'s own named styles (exact names: Title, Heading 1, Heading 2, Text body) and plain tables."""\n',
        ),
        (
            'Follow CHAT RESPONSE FORMAT for that short reply."""\n',
            'Follow CHAT RESPONSE FORMAT for that short reply.\n'
            "EFFICIENCY (you get ONE tool call per turn, and a limited number of turns):\n"
            "- Questions about the document (summarise, explain, who/what/when, find): answer in the sidebar from the document text you already have; do not edit the document.\n"
            "- New or restructured content (minutes, letters, summaries, reports, tables): write it in ONE apply_document_content call.\n"
            "- Translation, or rewriting/proofreading a whole document: get_document_content once, then ONE apply_document_content(target='full_document') with the complete result. Never go paragraph by paragraph. "
            "Only when the document has tables or images, use target='search' edits instead, each spanning as many consecutive paragraphs as possible.\n"
            "- Formatting: named styles via data-lo-style (Title, Heading1, Heading2) and plain <table>. No custom colours, fonts or new styles unless the user asks for them. Stop once the requested work is done; do not polish.\n"
            "- Never put placeholders such as [REDACTED], [TBD] or '...' in place of document text; keep every fact from the source.\n"
            "- After your main write, do not rewrite or delete sections you wrote; a second write is only to fix a tool error or something the user asked for. Meeting dates, times and places, names, numbers and decisions are normal business content, not private data: keep them all. Before you reply, check that every name, number, date, time, place and decision from the source is in the result; never claim nothing was lost unless you checked.\"\"\"\n",
        ),
        (
            '        return _("AI: I can help you with formulas, data analysis, and colorful charts. Try me!")\n',
            '        return _("Hi, I\'m CellScribe. Ask me to summarise this sheet, answer questions about it, add formulas, sort, format or make a chart.")\n',
        ),
        (
            '    return _("AI: I can edit or translate your document instantly with professional formatting and color. Try me!")\n',
            '    return _("Hi, I\'m CellScribe. Ask me to summarise, rewrite, proofread, translate or format this document.")\n',
        ),
        (
            'MEMORY_GUIDANCE = """MEMORY:\n'
            "You have a persistent file-backed memory tool.\n"
            "- Save user preferences and durable facts; update an existing key or delete stale facts with \"\".\n"
            "- Never store credentials, tokens, or one-off document text.\n"
            "- Keep values short.\n"
            'Prioritize what reduces future user steering."""\n',
            'MEMORY_GUIDANCE = ""  # CellScribe: no cross-document memory\n',
        ),
        (
            '    """Read MemoryStore and return a fenced, capped user profile block, or empty string."""\n',
            '    """Read MemoryStore and return a fenced, capped user profile block, or empty string."""\n'
            "    return \"\"  # CellScribe: memory must not carry between documents\n",
        ),
        (
            "Python on sheet data: write_formula_range of =PY (that tool's description).\n",
            "Python (=PY) only when the user explicitly asks for Python; otherwise use ordinary Calc formulas.\n",
        ),
        (
            'CALC_WORKFLOW = """WORKFLOW:\n'
            "1. get_sheet_summary for size/headers.\n"
            "   read_cell_range only for a small peek (headers or a few dozen cells).\n"
            "   A large range in chat overloads the model context — the truncated result is a peek only.\n"
            "   Row-wise ordinary Calc formulas: write_formula_range (fill-down adjusts relative refs).\n"
            "   Reductions that spill a small result: =PY into one empty cell outside the data.\n"
            "2. Do the work with tools. Use ranges, not one cell at a time.\n",
            'CALC_WORKFLOW = """WORKFLOW:\n'
            "1. The active sheet's values are already in the document context as CSV (\"Sheet Data\"): the first field of each line is the row number, the header line gives column letters. Use it directly. Do NOT call get_sheet_summary or read_cell_range for data that is already shown; use them only for other sheets or rows the context says are not shown.\n"
            "   QUESTIONS (summarise, explain, count, compare, find): answer in the chat in ONE reply from that data, with no tool calls. Take totals, counts and averages from the \"Column profile\" and \"Group totals\" (computed exactly) and column letters from the \"Column map\" — do not add up rows or count columns yourself. Never write helper cells or tables into the sheet just to answer a question; only change the sheet when the user asks for a change. Give exact figures, cite the rows/cells they come from, and never invent names, dates or values that are not in the data. If the layout is ambiguous, say what you assumed.\n"
            "2. CHANGES: do the work with tools in as few calls as possible. Use ranges, not one cell at a time. Row-wise formulas: write_formula_range into a 1-column (or 1-row) destination (fill-down adjusts relative refs). Totals and counts: ordinary formulas (SUM, COUNTIF, COUNTIFS, AVERAGE) in empty cells outside the data.\n"
            "   You get ONE tool call per turn, so make each call count: write a whole new table (title, header row, labels, formulas and its total row) in a SINGLE write_formula_range call with a 2D values array, and style a whole block in one set_style call. Never leave a formula pointing at a cell you have not written yet (write the total row in the same call as the share/percentage formulas that divide by it).\n"
            "   ORDER: finish everything the user asked for first (data, formulas, chart). Styling is optional: unless the user asks for formatting, use at most ONE set_style call (bold header) for a new table, and never restyle the user's existing data.\n",
        ),
        (
            "Do not explain, do the operation directly using tools. Perform as many steps as needed in one turn when possible.\n",
            "When the user asks for a change, make it directly with tools, batching work into as few tool calls as possible. When the user asks about the data, answer directly from the sheet data in context.\n",
        ),
        (
            "You are a LibreOffice Calc spreadsheet assistant who creates polished, professional, and colorful spreadsheets.\n",
            "You are a LibreOffice Calc spreadsheet assistant for busy office staff: you get exactly the requested work done quickly and correctly.\n",
        ),
    ],
    # --- Sidebar UI: plain progress, no raw tool lines or model reasoning -------
    "plugin/chatbot/tool_loop_actions.py": [
        (
            "def format_tool_running_ui(func_name: str, func_args: Mapping[str, Any]) -> tuple[str, str]:\n"
            '    """Status bar text and chat run-line when a tool starts executing."""\n',
            _FRIENDLY_PROGRESS
            + "def format_tool_running_ui(func_name: str, func_args: Mapping[str, Any]) -> tuple[str, str]:\n"
            '    """Status bar text and chat run-line when a tool starts executing."""\n'
            "    _cs = _cs_progress(func_name, func_args)  # CellScribe: plain language\n"
            '    return _cs, "\\u2022 " + _cs + "\\u2026\\n"\n',
        ),
        (
            '    """Chat append body for a tool result (error or success); does not mutate *result_data*."""\n'
            "    # crosshair: off\n"
            "    note = _result_note(result_data)\n",
            '    """Chat append body for a tool result (error or success); does not mutate *result_data*."""\n'
            "    # crosshair: off\n"
            "    note = _result_note(result_data)\n"
            "    # CellScribe: successes are already announced by the progress line; a\n"
            "    # failed step is retried by the model, so show one plain line, no JSON.\n"
            '    if result_data.get("status") == "error":\n'
            '        return "\\u2022 That step didn\'t work; trying another way\\u2026\\n"\n'
            '    if result_data.get("status") == "incomplete":\n'
            '        return ""\n'
            '    if not (func_name == "web_research" or (func_name in DELEGATE_GATEWAY_TOOL_NAMES and domain_from_delegate_args(func_args) == "web_research")):\n'
            '        return ""\n',
        ),
    ],
    "plugin/chatbot/rich_text_paste.py": [
        (  # a turn that only calls a tool is already shown by its progress line
            '            elif msg.get("tool_calls"):\n'
            '                text += "\\n%s [Thinking...]" % _("Assistant:")\n',
            '            elif msg.get("tool_calls"):\n'
            "                pass  # CellScribe: no [Thinking...] rows\n",
        ),
        (
            '            elif msg.get("tool_calls"):\n'
            '                items.append(("assistant", "[Thinking...]"))\n',
            '            elif msg.get("tool_calls"):\n'
            "                pass  # CellScribe: no [Thinking...] rows\n",
        ),
    ],
    "plugin/chatbot/panel.py": [
        (
            '    def _append_response(self, text: str, is_thinking: bool = False, role: str = "assistant") -> None:\n'
            '        """Project ``text`` onto the session, then draw that list.\n',
            '    def _append_response(self, text: str, is_thinking: bool = False, role: str = "assistant") -> None:\n'
            '        """Project ``text`` onto the session, then draw that list.\n'
            "\n"
            "        CellScribe: the model's private reasoning is not shown in the chat.\n",
        ),
        (
            "        from plugin.chatbot.tool_loop_actions import TurnController, current_turn\n\n"
            "        turn = current_turn(self)\n"
            "        session = self.session\n",
            "        if is_thinking:  # CellScribe: hide [Thinking] ... /thinking blocks\n"
            "            return\n"
            "        from plugin.chatbot.tool_loop_actions import TurnController, current_turn\n\n"
            "        turn = current_turn(self)\n"
            "        session = self.session\n",
        ),
    ],
    "plugin/chatbot/panel_factory.py": [
        (
            "        header_buttons: list[tuple[str, Any, Any, Any, Any]] = [\n",
            "        # CellScribe: office users get Ask/Send/Stop/Clear; developer tools\n"
            "        # (Python, LaTeX, file search, voice, mode/model/image pickers) are hidden.\n"
            "        for _cs_name in (\"btn_python\", \"btn_latex\", \"btn_search\", \"chk_voice\", \"chat_mode_selector\",\n"
            "                         \"model_label\", \"model_selector\", \"reasoning_effort_selector\", \"image_model_selector\",\n"
            "                         \"base_size_label\", \"base_size_input\", \"aspect_ratio_selector\"):\n"
            "            _cs_ctrl = controls.get(_cs_name)\n"
            "            if _cs_ctrl is not None and hasattr(_cs_ctrl, \"setVisible\"):\n"
            "                _cs_ctrl.setVisible(False)\n\n"
            "        header_buttons: list[tuple[str, Any, Any, Any, Any]] = [\n",
        ),
    ],
    "plugin/chatbot/panel_wiring.py": [
        (
            '        set_control_visible(controls.get("model_label"), not is_image)\n'
            '        set_control_visible(controls.get("model_selector"), not is_image)\n'
            '        set_control_visible(controls.get("reasoning_effort_selector"), not is_image)\n'
            '        set_control_visible(controls.get("image_model_selector"), is_image)\n'
            '        set_control_visible(controls.get("aspect_ratio_selector"), is_image)\n'
            '        set_control_visible(controls.get("base_size_input"), is_image)\n'
            '        set_control_visible(controls.get("base_size_label"), is_image)\n',
            "        # CellScribe: model and image pickers stay hidden (admin sets the model).\n"
            '        for _cs_name in ("model_label", "model_selector", "reasoning_effort_selector", "image_model_selector",\n'
            '                         "aspect_ratio_selector", "base_size_input", "base_size_label"):\n'
            "            set_control_visible(controls.get(_cs_name), False)\n",
        ),
    ],
    # Hidden bottom controls sit inside the button row so they leave no empty
    # band; the menu button moves next to Settings.
    "Dialogs/ChatPanelDialog.xdl": [
        (old_new)
        for old_new in _XDL_MOVES
    ],
    "registry/org/openoffice/Office/UI/Sidebar.xcu": [
        (
            '<value xml:lang="en-US">WriterAgent</value>',
            '<value xml:lang="en-US">CellScribe</value>',
        ),
        (  # the separate "Python" sidebar deck is a developer tool: never shown
            'python_32.png</value>\n'
            '                </prop>\n'
            '                <prop oor:name="ContextList">\n'
            '                    <value oor:separator=";">com.sun.star.sheet.SpreadsheetDocument, any, visible; com.sun.star.text.TextDocument, any, visible; com.sun.star.text.WebDocument, any, visible; com.sun.star.text.GlobalDocument, any, visible;</value>',
            'python_32.png</value>\n'
            '                </prop>\n'
            '                <prop oor:name="ContextList">\n'
            '                    <value oor:separator=";">com.sun.star.cellscribe.Hidden, any, visible;</value>',
        ),
        (
            '<value xml:lang="en-US">Chat with Document</value>',
            '<value xml:lang="en-US">Ask CellScribe</value>',
        ),
    ],
    # --- Bound the agent loop -------------------------------------------------
    "plugin/chatbot/module.yaml": [
        ("  max_tool_rounds:\n    type: int\n    default: 15\n", f"  max_tool_rounds:\n    type: int\n    default: {MAX_TOOL_ROUNDS}\n"),
    ],
    "plugin/_manifest.py": [
        (
            "'config': {   'max_tool_rounds': {   'type': 'int',\n"
            "                                             'default': 15,\n",
            "'config': {   'max_tool_rounds': {   'type': 'int',\n"
            f"                                             'default': {MAX_TOOL_ROUNDS},\n",
        ),
    ],
}


def apply(filename: str, data: bytes) -> bytes:
    """Return *data* with this file's patches applied (unchanged if it has none)."""
    patches = PATCHES.get(filename)
    if not patches:
        return data
    text = data.decode("utf-8")
    for old, new in patches:
        count = text.count(old)
        if count != 1:
            raise ValueError(
                f"CellScribe patch for {filename} matched {count} times (expected 1); "
                "the vendored WriterAgent changed — update gateway/patches.py"
            )
        text = text.replace(old, new)
    return text.encode("utf-8")
