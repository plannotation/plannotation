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
from itertools import pairwise
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from plannotation.export.ifc_svg_pdf import ExportedSheet
    from plannotation.export.models import BuiltModel
    from plannotation.model import Annotation, Element

MOD_DATE = datetime(2024, 1, 1, tzinfo=UTC)

needs_ifc = pytest.mark.skipif(
    importlib.util.find_spec("ifcopenshell") is None, reason="ifcopenshell is not installed"
)
needs_cairo = pytest.mark.skipif(
    importlib.util.find_spec("cairosvg") is None, reason="cairosvg is not installed"
)

#: Where the raised model's ground floor stands, and so its ±0,00, in metres.
RAISED_Z = 10.0


def build_raised(
    out: Path,
    *,
    rotation: float = 0.0,
    guid_tags: bool = False,
    roof: bool = False,
    behind: bool = False,
) -> BuiltModel:
    """Build a two-room house whose ground floor stands at z = 10 m, turned by ``rotation``.

    A 6.0 by 4.0 metre house in 250 mm load-bearing walls, split by a 100 mm partition
    -- an ``IfcWallStandardCase`` -- that bears nothing. A left-hand door opens into
    the left room from the south; the rooms are spaces "1" (Room, 9,5 m²) and "2"
    (Store, whose area the model states as zero). A grid runs A and B along the long
    walls and 1 and 2 along the short ones, and turns with the house. The building's
    ±0,00 is at z = 10 m, so the storey's ``Elevation`` is 0 while its placement is 10,
    as in Maleva 18.

    Args:
        out: Where to write the IFC file.
        rotation: How far the house and its grid are turned about the origin, degrees.
        guid_tags: Fill every wall's ``Tag`` with a GUID, as Archicad does.
        roof: Add a storey at the top of the walls, for a section's second level.
        behind: Add a metre-high box outside the south wall, which a section looking
            south cannot see.

    Returns:
        The model, set up for a plan of its ground floor along the model axes.
    """
    pytest.importorskip("ifcopenshell")
    import ifcopenshell
    import ifcopenshell.api.aggregate
    import ifcopenshell.api.geometry
    import ifcopenshell.api.grid
    import ifcopenshell.api.root
    import ifcopenshell.api.spatial
    import numpy as np

    from plannotation.export import models

    model = ifcopenshell.file(schema="IFC4")
    body, storey = models._setup(model, "Raised House")
    building = model.by_type("IfcBuilding")[0]
    raised = np.eye(4)
    raised[2, 3] = RAISED_Z
    ifcopenshell.api.geometry.edit_object_placement(
        model, product=storey, matrix=raised, is_si=True
    )
    storey.Name, storey.Elevation = "Ground", 0.0
    if roof:
        upper = models._storey(model, building, "Roof", RAISED_Z + 2.8)
        upper.Elevation = 2.8

    turn = math.radians(rotation)

    def world(x: float, y: float) -> tuple[float, float]:
        return (x * math.cos(turn) - y * math.sin(turn), x * math.sin(turn) + y * math.cos(turn))

    walls = {}
    for name, (x, y), angle, length, thickness, bearing, kind in (
        ("South", (0.0, 0.0), 0.0, 6.0, 0.25, True, "IfcWall"),
        ("East", (6.0, 0.0), 90.0, 4.0, 0.25, True, "IfcWall"),
        ("North", (6.0, 4.0), 180.0, 6.0, 0.25, True, "IfcWall"),
        ("West", (0.0, 4.0), 270.0, 4.0, 0.25, True, "IfcWall"),
        ("Partition", (3.0, 0.25), 90.0, 3.5, 0.10, False, "IfcWallStandardCase"),
    ):
        wall = models._box(
            model,
            body,
            storey,
            kind,
            name,
            at=(*world(x, y), RAISED_Z),
            size=(length, thickness, 2.8),
            rotation=angle + rotation,
        )
        _pset(model, wall, "Pset_WallCommon", {"LoadBearing": bearing})
        if guid_tags:
            wall.Tag = "E3015C6C-82A0-94D5-92BB-FE54DA4A5EC5"
        walls[name] = wall

    door = models._filling(
        model,
        body,
        storey,
        "IfcDoor",
        "Front door",
        at=(*world(1.0, 0.0), RAISED_Z),
        width=1.0,
        height=2.1,
        rotation=rotation,
    )
    door.OperationType = "SINGLE_SWING_LEFT"
    models._opening(
        model,
        body,
        walls["South"],
        door,
        at=(*world(1.0, -0.01), RAISED_Z),
        size=(1.0, 0.27, 2.1),
        rotation=rotation,
    )

    if behind:
        models._box(
            model,
            body,
            storey,
            "IfcBuildingElementProxy",
            "Behind",
            at=(*world(4.0, -1.2), RAISED_Z),
            size=(1.0, 0.2, 1.0),
            rotation=rotation,
        )

    for number, long_name, area, (x0, x1) in (
        ("1", "Room", "9,5", (0.25, 2.9)),
        ("2", "Store", "0.000", (3.0, 5.75)),
    ):
        space = models._box(
            model,
            body,
            None,
            "IfcSpace",
            number,
            at=(*world(x0, 0.25), RAISED_Z),
            size=(x1 - x0, 3.5, 2.6),
            rotation=rotation,
        )
        space.LongName = long_name
        ifcopenshell.api.aggregate.assign_object(model, products=[space], relating_object=storey)
        _pset(model, space, "Ruum", {"Pindala": area})

    grid = ifcopenshell.api.root.create_entity(model, ifc_class="IfcGrid", name="Grid")
    ifcopenshell.api.geometry.edit_object_placement(model, product=grid, matrix=raised, is_si=True)
    ifcopenshell.api.spatial.assign_container(model, products=[grid], relating_structure=storey)
    for tag, uvw, start, end in (
        ("A", "UAxes", (-1.0, 0.125), (7.0, 0.125)),
        ("B", "UAxes", (-1.0, 3.875), (7.0, 3.875)),
        ("1", "VAxes", (0.125, -1.0), (0.125, 5.0)),
        ("2", "VAxes", (5.875, -1.0), (5.875, 5.0)),
    ):
        axis = ifcopenshell.api.grid.create_grid_axis(model, axis_tag=tag, uvw_axes=uvw, grid=grid)
        ifcopenshell.api.grid.create_axis_curve(
            model,
            p1=np.array((*world(*start), RAISED_Z)),
            p2=np.array((*world(*end), RAISED_Z)),
            grid_axis=axis,
        )
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
        walls = [e for e in elements if e.ifc_class.startswith("IfcWall")]
        assert sorted(e.name or "" for e in walls) == [
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


def _drawn_plan(
    tmp_path: Path,
    *,
    rotation: float = 0.0,
    guid_tags: bool = False,
    behind: bool = False,
    **changes: object,
) -> tuple[BuiltModel, ExportedSheet]:
    """Draw the raised house's ground floor square to its grid, as the examples draw.

    Args:
        tmp_path: Where to build the model.
        rotation: How far the house is turned, in degrees.
        guid_tags: Fill every wall's ``Tag`` with a GUID.
        behind: Add the low box outside the south wall.
        **changes: Sheet spec fields to change from the full treatment.

    Returns:
        The model as drawn, and the exported sheet.
    """
    from dataclasses import replace

    from plannotation.export.drafting import RoomLabels, SectionMark, ViewTitle
    from plannotation.export.ifc_svg_pdf import export_sheet
    from plannotation.export.views import (
        building_datum,
        find_storey,
        grid_axes_on,
        grid_lines,
        open_model,
        plan_along,
        storey_products,
    )

    built = build_raised(
        tmp_path / "raised.ifc", rotation=rotation, guid_tags=guid_tags, behind=behind
    )
    model = open_model(built.path)
    storey = find_storey(model, "Ground")
    lines = grid_lines(model)
    cut = plan_along(lines, origin=("1", "A"), along="A", z=storey.z + 1.2)
    built = replace(
        built,
        section=cut,
        include=storey_products(model, storey),
        datum=building_datum(model),
    )
    spec = _spec(
        grids=grid_axes_on(lines, cut),
        presentation=True,
        grid_overshoot_mm=20.0,
        rooms=RoomLabels(area_property="Ruum.Pindala"),
        door_swings=True,
        section_marks=(SectionMark(label="A", target_sheet="X-301", position=4.5),),
        view_title=ViewTitle(text="Ground floor"),
        north_arrow=True,
        scale_bar=True,
        page_size="A2",
    )
    return built, export_sheet(built, replace(spec, **changes), generator_version="0.0.0-test")


def _by_class(exported: ExportedSheet, prefix: str) -> list[Element]:
    """Return the elements whose class starts with a prefix.

    Args:
        exported: The exported sheet.
        prefix: Such as ``IfcWall``.

    Returns:
        The elements.
    """
    return [e for e in exported.plannotation.elements or [] if e.ifc_class.startswith(prefix)]


def _annotations(exported: ExportedSheet, kind: str) -> list[Annotation]:
    """Return the annotations of one type.

    Args:
        exported: The exported sheet.
        kind: The annotation type.

    Returns:
        The annotations.
    """
    return [a for a in exported.plannotation.annotations or [] if a.annotation_type == kind]


@needs_ifc
class TestTheModelIsReadInItsOwnTerms:
    """Storeys, datum and grid come from the model, in metres, whatever its unit."""

    def test_the_datum_is_where_the_storeys_elevation_is_measured_from(
        self, tmp_path: Path
    ) -> None:
        """A storey at z = 10 m with Elevation 0 puts the building's ±0,00 at 10 m."""
        from plannotation.export.views import building_datum, open_model, storey_levels

        built = build_raised(tmp_path / "raised.ifc", roof=True)
        model = open_model(built.path)
        assert building_datum(model) == pytest.approx(RAISED_Z)
        levels = storey_levels(model)
        assert [(level.name, level.elevation) for level in levels] == [
            ("Ground", RAISED_Z),
            ("Roof", pytest.approx(RAISED_Z + 2.8)),
        ]

    def test_storeys_that_disagree_about_their_datum_are_refused(self, tmp_path: Path) -> None:
        """No level could be printed on a building whose storeys disagree about ±0,00."""
        from plannotation.errors import ExportError
        from plannotation.export.views import building_datum, open_model

        built = build_raised(tmp_path / "raised.ifc", roof=True)
        model = open_model(built.path)
        next(s for s in model.by_type("IfcBuildingStorey") if s.Name == "Roof").Elevation = 2.0
        with pytest.raises(ExportError, match="disagree"):
            building_datum(model)

    def test_a_storey_holds_its_elements_and_spaces_but_no_voids(self, tmp_path: Path) -> None:
        """Walls, the door and both rooms; not the opening the door fills, nor the grid."""
        from plannotation.export.views import find_storey, open_model, storey_products

        built = build_raised(tmp_path / "raised.ifc")
        model = open_model(built.path)
        held = storey_products(model, find_storey(model, "Ground"))
        classes = sorted(model.by_guid(guid).is_a() for guid in held)
        assert classes == ["IfcDoor", "IfcSpace", "IfcSpace"] + ["IfcWall"] * 4 + [
            "IfcWallStandardCase"
        ]
        without = storey_products(model, find_storey(model, "Ground"), spaces=False)
        assert len(without) == len(held) - 2

    @pytest.mark.parametrize("rotation", [0.0, 30.0, -60.8])
    def test_a_plan_along_the_grid_places_the_grid_square_to_the_paper(
        self, tmp_path: Path, rotation: float
    ) -> None:
        """Grid 1 at plane x 0 and 2 at 5.75; A at plane y 0 and B at 3.75, however turned."""
        from plannotation.export.views import grid_axes_on, grid_lines, open_model, plan_along

        built = build_raised(tmp_path / "raised.ifc", rotation=rotation)
        lines = grid_lines(open_model(built.path))
        cut = plan_along(lines, origin=("1", "A"), along="A", z=11.2)
        axes = {axis.axis: (axis.vertical, axis.position) for axis in grid_axes_on(lines, cut)}
        assert axes["1"] == (True, pytest.approx(0.0, abs=1e-9))
        assert axes["2"] == (True, pytest.approx(5.75))
        assert axes["A"] == (False, pytest.approx(0.0, abs=1e-9))
        assert axes["B"] == (False, pytest.approx(3.75))
        x_axis, _ = cut.axes()
        assert math.degrees(math.atan2(x_axis[1], x_axis[0])) == pytest.approx(rotation)

    def test_a_section_between_two_axes_looks_towards_the_third(self, tmp_path: Path) -> None:
        """Halfway between A and B, looking at A: grids 1 and 2 cross it, A and B do not."""
        from plannotation.export.views import grid_axes_on, grid_lines, open_model, section_between

        built = build_raised(tmp_path / "raised.ifc")
        lines = grid_lines(open_model(built.path))
        cut = section_between(lines, first="A", second="B", looking_to="A", datum=RAISED_Z)
        assert cut.location[1] == pytest.approx(2.0)
        # The viewer stands on B's side, looking towards A.
        assert cut.direction == pytest.approx((0.0, 1.0, 0.0))
        axes = {axis.axis: axis for axis in grid_axes_on(lines, cut)}
        assert sorted(axes) == ["1", "2"]
        assert axes["2"].position - axes["1"].position == pytest.approx(-5.75)


@needs_ifc
class TestARotatedPlanIsDrawnSquareToItsGrid:
    """A plan along a grid the model places at an angle comes out square on the paper."""

    @pytest.mark.parametrize("rotation", [0.0, 30.0])
    def test_the_long_walls_run_along_the_paper(self, tmp_path: Path, rotation: float) -> None:
        """At 1:50 the 6 m south wall is 120 mm long and 5 mm thick on the paper."""
        _, exported = _drawn_plan(tmp_path, rotation=rotation)
        south = next(e for e in _by_class(exported, "IfcWall") if e.name == "South")
        x0, y0, x1, y1 = south.paper_bbox
        assert (x1 - x0, y1 - y0) == (pytest.approx(120.0, abs=0.01), pytest.approx(5.0, abs=0.01))

    def test_the_viewport_is_a_plan_on_the_grid_s_plane(self, tmp_path: Path) -> None:
        """Its plane is the cut, 1.2 m above the storey, with the grid's x-axis."""
        _, exported = _drawn_plan(tmp_path, rotation=30.0)
        (viewport,) = exported.plannotation.viewports or []
        assert viewport.kind == "plan"
        assert viewport.local_id == "vp-plan"
        assert viewport.plane is not None
        assert viewport.plane.origin[2] == pytest.approx(RAISED_Z + 1.2)
        assert viewport.plane.x_axis[:2] == pytest.approx(
            (math.cos(math.radians(30)), math.sin(math.radians(30))), abs=1e-3
        )

    def test_only_the_storey_is_drawn(self, tmp_path: Path) -> None:
        """Five walls, one door and two rooms, and nothing else."""
        _, exported = _drawn_plan(tmp_path)
        classes = sorted(e.ifc_class for e in exported.plannotation.elements or [])
        assert classes == ["IfcDoor", "IfcSpace", "IfcSpace"] + ["IfcWall"] * 4 + [
            "IfcWallStandardCase"
        ]


@needs_ifc
class TestThePresentation:
    """Presentation attributes on each group, since the serializer's CSS does not survive."""

    @staticmethod
    def _group(svg: str, guid: str) -> str:
        """Return a product group's opening tag.

        Args:
            svg: The sheet.
            guid: The product's GlobalId.

        Returns:
            The tag.
        """
        import re

        match = re.search(rf'<g\b[^>]*ifc:guid="{re.escape(guid)}"[^>]*>', svg)
        assert match is not None
        return match.group(0)

    def test_load_bearing_walls_are_solid_partitions_grey(self, tmp_path: Path) -> None:
        """LoadBearing decides it, from Pset_WallCommon."""
        _, exported = _drawn_plan(tmp_path)
        for wall in _by_class(exported, "IfcWall"):
            fill = "#9a9a9a" if wall.name == "Partition" else "#000"
            assert f'fill="{fill}"' in self._group(exported.svg, wall.ifc_guid)

    def test_doors_are_outlined_and_spaces_not_drawn(self, tmp_path: Path) -> None:
        """A black blob where a door stands is not a door."""
        _, exported = _drawn_plan(tmp_path)
        (door,) = _by_class(exported, "IfcDoor")
        assert 'fill="none"' in self._group(exported.svg, door.ifc_guid)
        for space in _by_class(exported, "IfcSpace"):
            assert 'stroke="none"' in self._group(exported.svg, space.ifc_guid)

    def test_grid_lines_are_chain_lines(self, tmp_path: Path) -> None:
        """Long dash, dot: a grid is not a wall."""
        _, exported = _drawn_plan(tmp_path)
        assert exported.svg.count("stroke-dasharray") >= 4

    def test_without_it_the_serializer_s_paths_are_untouched(self, tmp_path: Path) -> None:
        """The samples are drawn without it, and stay byte-identical."""
        _, exported = _drawn_plan(tmp_path, presentation=False)
        assert "#9a9a9a" not in exported.svg
        assert "stroke-dasharray" not in exported.svg


@needs_ifc
class TestMarksShapedLikeGuidsAreNotMarks:
    """Archicad writes its own GUID into Tag; no drawing prints that."""

    @pytest.mark.parametrize(
        ("tag", "kind"),
        [
            ("E3015C6C-82A0-94D5-92BB-FE54DA4A5EC5", "id"),
            ("{E3015C6C-82A0-94D5-92BB-FE54DA4A5EC5}", "id"),
            ("2O2Fr$t4X7Zf8NOew3FLOH", "id"),
            ("Pos. 1", "mark"),
            ("T1", "mark"),
            ("W-12", "mark"),
            ("  ", "id"),
        ],
    )
    def test_is_mark(self, tag: str, kind: str) -> None:
        """A UUID or a GlobalId is an id; anything a person would write is a mark."""
        from plannotation.export.ifc_svg_pdf import is_mark

        assert is_mark(tag, "0000000000000000000000") is (kind == "mark")

    def test_a_tag_that_is_the_element_s_own_guid_is_no_mark(self) -> None:
        """Whatever it looks like."""
        from plannotation.export.ifc_svg_pdf import is_mark

        assert not is_mark("A1", "A1")

    def test_guid_tags_leave_no_mark_on_the_sheet(self, tmp_path: Path) -> None:
        """No tag annotation, and no element claiming the GUID as its tag."""
        _, exported = _drawn_plan(tmp_path, guid_tags=True)
        assert all(e.tag is None for e in _by_class(exported, "IfcWall"))
        assert "E3015C6C" not in exported.svg


@needs_ifc
class TestDoorSwings:
    """The biggest tell of a machine-drawn plan is a door that does not open."""

    def test_a_left_hand_door_hangs_at_its_origin_and_opens_to_positive_y(self) -> None:
        """IFC's convention: seen along +y, the hinge is on the left."""
        from plannotation.export.doors import local_swing

        swing = local_swing("SINGLE_SWING_LEFT", 1.0, (0.0, 0.1))
        assert swing is not None
        (leaf,) = swing.leaves
        assert leaf == ((0.0, 0.1), (0.0, pytest.approx(1.1)))
        (arc,) = swing.arcs
        assert arc[0] == pytest.approx((0.0, 1.1))
        assert arc[-1] == pytest.approx((1.0, 0.1))
        assert all(math.hypot(x, y - 0.1) == pytest.approx(1.0) for x, y in arc)

    def test_a_right_hand_door_hangs_at_its_far_side(self) -> None:
        """The mirror image."""
        from plannotation.export.doors import local_swing

        swing = local_swing("SINGLE_SWING_RIGHT", 0.9, (0.0, 0.0))
        assert swing is not None
        assert swing.leaves[0][0] == (0.9, 0.0)
        assert swing.arcs[0][-1] == pytest.approx((0.0, 0.0))
        assert all(y >= -1e-9 for _, y in swing.arcs[0])

    def test_a_double_door_opens_two_leaves_split_as_its_panel_says(self) -> None:
        """Hinged at both jambs, meeting where the first panel ends."""
        from plannotation.export.doors import local_swing

        swing = local_swing("DOUBLE_DOOR_SINGLE_SWING", 2.0, (0.0, 0.0), first_share=0.4)
        assert swing is not None
        assert [leaf[0] for leaf in swing.leaves] == [(0.0, 0.0), (2.0, 0.0)]
        assert [leaf[1][1] for leaf in swing.leaves] == pytest.approx([0.8, 1.2])

    def test_a_double_swing_door_sweeps_both_faces(self) -> None:
        """And stays on the side of each face it swings from."""
        from plannotation.export.doors import local_swing

        swing = local_swing("DOUBLE_SWING_RIGHT", 1.0, (-0.1, 0.1))
        assert swing is not None
        above, below = swing.arcs
        assert all(y >= 0.1 - 1e-9 for _, y in above)
        assert all(y <= -0.1 + 1e-9 for _, y in below)

    @pytest.mark.parametrize("operation", ["SLIDING_TO_LEFT", "USERDEFINED", "NOTDEFINED"])
    def test_a_door_the_model_does_not_say_swings_gets_no_swing(self, operation: str) -> None:
        """Drawing a guess would be a false statement about the building."""
        from plannotation.export.doors import local_swing

        assert local_swing(operation, 1.0, (0.0, 0.1)) is None

    @pytest.mark.parametrize("rotation", [0.0, 30.0])
    def test_the_front_door_opens_into_the_room(self, tmp_path: Path, rotation: float) -> None:
        """Its arc lies inside the house, north of the south wall, hinged at grid 1's side."""
        _, exported = _drawn_plan(tmp_path, rotation=rotation)
        (door,) = _by_class(exported, "IfcDoor")
        south = next(e for e in _by_class(exported, "IfcWall") if e.name == "South")
        swing = door.paper_outlines[-2:]
        points = [point for line in swing for point in line]
        # A 1 m leaf at 1:50 reaches 20 mm into the room from the wall's inner face.
        assert min(y for _, y in points) >= south.paper_bbox[1]
        assert max(y for _, y in points) == pytest.approx(south.paper_bbox[3] + 20.0, abs=1.0)
        hinge = swing[0][0]
        assert hinge[0] == pytest.approx(min(x for x, _ in points), abs=0.01)
        assert door.paper_bbox[3] >= max(y for _, y in points) - 0.001
        assert 'stroke-width="0.1"' in exported.svg


@needs_ifc
class TestRoomLabels:
    """Number, name and area, from the model, inside the room they name."""

    def test_each_room_is_labelled_with_what_the_model_says(self, tmp_path: Path) -> None:
        """Room 1 has all three; room 2's area is stated as zero and is left off."""
        _, exported = _drawn_plan(tmp_path)
        spaces = {e.local_id: e for e in _by_class(exported, "IfcSpace")}
        labels = {
            (spaces[a.shows.element].name, a.shows.property_name): a.text
            for a in exported.plannotation.annotations or []
            if a.shows is not None and a.shows.element in spaces
        }
        assert labels == {
            ("1", "Name"): "1",
            ("1", "LongName"): "Room",
            ("1", "Ruum.Pindala"): "9,5 m²",
            ("2", "Name"): "2",
            ("2", "LongName"): "Store",
        }

    def test_the_number_is_a_tag_and_the_rest_text(self, tmp_path: Path) -> None:
        """The number is the room's mark; name and area are what it says about itself."""
        _, exported = _drawn_plan(tmp_path)
        tags = {a.text for a in _annotations(exported, "tag")}
        assert tags == {"1", "2"}

    def test_every_label_is_inside_its_room_and_off_every_wall(self, tmp_path: Path) -> None:
        """A label on a wall is black on black."""
        from plannotation.export.drafting import overlaps

        _, exported = _drawn_plan(tmp_path, rotation=30.0)
        spaces = {e.local_id: e for e in _by_class(exported, "IfcSpace")}
        walls = [e.paper_bbox for e in _by_class(exported, "IfcWall")]
        for label in exported.plannotation.annotations or []:
            if label.shows is None or label.shows.element not in spaces:
                continue
            room = spaces[label.shows.element].paper_bbox
            x0, y0, x1, y1 = label.paper_bbox
            assert room[0] <= x0 <= x1 <= room[2]
            assert room[1] <= y0 <= y1 <= room[3]
            assert not any(overlaps(label.paper_bbox, wall) for wall in walls)

    def test_the_space_carries_the_property_its_area_label_shows(self, tmp_path: Path) -> None:
        """So that PL-REF-012 finds the property set the label names."""
        _, exported = _drawn_plan(tmp_path)
        room = next(e for e in _by_class(exported, "IfcSpace") if e.name == "1")
        assert room.pset("Ruum") == {"Pindala": "9,5"}

    def test_the_floor_level_is_marked_relative_to_the_datum(self, tmp_path: Path) -> None:
        """The ground floor stands at z = 10 m, which is ±0,00."""
        _, exported = _drawn_plan(tmp_path)
        (level,) = _annotations(exported, "level")
        assert level.text == "±0,00"
        assert level.elevation == 0
        assert level.ifc_guid is not None


class TestAreaText:
    """An area prints as the model states it, and a zero area not at all."""

    @pytest.mark.parametrize(
        ("value", "printed"),
        [
            ("25,2", "25,2 m²"),
            (25.23, "25,2 m²"),
            (6, "6,0 m²"),
            ("0.000", None),
            (0.0, None),
            (None, None),
            ("n/a", None),
            (True, None),
        ],
    )
    def test_area_text(self, value: object, printed: str | None) -> None:
        """Decimal comma, one place, the model's own text kept."""
        from plannotation.export.drafting import area_text

        assert area_text(value, "m²") == printed


@needs_ifc
class TestTheSheetsFurniture:
    """Section marks, view title, north arrow and scale bar, each described."""

    def test_the_section_is_marked_at_both_ends_clear_of_the_house(self, tmp_path: Path) -> None:
        """Two marks targeting the section's sheet, neither on the house."""
        from plannotation.export.drafting import overlaps

        _, exported = _drawn_plan(tmp_path)
        marks = _annotations(exported, "sectionMark")
        assert len(marks) == 2
        assert {(m.target.sheet_id, m.target.detail) for m in marks} == {("X-301", "A")}
        walls = [e.paper_bbox for e in _by_class(exported, "IfcWall")]
        for mark in marks:
            assert not any(overlaps(mark.paper_bbox, wall) for wall in walls)
        # Grid 1 is at plane x 0, the mark at 4.5 m: 90 mm to the right at 1:50.
        (grid_1,) = [a for a in _annotations(exported, "grid") if a.axis == "1"]
        assert marks[0].geometry[0][0] - grid_1.geometry[0][0] == pytest.approx(90.0, abs=0.01)

    def test_a_section_s_title_calls_out_the_sheet_it_is_marked_on(self, tmp_path: Path) -> None:
        """The bubble under Section A-A points back at the plan."""
        from dataclasses import replace

        from plannotation.export.drafting import ViewTitle
        from plannotation.export.ifc_svg_pdf import export_sheet
        from plannotation.export.views import (
            grid_axes_on,
            grid_lines,
            open_model,
            section_between,
            storey_levels,
        )

        built = build_raised(tmp_path / "raised.ifc", roof=True)
        model = open_model(built.path)
        lines = grid_lines(model)
        cut = section_between(lines, first="A", second="B", looking_to="A", datum=RAISED_Z)
        built = replace(built, section=cut, cut_height=None, levels=storey_levels(model))
        spec = _spec(
            sheet_id="X-301",
            drawing_type="section",
            grids=grid_axes_on(lines, cut),
            presentation=True,
            view_title=ViewTitle(text="Section A-A", label="A", target_sheet="X-101"),
            page_size="A2",
        )
        exported = export_sheet(built, spec, generator_version="0.0.0-test")
        (callout,) = _annotations(exported, "callout")
        assert callout.target.sheet_id == "X-101"
        assert callout.text == "A"
        levels = sorted((a.text, a.elevation) for a in _annotations(exported, "level"))
        assert levels == [("+2,80", pytest.approx(2.8)), ("±0,00", 0)]
        (viewport,) = exported.plannotation.viewports or []
        assert viewport.kind == "section"

    def test_north_points_where_the_model_says(self, tmp_path: Path) -> None:
        """The house turned 30 degrees: north, the model's +y, leans 30 degrees right."""
        _, exported = _drawn_plan(tmp_path, rotation=30.0)
        (arrow,) = _annotations(exported, "northArrow")
        (x0, y0), (x1, y1) = arrow.geometry
        assert math.degrees(math.atan2(x1 - x0, y1 - y0)) == pytest.approx(30.0, abs=0.1)

    def test_the_scale_bar_is_ten_metres_at_the_sheet_s_scale(self, tmp_path: Path) -> None:
        """200 mm at 1:50."""
        _, exported = _drawn_plan(tmp_path)
        (bar,) = _annotations(exported, "scaleBar")
        (x0, _), (x1, _) = bar.geometry
        assert x1 - x0 == pytest.approx(200.0)
        assert (bar.value, bar.unit) == (10, "m")


@needs_ifc
class TestTheGroundTruthCountsSubtypes:
    """An IfcWallStandardCase is a wall."""

    def test_is_subtype(self) -> None:
        """In the model's own schema."""
        from plannotation.export.ifc_svg_pdf import is_subtype

        assert is_subtype("IFC2X3", "IfcWallStandardCase", "IfcWall")
        assert is_subtype("IFC4", "IfcWall", "IfcWall")
        assert not is_subtype("IFC4", "IfcWall", "IfcWallStandardCase")
        assert not is_subtype("IFC4", "IfcNoSuchThing", "IfcWall")

    def test_the_wall_count_includes_the_standard_case(self, tmp_path: Path) -> None:
        """Four IfcWall and one IfcWallStandardCase are five walls."""
        _, exported = _drawn_plan(tmp_path)
        question = next(q for q in exported.ground_truth if "walls (IfcWall)" in str(q["question"]))
        assert question["answer"] == 5


def _mesh(
    quads: list[list[tuple[float, float, float]]],
) -> tuple[Any, Any]:
    """Return quadrilaterals as a mesh: welded-apart vertices and two triangles each.

    Args:
        quads: Each quadrilateral's corners in order, in plane x, y and depth; the
            triangles keep that order's winding.

    Returns:
        The vertices and the triangles, as ``edges_beyond`` takes them.
    """
    import numpy as np

    points: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    for quad in quads:
        base = len(points)
        points += quad
        faces += [(base, base + 1, base + 2), (base, base + 2, base + 3)]
    return np.array(points, dtype=np.float64), np.array(faces, dtype=np.int64)


def _edge_set(rows: Any) -> set[frozenset[tuple[float, ...]]]:  # noqa: ANN401
    """Return edges as unordered pairs of rounded ends, to compare regardless of direction.

    Args:
        rows: ``x0, y0, d0, x1, y1, d1`` per edge.

    Returns:
        The edges.
    """
    # Adding 0.0 turns a -0.0 into 0.0, which would otherwise not equal it as a key.
    return {
        frozenset(
            {
                tuple(round(float(v), 6) + 0.0 for v in row[:3]),
                tuple(round(float(v), 6) + 0.0 for v in row[3:]),
            }
        )
        for row in rows
    }


def _edge(*ends: tuple[float, float, float]) -> frozenset[tuple[float, ...]]:
    """Return one edge as :func:`_edge_set` writes it.

    Args:
        *ends: Its two ends.

    Returns:
        The edge.
    """
    return frozenset(tuple(float(v) for v in end) for end in ends)


class TestEdgesBeyond:
    """A shape's edges beyond the cut are where its faces meet at an angle or end."""

    def test_a_square_gives_its_sides_not_its_diagonal(self) -> None:
        """Two triangles in one plane share a diagonal that no drawing shows."""
        from plannotation.export.ifc_svg_pdf import edges_beyond

        square = [(0.0, 0.0, -1.0), (1.0, 0.0, -1.0), (1.0, 1.0, -1.0), (0.0, 1.0, -1.0)]
        rows = edges_beyond(*_mesh([square]))
        assert _edge_set(rows) == {
            _edge(square[0], square[1]),
            _edge(square[1], square[2]),
            _edge(square[2], square[3]),
            _edge(square[3], square[0]),
        }

    def test_a_cube_through_the_plane_keeps_only_what_lies_beyond(self) -> None:
        """Its far face's four edges, and its side edges clipped where the plane cuts them."""
        from plannotation.export.ifc_svg_pdf import edges_beyond

        near, far = 0.5, -0.5
        faces = [
            [(0.0, 0.0, far), (0.0, 1.0, far), (1.0, 1.0, far), (1.0, 0.0, far)],
            [(0.0, 0.0, near), (1.0, 0.0, near), (1.0, 1.0, near), (0.0, 1.0, near)],
            [(0.0, 0.0, far), (1.0, 0.0, far), (1.0, 0.0, near), (0.0, 0.0, near)],
            [(1.0, 0.0, far), (1.0, 1.0, far), (1.0, 1.0, near), (1.0, 0.0, near)],
            [(1.0, 1.0, far), (0.0, 1.0, far), (0.0, 1.0, near), (1.0, 1.0, near)],
            [(0.0, 1.0, far), (0.0, 0.0, far), (0.0, 0.0, near), (0.0, 1.0, near)],
        ]
        rows = edges_beyond(*_mesh(faces))
        assert len(rows) == 8
        assert rows[:, [2, 5]].max() <= 0.0
        corners = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        far_face = {_edge((*a, far), (*b, far)) for a, b in pairwise([*corners, corners[0]])}
        sides = {_edge((*a, far), (*a, 0.0)) for a in corners}
        assert _edge_set(rows) == far_face | sides

    def test_two_faces_at_a_right_angle_keep_the_edge_they_share(self) -> None:
        """A fold is an edge: seven of the two squares' eight sides, the shared one once."""
        from plannotation.export.ifc_svg_pdf import edges_beyond

        floor = [(0.0, 0.0, -1.0), (1.0, 0.0, -1.0), (1.0, 1.0, -1.0), (0.0, 1.0, -1.0)]
        wall = [(1.0, 0.0, -1.0), (0.0, 0.0, -1.0), (0.0, 0.0, -2.0), (1.0, 0.0, -2.0)]
        rows = edges_beyond(*_mesh([floor, wall]))
        assert len(rows) == 7
        assert _edge((0.0, 0.0, -1.0), (1.0, 0.0, -1.0)) in _edge_set(rows)

    def test_an_open_mesh_keeps_its_border(self) -> None:
        """An L of three flat squares: its eight border edges, the notch among them."""
        from plannotation.export.ifc_svg_pdf import edges_beyond

        squares = [
            [(x, y, -1.0), (x + 1, y, -1.0), (x + 1, y + 1, -1.0), (x, y + 1, -1.0)]
            for x, y in ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))
        ]
        rows = edges_beyond(*_mesh(squares))
        ring = [(0, 0), (1, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2), (0, 1)]
        assert _edge_set(rows) == {
            _edge((*a, -1.0), (*b, -1.0)) for a, b in pairwise([*ring, ring[0]])
        }

    def test_nothing_in_front_of_the_plane(self) -> None:
        """A shape wholly on the viewer's side has no edge beyond the cut."""
        from plannotation.export.ifc_svg_pdf import edges_beyond

        square = [(0.0, 0.0, 1.0), (1.0, 0.0, 1.0), (1.0, 1.0, 1.0), (0.0, 1.0, 1.0)]
        assert edges_beyond(*_mesh([square])).shape == (0, 6)


class TestLineOwners:
    """Each line drawn beyond the cut belongs to the edge nearest the viewer along it."""

    @staticmethod
    def _owners(line: tuple[float, float, float, float], **edges: list[tuple[float, ...]]) -> Any:  # noqa: ANN401
        """Return the pieces of one line, rounded, given each product's edges.

        Args:
            line: ``x0, y0, x1, y1``.
            **edges: Rows ``x0, y0, d0, x1, y1, d1`` by product name.

        Returns:
            The line's pieces, fractions rounded to six places.
        """
        import numpy as np

        from plannotation.export.ifc_svg_pdf import line_owners

        (pieces,) = line_owners(
            np.array([line], dtype=np.float64),
            {name: np.array(rows, dtype=np.float64).reshape(-1, 6) for name, rows in edges.items()},
        )
        return [(round(first, 6), round(second, 6), owner) for first, second, owner in pieces]

    def test_a_line_along_a_jamb_and_then_a_wall_splits_between_them(self) -> None:
        """The door's jamb stands in front of the wall up to the head; above it, the wall."""
        pieces = self._owners(
            (0.0, 0.0, 0.0, 3.0),
            door=[(0.0, 0.0, -1.0, 0.0, 2.0, -1.0)],
            wall=[(0.0, 0.0, -1.2, 0.0, 3.0, -1.2)],
        )
        assert pieces == [(0.0, round(2 / 3, 6), "door"), (round(2 / 3, 6), 1.0, "wall")]

    def test_where_edges_coincide_the_nearest_owns_the_line(self) -> None:
        """Whichever product's name sorts first, the one nearer the viewer wins."""
        pieces = self._owners(
            (0.0, 0.0, 1.0, 0.0),
            a_far=[(0.0, 0.0, -2.0, 1.0, 0.0, -2.0)],
            z_near=[(1.0, 0.0, -1.0, 0.0, 0.0, -1.0)],
        )
        assert pieces == [(0.0, 1.0, "z_near")]

    def test_a_stretch_along_no_edge_is_left_out(self) -> None:
        """Only the first metre runs along an edge; an edge that merely crosses owns nothing."""
        pieces = self._owners(
            (0.0, 0.0, 3.0, 0.0),
            along=[(0.0, 0.0, -1.0, 1.0, 0.0, -1.0)],
            across=[(2.0, -1.0, -0.5, 2.0, 1.0, -0.5)],
        )
        assert pieces == [(0.0, round(1 / 3, 6), "along")]

    def test_a_segment_too_short_for_a_direction_goes_to_the_edge_through_it(self) -> None:
        """Four millimetres: the edge through its midpoint, not a nearer one beside it."""
        pieces = self._owners(
            (0.0, 0.0, 0.004, 0.0),
            through=[(0.002, -1.0, -1.0, 0.002, 1.0, -1.0)],
            beside=[(0.02, -1.0, -0.5, 0.02, 1.0, -0.5)],
        )
        assert pieces == [(0.0, 1.0, "through")]

    def test_no_edges_no_owners(self) -> None:
        """Every line is returned, with no pieces."""
        import numpy as np

        from plannotation.export.ifc_svg_pdf import line_owners

        lines = np.array([(0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0)], dtype=np.float64)
        assert line_owners(lines, {}) == [[], []]


def _projection_lines(svg: str, page_height_mm: float) -> Any:  # noqa: ANN401
    """Return the lines a sheet draws beyond its view's cut, as paper segments.

    Read from the sheet itself -- the view group's translation and its projection
    group -- not from the exporter's own bookkeeping.

    Args:
        svg: The composed sheet.
        page_height_mm: The page height, for the y-flip.

    Returns:
        One row ``x0, y0, x1, y1`` per segment, in paper millimetres.
    """
    import re

    from plannotation.export.drafting import segments_of
    from plannotation.export.geometry import path_points

    view = svg.index('class="section"')
    wrapper = svg.rindex('<g transform="translate(', 0, view)
    found = re.match(r'<g transform="translate\(([-\d.]+),([-\d.]+)\)"', svg[wrapper:])
    assert found is not None
    offset = (float(found.group(1)), float(found.group(2)))
    start = svg.index('<g class="projection"', view)
    body = svg[start : svg.index("</g>", start)]
    return segments_of(
        [
            path_points(d, page_height_mm=page_height_mm, offset=offset)
            for d in re.findall(r'\bd="([^"]+)"', body)
        ]
    )


def _distance_to(point: tuple[float, float], segments: Any) -> float:  # noqa: ANN401
    """Return how far a point lies from the nearest of some segments.

    Args:
        point: The point.
        segments: ``x0, y0, x1, y1`` rows.

    Returns:
        The distance.
    """
    import numpy as np

    x0, y0, x1, y1 = segments.T
    dx, dy = x1 - x0, y1 - y0
    span = np.where(dx * dx + dy * dy > 0, dx * dx + dy * dy, 1.0)
    share = np.clip(((point[0] - x0) * dx + (point[1] - y0) * dy) / span, 0.0, 1.0)
    return float(np.hypot(x0 + share * dx - point[0], y0 + share * dy - point[1]).min())


def _drawn_section(tmp_path: Path) -> tuple[BuiltModel, ExportedSheet]:
    """Draw section A-A through the raised house, looking south, with the box behind it.

    Args:
        tmp_path: Where to build the model.

    Returns:
        The model as drawn, and the exported sheet.
    """
    from dataclasses import replace

    from plannotation.export.ifc_svg_pdf import export_sheet
    from plannotation.export.views import (
        building_products,
        grid_axes_on,
        grid_lines,
        open_model,
        section_between,
        storey_levels,
    )

    built = build_raised(tmp_path / "raised.ifc", roof=True, behind=True)
    model = open_model(built.path)
    lines = grid_lines(model)
    cut = section_between(lines, first="A", second="B", looking_to="A", datum=RAISED_Z)
    built = replace(
        built,
        section=cut,
        cut_height=None,
        levels=storey_levels(model),
        include=building_products(model),
    )
    spec = _spec(
        sheet_id="X-301",
        title="Section A-A",
        drawing_type="section",
        grids=grid_axes_on(lines, cut),
        presentation=True,
        page_size="A2",
    )
    return built, export_sheet(built, spec, generator_version="0.0.0-test")


@needs_ifc
class TestWhatAViewSeesBeyondItsCut:
    """Beyond a section's cut, and below a plan's, each drawn line is some product's edge."""

    def test_a_section_describes_the_wall_and_door_it_looks_at(self, tmp_path: Path) -> None:
        """The south wall and its door, as projection, on lines the sheet really draws."""
        from plannotation.export.sheet import PAPER_SIZES

        _, exported = _drawn_section(tmp_path)
        seen = {
            e.name: e
            for e in exported.plannotation.elements or []
            if e.representation == "projection"
        }
        assert sorted(seen) == ["Front door", "South"]
        lines = _projection_lines(exported.svg, PAPER_SIZES["A2"][1])
        for element in seen.values():
            assert element.paper_outlines
            for outline in element.paper_outlines:
                (ax, ay), (bx, by) = outline[0], outline[-1]
                for point in (outline[0], ((ax + bx) / 2, (ay + by) / 2), outline[-1]):
                    assert _distance_to(point, lines) <= 0.05

    def test_the_cut_walls_stay_cut(self, tmp_path: Path) -> None:
        """East, west and the partition straddle the plane."""
        _, exported = _drawn_section(tmp_path)
        cut = sorted(
            e.name or "" for e in exported.plannotation.elements or [] if e.representation == "cut"
        )
        assert cut == ["East", "Partition", "West"]

    def test_what_the_wall_hides_is_not_described(self, tmp_path: Path) -> None:
        """The box outside the south wall draws no line, so no element stands for it."""
        _, exported = _drawn_section(tmp_path)
        assert "Behind" not in {e.name for e in exported.plannotation.elements or []}

    def test_a_plan_still_describes_what_it_sees_below_its_cut(self, tmp_path: Path) -> None:
        """The same box, a metre high, lies below a plan cut at 1.2 m: it is seen from above."""
        _, exported = _drawn_plan(tmp_path, behind=True)
        (box,) = [e for e in exported.plannotation.elements or [] if e.name == "Behind"]
        assert box.representation == "projection"
        x0, y0, x1, y1 = box.paper_bbox
        # 1.0 by 0.2 metres at 1:50.
        assert (x1 - x0, y1 - y0) == (pytest.approx(20.0, abs=0.05), pytest.approx(4.0, abs=0.05))

    @needs_cairo
    def test_the_section_validates(self, tmp_path: Path) -> None:
        """Written out, not even a warning."""
        from plannotation.export.ifc_svg_pdf import write_sample
        from plannotation.validate import validate

        built, exported = _drawn_section(tmp_path)
        pdf = write_sample(exported, built, tmp_path / "out", mod_date=MOD_DATE)
        assert [finding.code for finding in validate(pdf).findings] == []


@needs_ifc
@needs_cairo
class TestTheDrawnPlanValidates:
    """The full treatment still writes a plannotation the validator accepts."""

    def test_square_to_the_world_there_are_no_findings(self, tmp_path: Path) -> None:
        """Not even a warning."""
        from plannotation.export.ifc_svg_pdf import write_sample
        from plannotation.validate import validate

        built, exported = _drawn_plan(tmp_path)
        pdf = write_sample(exported, built, tmp_path / "out", mod_date=MOD_DATE)
        assert [finding.code for finding in validate(pdf).findings] == []

    def test_turned_only_the_rounded_axes_are_reported(self, tmp_path: Path) -> None:
        """PL-GEO-010 until the validator allows for three-decimal rounding."""
        from plannotation.export.ifc_svg_pdf import write_sample
        from plannotation.validate import validate

        built, exported = _drawn_plan(tmp_path, rotation=30.0)
        pdf = write_sample(exported, built, tmp_path / "out", mod_date=MOD_DATE)
        assert {finding.code for finding in validate(pdf).findings} <= {"PL-GEO-010"}
