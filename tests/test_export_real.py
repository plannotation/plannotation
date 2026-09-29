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
    from plannotation.model import Annotation, Element, Viewport

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
    beside: bool = False,
    millimetres: bool = False,
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
        beside: Add a metre cube outside the east wall at its south end, which a
            section looking south sees beside the house.
        millimetres: State the model's lengths in millimetres, as models from practice
            do. The house is the same: placements, extrusions and the grid are given
            to ``ifcopenshell.api`` in metres, which converts them, and what the model
            holds in its own unit -- the door's size and the roof storey's
            ``Elevation`` -- is written in millimetres.

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
    # How many of the model's length unit make a metre.
    unit = _in_millimetres(model) if millimetres else 1.0
    building = model.by_type("IfcBuilding")[0]
    raised = np.eye(4)
    raised[2, 3] = RAISED_Z
    ifcopenshell.api.geometry.edit_object_placement(
        model, product=storey, matrix=raised, is_si=True
    )
    storey.Name, storey.Elevation = "Ground", 0.0
    if roof:
        upper = models._storey(model, building, "Roof", RAISED_Z + 2.8)
        upper.Elevation = 2.8 * unit

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
        width=1.0 * unit,
        height=2.1 * unit,
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

    for name, wanted, (x, y), size in (
        ("Behind", behind, (4.0, -1.2), (1.0, 0.2, 1.0)),
        ("Beside", beside, (6.0, 0.25), (1.0, 1.0, 1.0)),
    ):
        if wanted:
            models._box(
                model,
                body,
                storey,
                "IfcBuildingElementProxy",
                name,
                at=(*world(x, y), RAISED_Z),
                size=size,
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


def _in_millimetres(model: Any) -> float:  # noqa: ANN401 - ifcopenshell is untyped here
    """State a model's lengths in millimetres rather than the metres it was set up in.

    Args:
        model: The ``ifcopenshell.file``, before anything is placed in it.

    Returns:
        How many of its length unit make a metre.
    """
    import ifcopenshell.api.unit
    import ifcopenshell.util.unit

    length = ifcopenshell.util.unit.get_project_unit(model, "LENGTHUNIT")
    ifcopenshell.api.unit.edit_named_unit(model, unit=length, attributes={"Prefix": "MILLI"})
    assert ifcopenshell.util.unit.calculate_unit_scale(model) == pytest.approx(0.001)
    return 1000.0


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


@needs_ifc
class TestTitleFields:
    """A title block of labelled fields prints each one inside its border, and nothing else."""

    #: The source credit: long enough to wrap, as Maleva 18's attribution does.
    CREDIT = (
        "Raised House, a two-room model built through ifcopenshell.api for Plannotation's "
        "own tests, its ground floor standing 10 m above the model's origin. Drawn and "
        "annotated from the model by the exporter under test; not an issued drawing, and "
        "nothing is to be built from it."
    )

    @classmethod
    def _export(cls, tmp_path: Path) -> tuple[Any, ExportedSheet]:
        """Draw the raised house with fields laid out as the examples lay out Maleva 18's.

        Args:
            tmp_path: Where to build the model.

        Returns:
            The sheet spec, and the exported sheet.
        """
        from plannotation.export.ifc_svg_pdf import TitleField, export_sheet
        from plannotation.model import Project

        spec = _spec(
            title="Ground floor plan",
            project=Project(name="Raised House, lot 7", number="RH-07"),
            title_block_mm=(180.0, 64.0),
            title_fields=(
                TitleField("PROJECT", "Raised House, lot 7"),
                TitleField("ADDRESS", "No street: a model built through ifcopenshell.api"),
                TitleField("STAGE", "Test sheet, not for construction"),
                TitleField("DRAWING", "Ground floor plan"),
                TitleField("SOURCE", cls.CREDIT),
                TitleField("SHEET", "X-101"),
                TitleField("SCALE", "1:50"),
            ),
        )
        built = build_raised(tmp_path / "raised.ifc")
        return spec, export_sheet(built, spec, generator_version="0.0.0-test")

    @staticmethod
    def _printed(
        exported: ExportedSheet, block: tuple[float, float, float, float]
    ) -> list[tuple[str, tuple[float, float, float, float]]]:
        """Return each line of text the sheet anchors inside a box, with the box it prints in.

        Read from the SVG's own ``<text>`` elements. A line's box runs from its descender
        to its cap height, as ``drafting.label_boxes`` measures a label, and is as wide as
        ``sheet.text_width`` says.

        Args:
            exported: The exported sheet.
            block: The paper box, ``x0, y0, x1, y1``.

        Returns:
            The lines in the order the sheet writes them, each with its paper box.
        """
        import html
        import re

        from plannotation.export.sheet import text_width

        height = exported.plannotation.page.height_mm
        printed = []
        for x, y, size, bold, anchor, value in re.findall(
            r'<text x="([-\d.]+)" y="([-\d.]+)" font-family="Helvetica" '
            r'font-size="([\d.]+)"( font-weight="bold")? text-anchor="(\w+)" fill="#000">'
            r"([^<]*)</text>",
            exported.svg,
        ):
            text, points = html.unescape(value), float(size)
            width = text_width(text, points, bold=bool(bold))
            left = float(x) - {"start": 0.0, "middle": width / 2.0, "end": width}[anchor]
            baseline = height - float(y)
            if block[0] <= float(x) <= block[2] and block[1] <= baseline <= block[3]:
                box = (left, baseline - 0.22 * points, left + width, baseline + 0.76 * points)
                printed.append((text, box))
        return printed

    def test_every_field_prints_inside_the_title_block(self, tmp_path: Path) -> None:
        """Label then value, field by field, the credit wrapped, nothing across the border."""
        from plannotation.export.sheet import title_block_box

        spec, exported = self._export(tmp_path)
        page = exported.plannotation.page
        block = title_block_box(page.width_mm, page.height_mm, spec.title_block_mm)
        printed = self._printed(exported, block)
        texts = [text for text, _ in printed]
        labels = [field.label for field in spec.title_fields]
        starts = [texts.index(label) for label in labels]
        assert starts[0] == 0
        assert starts == sorted(starts)
        lines = {
            label: texts[start + 1 : end]
            for label, (start, end) in zip(labels, pairwise([*starts, len(texts)]), strict=True)
        }
        assert {label: " ".join(value) for label, value in lines.items()} == {
            field.label: field.value for field in spec.title_fields
        }
        assert len(lines["SOURCE"]) > 1
        for text, (x0, y0, x1, y1) in printed:
            assert block[0] < x0 <= x1 < block[2], text
            assert block[1] < y0 <= y1 < block[3], text

    def test_a_wrapped_value_pushes_the_next_field_down(self, tmp_path: Path) -> None:
        """No printed line overlaps another, and each field ends above the next one's label.

        The last two fields share the bottom row, side by side; every field above that row
        is checked against the label below it.
        """
        from itertools import combinations

        from plannotation.export.sheet import title_block_box

        spec, exported = self._export(tmp_path)
        page = exported.plannotation.page
        block = title_block_box(page.width_mm, page.height_mm, spec.title_block_mm)
        printed = self._printed(exported, block)
        for (text, (ax0, ay0, ax1, ay1)), (other, (bx0, by0, bx1, by1)) in combinations(printed, 2):
            assert not (ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1), (text, other)
        texts = [text for text, _ in printed]
        labels = [field.label for field in spec.title_fields]
        for label, following in pairwise(labels[:-1]):
            below = texts.index(following)
            assert texts.index(label) < below - 1, label
            last_value, next_label = printed[below - 1][1], printed[below][1]
            assert last_value[1] > next_label[3], label

    def test_the_plannotation_states_the_spec_s_title_and_project(self, tmp_path: Path) -> None:
        """The spec's project, not the model's ``Raised House``, and the spec's title."""
        from plannotation.export.sheet import title_block_box
        from plannotation.model import Project

        _, exported = self._export(tmp_path)
        sheet = exported.plannotation.sheet
        assert (sheet.sheet_id, sheet.title) == ("X-101", "Ground floor plan")
        assert sheet.project == Project(name="Raised House, lot 7", number="RH-07")
        page = exported.plannotation.page
        block = title_block_box(page.width_mm, page.height_mm, (180.0, 64.0))
        assert sheet.title_block_bbox == block

    def test_no_sample_s_wording_leaks_onto_the_sheet(self, tmp_path: Path) -> None:
        """Neither printed nor recorded: the samples' project, number, storey or revision."""
        from plannotation.model import canonical_json

        _, exported = self._export(tmp_path)
        written = canonical_json(exported.plannotation)
        for wording in ("Wohnanlage Lindenhof", "2024-118", "Erdgeschoss", "Index A"):
            assert wording not in exported.svg
            assert wording not in written


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
    millimetres: bool = False,
    **changes: object,
) -> tuple[BuiltModel, ExportedSheet]:
    """Draw the raised house's ground floor square to its grid, as the examples draw.

    Args:
        tmp_path: Where to build the model.
        rotation: How far the house is turned, in degrees.
        guid_tags: Fill every wall's ``Tag`` with a GUID.
        behind: Add the low box outside the south wall.
        millimetres: State the model's lengths in millimetres.
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
        tmp_path / "raised.ifc",
        rotation=rotation,
        guid_tags=guid_tags,
        behind=behind,
        millimetres=millimetres,
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


def _cut(exported: ExportedSheet, prefix: str) -> list[Element]:
    """Return the elements whose class starts with a prefix, as the cut draws them.

    A product the plane cuts may also be described by what shows of it beyond the cut,
    as a second element with the same GlobalId.

    Args:
        exported: The exported sheet.
        prefix: Such as ``IfcDoor``.

    Returns:
        The cut elements.
    """
    return [e for e in _by_class(exported, prefix) if e.representation == "cut"]


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
class TestWhatASectionDraws:
    """A section through the building draws every storey's products and their parts."""

    @staticmethod
    def _build(out: Path) -> tuple[Any, dict[str, str]]:
        """Build the raised house with a roof of two layers and a note on its floor.

        On the roof storey an ``IfcRoof`` aggregates two ``IfcBuildingElementPart``
        layers, as Maleva 18's roof does; on the ground storey an ``IfcAnnotation`` stands
        beside the walls, the door, its opening, the rooms and the grid.

        Args:
            out: Where to write the IFC file.

        Returns:
            The model, and the GlobalIds of the roof, its layers and the note by name.
        """
        pytest.importorskip("ifcopenshell")
        import ifcopenshell.api.aggregate
        import ifcopenshell.api.root
        import ifcopenshell.api.spatial

        from plannotation.export import models
        from plannotation.export.views import open_model

        built = build_raised(out, roof=True)
        model = open_model(built.path)
        storeys = {storey.Name: storey for storey in model.by_type("IfcBuildingStorey")}
        (body,) = [
            context
            for context in model.by_type("IfcGeometricRepresentationSubContext")
            if context.ContextIdentifier == "Body"
        ]
        roof = ifcopenshell.api.root.create_entity(model, ifc_class="IfcRoof", name="Roof")
        ifcopenshell.api.spatial.assign_container(
            model, products=[roof], relating_structure=storeys["Roof"]
        )
        for name, bottom in (("Deck", 0.0), ("Insulation", 0.2)):
            part = models._box(
                model,
                body,
                None,
                "IfcBuildingElementPart",
                name,
                at=(0.0, 0.0, RAISED_Z + 2.8 + bottom),
                size=(6.0, 4.0, 0.2),
            )
            ifcopenshell.api.aggregate.assign_object(model, products=[part], relating_object=roof)
        note = ifcopenshell.api.root.create_entity(model, ifc_class="IfcAnnotation", name="Note")
        ifcopenshell.api.spatial.assign_container(
            model, products=[note], relating_structure=storeys["Ground"]
        )
        models.reseed_guids(model, "raised")
        models.write_model(model, out)
        written = open_model(out)
        return written, {
            str(product.Name): str(product.GlobalId)
            for product in written.by_type("IfcProduct")
            if product.Name in {"Roof", "Deck", "Insulation", "Note"}
            and not product.is_a("IfcBuildingStorey")
        }

    def test_every_storey_s_products_and_their_parts(self, tmp_path: Path) -> None:
        """The ground floor's walls and door, the roof on the storey above, and its layers."""
        from plannotation.export.views import building_products

        model, named = self._build(tmp_path / "raised.ifc")
        drawn = building_products(model)
        classes = sorted(model.by_guid(guid).is_a() for guid in drawn)
        assert classes == [
            "IfcBuildingElementPart",
            "IfcBuildingElementPart",
            "IfcDoor",
            "IfcRoof",
            "IfcWall",
            "IfcWall",
            "IfcWall",
            "IfcWall",
            "IfcWallStandardCase",
        ]
        assert {named["Roof"], named["Deck"], named["Insulation"]} <= set(drawn)

    def test_spaces_only_when_asked(self, tmp_path: Path) -> None:
        """A section usually leaves the rooms out; asked, it has both."""
        from plannotation.export.views import building_products

        model, _ = self._build(tmp_path / "raised.ifc")
        without, with_spaces = building_products(model), building_products(model, spaces=True)
        assert not any(model.by_guid(guid).is_a("IfcSpace") for guid in without)
        added = sorted(set(with_spaces) - set(without))
        assert [model.by_guid(guid).is_a() for guid in added] == ["IfcSpace", "IfcSpace"]

    def test_never_an_opening_a_grid_or_an_annotation(self, tmp_path: Path) -> None:
        """The model holds each of them; the drawing draws none of them, spaces or not."""
        from plannotation.export.views import building_products

        model, named = self._build(tmp_path / "raised.ifc")
        left_out = {
            str(product.GlobalId)
            for kind in ("IfcOpeningElement", "IfcGrid", "IfcAnnotation")
            for product in model.by_type(kind)
        }
        assert len(left_out) == 3  # the door's opening, the grid, the note
        assert named["Note"] in left_out
        for spaces in (False, True):
            assert not left_out & set(building_products(model, spaces=spaces))

    def test_sorted_and_each_once(self, tmp_path: Path) -> None:
        """A part is held by its roof and reached again through it, but listed once."""
        from plannotation.export.views import building_products

        model, _ = self._build(tmp_path / "raised.ifc")
        for spaces in (False, True):
            drawn = building_products(model, spaces=spaces)
            assert list(drawn) == sorted(set(drawn))


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
        products = {(e.ifc_guid, e.ifc_class) for e in exported.plannotation.elements or []}
        classes = sorted(ifc_class for _, ifc_class in products)
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
        (door,) = _cut(exported, "IfcDoor")
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


def _roofed(out: Path) -> BuiltModel:
    """Build the raised house under a roof that, as Maleva 18's does, is only its parts.

    An ``IfcRoof`` with no body of its own stands on the roof storey and aggregates three
    ``IfcBuildingElementPart`` layers over the whole house: "Slab", 200 mm of concrete
    on the walls; "Insulation", 200 mm of mineral wool on that, a material it has only
    through its ``IfcBuildingElementPartType``; and "Bare", 50 mm with no material at
    all. The roof's ``Pset_RoofCommon`` states that it bears no load, which its class
    overrules when it is drawn.

    Args:
        out: Where to write the IFC file.

    Returns:
        The model, set up as :func:`build_raised` sets it up.
    """
    pytest.importorskip("ifcopenshell")
    import ifcopenshell
    import ifcopenshell.api.aggregate
    import ifcopenshell.api.material
    import ifcopenshell.api.root
    import ifcopenshell.api.spatial
    import ifcopenshell.api.type

    from plannotation.export import models

    built = build_raised(out, roof=True)
    model = ifcopenshell.open(str(out))
    (upper,) = [s for s in model.by_type("IfcBuildingStorey") if s.Name == "Roof"]
    (body,) = [
        context
        for context in model.by_type("IfcGeometricRepresentationSubContext")
        if context.ContextIdentifier == "Body"
    ]
    roof = ifcopenshell.api.root.create_entity(model, ifc_class="IfcRoof", name="Roof")
    ifcopenshell.api.spatial.assign_container(model, products=[roof], relating_structure=upper)
    _pset(model, roof, "Pset_RoofCommon", {"LoadBearing": False})
    for name, bottom, thickness, material, typed in (
        ("Slab", 0.0, 0.2, "Concrete C30/37", False),
        ("Insulation", 0.2, 0.2, "Mineral wool", True),
        ("Bare", 0.4, 0.05, None, False),
    ):
        part = models._box(
            model,
            body,
            None,
            "IfcBuildingElementPart",
            name,
            at=(0.0, 0.0, RAISED_Z + 2.8 + bottom),
            size=(6.0, 4.0, thickness),
        )
        ifcopenshell.api.aggregate.assign_object(model, products=[part], relating_object=roof)
        if material is None:
            continue
        made_of = ifcopenshell.api.material.add_material(model, name=material)
        holder = part
        if typed:
            holder = ifcopenshell.api.root.create_entity(
                model, ifc_class="IfcBuildingElementPartType", name=name
            )
            ifcopenshell.api.type.assign_type(model, related_objects=[part], relating_type=holder)
        ifcopenshell.api.material.assign_material(model, products=[holder], material=made_of)
    models.reseed_guids(model, "raised")
    models.write_model(model, out)
    return built


def _drawn_roof(tmp_path: Path, **changes: object) -> ExportedSheet:
    """Draw section A-A through the roofed house, its parts styled as Maleva 18's are.

    Args:
        tmp_path: Where to build the model.
        **changes: Sheet spec fields to change.

    Returns:
        The exported sheet.
    """
    from dataclasses import replace

    from plannotation.export.examples import M18_MATERIAL_STYLES
    from plannotation.export.ifc_svg_pdf import export_sheet
    from plannotation.export.views import (
        building_products,
        grid_axes_on,
        grid_lines,
        open_model,
        section_between,
        storey_levels,
    )

    built = _roofed(tmp_path / "roofed.ifc")
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
        material_styles=M18_MATERIAL_STYLES,
        page_size="A2",
    )
    return export_sheet(built, replace(spec, **changes), generator_version="0.0.0-test")


@needs_ifc
class TestARoofIsDrawnAsItsLayersAreMade:
    """A roof's parts are drawn by their material, and a bare one as the roof itself."""

    @staticmethod
    def _fills(exported: ExportedSheet) -> dict[str | None, str]:
        """Return each part's fill on the sheet, by name.

        Args:
            exported: The exported sheet.

        Returns:
            The fill each part's group is given.
        """
        import re

        fills = {}
        for part in _by_class(exported, "IfcBuildingElementPart"):
            assert part.representation == "cut"
            fill = re.search(
                r'\bfill="([^"]+)"', TestThePresentation._group(exported.svg, part.ifc_guid)
            )
            assert fill is not None
            fills[part.name] = fill.group(1)
        return fills

    def test_the_concrete_is_solid_the_insulation_outlined(self, tmp_path: Path) -> None:
        """As the slabs below it are filled, and what bears nothing is not."""
        fills = self._fills(_drawn_roof(tmp_path))
        assert fills == {"Slab": "#000", "Insulation": "none", "Bare": "#000"}

    def test_without_material_styles_a_layer_with_a_material_is_outlined(
        self, tmp_path: Path
    ) -> None:
        """No material is claimed as structure unless the sheet says so."""
        fills = self._fills(_drawn_roof(tmp_path, material_styles=()))
        assert fills == {"Slab": "none", "Insulation": "none", "Bare": "#000"}

    def test_the_facts_hold_each_part_s_materials_and_its_roof(self, tmp_path: Path) -> None:
        """Read from the model, not from the drawing: a type's material, the roof's LoadBearing."""
        import ifcopenshell.util.element

        from plannotation.export.ifc_svg_pdf import read_model_facts
        from plannotation.export.views import open_model

        built = _roofed(tmp_path / "roofed.ifc")
        model = open_model(built.path)
        parts = {part.Name: part for part in model.by_type("IfcBuildingElementPart")}
        # The insulation's material is its type's; the part has none of its own.
        insulation = parts["Insulation"]
        assert ifcopenshell.util.element.get_material(insulation, should_inherit=False) is None
        assert ifcopenshell.util.element.get_type(insulation) is not None
        guids = {name: str(part.GlobalId) for name, part in parts.items()}
        facts = read_model_facts(built.path, include=tuple(guids.values()))
        assert {name: facts.materials.get(guid) for name, guid in guids.items()} == {
            "Slab": ("Concrete C30/37",),
            "Insulation": ("Mineral wool",),
            "Bare": None,
        }
        # As the model states it, though an IfcRoof is drawn as structure whatever it says.
        assert {facts.aggregates[guid] for guid in guids.values()} == {("IfcRoof", False)}

    def test_materials_are_read_in_the_model_s_order(self) -> None:
        """A layer set's layers as they are stacked; a layer associated alone, its own."""
        import ifcopenshell
        import ifcopenshell.api.material
        import ifcopenshell.api.root
        import ifcopenshell.guid

        from plannotation.export.ifc_svg_pdf import _material_names

        model = ifcopenshell.file(schema="IFC4")
        wall = ifcopenshell.api.root.create_entity(model, ifc_class="IfcWall")
        layers = ifcopenshell.api.material.add_material_set(
            model, name="Wall", set_type="IfcMaterialLayerSet"
        )
        made = {
            name: ifcopenshell.api.material.add_material(model, name=name)
            for name in ("Plaster", "Concrete", "Mineral wool")
        }
        for material in made.values():
            ifcopenshell.api.material.add_layer(model, layer_set=layers, material=material)
        ifcopenshell.api.material.assign_material(
            model, products=[wall], type="IfcMaterialLayerSetUsage", material=layers
        )
        assert _material_names(wall) == ("Plaster", "Concrete", "Mineral wool")

        part = ifcopenshell.api.root.create_entity(model, ifc_class="IfcBuildingElementPart")
        layer = model.createIfcMaterialLayer(made["Mineral wool"], 0.1)
        model.createIfcRelAssociatesMaterial(
            ifcopenshell.guid.new(), None, None, None, [part], layer
        )
        assert _material_names(part) == ("Mineral wool",)


class TestMaterialStylesAreCheckedWhenTheSpecIsMade:
    """A mistyped style or pattern is refused at once, not when a part first matches it."""

    def test_none_by_default(self) -> None:
        """No material is drawn as structure unless the sheet says so."""
        assert _spec().material_styles == ()

    @pytest.mark.parametrize(
        ("styles", "complaint"),
        [
            (((r"(?i)betoon", "Solid"),), "'Solid' is no style"),
            (((r"(?i)(betoon", "solid"),), "is no regular expression"),
        ],
    )
    def test_a_bad_entry_is_refused(
        self, styles: tuple[tuple[str, str], ...], complaint: str
    ) -> None:
        """Naming the entry that is wrong."""
        import re

        from plannotation.errors import ExportError

        with pytest.raises(ExportError, match=re.escape(complaint)):
            _spec(material_styles=styles)


#: Concrete first, then wool; and the other way round.
_CONCRETE_FIRST = ((r"(?i)concrete", "solid"), (r"(?i)wool", "partition"))
_WOOL_FIRST = tuple(reversed(_CONCRETE_FIRST))


class TestAPartIsDrawnAsItIsMade:
    """The rule: a matching material first, then the whole it is part of, then its class."""

    @pytest.mark.parametrize(
        ("ifc_class", "materials", "aggregate", "material_styles", "style"),
        [
            # The first expression that any material matches, not the first material.
            ("IfcBuildingElementPart", ("Wool", "Concrete"), None, _CONCRETE_FIRST, "solid"),
            ("IfcBuildingElementPart", ("Wool", "Concrete"), None, _WOOL_FIRST, "partition"),
            # Materials that match nothing: the class's style, whatever the whole is.
            ("IfcBuildingElementPart", ("Glass",), ("IfcRoof", None), _CONCRETE_FIRST, "outline"),
            # No material: the whole's style, from its class and its LoadBearing.
            ("IfcBuildingElementPart", (), ("IfcRoof", None), _CONCRETE_FIRST, "solid"),
            ("IfcBuildingElementPart", (), ("IfcWall", False), _CONCRETE_FIRST, "partition"),
            ("IfcBuildingElementPart", (), ("IfcWall", True), (), "solid"),
            # Neither: the class's style.
            ("IfcBuildingElementPart", (), None, _CONCRETE_FIRST, "outline"),
            # Not a part: its class decides, as it always has.
            ("IfcWall", ("Concrete",), ("IfcRoof", None), _CONCRETE_FIRST, "partition"),
            ("IfcDoor", ("Concrete",), None, _CONCRETE_FIRST, "outline"),
        ],
    )
    def test_style_of(
        self,
        ifc_class: str,
        materials: tuple[str, ...],
        aggregate: tuple[str, bool | None] | None,
        material_styles: tuple[tuple[str, str], ...],
        style: str,
    ) -> None:
        """Each case of the rule."""
        from plannotation.export.drafting import style_of

        assert (
            style_of(
                ifc_class,
                load_bearing=None,
                is_a=lambda cls, ancestor: cls == ancestor,
                materials=materials,
                aggregate=aggregate,
                material_styles=material_styles,
            )
            == style
        )

    @pytest.mark.parametrize(
        ("material", "style"),
        [
            ("Raudbetoon - Konstruktsioon", "solid"),
            ("Soojustus- SPU", "outline"),
            ("Soojustus - vill kõva", "outline"),
            ("Membraan - vihmakindel katus", "outline"),
        ],
    )
    def test_maleva_18_s_roof_layers_by_their_estonian_names(
        self, material: str, style: str
    ) -> None:
        """The reinforced concrete is structure; the insulation and the membrane are not."""
        from plannotation.export.drafting import style_of
        from plannotation.export.examples import M18_MATERIAL_STYLES

        assert (
            style_of(
                "IfcBuildingElementPart",
                load_bearing=None,
                is_a=lambda cls, ancestor: cls == ancestor,
                materials=(material,),
                aggregate=("IfcRoof", None),
                material_styles=M18_MATERIAL_STYLES,
            )
            == style
        )


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
        (door,) = _cut(exported, "IfcDoor")
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
class TestIfc2x3DoorStyles:
    """In IFC2X3 a door has no operation type of its own: its ``IfcDoorStyle`` carries it."""

    @staticmethod
    def _build(out: Path) -> BuiltModel:
        """Build an IFC2X3 wall with two doors in it, each typed by a door style.

        An 8 m wall 250 mm thick runs along x. At x = 1 m stands a 2 m double door whose
        style lists a right panel of 0.4 and a left panel of 0.6 of its width; at x = 5 m
        a 1.2 m door whose style says it slides. IFC2X3 makes every rooted entity record
        who owns it, so the model has an owner.

        Args:
            out: Where to write the IFC file.

        Returns:
            The model, set up for a plan cut 1.2 m above its floor, along the model axes.
        """
        pytest.importorskip("ifcopenshell")
        import ifcopenshell
        import ifcopenshell.api.owner
        import ifcopenshell.api.root
        import ifcopenshell.api.type

        from plannotation.export import models
        from plannotation.export.views import find_storey, open_model, storey_products

        model = ifcopenshell.file(schema="IFC2X3")
        person = ifcopenshell.api.owner.add_person(
            model, identification="tests", family_name="Tests", given_name="Plannotation"
        )
        organisation = ifcopenshell.api.owner.add_organisation(
            model, identification="PLN", name="Plannotation"
        )
        ifcopenshell.api.owner.add_person_and_organisation(
            model, person=person, organisation=organisation
        )
        ifcopenshell.api.owner.add_application(model)
        body, storey = models._setup(model, "Door Styles")
        storey.Name = "Ground"
        wall = models._box(
            model,
            body,
            storey,
            "IfcWallStandardCase",
            "Wall",
            at=(0.0, 0.0, 0.0),
            size=(8.0, 0.25, 2.8),
        )
        for name, x, width, operation, panels in (
            ("Double", 1.0, 2.0, "DOUBLE_DOOR_SINGLE_SWING", (("RIGHT", 0.4), ("LEFT", 0.6))),
            ("Sliding", 5.0, 1.2, "SLIDING_TO_LEFT", ()),
        ):
            door = models._filling(
                model,
                body,
                storey,
                "IfcDoor",
                name,
                at=(x, 0.0, 0.0),
                width=width,
                height=2.1,
                rotation=0.0,
            )
            models._opening(
                model, body, wall, door, at=(x, -0.01, 0.0), size=(width, 0.27, 2.1), rotation=0.0
            )
            style = ifcopenshell.api.root.create_entity(model, ifc_class="IfcDoorStyle", name=name)
            style.OperationType = operation
            held = []
            for position, share in panels:
                panel = ifcopenshell.api.root.create_entity(
                    model, ifc_class="IfcDoorPanelProperties", name=f"{position} panel"
                )
                panel.PanelOperation, panel.PanelPosition, panel.PanelWidth = (
                    "SWINGING",
                    position,
                    share,
                )
                held.append(panel)
            style.HasPropertySets = tuple(held) or None
            ifcopenshell.api.type.assign_type(model, related_objects=[door], relating_type=style)
        models.reseed_guids(model, "door-styles")
        models.write_model(model, out)
        written = open_model(out)
        ground = find_storey(written, "Ground")
        return models.BuiltModel(
            path=out,
            seed="door-styles",
            storey_elevation=ground.z,
            cut_height=1.2,
            section=models.SectionCut(
                location=(0.0, 0.0, 1.2), direction=(0.0, 0.0, 1.0), x_axis=(1.0, 0.0, 0.0)
            ),
            storey_name=ground.name,
            storey_guid=ground.guid,
            include=storey_products(written, ground),
        )

    @staticmethod
    def _doors(built: BuiltModel) -> tuple[Any, dict[str, Any]]:
        """Open the model and find its doors.

        Args:
            built: The model.

        Returns:
            The ``ifcopenshell.file``, and its doors by name.
        """
        from plannotation.export.views import open_model

        model = open_model(built.path)
        return model, {door.Name: door for door in model.by_type("IfcDoor")}

    def test_the_operation_type_is_read_from_the_style(self, tmp_path: Path) -> None:
        """The door has no ``OperationType`` to read; its style has."""
        from plannotation.export.doors import operation_type

        model, doors = self._doors(self._build(tmp_path / "doors.ifc"))
        assert model.schema == "IFC2X3"
        assert all("OperationType" not in door.get_info() for door in doors.values())
        assert {name: operation_type(door) for name, door in doors.items()} == {
            "Double": "DOUBLE_DOOR_SINGLE_SWING",
            "Sliding": "SLIDING_TO_LEFT",
        }

    def test_a_double_door_opens_two_leaves_split_as_its_left_panel_says(
        self, tmp_path: Path
    ) -> None:
        """PanelWidth 0.6 on the left panel: leaves of 1.2 and 0.8 m, meeting at x = 2.2 m."""
        from plannotation.export.doors import door_swings

        model, doors = self._doors(self._build(tmp_path / "doors.ifc"))
        guid = doors["Double"].GlobalId
        swings, reasons = door_swings(model, [guid])
        assert reasons == {}
        leaves, arcs = swings[guid].leaves, swings[guid].arcs
        # Hinged at both jambs, on the wall's face at y = 0.25 m, and open square to it.
        assert [c for leaf in leaves for c in leaf[0]] == pytest.approx([1.0, 0.25, 3.0, 0.25])
        assert [end[1] - start[1] for start, end in leaves] == pytest.approx([1.2, 0.8])
        assert [c for arc in arcs for c in arc[-1]] == pytest.approx([2.2, 0.25, 2.2, 0.25])

    def test_a_sliding_door_gets_no_swing(self, tmp_path: Path) -> None:
        """Its style says it slides; an arc would say it swings."""
        from plannotation.export.doors import door_swings

        model, doors = self._doors(self._build(tmp_path / "doors.ifc"))
        guid = doors["Sliding"].GlobalId
        swings, reasons = door_swings(model, [guid])
        assert swings == {}
        assert reasons == {guid: "operation type SLIDING_TO_LEFT does not swing"}

    def test_the_plan_draws_the_double_door_open_and_the_sliding_door_shut(
        self, tmp_path: Path
    ) -> None:
        """At 1:50 the double door gains leaves of 24 and 16 mm; the sliding door gains none."""
        from plannotation.export.ifc_svg_pdf import export_sheet

        built = self._build(tmp_path / "doors.ifc")
        shut, drawn = (
            {
                door.name: door.paper_outlines or []
                for door in _cut(
                    export_sheet(built, _spec(door_swings=swings), generator_version="0.0.0-test"),
                    "IfcDoor",
                )
            }
            for swings in (False, True)
        )
        assert drawn["Sliding"] == shut["Sliding"]
        assert drawn["Double"][: len(shut["Double"])] == shut["Double"]
        added = drawn["Double"][len(shut["Double"]) :]
        assert len(added) == 4
        assert [math.dist(line[0], line[-1]) for line in added[:2]] == pytest.approx(
            [24.0, 16.0], abs=0.01
        )


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

    def test_a_face_drawn_from_both_sides_has_no_diagonal(self) -> None:
        """Archicad writes doors and windows with each face twice, once each way round."""
        import numpy as np

        from plannotation.export.ifc_svg_pdf import edges_beyond

        square = [(0.0, 0.0, -1.0), (1.0, 0.0, -1.0), (1.0, 1.0, -1.0), (0.0, 1.0, -1.0)]
        # The front two triangles, then the same two turned over, all four sharing the
        # diagonal from the first corner to the third.
        faces = [(0, 1, 2), (0, 2, 3), (0, 2, 1), (0, 3, 2)]
        rows = edges_beyond(np.array(square), np.array(faces))
        assert _edge_set(rows) == {
            _edge(square[0], square[1]),
            _edge(square[1], square[2]),
            _edge(square[2], square[3]),
            _edge(square[3], square[0]),
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
        """Eight millimetres: the edge through its midpoint, not a nearer one beside it.

        The nearer edge passes 7 mm from the midpoint, inside the box the segment is
        searched in but outside the tolerance, so only the rule decides.
        """
        pieces = self._owners(
            (0.0, 0.0, 0.008, 0.0),
            through=[(0.004, -1.0, -1.0, 0.004, 1.0, -1.0)],
            beside=[(0.011, -1.0, -0.5, 0.011, 1.0, -0.5)],
        )
        assert pieces == [(0.0, 1.0, "through")]

    def test_an_edge_seen_end_on_owns_no_short_segment(self) -> None:
        """It draws nothing on the paper, whichever of its ends comes first."""
        for corner in ((0.004, 0.0, -2.0, 0.004, 0.0, 0.0), (0.004, 0.0, 0.0, 0.004, 0.0, -2.0)):
            pieces = self._owners(
                (0.0, 0.0, 0.008, 0.0),
                drawn=[(-1.0, 0.0, -1.0, 1.0, 0.0, -1.0)],
                corner=[corner],
            )
            assert pieces == [(0.0, 1.0, "drawn")]

    def test_a_segment_no_longer_than_the_tolerance_is_nobody_s(self) -> None:
        """Four millimetres cannot say whose edge they are."""
        pieces = self._owners(
            (0.0, 0.0, 0.004, 0.0), through=[(0.002, -1.0, -1.0, 0.002, 1.0, -1.0)]
        )
        assert pieces == []

    def test_a_hidden_edge_gets_no_sliver_where_the_nearer_one_stops_short(self) -> None:
        """A wall's edge ending 0.04 mm before the drawn line does: the wall behind gets nothing.

        Paper points are rounded to 0.001 mm and model points welded to 0.1 mm, so a
        line and the edge it draws seldom end at exactly the same place.
        """
        pieces = self._owners(
            (0.0, 0.0, 7.5, 0.0),
            near=[(0.0, 0.0, -1.0, 7.49996, 0.0, -1.0)],
            far=[(0.0, 0.0, -2.0, 7.6, 0.0, -2.0)],
        )
        assert [owner for _, _, owner in pieces] == ["near"]
        assert pieces[0][1] == pytest.approx(1.0, abs=1e-5)

    def test_an_edge_that_leaves_the_line_owns_none_of_it(self) -> None:
        """Nearer, and starting on the line, but only one of its ends is on it."""
        pieces = self._owners(
            (0.0, 0.0, 3.0, 0.0),
            along=[(0.0, 0.0, -1.0, 3.0, 0.0, -1.0)],
            slant=[(1.0, 0.0, -0.5, 2.0, 1.0, -0.5)],
        )
        assert pieces == [(0.0, 1.0, "along")]

    def test_a_short_piece_of_a_long_slope_is_the_slope_s(self) -> None:
        """A 20 mm piece of a 10 m roof slope, its ends rounded as a sheet rounds them.

        The piece's direction is off the slope's by rounding, which tilts its line
        millimetres away from the slope's far ends; along the piece itself they agree.
        A short collinear edge two metres further back does not take it.
        """
        slope = math.radians(30.0)

        def on(x: float) -> tuple[float, float]:
            return (x, x * math.tan(slope))

        pieces = self._owners(
            (3.5065, 2.0245, 3.5239, 2.0345),
            roof=[(0.0, 0.0, -1.0, 8.660, 5.0, -1.0)],
            behind=[(*on(3.4), -3.0, *on(3.7), -3.0)],
        )
        assert pieces == [(0.0, 1.0, "roof")]

    def test_a_tie_goes_to_the_product_the_plane_cuts(self) -> None:
        """Equally near: the cut product's face hides the other, whichever name sorts first.

        The serializer does not let a cut face hide anything, so the far edge of a wall
        the plane cuts and the edge of a wall behind it can coincide exactly.
        """
        import numpy as np

        from plannotation.export.ifc_svg_pdf import line_owners

        line = np.array([(0.0, 0.0, 1.0, 0.0)])
        edges = {
            "a_hidden": np.array([(0.0, 0.0, -2.0, 1.0, 0.0, -2.0)]),
            "z_cut": np.array([(0.0, 0.0, -2.0, 1.0, 0.0, -2.0)]),
        }
        assert line_owners(line, edges) == [[(0.0, 1.0, "a_hidden")]]
        assert line_owners(line, edges, prefer={"z_cut"}) == [[(0.0, 1.0, "z_cut")]]

    def test_no_edges_no_owners(self) -> None:
        """Every line is returned, with no pieces."""
        import numpy as np

        from plannotation.export.ifc_svg_pdf import line_owners

        lines = np.array([(0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0)], dtype=np.float64)
        assert line_owners(lines, {}) == [[], []]


class TestTheEdgeIndex:
    """Looking up only the edges near a line finds every edge the full search would."""

    def test_it_misses_no_edge_whose_box_meets_the_query(self) -> None:
        """On seeded random boxes, long and short, some off the grid: a superset, in order."""
        import numpy as np

        from plannotation.export.ifc_svg_pdf import _edge_index

        rng = np.random.default_rng(18)
        corner = rng.uniform(-5.0, 60.0, size=(3000, 2))
        size = rng.exponential(0.5, size=(3000, 2)) * rng.choice(
            [1.0, 40.0], size=(3000, 1), p=[0.95, 0.05]
        )
        boxes = np.hstack([corner, corner + size])
        index = _edge_index(boxes)
        for query in rng.uniform(-20.0, 80.0, size=(300, 4)):
            box = (
                min(query[0], query[2]),
                min(query[1], query[3]),
                max(query[0], query[2]),
                max(query[1], query[3]),
            )
            meets = np.flatnonzero(
                (boxes[:, 0] <= box[2])
                & (box[0] <= boxes[:, 2])
                & (boxes[:, 1] <= box[3])
                & (box[1] <= boxes[:, 3])
            )
            found = index.near(box)
            assert set(meets) <= set(found)
            assert list(found) == sorted(set(found))

    def test_one_edge_is_its_own_grid(self) -> None:
        """A single point-sized edge still makes an index that finds it."""
        import numpy as np

        from plannotation.export.ifc_svg_pdf import _edge_index

        index = _edge_index(np.array([(1.0, 1.0, 1.0, 1.0)]))
        assert list(index.near((0.0, 0.0, 2.0, 2.0))) == [0]


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
    from plannotation.export.geometry import path_rings

    view = svg.index('class="section"')
    wrapper = svg.rindex('<g transform="translate(', 0, view)
    found = re.match(r'<g transform="translate\(([-\d.]+),([-\d.]+)\)"', svg[wrapper:])
    assert found is not None
    offset = (float(found.group(1)), float(found.group(2)))
    start = svg.index('<g class="projection"', view)
    body = svg[start : svg.index("</g>", start)]
    return segments_of(
        [
            ring
            for d in re.findall(r'\bd="([^"]+)"', body)
            for ring in path_rings(d, page_height_mm=page_height_mm, offset=offset)
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


def _drawn_section(
    tmp_path: Path, *, beside: bool = False, millimetres: bool = False
) -> tuple[BuiltModel, ExportedSheet]:
    """Draw section A-A through the raised house, looking south, with the box behind it.

    Args:
        tmp_path: Where to build the model.
        beside: Add the cube outside the east wall.
        millimetres: State the model's lengths in millimetres.

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

    built = build_raised(
        tmp_path / "raised.ifc", roof=True, behind=True, beside=beside, millimetres=millimetres
    )
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


#: The raised house in metres, and the same house in millimetres, the unit most models
#: from practice are in. The plane's coordinates are in the model's unit and the edges
#: they are matched with in metres, so a check that passes in one may fail in the other.
_IN_EITHER_UNIT = pytest.mark.parametrize(
    "millimetres", [False, True], ids=["metres", "millimetres"]
)


def _on_paper(viewport: Viewport, point: tuple[float, float, float]) -> tuple[float, float]:
    """Return where a point in metres falls on the paper, from the view's plane and scale.

    Only the paper point of the plane's origin is taken from ``paperToPlane``; the rest
    is the point's distance along the plane's axes, at the viewport's scale.

    Args:
        viewport: The view, in a model whose unit is the metre.
        point: The point in model coordinates.

    Returns:
        The paper point in millimetres.
    """
    from plannotation.export.paper import invert

    assert viewport.plane is not None
    assert viewport.paper_to_plane is not None
    assert viewport.scale is not None
    x0, y0 = invert(viewport.paper_to_plane, 0.0, 0.0)
    relative = [p - o for p, o in zip(point, viewport.plane.origin, strict=True)]
    u = sum(r * a for r, a in zip(relative, viewport.plane.x_axis, strict=True))
    v = sum(r * a for r, a in zip(relative, viewport.plane.y_axis, strict=True))
    mm_per_m = 1000.0 / viewport.scale
    return (x0 + u * mm_per_m, y0 + v * mm_per_m)


def _vertical_stretches(element: Element, x: float) -> list[tuple[float, float]]:
    """Return the stretches of a vertical paper line an element's outlines run along.

    Args:
        element: The element.
        x: The line's paper x.

    Returns:
        Each segment of its outlines that lies on the line, as its lower and upper paper
        y, from the bottom up.
    """
    return sorted(
        (min(a[1], b[1]), max(a[1], b[1]))
        for outline in element.paper_outlines or []
        for a, b in pairwise(outline)
        if abs(a[0] - x) <= 0.01 and abs(b[0] - x) <= 0.01 and a[1] != b[1]
    )


@needs_ifc
class TestWhatAViewSeesBeyondItsCut:
    """Beyond a section's cut, and below a plan's, each drawn line is some product's edge."""

    @_IN_EITHER_UNIT
    def test_a_section_describes_the_wall_and_door_it_looks_at(
        self, tmp_path: Path, *, millimetres: bool
    ) -> None:
        """The south wall and its door, as projection, on lines the sheet really draws."""
        from plannotation.export.sheet import PAPER_SIZES

        _, exported = _drawn_section(tmp_path, millimetres=millimetres)
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

    @_IN_EITHER_UNIT
    def test_the_cut_walls_stay_cut(self, tmp_path: Path, *, millimetres: bool) -> None:
        """East, west and the partition straddle the plane."""
        _, exported = _drawn_section(tmp_path, millimetres=millimetres)
        cut = sorted(
            e.name or "" for e in exported.plannotation.elements or [] if e.representation == "cut"
        )
        assert cut == ["East", "Partition", "West"]

    @_IN_EITHER_UNIT
    def test_what_the_wall_hides_is_not_described(
        self, tmp_path: Path, *, millimetres: bool
    ) -> None:
        """The box outside the south wall draws no line, so no element stands for it."""
        _, exported = _drawn_section(tmp_path, millimetres=millimetres)
        assert "Behind" not in {e.name for e in exported.plannotation.elements or []}

    @_IN_EITHER_UNIT
    def test_a_plan_still_describes_what_it_sees_below_its_cut(
        self, tmp_path: Path, *, millimetres: bool
    ) -> None:
        """The same box, a metre high, lies below a plan cut at 1.2 m: it is seen from above."""
        _, exported = _drawn_plan(tmp_path, behind=True, millimetres=millimetres)
        (box,) = [e for e in exported.plannotation.elements or [] if e.name == "Behind"]
        assert box.representation == "projection"
        x0, y0, x1, y1 = box.paper_bbox
        # 1.0 by 0.2 metres at 1:50.
        assert (x1 - x0, y1 - y0) == (pytest.approx(20.0, abs=0.05), pytest.approx(4.0, abs=0.05))

    def test_a_cut_product_is_also_described_by_what_shows_below_its_cut(
        self, tmp_path: Path
    ) -> None:
        """The door the plan cuts: its cut element, and beside it what of it shows below.

        The second element carries the same GlobalId and only lines off its own cut
        outline; the swing stays with the cut one, and the door is still one door.
        """
        from plannotation.export.drafting import segments_of

        _, exported = _drawn_plan(tmp_path)
        doors = _by_class(exported, "IfcDoor")
        assert sorted(door.representation or "" for door in doors) == ["cut", "projection"]
        (cut,) = _cut(exported, "IfcDoor")
        (seen,) = [door for door in doors if door.representation == "projection"]
        assert seen.ifc_guid == cut.ifc_guid
        own = segments_of(cut.paper_outlines or [])
        for outline in seen.paper_outlines or []:
            (ax, ay), (bx, by) = outline[0], outline[-1]
            assert _distance_to(((ax + bx) / 2, (ay + by) / 2), own) > 0.05
        question = next(q for q in exported.ground_truth if "doors (IfcDoor)" in str(q["question"]))
        assert question["answer"] == 1

    @pytest.mark.parametrize("view", ["section", "plan"])
    def test_in_millimetres_every_element_is_where_it_is_in_metres(
        self, tmp_path: Path, view: str
    ) -> None:
        """The same elements in the same paper boxes: the model's unit moves no line."""

        def boxes(*, millimetres: bool) -> dict[tuple[str | None, str | None], Any]:
            folder = tmp_path / ("millimetres" if millimetres else "metres")
            folder.mkdir()
            if view == "section":
                _, exported = _drawn_section(folder, millimetres=millimetres)
            else:
                _, exported = _drawn_plan(folder, behind=True, millimetres=millimetres)
            return {
                (e.name, e.representation): (
                    e.paper_bbox,
                    [point for outline in e.paper_outlines or [] for point in outline],
                )
                for e in exported.plannotation.elements or []
            }

        metres, millimetres = boxes(millimetres=False), boxes(millimetres=True)
        assert any(representation == "projection" for _, representation in metres)
        assert sorted(millimetres, key=str) == sorted(metres, key=str)
        for key, (box, points) in metres.items():
            assert millimetres[key][0] == pytest.approx(box, abs=0.001), key
            assert len(millimetres[key][1]) == len(points), key
            for got, want in zip(millimetres[key][1], points, strict=True):
                assert got == pytest.approx(want, abs=0.001), key

    def test_a_line_two_products_run_along_is_split_where_their_edges_meet(
        self, tmp_path: Path
    ) -> None:
        """The cube outside the east wall owns the lowest metre of the house's end line.

        Looking south, the section draws one line up the house's east end, where the cut
        passes through the east wall, from the floor to the top of the walls. The south
        wall's end edge runs all the way along it; the cube's corner runs along its lowest
        metre, nearer the viewer, so that metre is the cube's and the 1.8 m above it the
        wall's. Each piece is drawn where it lies on the line, not measured from the
        line's other end.
        """
        _, exported = _drawn_section(tmp_path, beside=True)
        (viewport,) = exported.plannotation.viewports or []
        assert viewport.scale == 50
        elements = {e.name: e for e in exported.plannotation.elements or []}
        cube, wall = elements["Beside"], elements["South"]
        assert (cube.representation, wall.representation) == ("projection", "projection")
        # The cube's face towards the viewer: 1 m square, from x = 6 m, on the floor.
        corners = [
            _on_paper(viewport, (x, 1.25, z))
            for x in (6.0, 7.0)
            for z in (RAISED_Z, RAISED_Z + 1.0)
        ]
        own = (
            min(x for x, _ in corners),
            min(y for _, y in corners),
            max(x for x, _ in corners),
            max(y for _, y in corners),
        )
        assert (own[2] - own[0], own[3] - own[1]) == (
            pytest.approx(20.0, abs=0.01),
            pytest.approx(20.0, abs=0.01),
        )
        assert cube.paper_bbox == pytest.approx(own, abs=0.01)
        (end_x, _) = _on_paper(viewport, (6.0, 1.25, RAISED_Z))
        assert _vertical_stretches(cube, end_x) == [
            (pytest.approx(own[1], abs=0.01), pytest.approx(own[3], abs=0.01))
        ]
        assert _vertical_stretches(wall, end_x) == [
            (pytest.approx(own[3], abs=0.01), pytest.approx(wall.paper_bbox[3], abs=0.01))
        ]

    @_IN_EITHER_UNIT
    @needs_cairo
    def test_the_section_validates(self, tmp_path: Path, *, millimetres: bool) -> None:
        """Written out, not even a warning."""
        from plannotation.export.ifc_svg_pdf import write_sample
        from plannotation.validate import validate

        built, exported = _drawn_section(tmp_path, millimetres=millimetres)
        pdf = write_sample(exported, built, tmp_path / "out", mod_date=MOD_DATE)
        assert [finding.code for finding in validate(pdf).findings] == []


@needs_ifc
@needs_cairo
class TestTheDrawnPlanValidates:
    """The full treatment still writes a plannotation the validator accepts."""

    def test_square_to_the_world_there_are_no_findings(self, tmp_path: Path) -> None:
        """Not even a warning, with what is seen below the cut described as projection.

        The box outside the house, and what of the door the plan cuts shows below the cut.
        """
        from plannotation.export.ifc_svg_pdf import write_sample
        from plannotation.validate import validate

        built, exported = _drawn_plan(tmp_path, behind=True)
        assert sorted(
            e.name or ""
            for e in exported.plannotation.elements or []
            if e.representation == "projection"
        ) == ["Behind", "Front door"]
        pdf = write_sample(exported, built, tmp_path / "out", mod_date=MOD_DATE)
        assert [finding.code for finding in validate(pdf).findings] == []

    def test_turned_only_the_rounded_axes_are_reported(self, tmp_path: Path) -> None:
        """PL-GEO-010 until the validator allows for three-decimal rounding."""
        from plannotation.export.ifc_svg_pdf import write_sample
        from plannotation.validate import validate

        built, exported = _drawn_plan(tmp_path, rotation=30.0)
        pdf = write_sample(exported, built, tmp_path / "out", mod_date=MOD_DATE)
        assert {finding.code for finding in validate(pdf).findings} <= {"PL-GEO-010"}
