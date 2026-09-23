# Plannotation validation report: 13-bbox-reversed.json

**FAIL** -- 1 error(s), 0 warning(s); exit code 1.

- Source: `<path>`
- Carrier: labels
- Page labels examined: 1

## Pages

| page | sheet | level | version | viewports | elements | annotations |
| ---: | --- | --- | --- | ---: | ---: | ---: |
| 3 | ARC-201 | L3 | 0.1 | 2 | 5 | 6 |

## Findings

### page 3

- **error** `PL-GEO-002` at `/elements/4/paperBBox` -- element 'e-col-12': bbox [383.0, 317.0, 377.0, 323.0] is not ordered x0 <= x1, y0 <= y1; a bbox is [x0, y0, x1, y1] with the lower-left corner first _(SPEC 3.4)_

## Notes

- the veraPDF pass (PL-PDF-001) was not run: it needs a PDF
- target.pdfPage (PL-REF-006) and the page block against its page (PL-GEO-001) were not checked: there is no document to check them against
- the model cross-check (PL-IFC-001 to PL-IFC-003) was not run; pass --ifc MODEL.ifc to run it

---

Validated by plannotation <version>, Plannotation schema 0.1.
