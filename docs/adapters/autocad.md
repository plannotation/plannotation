# AutoCAD adapter — design note

**Status: design only.** This project ships no AutoCAD plug-in. This note says where
each part of a label lives in a DWG, how paper space maps to the model, and what a
first prototype would look like. API names are from the AutoCAD .NET API
(`Autodesk.AutoCAD.DatabaseServices`); the DXF group-code equivalents are given where a
prototype would read a DXF instead.

## What is different about AutoCAD

A Revit or Tekla model knows that a line is a wall. A plain DWG does not: it has
entities on layers, and IFC identity exists only if something put it there. So the
adapter has two jobs, and the second decides the label's provenance:

1. **Geometry** — sheets, viewports and the paper transform. This is always available
   and always exact.
2. **Identity** — which entity is which IFC element. This comes from one of:
   - **Xdata** written by whatever links the DWG to a model, under a registered
     application name such as `PLANNOTATION`: `(1001 "PLANNOTATION") (1000 "IfcWall")
     (1000 "<GlobalId>")`. Identity from here is `authored`.
   - **AutoCAD Architecture / MEP objects**, whose object type maps to an IFC class
     (a wall object to `IfcWall`), and whose IFC export stores a GUID. Also `authored`.
   - **A layer standard** (ISO 13567, the AIA CAD layer guidelines, an office's own)
     mapped to IFC classes. This is a reading of the drawing, so elements identified
     this way must be `inferred`, with a confidence, and carry no `ifcGuid`.

## Where each part of a label comes from

| Label | DWG |
| --- | --- |
| `sheet` | A paper-space `Layout` (`Layout.LayoutName`), and the title block's attributes (`AttributeReference.Tag` / `TextString`) for number, title and revision |
| `page.widthMm`, `heightMm` | The layout's paper size (`Layout.PlotPaperSize`) in the orientation `Layout.PlotRotation` gives |
| `viewports[]` | The `Viewport` entities in the layout's block table record, skipping the overall paper-space viewport (number 1) |
| `viewport.paperBBox` | `Viewport.CenterPoint`, `Width`, `Height` (paper units); a clipped viewport's `NonRectClipEntityId` boundary |
| `viewport.scale` | `1 / Viewport.CustomScale`, with the model and paper units accounted for |
| `viewport.plane` | `Viewport.ViewTarget`, `ViewDirection` and `TwistAngle` |
| `elements[]` | Model-space entities inside the viewport's view, with identity from one of the three sources above; `Entity.Handle` gives a stable local id |
| `dimension` annotations | `AlignedDimension` / `RotatedDimension`: `XLine1Point`, `XLine2Point`, `Measurement`; associativity from the `ACAD_DIMASSOC` entry in the extension dictionary names the measured entities |
| `tag` annotations | Block references with attributes, or `MLeader`s, linked to the entity they mark by Xdata or by the leader's arrowhead |
| The PDF | `PLOT` or `PUBLISH` of the layout through `DWG To PDF.pc3`, plot area *Layout*, scale 1:1, not *fit to paper* |

## The paper transform

For a viewport, a model point `P` (WCS) goes to paper in three steps:

1. **World to display (DCS).** Subtract `ViewTarget`; rotate so that `ViewDirection`
   becomes +Z (identity for a plan view, whose direction is `(0, 0, 1)`); rotate by
   `−TwistAngle` about Z. Keep x and y.
2. **Display to paper space.** `paper = CenterPoint + CustomScale · (dcs_xy − ViewCenter)`,
   where `ViewCenter` is the display centre in DCS and `CustomScale` is paper units per
   drawing unit.
3. **Paper space to the page.** Paper-space units are usually millimetres
   (`Layout.PlotPaperUnits`). The layout's origin is placed at the printable area's
   lower-left corner plus `Layout.PlotOrigin`, with the paper margins
   (`Layout.PlotPaperMargins`) taken off and `PlotCentered` honoured. Plannotation's paper
   origin is the page's lower-left, so that offset is added.

Every step is affine, so `paperToPlane` is the inverse of their composition, expressed
in the model's length unit along the plane axes of step 1. A twisted viewport gives an
affine with off-diagonal terms, which the format allows; a mirrored one (a negative
`CustomScale` or a view from below) is rejected by the validator (PL-GEO-007), and the
adapter should say so rather than write it.

## A prototype

[ezdxf](https://ezdxf.mozman.de) (MIT) reads a DXF saved from AutoCAD without AutoCAD
running, which makes it the quickest route to a working prototype. It would be an
optional dependency behind its own extra, and — per the project's rules — only after
asking.

1. `doc = ezdxf.readfile("A-101.dxf")`; for each paper-space layout, the viewports are
   `layout.viewports()`, with `vp.dxf.center`, `width`, `height`,
   `view_center_point`, `view_height`, `view_direction_vector`, `view_target_point`
   and `view_twist_angle`. The scale is `vp.dxf.height / vp.dxf.view_height`.
2. Model-space entities carry `entity.dxf.handle` and `entity.get_xdata("PLANNOTATION")`.
3. Write one label per layout, then `plannotation attach` it to the PDF AutoCAD plotted.

What it cannot do: identity when nothing put it into the DWG (then everything is
`inferred`, and `plannotation infer` on the PDF may do as well); external references,
whose entities need the xref's insertion transform; paper-space dimensions that
measure model space through a viewport, which need the transform applied in reverse.
