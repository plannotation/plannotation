# SPDX-License-Identifier: Apache-2.0
"""The real example sheets, drawn from the pinned Maleva 18 model, as ``make examples`` draws them.

The other tests use small models built for them. These draw the two sheets the site
shows and pin what was checked by eye when they were last reviewed, so that a change to
the exporter that changes them is noticed. They need the model, which is never
downloaded here: they run only when ``PLANNOTATION_EXAMPLES_CACHE`` names a directory
that holds it with its pinned hash, as the CI examples job's cache does, and skip
otherwise. Each sheet takes tens of seconds to draw.
"""

from __future__ import annotations

import importlib.util
import math
import os
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.test_export_real import _distance_to, _projection_lines

if TYPE_CHECKING:
    from plannotation.export.ifc_svg_pdf import ExportedSheet


def _model() -> Path | None:
    """Return the cached Maleva 18 model, if the environment names a cache that holds it.

    Returns:
        The model's path, or None.
    """
    if importlib.util.find_spec("ifcopenshell") is None:
        return None
    directory = os.environ.get("PLANNOTATION_EXAMPLES_CACHE")
    if not directory:
        return None
    from plannotation.export.examples import MALEVA_18, sha256_of

    model = Path(directory) / MALEVA_18.filename
    if not model.is_file() or sha256_of(model) != MALEVA_18.sha256:
        return None
    return model


MODEL = _model()

pytestmark = pytest.mark.skipif(
    MODEL is None, reason="PLANNOTATION_EXAMPLES_CACHE does not hold the pinned Maleva 18 model"
)

#: What each sheet describes, by representation, as last reviewed.
COUNTS = {"M18-101": {"cut": 330, "projection": 114}, "M18-301": {"cut": 78, "projection": 139}}

#: The shortest line a sheet at 1:100 can say is some product's edge: the 5 mm an edge is
#: matched within, on the paper.
SHORTEST_MM = 0.05


@pytest.fixture(scope="module", params=sorted(COUNTS))
def sheet(request: pytest.FixtureRequest) -> tuple[str, ExportedSheet]:
    """Draw one example sheet from the cached model.

    Args:
        request: Names the sheet.

    Returns:
        Its name, and the exported sheet.
    """
    from plannotation.export.examples import EXAMPLES
    from plannotation.export.ifc_svg_pdf import export_sheet

    (example,) = [example for example in EXAMPLES if example.name == request.param]
    assert MODEL is not None
    built, spec = example.draw(MODEL)
    return example.name, export_sheet(built, spec, generator_version="0.0.0-test")


def test_what_each_sheet_describes(sheet: tuple[str, ExportedSheet]) -> None:
    """So many products cut, so many seen beyond the cut."""
    name, exported = sheet
    counts: dict[str, int] = {}
    for element in exported.plannotation.elements or []:
        key = element.representation or "none"
        counts[key] = counts.get(key, 0) + 1
    assert counts == COUNTS[name]


def test_what_lies_beyond_the_cut_is_described_by_lines_the_sheet_draws(
    sheet: tuple[str, ExportedSheet],
) -> None:
    """Every outline of a product seen beyond the cut runs along a drawn line."""
    from plannotation.export.sheet import PAPER_SIZES

    _, exported = sheet
    page = exported.plannotation.page
    height = page.height_mm
    assert (page.width_mm, height) in PAPER_SIZES.values()
    lines = _projection_lines(exported.svg, height)
    for element in exported.plannotation.elements or []:
        if element.representation != "projection":
            continue
        for outline in element.paper_outlines or []:
            (ax, ay), (bx, by) = outline[0], outline[-1]
            for point in (outline[0], ((ax + bx) / 2, (ay + by) / 2), outline[-1]):
                assert _distance_to(point, lines) <= 0.05, element.local_id


def test_no_outline_is_shorter_than_an_edge_can_be_told(
    sheet: tuple[str, ExportedSheet],
) -> None:
    """A piece of line that short cannot say whose edge it is, so none is recorded."""
    _, exported = sheet
    for element in exported.plannotation.elements or []:
        if element.representation != "projection":
            continue
        for outline in element.paper_outlines or []:
            length = sum(math.dist(a, b) for a, b in pairwise(outline))
            assert length > SHORTEST_MM, element.local_id
