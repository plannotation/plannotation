# Revit adapter — design note

**Status: design only.** This project ships no Revit add-in. This note is for someone
who wants authored Plannotation output from Revit: which API calls give each part of a
label, what the paper-to-model arithmetic has to be, and the smallest prototype that
would work. API names are from the Revit 2022–2025 API; check them against the version
you build for, and note that several of the calls below arrived in 2022.

## The shape of it

Revit stays the leading tool and never learns Plannotation. An add-in (or a pyRevit
script) collects, for each sheet, what a label needs and writes it as a JSON label
beside the PDF Revit exported; Plannotation's CLI attaches and validates it:

```text
Revit  ──Document.Export(PDFExportOptions)──▶  A-101.pdf
  └────collect sheet, viewports, elements──▶  A-101.plannotation.json
plannotation attach A-101.pdf A-101.plannotation.json -o A-101.plannotated.pdf
plannotation validate A-101.plannotated.pdf
```

Keeping the Revit side free of Python dependencies is the point: the add-in writes
plain JSON, and everything Plannotation guarantees about the PDF is guaranteed by the
library that is tested for it.

## Where each part of a label comes from

| Label | Revit API |
| --- | --- |
| `sheet.id`, `title`, `revision` | `ViewSheet.SheetNumber`, `ViewSheet.Name`, the current revision via `ViewSheet.GetCurrentRevision()` |
| `page.widthMm`, `heightMm` | The title block's extents on the sheet: `FamilyInstance.get_BoundingBox(sheet)` of the `OST_TitleBlocks` instance, in feet |
| `viewports[]` | `ViewSheet.GetAllViewports()` → `Viewport`; `Viewport.ViewId` → the `View` |
| `viewport.paperBBox` | `Viewport.GetBoxOutline()` (sheet coordinates, feet) |
| `viewport.scale` | `View.Scale` |
| `viewport.plane` | `View.Origin`, `View.RightDirection`, `View.UpDirection`; for a plan, the level's elevation plus the view range cut plane (`PlanViewRange.GetOffset(PlanViewPlane.CutPlane)`) |
| `viewport.paperToPlane` | See the next section; `View.CropBox` and `Viewport.GetBoxCenter()` |
| `elements[]` | `new FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType()` |
| `element.ifcGuid` | The `IfcGUID` parameter (`BuiltInParameter.IFC_GUID`) when the IFC exporter stored it; otherwise `ExportUtils.GetExportId(doc, element.Id)`, a `Guid` encoded to 22 characters exactly as `plannotation.svg.carrier.guid_from_uuid` does |
| `element.ifcClass` | The IFC exporter's class for the element: the `IfcExportAs` parameter if set, else its category mapping |
| `element.paperBBox`, `paperOutlines` | `Element.get_Geometry(new Options { View = view })`, edges projected to the view and then to paper; `Element.get_BoundingBox(view)` is the cheap, conservative fallback |
| `element.tag` | The `Mark` parameter (`BuiltInParameter.ALL_MODEL_MARK`), which the IFC exporter writes to `Tag` |
| `dimension` annotations | `Dimension.References` → each `Reference.ElementId` gives `measures`; `Dimension.Value` or `Dimension.Segments[i].Value` (feet) gives `value`; `Dimension.TextPosition` places it |
| `tag` annotations | `IndependentTag.GetTaggedLocalElementIds()` (2022+; `TaggedLocalElementId` before) gives `shows.element`; `IndependentTag.TagHeadPosition` places it |
| `grid` annotations | `Grid.Name` and `Grid.Curve`, for the grids visible in the view |
| `level` annotations | `Level.Elevation` on sections and elevations |
| `callout` / `sectionMark` targets | The referenced view's `BuiltInParameter.VIEWER_SHEET_NUMBER` gives `target.sheetId` |
| The PDF | `Document.Export(folder, viewIds, PDFExportOptions)` (2022+), one file per sheet, `ZoomType.Zoom` at 100 %, `HideCropBoundaries` and `HideScopeBoxes` set |

## The paper transform

Revit measures in feet. A sheet's coordinates have their own origin; the exported page
is the title block's extents, so let `T` be the lower-left corner of the title block's
bounding box on the sheet. Then, for a point `s` on the sheet (feet),

```text
paper_mm = (s − T) × 304.8
```

For a view with no rotation on the sheet, a model point `P` (feet) has view coordinates

```text
u = (P − View.Origin) · View.RightDirection
v = (P − View.Origin) · View.UpDirection
```

The crop region's centre `c = (c_u, c_v)` — the crop box's `Transform.OfPoint` of the
mid-point of `CropBox.Min` and `CropBox.Max`, in the same `u, v` — lands on the
viewport's box centre `B = Viewport.GetBoxCenter()` when the annotation crop is off.
With `k = 1 / View.Scale`:

```text
s = B + k · (u − c_u, v − c_v)
paper = 304.8 · (B − T) + 304.8 · k · ((u, v) − c)
```

Inverting, and expressing `u, v` in the model's length unit (metres: × 0.3048), gives
the SPEC 3.5 affine. For a model in metres at 1:`n`:

```text
paperToPlane = [ n/1000, 0, 0, n/1000,
                 c_u·0.3048 − (n/1000)·304.8·(B_x − T_x),
                 c_v·0.3048 − (n/1000)·304.8·(B_y − T_y) ]
```

and `plane.origin` is `View.Origin` in metres, with `xAxis = RightDirection` and
`yAxis = UpDirection`. Revit 2022 added `View.GetModelToProjectionTransforms()` and
`Viewport.GetProjectionToSheetTransform()`, which compose the same chain and also cover
a viewport rotated on the sheet (`Viewport.Rotation`) and a split crop; prefer them
where available, and check them against the arithmetic above on an unrotated view.

Two checks catch nearly every mistake: the validator's PL-GEO-008 compares the scale
implied by `paperToPlane` with `viewport.scale`, and projecting a grid intersection
through the inverse must land on its bubble within 0.5 mm.

## A prototype

A pyRevit script is the smallest thing that works. pyRevit is GPL-3.0; that is fine,
since it is a tool someone runs, not a dependency of Plannotation.

1. For each selected `ViewSheet`: export the PDF with `Document.Export`.
2. Build the label as a Python dict following the table above, with
   `provenance: "authored"` at the top level, and `json.dump` it with sorted keys.
3. Run `plannotation attach` and `plannotation validate` on the result with
   `subprocess.run`, using a CPython that has `plannotation` installed.

What it cannot do: rotated viewports and split crops without the 2022 transforms;
dependent views (a view placed on several sheets is several viewports); linked models,
whose elements need the link instance's transform and the link document's
`GetExportId`.
