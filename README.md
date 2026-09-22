# PlanLabel

[![check](https://github.com/srtgn/planlabel/actions/workflows/check.yml/badge.svg)](https://github.com/srtgn/planlabel/actions/workflows/check.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

**An open drawing-semantics sidecar for 2D construction drawings.**

A PDF drawing is a picture as far as a machine is concerned. PlanLabel attaches a
small JSON *label* to every drawing page that says what is actually on it: which
sheet it is, which viewports it contains and how paper maps to model space, which
elements appear (IFC class, GlobalId, mark, outline on the paper), and which
annotations are present — dimensions and what they measure, tags and what they show,
callouts and where they point, grids, levels. The page looks and prints exactly as
it did before, byte-for-byte identical on screen and on paper. Any program can read
the label without the software that authored the drawing.

> **Status: early. Phase 0 (scaffold) is complete.** The schema, the PDF carrier and
> the tooling land in the phases that follow. Nothing here is stable yet, and the
> `0.1` schema is a draft.

## One core, three surfaces

| Surface | Package | What it is |
| --- | --- | --- |
| **Library + CLI** | `planlabel` | Write, read, validate, inspect and export labels. |
| **MCP server** | `planlabel-mcp` | Exposes labelled projects to Claude Desktop, ChatGPT, Copilot and other MCP hosts. |
| **Inference** | `planlabel infer` | Reconstructs labels for legacy PDFs, marked `inferred` with a confidence. |

## 60-second demo

> Not yet functional — the commands below land in Phases 4 and 5.

```bash
uvx planlabel samples build
uvx planlabel inspect samples/positionsplan/sheet.labelled.pdf
```

## Why a label helps

<!-- BENCHMARK:START -->
> **Not measured yet.** The harness is in place: 38 questions about the three sample
> sheets, each answered by the same model with and without the page label, and each
> keyed to the IFC model the sheet was drawn from ([method](docs/benchmark.md)).
> `make bench` runs it with an API key in `.env` and writes the measured table here.
> Until then there is deliberately no number in this section.
<!-- BENCHMARK:END -->

## Who did half of this already

<!-- PRIOR-ART:START -->
> Placeholder — filled in for the v0.1.0 release. PlanLabel deliberately sits on top
> of existing work rather than beside it; this table credits what each prior effort
> solved and names the gap PlanLabel closes.

| Prior work | What it already solves | What is still missing |
| --- | --- | --- |
| PDF 2.0 Associated Files (ISO 32000-2) | _pending_ | _pending_ |
| PDF Declarations (PDF Association) | _pending_ | _pending_ |
| IfcOpenShell SVG serializer | _pending_ | _pending_ |
| SWAPP ifc-docs | _pending_ | _pending_ |
<!-- PRIOR-ART:END -->

## Design commitments

- **The page is the leading document.** Content streams are never touched, no visible
  mark is ever added, and no third-party attachment or metadata is ever removed. The
  test suite rasterises every page before and after and asserts the pixels are
  *identical*.
- **Provenance is explicit.** Every element and annotation says whether it was
  `authored` from the model or `inferred` from the drawing, and everything inferred
  carries a confidence.
- **The vocabulary is IFC's** — class names, GlobalIds and property-set names — never
  a vendor's.
- **Labels are data, never executable.** Readers must validate before trusting.

## Install

```bash
uv sync --all-extras
uv run planlabel --version
```

Requires Python 3.11 or newer. See [CONTRIBUTING.md](CONTRIBUTING.md) to set up a
development environment.

## Documentation

| | |
| --- | --- |
| [`spec/SPEC.md`](spec/SPEC.md) | The normative specification. |
| [`planlabel/schema/`](planlabel/schema/) | The JSON Schema — single source of truth. |
| [`docs/`](docs/) | Guides, and design notes for the Revit, AutoCAD and Tekla adapters. |
| [`samples/`](samples/) | Generated sample drawings with ground truth. |

## Licence

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).

Dependencies are restricted to MIT, BSD, Apache and MPL, with LGPL permitted only as
an unmodified dependency and GPL only as an optional external command-line tool.
PlanLabel does not depend on any AGPL component. The policy, and the reasoning behind
it, is in [CONTRIBUTING.md](CONTRIBUTING.md).

Contributions are welcome — please read [CONTRIBUTING.md](CONTRIBUTING.md) and the
[Code of Conduct](CODE_OF_CONDUCT.md) first.
