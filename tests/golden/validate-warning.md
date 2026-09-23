# Plannotation validation report: 23-dimension-contradicts-scale.json

**PASS** -- 0 error(s), 1 warning(s); exit code 0.

- Source: `<path>`
- Carrier: plannotations
- Page labels examined: 1

## Pages

| page | sheet | level | version | viewports | elements | annotations |
| ---: | --- | --- | --- | ---: | ---: | ---: |
| 3 | ARC-201 | L3 | 0.1 | 2 | 5 | 6 |

## Findings

### page 3

- **warning** `PL-GEO-009` at `/annotations/1` -- annotation 'a-dim-raum': geometry runs 234 mm of paper at 1:50 = 11700 mm, but the value is 12.5 m = 12500 mm _(SPEC 3.4; design brief 8 (3))_

## Notes

- the veraPDF pass (PL-PDF-001) was not run: it needs a PDF
- target.pdfPage (PL-REF-006) and the page block against its page (PL-GEO-001) were not checked: there is no document to check them against
- the model cross-check (PL-IFC-001 to PL-IFC-003) was not run; pass --ifc MODEL.ifc to run it

---

Validated by plannotation <version>, Plannotation schema 0.1.
