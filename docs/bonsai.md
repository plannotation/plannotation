# The Bonsai operator

`bonsai_ext/plannotation_bonsai.py` is a Blender add-on for
[Bonsai](https://bonsaibim.org). After Bonsai has produced a sheet, it attaches a
Plannotation page label to the sheet's PDF. The PDF looks and prints exactly as before;
the label is an attachment.

## How it works

Bonsai draws with IfcOpenShell's SVG serializer, so the sheet SVG it writes already
carries what a label needs:

| In the SVG | Becomes |
| --- | --- |
| A product group's `ifc:guid`, or its `id="product-<uuid>"` | `element.ifcGuid` |
| Its `class`, such as `IfcWall` | `element.ifcClass` |
| What it draws, through every transform above it | `element.paperBBox`, `paperOutlines` |
| A view group's `ifc:matrix3` and `ifc:plane`, and where the view is placed | `viewport.paperToPlane`, `plane`, `scale` |

The IFC model Bonsai has open supplies the rest: its length unit, its schema, and each
element's `Tag`, which becomes `element.tag`. The label is `authored` and reaches
conformance level L2 (sheet, viewports, elements). Dimensions, tags and grids are not
derived as annotations: the serializer does not mark them, and guessing them from line
work is what `plannotation infer` is for.

All of that is done by `plannotation.svg`, which is tested in Plannotation's CI against the
serializer's own output: a label derived from each sample's sheet SVG must agree with
the label written from the model, element for element. The add-on is only the Blender
side. The same step runs outside Blender as

```bash
plannotation from-svg sheet.svg sheet.pdf -o sheet.labelled.pdf --sheet-id A-101 --ifc model.ifc
```

## Installing

1. Install Plannotation into Blender's own Python. Blender 4.2 ships Python 3.11; find the
   interpreter under Blender's installation, for example
   `Blender.app/Contents/Resources/4.2/python/bin/python3.11` on macOS, then:

   ```bash
   <blender-python> -m ensurepip
   <blender-python> -m pip install plannotation
   ```

2. In Blender, **Edit > Preferences > Add-ons > Install from Disk**, choose
   `bonsai_ext/plannotation_bonsai.py`, and enable **Plannotation for Bonsai**.

## Using it

- **File > Export > Plannotation: Attach to Sheet PDF.** The dialog is filled from the
  sheet selected in Bonsai's sheet list: its number and title, and the newest
  `<number>*.svg` and `<number>*.pdf` in the `sheets` folder beside the IFC file.
  Correct anything it guessed wrong. The labelled copy is written beside the PDF as
  `<name>.labelled.pdf` unless another path is given; the PDF itself is not modified.
- **Create Sheet and Attach Plannotation** (search for it with F3) runs Bonsai's own
  *Create Sheets* first, then opens the same dialog.

## Manual test protocol

The add-on cannot be tested in CI: Blender is not available there. Run this before a
release, and record the result in the table at the end.

**Setup.** A fresh Blender with Bonsai, Plannotation and the add-on installed as above,
and a project with at least one sheet that has a plan view with walls on it.

1. **It loads.** Enable the add-on. Expect no error in the system console (Window >
   Toggle System Console on Windows; the terminal Blender was started from elsewhere),
   and **Plannotation: Attach to Sheet PDF** under File > Export.
2. **It is prefilled.** Select the sheet in Bonsai's sheet list, create it with Bonsai,
   then open the operator. Expect the sheet number and title filled in, and the SVG
   and PDF paths pointing into the project's `sheets` folder.
3. **It labels.** Confirm. Expect *Labelled <name>.labelled.pdf: N element(s)* in the
   status bar, where N is the number of model elements on the sheet.
4. **The label is right.** In a terminal:

   ```bash
   plannotation validate "<name>.labelled.pdf"     # expect: no errors, level L2
   plannotation inspect "<name>.labelled.pdf"      # expect: every element, its class and GlobalId
   ```

   Open the labelled PDF in `inspector/index.html` and check that each outline sits on
   the element it names, and that hovering shows the GlobalId Bonsai shows for it.
5. **The page is untouched.** Expect both files to render identically:

   ```bash
   python -c "from plannotation.pdf.render import assert_same_appearance as a; a('<name>.pdf', '<name>.labelled.pdf')"
   ```

6. **The transform is right.** Pick a wall corner in the inspector, note its paper
   position, and apply the viewport's `paperToPlane` to it (`plannotation read --json`).
   Expect the model coordinates Bonsai shows for that corner, within 5 mm.
7. **Failures are reported, not raised.** Each of these must end with a message in the
   status bar and no traceback:
   - point **Sheet PDF** at an A4 PDF when the sheet is A3 (*not a rendering of this
     SVG*);
   - close the IFC project and run the operator (*Bonsai has no IFC model open*);
   - uninstall Plannotation from Blender's Python and run it (*Plannotation is not installed*).
8. **It unloads.** Disable the add-on. Expect the menu entry gone and no error.

| Date | Blender | Bonsai | Plannotation | OS | Steps passed | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| | | | | | | |

## Limits

- The file guesses in step 2 follow the folder layout of the Bonsai version this was
  written against. Where they are wrong the fields stay editable; nothing is written
  until the user confirms.
- `<image>` references in a sheet layout are followed to local SVGs only, and two
  levels deep, so a sheet that places its drawings by reference still reads.
- A model in feet or inches is refused: Plannotation 0.1 names only m, cm and mm.
