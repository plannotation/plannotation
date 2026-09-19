# Contributing to PlanLabel

Thanks for your interest. PlanLabel is a specification first and an implementation
second, so the bar for changes that touch the on-disk format is deliberately high.

By contributing you agree that your contribution is licensed under the
[Apache License 2.0](LICENSE), and you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Getting set up

```bash
uv sync --all-extras          # create the environment from uv.lock
uv run planlabel --version    # smoke test
make check                    # format, lint, type-check, test
```

`make check` is the single gate. It runs exactly what CI runs:

| Step | Command |
| --- | --- |
| Format | `ruff format --check .` |
| Lint | `ruff check .` |
| Types | `mypy --strict planlabel` |
| Tests | `pytest -q` |

Install the hooks so you find problems before CI does:

```bash
uv run pre-commit install
```

## The rules that are not negotiable

These come from the project's design constraints. A pull request that breaks one
of them will be rejected regardless of how good the rest of it is.

### 1. The PDF is the leading document; the label is auxiliary

PlanLabel never alters how a drawing looks or prints. Concretely: **never** modify
page content streams, **never** add visible marks, **never** delete third-party
attachments or metadata. The appearance-guarantee test rasterises every page before
and after and asserts the pixel arrays are *identical* — not merely similar.

When the specification is ambiguous, choose the reading that keeps the PDF leading
and the label auxiliary, then write that decision into `spec/SPEC.md`.

### 2. Licence policy

The core and every open surface are Apache-2.0. Dependencies must be **MIT, BSD,
Apache or MPL**.

- **LGPL** is acceptable only as an unmodified, dynamically imported dependency
  (today: `ifcopenshell`, `cairosvg`).
- **GPL** is acceptable only as an external command-line program invoked over a
  process boundary and never linked (today: veraPDF, Inkscape, tesseract via
  ocrmypdf). Every such tool is optional; the code must degrade gracefully when it
  is missing, and CI must never fail because a runner lacks one.
- **AGPL is forbidden outright.** In particular, do not add PyMuPDF (`fitz`) for
  any purpose. Use `pikepdf` for PDF objects, `pypdfium2` for rendering and text,
  and `pdfplumber` for vectors and characters.

**Ask before adding any dependency, and state its licence in the pull request.**
Run `make licenses` to regenerate `THIRD_PARTY_LICENSES.md` when the set changes.

### 3. Provenance is never guessed

Every element and annotation carries `provenance`. `authored` means it was written
from the model by the authoring tool. `inferred` means it was reconstructed from
the drawing by a reader, and it **must** carry a `confidence`. Never label
reconstructed data as authored.

### 4. The vocabulary is IFC's

Class names, GlobalIds and property-set names come from IFC, never from a vendor.
Where [SWAPP ifc-docs](https://github.com/SWAPP-eu) already names a concept, reuse
SWAPP's name rather than inventing one.

### 5. Determinism

Sorted JSON keys, two-space indent, LF, UTF-8, numbers to at most three decimals.
Fixed seeds. Injectable timestamps. Golden files live in `tests/golden/`. Tests
never touch the network and never depend on wall-clock time.

### 6. Repository hygiene

- Type hints everywhere; `mypy --strict` must pass.
- Geometry code is written as pure functions.
- Logging goes through `logging`. `print` is for the CLI only.
- No committed binaries over 200 kB.
- English only in code, documentation and commit messages. German terms are allowed
  inside enum values, with English descriptions.
- No personal data, employer material or application material anywhere in the repo.
  `tests/fixtures/realworld/` is git-ignored by design — real drawings never get
  committed.

## Changing the schema

`planlabel/schema/planlabel-0.1.json` is the single source of truth. The pydantic
models in `planlabel/model.py` follow the schema, not the other way round.

A schema change needs, in the same pull request:

1. The schema edit, with `$id` bumped if the change is breaking.
2. Regenerated and re-verified pydantic models — round-tripping must reproduce the
   canonical JSON byte for byte.
3. Positive **and** negative fixtures covering the new rule.
4. The corresponding normative text in `spec/SPEC.md`.
5. A `CHANGELOG.md` entry.

Specification changes belong in their own commits, separate from implementation.

## Commits and pull requests

[Conventional Commits](https://www.conventionalcommits.org/), scoped to the area:

```
feat(pdf): embed page-level /AF with /AFRelationship /Data
fix(units): flip SVG y-axis before computing paper bbox
docs(spec): define conformance levels L1-L3
```

Development proceeds in numbered phases (see the project design brief). One phase is
one or more commits, and history is never squashed across a phase boundary.

Before opening a pull request: `make check` is green, new behaviour has tests, and
anything normative is reflected in `spec/SPEC.md`.

## Reporting a security issue

Please do not open a public issue. Use
[private vulnerability reporting](https://github.com/srtgn/planlabel/security/advisories/new).

Note the threat model in `spec/SPEC.md`: labels are **data, never executable**, and
a conforming reader is required to validate a label against the schema before
trusting any value in it.
