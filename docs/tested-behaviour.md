# Tested behaviour

Tested on 16 November 2026 with `deepseek/deepseek-v4.1-flash-1017` through the gateway. Each task
ran through the real WriterAgent agent (same prompt, tools and one-tool-per-call loop as the
sidebar) in LibreOffice 26.2, on a fresh copy of the file, and the resulting document was checked
automatically against figures computed from the source data. Several tasks were also run by hand in
the sidebar.

**Calc** ([`samples/sales_register.csv`](../samples/sales_register.csv): 150 invoices, Jan–Mar 2026),
12 of 12 correct (10 by the automatic checks; the other two were confirmed by hand, see below):

| Task | Steps | Time |
| --- | --- | --- |
| Management summary; outstanding dues; filtered count; revenue per month (questions) | 1 each | 2–9 s |
| VAT and "Total incl. VAT" columns for 150 rows | 6 | 15 s |
| Totals row | 2 | 5 s |
| New "Region Summary" sheet with live formulas | 4 | 13 s |
| Sort by amount, rows kept intact | 2 | 10 s |
| Highlight the 26 unpaid invoices | 2 | 30 s |
| Bold header, `#,##0` amounts, frozen header row | 4 | 17 s |
| Bar chart of revenue by region | 3 | 17 s |
| A region's branches and amounts by category, with chart | 3 | 24 s |

The two hand-checked results: the management summary gave unpaid invoices as an amount
(৳2,383,004) where the check looked for the count, and the region breakdown built branch totals
plus a category table where the check expected a branch × category grid. All figures were exact.

**Writer** ([`samples/`](../samples/) `.docx` files), 12 of 12 correct:

| Task | Steps | Time |
| --- | --- | --- |
| Summarise a report; answer a question about it | 1 | 2–4 s |
| Notes → meeting minutes with action table | 2 | 7 s |
| Letter from bullet points; formal rewrite of one paragraph | 2 | 4–13 s |
| Proofread a memo (9 planted errors) | 7 | 30 s |
| Translate a memo / a 2-page report with a table into Bangla | 3 | 10–21 s |
| Add a risk table in the right section; apply heading styles | 6–8 | 14–32 s |
| Find and replace across the document | 3 | 8 s |
| Add an executive summary | 14 | 45 s |

Typical cost: under one US cent per task (all 24 tasks together: about $0.20). Questions are answered without changing the document.

For how self-hostable open-weight models compare on these same tasks, see [BENCHMARK.md](../BENCHMARK.md).
