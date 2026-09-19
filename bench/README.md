# PlanLabel benchmark

Landing in **Phase 8**.

The question it answers: *how much better does a model understand a drawing when the
page label is available?*

- `questions.jsonl` — merged from each `samples/*/groundtruth.jsonl`, so every answer
  is derived from the source model rather than hand-written.
- Two conditions, one prompt template:
  - `plain` — the page rendered at 150 dpi, plus its extracted text.
  - `labelled` — the same, plus the PlanLabel page label supplied as a tool result.
- Scoring is exact for categorical answers and numeric within 1% for measurements.
  Cost and latency are logged; responses are cached on
  `(condition, model, question)` so a re-run is free and deterministic.
- `results/` holds the generated reports; the table in the top-level `README.md` is
  written from them between the `BENCHMARK:START` and `BENCHMARK:END` markers.

An API key is read from `.env`, which is git-ignored. No key ever enters the
repository, and the test suite never calls a live API.
