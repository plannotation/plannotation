# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The `plannotation` schema carries its own version (`plannotation: "0.1"`), which is
independent of the package version. Schema versioning policy is normative and
lives in [`spec/SPEC.md`](spec/SPEC.md).

## [Unreleased]

### Added

- **Authoring tools — SVG and Bonsai.** `plannotation.svg` reads the IFC identity an
  IfcOpenShell SVG already carries — each product's GlobalId and class, each view's
  `ifc:matrix3` and `ifc:plane` — through every unit, `viewBox`, nested viewport and
  transform, and writes an `authored` L2 plannotation for the PDF rendered from that
  SVG (`plannotation from-svg`). The SVG is treated as untrusted: a DTD is refused,
  sizes and element counts are bounded, and `<image>` references are followed only to
  local SVGs, two levels deep. `bonsai_ext/plannotation_bonsai.py` is a Blender add-on
  that does this for Bonsai's sheets; `docs/bonsai.md` is its manual test protocol.
  Design notes for Revit, AutoCAD and Tekla adapters in `docs/adapters/`.
- **Benchmark.** `plannotation-bench` asks one model the same questions about each
  sample sheet with and without its plannotation, the plannotation supplied as the
  result of a `get_plannotation` tool call; numbers are scored within 1 %, everything
  else exactly. Responses are cached on condition, model, question and a digest of the
  request, so a re-run asks nothing. The key is read from the environment or a
  git-ignored `.env`, and the tests drive the real SDK only against an in-process
  mock. 59 questions, every answer from the model the sheet was drawn from.
  `make bench`.
- **MCP server.** `plannotation-mcp` serves plannotated drawings to MCP hosts: list
  sheets, read a plannotation, find elements, measure between two items in model
  units, validate, and — only with `--allow-write` — attach and infer. Every path is
  confined to one root; a symlink out of it is refused.
- **Inference.** `plannotation infer` reconstructs plannotations for PDFs that never
  had one: the title block, grid bubbles, dimensions and what they span, level marks,
  marks and the class their family implies, and callouts, every item `inferred` with a
  confidence. With `--ifc`, each mark the model also holds recovers its element's
  GlobalId and class. 100 % recall and precision on the samples in every category,
  counting a dimension as found only if it measures the right grids or levels.
- **Inspector.** `inspector/index.html`, a single file that draws the plannotation over
  the page with pdf.js, with per-type toggles, tooltips, Copy JSON and Export CSV; and
  `plannotation inspect` for the terminal.
- **Authored exporter and samples.** `plannotation samples build` generates three seeded
  IFC4 models — a floor plan with doors and windows in openings on the grid A–D / 1–4,
  a foundation *Positionsplan* whose sixteen members each show their cross-section,
  and a true vertical section through two storeys with level marks — draws them with
  IfcOpenShell's serializer, composes A3 sheets with grids, dimensions, marks and
  callouts, and writes plannotations computed from the models. Every sheet validates
  at L3 with no findings against its model, and rebuilds byte for byte. SVG to PDF
  falls back to the Inkscape command line with `--inkscape-fallback`.
- **Validator.** `plannotation validate` checks 46 rules in seven families — schema,
  references, geometry, provenance, carrier, IFC cross-check and PDF/A via veraPDF —
  with JSON Pointer paths, Markdown or JSON reports, and exit codes 0, 1 and 2. A
  fixture per rule proves each one fires.
- **PDF carrier.** `plannotation attach`, `read`, `strip` and `sidecar`: plannotations
  as PDF 2.0 Associated Files with an index and an XMP PDF Declaration, written without
  touching a content stream. Every page is rasterised before and after and must be
  pixel-identical. Signed PDFs are refused; hostile input is read with bounds.
- **Specification.** `spec/SPEC.md` is complete: scope, terms, coordinate
  conventions, conformance, provenance, the PDF, SVG and sidecar carriers, the IFC
  mapping, the versioning policy and security considerations.

- **Schema 0.1.** The `plannotation-0.1` JSON Schema, which is the single
  source of truth for the format, plus the document-level `plannotation-index-0.1`
  schema. Both ship inside the wheel as package data, so a validator works from an
  installed package with no network and no checkout. Pydantic v2 models mirror them
  exactly, with a canonical serialiser — sorted keys, two-space indent, LF, at most
  three decimals — that round-trips byte for byte. The rules of SPEC 4.1 and 4.6 are
  encoded as pure functions the Phase 3 validator can call rather than reimplement:
  conformance level, provenance aggregation, and link detection. 24 plannotation
  fixtures (12 valid, 12 invalid) and 6 index fixtures, each negative failing for
  exactly one reason recorded in a manifest. Specification sections 1 to 4 are now
  normative.

- **Scaffold.** Repository layout, packaging for the two distributions
  (`plannotation`, `plannotation-mcp`), `plannotation --version` CLI entry point, lint and
  type-check configuration, test harness, pre-commit hooks, and CI.

### Changed

- Renamed from PlanLabel to Plannotation before the first release: package, CLI, schema
  key, embedded file names and URLs changed; no compatibility shim.

### Fixed

- The sample models were in millimetres while their plannotations said metres, putting
  `paperToPlane`, `plane` and `cutHeight` a factor of a thousand out against the files
  they described. The exporter now reads the unit from the model.
- Seeded GlobalIds took 136 bits for a 128-bit number, so their first character ran
  past `3` and the serializer's `product-<uuid>` ids named different numbers.
- Marks were printed at their elements' centres, black on black inside cut walls.

### Decided

Ambiguities in the format, resolved and written into `spec/SPEC.md`:

- **Paper coordinates are the CropBox, unrotated, with `/UserUnit` applied.** A
  non-zero box origin shifts every coordinate and `/UserUnit` scales them; both are
  ordinary on large-format construction drawings, and ignoring either corrupts a
  whole plannotation silently.
- **An item that omits `provenance` inherits the plannotation's declaration** (and
  `inferred` where that declaration is `mixed`). Reading silence as `authored` made
  a wholly reconstructed plannotation contradict itself.
- **With no elements and no annotations, the aggregation rule determines nothing**
  and the declared value stands. A title-block sheet written from the model and one
  recovered from a legacy PDF are indistinguishable by their items alone.
- **For a cut view the drawing plane is the cutting plane**, and `cutHeight` is the
  height above `storey.elevation` — German practice's *Schnitthöhe 1,20 m über
  OKFF*. So `plane.origin` along the normal equals `storey.elevation + cutHeight`,
  which a validator can check.
- **A `paperToPlane` with a negative determinant is an error.** It mirrors the view,
  and orientation is already carried by the plane axes, so a mirror there is always
  a second and contradictory statement of it.
- **`element.properties` is typed exactly as permissively as the schema.** A reader
  stricter than the format would reject conforming third-party plannotations;
  ergonomics live in `Element.pset()` instead.

[Unreleased]: https://github.com/plannotation/plannotation/commits/main
