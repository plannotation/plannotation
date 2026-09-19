# AutoCAD adapter — design note

**Status: not written. Lands in Phase 9, as a design document only.**

This project ships **no AutoCAD add-in**. This note exists so that someone who wants to
produce authored PlanLabel output from AutoCAD knows exactly which calls to make and what
the paper-to-model arithmetic has to be.

When written, it will cover:

1. **The API surface** — paper-space viewports, entity handles, Xdata.
2. **The paper transform** — deriving each viewport's `paperToPlane` affine from the
   view placement, crop box and scale, in paper millimetres with the origin at the
   bottom-left and y up (see [`../../spec/SPEC.md`](../../spec/SPEC.md) §3).
3. **The post-processing step** — handing the collected geometry and identifiers to
   this library to write the label and attach it to the exported PDF.
4. **A prototype path** — the smallest thing that could work, and what it cannot do.
