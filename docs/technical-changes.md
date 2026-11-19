# What Libre Thinker Agent changes in WriterAgent

`vendor/WriterAgent.oxt` is kept byte-for-byte as pinned. When a PC downloads `LibreThinkerAgent.oxt`, the
gateway builds it from that package: it adds the token-entry menu item (`gateway/packager.py`),
removes WriterAgent's update feed so the Extension Manager cannot swap the build for upstream, and
applies the exact search-and-replace patches in `gateway/patches.py`. Each patch must match exactly
once, so a WriterAgent upgrade that changes the code underneath fails the build instead of shipping
a half-patched extension. The extension keeps WriterAgent's identifier, so it installs over an
existing WriterAgent.

| Area | WriterAgent default | Libre Thinker Agent | Why |
| --- | --- | --- | --- |
| Calc: what the model sees | sheet size and row 1 only | the sheet's values as CSV, a column map (letter → header), an exact per-column profile (sums, min/max, counts, date ranges), group totals by category and month, and other sheets when they fit; total rows are excluded from figures | The model never saw the data, so summaries were invented or built from 80-cell reads. Models also miscount wide rows and mis-add long columns. |
| Calc prompt | read with tools, compute with `=PY`, "colorful spreadsheets" | answer questions from the data in one reply; write whole tables in one call with ordinary formulas; requested work before styling | `=PY` needs Python packages office PCs lack; decoration used up to 9 steps per task and starved the request. |
| `sort_range` | silently did nothing | sorts | pyuno passed the sort fields as an untyped sequence, which Calc ignores. |
| `set_style` | no number formats, no freeze panes | `number_format` (e.g. `#,##0`) and `freeze_at` (e.g. `A2`) | Routine office formatting the model could not do at all. |
| Calc tool arguments | a list sent as a JSON string, or one value per listed cell, was rejected | accepted | Each rejection cost a full step. |
| `read_cell_range` limit | 80 cells | 400 cells | Fewer steps when the model does read. |
| Writer: what the model sees | text read by cursor steps, which undercount tables | full text whenever the cursor read comes up short (documents without tracked changes) | Everything after a table was silently missing. Models then "repaired" correct meeting minutes by deleting sections they could not see (0/4 → 4/4 after the fix). |
| Writer prompt | "thoughtful use of color", no notion of a step budget | requested work first; one write per new part; whole-document translations and rewrites in one write; named styles only; no placeholders; check every fact is kept | Minutes took 20 steps and 5 minutes of colours and custom styles; translation went paragraph by paragraph and would stop half-way on long documents. |
| Writer full rewrites | no check | the tool reports passages a rewrite dropped (fuzzy match; translations and restructures are not flagged) | A safety net so a rewrite cannot silently lose content. |
| Context size | 8,000 characters | 60,000 characters (~15k tokens) | Lets the model see a whole working document. |
| Steps per request | 15 | 20 | The sidebar runs one tool per model call; a table plus a chart needs about 10. |
| Memory | one global `USER.md` added to every prompt | off | A preference saved in one document leaked into unrelated ones. Chat history stays per document. |
| Sidebar | raw `[Running tool: …]` lines, model reasoning, ~24 controls, "WriterAgent" | plain progress lines, no reasoning text, Ask/Send/Stop/Clear plus Settings and menu, Libre Thinker Agent branding and greeting | Office users saw developer output. Model, mode and image pickers, Python, LaTeX, search and voice are hidden, not removed. |
