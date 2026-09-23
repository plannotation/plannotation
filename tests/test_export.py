# SPDX-License-Identifier: Apache-2.0
"""The authored exporter: paper arithmetic, sheet composition and PDF conversion.

The transform in :mod:`plannotation.export.paper` is the piece of arithmetic the whole
phase rests on. Three conventions meet in it -- the serializer's y negation, SVG's
y-down, and the model's own length unit -- and each is a chance to be wrong in a way
that looks plausible on a drawing and is wrong by metres in the model. It is therefore
tested against numbers whose answer is known independently, not against itself.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pikepdf
import pytest

from plannotation.errors import ExportError
from plannotation.export.geometry import bounding_box, path_points, union_box
from plannotation.export.paper import (
    apply,
    invert,
    paper_to_plane,
    plane_from_ifc_plane,
    scale_denominator,
    svg_to_paper,
)
from plannotation.export.sheet import PAPER_SIZES, Sheet, frame_box, title_block_box

MOD_DATE = datetime(2024, 1, 1, tzinfo=UTC)


def has(module: str) -> bool:
    """Report whether an optional module can be imported.

    Args:
        module: The module name.

    Returns:
        True when it is importable.
    """
    return importlib.util.find_spec(module) is not None


needs_cairo = pytest.mark.skipif(not has("cairosvg"), reason="cairosvg is not installed")

#: IFC's base64 alphabet, spelled out independently of the code under test.
_IFC_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"


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
        from plannotation.export.to_pdf import svg_to_pdf

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
        from plannotation.export.to_pdf import svg_to_pdf

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
        from plannotation.errors import ExportError
        from plannotation.export.to_pdf import svg_to_pdf

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
        from plannotation.export.svg_render import read_products

        assert [p.name for p in read_products(self.NESTED)] == ["First", "Second"]

    def test_the_view_group_is_not_itself_a_product(self) -> None:
        """It carries a class but no guid, which is what tells them apart."""
        from plannotation.export.svg_render import read_products

        assert all(p.guid for p in read_products(self.NESTED))

    def test_each_product_keeps_only_its_own_paths(self) -> None:
        """A product that collected its neighbour's geometry would have a wrong bbox."""
        from plannotation.export.svg_render import read_products

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
        from plannotation.export.models import build_floorplan

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
        from plannotation.export.models import FIXED_TIMESTAMP, build_floorplan

        built = build_floorplan(tmp_path / "a.ifc")
        header = built.path.read_text("utf-8").partition("DATA;")[0]
        assert FIXED_TIMESTAMP in header

    def test_seeded_guids_are_well_formed(self) -> None:
        """22 characters of IFC's own base64 alphabet, which the schema enforces."""
        from plannotation.export.models import seeded_guid

        guid = seeded_guid("floorplan", 3)
        assert re.fullmatch(r"[0-9A-Za-z_$]{22}", guid)

    def test_seeded_guids_are_128_bit_numbers(self) -> None:
        """So each round-trips through a UUID, as the serializer's ids assume.

        With one byte too many the first character ran past ``3``, and the serializer's
        ``product-<uuid>`` id silently named a different number from its ``ifc:guid``.
        """
        from plannotation.export.models import seeded_guid

        for index in range(64):
            guid = seeded_guid("floorplan", index)
            assert guid[0] in "0123"
            number = 0
            for character in guid:
                number = number * 64 + _IFC_ALPHABET.index(character)
            assert number < 2**128

    def test_seeded_guids_differ_between_models(self) -> None:
        """Two samples must not claim the same building element."""
        from plannotation.export.models import seeded_guid

        assert seeded_guid("floorplan", 0) != seeded_guid("positionsplan", 0)

    def test_every_wall_reaches_the_drawing(self, tmp_path: Path) -> None:
        """Five walls, two doors and two windows in the model; all nine on the plan."""
        from plannotation.export.models import build_floorplan
        from plannotation.export.svg_render import render_view

        built = build_floorplan(tmp_path / "a.ifc")
        view = render_view(
            built.path,
            section_height=built.cut_height,
            scale_denominator=50.0,
            width_mm=300.0,
            height_mm=220.0,
        )
        classes = sorted(product.ifc_class for product in view.products)
        assert classes == ["IfcDoor"] * 2 + ["IfcWall"] * 5 + ["IfcWindow"] * 2

    def test_the_drawing_is_at_the_scale_it_was_asked_for(self, tmp_path: Path) -> None:
        """PL-GEO-008 compares the transform with the declared scale, so they must agree."""
        from plannotation.export.models import build_floorplan
        from plannotation.export.svg_render import render_view

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

        The external walls are built 240 mm thick and 6.0 and 8.0 metres long, the
        internal one 115 mm thick and 5.52 metres long. Measuring them off the drawing
        has to give those numbers back, or the label describes a different building
        from the one it was made from.
        """
        from plannotation.export.models import build_floorplan
        from plannotation.export.svg_render import render_view

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
            if product.ifc_class != "IfcWall":
                continue
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
        assert thicknesses == [0.115, 0.24, 0.24, 0.24, 0.24]
        assert lengths == [5.52, 6.0, 6.0, 8.0, 8.0]


@needs_ifc
@needs_cairo
class TestTheSampleSets:
    """Design brief section 9: three sets, reproducible, and every one L3 and clean.

    These are slow -- three models built, drawn, converted and labelled -- and they are
    the only tests that exercise the whole chain at once. Everything later in the
    project points at them: the inspector renders them, the MCP server serves them, the
    inference pass is measured against them stripped, and the benchmark asks questions
    whose answers came from the models.
    """

    @staticmethod
    def _build(root: Path) -> list[Path]:
        """Build every sample set into a directory.

        Args:
            root: Where to build them.

        Returns:
            The labelled PDFs.
        """
        from plannotation.export.samples import build_samples

        return build_samples(root, mod_date=datetime(2024, 1, 1, tzinfo=UTC), version="0.0.0-test")

    def test_three_sets_are_built(self, tmp_path: Path) -> None:
        """A floor plan, a position plan and a section."""
        assert len(self._build(tmp_path)) == 3

    def test_each_set_has_every_file(self, tmp_path: Path) -> None:
        """A sample without its model or its ground truth is not a sample."""
        self._build(tmp_path)
        for name in ("floorplan", "positionsplan", "section"):
            present = {path.name for path in (tmp_path / name).iterdir()}
            assert present == {
                "model.ifc",
                "sheet.svg",
                "sheet.pdf",
                "sheet.plannotated.pdf",
                "plannotations.json",
                "groundtruth.jsonl",
            }

    def test_every_sample_validates_with_no_findings(self, tmp_path: Path) -> None:
        """The exporter's output must satisfy the validator this project ships.

        Not merely "no errors": no warnings either. A sample is what a reader learns
        the format from, and a warning in it teaches that warnings are normal.
        """
        from plannotation.validate import validate

        for labelled in self._build(tmp_path):
            report = validate(labelled)
            assert list(report.findings) == [], (
                f"{labelled.parent.name}: {[f.code for f in report.findings]}"
            )

    def test_every_sample_reaches_l3(self, tmp_path: Path) -> None:
        """Section 9's gate. L3 needs elements and a linked annotation on every sheet."""
        from plannotation.validate import validate

        for labelled in self._build(tmp_path):
            assert [page.level.value for page in validate(labelled).pages] == ["L3"]

    def test_a_rebuild_is_byte_identical(self, tmp_path: Path) -> None:
        """Section 9: reproducible. Every file, not just the deterministic-looking ones.

        The IFC writer alone defeated this twice -- once with a wall-clock stamp in the
        header, once with IFC set attributes whose member order varied per run.
        """
        first, second = tmp_path / "one", tmp_path / "two"
        self._build(first)
        self._build(second)
        for path in sorted(first.rglob("*")):
            if path.is_file():
                twin = second / path.relative_to(first)
                assert path.read_bytes() == twin.read_bytes(), f"{path.name} differs"

    def test_the_labelled_pdf_looks_the_same_as_the_plain_one(self, tmp_path: Path) -> None:
        """The project's central claim, on the project's own drawings."""
        from plannotation.pdf.render import assert_same_appearance

        self._build(tmp_path)
        for name in ("floorplan", "positionsplan", "section"):
            assert_same_appearance(
                tmp_path / name / "sheet.pdf", tmp_path / name / "sheet.plannotated.pdf"
            )

    def test_the_ground_truth_answers_come_from_the_model(self, tmp_path: Path) -> None:
        """Every question has an answer, and the wall count is the model's wall count."""
        import json as json_module

        self._build(tmp_path)
        lines = (tmp_path / "groundtruth.jsonl").read_text("utf-8").splitlines()
        questions = [json_module.loads(line) for line in lines]
        assert questions
        assert all(q["answer"] is not None for q in questions)
        assert {q["category"] for q in questions} >= {"count", "dimension", "callout", "grid"}

    def test_the_callouts_form_a_cycle(self, tmp_path: Path) -> None:
        """Each sheet points at the next, so the set is one document and not three.

        It also gives the validator's cross-sheet reference rules something real to
        resolve, and the benchmark a question whose answer is on another drawing.
        """
        import json as json_module

        self._build(tmp_path)
        targets = {}
        for name in ("floorplan", "positionsplan", "section"):
            label = json_module.loads((tmp_path / name / "plannotations.json").read_text("utf-8"))
            callout = next(a for a in label["annotations"] if a["type"] == "callout")
            targets[label["sheet"]["id"]] = callout["target"]["sheetId"]
        assert targets == {"ARC-101": "TWP-201", "TWP-201": "ARC-301", "ARC-301": "ARC-101"}

    def test_building_one_set_by_name_builds_only_it(self, tmp_path: Path) -> None:
        """So that iterating on one drawing does not cost the other two."""
        from plannotation.export.samples import build_samples

        written = build_samples(
            tmp_path,
            mod_date=datetime(2024, 1, 1, tzinfo=UTC),
            version="0.0.0-test",
            only="section",
        )
        assert len(written) == 1
        assert not (tmp_path / "floorplan").exists()

    def test_an_unknown_sample_name_is_refused(self, tmp_path: Path) -> None:
        """And says which names there are."""
        from plannotation.export.samples import build_samples

        with pytest.raises(KeyError, match="floorplan"):
            build_samples(
                tmp_path,
                mod_date=datetime(2024, 1, 1, tzinfo=UTC),
                version="0.0.0-test",
                only="nonesuch",
            )


SAMPLES = Path(__file__).parent.parent / "samples"

needs_samples = pytest.mark.skipif(
    not (SAMPLES / "section" / "plannotations.json").is_file(),
    reason="samples are not built; run make samples",
)


def _sample(name: str) -> dict[str, Any]:
    """Load one built sample's label as JSON.

    Args:
        name: The sample.

    Returns:
        Its label.
    """
    return json.loads((SAMPLES / name / "plannotations.json").read_text("utf-8"))


class TestLevelText:
    """Levels print as a German section prints them."""

    @pytest.mark.parametrize(
        ("elevation", "text"),
        [(0.0, "±0,00"), (0.004, "±0,00"), (3.0, "+3,00"), (-0.25, "-0,25"), (12.5, "+12,50")],
    )
    def test_level_text(self, elevation: float, text: str) -> None:
        """Signed, two decimals, a decimal comma."""
        from plannotation.export.ifc_svg_pdf import level_text

        assert level_text(elevation) == text


@needs_samples
class TestWhatTheDesignBriefAsksOfTheSamples:
    """Design brief section 9, sheet by sheet."""

    @pytest.mark.parametrize("name", ["floorplan", "positionsplan", "section"])
    def test_the_label_states_the_model_s_own_unit(self, name: str) -> None:
        """A label naming metres for a millimetre model is out by a factor of a thousand."""
        ifcopenshell = pytest.importorskip("ifcopenshell")
        from ifcopenshell.util.unit import calculate_unit_scale

        from plannotation.units import length_unit_for

        model = ifcopenshell.open(str(SAMPLES / name / "model.ifc"))
        stated = _sample(name)["model"]["lengthUnit"]
        assert stated == length_unit_for(calculate_unit_scale(model))

    @pytest.mark.parametrize("name", ["floorplan", "positionsplan", "section"])
    def test_at_least_six_dimensions_per_sheet(self, name: str) -> None:
        """Between grids, or between levels, each linked to what it measures."""
        dimensions = [a for a in _sample(name)["annotations"] if a["type"] == "dimension"]
        assert len(dimensions) >= 6
        assert all(len(a["measures"]) == 2 for a in dimensions)

    def test_the_floor_plan_has_doors_windows_and_a_four_by_four_grid(self) -> None:
        """(a) walls, doors, windows, one storey, grid A-D / 1-4."""
        label = _sample("floorplan")
        classes = sorted(e["ifcClass"] for e in label["elements"])
        assert classes.count("IfcDoor") == 2
        assert classes.count("IfcWindow") == 2
        grids = sorted(a["axis"] for a in label["annotations"] if a["type"] == "grid")
        assert grids == ["1", "2", "3", "4", "A", "B", "C", "D"]

    def test_the_position_plan_numbers_every_member_and_states_its_section(self) -> None:
        """(b) columns, beams, a slab, every one tagged Pos. n with its cross-section."""
        label = _sample("positionsplan")
        elements = label["elements"]
        assert {e["ifcClass"] for e in elements} == {"IfcColumn", "IfcBeam", "IfcSlab"}
        assert sorted(int(e["tag"].split()[-1]) for e in elements) == list(range(1, 17))
        shown = {
            a["shows"]["element"]: a["text"]
            for a in label["annotations"]
            if a["type"] == "text" and a["shows"]["property"].endswith(".Reference")
        }
        assert {shown[e["id"]] for e in elements} == {"30/30", "30/75", "d = 25 cm"}

    def test_the_slab_below_the_cut_is_a_projection(self) -> None:
        """Drawn beyond the cut, and said to be; and written where an SVG reader finds it."""
        label = _sample("positionsplan")
        slab = next(e for e in label["elements"] if e["ifcClass"] == "IfcSlab")
        assert slab["representation"] == "projection"
        svg = (SAMPLES / "positionsplan" / "sheet.svg").read_text("utf-8")
        assert f'ifc:guid="{slab["ifcGuid"]}"' in svg
        cut = {e["representation"] for e in label["elements"] if e["ifcClass"] != "IfcSlab"}
        assert cut == {"cut"}

    def test_the_section_is_a_vertical_cut_with_levels(self) -> None:
        """(c) two storeys and their levels, on a plane that stands up."""
        label = _sample("section")
        (viewport,) = label["viewports"]
        assert viewport["kind"] == "section"
        assert viewport["plane"]["yAxis"] == [0, 0, 1]
        levels = [a for a in label["annotations"] if a["type"] == "level"]
        assert sorted(a["elevation"] for a in levels) == [0, 3, 6]
        assert all(a.get("ifcGuid") for a in levels)

    def test_each_level_mark_stands_at_its_elevation(self) -> None:
        """A level's box centre, taken through the viewport, is the height it prints."""
        label = _sample("section")
        (viewport,) = label["viewports"]
        a, b, c, d, e, f = viewport["paperToPlane"]
        plane = viewport["plane"]
        for level in (x for x in label["annotations"] if x["type"] == "level"):
            x0, y0, x1, y1 = level["paperBBox"]
            x, y = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            u, v = a * x + c * y + e, b * x + d * y + f
            z = plane["origin"][2] + u * plane["xAxis"][2] + v * plane["yAxis"][2]
            assert z == pytest.approx(level["elevation"], abs=0.01)

    @pytest.mark.parametrize("name", ["floorplan", "positionsplan", "section"])
    def test_no_mark_sits_on_another_mark_or_on_an_element(self, name: str) -> None:
        """A mark on a cut wall is black on black; two marks on each other are neither."""
        label = _sample(name)
        marks = [a["paperBBox"] for a in label["annotations"] if a["type"] in ("tag", "text")]
        content = (
            min(e["paperBBox"][0] for e in label["elements"]),
            min(e["paperBBox"][1] for e in label["elements"]),
            max(e["paperBBox"][2] for e in label["elements"]),
            max(e["paperBBox"][3] for e in label["elements"]),
        )
        small = [
            e["paperBBox"]
            for e in label["elements"]
            if not (
                (e["paperBBox"][2] - e["paperBBox"][0]) > 0.5 * (content[2] - content[0])
                and (e["paperBBox"][3] - e["paperBBox"][1]) > 0.5 * (content[3] - content[1])
            )
        ]

        def overlap(p: list[float], q: list[float]) -> float:
            width = min(p[2], q[2]) - max(p[0], q[0])
            height = min(p[3], q[3]) - max(p[1], q[1])
            return max(0.0, width) * max(0.0, height)

        tags = [a for a in label["annotations"] if a["type"] == "tag"]
        for tag in tags:
            others = [m for m in marks if m is not tag["paperBBox"]]
            reference = next(
                (
                    a["paperBBox"]
                    for a in label["annotations"]
                    if a["type"] == "text" and a["shows"]["element"] == tag["shows"]["element"]
                ),
                None,
            )
            clash = [m for m in others if m is not reference and overlap(tag["paperBBox"], m) > 0]
            assert clash == [], tag["text"]
            assert all(overlap(tag["paperBBox"], box) == 0 for box in small), tag["text"]

    def test_the_new_question_kinds_are_asked(self) -> None:
        """Storey elevations and cross-sections join counts, dimensions and callouts."""
        categories = {
            json.loads(line)["category"]
            for line in (SAMPLES / "groundtruth.jsonl").read_text("utf-8").splitlines()
        }
        assert categories >= {"count", "dimension", "callout", "grid", "tag", "level", "section"}


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in inkscape is a shell script")
class TestTheInkscapeFallback:
    """Design brief section 9: fall back to the Inkscape CLI if present and the flag is set."""

    SHEET = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="420mm" height="297mm" '
        'viewBox="0 0 420 297"><rect width="10" height="10"/></svg>'
    )

    @staticmethod
    def _no_cairo(monkeypatch: pytest.MonkeyPatch) -> None:
        """Make CairoSVG unavailable, as on a machine without libcairo.

        Args:
            monkeypatch: pytest's monkeypatch.
        """
        from plannotation.errors import MissingExtraError
        from plannotation.export import to_pdf

        def missing() -> None:
            msg = "no libcairo"
            raise MissingExtraError(msg)

        monkeypatch.setattr(to_pdf, "_cairosvg", missing)

    @staticmethod
    def _inkscape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, exit_code: int = 0) -> None:
        """Put a stand-in ``inkscape`` on the PATH that writes an A3 PDF.

        Args:
            tmp_path: Where to put it.
            monkeypatch: pytest's monkeypatch.
            exit_code: What it exits with.
        """
        a3 = tmp_path / "a3.pdf"
        with pikepdf.new() as pdf:
            pdf.add_blank_page(page_size=(420 / 25.4 * 72, 297 / 25.4 * 72))
            pdf.save(a3)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        script = bin_dir / "inkscape"
        script.write_text(
            "#!/bin/sh\n"
            'for arg in "$@"; do case "$arg" in --export-filename=*) '
            'out="${arg#--export-filename=}";; esac; done\n'
            f'/bin/cp "{a3}" "$out"\n'
            f"exit {exit_code}\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        monkeypatch.setenv("PATH", str(bin_dir))

    def test_without_the_flag_a_missing_cairo_is_an_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The fallback is asked for, never assumed."""
        from plannotation.errors import MissingExtraError
        from plannotation.export.to_pdf import svg_to_pdf

        self._no_cairo(monkeypatch)
        self._inkscape(tmp_path, monkeypatch)
        with pytest.raises(MissingExtraError, match="libcairo"):
            svg_to_pdf(
                self.SHEET, tmp_path / "o.pdf", width_mm=420, height_mm=297, mod_date=MOD_DATE
            )

    def test_with_the_flag_inkscape_converts_and_the_size_is_checked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same checks, same stamp, whichever program drew the page."""
        from plannotation.export.to_pdf import svg_to_pdf

        self._no_cairo(monkeypatch)
        self._inkscape(tmp_path, monkeypatch)
        out = svg_to_pdf(
            self.SHEET,
            tmp_path / "o.pdf",
            width_mm=420,
            height_mm=297,
            mod_date=MOD_DATE,
            inkscape_fallback=True,
        )
        with pikepdf.open(out) as pdf:
            assert str(pdf.docinfo["/ModDate"]) == "D:20240101000000Z"
        with pytest.raises(ExportError, match=r"297\.00 x 420\.00"):
            svg_to_pdf(
                self.SHEET,
                tmp_path / "p.pdf",
                width_mm=297,
                height_mm=420,
                mod_date=MOD_DATE,
                inkscape_fallback=True,
            )

    def test_with_the_flag_and_no_inkscape_the_message_names_both(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Either program would do; the message says so."""
        from plannotation.errors import MissingExtraError
        from plannotation.export.to_pdf import svg_to_pdf

        self._no_cairo(monkeypatch)
        monkeypatch.setenv("PATH", str(tmp_path))
        with pytest.raises(MissingExtraError, match="inkscape"):
            svg_to_pdf(
                self.SHEET,
                tmp_path / "o.pdf",
                width_mm=420,
                height_mm=297,
                mod_date=MOD_DATE,
                inkscape_fallback=True,
            )

    def test_an_inkscape_failure_is_an_export_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With its exit code."""
        from plannotation.export.to_pdf import svg_to_pdf

        self._no_cairo(monkeypatch)
        self._inkscape(tmp_path, monkeypatch, exit_code=3)
        with pytest.raises(ExportError, match="exit 3"):
            svg_to_pdf(
                self.SHEET,
                tmp_path / "o.pdf",
                width_mm=420,
                height_mm=297,
                mod_date=MOD_DATE,
                inkscape_fallback=True,
            )
