# PlanLabel inspector

A single-file, offline-capable viewer for labelled PDFs. Landing in **Phase 5**.

It will render a page with [pdf.js](https://mozilla.github.io/pdf.js/), read the
embedded labels through `getAttachments()`, and overlay element bounding boxes,
element outlines and annotation geometry on a canvas layer — with hover tooltips
(class, name, tag, GlobalId, provenance, confidence), per-type visibility toggles,
"copy JSON" and "export CSV".

`index.html` is deliberately a single file with no build step. pdf.js is loaded from
a CDN on first use and then works from disk; a vendored build is an option for fully
air-gapped use.
