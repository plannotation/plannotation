# SPDX-License-Identifier: Apache-2.0
"""The PDF carrier: embedding, reading, stripping and rendering.

Three modules, with one job each:

:mod:`plannotation.pdf.embed`
    Writes plannotations into a PDF as associated files, reads them back out, takes
    them off again, and writes the sidecar twin. This is where the promise that a
    plannotation never changes the page is kept.

:mod:`plannotation.pdf.render`
    Rasterises pages with pdfium and compares two documents pixel for pixel. This is
    where that promise is checked.

:mod:`plannotation.pdf.extract`
    Vectors, characters and words with coordinates, for the inference pipeline.

The names below are re-exported so that ``from plannotation.pdf import attach, read``
works; the modules themselves remain the documented home of each function.
"""

from __future__ import annotations

from plannotation.pdf.embed import (
    PAGE_DIMENSION_TOLERANCE_MM,
    AttachReport,
    CarrierReport,
    PageGeometry,
    PlannotationSet,
    SignatureReport,
    StripReport,
    add_declaration,
    attach,
    attach_in_place,
    build_index,
    build_sidecar,
    carrier_report,
    check_plannotations_against,
    has_declaration,
    is_plannotation_filename,
    page_geometry,
    pdf_date,
    read,
    read_pdf,
    read_sidecar,
    remove_declaration,
    save_plannotated,
    sidecar_path,
    signature_report,
    strip,
    strip_in_place,
    write_sidecar,
)
from plannotation.pdf.render import (
    DPI,
    RENDER_OPTIONS,
    PageDiff,
    assert_same_appearance,
    compare_documents,
    describe_diffs,
    diff_arrays,
    page_count,
    render_options,
    render_page,
)

__all__ = [
    "DPI",
    "PAGE_DIMENSION_TOLERANCE_MM",
    "RENDER_OPTIONS",
    "AttachReport",
    "CarrierReport",
    "PageDiff",
    "PageGeometry",
    "PlannotationSet",
    "SignatureReport",
    "StripReport",
    "add_declaration",
    "assert_same_appearance",
    "attach",
    "attach_in_place",
    "build_index",
    "build_sidecar",
    "carrier_report",
    "check_plannotations_against",
    "compare_documents",
    "describe_diffs",
    "diff_arrays",
    "has_declaration",
    "is_plannotation_filename",
    "page_count",
    "page_geometry",
    "pdf_date",
    "read",
    "read_pdf",
    "read_sidecar",
    "remove_declaration",
    "render_options",
    "render_page",
    "save_plannotated",
    "sidecar_path",
    "signature_report",
    "strip",
    "strip_in_place",
    "write_sidecar",
]
