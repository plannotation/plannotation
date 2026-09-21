# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The `planlabel` schema carries its own version (`planlabel: "0.1"`), which is
independent of the package version. Schema versioning policy is normative and
lives in [`spec/SPEC.md`](spec/SPEC.md).

## [Unreleased]

### Added

- **Phase 1 — Schema 0.1.** The `planlabel-0.1` JSON Schema, which is the single
  source of truth for the format, plus the document-level `planlabel-index-0.1`
  schema. Both ship inside the wheel as package data, so a validator works from an
  installed package with no network and no checkout. Pydantic v2 models mirror them
  exactly, with a canonical serialiser — sorted keys, two-space indent, LF, at most
  three decimals — that round-trips byte for byte. The rules of the design brief are
  encoded as pure functions the Phase 3 validator can call rather than reimplement:
  conformance level, provenance aggregation, and link detection. 24 label fixtures
  (12 valid, 12 invalid) and 6 index fixtures, each negative failing for exactly one
  reason recorded in a manifest. Specification sections 1 to 4 are now normative.

- **Phase 0 — Scaffold.** Repository layout, packaging for the two distributions
  (`planlabel`, `planlabel-mcp`), `planlabel --version` CLI entry point, lint and
  type-check configuration, test harness, pre-commit hooks, and CI.

### Decided

Ambiguities in the format, resolved and written into `spec/SPEC.md`:

- **Paper coordinates are the CropBox, unrotated, with `/UserUnit` applied.** A
  non-zero box origin shifts every coordinate and `/UserUnit` scales them; both are
  ordinary on large-format construction drawings, and ignoring either corrupts a
  whole label silently.
- **An item that omits `provenance` inherits the label's declaration** (and
  `inferred` where that declaration is `mixed`). Reading silence as `authored` made
  a wholly reconstructed label contradict itself.
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
  stricter than the format would reject conforming third-party labels; ergonomics
  live in `Element.pset()` instead.

[Unreleased]: https://github.com/srtgn/planlabel/commits/main
