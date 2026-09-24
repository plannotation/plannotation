# SPDX-License-Identifier: Apache-2.0
"""The exporter on models shaped like real ones, not like the samples.

The samples stand their ground floor at z = 0, draw along the model's own axes and hold
tidy marks in ``Tag``. A real model does none of that: Maleva 18 stands its ground floor
at z = 14.30 m, places its grid 60.8 degrees off the world axes, and fills ``Tag`` with
Archicad's internal GUIDs. Each test here builds a small model with that one trait,
through ``ifcopenshell.api``, and checks that the exporter copes. No test reaches the
network.
"""

from __future__ import annotations

import importlib.util
import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from plannotation.export.models import BuiltModel

MOD_DATE = datetime(2024, 1, 1, tzinfo=UTC)

needs_ifc = pytest.mark.skipif(
    importlib.util.find_spec("ifcopenshell") is None, reason="ifcopenshell is not installed"
)
needs_cairo = pytest.mark.skipif(
    importlib.util.find_spec("cairosvg") is None, reason="cairosvg is not installed"
)

#: Where the raised model's ground floor stands, and so its ±0,00, in metres.
RAISED_Z = 10.0


def build_raised(out: Path, *, rotation: float = 0.0) -> BuiltModel:
    """Build a one-room house whose ground floor stands at z = 10 m, turned by ``rotation``.

    A 6.0 by 4.0 metre room in 250 mm load-bearing walls, split by a 100 mm partition
    that bears nothing, with a door in the front wall. The building's ±0,00 is at
    z = 10 m, so the storey's ``Elevation`` is 0 while its placement is 10, as in
    Maleva 18.

    Args:
        out: Where to write the IFC file.
        rotation: How far the house is turned about the origin, in degrees.

    Returns:
        The model, set up for a plan of its ground floor along the model axes.
    """
    ifcopenshell = pytest.importorskip("ifcopenshell")
    from plannotation.export import models

    model = ifcopenshell.file(schema="IFC4")
    body, _ = models._setup(model, "Raised House")
    building = model.by_type("IfcBuilding")[0]
    storey = models._storey(model, building, "Ground", RAISED_Z)
    storey.Elevation = 0.0

    turn = math.radians(rotation)

    def world(x: float, y: float) -> tuple[float, float]:
        return (x * math.cos(turn) - y * math.sin(turn), x * math.sin(turn) + y * math.cos(turn))

    walls = []
    for name, (x, y), angle, length, thickness, bearing in (
        ("South", (0.0, 0.0), 0.0, 6.0, 0.25, True),
        ("East", (6.0, 0.0), 90.0, 4.0, 0.25, True),
        ("North", (6.0, 4.0), 180.0, 6.0, 0.25, True),
        ("West", (0.0, 4.0), 270.0, 4.0, 0.25, True),
        ("Partition", (3.0, 0.25), 90.0, 3.5, 0.10, False),
    ):
        wall = models._box(
            model,
            body,
            storey,
            "IfcWall",
            name,
            at=(*world(x, y), RAISED_Z),
            size=(length, thickness, 2.8),
            rotation=angle + rotation,
        )
        _pset(model, wall, "Pset_WallCommon", {"LoadBearing": bearing})
        walls.append(wall)
    models.reseed_guids(model, "raised")
    models.write_model(model, out)
    return models.BuiltModel(
        path=out,
        seed="raised",
        storey_elevation=RAISED_Z,
        cut_height=1.2,
        storey_name=str(storey.Name),
        storey_guid=str(storey.GlobalId),
        datum=RAISED_Z,
    )


def _pset(model: Any, product: Any, name: str, values: dict[str, object]) -> None:  # noqa: ANN401
    """Give a product a property set.

    Args:
        model: The ``ifcopenshell.file``.
        product: The product.
        name: The property set's name.
        values: Its properties.
    """
    import ifcopenshell.api.pset

    holder = ifcopenshell.api.pset.add_pset(model, product=product, name=name)
    ifcopenshell.api.pset.edit_pset(model, pset=holder, properties=values)


def _spec(**changes: Any) -> Any:  # noqa: ANN401
    """Return a plain sheet spec for the raised house, with some fields changed.

    Args:
        **changes: Fields to set.

    Returns:
        The spec.
    """
    from dataclasses import replace

    from plannotation.export.ifc_svg_pdf import SheetSpec

    return replace(
        SheetSpec(sheet_id="X-101", title="Ground floor", scale=50.0, grids=(), callout_to=None),
        **changes,
    )


@needs_ifc
class TestAStoreyAboveZeroIsCutAtItsOwnHeight:
    """The serializer cuts at an absolute z, so the storey's z has to go in with the cut.

    Passing the 1.2 m cut height alone cut the raised house 8.8 m below its floor, where
    there is nothing: the plan came out empty.
    """

    def test_every_wall_is_drawn_and_cut(self, tmp_path: Path) -> None:
        """Five walls stand on the storey, and the cut at 11.2 m passes through all five."""
        from plannotation.export.ifc_svg_pdf import export_sheet

        built = build_raised(tmp_path / "raised.ifc")
        exported = export_sheet(built, _spec(), generator_version="0.0.0-test")
        elements = exported.plannotation.elements or []
        assert sorted(e.name or "" for e in elements) == [
            "East",
            "North",
            "Partition",
            "South",
            "West",
        ]
        assert {e.representation for e in elements} == {"cut"}

    def test_the_viewport_states_the_storey_in_model_coordinates(self, tmp_path: Path) -> None:
        """SPEC 3.6: storey.elevation + cutHeight is the plane's height, all in the model."""
        from plannotation.export.ifc_svg_pdf import export_sheet

        built = build_raised(tmp_path / "raised.ifc")
        exported = export_sheet(built, _spec(), generator_version="0.0.0-test")
        (viewport,) = exported.plannotation.viewports or []
        assert viewport.kind == "plan"
        assert viewport.storey is not None
        assert viewport.storey.name == "Ground"
        assert viewport.storey.ifc_guid == built.storey_guid
        assert viewport.storey.elevation == pytest.approx(RAISED_Z)
        assert viewport.cut_height == pytest.approx(1.2)
        assert viewport.plane is not None
        assert viewport.plane.origin[2] == pytest.approx(RAISED_Z + 1.2)


@needs_ifc
class TestTheSheetSaysWhatTheSpecAndTheModelSay:
    """No sample's project, revision, storey or schema leaks onto another model's sheet."""

    def test_the_project_is_the_model_s_when_the_spec_names_none(self, tmp_path: Path) -> None:
        """The model's IfcProject names the project; nothing invents a number."""
        from plannotation.export.ifc_svg_pdf import export_sheet

        built = build_raised(tmp_path / "raised.ifc")
        exported = export_sheet(built, _spec(), generator_version="0.0.0-test")
        sheet = exported.plannotation.sheet
        assert sheet.project is not None
        assert sheet.project.name == "Raised House"
        assert sheet.project.number is None
        assert "Raised House" in exported.svg
        assert "Lindenhof" not in exported.svg

    def test_a_sheet_without_a_revision_states_none(self, tmp_path: Path) -> None:
        """A revision nobody issued is a false statement about the sheet."""
        from plannotation.export.ifc_svg_pdf import export_sheet

        built = build_raised(tmp_path / "raised.ifc")
        exported = export_sheet(built, _spec(), generator_version="0.0.0-test")
        assert exported.plannotation.sheet.revision is None
        assert "Index" not in exported.svg

    def test_the_schema_is_the_model_s(self, tmp_path: Path) -> None:
        """Read from the file, not assumed to be IFC4."""
        from plannotation.export.ifc_svg_pdf import export_sheet

        built = build_raised(tmp_path / "raised.ifc")
        exported = export_sheet(built, _spec(), generator_version="0.0.0-test")
        assert exported.plannotation.source_model is not None
        assert exported.plannotation.source_model.ifc_schema == "IFC4"

    def test_a_larger_page_places_the_view_on_itself(self, tmp_path: Path) -> None:
        """An A1 sheet is A1, and the view sits inside its frame, not on an A3 layout."""
        from plannotation.export.ifc_svg_pdf import export_sheet, viewport_box

        built = build_raised(tmp_path / "raised.ifc")
        exported = export_sheet(built, _spec(page_size="A1"), generator_version="0.0.0-test")
        page = exported.plannotation.page
        assert (page.width_mm, page.height_mm) == (841.0, 594.0)
        box = viewport_box(841.0, 594.0)
        for element in exported.plannotation.elements or []:
            x0, y0, x1, y1 = element.paper_bbox
            assert box[0] <= x0 <= x1 <= box[2]
            assert box[1] <= y0 <= y1 <= box[3]


class TestTheDefaultLayout:
    """The layout rule reproduces the A3 layout the samples were drawn on."""

    def test_a3_is_the_samples_viewport(self) -> None:
        """So that the samples are byte-identical to the ones drawn before the rule."""
        from plannotation.export.ifc_svg_pdf import VIEWPORT_BOX, viewport_box

        assert viewport_box(420.0, 297.0) == VIEWPORT_BOX

    @pytest.mark.parametrize("size", ["A0", "A1", "A2", "A4"])
    def test_every_page_has_a_viewport_inside_its_frame(self, size: str) -> None:
        """Above the title block and clear of the frame."""
        from plannotation.export.ifc_svg_pdf import viewport_box
        from plannotation.export.sheet import PAPER_SIZES, frame_box, title_block_box

        width, height = PAPER_SIZES[size]
        box = viewport_box(width, height)
        frame = frame_box(width, height)
        block = title_block_box(width, height)
        assert frame[0] < box[0] < box[2] < frame[2]
        assert block[3] < box[1] < box[3] < frame[3]


@needs_ifc
@needs_cairo
class TestTheRaisedHouseValidates:
    """Written out, the sheet satisfies the validator: PL-GEO-012 in particular."""

    def test_no_findings(self, tmp_path: Path) -> None:
        """Nothing at all: a real model is no excuse for a warning."""
        from plannotation.export.ifc_svg_pdf import export_sheet, write_sample
        from plannotation.validate import validate

        built = build_raised(tmp_path / "raised.ifc")
        exported = export_sheet(built, _spec(), generator_version="0.0.0-test")
        pdf = write_sample(exported, built, tmp_path / "out", mod_date=MOD_DATE)
        report = validate(pdf)
        assert [finding.code for finding in report.findings] == []
