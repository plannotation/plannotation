# SPDX-License-Identifier: Apache-2.0
"""Blender add-on: attach a plannotation to a sheet PDF that Bonsai produced.

Bonsai draws with IfcOpenShell's SVG serializer, so every sheet SVG it writes already
carries each product's GlobalId and class and each view's paper-to-model transform.
This operator reads those from the sheet SVG, takes the units and marks from the IFC
model Bonsai has open, and attaches the resulting plannotation to the sheet's PDF. The
PDF looks and prints exactly as before; the plannotation is an attachment.

All of the work is done by ``plannotation.svg.derive``, which is tested in Plannotation's own
CI against the serializer's output. This file is only the Blender side: two operators,
a menu entry, and a best-effort guess at the active sheet's files. It cannot be tested
in CI; ``docs/bonsai.md`` is its manual test protocol.

Installation: install the ``plannotation`` package into Blender's Python, then install
this file as an add-on (Edit > Preferences > Add-ons > Install from Disk).

``from __future__ import annotations`` is deliberately absent. Blender reads operator
properties from class annotations and needs them evaluated, not stringified.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path

import bpy

bl_info = {
    "name": "Plannotation for Bonsai",
    "author": "The Plannotation Authors",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "File > Export > Plannotate sheet PDF",
    "description": "Attach a plannotation to a sheet PDF produced by Bonsai",
    "category": "Import-Export",
}

logger = logging.getLogger(__name__)

#: Suffix of the plannotated copy written beside the sheet PDF.
PLANNOTATED_SUFFIX = ".plannotated.pdf"


# ---------------------------------------------------------------------------
# Finding the active sheet. Everything here tolerates a Bonsai that differs from
# the one this was written against: a guess that fails leaves a field blank for
# the user to fill in, and never raises.
# ---------------------------------------------------------------------------
def bonsai_model():
    """Return the IFC model Bonsai has open, or None.

    Returns:
        An ``ifcopenshell.file``, or None when Bonsai is absent or has no model.
    """
    try:
        from bonsai import tool  # noqa: PLC0415 -- only present inside Blender

        return tool.Ifc.get()
    except Exception:  # noqa: BLE001 -- any failure means "no model we can use"
        return None


def bonsai_model_path():
    """Return the path of the IFC file Bonsai has open, or None.

    Returns:
        The path, or None.
    """
    try:
        from bonsai import tool  # noqa: PLC0415 -- only present inside Blender

        path = tool.Ifc.get_path()
    except Exception:  # noqa: BLE001 -- as above
        return None
    return Path(path) if path else None


def active_sheet(context, model):
    """Return the ``IfcDocumentInformation`` of the sheet selected in Bonsai.

    Args:
        context: Blender's context.
        model: The open IFC model.

    Returns:
        The sheet entity, or None.
    """
    props = getattr(context.scene, "DocProperties", None)
    sheets = getattr(props, "sheets", None)
    index = getattr(props, "active_sheet_index", -1)
    if model is None or not sheets or not 0 <= index < len(sheets):
        return None
    definition = getattr(sheets[index], "ifc_definition_id", 0)
    try:
        entity = model.by_id(definition) if definition else None
    except RuntimeError:
        return None
    if entity is None or not entity.is_a("IfcDocumentInformation"):
        return None
    return entity


def sheet_files(model_path, identification):
    """Guess a sheet's SVG and PDF from Bonsai's folder layout.

    Bonsai writes sheets into a ``sheets`` folder beside the IFC file, named after the
    sheet's identification. The newest match of each is taken.

    Args:
        model_path: The IFC file.
        identification: The sheet number.

    Returns:
        ``(svg, pdf)``, either of which may be None.
    """
    if model_path is None or not identification:
        return None, None
    folder = Path(model_path).parent / "sheets"

    def newest(pattern):
        matches = [
            path
            for path in folder.glob(pattern)
            if path.is_file() and not path.name.endswith(PLANNOTATED_SUFFIX)
        ]
        return max(matches, key=lambda path: path.stat().st_mtime, default=None)

    return newest(f"{identification}*.svg"), newest(f"{identification}*.pdf")


def plannotated_path(pdf):
    """Return where the plannotated copy of a PDF goes.

    Args:
        pdf: The sheet PDF.

    Returns:
        ``<name>.plannotated.pdf`` beside it.
    """
    return pdf.with_name(pdf.stem + PLANNOTATED_SUFFIX)


def plannotate_sheet(svg, pdf, out, *, sheet_id, title, model):
    """Attach a plannotation to one sheet PDF. The only place Plannotation is called.

    Args:
        svg: The sheet SVG Bonsai wrote.
        pdf: The PDF Bonsai rendered from it.
        out: Where to write the plannotated copy.
        sheet_id: The sheet number.
        title: The sheet title.
        model: The open IFC model, for units, schema and marks.

    Returns:
        The plannotation that was attached.
    """
    from plannotation.svg.derive import attach_from_svg, source_from_model  # noqa: PLC0415

    path = bonsai_model_path()
    # No model_sha256: the model Bonsai holds may have edits the file on disk has not
    # saved, and SPEC 7.1.4 forbids hashing a file that is not the model drawn.
    source = source_from_model(
        model, sheet_id=sheet_id, title=title or None, model_file=path.name if path else None
    )
    return attach_from_svg(svg, pdf, out, source, mod_date=datetime.now(UTC))


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------
class PLANNOTATION_OT_attach_to_sheet(bpy.types.Operator):  # noqa: N801 -- Blender's naming
    """Attach a plannotation to a sheet PDF produced by Bonsai."""

    bl_idname = "plannotation.attach_to_sheet"
    bl_label = "Plannotate sheet PDF"
    bl_options = {"REGISTER"}

    svg_path: bpy.props.StringProperty(name="Sheet SVG", subtype="FILE_PATH")
    pdf_path: bpy.props.StringProperty(name="Sheet PDF", subtype="FILE_PATH")
    out_path: bpy.props.StringProperty(
        name="Plannotated PDF",
        subtype="FILE_PATH",
        description="Leave blank to write <sheet>.plannotated.pdf beside the sheet PDF",
    )
    sheet_id: bpy.props.StringProperty(name="Sheet Number")
    title: bpy.props.StringProperty(name="Title")

    def invoke(self, context, event):  # noqa: ARG002 -- Blender's signature
        """Fill in what can be guessed from the active sheet, then ask."""
        self.prefill(context)
        return context.window_manager.invoke_props_dialog(self, width=520)

    def prefill(self, context):
        """Take the sheet number, title and files from Bonsai's active sheet."""
        model = bonsai_model()
        sheet = active_sheet(context, model)
        if sheet is None:
            return
        identification = getattr(sheet, "Identification", None) or ""
        self.sheet_id = self.sheet_id or identification
        self.title = self.title or (getattr(sheet, "Name", None) or "")
        svg, pdf = sheet_files(bonsai_model_path(), identification)
        if svg is not None and not self.svg_path:
            self.svg_path = str(svg)
        if pdf is not None and not self.pdf_path:
            self.pdf_path = str(pdf)

    def draw(self, context):  # noqa: ARG002 -- Blender's signature
        """Lay out the dialog."""
        for name in ("sheet_id", "title", "svg_path", "pdf_path", "out_path"):
            self.layout.prop(self, name)

    def execute(self, context):  # noqa: ARG002 -- Blender's signature
        """Attach the plannotation, reporting any problem in Blender's status bar."""
        try:
            import plannotation  # noqa: F401, PLC0415 -- checks the package is installed
        except ImportError:
            self.report(
                {"ERROR"},
                "Plannotation is not installed in Blender's Python; see docs/bonsai.md",
            )
            return {"CANCELLED"}
        from plannotation.errors import PlannotationError  # noqa: PLC0415

        model = bonsai_model()
        if model is None:
            self.report({"ERROR"}, "Bonsai has no IFC model open")
            return {"CANCELLED"}
        if not (self.svg_path and self.pdf_path and self.sheet_id):
            self.report({"ERROR"}, "The sheet number, SVG and PDF are all needed")
            return {"CANCELLED"}
        svg = Path(bpy.path.abspath(self.svg_path))
        pdf = Path(bpy.path.abspath(self.pdf_path))
        out = Path(bpy.path.abspath(self.out_path)) if self.out_path else plannotated_path(pdf)
        try:
            doc = plannotate_sheet(
                svg, pdf, out, sheet_id=self.sheet_id, title=self.title, model=model
            )
        except (PlannotationError, OSError, ValueError) as error:
            self.report({"ERROR"}, f"Plannotation: {error}")
            return {"CANCELLED"}
        count = len(doc.elements or [])
        self.report({"INFO"}, f"Plannotated {out.name}: {count} element(s)")
        return {"FINISHED"}


class PLANNOTATION_OT_create_sheets_and_attach(bpy.types.Operator):  # noqa: N801
    """Create the active sheet with Bonsai, then attach its plannotation."""

    bl_idname = "plannotation.create_sheets_and_attach"
    bl_label = "Create and plannotate sheet"
    bl_options = {"REGISTER"}

    def execute(self, context):  # noqa: ARG002 -- Blender's signature
        """Run Bonsai's own sheet creation, then plannotate what it wrote."""
        create = getattr(getattr(bpy.ops, "bim", None), "create_sheets", None)
        if create is None:
            self.report({"ERROR"}, "Bonsai's Create Sheets operator is not available")
            return {"CANCELLED"}
        if "FINISHED" not in create():
            self.report({"ERROR"}, "Bonsai did not create the sheet")
            return {"CANCELLED"}
        return bpy.ops.plannotation.attach_to_sheet("INVOKE_DEFAULT")


def menu_entry(self, context):  # noqa: ARG001 -- Blender's signature
    """Add the operator to File > Export."""
    self.layout.operator(PLANNOTATION_OT_attach_to_sheet.bl_idname, text="Plannotate sheet PDF")


CLASSES = (PLANNOTATION_OT_attach_to_sheet, PLANNOTATION_OT_create_sheets_and_attach)


def register():
    """Register the operators and the menu entry."""
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_export.append(menu_entry)


def unregister():
    """Remove what :func:`register` added."""
    bpy.types.TOPBAR_MT_file_export.remove(menu_entry)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
