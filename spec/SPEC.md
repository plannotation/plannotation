---
title: PlanLabel Specification
version: "0.1"
status: draft
canonical: https://srtgn.github.io/planlabel/spec/0.1
---

# PlanLabel 0.1 — Specification

**Status: draft.** This document is the normative specification for PlanLabel. It
is written and ratified phase by phase. Sections 1 to 4 are normative as of
Phase 1. Sections still marked *(stub)* are not yet normative and must not be
relied upon.

The canonical location of this document is
<https://srtgn.github.io/planlabel/spec/0.1>. The `conformsTo` value written into
a labelled PDF's XMP declaration is exactly that URI.

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT,
RECOMMENDED, MAY and OPTIONAL are to be interpreted as described in
[RFC 2119](https://www.rfc-editor.org/rfc/rfc2119) and
[RFC 8174](https://www.rfc-editor.org/rfc/rfc8174) when, and only when, they
appear in all capitals.

---

## 1. Scope

### 1.1 What this specification defines

This specification defines **PlanLabel 0.1**: a small JSON document, called a
*label*, that states what is on one page of a 2-D construction drawing — which
sheet the page is, which viewports it contains and how paper maps to model space,
which elements are drawn and which annotations are placed on them — together with
a document-level *index* of the labelled pages, the rules for the coordinates both
use, and the obligations of the programs that write and read them.

It specifies the payload and its meaning. It does not specify how a drawing is
produced, how it is drawn, or what it should contain.

### 1.2 The governing principle

> **The PDF page is the leading document. The label is auxiliary.**

Everything below follows from that sentence, so it is stated normatively here and
its consequences are given as requirements.

1. **Appearance is untouched.** A conforming writer MUST NOT change how any page
   of a document looks or prints. Rendering any page of the labelled document
   MUST produce output identical to rendering the corresponding page of the input
   document, for the same renderer at the same settings, at any resolution and on
   any medium. A conforming writer MUST NOT modify a page's content streams and
   MUST NOT add a visible mark of any kind. The file's bytes necessarily change —
   a label has to be stored somewhere — but nothing a reader of the drawing can
   see does.

2. **Additive, never subtractive.** A conforming writer MUST NOT remove or alter
   content that it did not itself write, including attachments, annotations,
   form fields and metadata placed by other producers. The only metadata a
   conforming writer adds is the declaration described in section 6.

3. **A label is not a drawing.** A label is never sufficient to reconstruct the
   drawing it describes, and this specification makes no attempt to make it so.
   It records bounding boxes, outlines and identities — what is where and what it
   means — not line weights, hatching, text placement or any other matter of
   presentation. A program that needs the drawing MUST read the page.

4. **A label is not a model.** Where a label carries model-derived values, it
   carries them at the precision defined in section 3.8, which is coarser than
   the model's. A program that needs model geometry MUST read the model.

5. **Ignoring the label is conforming.** A reader that does not know about
   PlanLabel, or that chooses not to use it, behaves exactly as it did before the
   label existed. It loses nothing but meaning. No part of this specification may
   be read as requiring a consumer of the document to process the label.

6. **The page wins.** Where a label and the page it describes disagree, the page
   is correct and the label is wrong. A reader MUST resolve such a disagreement
   in favour of the page. A writer MUST NOT repair a page to agree with a label.

### 1.3 Exclusions

The following are outside the scope of this specification. Each is listed because
it has been asked for, not because it is unimaginable.

| Excluded | Note |
| --- | --- |
| A new drawing file format | PlanLabel annotates documents in formats that already exist. |
| Any change to how a PDF is drawn | See 1.2 (1). Content streams are out of bounds. |
| Reconstruction of a drawing from a label | See 1.2 (3). The label is lossy by design. |
| Reconstruction of a model from a label | See 1.2 (4). A label references a model; it does not contain one. |
| Authoring-tool integration | How a CAD application obtains the values is its own business; this specification constrains only what it writes. |
| The inference process | How a label is recovered from an unlabelled drawing is unspecified. This specification constrains only how such a label is marked — see 4.6. |
| Presentation | No viewer behaviour, no overlay style, no user interface is normative. |
| Networked exchange | No service, protocol or API is defined. A label travels with its document. |
| The IFC vocabulary itself | Class names, GlobalIds and property-set names are IFC's and are used, not redefined. |

### 1.4 Carriers

The same payload is defined once and carried in three interchangeable ways: as
embedded files in a **PDF**, as data added to an **SVG**, and as a **sidecar JSON**
file beside the document. The carriers are specified in section 6. The rest of
this specification is written in terms of the payload and applies to all three.

---

## 2. Terms and definitions

Where [SWAPP ifc-docs](https://github.com/SWAPP-eu) has already named a concept,
this specification uses SWAPP's name for it, and says so in the definition. That
reuse is deliberate: two vocabularies for one idea help nobody, and the credit is
part of the point. Where a term has a correspondence in IFC, the correspondence is
given; it is informative, and does not make IFC a prerequisite for reading a label.

Terms defined here are printed in *italics* on first use elsewhere in this
document. JSON member names are printed as `code`.

### 2.1 sheet

The drawing as a document in its own right: the thing that carries a sheet number,
a title, a revision, a scale and a title block, and that a project issues, checks
and supersedes. **SWAPP calls this a Sheet and this specification keeps that name.**
A sheet is not a page: it is what the page is a printing of.

In IFC a sheet corresponds to an `IfcDocumentInformation` whose `Scope` is
`"SHEET"`.

In a label, the sheet is the `sheet` member, and there is exactly one per page.

### 2.2 page

One page of the document that carries the drawing, identified by its zero-based
index within that document, and having a width, a height and a rotation. The page
is the leading document of section 1.2.

In a label, the page is the `page` member. `page.index` MUST equal the zero-based
index of the page the label describes.

A page carries exactly one sheet. A sheet MAY be printed on more than one page,
in which case each page carries its own label and the labels repeat the sheet.

### 2.3 viewport

A rectangle on the sheet into which a view of the model has been placed, together
with the mapping from that rectangle to the model. **SWAPP calls this a ViewPort
and this specification keeps that name**; the JSON member is `viewports`, spelled
in the lower camel case used for every member name.

In IFC a viewport corresponds to an `IfcAnnotation` aggregated to the sheet with
`IfcRelAggregates`.

A viewport's rectangle is `paperBBox`, in paper coordinates. Its mapping is
`plane` and `paperToPlane`, defined in section 3.

### 2.4 view

The projection of the model that is drawn inside a viewport: a plan, a section, an
elevation, a detail. **SWAPP calls this a View and this specification keeps that
name for the concept**, but does not give it an object of its own: a view's
properties — its `kind`, `scale`, `plane`, `cutHeight` and `storey` — are recorded
on the viewport that contains it.

In IFC a view corresponds to an `IfcAnnotation` with `ObjectType = "DRAWING"`,
described by the `EPset_Drawing` property set.

One viewport holds one view. Where an authoring tool places the same view in two
viewports, the label records two viewports.

### 2.5 element

A thing in the building, drawn on the page: a wall, a column, a beam, a door.

An element entry describes an **appearance**, not an object. It says that
something with this identity was drawn here, on this page, inside this viewport.
The same building object drawn in two viewports of the same sheet produces two
element entries, with two distinct `id` values and, where known, the same
`ifcGuid`. It follows that `ifcGuid` is not a key: see 4.5.

In IFC an element corresponds to the `IfcProduct` occurrence itself, referenced by
its `GlobalId`. SWAPP relates a drawn annotation to that product with
`IfcRelAssignsToProduct`.

An element MAY have no `ifcGuid` — a label recovered from a drawing usually knows
that a wall is there without knowing which wall it is.

### 2.6 annotation

A mark on the sheet that says something about the drawing rather than depicting
the building: a dimension, a tag, a callout, a grid line, a level, a section mark,
a north arrow. **SWAPP calls this an Annotation and this specification keeps that
name.**

In IFC an annotation corresponds to an `IfcAnnotation`.

What makes an annotation useful to a machine is its *link* — the member that says
what it annotates: `measures`, `shows`, `target`, `axis` or `ifcGuid`. Conformance
level L3 is defined in terms of those links; see 4.1.

### 2.7 label

The JSON object that describes exactly one page, conforming to the page-label
schema `planlabel.schema.json`. A label is data. It is never executable, and a
reader MUST NOT treat it as though it were; see section 9.

"Label" without qualification means a page label. The index of 2.8 is not a label.

### 2.8 index

The JSON object that lists, for one document, which pages carry labels, what sheet
each of them is, and which conformance level each label reaches. It conforms to
`planlabel-index.schema.json`, which is deliberately self-contained so that an
index can be validated without also fetching the page-label schema.

The index is a convenience, not a source of truth: it lets a reader see what a
document contains without opening every page label. Where the index and a page
label disagree, the page label is correct — the index is to the labels what the
labels are to the page. **SWAPP's DocumentSet is the nearest concept**, though
SWAPP's is a set of sheets and PlanLabel's is a manifest of one document's pages.

### 2.9 carrier

The mechanism that binds a label to the document it describes: embedded files in a
PDF, added data in an SVG, or a sidecar JSON file. A carrier changes how a label
travels, never what it means. The three carriers are specified in section 6.

### 2.10 provenance

Where a value came from: from the record that produced the drawing (`authored`),
or from a reconstruction of the drawing after the fact (`inferred`), or, at the
level of a whole label, from both (`mixed`).

Provenance is an assertion the writer makes about its own knowledge, and it is the
one assertion this specification will not let a writer make loosely. The rules are
normative in 4.6.

### 2.11 conformance level

One of L1, L2 or L3: a statement about how much of a page a label describes, not
about how well it describes it. The levels are cumulative and are defined in 4.1.
A level is a property of a single page label.

### 2.12 paper coordinates

The coordinate system in which every geometric value in a label is expressed:
millimetres, origin at the bottom-left corner of the page, y increasing upwards.
Defined normatively in section 3.1.

The name is literal. These are coordinates on the sheet of paper, as it would be
measured with a ruler, not coordinates in the building.

### 2.13 model plane

The plane in model space onto which a view projects, given by a viewport's
`plane` member as an origin and two axis directions. A point's *plane coordinates*
are its two coordinates on that plane, in the model's length unit. Defined
normatively in section 3.6.

### 2.14 authoring tool

The program that produced the drawing, and that therefore holds the record from
which the drawing was made. Only an authoring tool, or a writer acting on that
tool's record, can produce `authored` values.

### 2.15 writer

A program that produces a label, an index, or both, and attaches them to a
document through a carrier. Obligations in 4.2.

### 2.16 reader

A program that obtains a label from a document and uses it. Obligations in 4.3.

### 2.17 validator

A reader whose output is a report about a label's conformance rather than a use of
the label's content. Obligations in 4.4. A validator is a reader and is bound by
everything 4.3 requires of one, including the requirement not to modify what it
reads.

### 2.18 Correspondence summary (informative)

| PlanLabel | SWAPP ifc-docs | IFC |
| --- | --- | --- |
| index | DocumentSet (approximately) | — |
| sheet | **Sheet** | `IfcDocumentInformation`, `Scope = "SHEET"` |
| viewport | **ViewPort** | `IfcAnnotation` aggregated with `IfcRelAggregates` |
| view | **View** | `IfcAnnotation`, `ObjectType = "DRAWING"`, `EPset_Drawing` |
| annotation | **Annotation** | `IfcAnnotation` |
| element | referenced, not named | the `IfcProduct` occurrence, by `GlobalId`; related with `IfcRelAssignsToProduct` |
| label, carrier, provenance, conformance level, paper coordinates, model plane | — | — |

---

## 3. Coordinate conventions

This section is normative in full. It says the same thing as the module docstring
of `planlabel/units.py`, which is the conventions' home in code; the two are kept
in step and neither may be changed alone.

### 3.1 Paper coordinates

Every geometric value in a label — every `bbox`, every `point`, every `polyline`,
and the `x` and `y` inputs of every `paperToPlane` — is expressed in **paper
coordinates**:

- the unit is the **millimetre**;
- the origin is the **bottom-left corner of the page**;
- **x increases to the right and y increases upwards**;
- the axes are the page's own, not the viewer's; see 3.7 for rotation.

The choice is not arbitrary and is not a matter of taste. It is the PDF
convention: default PDF user space has its origin at the bottom-left of the page
with y increasing upwards. Adopting it means a label and the page it describes
agree about which way is up, so that no implementation has to flip anything to
compare them, and so that a coordinate read out of a label can be handed to a PDF
operation unchanged but for a scale factor. The governing principle of 1.2 decides
this: the page's convention leads, and the label follows it.

"The page" means the page as displayed and printed, which in PDF terms is the
page's `CropBox`, defaulting to the `MediaBox` where no `CropBox` is present.
`page.widthMm` and `page.heightMm` MUST be that box's width and height, converted
by 3.2. A writer MUST measure from that box's lower-left corner; where the box's
lower-left corner is not at the origin of user space, the writer MUST subtract it.
A validator MUST report a `page.widthMm` or `page.heightMm` that differs from the
page by more than the geometric tolerance of 0.5 mm.

### 3.2 Relation to PDF user space

PDF user space is measured in **points**, where one point is exactly 1/72 inch and
one inch is exactly 25.4 millimetres. The origin and the axis directions already
agree with 3.1, so the conversion is a scale factor and, where the page box is
offset, a translation:

```
x_mm = (x_pt − cropBox.x0) × 25.4 / 72
y_mm = (y_pt − cropBox.y0) × 25.4 / 72

x_pt = x_mm × 72 / 25.4 + cropBox.x0
y_pt = y_mm × 72 / 25.4 + cropBox.y0
```

The factors are `MM_PER_PT = 25.4 / 72 = 0.352777…` and
`PT_PER_MM = 72 / 25.4 = 2.834645…`. Both are non-terminating decimals.
Implementations MUST use the exact ratio and MUST NOT substitute a rounded
constant; rounding belongs at serialisation and nowhere else (3.8).

Where the page carries a `UserUnit` other than 1, the default user space unit is
that many multiples of 1/72 inch, and a writer MUST include the factor:

```
x_mm = (x_pt − cropBox.x0) × UserUnit × 25.4 / 72
```

`UserUnit` is common on large-format drawings and omitting it scales an entire
label wrongly, silently.

An A3 page, 420 mm × 297 mm, is 1190.551 pt × 841.89 pt at `UserUnit` 1.

### 3.3 Relation to SVG

SVG is **y-down**: its origin is the top-left corner and y increases downwards.
Every coordinate crossing the boundary between an SVG carrier and a label MUST
therefore be flipped about the horizontal axis. For a page of height `h`
millimetres the flip is its own inverse, and x is unchanged:

```
y_paper = h − y_svg
y_svg   = h − y_paper
```

The formula assumes the SVG's user unit is the millimetre. Where it is not, a
writer MUST convert to millimetres before flipping; where the SVG's root
coordinate system is offset from the page corner, the writer MUST remove that
offset first.

Forgetting the flip produces a label that is mirrored about the page's horizontal
centre line — an error that is invisible on a small, roughly centred drawing and
obvious on a title block. Implementations SHOULD apply the flip in exactly one
place per direction rather than at each call site.

### 3.4 Bounding boxes

A `bbox` is the four-element array `[x0, y0, x1, y1]` in paper millimetres, with

```
x0 ≤ x1    and    y0 ≤ y1
```

so that `x0, y0` is the lower-left corner and `x1, y1` the upper-right. A writer
MUST emit bounding boxes in that order. The ordering cannot be expressed in JSON
Schema, so schema validity does not imply it; a validator MUST check it and MUST
report a violation as an error (4.4).

A bounding box is axis-aligned **in paper space**, whatever the orientation of what
it bounds. Where the writer has the geometry, the box MUST be its tight
axis-aligned bounds; where the writer has only an estimate of where something was
drawn, the box is that estimate, and the object is `inferred` and carries a
confidence (4.6). Degenerate boxes are permitted: a horizontal grid line has
`y0 = y1`.

Where an object carries both a `paperBBox` and geometry — `paperOutlines` on an
element, `geometry` on an annotation — every point of that geometry MUST lie
within the bounding box, to within the geometric tolerance of 0.5 mm.

### 3.5 The paper-to-plane transform

Each viewport MAY carry `paperToPlane`, a 2-D affine transform stored as the
six-element array `[a, b, c, d, e, f]` and applied to a point in paper millimetres
as:

```
X = a·x + c·y + e
Y = b·x + d·y + f
```

Two properties of that definition are easy to get wrong and are therefore stated
explicitly.

**The component order is the PDF and PostScript matrix convention**, not the
row-major order of a mathematics textbook. `[a b c d e f]` denotes

```
        ⎡ a  b  0 ⎤
[x y 1] ⎢ c  d  0 ⎥
        ⎣ e  f  1 ⎦
```

so that a transform read out of a label can be handed to a PDF operator, or
composed with one, without being rearranged. In particular `b` is the second
component of the image of the x axis, not the first component of the image of the
y axis.

**The output is in the model's length unit**, the one named by `model.lengthUnit`,
and not in millimetres. A writer that emits `paperToPlane` MUST also emit
`model.lengthUnit`. A reader that encounters `paperToPlane` without
`model.lengthUnit` MUST NOT assume a unit: it MAY use the transform for ratios and
for identifying positions, and MUST NOT report a distance derived from it as a
length.

The inverse, for a reader that has a point on the plane and wants to know where it
was drawn, is:

```
det = a·d − b·c
x = ( d·(X − e) − c·(Y − f) ) / det
y = ( a·(Y − f) − b·(X − e) ) / det
```

A writer MUST NOT emit a transform whose determinant is zero; such a transform
collapses the viewport to a line and cannot be inverted. A reader MUST check the
determinant before inverting.

A writer MUST NOT emit a transform whose determinant is **negative**. A negative
determinant mirrors the view, so a north elevation would be drawn reading as a south
elevation — and because a view's orientation is already carried by `plane.xAxis` and
`plane.yAxis`, a mirror in the transform is always a second, contradictory statement
of it rather than a legitimate choice. A validator MUST report a negative determinant
as an error. This is the same prohibition as 3.6's "a writer MUST NOT mirror a view
by negating an axis", stated for the other place a mirror can hide.

Where the linear part is a similarity — a uniform scale with a rotation, possibly
with a reflection, which is what an authoring tool produces for an ordinary
viewport — the drawing scale can be recovered from it:

```
k = √|a·d − b·c|             model length units per paper millimetre
S = k × (millimetres per model length unit)    the scale denominator
```

For `k = 0.05` with `model.lengthUnit = "m"`, `S = 0.05 × 1000 = 50`: the view is
at 1:50. Where a viewport carries both `scale` and `paperToPlane`, and the linear
part is a similarity, the two MUST agree to within 0.1 %; a validator MUST report
a disagreement. Where the linear part is not a similarity, `k` is undefined and no
scale can be recovered; `scale` then stands alone and a validator MUST NOT check
it against the transform.

### 3.6 The model plane

A viewport's `plane` gives the model-space plane the view projects onto, as an
`origin` and two axis directions `xAxis` and `yAxis`, all `vec3` in the model's
length unit (the axes being directions, their unit is immaterial).

`xAxis` and `yAxis` MUST be unit vectors and MUST be mutually orthogonal, each to
within 1 × 10⁻⁶. The requirement exists because scale is already carried by
`paperToPlane`: axes of a length other than one would express it a second time and
the two statements could disagree.

Plane coordinates `(X, Y)` — the output of `paperToPlane` — map to a model point:

```
P = origin + X·xAxis + Y·yAxis
```

and a model point `P` lying on the plane maps back by projection:

```
X = (P − origin)·xAxis        Y = (P − origin)·yAxis
```

A model point that does not lie on the plane projects along the plane normal. A
label records where a thing was **drawn**, not where it is; for anything cut or
projected, the two differ, and the label does not carry the difference.

**The view direction.** The plane's normal is

```
n = xAxis × yAxis
```

and `n` points **from the plane towards the observer**: the drawing is what is seen
looking along `−n`. A floor plan of a model whose Z axis is up therefore has
`xAxis = [1, 0, 0]`, `yAxis = [0, 1, 0]` and `n = [0, 0, 1]` — the plan is seen
from above, model +X runs right across the paper and model +Y runs up it. This is
the reading in which the ordinary case needs no sign to be remembered; a writer
MUST NOT mirror a view by negating an axis.

**Cut height.** For a view that cuts the model, the view's drawing plane **is** the
cutting plane, and `cutHeight` is the height of that cut **above
`storey.elevation`**, in the model's length unit. This is the quantity a drawing is
actually specified by — German practice writes it on the sheet as *Schnitthöhe 1,20
m über OKFF*.

It follows that where both `storey.elevation` and `cutHeight` are present, the
component of `plane.origin` along the plane normal `n` MUST equal
`storey.elevation + cutHeight`. A validator MUST check this, and a writer that
cannot satisfy it has misplaced one of the three.

So a plan of a storey whose finished floor is at elevation 3.0 m, cut 1.2 m above
that floor, has `storey.elevation` 3.0, `cutHeight` 1.2, and a `plane.origin` whose
normal component is 4.2. `storey.elevation` is absolute; `cutHeight` is relative to
it.

### 3.7 Page rotation

`page.rotation` records the page's effective `/Rotate` value, normalised to one of
0, 90, 180 or 270 — the number of degrees by which a viewer rotates the page
clockwise before displaying it.

**Paper coordinates are unrotated.** They are measured in the page's own
coordinate system, before `/Rotate` is applied, and `page.widthMm` and
`page.heightMm` are the unrotated dimensions. A writer MUST NOT pre-rotate
coordinates, and MUST NOT swap width and height, whatever `page.rotation` says.

This follows from 1.2: the page's geometry is the leading document's geometry.
Everything else in a PDF that refers to a position on the page — `MediaBox`,
`CropBox`, annotation rectangles, the content stream itself — is expressed before
rotation, and a label that were expressed after it would be the only thing in the
file that needed converting before it could be compared with anything else.

`page.rotation` is recorded so that a reader can reproduce the displayed
orientation, and for no other purpose. A reader overlaying label geometry on a
rotated rendering of the page MUST apply the same rotation to that geometry. For a
page of unrotated width `w` and height `h`, a paper point `(x, y)` appears in the
displayed page at:

| `page.rotation` | displayed position | displayed page size |
| --- | --- | --- |
| 0 | `(x, y)` | `w × h` |
| 90 | `(y, w − x)` | `h × w` |
| 180 | `(w − x, h − y)` | `w × h` |
| 270 | `(h − y, x)` | `h × w` |

with the displayed coordinates still measured from the bottom-left corner, y up.

### 3.8 Serialisation

**Precision.** Every number in a label and in an index, without exception, MUST be
serialised with **at most three decimal places**, rounded **to nearest, ties to
even**: of the two three-decimal candidates, the nearer is chosen, and where the
value lies exactly halfway between them the candidate whose last digit is even is
chosen.

"Exactly halfway" is decided on the **exact value of the number as the
implementation holds it**, not on a decimal spelling of it. In binary floating
point almost nothing is exactly halfway — the double nearest to 0.1235 is slightly
below it and rounds down, not to even — so the tie rule fires only for values such
as 0.0625 that are exactly representable, and then it fires identically everywhere.
Ties to even is specified because it is IEEE 754's own default and therefore what
an unadorned rounding routine already does: Python's `round(value, 3)` implements
this clause exactly.

Numbers MUST be written in plain decimal notation: no exponent, no trailing zeros
in the fractional part, no trailing decimal point, integral values written without
a fractional part — a page width of 841 mm is `841`, never `841.0` — and negative
zero written as `0`.

A label MUST NOT contain a NaN or an infinity. Neither is JSON, neither can be a
coordinate, and a writer that arrives at one MUST fail rather than emit it.

**Canonical form.** A label or index MUST be serialised as:

- UTF-8, without a byte order mark, with characters that UTF-8 can represent
  written literally and not escaped as `\uXXXX` — `Maßstab` and not `Ma\u00dfstab`;
- object members sorted by name in ascending Unicode code point order;
- indented with two spaces per nesting level, **each member of an object and each
  element of an array on its own line**, with no whitespace at the end of any line;
- LF line endings, ending with exactly one LF;
- absent members absent, never written as `null`.

Arrays are not exempted from the indent rule. A four-number bounding box occupies
six lines. That is what a standard two-space pretty-printer produces in every
language that has one, and an exception for numeric arrays would have to be
specified, agreed and implemented identically everywhere to buy nothing but a
shorter file.

Two writers given the same label therefore produce the same bytes, so a label can
be compared, hashed and checked into version control like any other source.

**What the precision means.** Three decimals of a millimetre is one micrometre on
paper, which is finer than any drawing is worth. In the model it is coarser, and
the limit falls on `paperToPlane`: rounding each coefficient by up to 0.0005
displaces a point at paper `(x, y)` by up to

```
|ΔX|, |ΔY| ≤ 0.0005 · (|x| + |y| + 1)     in model length units
```

For a point near the far corner of an A3 sheet that is about 0.29 model length
units: 0.29 mm where `model.lengthUnit` is `"mm"`, and 0.29 m where it is `"m"`.
The common case is exact — an unrotated viewport at a standard scale has
coefficients such as 0.05 or 0.1 that survive rounding untouched — and a rotated
viewport is not, because its coefficients carry a sine and a cosine.

Therefore: a reader MUST treat plane coordinates obtained through `paperToPlane`
as approximate, with the bound above, and MUST NOT present them as measurements of
the building. Where a reader needs model geometry to a finer tolerance, it MUST
obtain it from the model. The label says what is on the page; it is no more a
substitute for the model than it is for the drawing.

### 3.9 A worked example

An A3 landscape sheet, 420 mm × 297 mm, `page.rotation` 0, `CropBox` at the origin
of user space, `UserUnit` 1. It carries one viewport:

```json
{
  "cutHeight": 1.2,
  "id": "vp1",
  "kind": "plan",
  "paperBBox": [20, 20, 320, 260],
  "paperToPlane": [0.05, 0, 0, 0.05, -3, -2.25],
  "plane": {
    "origin": [0, 0, 3],
    "xAxis": [1, 0, 0],
    "yAxis": [0, 1, 0]
  },
  "scale": 50,
  "storey": {
    "elevation": 3,
    "name": "Level 1"
  }
}
```

and `model.lengthUnit` is `"m"`. The view is a plan of the storey whose floor is at
3.000 m, cut 1.200 m above it, drawn at 1:50 — confirmed by
`k = √(0.05 × 0.05) = 0.05` and `S = 0.05 × 1000 = 50`, which agrees with `scale`.
The plan is seen from above, because `xAxis × yAxis = [0, 0, 1]` points at the
observer.

The members above are in ascending name order and the numbers carry no needless
decimals, as 3.8 requires. The arrays are shown inline for legibility only: in the
canonical form each of their elements sits on its own line.

Follow one corner of one wall through every coordinate system. The arithmetic below
is shown with three decimals throughout so that the precision stays visible; in a
label the trailing zeros would be dropped, by 3.8.

**1. SVG to paper.** The exporter's SVG has the corner at user-unit position
`(168.400, 165.800)`, measured from the top-left. The page is 297 mm high, so by
3.3:

```
x_paper = 168.400
y_paper = 297 − 165.800 = 131.200
```

**2. Paper to PDF user space.** By 3.2, with the `CropBox` at the origin:

```
x_pt = 168.400 × 72 / 25.4 = 477.354
y_pt = 131.200 × 72 / 25.4 = 371.906
```

and back, `477.354 × 25.4 / 72 = 168.400`, `371.906 × 25.4 / 72 = 131.200`. This is
where the corner is drawn in the file, and a reader that wants to draw a marker on
top of it in a PDF tool needs no other number.

**3. Paper to plane.** The point lies inside `vp1`'s `paperBBox`, so `vp1`'s
transform applies. By 3.5:

```
X = 0.05 × 168.400 + 0 × 131.200 + (−3)    = 5.420
Y = 0 × 168.400 + 0.05 × 131.200 + (−2.25) = 4.310
```

and the inverse checks: `det = 0.0025`, `x = 0.05 × (5.420 + 3) / 0.0025 = 168.400`,
`y = 0.05 × (4.310 + 2.25) / 0.0025 = 131.200`.

**4. Plane to model.** By 3.6:

```
P = [0, 0, 3] + 5.420 × [1, 0, 0] + 4.310 × [0, 1, 0] = [5.420, 4.310, 3.000]
```

The wall corner is at model coordinates (5.420, 4.310, 3.000) metres. Because the
plan is a cut, the corner as drawn is the wall's footprint at the cut, and its
model Z is the plane's, not the wall's.

**5. The reading the numbers do not support.** The transform's coefficients are
exact at three decimals here, so step 3 loses nothing. Rotate the viewport by 13°
and they are not: `a` becomes 0.048719, serialised as 0.049. By the bound in 3.8
the point recovered in step 3 may then sit
`0.0005 × (168.400 + 131.200 + 1) = 0.150` m from where the model has the corner —
150 mm, on a drawing whose own tolerance is a few millimetres. The label still says
correctly that a wall corner is drawn at paper (168.400, 131.200) — that is a fact
about the page, and it is what the label is for.

---

## 4. Conformance

### 4.1 Conformance levels

A conformance level says how much of a page a label describes. It says nothing
about how accurate the description is: accuracy is what provenance (4.6) and
validation (4.4) are for, and a careless L3 label is worth less than a careful L1
one. A label may reach L3 and still violate rules in 4.5 or 4.6; a validator
reports the level and the errors side by side, and neither cancels the other.

| Level | A label reaches this level when |
| --- | --- |
| **L1** | It is valid against the page-label schema. `page` and `sheet` are therefore present; `viewports` MAY be. |
| **L2** | L1, and `elements` is present and non-empty. Every element carries `ifcClass` and `paperBBox`, both of which the schema requires. |
| **L3** | L2, and at least one annotation carries at least one link. |

A **link** is any one of `measures`, `shows`, `target`, `axis` or `ifcGuid`, carrying
a value that actually refers to something: an empty `shows`, an empty `target` or an
empty `axis` is schema-valid and links nothing, so none of them counts.

*Informative.* One link suffices deliberately. Several annotation types — `text`,
`revisionCloud`, `keynote`, `symbol`, `leader`, `hatch`, `northArrow`, `scaleBar` —
have nothing to link to, and a north arrow that means what it looks like should not
hold a sheet back. The types that usually can be linked are `dimension`, `tag`,
`callout`, `sectionMark` and `grid`; a writer SHOULD link every annotation of those
types that has a referent, and a validator SHOULD report any that are unlinked as a
warning. That is a quality signal, not a level boundary: making it one would let a
single unlinked dimension among fifty demote a sheet, which tells a consumer less,
not more.

The levels are cumulative: L3 implies L2 implies L1. A label that is not valid
against the schema reaches no level at all, and a reader MUST treat it as absent
(4.3).

A writer MUST NOT omit or weaken an annotation in order to reach a level. A label
that describes the page less truthfully in exchange for a higher letter has
defeated its own purpose; a validator cannot detect this and the obligation is on
the writer.

A level is a property of one page label. The index records the level of each
labelled page. This specification defines no conformance level for a document as a
whole, and a reader MUST NOT infer one; where a single level is quoted for a
document informally, it is the lowest reached by any of its labelled pages.

### 4.2 Conforming writers

A conforming writer MUST:

1. produce labels valid against the page-label schema, and, where it writes one,
   an index valid against the index schema, both at the version named in
   `planlabel`;
2. serialise both in the canonical form of 3.8, with every number at three
   decimals or fewer;
3. leave every page's appearance unchanged, and add nothing visible — 1.2 (1);
4. leave content it did not write alone, including third-party attachments,
   annotations and metadata — 1.2 (2);
5. express every geometric value in paper coordinates as defined in section 3,
   unrotated, with normalised bounding boxes;
6. give every object in a page a `localId` unique within that page, and reference
   other pages only through `target` — 4.5;
7. record provenance truthfully, and in particular MUST NOT record reconstructed
   data as `authored` — 4.6;
8. omit a member it does not know rather than guess it. Every member other than
   those the schema requires is optional, and an absent member is a smaller
   failure than a wrong one.

A conforming writer SHOULD record `generator` with its own name and version, so
that a label whose provenance is later doubted can be traced to the program that
made it. A writer whose output is deterministic SHOULD make `generator.created`
injectable, so that two runs over the same input produce the same bytes.

Obligations specific to each carrier — where files are attached, what the
declaration says, what to do with a signed document — are in section 6.

### 4.3 Conforming readers

A conforming reader MUST:

1. **validate before trusting.** It MUST validate a label against the page-label
   schema for the version the label declares, before using any value in it. No
   value in an unvalidated label may be relied on, displayed as fact, or passed
   to another system;
2. **treat an invalid label as absent.** A label that fails validation MUST be
   treated exactly as though the document carried no label: the reader falls back
   to the page. A reader MUST NOT use the parts of an invalid label that happen to
   parse, and MUST NOT repair it;
3. **treat an unknown version as absent.** A reader that does not implement the
   version in `planlabel` MUST treat the label as absent rather than parse it
   partially;
4. **resolve disagreement in favour of the page** — 1.2 (6);
5. **not present inferred content as fact.** A reader that presents label content
   to a person or to another system MUST make the provenance of that content
   available with it, and MUST NOT present content whose effective provenance is
   `inferred` as though it were `authored` — 4.6;
6. **ignore what it does not understand** inside `extensions`, and never require an
   extension to be present;
7. **treat the label as untrusted input**: not execute it, not dereference it, and
   bound the resources it spends parsing it — section 9.

A conforming reader MAY ignore labels entirely. Ignoring a label is always
conforming behaviour, and a reader that does so is not a lesser reader of the
document — only a reader that gets less out of it.

A reader MUST NOT assume that the presence of an index implies a label on every
page, or that the absence of an index implies no labels: the index lists what was
labelled, and pages that were not labelled are simply absent from it.

### 4.4 Conforming validators

A validator MUST NOT modify the document or the label it validates, under any
circumstance, including to repair an error it has just reported.

A validator MUST report, for each label it examines:

1. the declared version, and whether the validator implements it;
2. **schema validity**, and for each failure a machine-readable location — a JSON
   Pointer into the label — together with a human-readable message;
3. the **conformance level reached**, or that none was, per 4.1;
4. **counts** of viewports, elements and annotations;
5. every violation of the **identity and reference rules** of 4.5;
6. every violation of the **geometric rules**: bounding-box ordering (3.4),
   geometry within its bounding box (3.4), bounding boxes within the page, page
   dimensions matching the page (3.1), a singular or `scale`-contradicting
   `paperToPlane` (3.5), and non-orthonormal plane axes (3.6). The geometric
   tolerance is 0.5 mm on paper unless a rule states otherwise;
7. every violation of the **provenance rules** of 4.6;
8. its own identity and version, so that a report can be reproduced.

A validator MUST classify each finding as an **error** or a **warning**, and MUST
report which. An error is a violation of a MUST in this specification. A warning
is a violation of a SHOULD, or a finding that depends on a tolerance or a heuristic
— an element's bounding box falling outside its viewport's, for instance, which is
legitimate for a tag drawn in the margin. A validator MUST NOT report a warning as
an error; a label with warnings and no errors is conforming.

A validator MUST report the level reached even when it also reports errors, since
"L3 with four referential errors" is a more useful statement than either half.

### 4.5 Identity and references

**localId.** Every viewport, element and annotation carries an `id`, matching the
`localId` pattern `^[A-Za-z0-9_.:-]+$`. Within one page label, all `id` values
MUST be unique **across all three collections**, which share a single namespace.
They share one because references do not say which collection they point into:
`measures` may name an element or a grid annotation, and uniqueness across the page
is what makes such a reference unambiguous.

A `localId` is opaque. A reader MUST NOT derive meaning from its text, and MUST NOT
assume it is stable across revisions of a sheet: an authoring tool that re-exports
a sheet may legitimately renumber everything on it.

**ifcGuid is not a key.** An element entry describes an appearance (2.5), so two
element entries on one page MAY carry the same `ifcGuid`. A reader MUST NOT use
`ifcGuid` to identify an entry within a page, and MUST NOT assume it is unique. It
MAY use it to join a page to a model, which is what it is for.

**References within a page.** These members hold `localId` references and MUST
resolve within the same page label:

| Member | MUST reference |
| --- | --- |
| `element.viewport`, `annotation.viewport` | a viewport in this label |
| `annotation.shows.element` | an element in this label |
| `annotation.measures[*]` | an element, or an annotation of type `grid` or `level`, in this label |

`measures` admits levels as well as grids because a dimension in a section runs
between levels, and refusing to record that link would lose a meaning that is
visibly on the page.

A reference that does not resolve is an error; a validator MUST report it, and a
reader MUST treat the referencing object as though the member were absent rather
than invent a target.

**References across pages.** `target` is the only member through which a label may
refer to another page, and a `localId` from another page MUST NOT appear anywhere
else. Within `target`:

- `pdfPage` is a zero-based page index **within the same document**, and MUST be
  less than that document's page count;
- `sheetId` names a sheet by its printed number, which MAY be in another document;
- `viewportId` is a `localId` interpreted **in the targeted page**, and is the one
  place a foreign `localId` is permitted;
- `detail` is free text, as printed.

`sheet.id` SHOULD be unique within a document. Where it is not, a reader resolving
a `target` MUST prefer `pdfPage` if it is present, and MAY resolve to any matching
page otherwise.

**Page identity.** `page.index` MUST equal the zero-based index of the page the
label is attached to, and an index entry's `pageIndex` MUST equal the `page.index`
of the label it describes. A reader that finds otherwise MUST trust the attachment
over the value: the page a label is attached to is a fact about the document, and
`page.index` is a claim inside a label.

### 4.6 Provenance

Provenance is the member that says where a value came from. It is what lets a
reader tell a measurement taken from the model from a number recovered off a
picture, and it is the reason a label can be trusted at all.

**4.6.1 The three values.**

| Value | Meaning |
| --- | --- |
| `authored` | The value came from the record from which the drawing was produced — the model, or the authoring tool's own account of the drawing. It was **known**, not recovered. |
| `inferred` | The value was reconstructed after the fact, from the drawing or from any other evidence, by a process without access to that record. It was **recovered**, however carefully. |
| `mixed` | Aggregate only: some values in this label are authored and some are inferred. |

**4.6.2 `mixed` is an aggregate.** A writer SHOULD NOT put `mixed` on an element or
an annotation. An object whose members come from both sources SHOULD be recorded as
`inferred` as a whole, because nothing in the format could say which of its members
were which, and a reader that assumed the wrong half would be wrong without knowing
it.

This is advice and not a prohibition: the schema gives element and annotation
`provenance` the same three-value vocabulary as the top level, so `mixed` on an item
is well-formed and a conforming reader MUST accept it. A reader encountering it MUST
treat the object as containing inferred content — that is, no less cautiously than
`inferred`. Narrowing this to a prohibition is a candidate for a future schema
version, where the restriction could be expressed and checked rather than merely
asserted.

**4.6.3 Absence.** `provenance` is optional on an element and on an annotation.
Where it is absent, the object's *effective provenance* is the label's top-level
`provenance`, except that where the top level is `mixed` the effective provenance
is `inferred`. Every rule in this section is written in terms of effective
provenance. A reader MUST NOT read an absent `provenance` as `authored` unless the
top level is `authored`.

**4.6.4 Aggregation.** The top-level `provenance` describes the whole label —
`sheet`, `page` and `viewports` as well as the elements and annotations. It MUST
be:

- `authored` if and only if **every** value in the label is authored;
- `inferred` if and only if **every** value in the label is inferred;
- `mixed` otherwise.

In particular the top level MUST NOT be `authored` unless every element and every
annotation is authored; and a label recovered entirely from an unlabelled drawing
is `inferred`, not `mixed`. The index's `provenance` aggregates the same way over
every page of the document.

Only elements and annotations carry a provenance of their own. Where the top level
is `mixed`, a reader MUST NOT assume that any value outside them — a sheet number,
a scale, a viewport transform — was authored.

4.6.4 binds the writer and 4.6.3 binds the reader, and they meet only if the writer
is explicit. **Where the top-level `provenance` is `mixed`, a writer MUST record
`provenance` on every element and every annotation.** A writer that leaves them out
has, by 4.6.3, told every reader that all of them are inferred, which is a smaller
claim than the truth but not the one it meant to make.

**4.6.5 Confidence.** An element or annotation whose effective provenance is
`inferred` MUST carry a `confidence`, a number in [0, 1]. An element or annotation
whose effective provenance is `authored` SHOULD NOT carry a `confidence`: the member
has no meaning there, and its presence would invite a reader to discount a value that
is not in doubt. A validator MUST report a missing `confidence` on an inferred object
as an error, and SHOULD report a `confidence` on an authored object as a warning.

The asymmetry is deliberate. The design brief requires the first — an inferred value
that does not say how sure it is has withheld the thing that makes it safe to use —
and the schema cannot express it, so the validator must. The second is untidiness
rather than a loss of meaning, and rejecting a label over it would serve nobody.

`confidence` is monotone — a higher value means the writer considers the value more
likely to be right — and nothing more. It is not calibrated, and a reader MUST NOT
compare confidences produced by different writers, or by different versions of one
writer, as though they were on a common scale.

Values outside elements and annotations have no `confidence` member. A reader MUST
treat such a value as unquantified whenever its effective provenance is not
`authored`.

**4.6.6 A writer MUST NOT label reconstructed data as `authored`.**

This is the rule the rest of the section exists to support, and the one violation
of this specification that is not merely a defect. Every other error makes a label
less useful; this one makes it a claim about where the drawing's meaning came
from that the writer cannot support, in a document that may be issued, checked and
relied upon.

Three consequences:

- **Confirmation is not authorship.** A person who reviews an inferred value and
  finds it correct has raised its `confidence`, at most to 1. The value is still
  `inferred`, because `authored` is a statement about the source of the value, not
  about anyone's belief in it. A writer MUST NOT promote `inferred` to `authored`
  on review, on agreement with a model, or on any other evidence short of having
  the authoring record itself.
- **Provenance survives copying.** A writer that copies, merges or re-emits labels
  produced elsewhere MUST preserve their provenance and confidence, and MUST
  recompute the aggregate of 4.6.4 over the result.
- **Under-claiming is not an error.** Marking an authored value `inferred` wastes
  information but asserts nothing false. A writer SHOULD NOT do it; a validator
  MUST NOT report it as an error, because it cannot distinguish it from an honest
  reconstruction.

## 5. Provenance

Provenance is specified normatively in [4.6](#46-provenance). It lives under
conformance because the rules there are obligations on a writer and a reader, not a
description of a data member.

This section is retained, rather than deleted and the rest renumbered, so that
sections 6 to 9 keep the numbers the README and the cross-references in section 4
already use.

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
