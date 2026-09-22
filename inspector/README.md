# PlanLabel inspector

`index.html` is a single-file viewer for labelled PDFs. Open it in a browser and choose
a PDF: it renders the page with [pdf.js](https://mozilla.github.io/pdf.js/), reads the
embedded labels through `getAttachments()`, and draws the label over the page —
element boxes and outlines, annotation geometry, viewports — with tooltips (class,
name, mark, GlobalId, provenance, confidence), toggles per kind and per annotation
type, **Copy JSON** and **Export CSV**.

It has no build step and no local dependencies. pdf.js is loaded from cdnjs on first
use, after which the browser's cache serves it. For an air-gapped machine, point the
two pdf.js URLs at the top of the script at a local copy; `inspector/vendor/` is
git-ignored for that purpose, since pdf.js is far over the repository's size limit.

`planlabel inspect file.pdf` prints the same in a terminal. The manual check against
the samples is in [`../docs/inspector.md`](../docs/inspector.md).
