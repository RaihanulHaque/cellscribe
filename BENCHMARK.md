# Benchmark: open-weight models for Libre Thinker

Can a model small enough to host in-house do the work Libre Thinker asks of it? This page records
one benchmark run on 17 November 2026 against the same office tasks used to validate the gateway.

## Summary

- **Qwen 3.8 27B matched the hosted model.** Both Qwen 3.8 27B and DeepSeek V4.1 Flash (the current
  default) passed 21 of 22 tasks.
- **Nothing smaller came close.** The best of the rest scored 17 of 22.
- **A 27B-class model looks viable for self-hosting**, but this test measured answer quality only.
  It did not measure speed on your own hardware (see [Limitations](#limitations)).

## Results

Scored out of 22 tasks (11 Calc, 11 Writer). Cost and time are totals for all tasks; time is model
and tool time only, not opening files or restarting LibreOffice.

| Model | Passed | Calc | Writer | Cost | Time | Tool errors |
| --- | --- | --- | --- | --- | --- | --- |
| Qwen 3.8 27B (`qwen/qwen3.8-27b`) | **21**/22 | 11/11 | 10/11 | $0.14 | 7.8 min | 0 |
| DeepSeek V4.1 Flash (`deepseek/deepseek-v4.1-flash-1017`) | **21**/22 | 10/11 | 11/11 | $0.09 | 6.7 min | 3 |
| Qwen 3.6 35B-A3B (`qwen/qwen3.6-35b-a3b`) | **17**/22 | 8/11 | 9/11 | $0.09 | 17.0 min | 13 |
| Gemma 4 31B (`google/gemma-4-31b-it`) | **17**/22 | 9/11 | 8/11 | $0.10 | 4.6 min | 11 |
| gpt-oss-20B (`openai/gpt-oss-20b`) | **14**/22 | 5/11 | 9/11 | $0.05 | 5.0 min | 4 |
| Qwen 3.5 9B (`qwen/qwen3.5-9b`) | **14**/22 | 9/10 | 5/11 | $0.17 | 24.4 min | 9 |
| Gemma 4 26B-A4B (`google/gemma-4-26b-a4b-it`) | **14**/22 | 6/11 | 8/11 | $0.07 | 8.9 min | 10 |
| Ministral 14B (`mistralai/ministral-14b-2512`) | **13**/22 | 4/11 | 9/11 | $0.11 | 13.2 min | 4 |
| Mistral Small 3.2 (`mistralai/mistral-small-3.2-24b-instruct`) | **11**/22 | 6/11 | 5/11 | $0.04 | 12.9 min | 14 |

Qwen 3.5 9B's Calc chart task never recorded a result (the run hit its time limit), so its Calc
figure is out of 10 and its total is a floor.

## Qwen 3.8 27B against DeepSeek V4.1 Flash, task by task

`P` = passed, `F` = failed. Cost is what OpenRouter billed for that task (in USD).

| Task | Qwen 3.8 27B | DeepSeek V4.1 Flash |
| --- | --- | --- |
| Calc: Management summary | P, 10 s, $0.0042 | F, 8 s, $0.0030 |
| Calc: Outstanding dues | P, 6 s, $0.0187 | P, 13 s, $0.0006 |
| Calc: Filtered count | P, 4 s, $0.0152 | P, 4 s, $0.0038 |
| Calc: Monthly revenue | P, 5 s, $0.0017 | P, 6 s, $0.0007 |
| Calc: VAT columns | P, 21 s, $0.0075 | P, 15 s, $0.0024 |
| Calc: Totals row | P, 9 s, $0.0057 | P, 10 s, $0.0008 |
| Calc: Region summary sheet | P, 28 s, $0.0102 | P, 32 s, $0.0071 |
| Calc: Sort | P, 27 s, $0.0027 | P, 18 s, $0.0010 |
| Calc: Highlight unpaid | P, 11 s, $0.0042 | P, 30 s, $0.0067 |
| Calc: Professional formatting | P, 8 s, $0.0028 | P, 8 s, $0.0038 |
| Calc: Chart | P, 86 s, $0.0071 | P, 22 s, $0.0039 |
| Writer: Summarise report | F, 15 s, $0.0015 | P, 3 s, $0.0009 |
| Writer: Ask a question | P, 4 s, $0.0008 | P, 2 s, $0.0033 |
| Writer: Executive summary | P, 36 s, $0.0089 | P, 50 s, $0.0060 |
| Writer: Proofread memo | P, 21 s, $0.0078 | P, 11 s, $0.0038 |
| Writer: Formal tone | P, 26 s, $0.0070 | P, 62 s, $0.0050 |
| Writer: Notes to minutes | P, 13 s, $0.0058 | P, 9 s, $0.0032 |
| Writer: Letter from points | P, 10 s, $0.0032 | P, 10 s, $0.0053 |
| Writer: Translate to Bangla | P, 32 s, $0.0087 | P, 15 s, $0.0022 |
| Writer: Risk table | P, 55 s, $0.0092 | P, 35 s, $0.0174 |
| Writer: Apply headings | P, 28 s, $0.0062 | P, 7 s, $0.0043 |
| Writer: Find and replace | P, 12 s, $0.0026 | P, 33 s, $0.0013 |

- Qwen's miss: *Summarise report* left out the 3.1% mortality figure.
- DeepSeek's miss: *Management summary* did not state the unpaid-invoice count (26).
- Both are omissions rather than wrong answers.

## Pass/fail by model and task

| Task | Qwen 3.8 27B | DeepSeek V4.1 Flash | Qwen 3.6 35B-A3B | Gemma 4 31B | gpt-oss-20B | Qwen 3.5 9B | Gemma 4 26B-A4B | Ministral 14B | Mistral Small 3.2 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Calc: Management summary | P | F | P | F | P | P | F | F | F |
| Calc: Outstanding dues | P | P | F | F | F | F | F | F | F |
| Calc: Filtered count | P | P | P | P | F | P | P | F | F |
| Calc: Monthly revenue | P | P | P | P | F | P | P | P | P |
| Calc: VAT columns | P | P | F | P | F | P | P | F | P |
| Calc: Totals row | P | P | P | P | F | P | F | F | F |
| Calc: Region summary sheet | P | P | P | P | P | P | F | F | P |
| Calc: Sort | P | P | F | P | P | P | P | P | P |
| Calc: Highlight unpaid | P | P | P | P | F | P | P | F | F |
| Calc: Professional formatting | P | P | P | P | P | P | P | P | P |
| Calc: Chart | P | P | P | P | P | - | F | P | P |
| Writer: Summarise report | F | P | P | P | F | F | F | P | F |
| Writer: Ask a question | P | P | P | P | P | P | P | P | P |
| Writer: Executive summary | P | P | P | F | F | F | F | P | P |
| Writer: Proofread memo | P | P | F | F | P | P | P | P | F |
| Writer: Formal tone | P | P | P | P | P | F | P | P | P |
| Writer: Notes to minutes | P | P | P | F | P | F | P | P | P |
| Writer: Letter from points | P | P | P | P | P | P | P | P | P |
| Writer: Translate to Bangla | P | P | F | P | P | P | P | P | F |
| Writer: Risk table | P | P | P | P | P | F | P | P | F |
| Writer: Apply headings | P | P | P | P | P | P | F | F | F |
| Writer: Find and replace | P | P | P | P | P | F | P | F | F |

## How the test was run

- **Tasks.** The same 22 office tasks as the README's [Tested behaviour](README.md#tested-behaviour):
  11 on `samples/sales_register.csv` (150 invoices) and 11 on the `.docx` files in `samples/`.
  Each task has an automatic check against figures computed from the source data.
- **Agent.** The real WriterAgent tools and system prompt, driven through LibreOffice 26.2 exactly as
  the sidebar does it: one tool call per model turn, document context refreshed after each change,
  at most 8 tool rounds.
- **Models.** Called directly on OpenRouter (not through the gateway), with the same tools for every
  model. One run per model, in an isolated LibreOffice profile on a copy of the files.
- **Parallel runs.** Several LibreOffice instances ran at once, each with its own profile and its
  own copy of the sample files.

## What the failures looked like

- **Claimed work that was not done.** Several smaller models reported a finished table, heading style
  or replacement that was not in the document. Ministral 14B and Mistral Small 3.2 also stated wrong
  numbers confidently (14 and 15 Dhaka invoices in March; the correct answer is 11).
- **Raw tool calls in the reply.** gpt-oss-20B printed an internal tool call as its answer on one
  task.
- **Multi-step Calc edits.** Sort, a new summary sheet and a chart separated the models most. The
  mixture-of-experts models (Gemma 4 26B-A4B, Qwen 3.6 35B-A3B) hit the most tool errors.
- **Checker limits.** Some failures are the check being strict (for example, numbers written as
  "2 383 004" with spaces, or an answer that omits a required figure). gpt-oss-20B's Calc score is
  understated by about two points for the spacing reason.

## Limitations

- **One run per model.** Differences of one or two tasks are within run-to-run noise.
- **Quality, not speed.** The models ran on OpenRouter. How fast they would answer on hardware you
  own was not measured. A 27B model needs roughly 17 to 20 GB of memory at 4-bit, and each request
  carries about 20,000 tokens of sheet context, so a GPU is likely needed.
- **Hosted, so not private.** Running through OpenRouter, as here, does not stop data leaving the
  company. The benchmark shows which model to host, not how to host it.
- **Different from the README.** The README reports 24 of 24 for DeepSeek, from an earlier run in
  which two results were confirmed by hand. This run is fully automatic with stricter checks, so
  21 of 22 is the like-for-like figure.
- **Prices move.** OpenRouter charges DeepSeek V4.1 Flash more at peak hours, so its cost depends on
  when it ran. Qwen 3.8 27B is a reasoning model and spends tokens on simple questions, which makes
  its per-task cost uneven.
- **Narrow task set.** English and Bangla office tasks on one sales register and five documents.
  Long documents, tracked changes and several open files were not tested.

## Suggested next steps

1. Repeat Qwen 3.8 27B two or three times to confirm the score is stable.
2. Test it on a rented GPU or a small on-site server to measure real speed and memory.
3. Test the nearby-files mode for questions that need data from several spreadsheets.
