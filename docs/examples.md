# Example drawings

The samples are drawn from models this project builds itself. The examples are drawn
from a real building's model, through the same exporter, with nothing special-cased.

```bash
make examples          # downloads the pinned model once, then builds examples/
make examples-check    # builds them, then validates each sheet against the model
```

| Sheet | Drawing | Paper |
|---|---|---|
| M18-101 | 1. korrus / Ground floor plan, 1:100 | A1 |
| M18-301 | Lõige A-A / Section A-A, 1:100 | A2 |

The model is Esplan OÜ's preliminary-design architecture model of the Maleva 18 seniors'
apartment building in Tallinn (Archicad, IFC2X3), published by buildingSMART among its
[Community Sample Test Files](https://github.com/buildingsmart-community/Community-Sample-Test-Files/tree/main/IFC%202.3.0.1%20(IFC%202x3)/Esplanades)
under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Every sheet prints the
credit; the drawings are Plannotation's, not Esplan's.

## What the build does

- **Fetches** the model from a fixed upstream commit into `.cache/examples/` (or the
  directory `PLANNOTATION_EXAMPLES_CACHE` or `--cache` names), unless a copy with the
  pinned size and SHA-256 is there already, and refuses a download that does not match
  them. Nothing is committed.
- **Draws** the plan through a horizontal plane 1.2 m above the ground floor, square to
  the model's grid, which the model places 60.8° off its world axes. The section is cut
  halfway between grids 4 and 5, looking towards grid 1.
- **Writes**, per sheet, `<sheet>.pdf` (plannotated) and `<sheet>.png` (1200 px, the
  plannotation's outlines drawn in the inspector's colours), and one `index.json`.
  A rebuild on the same machine is byte-identical. The sheets are set in Helvetica; on
  Linux, `tools/linux_fonts.sh` sets it as Liberation Sans, which has its metrics.

## What is read from the model

Storeys, the building's ±0,00, the grid, walls' `LoadBearing`, the materials of the
roof's layers, doors' operation types, spaces' `Name`, `LongName` and area
(`AR_Ruum.120_Pindala` in this model), and true north. What is drawn from them: solid
structure and grey partitions, door swings, room labels, a floor level, chain-line grids
with bay dimensions, level marks relative to ±0,00, section marks, north arrow and scale
bar. What each view sees beyond its cut is described element by element, from the lines
the drawing shows: a product hidden behind another is left out, and one the plane cuts
is described twice, as cut and by what of it shows beyond the cut.

## Known limits

- No material hatching: the roof's concrete is filled, its insulation and membrane
  outlined.
- No ground line: the model's site carries no terrain.
- Doors the model calls sliding or user-defined get no swing.
