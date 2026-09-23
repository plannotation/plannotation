# Tekla adapter — design note

**Status: design only.** This project ships no Tekla extension. Tekla Structures is the
closest fit of the three authoring tools: its drawings are generated from the model,
every drawing object knows the model object it represents, and a *Positionsplan* — the
German structural placement plan the samples include — is exactly what its general
arrangement drawings are. API names are from the Tekla Open API
(`Tekla.Structures.Drawing`, `Tekla.Structures.Model`); check them against the version
you build for.

## Where each part of a label comes from

| Label | Tekla Open API |
| --- | --- |
| `sheet` | `DrawingHandler.GetActiveDrawing()` or `GetDrawings()` → `Drawing`: `Drawing.Mark` (the drawing number), `Drawing.Name`, `Drawing.Title1`–`Title3` |
| `page.widthMm`, `heightMm` | `Drawing.Layout.SheetSize` |
| `viewports[]` | `Drawing.GetSheet()` → `ContainerView`; its `GetAllViews()` → `View` |
| `viewport.paperBBox` | `View.Origin` (the view's position on the sheet) with `View.Width` and `View.Height` |
| `viewport.scale` | `View.Attributes.Scale` |
| `viewport.plane` | `View.DisplayCoordinateSystem` (origin, x axis, y axis in model coordinates) |
| `elements[]` | `View.GetAllObjects(typeof(Part))` → drawing `Part` objects |
| `element.ifcGuid` | `Part.ModelIdentifier` → `new Model().SelectModelObject(identifier)` → `ModelObject.Identifier.GUID`, a `Guid` encoded to 22 characters as `plannotation.svg.carrier.guid_from_uuid` does. Tekla's IFC export uses the same GUID, so the label and an IFC export agree |
| `element.ifcClass` | Tekla's IFC export mapping for the part (beam, column, plate, slab), or the part's IFC entity setting where one is set |
| `element.tag` | The part or assembly position: `ModelObject.GetReportProperty("ASSEMBLY_POS", ref value)` or `"PART_POS"` |
| `tag` annotations | `Mark` objects; `Mark.GetRelatedObjects()` gives the marked part, so `shows.element` is exact, not guessed |
| `dimension` annotations | `StraightDimensionSet` → `StraightDimension`: `StartPoint`, `EndPoint` (view coordinates) and the distance; `GetRelatedObjects()` on an associative dimension gives `measures` |
| `grid` annotations | `Grid` drawing objects and their labels |
| `level` annotations | Level marks on section views |
| The PDF | Printing through Tekla's PDF output (`DrawingHandler.PrintDrawing` with print attributes in recent versions; the drawing list's *Print* in older ones) at 1:1 |

## The paper transform

A drawing view shows the model in its display coordinate system, scaled onto the sheet.
For a model point `P`:

1. **Model to view.** Transform `P` into `View.DisplayCoordinateSystem` —
   `MatrixFactory.ToCoordinateSystem(view.DisplayCoordinateSystem).Transform(P)` in
   `Tekla.Structures.Geometry3d`. The result is in model millimetres on the view plane.
2. **View to sheet.** Divide by `View.Attributes.Scale` and add the view's placement on
   the sheet, `View.Origin`, measured from the sheet's lower-left corner in millimetres.
   Check the placement against `View.GetAxisAlignedBoundingBox()` on the sheet: the two
   must agree to within a line width, or the frame origin of the view is being
   confused with its placement point.

The sheet is already millimetres with a lower-left origin, so no flip is needed, and
`paperToPlane` is the inverse of step 2 followed by the conversion from millimetres to
the model's length unit. `plane` is the display coordinate system's origin and axes.

## A prototype

A small .NET console application against the Open API, run with Tekla open:

1. For each selected drawing, collect the sheet, views, parts, marks and dimensions as
   above, and write one label per drawing as JSON, with `provenance: "authored"`.
2. Print the drawing to PDF.
3. Run `plannotation attach` and `plannotation validate`.

What it cannot do: views whose display coordinate system is rotated relative to the
sheet need the rotation carried into `paperToPlane` (the format allows it; the
prototype should test it on a rotated section); cast-unit and reinforcement drawings
have their own object types, which the table above does not cover.
