# Plannotation

[![check](https://github.com/plannotation/plannotation/actions/workflows/check.yml/badge.svg)](https://github.com/plannotation/plannotation/actions/workflows/check.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

**An open drawing-semantics sidecar for 2D construction drawings.**

A PDF drawing is a picture as far as a machine is concerned. Plannotation attaches a small
JSON *label* to every drawing page that says what is actually on it: which sheet it
is, which viewports it holds and how their paper maps to model coordinates, which
elements are drawn — IFC class, GlobalId, mark, outline on the paper — and which
annotations are present: dimensions and what they measure, marks and what they show,
levels, grids, callouts and where they point. The page looks and prints exactly as it
did before, pixel for pixel. Any program can read the label without the software
that authored the drawing.

> **Status: 0.1, draft.** The specification, schema, library, command line, MCP
> server, inference and inspector are complete and tested. The format may still change
> before 1.0 ([versioning](spec/SPEC.md#8-versioning-policy)).

## Why

Drawings are still how buildings are issued, checked, priced and built, and they
leave the model as PDFs that have forgotten everything the model knew. A contractor's
software, a checking engineer's script or a language model reading the sheet has to
reconstruct from pixels what the architect's tool knew exactly. Plannotation keeps that
knowledge with the page, in an open format, as an attachment the page does not
depend on.

## One core, four surfaces

| Surface | Command or package | What it is |
| --- | --- | --- |
| **Library + CLI** | `plannotation` | Attach, read, strip, validate, inspect and export labels. |
| **MCP server** | `plannotation-mcp` | Serves labelled drawings to Claude Desktop and other MCP hosts, read-only unless told otherwise. |
| **Inference** | `plannotation infer` | Reconstructs labels for legacy PDFs, marked `inferred` with a confidence. |
| **Authoring tools** | `plannotation from-svg`, the Bonsai add-on | Labels the PDF an IfcOpenShell-based tool rendered, from the SVG it drew. |

## 60-second demo

```bash
uvx --from 'plannotation[all]' plannotation samples build
uvx plannotation inspect samples/positionsplan/sheet.labelled.pdf
```

The first command builds three IFC models and draws them — a floor plan, a structural
position plan and a section — as labelled A3 sheets; it needs a system `libcairo`
(`brew install cairo`, `apt-get install libcairo2`) or Inkscape with
`--inkscape-fallback`. The second prints what the position plan's label says. For the
same on a page, open [`inspector/index.html`](inspector/index.html) and choose the PDF:

![The position plan in the inspector: each member's mark and cross-section, the grids, dimensions and viewport, drawn from the label over the page](docs/img/inspector-positionsplan.png)

From a clone, before the package is on PyPI:

```bash
uv sync --all-extras
uv run plannotation samples build
uv run plannotation inspect samples/positionsplan/sheet.labelled.pdf
uv run plannotation validate samples/positionsplan/sheet.labelled.pdf
```

## Why a label helps

<!-- BENCHMARK:START -->
> **Not measured yet.** The harness is in place: 59 questions about the three sample
> sheets, each answered by the same model with and without the page label, and each
> keyed to the IFC model the sheet was drawn from ([method](docs/benchmark.md)).
> `make bench` runs it with an API key in `.env` and writes the measured table here.
> Until then there is deliberately no number in this section.
<!-- BENCHMARK:END -->

## Who did half of this already

<!-- PRIOR-ART:START -->
Plannotation invents as little as it can. Each piece below solved part of the problem;
the label is the part none of them covers — what a construction drawing shows, in
IFC's words, travelling with the issued page.

| Prior work | What it already solves | What is still missing |
| --- | --- | --- |
| PDF 2.0 Associated Files (ISO 32000-2) | Machine-readable files attached to a page or a document, with their relationship to it | Any vocabulary for what a drawing shows |
| PDF Declarations (PDF Association) | A standard way for a PDF to state which specification it conforms to | The specification to declare |
| PDF/A-3 hybrid invoices (ZUGFeRD, Factur-X) | The pattern: a human-readable PDF with a machine-readable twin inside it | Anything for drawings; they carry invoices |
| IFC (ISO 16739) | The building model's classes, GlobalIds and property sets, and its own entities for sheets, drawings and annotations | Survival past export: the issued PDF keeps none of it |
| IfcOpenShell SVG serializer, Bonsai | Drawings whose SVG groups carry each product's GlobalId and class | The step to the PDF that is actually issued, signed and archived |
| BCF (BIM Collaboration Format) | Referring to model elements by GlobalId from outside the model | Issued sheets; it describes issues, not drawings |
| Tagged PDF, PDF/UA | A semantic structure tree for text documents | Geometry, scale and model identity; drawings are rarely tagged |
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
pip install plannotation            # library and CLI
pip install 'plannotation[all]'     # plus the exporter (ifc, svg) and the benchmark (bench)
pip install plannotation-mcp        # the MCP server
```

Requires Python 3.11 or newer. See [CONTRIBUTING.md](CONTRIBUTING.md) to set up a
development environment.

## Documentation

| | |
| --- | --- |
| [`spec/SPEC.md`](spec/SPEC.md) | The normative specification. |
| [`plannotation/schema/`](plannotation/schema/) | The JSON Schema — single source of truth. |
| [`docs/`](docs/) | The inspector, MCP server, inference, benchmark and Bonsai guides, and design notes for Revit, AutoCAD and Tekla adapters. |
| [`samples/`](samples/) | What the three generated sample sheets contain. |
| [`CHANGELOG.md`](CHANGELOG.md) | What changed, release by release. |

## Licence and contributing

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).

Dependencies are restricted to MIT, BSD, Apache and MPL, with LGPL permitted only as
an unmodified dependency and GPL only as an optional external command-line tool.
Plannotation does not depend on any AGPL component. The policy, and the reasoning behind
it, is in [CONTRIBUTING.md](CONTRIBUTING.md).

Contributions are welcome — please read [CONTRIBUTING.md](CONTRIBUTING.md) and the
[Code of Conduct](CODE_OF_CONDUCT.md) first.
