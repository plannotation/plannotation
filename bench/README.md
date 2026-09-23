# Plannotation benchmark

The question it answers: *how much better does a model understand a drawing when its
plannotation is available?* See [`docs/benchmark.md`](../docs/benchmark.md) for the method.

- `questions.jsonl` — merged from each `samples/*/groundtruth.jsonl` by
  `plannotation-bench questions`, so every answer is derived from the source model rather
  than hand-written.
- `results/` — the run logs (`<model>-<condition>.jsonl`) and the reports
  (`<date>-<model>.md`). The table in the top-level `README.md` is written from them.
- `cache/` — cached responses, git-ignored.

```bash
make bench-dry    # asks nothing
make bench        # needs ANTHROPIC_API_KEY in .env (see .env.example)
```

No key ever enters the repository, and the test suite never calls a live API.
