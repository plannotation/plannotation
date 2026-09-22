# SPDX-License-Identifier: Apache-2.0
"""The PDF carrier: embedding, reading, stripping and rendering.

Three modules, with one job each:

:mod:`planlabel.pdf.embed`
    Writes labels into a PDF as associated files, reads them back out, takes them
    off again, and writes the sidecar twin. This is where the promise that a label
    never changes the page is kept.

:mod:`planlabel.pdf.render`
    Rasterises pages with pdfium and compares two documents pixel for pixel. This is
    where that promise is checked.

:mod:`planlabel.pdf.extract`
    Vectors, characters and words with coordinates, for the inference pipeline.
    Arrives in a later phase.

The names below are re-exported so that ``from planlabel.pdf import attach, read``
works; the modules themselves remain the documented home of each function.
"""

from __future__ import annotations

from planlabel.pdf.embed import (
    PAGE_DIMENSION_TOLERANCE_MM,
    AttachReport,
    CarrierReport,
    LabelSet,
    PageGeometry,
    SignatureReport,
    StripReport,
    add_declaration,
    attach,
    attach_in_place,
    build_index,
    build_sidecar,
    carrier_report,
    check_labels_against,
    has_declaration,
    is_planlabel_filename,
    page_geometry,
    pdf_date,
    read,
    read_pdf,
    read_sidecar,
    remove_declaration,
    save_labelled,
    sidecar_path,
    signature_report,
    strip,
    strip_in_place,
    write_sidecar,
)
from planlabel.pdf.render import (
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
    "LabelSet",
    "PageDiff",
    "PageGeometry",
    "SignatureReport",
    "StripReport",
    "add_declaration",
    "assert_same_appearance",
    "attach",
    "attach_in_place",
    "build_index",
    "build_sidecar",
    "carrier_report",
    "check_labels_against",
    "compare_documents",
    "describe_diffs",
    "diff_arrays",
    "has_declaration",
    "is_planlabel_filename",
    "page_count",
    "page_geometry",
    "pdf_date",
    "read",
    "read_pdf",
    "read_sidecar",
    "remove_declaration",
    "render_options",
    "render_page",
    "save_labelled",
    "sidecar_path",
    "signature_report",
    "strip",
    "strip_in_place",
    "write_sidecar",
]
