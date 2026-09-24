---
title: Plannotation Specification
version: "0.1"
status: draft
canonical: https://plannotation.github.io/spec/0.1
---

# Plannotation 0.1 — Specification

**Status: draft.** This document is the normative specification for Plannotation 0.1.
Every section is complete. Sections marked *informative* explain or illustrate and
impose no requirement; everything else is normative. While the version is `0.x` the
format may still change between minor versions (8.2).

The canonical location of this document is
<https://plannotation.github.io/spec/0.1>. The `conformsTo` value written into
a plannotated PDF's XMP declaration is exactly that URI.

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT,
RECOMMENDED, MAY and OPTIONAL are to be interpreted as described in
[RFC 2119](https://www.rfc-editor.org/rfc/rfc2119) and
[RFC 8174](https://www.rfc-editor.org/rfc/rfc8174) when, and only when, they
appear in all capitals.

---

## 1. Scope

### 1.1 What this specification defines

This specification defines **Plannotation 0.1**: a small JSON document, called a
*plannotation*, that states what is on one page of a 2-D construction drawing —
which sheet the page is, which viewports it contains and how paper maps to model
space, which elements are drawn and which annotations are placed on them —
together with a document-level *index* of the plannotated pages, the rules for the
coordinates both use, and the obligations of the programs that write and read
them.

It specifies the payload and its meaning. It does not specify how a drawing is
produced, how it is drawn, or what it should contain.

### 1.2 The governing principle

> **The PDF page is the leading document. The plannotation is auxiliary.**

Everything below follows from that sentence, so it is stated normatively here and
its consequences are given as requirements.

1. **Appearance is untouched.** A conforming writer MUST NOT change how any page
   of a document looks or prints. Rendering any page of the plannotated document
   MUST produce output identical to rendering the corresponding page of the input
   document, for the same renderer at the same settings, at any resolution and on
   any medium. A conforming writer MUST NOT modify a page's content streams and
   MUST NOT add a visible mark of any kind. The file's bytes necessarily change —
   a plannotation has to be stored somewhere — but nothing a reader of the drawing
   can see does.

2. **Additive, never subtractive.** A conforming writer MUST NOT remove or alter
   content that it did not itself write, including attachments, annotations,
   form fields and metadata placed by other producers. The only metadata a
   conforming writer adds is the declaration described in section 6.

3. **A plannotation is not a drawing.** A plannotation is never sufficient to
   reconstruct the drawing it describes, and this specification makes no attempt
   to make it so. It records bounding boxes, outlines and identities — what is
   where and what it means — not line weights, hatching, text placement or any
   other matter of presentation. A program that needs the drawing MUST read the
   page.

4. **A plannotation is not a model.** Where a plannotation carries model-derived
   values, it carries them at the precision defined in section 3.8, which is
   coarser than the model's. A program that needs model geometry MUST read the
   model.

5. **Ignoring the plannotation is conforming.** A reader that does not know about
   Plannotation, or that chooses not to use it, behaves exactly as it did before
   the plannotation existed. It loses nothing but meaning. No part of this
   specification may be read as requiring a consumer of the document to process
   the plannotation.

6. **The page wins.** Where a plannotation and the page it describes disagree, the
   page is correct and the plannotation is wrong. A reader MUST resolve such a
   disagreement in favour of the page. A writer MUST NOT repair a page to agree
   with a plannotation.

7. **No PDF annotations.** A conforming writer MUST NOT add an annotation object
   to any page — no entry in a page's `/Annots` array, visible or hidden — and
   MUST NOT change what the page shows. In AEC practice and in PDF alike, an
   *annotation* usually means visible markup — a dimension or tag on the sheet
   (2.6), a comment, stamp or redline added in a viewer — whereas the name
   Plannotation means annotating plans for machines: a plannotation describes the
   annotations a page already shows and adds none of its own.

### 1.3 Exclusions

The following are outside the scope of this specification. Each is listed because
it has been asked for, not because it is unimaginable.

| Excluded | Note |
| --- | --- |
| A new drawing file format | Plannotation annotates documents in formats that already exist. |
| Any change to how a PDF is drawn | See 1.2 (1). Content streams are out of bounds. |
| Reconstruction of a drawing from a plannotation | See 1.2 (3). The plannotation is lossy by design. |
| Reconstruction of a model from a plannotation | See 1.2 (4). A plannotation references a model; it does not contain one. |
| Authoring-tool integration | How a CAD application obtains the values is its own business; this specification constrains only what it writes. |
| The inference process | How a plannotation is recovered from an unplannotated drawing is unspecified. This specification constrains only how such a plannotation is marked — see 4.6. |
| Presentation | No viewer behaviour, no overlay style, no user interface is normative. |
| Networked exchange | No service, protocol or API is defined. A plannotation travels with its document. |
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
given; it is informative, and does not make IFC a prerequisite for reading a
plannotation.

Terms defined here are printed in *italics* on first use elsewhere in this
document. JSON member names are printed as `code`.

### 2.1 sheet

The drawing as a document in its own right: the thing that carries a sheet number,
a title, a revision, a scale and a title block, and that a project issues, checks
and supersedes. **SWAPP calls this a Sheet and this specification keeps that name.**
A sheet is not a page: it is what the page is a printing of.

In IFC a sheet corresponds to an `IfcDocumentInformation` whose `Scope` is
`"SHEET"`.

In a plannotation, the sheet is the `sheet` member, and there is exactly one per
page.

### 2.2 page

One page of the document that carries the drawing, identified by its zero-based
index within that document, and having a width, a height and a rotation. The page
is the leading document of section 1.2.

In a plannotation, the page is the `page` member. `page.index` MUST equal the
zero-based index of the page the plannotation describes.

A page carries exactly one sheet. A sheet MAY be printed on more than one page, in
which case each page carries its own plannotation and the plannotations repeat the
sheet.

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
viewports, the plannotation records two viewports.

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

An element MAY have no `ifcGuid` — a plannotation recovered from a drawing usually
knows that a wall is there without knowing which wall it is.

### 2.6 annotation

A mark on the sheet that says something about the drawing rather than depicting
the building: a dimension, a tag, a callout, a grid line, a level, a section mark,
a north arrow. **SWAPP calls this an Annotation and this specification keeps that
name.**

In IFC an annotation corresponds to an `IfcAnnotation`.

What makes an annotation useful to a machine is its *link* — the member that says
what it annotates: `measures`, `shows`, `target`, `axis` or `ifcGuid`. Conformance
level L3 is defined in terms of those links; see 4.1.

### 2.7 plannotation

The JSON object that describes exactly one page, conforming to the plannotation
schema `plannotation.schema.json`. A plannotation is data. It is never executable,
and a reader MUST NOT treat it as though it were; see section 9.

Written with a capital, *Plannotation* names this specification and the format it
defines; in lower case, *plannotation* always means the per-page object defined
here. A document whose pages carry plannotations is *plannotated*, and attaching
them is *plannotating* it. The index of 2.8 is not a plannotation.

### 2.8 index

The JSON object that lists, for one document, which pages carry plannotations,
what sheet each of them is, and which conformance level each plannotation reaches.
It conforms to `plannotation-index.schema.json`, which is deliberately
self-contained so that an index can be validated without also fetching the
plannotation schema.

The index is a convenience, not a source of truth: it lets a reader see what a
document contains without opening every plannotation. Where the index and a
plannotation disagree, the plannotation is correct — the index is to the
plannotations what the plannotations are to the page. **SWAPP's DocumentSet is the
nearest concept**, though SWAPP's is a set of sheets and Plannotation's is a
manifest of one document's pages.

### 2.9 carrier

The mechanism that binds a plannotation to the document it describes: embedded
files in a PDF, added data in an SVG, or a sidecar JSON file. A carrier changes
how a plannotation travels, never what it means. The three carriers are specified
in section 6.

### 2.10 provenance

Where a value came from: from the record that produced the drawing (`authored`),
or from a reconstruction of the drawing after the fact (`inferred`), or, at the
level of a whole plannotation, from both (`mixed`).

Provenance is an assertion the writer makes about its own knowledge, and it is the
one assertion this specification will not let a writer make loosely. The rules are
normative in 4.6.

### 2.11 conformance level

One of L1, L2 or L3: a statement about how much of a page a plannotation
describes, not about how well it describes it. The levels are cumulative and are
defined in 4.1. A level is a property of a single plannotation.

### 2.12 paper coordinates

The coordinate system in which every geometric value in a plannotation is expressed:
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

A program that produces a plannotation, an index, or both, and attaches them to a
document through a carrier. Obligations in 4.2.

### 2.16 reader

A program that obtains a plannotation from a document and uses it. Obligations in
4.3.

### 2.17 validator

A reader whose output is a report about a plannotation's conformance rather than a
use of the plannotation's content. Obligations in 4.4. A validator is a reader and
is bound by everything 4.3 requires of one, including the requirement not to
modify what it reads.

### 2.18 Correspondence summary (informative)

| Plannotation | SWAPP ifc-docs | IFC |
| --- | --- | --- |
| index | DocumentSet (approximately) | — |
| sheet | **Sheet** | `IfcDocumentInformation`, `Scope = "SHEET"` |
| viewport | **ViewPort** | `IfcAnnotation` aggregated with `IfcRelAggregates` |
| view | **View** | `IfcAnnotation`, `ObjectType = "DRAWING"`, `EPset_Drawing` |
| annotation | **Annotation** | `IfcAnnotation` |
| element | referenced, not named | the `IfcProduct` occurrence, by `GlobalId`; related with `IfcRelAssignsToProduct` |
| plannotation, carrier, provenance, conformance level, paper coordinates, model plane | — | — |

---

## 3. Coordinate conventions

This section is normative in full. It says the same thing as the module docstring
of `plannotation/units.py`, which is the conventions' home in code; the two are kept
in step and neither may be changed alone.

### 3.1 Paper coordinates

Every geometric value in a plannotation — every `bbox`, every `point`, every
`polyline`, and the `x` and `y` inputs of every `paperToPlane` — is expressed in
**paper coordinates**:

- the unit is the **millimetre**;
- the origin is the **bottom-left corner of the page**;
- **x increases to the right and y increases upwards**;
- the axes are the page's own, not the viewer's; see 3.7 for rotation.

The choice is not arbitrary and is not a matter of taste. It is the PDF
convention: default PDF user space has its origin at the bottom-left of the page
with y increasing upwards. Adopting it means a plannotation and the page it
describes agree about which way is up, so that no implementation has to flip
anything to compare them, and so that a coordinate read out of a plannotation can
be handed to a PDF operation unchanged but for a scale factor. The governing
principle of 1.2 decides this: the page's convention leads, and the plannotation
follows it.

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
plannotation wrongly, silently.

An A3 page, 420 mm × 297 mm, is 1190.551 pt × 841.89 pt at `UserUnit` 1.

### 3.3 Relation to SVG

SVG is **y-down**: its origin is the top-left corner and y increases downwards.
Every coordinate crossing the boundary between an SVG carrier and a plannotation
MUST therefore be flipped about the horizontal axis. For a page of height `h`
millimetres the flip is its own inverse, and x is unchanged:

```
y_paper = h − y_svg
y_svg   = h − y_paper
```

The formula assumes the SVG's user unit is the millimetre. Where it is not, a
writer MUST convert to millimetres before flipping; where the SVG's root
coordinate system is offset from the page corner, the writer MUST remove that
offset first.

Forgetting the flip produces a plannotation that is mirrored about the page's
horizontal centre line — an error that is invisible on a small, roughly centred
drawing and obvious on a title block. Implementations SHOULD apply the flip in
exactly one place per direction rather than at each call site.

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

so that a transform read out of a plannotation can be handed to a PDF operator, or
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
part is a similarity, the two MUST agree to within 0.1 % of `scale` plus what
rounding explains (3.8). Rounding each coefficient by up to 0.0005 moves `k` by at
most 2 × 0.0005 = 0.001, which is 1 in `S` for a model in metres, 0.01 in
centimetres and 0.001 in millimetres. A validator MUST report a larger
disagreement. Where the linear part is not a similarity, `k` is undefined and no
scale can be recovered; `scale` then stands alone and a validator MUST NOT check
it against the transform.

In the component order above, a similarity has `a = d` and `c = −b` (a rotation)
or `a = −d` and `c = b` (a reflection). Rounding need not keep those equalities
exact, so a serialised transform is a similarity where some similarity lies within
0.0005 of each of its coefficients: where `|a − d|` and `|b + c|`, or `|a + d|` and
`|b − c|`, are both at most 0.001.

### 3.6 The model plane

A viewport's `plane` gives the model-space plane the view projects onto, as an
`origin` and two axis directions `xAxis` and `yAxis`, all `vec3` in the model's
length unit (the axes being directions, their unit is immaterial).

`xAxis` and `yAxis` MUST be unit vectors and MUST be mutually orthogonal. The
requirement exists because scale is already carried by `paperToPlane`: axes of a
length other than one would express it a second time and the two statements could
disagree.

An oblique axis has no exact spelling at the three decimals of 3.8: a plan turned
29.17° to follow its building's grid has `xAxis` `[0.873, −0.487, 0]`, which is
0.99965 long. Both requirements therefore hold to within what that rounding
explains, and a validator MUST report an axis or a pair beyond it:

- each axis's length is within √3 × 0.0005 ≈ 0.00087 of 1: rounding three
  components by up to 0.0005 each moves a vector by at most that, and so changes
  its length by no more;
- `|xAxis · yAxis|` is at most 2√3 × 0.0005 + 3 × 0.0005² ≈ 0.0017, which bounds the
  dot product rounding can give two orthogonal unit vectors.

An axis along a model axis, such as `[1, 0, 0]`, is exact at three decimals and
gains nothing from this: 0.999 and 1.001 are both outside it.

Plane coordinates `(X, Y)` — the output of `paperToPlane` — map to a model point:

```
P = origin + X·xAxis + Y·yAxis
```

and a model point `P` lying on the plane maps back by projection:

```
X = (P − origin)·xAxis        Y = (P − origin)·yAxis
```

A model point that does not lie on the plane projects along the plane normal. A
plannotation records where a thing was **drawn**, not where it is; for anything
cut or projected, the two differ, and the plannotation does not carry the
difference.

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
component of `plane.origin` along the plane normal, `plane.origin · n / |n|`, MUST
equal `storey.elevation + cutHeight`, to within (2 + √3) × 0.0005 ≈ 0.0019 model
length units, which bounds how far rounding the three can separate them (3.8). A
validator MUST check this, and a writer that cannot satisfy it has misplaced one of
the three.

So a plan of a storey whose finished floor is at elevation 3.0 m, cut 1.2 m above
that floor, has `storey.elevation` 3.0, `cutHeight` 1.2, and a `plane.origin` whose
normal component is 4.2. `storey.elevation` is absolute; `cutHeight` is relative to
it.

**Storey elevation.** Absolute means in model coordinates: `storey.elevation` is the z
of the storey's floor in the coordinates `plane.origin` is stated in, which is what
lets the sum above be compared with the plane. It is not the storey's height above the
building's own ±0,00, which is what a level mark prints and what IFC records in
`IfcBuildingStorey.Elevation`; the two agree only where the model places that datum at
z = 0. The Maleva 18 model stands its ground floor at z = 14.30 m, so a plan of that
floor cut 1.2 m above it has `storey.elevation` 14300 and a plane at z = 15500 in
millimetres, and the floor's level mark reads ±0,00.

**Level elevation.** A `level` annotation's `elevation` is the other number: the
height its mark states, in metres, from the datum the mark states it from, which is
the building's ±0,00 on most drawings and sea level on a site plan. Where the mark
stands in the model is already given by its position on the paper, through its
viewport's transform, and is not stated a second time. On a section of the Maleva 18
model the mark reading +3,35 has `elevation` 3.35 and stands at z = 17.65 m. The
marks of one section that share a datum therefore differ exactly as their heights on
the plane do.

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
rotation, and a plannotation that were expressed after it would be the only thing
in the file that needed converting before it could be compared with anything else.

`page.rotation` is recorded so that a reader can reproduce the displayed
orientation, and for no other purpose. A reader overlaying plannotation geometry
on a rotated rendering of the page MUST apply the same rotation to that geometry.
For a page of unrotated width `w` and height `h`, a paper point `(x, y)` appears
in the displayed page at:

| `page.rotation` | displayed position | displayed page size |
| --- | --- | --- |
| 0 | `(x, y)` | `w × h` |
| 90 | `(y, w − x)` | `h × w` |
| 180 | `(w − x, h − y)` | `w × h` |
| 270 | `(h − y, x)` | `h × w` |

with the displayed coordinates still measured from the bottom-left corner, y up.

### 3.8 Serialisation

**Precision.** Every number in a plannotation and in an index, without exception,
MUST be serialised with **at most three decimal places**, rounded **to nearest,
ties to even**: of the two three-decimal candidates, the nearer is chosen, and
where the value lies exactly halfway between them the candidate whose last digit
is even is chosen.

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

A plannotation MUST NOT contain a NaN or an infinity. Neither is JSON, neither can
be a coordinate, and a writer that arrives at one MUST fail rather than emit it.

**Canonical form.** A plannotation or index MUST be serialised as:

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

Two writers given the same plannotation therefore produce the same bytes, so a
plannotation can be compared, hashed and checked into version control like any
other source.

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
obtain it from the model. The plannotation says what is on the page; it is no more a
substitute for the model than it is for the drawing.

**Relations among rounded numbers.** Four requirements relate serialised numbers
exactly: that the plane axes are unit vectors and orthogonal (3.6), that `scale`
agrees with `paperToPlane` (3.5), and that `plane.origin` sits at
`storey.elevation + cutHeight` (3.6). A writer meets each with the values it holds
before rounding, and rounding may then leave the serialised numbers slightly off.
Each of those clauses therefore states a tolerance, derived from the 0.0005 by which
rounding can move one number, that admits everything rounding can explain; a
validator MUST NOT report a violation within it and MUST report one beyond it. The
requirements on a transform's determinant (3.5) and on bounding boxes (3.4) are not
relaxed. A reader inverts the transform the file holds, so its determinant is a
fact about the file; and rounding is monotonic, so it can neither reverse a box nor
move one out of a box that held it.

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
plannotation the trailing zeros would be dropped, by 3.8.

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
150 mm, on a drawing whose own tolerance is a few millimetres. The plannotation
still says correctly that a wall corner is drawn at paper (168.400, 131.200) —
that is a fact about the page, and it is what the plannotation is for.

---

## 4. Conformance

### 4.1 Conformance levels

A conformance level says how much of a page a plannotation describes. It says
nothing about how accurate the description is: accuracy is what provenance (4.6)
and validation (4.4) are for, and a careless L3 plannotation is worth less than a
careful L1 one. A plannotation may reach L3 and still violate rules in 4.5 or 4.6;
a validator reports the level and the errors side by side, and neither cancels the
other.

| Level | A plannotation reaches this level when |
| --- | --- |
| **L1** | It is valid against the plannotation schema. `page` and `sheet` are therefore present; `viewports` MAY be. |
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

The levels are cumulative: L3 implies L2 implies L1. A plannotation that is not
valid against the schema reaches no level at all, and a reader MUST treat it as
absent (4.3).

A writer MUST NOT omit or weaken an annotation in order to reach a level. A
plannotation that describes the page less truthfully in exchange for a higher
letter has defeated its own purpose; a validator cannot detect this and the
obligation is on the writer.

A level is a property of one plannotation. The index records the level of each
plannotated page. This specification defines no conformance level for a document
as a whole, and a reader MUST NOT infer one; where a single level is quoted for a
document informally, it is the lowest reached by any of its plannotated pages.

### 4.2 Conforming writers

A conforming writer MUST:

1. produce plannotations valid against the plannotation schema, and, where it
   writes one, an index valid against the index schema, both at the version named
   in their `plannotation` member;
2. serialise both in the canonical form of 3.8, with every number at three
   decimals or fewer;
3. leave every page's appearance unchanged, add nothing visible, and add no PDF
   annotation object to any page — 1.2 (1), (7);
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
that a plannotation whose provenance is later doubted can be traced to the program
that made it. A writer whose output is deterministic SHOULD make
`generator.created` injectable, so that two runs over the same input produce the
same bytes.

Obligations specific to each carrier — where files are attached, what the
declaration says, what to do with a signed document — are in section 6.

### 4.3 Conforming readers

A conforming reader MUST:

1. **validate before trusting.** It MUST validate a plannotation against the
   plannotation schema for the version the plannotation declares, before using any
   value in it. No value in an unvalidated plannotation may be relied on,
   displayed as fact, or passed to another system;
2. **treat an invalid plannotation as absent.** A plannotation that fails
   validation MUST be treated exactly as though the document carried no
   plannotation: the reader falls back to the page. A reader MUST NOT use the
   parts of an invalid plannotation that happen to parse, and MUST NOT repair it;
3. **treat an unknown version as absent.** A reader that does not implement the
   version its `plannotation` member names MUST treat the plannotation as absent
   rather than parse it partially;
4. **resolve disagreement in favour of the page** — 1.2 (6);
5. **not present inferred content as fact.** A reader that presents plannotation
   content to a person or to another system MUST make the provenance of that
   content available with it, and MUST NOT present content whose effective
   provenance is `inferred` as though it were `authored` — 4.6;
6. **ignore what it does not understand** inside `extensions`, and never require an
   extension to be present;
7. **treat the plannotation as untrusted input**: not execute it, not dereference
   it, and bound the resources it spends parsing it — section 9.

A conforming reader MAY ignore plannotations entirely. Ignoring a plannotation is
always conforming behaviour, and a reader that does so is not a lesser reader of
the document — only a reader that gets less out of it.

A reader MUST NOT assume that the presence of an index implies a plannotation on
every page, or that the absence of an index implies no plannotations: the index
lists what was plannotated, and pages that were not plannotated are simply absent
from it.

### 4.4 Conforming validators

A validator MUST NOT modify the document or the plannotation it validates, under any
circumstance, including to repair an error it has just reported.

A validator MUST report, for each plannotation it examines:

1. the declared version, and whether the validator implements it;
2. **schema validity**, and for each failure a machine-readable location — a JSON
   Pointer into the plannotation — together with a human-readable message;
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
an error; a plannotation with warnings and no errors is conforming.

A validator MUST report the level reached even when it also reports errors, since
"L3 with four referential errors" is a more useful statement than either half.

### 4.5 Identity and references

**localId.** Every viewport, element and annotation carries an `id`, matching the
`localId` pattern `^[A-Za-z0-9_.:-]+$`. Within one plannotation, all `id` values
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
resolve within the same plannotation:

| Member | MUST reference |
| --- | --- |
| `element.viewport`, `annotation.viewport` | a viewport in this plannotation |
| `annotation.shows.element` | an element in this plannotation |
| `annotation.measures[*]` | an element, or an annotation of type `grid` or `level`, in this plannotation |

`measures` admits levels as well as grids because a dimension in a section runs
between levels, and refusing to record that link would lose a meaning that is
visibly on the page.

A reference that does not resolve is an error; a validator MUST report it, and a
reader MUST treat the referencing object as though the member were absent rather
than invent a target.

**References across pages.** `target` is the only member through which a
plannotation may refer to another page, and a `localId` from another page MUST NOT
appear anywhere else. Within `target`:

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
plannotation is attached to, and an index entry's `pageIndex` MUST equal the
`page.index` of the plannotation it describes. A reader that finds otherwise MUST
trust the attachment over the value: the page a plannotation is attached to is a
fact about the document, and `page.index` is a claim inside a plannotation.

### 4.6 Provenance

Provenance is the member that says where a value came from. It is what lets a
reader tell a measurement taken from the model from a number recovered off a
picture, and it is the reason a plannotation can be trusted at all.

**4.6.1 The three values.**

| Value | Meaning |
| --- | --- |
| `authored` | The value came from the record from which the drawing was produced — the model, or the authoring tool's own account of the drawing. It was **known**, not recovered. |
| `inferred` | The value was reconstructed after the fact, from the drawing or from any other evidence, by a process without access to that record. It was **recovered**, however carefully. |
| `mixed` | Aggregate only: some values in this plannotation are authored and some are inferred. |

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
Where it is absent, the object's *effective provenance* is the plannotation's
top-level `provenance`, except that where the top level is `mixed` the effective
provenance is `inferred`. Every rule in this section is written in terms of
effective provenance. A reader MUST NOT read an absent `provenance` as `authored`
unless the top level is `authored`.

**4.6.4 Aggregation.** The top-level `provenance` describes the whole plannotation —
`sheet`, `page` and `viewports` as well as the elements and annotations. It MUST
be:

- `authored` if and only if **every** value in the plannotation is authored;
- `inferred` if and only if **every** value in the plannotation is inferred;
- `mixed` otherwise.

In particular the top level MUST NOT be `authored` unless every element and every
annotation is authored; and a plannotation recovered entirely from an
unplannotated drawing is `inferred`, not `mixed`. The index's `provenance`
aggregates the same way over every page of the document.

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

The asymmetry is deliberate. The first is a loss of meaning — an inferred value
that does not say how sure it is has withheld the thing that makes it safe to use
— and the schema cannot express it, so the validator must check it. The second is
untidiness rather than a loss of meaning, and rejecting a plannotation over it
would serve nobody.

`confidence` is monotone — a higher value means the writer considers the value more
likely to be right — and nothing more. It is not calibrated, and a reader MUST NOT
compare confidences produced by different writers, or by different versions of one
writer, as though they were on a common scale.

Values outside elements and annotations have no `confidence` member. A reader MUST
treat such a value as unquantified whenever its effective provenance is not
`authored`.

**4.6.6 A writer MUST NOT mark reconstructed data as `authored`.**

This is the rule the rest of the section exists to support, and the one violation
of this specification that is not merely a defect. Every other error makes a
plannotation less useful; this one makes it a claim about where the drawing's
meaning came from that the writer cannot support, in a document that may be
issued, checked and relied upon.

Three consequences:

- **Confirmation is not authorship.** A person who reviews an inferred value and
  finds it correct has raised its `confidence`, at most to 1. The value is still
  `inferred`, because `authored` is a statement about the source of the value, not
  about anyone's belief in it. A writer MUST NOT promote `inferred` to `authored`
  on review, on agreement with a model, or on any other evidence short of having
  the authoring record itself.
- **Provenance survives copying.** A writer that copies, merges or re-emits
  plannotations produced elsewhere MUST preserve their provenance and confidence,
  and MUST recompute the aggregate of 4.6.4 over the result.
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

## 6. Carriers

### 6.1 General

A *carrier* is a way of transporting a payload, not a kind of payload. The
payload is what sections 1 to 4 define: plannotations, and the index that lists
them. It is the same payload in all three carriers, it means the same thing in
all three, and every rule in sections 3 and 4 applies to it unchanged, however it
arrived.

| Carrier | Where the payload lives | Specified in |
| --- | --- | --- |
| PDF | Embedded files, associated with the pages and with the document | 6.2, 6.3 |
| SVG | Data added to the SVG document | 6.4 (reserved at 0.1) |
| Sidecar JSON | A separate file beside the document | 6.5 |

**6.1.1 Support is declared, not assumed.** A reader states which carriers it
supports. A reader that claims to support a carrier MUST accept any payload that
reaches it through that carrier, and MUST NOT reject or discount a payload
because of which carrier it came in. A reader MUST NOT require a particular
carrier; there is no primary carrier from the payload's point of view, and a
program that reads only sidecars is a conforming reader of Plannotation.

Supporting no carrier at all is also conforming, by 4.3: a reader that ignores
plannotations entirely loses nothing but meaning.

**6.1.2 The carriers are equal in authority.** No carrier makes a stronger claim
than another. A plannotation embedded in a PDF and the same plannotation in a
sidecar are the same statement about the same page, and a reader MUST treat them
as equally authoritative. In particular, a reader MUST NOT treat a sidecar as a
draft, a cache or a second-class copy.

**6.1.3 Disagreement between carriers.** Equal authority is a statement about
meaning, not a procedure for a defective document. Where a reader obtains
payloads for the same page from more than one carrier and they are not identical,
it MUST resolve the disagreement as follows.

1. A reader MUST NOT merge payloads from two carriers. It selects one and uses it
   whole. Merging would produce a plannotation that no writer ever wrote, whose
   provenance aggregate (4.6.4) no longer describes anything, and whose internal
   references (4.5) may resolve across two documents that were never checked
   against each other.
2. A payload carried **inside the document** takes precedence over a payload
   carried **beside it**. For a PDF, the embedded payload of 6.2 wins over a
   sidecar; for an SVG, the in-document payload of 6.4 wins over a sidecar.
3. A reader MUST make the disagreement visible to whoever or whatever consumes
   its output. It MUST NOT resolve one silently.
4. A reader MAY let its user choose the other carrier explicitly, and MUST then
   report that it did so.

The precedence rule is decided by what each carrier can and cannot be separated
from. An embedded payload cannot be copied, mailed or archived without the
document it describes, and it was necessarily written against the bytes it now
sits inside. A sidecar is an ordinary file: it can be left behind, copied from a
neighbouring revision, edited by hand, or paired with a document it was never
written for. When the two have drifted apart, the one that travelled with the
document is the one that is still attached to the thing it describes.

The rule costs less than it appears to. In the case the sidecar exists for — a
document that MUST NOT be modified, so that nothing could be embedded in it —
there is no embedded payload and therefore no conflict. The case the rule actually
decides is a document that was plannotated, then re-plannotated into a sidecar
because it had since been signed. There the sidecar may well be the better
payload, which is why 6.1.3 (3) and (4) exist: the reader must say that the two
disagree, and its user may overrule it.

**6.1.4 Disagreement within a payload** is not a carrier question and is settled
elsewhere. Where the index and a plannotation disagree, the plannotation is correct
(2.8). Where a plannotation and the page it describes disagree, the page is correct
(1.2 (6)). Both rules apply in every carrier.

---

### 6.2 The PDF carrier

The PDF carrier embeds the payload as associated files: one file per plannotated
page, associated with that page, and one index file associated with the document.
It is the carrier the rest of this specification was designed around, and it is
the only carrier that a plannotated document cannot be separated from.

**6.2.1 What is written.** A conforming writer plannotating a PDF:

1. MUST embed, for each page it plannotates, one file containing the canonical bytes
   (3.8) of that page's plannotation, associated with **that page** with
   `/AFRelationship /Data`;
2. MUST embed exactly one file containing the canonical bytes of the index,
   associated with the **document catalog** with `/AFRelationship /Data`;
3. MUST register every file it embeds in the document's `EmbeddedFiles` name tree
   (6.2.5);
4. MUST write the XMP PDF Declaration of 6.3;
5. MUST leave every page it does not plannotate without any Plannotation
   association.

A writer MUST NOT associate a plannotation with the catalog, and MUST NOT
associate the index with a page. The association is what says which page a
plannotation describes, and 4.5 makes it the fact that outranks the plannotation's
own `page.index`.

**6.2.2 Names.** The embedded files are named:

| File | Name |
| --- | --- |
| The plannotation for page *n* | `plannotation-p` followed by *n* in decimal, left-padded with zeros to a minimum of four digits, followed by `.json` |
| The index | `plannotation-index.json` |

so page 0 is `plannotation-p0000.json`, page 42 is `plannotation-p0042.json`, and page
12345 — a page index of four digits or more pads to nothing and keeps all its
digits — is `plannotation-p12345.json`. The index *n* is the zero-based page index of
2.2, not the number printed on the sheet and not the position a viewer displays.

A reader MUST parse the digits as a decimal integer and MUST NOT assume exactly
four of them. A name matching `^plannotation-p([0-9]{4}|[1-9][0-9]{4,})\.json$` —
exactly the names the rule above produces, so `plannotation-p00000.json` is not one
— or equal to `plannotation-index.json` is a *Plannotation name*; every other name is
foreign, and 6.2.6 says what a writer does when one collides.

The name is a claim about which page a file describes. The association of 6.2.1
is the fact. Where they disagree, 6.2.12 decides.

**6.2.3 The file specification.** Each embedded file is described by a file
specification dictionary carrying exactly these members:

| Member | Value |
| --- | --- |
| `/Type` | `/Filespec` |
| `/F` | the name from 6.2.2 |
| `/UF` | the same name, as a text string |
| `/Desc` | the description below |
| `/AFRelationship` | `/Data` |
| `/EF` | a dictionary whose `/F` and `/UF` both reference the one embedded-file stream |

All six are REQUIRED. `/UF` is required by ISO 32000-2 and `/F` is retained for
readers that predate it; a writer MUST write both and MUST give them the same
value. `/EF` MUST carry both `/F` and `/UF`, and both MUST reference the **same**
stream object, so that no reader can be handed two different files under one
name.

`/AFRelationship` MUST be `/Data`. The payload is data about the page, not a
source for it, not an alternative to it and not a supplement to it; and the eight
values ISO 32000-2 defines are the only ones permitted, so a Plannotation-specific
relationship name is not available and would not be wanted.

`/Desc` MUST be present and non-empty. A conforming writer writes exactly
`Plannotation 0.1 for page N`, with *N* the zero-based page index,
for a plannotation, and exactly `Plannotation 0.1 index` for the index. The number
is zero-based so that the description agrees with the filename beside which a
viewer displays it. `/Desc` is for a person reading an attachment pane: a reader
MUST NOT derive any value from it, and MUST NOT use it to identify a file.

**6.2.4 The embedded-file stream.** The stream referenced from `/EF` carries:

| Member | Value |
| --- | --- |
| `/Type` | `/EmbeddedFile` |
| `/Subtype` | the MIME type `application/json` as a PDF name |
| `/Params` | `/Size`, `/CheckSum` and `/ModDate`, as below |

`/Subtype` is REQUIRED. Because the solidus is a PDF delimiter it MUST be written
with the number-sign escape — `/application#2Fjson` — in which the case of the
hexadecimal digits is not significant.

`/Params` is REQUIRED and MUST carry:

- `/Size`, the length in bytes of the **decoded** file, before any stream filter;
- `/CheckSum`, the 16-byte MD5 digest of those same decoded bytes, as defined by
  ISO 32000-2;
- `/ModDate`, a PDF date string.

`/CreationDate` is OPTIONAL; a writer SHOULD omit it, because it duplicates
`/ModDate` for a file that has only ever been written once and is one more value
to keep deterministic (6.2.14).

`/Size` and `/CheckSum` describe the payload, not the stream as stored. A writer
MAY compress the stream with any standard filter, and MUST NOT recompute either
value when it does: after compression `/Size` still reports the decoded length
and `/CheckSum` still digests the decoded bytes. A writer MUST NOT alter the
bytes of an embedded file after computing them. A validator MUST decode the
stream and MUST report a `/Size` or a `/CheckSum` that does not match what it
decoded.

*Informative.* `/CheckSum` is an integrity check against accidental corruption and
nothing more. MD5 is not a sound basis for authenticity, ISO 32000-2 fixes the
algorithm so Plannotation cannot improve on it, and a reader MUST NOT treat a
matching `/CheckSum` as evidence that a plannotation is genuine. Section 9 governs
what a reader may conclude from attacker-supplied input.

**6.2.5 The `EmbeddedFiles` name tree.** Every file a writer embeds MUST also
appear in the document catalog's `/Names` `/EmbeddedFiles` name tree, under a key
equal to the file's `/UF`, referencing the same file specification object as the
`/AF` array does.

This is not automatic and it is not optional. An associated file that is not in
the name tree is invisible to every reader that enumerates attachments — a PDF
viewer's attachment pane, `pdf.js`'s `getAttachments()`, `pikepdf`'s
`attachments` mapping — and a name-tree entry that is associated with nothing
breaks PDF/A, which requires every embedded file to be associated. Both
directions are therefore required:

- every Plannotation file specification referenced from an `/AF` array MUST appear in
  the name tree;
- every Plannotation file specification in the name tree MUST be referenced from
  exactly one `/AF` array.

The `/AF` entry and the name-tree entry MUST be one indirect object referenced
twice, never two objects with equal contents. One object cannot disagree with
itself, and duplication is the only way a document could offer two different
plannotations for one page.

Name-tree keys MUST be sorted as ISO 32000-2 requires, and a reader MUST walk the
tree's `/Kids` rather than assuming a single `/Names` array: a document with many
attachments will have a branching tree, and a reader that reads only the root
node will silently miss plannotations.

**6.2.6 Name collisions.** A document MAY already contain an embedded file whose
name is a Plannotation name. Assigning over it would destroy a file Plannotation never
owned, and a later strip (6.2.13) would then delete a third party's data.

A writer MUST NOT overwrite an existing name-tree entry or embedded file whose
name is a Plannotation name unless **all** of the following hold, in which case the
file is part of a Plannotation payload this operation is replacing:

1. the document carries the Plannotation declaration of 6.3;
2. the entry's `/AFRelationship` is `/Data`;
3. the entry's embedded-file stream carries `/Subtype /application#2Fjson`.

Where they do not all hold, the writer MUST fail, and its message MUST name the
colliding file. A writer MUST NOT rename its own file to avoid the collision:
the names in 6.2.2 are how a reader finds the payload, and a payload under a
different name is not findable.

Re-plannotating a document that already carries a Plannotation payload is otherwise
unconstrained: a writer MAY replace the payload in place, and MAY instead remove
it (6.2.13) and write a fresh one. Either way the result MUST satisfy every rule
in this section, and MUST NOT leave a plannotation for a page it did not plannotate.

**6.2.7 Associated-file arrays.** The `/AF` array of a page or of the catalog MUST
be **appended to**, never assigned. Documents that already carry associated files
are ordinary — an electronic invoice carries one on the catalog and often one on
a page — and replacing the array would silently drop them while leaving them in
the name tree, producing exactly the unassociated entry 6.2.5 forbids.

- Where no `/AF` array is present, a writer creates one containing its entry.
- Where one is present, a writer appends and MUST preserve the existing entries
  and their order.
- A writer MUST NOT append a file specification that the array already
  references; attaching twice MUST NOT produce two entries for one file.
- Where removing entries would leave an `/AF` array empty, a writer MUST delete
  the `/AF` key rather than leave an empty array behind.

A reader MUST tolerate a null entry in an `/AF` array. A tool that deleted an
attachment without cleaning the arrays that referenced it leaves exactly that,
and a reader that dereferences it blindly fails on a document it could otherwise
have read.

**6.2.8 The appearance guarantee.** 1.2 (1) states the guarantee. This paragraph
states it as something a validator given both documents can check.

Let *D* be the input document and *D′* the plannotated output. A conforming writer
MUST satisfy all of the following, and a validator given both MUST check all of
them and MUST report each failure as an error.

1. **Pages.** *D′* has the same number of pages as *D*, in the same order.
2. **Content.** For every page, the decoded bytes of the page's content stream —
   the concatenation, in order, where `/Contents` is an array — are identical in
   *D* and *D′*. A writer SHOULD also leave the raw, encoded bytes identical, and
   MUST NOT change the filter of a content stream it did not write.
3. **Page dictionaries.** For every page, the members of the page dictionary are
   identical in *D* and *D′*, compared after resolving indirect references, with
   exactly one permitted difference: `/AF` MAY have been created or appended to
   per 6.2.7. `/MediaBox`, `/CropBox`, `/Rotate`, `/UserUnit`, `/Resources`,
   `/Annots`, `/Group` and `/Metadata` are therefore unchanged.
4. **Annotations.** Every annotation of every page is present in *D′*, in the same
   order, with the same subtype, rectangle, flags and appearance streams.
5. **Rendering.** For every page, rendering *D* and rendering *D′* produce
   identical images: the same renderer, the same build of it, the same process,
   the same settings, at a resolution of not less than 150 dpi, with
   anti-aliasing enabled. The comparison is **exact equality of every pixel**, not
   a tolerance. A difference in image dimensions MUST be reported separately from
   a difference in pixels, because it means the page's geometry or rotation
   changed rather than its content.

Rules 1 to 4 and rule 5 are both required because neither implies the other. A
writer can rewrite every content stream without moving a single pixel —
normalising whitespace and operator spelling does exactly that — so rule 5 alone
would not enforce 1.2 (1)'s prohibition on touching content streams. And rules 1
to 4 enumerate the mechanisms by which a page is usually damaged, while rule 5
checks the result, and so catches damage done by a mechanism this list does not
enumerate.

Exact equality is specified rather than a tolerance, and anti-aliasing is
required to be on, because together they are what gives the check its teeth: with
anti-aliasing enabled, exact comparison detects a line displaced by about a
hundredth of a point. A tolerance, or anti-aliasing switched off, would blunt
precisely the class of difference the rule exists to forbid.

**The guarantee is about the drawing, not about the file's bytes.** Writing a PDF
rewrites it: object numbers are renumbered, the cross-reference table is rebuilt,
the document `/ID` changes, and the output is never a byte-superset of the input.
A validator MUST NOT test the guarantee by comparing files, and a reader MUST NOT
expect a plannotated document to contain its input.

**6.2.9 What a writer MUST NOT do.** In addition to everything above, a
conforming writer MUST NOT:

1. modify any page's content streams, or add a mark of any kind that a renderer
   would draw;
2. remove, replace or alter any embedded file, annotation, form field, outline,
   structure element or other content it did not itself write;
3. alter any member of an existing XMP packet, or the document information
   dictionary, other than by adding the declaration of 6.3 and, where 6.3.7
   requires it, the extension schema that keeps that declaration legal — this
   prohibition includes `pdf:Producer`, `xmp:MetadataDate` and `pdf:PDFVersion`,
   each of which a naive metadata round-trip will rewrite;
4. record any trace of itself outside the payload and the declaration: not in
   `x:xmptk`, not in `pdf:Producer`, not in `/Info`, not in a custom key. After a
   strip (6.2.13) the document must retain no evidence that Plannotation touched it;
5. overwrite an embedded file whose name collides with a Plannotation name, except
   as 6.2.6 permits;
6. add, remove or change the document's encryption, or its permissions;
7. lower the PDF header version (6.2.10);
8. invalidate a digital signature silently (6.2.11).

A writer SHOULD leave the document's structural properties as it found them —
linearisation, object-stream mode, and the compression of streams it did not
write. None of these is visible, and none is forbidden, but each is a change a
downstream diff will show and none of them is Plannotation's business.

**6.2.10 The PDF header version.** Page-level associated files are a PDF 2.0
feature: ISO 32000-2 defines `/AF` on a page dictionary, and ISO 32000-1 does not.
A writer might therefore be tempted to raise the header version of every document
it plannotates. It MUST NOT do so unconditionally.

- A writer MUST NOT lower the header version, ever.
- A writer MUST NOT change the header version of a document that identifies
  itself as conforming to a standard that fixes it — any document whose XMP
  carries `pdfaid:part`, or a comparable identification for PDF/UA, PDF/X or
  PDF/E.
- A writer SHOULD leave the header version unchanged in every other case.
- Where a writer does raise it, it MUST raise it to exactly 2.0, MUST do so only
  in the absence of such an identification, and MUST NOT alter `pdf:PDFVersion`
  or any other XMP property to match — which, by 6.2.9 (3), it may not do anyway.

The reason is PDF/A-3. A PDF/A-3 document is a PDF 1.7 document; PDF/A-3 is where
associated files were introduced, so `/AF` is legitimate in it, and its XMP says
`pdfaid:part` 3. Raising its header to 2.0 would contradict the conformance claim
the document makes about itself, in order to describe a feature the document was
already entitled to use. The cost of leaving the header alone is nil: to a reader
that does not implement PDF 2.0, `/AF` is an unknown key, and an unknown key is
ignored — which is 1.2 (5) working exactly as intended.

**6.2.11 Signed documents.** A document may carry a digital signature, which
covers a range of its bytes. Rewriting the document moves those bytes, and the
signature no longer verifies.

A conforming writer MUST NOT invalidate a signature silently. Specifically:

1. **Detect.** Before modifying a document, a writer MUST determine whether it is
   signed. It MUST examine the interactive form's `/SigFlags` for the
   *SignaturesExist* bit, MUST walk the form's field tree — including `/Kids` —
   for a field with `/FT /Sig` carrying a `/V`, and MUST examine the catalog's
   `/Perms` for `/DocMDP` and `/UR3`. Any of these makes the document signed for
   the purpose of this rule.
2. **Refuse.** A writer MUST refuse, by default, to write a plannotated copy of a
   signed document. Its message MUST say that the document is signed and MUST
   name the sidecar of 6.5 as the carrier for a signed document.
3. **Never pretend.** A writer MUST NOT remove, alter or re-write a signature
   dictionary, its `/ByteRange`, the interactive form or `/Perms` in order to
   make a modified document appear valid, and MUST NOT report a signature as
   intact after modifying the document.
4. **An override says what it does.** A writer MAY offer a way to proceed. If it
   does, the option MUST be explicit, MUST be given per invocation, and MUST be
   named for its effect — that the signature is broken — and not for a mechanism.
   The writer MUST warn when it is used.
5. **Incremental updates are not a way round this.** A writer that can append a
   true incremental update, leaving the signed bytes untouched, MAY do so; nothing
   in this specification requires it, and it does not make the result safe. An
   incremental update leaves the signature's digest verifiable but makes the
   signed revision no longer the current one, which a verifier will say; and where
   `/Perms` `/DocMDP` is present the permitted changes do not include adding
   attachments, so a writer MUST NOT make an incremental update to a document
   carrying `/DocMDP`.

The sidecar is not a consolation prize here. A signed document is the leading
document of 1.2 in its strongest form — a document whose bytes someone has
undertaken not to change — and a carrier that does not touch it is the right
answer rather than a fallback.

The same rules govern removing a payload: stripping a signed document is
modifying it, and 6.2.13 does not exempt it.

**6.2.12 Reading.** A reader MUST look for the payload in both places, in this
order.

1. **Page-level `/AF` first.** For each page, scan the `/AF` array for a file
   specification whose `/UF` — or `/F`, where `/UF` is absent — is a plannotation
   file name. For the index, scan the catalog's `/AF` for `plannotation-index.json`.
2. **The name tree as a fallback.** For a page with no such entry, look up the
   page's name from 6.2.2 in the `EmbeddedFiles` name tree; likewise for the
   index.

Both paths are required. A producer may have written only one of them, or a
downstream tool may have dropped one, and a reader that implements a single path
will report a plannotated document as unplannotated.

Having found a candidate, a reader:

- MUST validate it against the plannotation or index schema before using any value
  in it, and MUST treat it as absent if it fails — 4.3 (1) and (2);
- MUST treat the association as the fact and `page.index` as a claim, where a
  plannotation found on page *i* declares a different index — 4.5;
- MUST treat the plannotation as absent, and a validator MUST report an error,
  where a plannotation found **only** through the name tree has a `page.index`
  that disagrees with the index encoded in its filename. There the filename and
  the plannotation are two claims and there is no fact to prefer;
- MUST treat a page's plannotation as absent, and a validator MUST report an
  error, where a page's `/AF` array references more than one plannotation file.
  Two plannotations for one page is a defect, and a reader has no basis for
  choosing between them;
- MUST NOT require the declaration of 6.3 to be present in order to read a payload
  it has found. A missing declaration is a writer's error (6.6) and not a reason
  to withhold a valid plannotation from a user.

**6.2.13 Removing a payload.** A writer MAY remove a Plannotation payload from a
document. When it does, it MUST remove exactly:

- every embedded file whose name is a Plannotation name and which satisfies 6.2.6
  (1) to (3);
- those files' entries in every `/AF` array, deleting an array that becomes empty
  rather than leaving it empty (6.2.7);
- those files' entries in the `EmbeddedFiles` name tree;
- the Plannotation declaration, and nothing else in the XMP packet (6.3.6).

and MUST NOT remove anything else. In particular a foreign attachment, a foreign
`/AF` entry and a foreign PDF Declaration MUST all survive.

**Removal is reversible in the only sense that matters.** Where *D* is a document,
removing the payload from a plannotated copy of *D* MUST produce a document
equivalent to *D*: the same page count, the same decoded content streams, the
same annotations, the same document information dictionary, an XMP packet with
the same properties and the same bytes for every property *D* had, the same
embedded files with the same bytes, and renderings identical under 6.2.8 (5). The
files will not be byte-identical, for the reason given at the end of 6.2.8. A
validator MUST check this equivalence where it is given both documents.

**6.2.14 Determinism.** Two runs of one writer over the same input document, the
same payload and the same supplied timestamps MUST produce byte-identical output.

Every timestamp a writer records — `/Params` `/ModDate`, `pdfd:claimDate` (6.3.4),
`generator.created` — MUST be taken from the writer's inputs. A conforming writer
MUST NOT read the system clock while plannotating. A writer SHOULD derive the
document `/ID` from the output's content rather than from a clock or a random
source, so that the requirement above is achievable at all.

Determinism is what makes a plannotated document diffable, cacheable and testable,
and it is cheap: the only values that would otherwise vary are the three named
above.

**6.2.15 Example.** The objects a writer adds to a two-page document, with a
third party's attachment (`9 0 R`) and a third party's page-level associated file
already present. Whitespace and object numbers are illustrative; object numbers
are assigned by the writer and mean nothing.

```
2 0 obj                                    % the document catalog
<< /Type    /Catalog
   /Pages   4 0 R
   /AF      [ 9 0 R 14 0 R ]               % the third party's, then the index
   /Names   << /EmbeddedFiles << /Names [
                 (plannotation-index.json) 14 0 R
                 (plannotation-p0000.json) 12 0 R
                 (site-notes.txt)        9 0 R ] >> >>
   /Metadata 3 0 R                         % carries the declaration of 6.3
>>
endobj

5 0 obj                                    % page 0
<< /Type      /Page
   /Parent    4 0 R
   /MediaBox  [ 0 0 1190.5512 841.8898 ]
   /Contents  6 0 R
   /AF        [ 10 0 R 12 0 R ]            % appended to, not replaced
>>
endobj

12 0 obj                                   % the file specification
<< /Type            /Filespec
   /F               (plannotation-p0000.json)
   /UF              (plannotation-p0000.json)
   /Desc            (Plannotation 0.1 for page 0)
   /AFRelationship  /Data
   /EF              << /F 13 0 R /UF 13 0 R >>
>>
endobj

13 0 obj                                   % the embedded file
<< /Type     /EmbeddedFile
   /Subtype  /application#2Fjson
   /Params   << /Size     3573
                /CheckSum <1cd6fcfbe30a10ecb788b4c9194a6a5d>
                /ModDate  (D:20240101000000Z) >>
   /Filter   /FlateDecode
   /Length   612
>>
stream
…
endstream
endobj
```

`/Size` is 3573 — the length of the canonical JSON — while `/Length` is 612,
the length of the compressed stream, and `/CheckSum` digests the 3573 plaintext
bytes. That is 6.2.4 working as specified, not an inconsistency.

---

### 6.3 The XMP PDF Declaration

A PDF Declaration is an XMP mechanism, published by the PDF Association, by which
a document states that it conforms to a specification outside ISO 32000. Plannotation
uses it for one purpose: so that a reader can tell, from the document's metadata
alone, that the document claims to carry a Plannotation 0.1 payload.

PDF Association, *PDF Declarations* (2019), listed in the normative references,
defines the mechanism. This subsection specifies what Plannotation writes into it,
what it must leave alone, and what a reader may conclude from it.

**6.3.1 The claim.** A plannotated PDF MUST carry exactly one Plannotation
declaration: one Declaration structure whose `pdfd:conformsTo` is exactly

```
https://plannotation.github.io/spec/0.1
```

which is the canonical URI of this specification, without a trailing slash and
without a fragment. The value is matched as a string. A writer MUST NOT write a
second declaration with that value, and a validator MUST report more than one as
an error; a reader that nevertheless finds two MUST treat the document as
carrying one.

**6.3.2 Where it goes.** The declaration MUST be written into the document
catalog's `/Metadata` XMP packet, where it is a claim about the document as a
whole.

The PDF Declarations specification also permits a declaration in an individual
object's `/Metadata`, scoped to that object, and the symmetry with Plannotation's
page-level associated files is tempting. Plannotation 0.1 does not use it: the claim
being made is that this document carries a Plannotation payload, which is a fact
about the document, and a per-page declaration would multiply the bytes and the
ways to be wrong without telling a reader anything the payload does not. A writer
MUST NOT write a Plannotation declaration into a page's or any other object's
`/Metadata`.

**6.3.3 Namespace and structure.** The XMP namespace is

```
http://pdfa.org/declarations/
```

with the preferred prefix `pdfd`. The scheme is `http`, not `https`, and the
trailing slash is part of the URI. XMP namespaces are matched as strings and are
never normalised, so both details are load-bearing. A reader SHOULD also
recognise the `https` spelling on input, which appears in some published
examples, and a writer MUST write only the `http` form.

The schema has one top-level property:

- **`pdfd:declarations`** — REQUIRED. An unordered array (`rdf:Bag`) of
  Declaration structures.

Each Declaration structure carries:

- **`pdfd:conformsTo`** — REQUIRED. A URI identifying the specification or
  profile the document claims to conform to.
- **`pdfd:claimData`** — OPTIONAL. An unordered array (`rdf:Bag`) of ClaimData
  structures.

Each ClaimData structure carries, all OPTIONAL:

- **`pdfd:claimBy`** — the organisation, individual or software making the claim;
- **`pdfd:claimDate`** — when the claim was made;
- **`pdfd:claimCredentials`** — the claimant's credentials;
- **`pdfd:claimReport`** — a URL to a report about the claim.

There are no other properties. A writer MUST NOT invent one: a level, an issuer, a
severity or a version has nowhere to go here, and anything Plannotation needs to say
beyond the claim belongs in the payload.

Both arrays are serialised as `rdf:Bag`, and each member as an `rdf:li` with
`rdf:parseType="Resource"`. A declarations property serialised as an `rdf:Seq`, or
whose members are plain text rather than structures, is not a declaration; a
validator MUST report it as an error rather than interpret it.

**6.3.4 What Plannotation writes.** A writer:

- MUST write `pdfd:conformsTo` with the value of 6.3.1;
- MAY write one ClaimData structure whose `pdfd:claimBy` identifies the writing
  program — but SHOULD NOT, and the reference implementation does not. A
  declaration whose bytes never vary can be removed by byte comparison, which is
  what lets a writer restore a third party's packet exactly (6.3.6) and lets a
  document written by one release be stripped cleanly by another. A `claimBy`
  carrying a version number trades that away for an identity the payload's
  `generator` (4.2) already records, in a place a reader is already looking;
- MUST omit `pdfd:claimDate` unless the date was supplied to it, and MUST NOT read
  the system clock for it (6.2.14);
- SHOULD NOT write `pdfd:claimCredentials` or `pdfd:claimReport` at 0.1. Neither
  has a defined meaning for Plannotation, and a value a reader cannot interpret is
  worse than an absent one.

**6.3.5 A complete packet.** A document that had no XMP packet at all, after
plannotating:

```xml
<?xpacket begin="&#xFEFF;" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:pdfd="http://pdfa.org/declarations/">
   <pdfd:declarations>
    <rdf:Bag>
     <rdf:li rdf:parseType="Resource">
      <pdfd:conformsTo>https://plannotation.github.io/spec/0.1</pdfd:conformsTo>
      <pdfd:claimData>
       <rdf:Bag>
        <rdf:li rdf:parseType="Resource">
         <pdfd:claimBy>Plannotation 0.1.0</pdfd:claimBy>
        </rdf:li>
       </rdf:Bag>
      </pdfd:claimData>
     </rdf:li>
    </rdf:Bag>
   </pdfd:declarations>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>
```

Note that `x:xmptk` is absent. It names the toolkit that wrote the packet, and a
document that has been stripped must carry no trace of Plannotation (6.2.9 (4)); a
writer MUST NOT set it, and MUST NOT alter it where a packet already has one.

Where the document already has an XMP packet, the writer adds a self-contained
`rdf:Description` of the shape above — declaring its own `xmlns:pdfd` — and MUST
leave every other byte of the packet as it found it. It MUST NOT re-serialise the
packet, reorder attributes, reindent, re-encode, or add, remove or modify any
other property. A packet belongs to whoever wrote it, and 1.2 (2) does not make
an exception for reformatting.

**6.3.6 Other declarations.** A document MAY carry declarations that have nothing
to do with Plannotation — accessibility claims, well-tagged PDF claims, a
publisher's own. They share the one `pdfd:declarations` array.

A writer MUST add its declaration to that array rather than replace it, MUST NOT
alter another declaration, and, when removing Plannotation's, MUST remove only the
Declaration structure whose `pdfd:conformsTo` is Plannotation's. It MUST remove the
`pdfd:declarations` property, and the `rdf:Description` that carried it, only when
removing that structure leaves the array empty.

Where removing the declaration would leave a packet that the writer itself
created and that now holds no properties at all, the writer SHOULD remove the
`/Metadata` stream, so that a stripped document is left as it was found. It MUST
NOT remove a packet that holds any other property.

**6.3.7 PDF/A.** PDF/A-1, PDF/A-2 and PDF/A-3 require that any XMP property in a
namespace the standard does not know be described by a PDF/A extension schema in
the same packet. The `pdfd` namespace is such a namespace. Therefore, where the
document identifies itself as `pdfaid:part` 1, 2 or 3, a writer MUST ensure the
packet describes the `pdfd` namespace — adding the PDF Association's published
extension schema for it where the packet does not already contain one, and MUST
at minimum report the risk to its caller when it does not. Without
it, the declaration itself invalidates the document's conformance claim, which
would make plannotating a PDF/A-3 document a destructive act.

PDF/A-4 (ISO 19005-4) removed the extension-schema requirement, keeping only that
the XMP conform to ISO 16684-1. Where the document identifies itself as
`pdfaid:part` 4, a writer SHOULD NOT add an extension schema.

Adding an extension schema is the one case in which a conforming writer adds a
property to a packet other than the declaration. It is permitted because it is
additive and because it is the only way to satisfy 1.2 (2)'s requirement not to
damage what it found: a writer that added the declaration alone would have
silently broken the document's conformance.

**6.3.8 What the declaration does and does not mean.** The declaration is a claim
that this document carries a Plannotation 0.1 payload. It is not a validation
result, it says nothing about the payload's conformance level, and a reader MUST
NOT treat it as evidence that any plannotation in the document is valid — 4.3 (1)
still requires the reader to validate what it finds.

A declaration with no payload is a claim the document does not support. A
validator MUST report it as an error. A payload with no declaration is a writer's
failure to make the claim; a validator MUST report it as an error, and a reader
MUST still read the payload (6.2.12).

---

### 6.4 The SVG carrier

An SVG carrier is defined in outline here and its encoding is reserved. Plannotation
0.1 specifies no SVG payload: a writer MUST NOT claim Plannotation 0.1 conformance
for an SVG document, and a reader MUST NOT infer one. The constraints in this
subsection are normative now, because they are what the encoding will have to
satisfy and because a writer experimenting ahead of the specification should not
break the documents it experiments on.

**6.4.1 Compatibility.** The SVG documents Plannotation cares about are produced by
IfcOpenShell's serialiser, which carries IFC identity in the markup already:
product groups take `id="product-<uuid>-body"`, where the UUID is the product's
GlobalId written as the 128-bit number it encodes (7.1.2), and repeat the GlobalId
as the model stores it in `ifc:guid` (`data-guid` where the serialiser writes no
`ifc` namespace); IFC classes appear as `class` values, and view information is
carried in attributes of its own.

A writer MUST NOT rename, renumber or remove an existing `id` or `class` value,
and MUST NOT alter any existing attribute. Those values are another tool's
identifiers, other documents reference them, and Plannotation gains nothing by
owning them. Where the markup carries an IFC GlobalId, in `ifc:guid` or encoded in
an `id`, a writer SHOULD read `element.ifcGuid` from it and MUST NOT rewrite it.

**6.4.2 Where Plannotation data goes.** Plannotation data is carried in a `<metadata>`
element, or in attributes whose names begin `data-plannotation-`, and nowhere else.
Both are inert: `<metadata>` is not rendered, and a `data-` attribute changes
nothing about how an element is drawn, so the appearance guarantee of 1.2 (1)
holds by construction rather than by inspection.

A writer MUST NOT add a rendered element, MUST NOT add or alter a presentation
attribute, style rule or transform, and MUST NOT reorder elements.

**6.4.3 Coordinates.** SVG is y-down and paper coordinates are y-up. Every
coordinate crossing the boundary between an SVG document and a plannotation MUST be
flipped as 3.3 requires, after conversion to millimetres and after removal of any
offset between the SVG's root coordinate system and the page corner. This is the
single most likely error in an SVG implementation, and 3.3 explains why it is
invisible on some drawings and obvious on others.

**6.4.4 Reserved.** The following are not specified at 0.1 and a future version
will settle them: the element name, namespace and content model of the
`<metadata>` payload; the `data-plannotation-*` attribute vocabulary and which of
the two mechanisms carries which part of the payload; whether a whole plannotation
or per-element fragments are carried; and how an SVG document announces that it
carries a payload, the declaration of 6.3 having no SVG equivalent.

**6.4.5 Reading IFC identity from an SVG *(informative)*.** An SVG written by
IfcOpenShell's serializer already says which product each group draws and how each
view maps to the model, so a writer can derive a plannotation for the PDF rendered
from that SVG without an SVG payload: the GlobalId from `ifc:guid`, or from the
serializer's `id="product-<uuid>-body"` by 7.1.2; the class from the group's
`class`; and the paper transform of 3.5 from the view group's `ifc:matrix3` and
`ifc:plane` composed with every transform and viewport above it, followed by the
flip of 3.3.
The reference implementation's `plannotation from-svg` and its Bonsai operator do
exactly this, and write nothing into the SVG.

---

### 6.5 The sidecar JSON

A *sidecar* is the whole payload of one document, in one JSON file, beside it. It
exists for two kinds of consumer: programs that cannot read PDF attachments, and
documents that MUST NOT be modified — a signed document above all (6.2.11).

**6.5.1 Shape.** A sidecar is a JSON object with these members, and no others:

| Member | Status | Value |
| --- | --- | --- |
| `plannotation` | REQUIRED | `"0.1"`, the format version |
| `index` | REQUIRED | one index document, valid against the index schema |
| `pages` | REQUIRED | an array of plannotations, each valid against the plannotation schema |
| `generator` | OPTIONAL | the program that wrote the sidecar, as in a plannotation |
| `extensions` | OPTIONAL | namespaced extras, as in a plannotation; a reader ignores what it does not understand — 4.3 (6) |

It is described by `plannotation-sidecar.schema.json`, published beside the
plannotation and index schemas, and a reader MUST validate a sidecar against it
before using any value in it — 4.3 (1) applies to a sidecar exactly as it applies
to a plannotation.

The members MUST agree with each other:

- `plannotation` MUST equal the `plannotation` of `index` and of every member of
  `pages`;
- `pages` MUST contain exactly one plannotation for each entry of `index.pages`,
  matched by `page.index` against `pageIndex`, and no plannotation that
  `index.pages` does not list;
- `pages` MUST be ordered by `page.index`, ascending, and MUST NOT contain two
  plannotations with the same `page.index`.

A validator MUST report each of these as an error. The redundancy is deliberate:
the index and the plannotations are separate documents in the PDF carrier, so the
sidecar carries both rather than deriving one from the other, and the price of
that is a consistency rule.

**6.5.2 Serialisation.** A sidecar MUST be serialised in the canonical form of
3.8 — UTF-8 without a byte order mark, members sorted by name, two-space indent,
every member and every array element on its own line, LF endings, one trailing
LF, no `null` for an absent member, every number at three decimals or fewer. It
is a public artefact like any other Plannotation document, and the reasons in 3.8
for making it byte-reproducible apply to it in full.

**6.5.3 Naming.** For a document whose filename is `NAME.pdf`, the sidecar is
`NAME.plannotation.json`, in the same directory. Where the document's filename has
no extension, the sidecar is that filename with `.plannotation.json` appended.

A sidecar is never itself embedded in the document it describes: it is the copy
that travels outside.

**6.5.4 Pairing.** A reader given a document looks for the sidecar at the name
above, **in the same directory as the document and nowhere else**. It MUST NOT
search parent directories, MUST NOT search a configured location, and MUST NOT
dereference any path or URL found inside a sidecar in order to locate its
document — section 9 forbids a reader from dereferencing anything in a Plannotation
payload, and a sidecar is one.

A reader MAY be given a sidecar alone, with no document. It MAY then use the
payload, and MUST make clear that it did so without the document: with no page to
consult, the reader cannot apply 1.2 (6), and every geometric value in the
payload is unverified.

**6.5.5 Binding a sidecar to its document.** Nothing in a filename proves that a
sidecar was written for the document beside it. The payload's `index` may carry the
hash of the *model* the plannotations came from (7.1.4), which says nothing about the
PDF.

Plannotation 0.1 does not add a member for this. The binding is the naming rule of
6.5.4, checked against what the payload already states about the document's pages.
A reader pairing a sidecar with a document MUST:

1. treat a plannotation as absent where its `page.index` is not a page of the
   document;
2. treat a plannotation as absent where its `page.widthMm` or `page.heightMm`
   differs from that page's own unrotated dimensions (3.2) by more than the
   geometric tolerance of 0.5 mm (3.1);
3. treat the whole payload as absent where no plannotation pairs with any page.

A validator MUST report each of the three as an error.

**Why this and not a hash.** A byte hash is the only exact binding available, and
it is exact about the wrong thing. A PDF's bytes change under operations that
change nothing a plannotation describes: every re-save renumbers its objects and
rewrites its cross-reference table, a signature-preserving incremental update
appends to it, an archive may recompress it. Requiring a matching hash would make
a sidecar refuse a document that is, in every respect a plannotation cares about,
the document it was written for — and it would do so first in the workflow the
sidecar exists to serve, where the document is passed between systems precisely
because it cannot be modified.

Requiring nothing is the opposite failure: a sidecar left behind from an earlier
revision would be read as though it described the sheet in front of the reader.

The checks above are cheap, robust, and directly about what the payload claims.
They catch the stale sidecar that matters — one written for a different document,
or for a revision whose pages changed — while a document that was merely re-saved
still reads. And where a reader is wrong about any of it, 1.2 (6) still holds: the
page is in front of it, and the page wins.

*An explicit `document` member carrying a page count, a filename and a hash was
considered and deferred to a future schema version, where the member could be
declared rather than asserted. Do not carry the binding under `extensions`: an
extension is something a reader may ignore (4.3 (6)), and a binding a reader may
ignore is not a binding.*

**6.5.6 Example.** A sidecar for a two-page document, with the payload's arrays
and objects elided. Shown with members in canonical order; the inner objects are
shown inline for legibility only, and a real sidecar puts every member and every
array element on its own line, by 3.8.

```json
{
  "generator": { "name": "plannotation", "version": "0.1.0" },
  "index": { "pages": [ … ], "plannotation": "0.1", "provenance": "authored" },
  "pages": [ … ],
  "plannotation": "0.1"
}
```

---

### 6.6 Conformance of a carrier

This subsection collects the obligations that attach to a carrier. It restates
nothing: each line points at the rule that governs it. A writer, reader or
validator is conforming with respect to a carrier when it satisfies the
corresponding column below, and a program MUST NOT claim support for a carrier it
does not.

**6.6.1 The PDF carrier.**

A conforming **writer** MUST: embed a plannotation for every page it plannotates,
associated with that page, and one index associated with the document (6.2.1);
name them as 6.2.2 requires; write every required file-specification and
embedded-file member (6.2.3, 6.2.4); register every file in the name tree and
associate every registered file (6.2.5); refuse a foreign name collision (6.2.6);
append to `/AF` and never assign, deleting an array it empties (6.2.7); satisfy
the appearance guarantee (6.2.8); do none of the things in 6.2.9; leave the header
version alone except as 6.2.10 permits; detect a signature and refuse by default
(6.2.11); write the declaration (6.3); and be deterministic (6.2.14).

A conforming **reader** MUST: implement both the `/AF` path and the name-tree
fallback (6.2.12); tolerate a null `/AF` entry (6.2.7) and a branching name tree
(6.2.5); validate before trusting and treat an invalid, unknown-version, ambiguous
or misattached plannotation as absent (6.2.12, 4.3); and read a payload whose
declaration is missing (6.3.8).

A conforming **validator** MUST report: every missing or wrong file-specification
or embedded-file member, including a `/Size` or `/CheckSum` that disagrees with
the decoded bytes (6.2.3, 6.2.4); every embedded file not registered in the name
tree and every registered file not associated (6.2.5); an `/AFRelationship` other
than `/Data` on a Plannotation file (6.2.3); an empty `/AF` array (6.2.7); a page
with more than one plannotation, and a name-tree-only plannotation whose
`page.index` contradicts its filename (6.2.12); a declaration without a payload
and a payload without a declaration (6.3.8); more than one Plannotation
declaration, or one that is not a `rdf:Bag` of structures (6.3.1, 6.3.3); and,
where it is given the input document as well as the plannotated one, every failure
of the appearance guarantee (6.2.8) and of the removal equivalence (6.2.13).

A validator given only a plannotated document MUST say so, and MUST NOT report the
appearance guarantee as satisfied. It cannot check it, and silence about a check
that was not run reads as a pass.

**6.6.2 The SVG carrier.** Reserved (6.4). There is no conforming SVG writer or
reader at 0.1. A validator that is given an SVG document MUST report that Plannotation
0.1 defines no SVG carrier rather than report the document as non-conforming.

**6.6.3 The sidecar carrier.**

A conforming **writer** MUST: write the shape of 6.5.1, with the three
consistency rules satisfied; serialise it canonically (6.5.2); name it as 6.5.3
requires; and MUST satisfy the pairing rules of 6.5.5.

A conforming **reader** MUST: validate the sidecar against the sidecar schema
before using any value in it (6.5.1); look for it only beside the document
(6.5.4); apply the pairing rules of 6.5.5; say when it has used a sidecar without
its document (6.5.4); and apply the carrier precedence of 6.1.3 where the
document also carries a payload.

A conforming **validator** MUST report: every violation of the three consistency
rules of 6.5.1; a non-canonical serialisation (6.5.2); a name that does not match
the document (6.5.3); and, where it is given the document, a page-count or
page-dimension mismatch as an error (6.5.5).

**6.6.4 Across carriers.** A validator MUST report a disagreement between two
carriers carrying payloads for the same document, and MUST report which carrier
6.1.3 selects. It MUST NOT report the disagreement as a failure of either
payload: both may be internally perfect, and the defect is that they are not the
same.

## 7. IFC mapping

Plannotation does not model buildings. Everything it says about the building is said in
IFC's words — class names, GlobalIds, property-set names — and this section states the
correspondence. It aligns with two descriptions of drawings in IFC:

- **SWAPP's `ifc-docs` draft** (CC0, 2024), which structures documentation as
  *DocumentSet → Sheet → ViewPort → View → Annotation*, with annotations as
  `IfcAnnotation` aggregated by `IfcRelAggregates` and cross-referenced to the
  products they describe by `IfcRelAssignsToProduct`. Plannotation reuses these
  names where it has the same concept: its *sheet*, *viewport*, *view* and
  *annotation* (2.1–2.6) are SWAPP's, and a plannotated document's *index* (2.8)
  plays the part of the document set.
- **The IfcOpenShell and Bonsai conventions**, in which a sheet is an
  `IfcDocumentInformation` with `Scope` `SHEET`, a drawing is an `IfcAnnotation`
  with `ObjectType` `DRAWING` whose view settings, scale among them, are in
  `EPset_Drawing`, drawings are placed on sheets by `IfcDocumentReference`, and a
  drawing's annotations are `IfcAnnotation`s whose type says what they are.

2.18 summarises which names correspond; this section adds the attributes. 7.1 and
7.3 are normative; 7.2 is an informative correspondence, because Plannotation 0.1
reads and writes plannotations, not IFC files, and does not require a writer to
have a model at all.

### 7.1 Identity

**7.1.1 Classes.** `element.ifcClass` MUST be the name of the IFC class the element
is an instance of, or of one of its supertypes — `IfcWall` for an
`IfcWallStandardCase` — as the schema spells it, and SHOULD be the exact class. It is
never a type object's name, an authoring tool's category or a layer name. `element.predefinedType`, where present, MUST be the instance's
`PredefinedType`, or its `ObjectType` when that is `USERDEFINED`.

**7.1.2 GlobalIds.** `element.ifcGuid` and `annotation.ifcGuid` MUST be the IFC
`GlobalId` of the entity they name, exactly as the model stores it: 22 characters of
IFC's base-64 alphabet encoding a 128-bit number, so that its first character is `0`
to `3`. A writer MUST NOT invent a GlobalId for something the model does not contain;
such an element has no `ifcGuid`. A reader MUST compare GlobalIds as case-sensitive
strings. Where a tool records the 128-bit number as a UUID, as IfcOpenShell's SVG
serializer does in `id="product-<uuid>-body"`, the GlobalId is its base-64 encoding
and a writer MAY decode it (6.4.5).

**7.1.3 Units.** `model.lengthUnit` MUST name the length unit of the model's
`IfcUnitAssignment` — `m` for the SI metre without prefix, `cm` for `CENTI`, `mm` for
`MILLI` — and a writer MUST read it from the model rather than assume it. Plannotation
0.1 names no other unit; a model in a conversion-based unit (feet, inches) cannot be
described with a `paperToPlane`, and a writer MUST NOT write one for it.

**7.1.4 The source model.** `model` names the model a plannotation was written
from, and `model.sha256` is what lets a reader holding a model tell whether it is
that model or a later revision of it, the distinction 7.3 turns on. A writer that
read the model from a file SHOULD record in `model.sha256` the SHA-256 of that
file's bytes. It MUST NOT record the hash of a file that does not hold the model it
wrote from: an authoring tool drawing a model with unsaved edits has no such file,
and omits the member. An index SHOULD carry `model`, hash included, where every
plannotation it lists records the same one, and MUST NOT carry one where they
differ, since a document drawn from two models has no single source.

This is a SHOULD and the member stays optional because 0.1 does not require a
writer to have a model at all (7), and because only the writer knows whether the
file it could hash is the model it drew. The hash identifies the model and never
the document (6.5.5).

### 7.2 Correspondence *(informative)*

| Plannotation | IFC | Notes |
| --- | --- | --- |
| `sheet` | SWAPP's Sheet; `IfcDocumentInformation` with `Scope` `SHEET` | `Identification` → `sheet.id`, `Name` → `title`, `Revision` → `revision` |
| The PDF of a sheet | `IfcDocumentReference` with the file's `Location` | The plannotation travels in the PDF; the model need not know it exists |
| The index of a plannotated document | SWAPP's DocumentSet | The sheets one PDF carries |
| `viewport` | SWAPP's ViewPort, an `IfcAnnotation` aggregated with `IfcRelAggregates`; in Bonsai, a drawing placed on the sheet by an `IfcDocumentReference` | Its box on the paper is `viewport.paperBBox` |
| `viewport.plane`, `paperToPlane`, `scale` | SWAPP's View; in Bonsai, the `IfcAnnotation` with `ObjectType` `DRAWING` and its `EPset_Drawing` | The annotation's placement is `viewport.plane`; the scale in `EPset_Drawing` is `viewport.scale` |
| `viewport.cutHeight`, `storey` | The section height above an `IfcBuildingStorey` | `storey.name` and `ifcGuid` are the storey's `Name` and `GlobalId`; `storey.elevation` is the z of the storey's floor in model coordinates (3.6): the z of its placement where the placement carries the level, and otherwise the z of the building's ±0,00 plus `Elevation`. It equals `Elevation` only where that ±0,00 is at z = 0 |
| `element` | An `IfcProduct`, usually an `IfcElement` | `GlobalId`, the entity class, `PredefinedType`, `Name` and `Tag` map one to one |
| `element.properties` | The element's property sets and quantity sets | Keyed by set name (`Pset_WallCommon`), then property name |
| `shows.property` | `Tag`, an attribute, or a dotted `Pset_Name.Property` | A mark shows `Tag`; a member's cross-section shows `Pset_ColumnCommon.Reference` |
| `annotation` | SWAPP's Annotation: an `IfcAnnotation`, aggregated under its view by `IfcRelAggregates` | `shows.element` records its `IfcRelAssignsToProduct` |
| `annotation.type = dimension` | `IfcAnnotation` of type `DIMENSION` | `measures` names what it runs between |
| `level` | `IfcAnnotation` of type `SECTION_LEVEL` or `PLAN_LEVEL` | `elevation` is the height the mark states, in metres (3.6), so a storey's mark measured from the building's ±0,00 carries the storey's `Elevation` in metres; `ifcGuid` names the storey or the level annotation |
| `text`, `leader` | `IfcAnnotation` of type `TEXT`, `TEXT_LEADER` | Assigned to a product by `IfcRelAssignsToProduct`, which is what `shows.element` records |
| `sectionMark` | `IfcAnnotation` of type `SECTION` | `target` names the sheet and viewport of the section it opens |
| `grid` | `IfcGridAxis` of an `IfcGrid` | `AxisTag` → `axis` |
| `callout` | A reference to another sheet's `IfcDocumentInformation` | `target.sheetId` is that sheet's `Identification` |
| `tag` | `IfcAnnotation` of type `TEXT`, templated from the product it is assigned to | Its text is the product's `Tag` |

### 7.3 Round trip

Given the model a plannotation was written from, a reader SHOULD resolve every
`ifcGuid` in it, and a validator given the model (4.4) MUST report an `ifcGuid`
the model does not contain, and an `ifcClass` that is neither the class of the
entity with that GlobalId nor a supertype of it (PL-IFC-001, PL-IFC-002). A
plannotation MAY name entities the reader's copy of the model lacks — the model
may have moved on since the drawing was issued — and a reader MUST then treat the
drawing, not the model, as the record of what was issued (1.2).

A validator given the model SHOULD also report a `storey.elevation` that is not the
z at which the model places the storey (3.6), as a warning (PL-IFC-004): a model may
leave its storeys' placements at the building's datum and state their levels only
in `Elevation`, so the comparison rests on a heuristic (4.4).

## 8. Versioning policy

### 8.1 One number, three places

A version of Plannotation is one `MAJOR.MINOR` number, and it appears in exactly three
places, which always move together:

1. the `plannotation` member of every plannotation, index and sidecar (`"0.1"`);
2. the `$id` of each schema, `https://plannotation.github.io/schema/0.1/…`;
3. the `conformsTo` URI of the PDF declaration, `https://plannotation.github.io/spec/0.1`.

A writer MUST write the same version in all three. A reader selects the schema by
the `plannotation` member and, where the version is one it does not implement,
treats the plannotation as absent (4.3 (3)); a validator reports such a
plannotation as unvalidatable rather than invalid (4.4).

### 8.2 What a version may change

While the major number is `0`, the format is a draft and any new minor version MAY
change anything. From `1.0`:

- a **major** version is required for any **breaking change**: removing or
  renaming a member; changing a member's meaning, unit or coordinate convention;
  making an optional member required; tightening a constraint so that a previously
  valid plannotation becomes invalid; removing a value from an enumeration; or
  changing the carrier — the attachment names, their relationship, their placement
  or the declaration;
- a **minor** version MAY add optional members, add values to an enumeration, add
  annotation types, and relax constraints. A reader of `1.n` MUST therefore treat an
  enumeration value it does not know as though it were `other`, where the enumeration
  has one, and otherwise ignore the object that carries it;
- **errata** change neither number. An erratum may correct an example, a typo or an
  ambiguity, and MUST NOT make a valid plannotation invalid or an invalid one valid.

A member deprecated in `1.n` remains valid until the next major version, and a writer
SHOULD stop writing it.

### 8.3 Permanence

A version's specification and schemas, once published under the canonical URLs
above, are never withdrawn and never changed except by errata, which are listed in
the document they amend. A plannotation written today must mean the same thing
when it is read from an archive in thirty years.

### 8.4 Software and extensions

The version of a program that reads or writes plannotations — including this
project's `plannotation` package, which follows semantic versioning of its own —
is independent of the format's, and is recorded in `generator.version`. Members
under `extensions` whose keys begin `x-` are outside the version entirely: they
may appear, change and disappear in any version, and a reader never requires them
(4.3 (6)).

## 9. Security considerations

A Plannotation payload is attacker-supplied input whenever the document carrying it is.
A drawing arrives by email, from a contractor's portal or out of an archive, and the
program that reads it is often a long-running service. This section states what a
reader owes its caller, and — equally important — what it cannot promise.

### 9.1 A plannotation is data

A plannotation is **data, never executable**. A conforming reader MUST NOT
evaluate, execute, or dereference as code any value it finds in a plannotation. In
particular, a reader MUST NOT fetch a URI found in a plannotation, MUST NOT
resolve a filename in it against the filesystem, and MUST NOT pass any part of it
to a template engine, a query language or a shell.

`extensions` and an element's `properties` hold arbitrary JSON by design (6.5). A
reader that forwards either into a system that interprets structure — a document
database, a serialisation format with type tags, an object deserialiser — MUST treat
them as untrusted data at that boundary too.

### 9.2 Validate before trusting

A conforming reader MUST validate a plannotation against the schema before
trusting any value in it, and MUST treat a plannotation that fails validation as
**absent** (4.3 (2)): no partial parse, no repair, no best effort. The page is in
front of the reader and the page is the leading document (1.2), so falling back to
it is always available and always correct.

A reader MUST NOT treat a document-level PDF Declaration as evidence that a payload
is valid. The declaration is a claim; the schema is the check.

### 9.3 Bounded parsing

A conforming reader MUST bound the resources it spends on a payload, and the bound
MUST hold for **every** input rather than for the shapes its author anticipated. The
document chooses the encoding, so a limit applied after a decoder has run is not a
limit.

Concretely, a reader:

1. MUST bound the decoded size of an embedded payload, and MUST enforce that bound
   *during* decoding rather than after it;
2. MUST bound the decoded size of the XMP packet, which is the same untrusted input
   as a payload;
3. MUST restrict which stream filters it will decode, and MUST treat a payload under
   any other filter chain as absent. A reader is not obliged to decode arbitrary PDF
   filters in order to find a plannotation;
4. MUST validate `/DecodeParms` — the predictor, colour count, bit depth and column
   count — against its own limits **before** allocating anything sized from them;
5. MUST bound the depth of the JSON it parses, or convert the resulting failure into
   an ordinary "this plannotation is absent" outcome;
6. MUST bound the work it does reporting a failure: the number of schema violations,
   and the length of any fragment quoted back from the document, are both the
   document's to choose;
7. MUST bound any walk over document structure whose breadth or depth the document
   controls — a form-field tree, a name tree, an array of associated files — and MUST
   NOT perform work that is quadratic in a count the document chooses;
8. SHOULD bound wall-clock time per operation independently of size, because a
   payload inside every byte limit can still be expensive to decode.

The reference implementation's limits are recorded here as a worked example, not as
part of the format: a decoded payload of 16 MiB, an XMP packet of 1 MiB, 10 000 form-field
nodes, and five reported violations each quoting at most 200 characters. An
implementation MAY choose different numbers; it MUST choose some.

### 9.4 What a reader cannot promise

A Plannotation reader sits on top of a PDF library, and some inputs are consumed by that
library before any Plannotation code runs. A conforming reader MUST NOT claim a guarantee
it cannot keep. Two limits are known and are stated here rather than papered over:

- **Cross-reference streams.** A cross-reference stream carries the same `/Predictor`,
  `/Columns` and `/Colors` surface as an embedded file, and a PDF library decodes it
  while opening the document. A reader's own decode limits do not apply to it.
- **Recursive structure walks.** A deeply nested page tree can exhaust the C stack of
  a library that walks it recursively, which ends the process rather than raising
  something a reader could convert into absence.

Neither is reachable by a reader's own bounds, and a reader that advertised protection
against them would be wrong. The mitigation is architectural: **an application
processing untrusted documents SHOULD do so in a separate process** with a memory
limit, a CPU-time limit and a wall-clock timeout, and treat the death of that process
as the document being unreadable. A library cannot survive its own address space being
torn down; a supervising process can.

### 9.5 Writers

A conforming writer:

- MUST NOT invalidate a digital signature silently. It MUST detect a signature, MUST
  refuse by default, and MUST name a course of action that does not modify the
  document — the sidecar carrier (6.5) exists for exactly this case. Where a writer
  offers to proceed anyway, the option MUST be explicit and MUST say that it breaks
  the signature (6.2).
- MUST NOT overwrite an embedded file it did not write, MUST NOT remove a third
  party's attachment, associated file or metadata, and MUST NOT leave a document it
  refused to plannotate partly written (6.2).
- SHOULD NOT record in a plannotation anything it was not asked to record. A
  plannotation travels with the drawing, and a filesystem path, a user name or a
  machine name in a `generator` or an `extensions` member travels with it too.

### 9.6 Privacy

A plannotation describes a drawing, and a drawing describes a building.
`sheet.author`, `sheet.checker` and `model.file` can carry personal names and
internal paths. A writer SHOULD record only what the drawing itself prints, and a
tool that publishes plannotated drawings SHOULD offer to remove those members.
This specification does not define a redaction mechanism; `plannotation strip`
removes the payload entirely.

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
