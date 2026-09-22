# Inferring labels for legacy PDFs

A drawing office has decades of PDFs and none of them has a label. `planlabel infer`
reads what is printed on each page and writes a labelled copy.

```bash
planlabel infer old-drawing.pdf -o old-drawing.labelled.pdf
planlabel infer old-drawing.pdf -o old-drawing.labelled.pdf --ifc model.ifc
```

The input is never modified.

## What it reads

| On the page | Becomes | How |
| --- | --- | --- |
| The title block | `sheet.id`, `title`, `scale`, `revision`, `drawingType`, `discipline` | Words in the bottom-right, matched against patterns for sheet numbers, scales and revision indexes. |
| A letter or number in a small circle at a line's end | a `grid` annotation with its `axis` | Grid bubbles are found first, because `12` inside a bubble is an axis and `12` beside a line is a dimension. |
| A number beside a line | a `dimension` with its `value`, and `measures` when both ends of the line land on grid or level lines | A German decimal comma is read as one: `2,50` is two and a half. A grid's or a level's own line is never taken for a dimension's, since an overall dimension's value often prints across one. |
| A mark such as `Pos. 3`, `St.4`, `UZ-1` | a `tag`, and an `element` it shows | The IFC class is guessed from the mark's family, with a confidence to match. |
| A sheet number in a larger circle | a `callout` targeting that sheet | |
| A signed elevation on a horizontal line, such as `+3,00` | a `level` with its `elevation` in metres | Read before dimensions, so a storey height that runs between two level lines `measures` them. |

Everything inferred carries `provenance: inferred` and a `confidence`. The confidences
are ordered but not calibrated, and SPEC 4.6.5 says a reader must not compare them
across tools.

## With a model

Given `--ifc`, every mark the model also stores in an element's `Tag` is matched back to
that element, which turns a guessed class into the model's own and adds the GlobalId. A
mark the model gives to two elements is left unmatched rather than resolved by guessing:
a wrong GlobalId is a false statement about the building no later reader could detect.

A matched element is still `inferred`. It was established by reading the drawing, and
SPEC 4.6.6 forbids promoting reconstructed data to `authored` however good the match.

## How well it works

Measured on the three sample drawings with their labels stripped, against the authored
labels written from the models (design brief section 12). The drawings carry 32 marks,
21 dimensions, 18 grid lines and 3 levels between them. A dimension counts as found only
when its value is right *and* it measures the same two grids or levels the authored one
does: the right number linked to the wrong grids states a false distance.

| Category | Recall | Precision | Gate |
| --- | ---: | ---: | --- |
| Tags | 100% | 100% | ≥ 90% |
| Dimensions, value and what they measure | 100% | 100% | ≥ 80% |
| Grids | 100% | 100% | all |
| Levels | 100% | 100% | — |
| Callouts | 100% | 100% | — |
| Title block fields | 100% | 100% | — |

With `--ifc`, every mark recovers its element's GlobalId and class.

**Read these numbers for what they are.** The samples were drawn by this project's own
exporter, so their conventions are the conventions inference was written against. A
real office's drawings will do better or worse depending on how closely they follow the
same habits: a title block in the bottom-right, grid bubbles as circles, marks with a
recognisable prefix. Drop real drawings into `tests/fixtures/realworld/` (git-ignored by
design) and the test suite checks that inference does not crash on them; it cannot check
that it is right, because there is no answer key.

## Not implemented

`--llm` is reserved for a vision-model fallback on sheets the rules cannot classify. It
is accepted and ignored, with a note, so that scripts written against it do not break
when it arrives.
