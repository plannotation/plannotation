# The inspector

`inspector/index.html` is a single file. Open it in a browser, choose a labelled PDF,
and it draws the label over the page: element outlines and bounding boxes, annotation
geometry, viewport extents, with a hover tooltip and a table of everything on the sheet.

There is no build step and nothing to install. pdf.js is fetched from a CDN the first
time and cached after that; everything else is in the file.

```bash
make samples                       # build something to look at
open inspector/index.html          # then choose samples/floorplan/sheet.labelled.pdf
```

For a terminal, `planlabel inspect` prints the same information as tables:

```bash
planlabel inspect samples/floorplan/sheet.labelled.pdf
planlabel inspect samples/floorplan/sheet.labelled.pdf --json | jq .pages
```

## What it reads

A page label travels as an attachment named `planlabel-pNNNN.json`. The inspector finds
it through pdf.js's `getAttachments()`, which exposes the document's `EmbeddedFiles`
name tree — which is why the carrier registers every label there as well as on the
page's own `/AF` (SPEC 6.2). A label that will not parse is treated as absent, as
SPEC 4.3 (2) requires of any reader.

Paper coordinates are millimetres with the origin at the bottom-left and y up; a canvas
is pixels with y down. One function does that flip, so nothing else has to remember it.

## Manual check

The automated tests establish that the file is self-contained and that the conventions
it hard-codes still match what PlanLabel writes. They cannot establish that it looks
right, so that part is checked by hand against the samples:

1. `make samples`
2. Open `inspector/index.html` and choose `samples/floorplan/sheet.labelled.pdf`.
3. The four walls should be outlined, each with a `Pos. n` tag box on it.
4. The grid bubbles A, B, 1 and 2 should be circled, and the two dimensions drawn.
5. Hovering an outline should name its IFC class and GlobalId.
6. Turning off **elements** should leave the annotations and the viewport frame.
7. Clicking a row in the table should highlight that one and dim the rest.
8. **Copy JSON** should put the page label on the clipboard; **Export CSV** should
   download one row per item.
9. Repeat with `positionsplan` (six structural members) and `section`.

Screenshots of steps 3 and 5 belong in `docs/img/`.
