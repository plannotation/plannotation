# SPDX-License-Identifier: Apache-2.0
"""Reconstruct labels for legacy PDFs that were never authored with PlanLabel.

A drawing office has decades of PDFs and none of them has a label. This package reads
what is printed on each page -- the title block, grid bubbles, dimensions, marks and
callouts -- and writes a label saying what it found. It never modifies the input: the
output is a labelled copy (design brief section 12).

Everything produced here carries ``provenance: inferred`` and a ``confidence``
(SPEC 4.6), and SPEC 4.6.6 forbids promoting any of it to ``authored``, however good
the match. Given an IFC model, marks are matched back to the elements they name, which
turns a guessed class into the model's own class and adds the GlobalId; the result is
still inferred, because it was still established by reading the drawing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from planlabel import __version__
from planlabel.infer.match_ifc import match_to_model
from planlabel.infer.page import infer_page
from planlabel.pdf import embed
from planlabel.pdf.extract import extract_page, page_count

if TYPE_CHECKING:
    from pathlib import Path

    from planlabel.model import PageLabel

__all__ = ["infer_document", "infer_labels"]


def infer_labels(pdf: Path, *, ifc_model: Path | None = None) -> tuple[list[PageLabel], int]:
    """Reconstruct a label for every page of a document.

    Args:
        pdf: The document to read.
        ifc_model: An IFC model to match marks against, or None.

    Returns:
        One label per page, and how many elements were matched to the model.
    """
    labels: list[PageLabel] = []
    matched = 0
    for page_index in range(page_count(pdf)):
        label = infer_page(
            extract_page(pdf, page_index), page_index=page_index, generator_version=__version__
        )
        if ifc_model is not None:
            label, count = match_to_model(label, ifc_model)
            matched += count
        labels.append(label)
    return labels, matched


def infer_document(
    pdf: Path,
    out: Path,
    *,
    ifc_model: Path | None = None,
    mod_date: datetime | None = None,
) -> dict[str, object]:
    """Infer labels for a document and write a labelled copy.

    The input is never modified: the design brief is explicit, and a tool that edits the
    only copy of a legacy drawing is a tool nobody can afford to try.

    Args:
        pdf: The document to read.
        out: Where to write the labelled copy, which must not be the input.
        ifc_model: An IFC model to match marks against, or None.
        mod_date: The timestamp to stamp, or None for now.

    Returns:
        What was inferred: pages, elements, annotations, and matches to the model.

    Raises:
        ValueError: If ``out`` is the input.
    """
    if out.resolve() == pdf.resolve():
        msg = "infer writes a labelled copy and never modifies its input; choose another -o"
        raise ValueError(msg)
    labels, matched = infer_labels(pdf, ifc_model=ifc_model)
    embed.attach(
        pdf,
        labels,
        embed.build_index(labels),
        out,
        mod_date=mod_date or datetime.now(tz=UTC),
    )
    return {
        "pages": len(labels),
        "elements": sum(len(label.elements or []) for label in labels),
        "annotations": sum(len(label.annotations or []) for label in labels),
        "matchedToModel": matched,
    }
