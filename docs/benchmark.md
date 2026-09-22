# The benchmark

How much better does a model answer questions about a drawing when the page label is
available? `planlabel-bench` measures it on the sample sheets.

```bash
make samples                       # the drawings the questions are about
make bench-dry                     # how many questions would reach the API; asks none
make bench                         # both conditions, the report, the README table
```

`make bench` is the only target in the repository that calls a paid API, and CI never
runs it.

## What is compared

Two conditions, one prompt template. Both send the same system prompt, the page
rendered at 150 dpi, the text a PDF reader extracts from it, and the same question.

| Condition | What the model gets |
| --- | --- |
| `plain` | The page image and its extracted text. |
| `labelled` | The same, then a `get_page_label` tool call and its result: the page's label as canonical JSON. |

The label arrives as a tool result because that is how it reaches a model in practice,
through the MCP server's `get_label` tool. The model is asked for `{"answer": "..."}`
through structured output, and the answer is scored against the key.

## The questions

`bench/questions.jsonl` is merged from each `samples/<name>/groundtruth.jsonl` by
`planlabel-bench questions`, and committed. Every answer is taken from the IFC model
the sheet was exported from, never from reading the drawing, so the key cannot share a
mistake with the thing it grades.

| Category | Asks for | Scored |
| --- | --- | --- |
| `count` | How many walls, doors, windows, columns, beams or slabs the sheet draws | numeric |
| `sheet` | The scale, and the kind of drawing | numeric, exact |
| `dimension` | A grid spacing or a storey height, in millimetres | numeric |
| `callout` | The sheet a callout points to | exact |
| `grid` | The grid axes drawn | exact, as a set |
| `tag` | The IFC class of the element carrying a mark | exact |
| `section` | A member's cross-section, or a slab's thickness | exact, numeric |
| `level` | The elevation of a level mark, in metres | numeric |
| `model` | The IFC GlobalId of a marked element | exact; **label only** |

Every category but `model` is about something the sheet prints or draws, so a careful
reader of the page alone could answer it; the label is meant to make that reliable, not
possible. `model` asks for something no drawing prints, and is reported apart so it
cannot inflate the headline number.

## Scoring

- **Numbers** are right within 1 % of the key. `8000`, `8,000`, `8 000`, `8.000,0` and
  `8 m` all read as eight thousand millimetres; `1:50` reads as a scale of 50. A count
  of zero has no tolerance.
- **Lists** are right when they hold the same items in any order: `A, B, 1 and 2`.
- **Everything else** is right when it matches exactly, ignoring case and wrapping
  quotes: `ifcwall` is `IfcWall`, `IfcWallStandardCase` is not.
- A refusal, or output cut off at `max_tokens`, is scored wrong.

The parsing is deliberately narrow, so a wrong answer cannot be argued into a right one.
`tests/test_bench.py` lists what is accepted.

## Running it

```bash
cp .env.example .env               # then put your key in it
uv run planlabel-bench run --condition plain    --model claude-opus-5 --n 100
uv run planlabel-bench run --condition labelled --model claude-opus-5 --n 100
uv run planlabel-bench report --model claude-opus-5
uv run planlabel-bench readme --model claude-opus-5
```

- **Credentials.** `ANTHROPIC_API_KEY` from the environment, else from `.env`, which is
  git-ignored. With neither, the SDK uses an `ant auth login` profile if there is one.
  No key ever belongs in the repository.
- **Model.** `claude-opus-5` by default; `--model` takes any model id, and
  `BENCH_MODEL=... make bench` does the same. `--effort` sets `output_config.effort`.
- **Cache.** Every response is cached in `bench/cache/` (git-ignored) under the
  condition, the model, the question and a digest of the whole request. A repeated run
  asks nothing and reproduces its table exactly; a run that fails part-way resumes where
  it stopped; a changed prompt or rebuilt sample is never answered from a stale entry.
  A fully cached run needs neither credentials nor the `bench` extra.
- **Prompt caching.** The cache breakpoint sits after the page material and before the
  question, so every question about a sheet after the first reads the page from the
  prompt cache.
- **Fallbacks.** Server-side refusal fallbacks are deliberately not enabled: a
  benchmark measures one model, and an answer served by another would be credited to
  the wrong one. A refusal is logged and scored wrong.

## Output

| File | Committed | What it is |
| --- | --- | --- |
| `bench/questions.jsonl` | yes | The question set. |
| `bench/results/<model>-<condition>.jsonl` | yes | One scored record per question: the answer given, tokens, estimated cost, latency. |
| `bench/results/<date>-<model>.md` | yes | The report: accuracy per category under each condition, the difference, cost and latency, and every answer. |
| `bench/cache/` | no | The raw responses. |

`planlabel-bench readme` writes the summary table into the top-level README, between the
`BENCHMARK:START` and `BENCHMARK:END` markers.

Costs are estimated from list prices in `planlabel/bench/pricing.py`; a model not in
that table is reported without a cost rather than with a guessed one.

## Reading the result

The samples are three clean sheets drawn by this project's own exporter. They show
whether a label helps on drawings where everything is legible; they say nothing about a
scanned sheet, a crowded one, or an office's own conventions. Add questions for real
drawings to a separate question file (not committed: the drawings are someone else's)
and pass it with `--questions`.
