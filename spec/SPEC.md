---
title: PlanLabel Specification
version: "0.1"
status: draft
canonical: https://srtgn.github.io/planlabel/spec/0.1
---

# PlanLabel 0.1 — Specification

**Status: draft.** This document is the normative specification for PlanLabel. It
is written and ratified phase by phase; sections still marked *(stub)* are not yet
normative and must not be relied upon.

The canonical location of this document is
<https://srtgn.github.io/planlabel/spec/0.1>. The `conformsTo` value written into
a labelled PDF's XMP declaration is exactly that URI.

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT,
RECOMMENDED, MAY and OPTIONAL are to be interpreted as described in
[RFC 2119](https://www.rfc-editor.org/rfc/rfc2119) and
[RFC 8174](https://www.rfc-editor.org/rfc/rfc8174) when, and only when, they
appear in all capitals.

---

## 1. Scope *(stub — Phase 1)*

The governing principle, stated here because everything else follows from it:

> **The PDF page is the leading document. The label is auxiliary.**

A PlanLabel label describes what is on a drawing page. It never replaces the page,
never changes how the page looks or prints, and is never sufficient to reconstruct
the drawing. A conforming writer MUST leave page appearance bit-identical.

## 2. Terms and definitions *(stub — Phase 1)*

Sheet, page, viewport, view, element, annotation, label, index, carrier, provenance,
conformance level. Where [SWAPP ifc-docs](https://github.com/SWAPP-eu) already names
a concept, this specification reuses SWAPP's name.

## 3. Coordinate conventions *(stub — Phase 1)*

Normative summary, to be expanded:

- **Paper coordinates** are millimetres, origin at the **bottom-left** of the page,
  **y up** — the PDF convention.
- **SVG** is y-down. A conforming implementation MUST flip the y axis on the way in
  and on the way out.
- `paperToPlane` is a 2-D affine transform `[a, b, c, d, e, f]` applied as
  `X = a·x + c·y + e`, `Y = b·x + d·y + f`, mapping paper millimetres to the
  viewport's model plane, expressed in the model's length unit.
- All numbers are serialised with **at most three decimals**.
- Label JSON is serialised with **sorted keys, two-space indent, LF line endings,
  UTF-8**, and no trailing newline ambiguity.

## 4. Conformance levels *(stub — Phase 1)*

| Level | Requires |
| --- | --- |
| **L1** | `page` and `sheet`, optionally `viewports` |
| **L2** | L1, plus `elements` carrying `ifcClass` and `paperBBox` |
| **L3** | L2, plus `annotations` carrying at least one link — `measures`, `shows`, `target`, `axis` or `ifcGuid` |

A validator MUST report the highest level fully reached.

## 5. Provenance *(stub — Phase 1)*

Every element and annotation carries `provenance`, one of `authored`, `inferred` or
`mixed`. Top-level `provenance` is the aggregate: `authored` only if every element
and annotation is authored, otherwise `mixed`. Anything `inferred` MUST carry a
`confidence` in `[0, 1]`.

## 6. Carriers *(stub — Phase 2)*

Three interchangeable carriers for the same payload:

1. **PDF** — page-level `/AF` with `/AFRelationship /Data`, a document-level index
   file, registration in the `EmbeddedFiles` name tree, and an XMP PDF Declaration.
2. **SVG** — IfcOpenShell-compatible; existing `id`/`class` values are never
   renamed, data is added via `data-planlabel-*` attributes or a `<metadata>` block.
3. **Sidecar JSON** — `X.planlabel.json`, for consumers that cannot read PDF
   attachments.

## 7. IFC mapping *(stub — Phase 4)*

Outline of the correspondence between PlanLabel objects and IFC entities, aligned
with SWAPP ifc-docs.

## 8. Versioning policy *(stub — Phase 1)*

How `planlabel`, the schema `$id` and the `conformsTo` URI move together, and what
counts as a breaking change.

## 9. Security considerations *(stub — Phase 1)*

Normative summary, to be expanded:

- A label is **data, never executable**. A conforming reader MUST NOT evaluate,
  execute or dereference label content as code.
- A conforming reader MUST validate a label against the schema before trusting any
  value in it, and MUST treat a label that fails validation as absent.
- Labels are attacker-supplied input whenever the PDF is. Readers MUST bound
  resource use when parsing.

---

## Normative references

- ISO 32000-2, *Document management — Portable document format — Part 2 (PDF 2.0)*
- ISO 19005-4:2020, *Document management — Electronic document file format for
  long-term preservation — Part 4 (PDF/A-4)*
- PDF Association, *PDF Declarations* (2019)
- buildingSMART, *Industry Foundation Classes (IFC)*
- RFC 2119, RFC 8174

## Informative references

- IfcOpenShell SVG serializer conventions, v0.8
- SWAPP ifc-docs (CC0, 2024)
