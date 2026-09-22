# SPDX-License-Identifier: Apache-2.0
"""The authored exporter: paper arithmetic, sheet composition and PDF conversion.

The transform in :mod:`planlabel.export.paper` is the piece of arithmetic the whole
phase rests on. Three conventions meet in it -- the serializer's y negation, SVG's
y-down, and the model's own length unit -- and each is a chance to be wrong in a way
that looks plausible on a drawing and is wrong by metres in the model. It is therefore
tested against numbers whose answer is known independently, not against itself.
"""

from __future__ import annotations

import importlib.util
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pikepdf
import pytest

from planlabel.export.geometry import bounding_box, path_points, union_box
from planlabel.export.paper import (
    apply,
    invert,
    paper_to_plane,
    plane_from_ifc_plane,
    scale_denominator,
    svg_to_paper,
)
from planlabel.export.sheet import PAPER_SIZES, Sheet, frame_box, title_block_box

if TYPE_CHECKING:
    from pathlib import Path


def has(module: str) -> bool:
    """Report whether an optional module can be imported.

    Args:
        module: The module name.

    Returns:
        True when it is importable.
    """
    return importlib.util.find_spec(module) is not None


needs_cairo = pytest.mark.skipif(not has("cairosvg"), reason="cairosvg is not installed")


class TestThePaperTransform:
    """SPEC 3.5: paper millimetres to the model plane, in the model's own unit."""

    # A view of a metre-unit model at 1:50, placed on an A3 sheet by translate(20, 37).
    # Every number below was checked against a model whose wall coordinates are known.
    MATRIX3 = ((20.0, 0.0, 70.0), (0.0, 20.0, 179.85), (0.0, 0.0, 1.0))
    OFFSET = (20.0, 37.0)
    PAGE_HEIGHT = 297.0

    def transform(self) -> tuple[float, float, float, float, float, float]:
        """Build the affine under test.

        Returns:
            The affine.
        """
        return paper_to_plane(
            self.MATRIX3, self.PAGE_HEIGHT, 1.0, svg_offset=self.OFFSET, svg_scale=1.0
        )

    def test_it_reproduces_the_known_affine(self) -> None:
        """Pinned against the value verified corner by corner on a real drawing."""
        assert [round(v, 6) for v in self.transform()] == [0.05, 0.0, 0.0, 0.05, -4.5, -4.0075]

    @pytest.mark.parametrize(
        ("svg_x", "svg_y", "model"),
        [(250.0, 96.85, (8.0, 6.0)), (90.0, 101.65, (0.0, 5.76)), (90.0, 96.85, (0.0, 6.0))],
    )
    def test_a_drawn_corner_lands_on_its_model_coordinates(
        self, svg_x: float, svg_y: float, model: tuple[float, float]
    ) -> None:
        """The corners of a wall whose model position is known independently."""
        paper = svg_to_paper(svg_x, svg_y, self.PAGE_HEIGHT)
        got = apply(self.transform(), *paper)
        assert got == pytest.approx(model, abs=1e-9)

    def test_the_implied_scale_is_the_declared_scale(self) -> None:
        """PL-GEO-008 compares these two, so the exporter must agree with itself."""
        assert scale_denominator(self.transform(), 1.0) == pytest.approx(50.0)

    def test_the_transform_inverts(self) -> None:
        """A reader highlighting model geometry on the page goes the other way."""
        paper = invert(self.transform(), 8.0, 6.0)
        assert apply(self.transform(), *paper) == pytest.approx((8.0, 6.0), abs=1e-9)

    def test_the_determinant_is_positive(self) -> None:
        """SPEC 6.2: a negative determinant mirrors the view."""
        a, b, c, d, _, _ = self.transform()
        assert a * d - b * c > 0

    def test_a_millimetre_model_gives_the_same_drawing_at_a_different_unit(self) -> None:
        """The output is in the model's unit, so the affine scales with it.

        The same drawing of the same building, modelled in millimetres rather than
        metres, must put its corners at a thousand times the coordinates.
        """
        metres = paper_to_plane(self.MATRIX3, self.PAGE_HEIGHT, 1.0, svg_offset=self.OFFSET)
        millimetres = paper_to_plane(self.MATRIX3, self.PAGE_HEIGHT, 0.001, svg_offset=self.OFFSET)
        point = (250.0, 200.15)
        in_m = apply(metres, *point)
        in_mm = apply(millimetres, *point)
        assert in_mm == pytest.approx((in_m[0] * 1000.0, in_m[1] * 1000.0), rel=1e-9)

    def test_a_degenerate_matrix_is_refused(self) -> None:
        """A view collapsed to a line is not a view."""
        with pytest.raises(ValueError, match="degenerate"):
            paper_to_plane(((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)), 297.0, 1.0)

    def test_a_singular_affine_cannot_be_inverted(self) -> None:
        """And says so, rather than dividing by zero."""
        with pytest.raises(ValueError, match="singular"):
            invert((0.0, 0.0, 0.0, 0.0, 0.0, 0.0), 1.0, 1.0)


class TestTheYFlip:
    """SPEC 3.3: SVG is y-down, paper is y-up from the bottom-left."""

    def test_the_top_of_the_page_is_the_top(self) -> None:
        """SVG y=0 is the top edge, which in paper is the full height."""
        assert svg_to_paper(10.0, 0.0, 297.0) == (10.0, 297.0)

    def test_the_bottom_is_zero(self) -> None:
        """And the bottom edge is the origin."""
        assert svg_to_paper(10.0, 297.0, 297.0) == (10.0, 0.0)

    def test_the_flip_is_its_own_inverse(self) -> None:
        """Which is what lets one function serve both directions."""
        once = svg_to_paper(10.0, 88.35, 297.0)
        assert svg_to_paper(*once, 297.0) == (10.0, 88.35)


class TestPaperGeometry:
    """What a label records about where something was drawn."""

    def test_a_path_becomes_paper_points(self) -> None:
        """The serializer writes moves and lines; both arrive as points."""
        points = path_points("M130,88.35 L290,88.35 L290,93.15", page_height_mm=297.0)
        assert points[0] == (130.0, 208.65)
        assert points[-1] == (290.0, 203.85)

    def test_a_wrapper_transform_is_folded_in(self) -> None:
        """A view placed on a sheet is measured where it sits, not where it was drawn."""
        points = path_points("M0,0 L10,0", page_height_mm=297.0, offset=(20.0, 37.0))
        assert points[0] == (20.0, 260.0)

    def test_a_bbox_is_ordered_lower_left_first(self) -> None:
        """SPEC 3.4, which PL-GEO-002 checks."""
        assert bounding_box([(290.0, 88.0), (130.0, 208.0)]) == (130.0, 88.0, 290.0, 208.0)

    def test_a_bbox_of_nothing_is_refused(self) -> None:
        """An empty box at the origin would be a confident lie."""
        with pytest.raises(ValueError, match="no points"):
            bounding_box([])

    def test_boxes_union(self) -> None:
        """A viewport's box is the union of what it contains."""
        assert union_box([(0.0, 0.0, 10.0, 10.0), (5.0, 5.0, 20.0, 8.0)]) == (0.0, 0.0, 20.0, 10.0)

    def test_points_are_rounded_to_the_serialised_precision(self) -> None:
        """What is recorded is what was measured, to three decimals (SPEC 3.8)."""
        points = path_points("M1.23456,2.34567 L9,9", page_height_mm=297.0)
        assert all(round(v, 3) == v for point in points for v in point)


class TestSheetComposition:
    """A sheet is written in paper millimetres, and says so."""

    def test_the_viewbox_is_one_unit_per_millimetre(self) -> None:
        """So that a reader measuring the drawing measures millimetres."""
        sheet = Sheet(width_mm=420.0, height_mm=297.0)
        assert 'viewBox="0 0 420 297"' in sheet.render()
        assert 'width="420mm"' in sheet.render()

    def test_paper_y_is_flipped_on_the_way_out(self) -> None:
        """A line at paper y=0 is drawn at the bottom of the SVG."""
        sheet = Sheet(width_mm=420.0, height_mm=297.0)
        sheet.line(0.0, 0.0, 10.0, 0.0)
        assert 'y1="297"' in sheet.render()

    def test_text_is_escaped(self) -> None:
        """A sheet title is text a person wrote, and may contain anything."""
        sheet = Sheet(width_mm=420.0, height_mm=297.0)
        sheet.text(10.0, 10.0, "Decke <über> & Co")
        rendered = sheet.render()
        assert "&lt;über&gt;" in rendered
        assert "&amp;" in rendered

    def test_the_title_block_sits_inside_the_frame(self) -> None:
        """Bottom-right of the frame, which is where a reader looks for it."""
        width, height = PAPER_SIZES["A3"]
        frame = frame_box(width, height)
        block = title_block_box(width, height)
        assert frame[0] <= block[0]
        assert block[2] <= frame[2]
        assert frame[1] <= block[1]
        assert block[3] <= frame[3]

    @pytest.mark.parametrize("size", ["A0", "A1", "A2", "A3", "A4"])
    def test_every_paper_size_is_a_real_one(self, size: str) -> None:
        """A drawing is issued at an A size, and the numbers are the ISO ones."""
        width, height = PAPER_SIZES[size]
        assert width > height
        assert abs(width / height - 2**0.5) < 0.01


@needs_cairo
class TestPdfConversion:
    """The converted page must be the size the sheet declared."""

    @staticmethod
    def _sheet() -> tuple[str, float, float]:
        """Compose a minimal A3 sheet.

        Returns:
            Its SVG and the page size it declares.
        """
        width, height = PAPER_SIZES["A3"]
        sheet = Sheet(width_mm=width, height_mm=height)
        sheet.rect(frame_box(width, height))
        sheet.text(20.0, 20.0, "ARC-101")
        return sheet.render(), width, height

    def test_an_a3_sheet_converts_to_an_a3_page(self, tmp_path: Path) -> None:
        """An A3 that comes out 396 mm wide is not an A3, and every label on it is wrong."""
        from planlabel.export.to_pdf import svg_to_pdf

        svg, width, height = self._sheet()
        out = svg_to_pdf(
            svg,
            tmp_path / "s.pdf",
            width_mm=width,
            height_mm=height,
            mod_date=datetime(2024, 1, 1, tzinfo=UTC),
        )
        with pikepdf.open(out) as pdf:
            box = [float(v) for v in pdf.pages[0].MediaBox]
        assert (box[2] - box[0]) * 25.4 / 72 == pytest.approx(width, abs=0.1)
        assert (box[3] - box[1]) * 25.4 / 72 == pytest.approx(height, abs=0.1)

    def test_conversion_is_reproducible(self, tmp_path: Path) -> None:
        """Design brief section 16: two runs over the same input, the same bytes."""
        from planlabel.export.to_pdf import svg_to_pdf

        svg, width, height = self._sheet()
        stamp = datetime(2024, 1, 1, tzinfo=UTC)
        first = svg_to_pdf(
            svg, tmp_path / "a.pdf", width_mm=width, height_mm=height, mod_date=stamp
        )
        second = svg_to_pdf(
            svg, tmp_path / "b.pdf", width_mm=width, height_mm=height, mod_date=stamp
        )
        assert first.read_bytes() == second.read_bytes()

    def test_a_page_of_the_wrong_size_is_refused(self, tmp_path: Path) -> None:
        """The check exists because a silent mis-size poisons every coordinate."""
        from planlabel.errors import ExportError
        from planlabel.export.to_pdf import svg_to_pdf

        svg, _, _ = self._sheet()
        with pytest.raises(ExportError, match="does not exist"):
            svg_to_pdf(
                svg,
                tmp_path / "s.pdf",
                width_mm=999.0,
                height_mm=999.0,
                mod_date=datetime(2024, 1, 1, tzinfo=UTC),
            )


class TestThePlaneIsReadCorrectly:
    """SPEC 3.6: the view's placement in the model."""

    PLANE = "[[1.0,0.0,0.0,0.0],[0.0,1.0,0.0,0.0],[0.0,0.0,1.0,1.2],[0.0,0.0,0.0,1.0]]"

    def test_the_origin_carries_the_cut_height(self) -> None:
        """A plan cut 1.2 m up has its plane there, which is what cutHeight records."""
        origin, _, _ = plane_from_ifc_plane(self.PLANE, 1.0)
        assert origin == pytest.approx([0.0, 0.0, 1.2])

    def test_the_axes_are_unit_length(self) -> None:
        """PL-GEO-010 requires it, so the exporter must not emit anything else."""
        _, x_axis, y_axis = plane_from_ifc_plane(self.PLANE, 1.0)
        for axis in (x_axis, y_axis):
            assert sum(value * value for value in axis) ** 0.5 == pytest.approx(1.0)

    def test_the_origin_is_in_the_models_unit(self) -> None:
        """A millimetre model puts the same plane at 1200, not 1.2."""
        origin, _, _ = plane_from_ifc_plane(self.PLANE, 0.001)
        assert origin[2] == pytest.approx(1200.0)


needs_ifc = pytest.mark.skipif(not has("ifcopenshell"), reason="ifcopenshell is not installed")


class TestReadingProductsOutOfASvg:
    """Groups nest, and a reader that forgets it loses a wall without saying so."""

    NESTED = (
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<g ifc:name="" class="section" ifc:matrix3="[[1,0,0],[0,1,0],[0,0,1]]">'
        '<g id="product-aaa-body" class="IfcWall" ifc:name="First" '
        'ifc:guid="0aaaaaaaaaaaaaaaaaaaaa">'
        '<path d="M0,0 L10,0"/></g>'
        '<g id="product-bbb-body" class="IfcWall" ifc:name="Second" '
        'ifc:guid="0bbbbbbbbbbbbbbbbbbbbb">'
        '<path d="M0,10 L10,10"/></g>'
        "</g></svg>"
    )

    def test_the_first_product_is_not_swallowed_by_the_view_group(self) -> None:
        """The regression this function was rewritten for.

        Matching a group to the next ``</g>`` makes the enclosing view group end at the
        first product's close tag, so the first product vanishes. It reads as a drawing
        with one fewer element rather than as an error, which is the worst way to be
        wrong.
        """
        from planlabel.export.svg_render import read_products

        assert [p.name for p in read_products(self.NESTED)] == ["First", "Second"]

    def test_the_view_group_is_not_itself_a_product(self) -> None:
        """It carries a class but no guid, which is what tells them apart."""
        from planlabel.export.svg_render import read_products

        assert all(p.guid for p in read_products(self.NESTED))

    def test_each_product_keeps_only_its_own_paths(self) -> None:
        """A product that collected its neighbour's geometry would have a wrong bbox."""
        from planlabel.export.svg_render import read_products

        first, second = read_products(self.NESTED)
        assert first.paths == ("M0,0 L10,0",)
        assert second.paths == ("M0,10 L10,10",)


@needs_ifc
class TestTheSampleModel:
    """Design brief section 9: the samples are built from source, and reproducibly."""

    def test_a_rebuilt_model_is_byte_identical(self, tmp_path: Path) -> None:
        """Every entity gets a fresh GlobalId from the API, so they are reseeded.

        Without that, nothing downstream -- the SVG, the labels, the ground truth --
        could be reproducible, and the benchmark's answers would change every run.
        """
        from planlabel.export.models import build_floorplan

        first = build_floorplan(tmp_path / "a.ifc")
        second = build_floorplan(tmp_path / "b.ifc")
        assert first.path.read_bytes() == second.path.read_bytes()

    def test_the_header_carries_no_wall_clock(self, tmp_path: Path) -> None:
        """The defect the test above only catches when the clock happens to tick.

        ifcopenshell stamps the current time into FILE_NAME, so two builds either side
        of a second boundary differ. Comparing two fast builds passes whenever they
        land in the same second -- which is most of the time, and not under coverage,
        which is how this was found. Asserting the stamp itself catches it always.
        """
        from planlabel.export.models import FIXED_TIMESTAMP, build_floorplan

        built = build_floorplan(tmp_path / "a.ifc")
        header = built.path.read_text("utf-8").partition("DATA;")[0]
        assert FIXED_TIMESTAMP in header

    def test_seeded_guids_are_well_formed(self) -> None:
        """22 characters of IFC's own base64 alphabet, which the schema enforces."""
        from planlabel.export.models import seeded_guid

        guid = seeded_guid("floorplan", 3)
        assert re.fullmatch(r"[0-9A-Za-z_$]{22}", guid)

    def test_seeded_guids_differ_between_models(self) -> None:
        """Two samples must not claim the same building element."""
        from planlabel.export.models import seeded_guid

        assert seeded_guid("floorplan", 0) != seeded_guid("positionsplan", 0)

    def test_every_wall_reaches_the_drawing(self, tmp_path: Path) -> None:
        """Four walls in the model, four walls on the plan."""
        from planlabel.export.models import build_floorplan
        from planlabel.export.svg_render import render_view

        built = build_floorplan(tmp_path / "a.ifc")
        view = render_view(
            built.path,
            section_height=built.cut_height,
            scale_denominator=50.0,
            width_mm=300.0,
            height_mm=220.0,
        )
        assert len(view.products) == 4

    def test_the_drawing_is_at_the_scale_it_was_asked_for(self, tmp_path: Path) -> None:
        """PL-GEO-008 compares the transform with the declared scale, so they must agree."""
        from planlabel.export.models import build_floorplan
        from planlabel.export.svg_render import render_view

        built = build_floorplan(tmp_path / "a.ifc")
        view = render_view(
            built.path,
            section_height=built.cut_height,
            scale_denominator=50.0,
            width_mm=300.0,
            height_mm=220.0,
        )
        transform = paper_to_plane(view.matrix3, view.height_mm, 1.0)
        assert scale_denominator(transform, 1.0) == pytest.approx(50.0)

    def test_a_drawn_wall_measures_what_the_model_says(self, tmp_path: Path) -> None:
        """The end-to-end claim: paper geometry, through the transform, is the building.

        The walls are built 240 mm thick and 6.0 and 8.0 metres long. Measuring them off
        the drawing has to give those numbers back, or the label describes a different
        building from the one it was made from.
        """
        from planlabel.export.models import build_floorplan
        from planlabel.export.svg_render import render_view

        built = build_floorplan(tmp_path / "a.ifc")
        view = render_view(
            built.path,
            section_height=built.cut_height,
            scale_denominator=50.0,
            width_mm=300.0,
            height_mm=220.0,
        )
        transform = paper_to_plane(view.matrix3, view.height_mm, 1.0)
        measured = []
        for product in view.products:
            points = [
                point
                for path in product.paths
                for point in path_points(path, page_height_mm=view.height_mm)
            ]
            box = bounding_box(points)
            low = apply(transform, box[0], box[1])
            high = apply(transform, box[2], box[3])
            measured.append((abs(high[0] - low[0]), abs(high[1] - low[1])))
        thicknesses = sorted(round(min(pair), 3) for pair in measured)
        lengths = sorted(round(max(pair), 3) for pair in measured)
        assert thicknesses == [0.24, 0.24, 0.24, 0.24]
        assert lengths == [6.0, 6.0, 8.0, 8.0]
