# The inspector

`inspector/index.html` is a single file. Open it in a browser, choose a plannotated PDF,
and it draws the plannotation over the page: element outlines and bounding boxes,
annotation geometry, viewport extents, with a tooltip on hover or tap and a table of
everything on the sheet. The page is fitted to the width and drawn at the screen's
density.

There is no build step and nothing to install. pdf.js is fetched from a CDN the first
time and cached after that; everything else is in the file.

```bash
make samples                       # build something to look at
open inspector/index.html          # then choose samples/floorplan/sheet.plannotated.pdf
```

For a terminal, `plannotation inspect` prints the same information as tables:

```bash
plannotation inspect samples/floorplan/sheet.plannotated.pdf
plannotation inspect samples/floorplan/sheet.plannotated.pdf --json | jq .pages
```

## Opening a drawing from a link

Served from a site, the inspector opens the PDF its address names:
`inspector/?pdf=<URL>&page=<n>`. The URL is resolved against the inspector's own and must
be on the same site; anything else is refused with a note. Choosing a sheet or turning a
page updates the address, so it can be shared.

Where `../examples/index.json` exists, a picker lists the example sheets and the first
opens by default. Without it, as in a copy kept next to the drawings, the inspector opens
files from its button, which reads them in the browser and uploads nothing.

## What it reads

A plannotation travels as an attachment named `plannotation-pNNNN.json`. The inspector
finds it through pdf.js's `getAttachments()`, which exposes the document's
`EmbeddedFiles` name tree — which is why the carrier registers every plannotation there
as well as on the page's own `/AF` (SPEC 6.2). A plannotation that will not parse is
treated as absent, as SPEC 4.3 (2) requires of any reader.

Paper coordinates are millimetres with the origin at the bottom-left and y up; a canvas
is CSS pixels with y down. One function does that flip, so nothing else has to remember it.

## Manual check

The automated tests establish that the file is self-contained and that the conventions
it hard-codes still match what Plannotation writes. They cannot establish that it looks
right, so that part is checked by hand against the samples:

1. `make samples`
2. Open `inspector/index.html` and choose `samples/floorplan/sheet.plannotated.pdf`.
3. The five walls, two doors and two windows should be outlined, each with its mark's
   tag box beside it (`Pos. n`, `T1`, `W1`).
4. The grid bubbles A–D and 1–4 should be circled, and the eight dimensions drawn.
5. Hovering an outline should name its IFC class and GlobalId.
6. Turning off **elements** should leave the annotations and the viewport frame.
7. Clicking a row in the table should highlight that one and dim the rest.
8. **Copy JSON** should put the page's plannotation on the clipboard; **Export CSV**
   should download one row per item.
9. Repeat with `positionsplan` (sixteen structural members, each with its
   cross-section) and `section` (walls and slabs on two storeys, and three levels).
10. With a drawing open, choose a file that is not a PDF. A note should say why it
    could not be opened, and no empty box or tooltip should be left from the drawing.

And on the site, built as GitHub Pages builds it (`make examples site site-serve`,
then http://localhost:8000/):

11. `/inspector/` opens the first example, fitted to the width and sharp.
12. Choosing the other example from the picker opens it and puts its `?pdf=` in the
    address; `?pdf=https://example.com/x.pdf` is refused with a note.
13. On a phone, the page does not scroll sideways, and tapping an outline shows its
    tooltip until the next tap.

The three samples as the inspector draws them, plannotations over the pages:

![The floor plan in the inspector](img/inspector-floorplan.png)

![The position plan in the inspector: every member's mark and cross-section boxed](img/inspector-positionsplan.png)

![The section in the inspector, with its level marks](img/inspector-section.png)
