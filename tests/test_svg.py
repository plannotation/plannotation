# SPDX-License-Identifier: Apache-2.0
"""The SVG carrier: reading the IFC identity an IfcOpenShell SVG already carries.

Three things are held to account. The geometry: units, ``viewBox``, nested viewports
and every transform end up as paper millimetres, y up, with the flip of SPEC 3.3 done
once. The identity: GlobalIds are read from ``ifc:guid`` or decoded from the
serializer's ``product-<uuid>`` ids, and never rewritten. And the safety: a DTD is
refused, sizes are bounded, and ``<image>`` references go nowhere but a local SVG.

The strongest check needs the samples: a label derived from each sample's sheet SVG
must agree with the label the exporter wrote from the model itself.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path

import pytest

import tests.pdf_fixtures as fx
from plannotation.errors import CarrierError, LabelMismatchError
from plannotation.model import PageLabel, canonical_json
from plannotation.svg import (
    SheetSource,
    attach_from_svg,
    carrier,
    derive_label,
    parse_svg,
    read_svg,
)
from plannotation.svg.carrier import (
    compose,
    guid_from_uuid,
    parse_transform,
    uuid_from_guid,
)
from plannotation.svg.label import paper_to_plane
from plannotation.validate import validate

SAMPLES = Path(__file__).parent.parent / "samples"
NAMES = ("floorplan", "positionsplan", "section")
MOD_DATE = datetime(2024, 1, 1, tzinfo=UTC)
GUID = "0J28eM80qAzh9m6vRrT_YM"
UUID = "13088a16-200d-0af6-b270-1b96f577e896"
MATRIX3 = "[[20,0,70],[0,20,160],[0,0,1]]"
PLANE = "[[1,0,0,0],[0,1,0,0],[0,0,1,1.2],[0,0,0,1]]"
SOURCE = SheetSource(sheet_id="A-101", unit_scale_to_m=1.0, length_unit="m")


def svg(body: str, *, root: str = 'width="420mm" height="297mm" viewBox="0 0 420 297"') -> bytes:
    """Wrap some markup in an SVG document with the IFC namespace declared.

    Args:
        body: The document's content.
        root: The root element's size attributes.

    Returns:
        The document.
    """
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:ifc="http://www.ifcopenshell.org/ns" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" {root}>{body}</svg>'
    ).encode()


def view(body: str, *, transform: str = "translate(25,12)") -> str:
    """Wrap products in a serializer-style view group, placed on the sheet.

    Args:
        body: The products.
        transform: The wrapper's transform.

    Returns:
        The markup.
    """
    return (
        f'<g transform="{transform}"><g class="section" ifc:name="Plan" '
        f"ifc:plane='{PLANE}' ifc:matrix3='{MATRIX3}'>{body}</g></g>"
    )


def product(guid: str = GUID, *, ifc_class: str = "IfcWall", shape: str = "") -> str:
    """Return one serializer-style product group.

    Args:
        guid: Its GlobalId.
        ifc_class: Its class.
        shape: What it draws; a 10 x 5 rectangle at (70, 150) by default.

    Returns:
        The markup.
    """
    drawn = shape or '<path d="M70,150 L80,150 L80,155 L70,155 Z"/>'
    return f'<g class="{ifc_class}" ifc:name="Wall" ifc:guid="{guid}">{drawn}</g>'


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
class TestIdentity:
    """GlobalIds are read, and decoded, and never invented."""

    def test_a_uuid_is_encoded_as_ifc_does(self) -> None:
        """Checked against a value ifcopenshell.guid.compress produced."""
        assert guid_from_uuid(UUID) == GUID
        assert guid_from_uuid(UUID.replace("-", "")) == GUID
        assert guid_from_uuid("ffffffff-ffff-ffff-ffff-ffffffffffff")[0] == "3"

    def test_a_global_id_decodes_to_its_uuid(self) -> None:
        """And back again, for every character the first position can hold."""
        assert uuid_from_guid(GUID) == UUID
        for first in "0123":
            guid = first + GUID[1:]
            assert guid_from_uuid(uuid_from_guid(guid)) == guid

    def test_what_is_not_a_global_id_is_refused(self) -> None:
        """A first character past 3 would be more than 128 bits."""
        with pytest.raises(ValueError, match="not an IFC GlobalId"):
            uuid_from_guid("O" + GUID[1:])

    def test_a_non_uuid_is_refused(self) -> None:
        """Rather than encoded as whatever number it happens to spell."""
        with pytest.raises(ValueError, match="not a UUID"):
            guid_from_uuid("not-a-uuid")

    @pytest.mark.parametrize(
        ("attributes", "expected"),
        [
            (f'ifc:guid="{GUID}"', GUID),
            (f'id="product-{UUID}-body"', GUID),
            (f'id="product-{GUID}-body"', GUID),
            (f'id="product-{GUID}"', GUID),
            (f'id="product-{UUID}" ifc:guid="3aaaaaaaaaaaaaaaaaaaaa"', "3aaaaaaaaaaaaaaaaaaaaa"),
            ('id="product-nothing"', None),
            ('id="wall-1"', None),
        ],
    )
    def test_where_a_global_id_is_found(self, attributes: str, expected: str | None) -> None:
        """``ifc:guid`` first, then the id; a group naming neither is no product."""
        body = f'<g class="IfcWall" {attributes}><path d="M0,0 L1,1"/></g>'
        found = [p.guid for p in parse_svg(svg(body)).products]
        assert found == ([] if expected is None else [expected])

    def test_the_ifc_class_is_the_class_value_that_is_one(self) -> None:
        """Other class values, such as a material, are left alone."""
        body = product(ifc_class="cut IfcSlab material-concrete")
        assert parse_svg(svg(body)).products[0].ifc_class == "IfcSlab"

    def test_a_product_without_a_class_is_ignored(self) -> None:
        """An element needs a class, and none is guessed."""
        assert parse_svg(svg(product(ifc_class="cut"))).products == ()

    def test_a_product_that_draws_nothing_is_left_out(self) -> None:
        """A label element needs a bounding box."""
        assert parse_svg(svg(product(shape="<text>Pos. 1</text>"))).products == ()


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
class TestGeometry:
    """Everything ends in paper millimetres, origin bottom-left, y up."""

    def test_the_flip_and_the_placement(self) -> None:
        """A rectangle at SVG (95..105, 162..167) is at paper y 130..135 on A3."""
        sheet = parse_svg(svg(view(product())))
        assert (sheet.width_mm, sheet.height_mm) == (420.0, 297.0)
        assert sheet.products[0].paper_bbox == (95.0, 130.0, 105.0, 135.0)
        assert sheet.products[0].view == 0

    @pytest.mark.parametrize(
        ("root", "size", "box"),
        [
            ('width="420mm" height="297mm" viewBox="0 0 420 297"', (420, 297), (70, 142, 80, 147)),
            ('width="42cm" height="29.7cm" viewBox="0 0 420 297"', (420, 297), (70, 142, 80, 147)),
            ('viewBox="0 0 420 297"', (420, 297), (70, 142, 80, 147)),
            (
                'width="840mm" height="594mm" viewBox="0 0 420 297"',
                (840, 594),
                (140, 284, 160, 294),
            ),
            (
                'width="96" height="96"',
                (25.4, 25.4),
                (18.521, -15.61, 21.167, -14.288),
            ),
        ],
    )
    def test_units_and_view_boxes(
        self, root: str, size: tuple[float, float], box: tuple[float, ...]
    ) -> None:
        """A bare size is CSS pixels; a viewBox alone is taken as millimetres."""
        sheet = parse_svg(svg(product(), root=root))
        assert (sheet.width_mm, sheet.height_mm) == pytest.approx(size)
        assert sheet.products[0].paper_bbox == pytest.approx(box, abs=1e-3)

    def test_a_page_with_no_size_is_refused(self) -> None:
        """There is nothing to flip about."""
        with pytest.raises(CarrierError, match="no page size"):
            parse_svg(svg(product(), root=""))

    @pytest.mark.parametrize(
        ("transform", "expected"),
        [
            ("", (1, 0, 0, 1, 0, 0)),
            ("translate(5)", (1, 0, 0, 1, 5, 0)),
            ("translate(5, 6) scale(2)", (2, 0, 0, 2, 5, 6)),
            ("scale(2 3)", (2, 0, 0, 3, 0, 0)),
            ("matrix(1 2 3 4 5 6)", (1, 2, 3, 4, 5, 6)),
            ("rotate(90)", (0, 1, -1, 0, 0, 0)),
            ("rotate(90 10 10)", (0, 1, -1, 0, 20, 0)),
            ("skewX(45)", (1, 0, 1, 1, 0, 0)),
            ("skewY(45)", (1, 1, 0, 1, 0, 0)),
            ("translate(1,2,3)", (1, 0, 0, 1, 0, 0)),
        ],
    )
    def test_transforms(self, transform: str, expected: tuple[float, ...]) -> None:
        """Every SVG transform function, and a malformed one read as none."""
        assert parse_transform(transform) == pytest.approx(expected, abs=1e-12)

    def test_composition_applies_the_inner_transform_first(self) -> None:
        """``translate(10) scale(2)`` scales, then translates."""
        combined = compose(parse_transform("translate(10)"), parse_transform("scale(2)"))
        assert carrier.apply(combined, 1, 1) == (12, 2)

    @pytest.mark.parametrize(
        ("shape", "box"),
        [
            ('<rect x="70" y="150" width="10" height="5"/>', (70, 142, 80, 147)),
            ('<line x1="70" y1="150" x2="80" y2="155"/>', (70, 142, 80, 147)),
            ('<polyline points="70,150 80,155"/>', (70, 142, 80, 147)),
            ('<polygon points="70,150 80,150 80,155"/>', (70, 142, 80, 147)),
            ('<circle cx="75" cy="150" r="5"/>', (70, 142, 80, 152)),
            ('<ellipse cx="75" cy="150" rx="5" ry="2"/>', (70, 145, 80, 149)),
            ('<path d="M70,150 C75,140 80,160 80,150"/>', (70, 144.113, 80, 149.887)),
        ],
    )
    def test_every_shape(self, shape: str, box: tuple[float, ...]) -> None:
        """Curves and ellipses are flattened; their boxes are close to exact."""
        sheet = parse_svg(svg(product(shape=shape)))
        assert sheet.products[0].paper_bbox == pytest.approx(box, abs=0.05)

    def test_an_unreadable_path_is_skipped(self) -> None:
        """One bad path does not lose the rest of the drawing."""
        body = product(shape='<path d="M 1 Q"/><rect x="0" y="0" width="1" height="1"/>')
        assert parse_svg(svg(body)).products[0].paper_bbox == (0, 296, 1, 297)

    def test_a_nested_svg_is_a_viewport(self) -> None:
        """Its viewBox is fitted into its box, as SVG says."""
        body = (
            f'<svg x="100" y="100" width="20" height="10" viewBox="0 0 200 100">{product()}</svg>'
        )
        box = parse_svg(svg(body)).products[0].paper_bbox
        assert box == pytest.approx((107, 181.5, 108, 182), abs=1e-6)

    @pytest.mark.parametrize(
        ("aspect", "box"),
        [
            (None, (107, 176.5, 108, 177)),
            ("xMinYMin meet", (107, 181.5, 108, 182)),
            ("xMaxYMax meet", (107, 171.5, 108, 172)),
            ("none", (107, 166, 108, 167)),
            ("xMidYMid slice", (104, 166, 106, 167)),
        ],
    )
    def test_preserve_aspect_ratio(self, aspect: str | None, box: tuple[float, ...]) -> None:
        """Meet, slice, none, and the alignments."""
        attribute = "" if aspect is None else f' preserveAspectRatio="{aspect}"'
        body = (
            f'<svg x="100" y="100" width="20" height="20" viewBox="0 0 200 100"{attribute}>'
            f"{product()}</svg>"
        )
        assert parse_svg(svg(body)).products[0].paper_bbox == pytest.approx(box, abs=1e-6)

    def test_a_nested_svg_without_a_view_box_only_moves(self) -> None:
        """Its content keeps its own units."""
        body = f'<svg x="10" y="20">{product()}</svg>'
        assert parse_svg(svg(body)).products[0].paper_bbox == (80, 122, 90, 127)

    def test_what_is_not_rendered_is_not_read(self) -> None:
        """A product inside ``<defs>`` is drawn only where it is used."""
        body = f"<defs>{product()}</defs><metadata>{product()}</metadata>"
        assert parse_svg(svg(body)).products == ()

    def test_elements_in_other_namespaces_are_skipped(self) -> None:
        """An editor's own elements are not SVG."""
        body = f'<foo:bar xmlns:foo="urn:x">{product()}</foo:bar><!-- a comment -->'
        assert parse_svg(svg(body)).products == ()

    def test_a_document_without_the_svg_namespace_still_reads(self) -> None:
        """Hand-written SVG often leaves it out."""
        square = product(shape='<rect width="1" height="1"/>')
        body = (
            '<svg xmlns:ifc="http://www.ifcopenshell.org/ns" width="10mm" height="10mm" '
            f'viewBox="0 0 10 10">{square}</svg>'
        )
        assert parse_svg(body.encode()).products[0].paper_bbox == (0, 9, 1, 10)


# ---------------------------------------------------------------------------
# Views and products
# ---------------------------------------------------------------------------
class TestViews:
    """Views carry the transform; products are collected per view."""

    def test_a_view_records_its_placement(self) -> None:
        """Everything above the view group, composed."""
        sheet = parse_svg(svg(view(product())))
        (only,) = sheet.views
        assert only.name == "Plan"
        assert only.matrix3 == ((20, 0, 70), (0, 20, 160))
        assert only.placement == (1, 0, 0, 1, 25, 12)
        assert only.plane == PLANE

    def test_an_unreadable_matrix_is_no_view(self) -> None:
        """Its products still count, just without a view."""
        body = f"<g ifc:matrix3='[[1,2],[3,4]]'>{product()}</g>"
        sheet = parse_svg(svg(body))
        assert sheet.views == ()
        assert sheet.products[0].view is None

    def test_a_product_drawn_in_several_groups_is_one_product(self) -> None:
        """The serializer draws a body and an axis; they are one element."""
        body = view(
            product(shape='<rect x="0" y="0" width="1" height="1"/>')
            + product(shape='<rect x="10" y="10" width="1" height="1"/>')
        )
        (only,) = parse_svg(svg(body)).products
        assert len(only.outlines) == 2

    def test_the_same_product_in_two_views_is_two_products(self) -> None:
        """A plan and a section may both draw one wall."""
        body = view(product()) + view(product(), transform="translate(200,12)")
        products = parse_svg(svg(body)).products
        assert [p.view for p in products] == [0, 1]

    def test_the_nearest_product_owns_the_geometry(self) -> None:
        """A storey group wrapping walls draws nothing of its own."""
        storey_guid = guid_from_uuid("0" * 32)
        body = f'<g class="IfcBuildingStorey" ifc:guid="{storey_guid}">{product()}</g>'
        assert [p.ifc_class for p in parse_svg(svg(body)).products] == ["IfcWall"]


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------
class TestSafety:
    """The SVG is untrusted input (SPEC 9.3)."""

    @pytest.mark.parametrize(
        "prefix",
        [
            b'<!DOCTYPE svg [<!ENTITY a "aaaa">]>',
            b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "x.dtd">',
        ],
    )
    def test_a_dtd_is_refused(self, prefix: bytes) -> None:
        """Entity expansion is the classic XML attack, and no drawing needs a DTD."""
        with pytest.raises(CarrierError, match="DTD"):
            parse_svg(prefix + svg(product()))

    def test_malformed_xml_is_refused(self) -> None:
        """With the parser's reason."""
        with pytest.raises(CarrierError, match="well-formed"):
            parse_svg(b"<svg><g></svg>")

    def test_a_document_that_is_not_svg_is_refused(self) -> None:
        """An XML file is not a drawing."""
        with pytest.raises(CarrierError, match="not an SVG"):
            parse_svg(b"<html/>")

    def test_size_is_bounded(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """In memory and on disk."""
        monkeypatch.setattr(carrier, "MAX_SVG_BYTES", 100)
        with pytest.raises(CarrierError, match="larger than 100"):
            parse_svg(svg(product()))
        big = tmp_path / "big.svg"
        big.write_bytes(svg(product()))
        with pytest.raises(CarrierError, match="not read"):
            read_svg(big)

    def test_the_element_count_is_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A billion empty groups are a denial of service without a DTD."""
        monkeypatch.setattr(carrier, "MAX_ELEMENTS", 3)
        with pytest.raises(CarrierError, match="more than 3 elements"):
            parse_svg(svg("<g/>" * 4))

    def test_deep_nesting_needs_no_recursion(self) -> None:
        """Ten thousand nested groups are walked with an explicit stack."""
        depth = 10_000
        body = "<g>" * depth + product() + "</g>" * depth
        assert len(parse_svg(svg(body)).products) == 1


# ---------------------------------------------------------------------------
# <image> references, as a sheet layout uses them
# ---------------------------------------------------------------------------
class TestImages:
    """A layout places drawings by reference; only local SVGs are followed."""

    @staticmethod
    def _layout(tmp_path: Path, href: str, *, size: str = 'width="100" height="50"') -> Path:
        """Write a drawing and a sheet that places it.

        Args:
            tmp_path: Where to write.
            href: The sheet's reference to the drawing.
            size: The image's size attributes.

        Returns:
            The sheet's path.
        """
        (tmp_path / "drawings").mkdir(exist_ok=True)
        drawing = svg(product(), root='width="200mm" height="100mm" viewBox="0 0 200 100"')
        (tmp_path / "drawings" / "plan.svg").write_bytes(drawing)
        (tmp_path / "sheets").mkdir(exist_ok=True)
        sheet = tmp_path / "sheets" / "A-101.svg"
        sheet.write_bytes(svg(f'<image x="10" y="20" {size} xlink:href="{href}"/>'))
        return sheet

    def test_a_referenced_drawing_is_placed_and_read(self, tmp_path: Path) -> None:
        """At half size, 10 mm in and 20 mm down."""
        sheet = read_svg(self._layout(tmp_path, "../drawings/plan.svg"))
        assert sheet.products[0].paper_bbox == pytest.approx((45, 199.5, 50, 202))

    def test_an_image_without_a_size_takes_the_drawing_s(self, tmp_path: Path) -> None:
        """Its viewBox, in the sheet's units."""
        sheet = read_svg(self._layout(tmp_path, "../drawings/plan.svg", size=""))
        assert sheet.products[0].paper_bbox == pytest.approx((80, 122, 90, 127))

    @pytest.mark.parametrize(
        "href",
        [
            "https://example.com/plan.svg",
            "data:image/svg+xml;base64,AAAA",
            "/etc/plan.svg",
            "../drawings/absent.svg",
            "../drawings/plan.png",
        ],
    )
    def test_nothing_else_is_followed(self, tmp_path: Path, href: str) -> None:
        """No network, no data URIs, no absolute paths, nothing that is not there."""
        assert read_svg(self._layout(tmp_path, href)).products == ()

    def test_references_are_not_followed_from_memory(self, tmp_path: Path) -> None:
        """Without a base directory there is nothing to resolve against."""
        data = self._layout(tmp_path, "../drawings/plan.svg").read_bytes()
        assert parse_svg(data).products == ()

    def test_references_nest_only_so_deep(self, tmp_path: Path) -> None:
        """A document that references itself does not loop."""
        loop = tmp_path / "loop.svg"
        loop.write_bytes(svg(product() + '<image width="10" height="10" href="loop.svg"/>'))
        products = read_svg(loop).products
        assert len(products) == 1
        assert len(products[0].outlines) == carrier.MAX_IMAGE_DEPTH + 1

    def test_an_image_whose_drawing_has_no_size_is_skipped(self, tmp_path: Path) -> None:
        """There is no way to know where it would go."""
        (tmp_path / "bare.svg").write_bytes(svg(product(), root='width="10mm" height="10mm"'))
        sheet = tmp_path / "sheet.svg"
        sheet.write_bytes(svg('<image href="bare.svg"/>'))
        assert read_svg(sheet).products == ()


# ---------------------------------------------------------------------------
# The label
# ---------------------------------------------------------------------------
class TestLabel:
    """From SVG to an authored L2 label."""

    def test_a_placed_view_gets_the_transform_the_exporter_computes(self) -> None:
        """The same numbers as the sample floor plan, from the SVG alone."""
        sheet = parse_svg(svg(view(product())))
        transform = paper_to_plane(sheet.views[0], sheet.height_mm, 1.0)
        assert transform == pytest.approx((0.05, 0, 0, 0.05, -4.75, -6.25))
        in_mm = paper_to_plane(sheet.views[0], sheet.height_mm, 0.001)
        assert in_mm == pytest.approx((50, 0, 0, 50, -4750, -6250))

    def test_a_rotated_placement_is_undone(self) -> None:
        """Round-trip a model point through a view placed at 90 degrees."""
        sheet = parse_svg(svg(view(product(), transform="translate(300,20) rotate(90)")))
        transform = paper_to_plane(sheet.views[0], sheet.height_mm, 1.0)
        (m00, m01, m02), (m10, m11, m12) = sheet.views[0].matrix3
        u, v = 3.0, 4.0
        local = (m00 * u - m01 * v + m02, m10 * u - m11 * v + m12)
        page = carrier.apply(sheet.views[0].placement, *local)
        a, b, c, d, e, f = transform
        x, y = page[0], sheet.height_mm - page[1]
        assert (a * x + c * y + e, b * x + d * y + f) == pytest.approx((u, v))

    def test_a_degenerate_placement_is_refused(self) -> None:
        """A view squashed to a line has no inverse."""
        sheet = parse_svg(svg(view(product(), transform="scale(0 1)")))
        with pytest.raises(ValueError, match="degenerate"):
            paper_to_plane(sheet.views[0], sheet.height_mm, 1.0)

    def test_the_label_is_authored_l2_with_tags_from_the_model(self) -> None:
        """Tags come from the source, since the SVG does not carry them."""
        body = view(product() + product(guid="1" * 22))
        sheet = parse_svg(svg(body))
        source = SheetSource(
            sheet_id="A-101",
            unit_scale_to_m=1.0,
            length_unit="m",
            title="Plan",
            tags={GUID: "Pos. 1"},
            model_file="model.ifc",
            ifc_schema="IFC4",
        )
        label = derive_label(sheet, source, page_index=2)
        data = json.loads(canonical_json(label))
        assert data["provenance"] == "authored"
        assert label.level is not None
        assert label.level.value == "L2"
        assert data["page"] == {"index": 2, "widthMm": 420, "heightMm": 297}
        assert data["sheet"] == {"id": "A-101", "title": "Plan", "scale": 50, "drawingType": "plan"}
        assert data["model"] == {"file": "model.ifc", "lengthUnit": "m", "schema": "IFC4"}
        assert [e["id"] for e in data["elements"]] == ["e-00", "e-01"]
        assert data["elements"][0]["tag"] == "Pos. 1"
        assert "tag" not in data["elements"][1]
        assert data["viewports"][0]["kind"] == "plan"
        assert data["viewports"][0]["paperBBox"] == [95, 130, 105, 135]

    def test_a_vertical_plane_is_a_section(self) -> None:
        """A view whose axes leave the horizontal is not a plan."""
        vertical = "[[1,0,0,0],[0,0,1,0],[0,-1,0,5],[0,0,0,1]]"
        body = f"<g class='section' ifc:plane='{vertical}' ifc:matrix3='{MATRIX3}'>{product()}</g>"
        label = derive_label(parse_svg(svg(body)), SOURCE)
        assert label.viewports is not None
        assert label.viewports[0].kind == "section"
        assert label.sheet.drawing_type == "section"

    def test_annotations_and_spatial_structure_are_not_elements(self) -> None:
        """They are drawn, but a label describes them otherwise or not at all."""
        body = view(
            product(ifc_class="IfcAnnotation")
            + product(guid="1" * 22, ifc_class="IfcGridAxis")
            + product(guid="2" * 22)
        )
        label = derive_label(parse_svg(svg(body)), SOURCE)
        assert [e.ifc_guid for e in label.elements or []] == ["2" * 22]

    def test_a_sheet_with_no_products_is_l1(self) -> None:
        """Still a valid label: the sheet is described, nothing on it is."""
        label = derive_label(parse_svg(svg("<rect width='1' height='1'/>")), SOURCE)
        assert label.viewports is None
        assert label.elements is None
        assert label.sheet.scale is None

    def test_a_product_outside_any_view_has_no_viewport(self) -> None:
        """And sheets with views at two scales have no single scale."""
        body = (
            product(guid="1" * 22)
            + view(product())
            + view(product(), transform="translate(200,12) scale(0.5)")
        )
        label = derive_label(parse_svg(svg(body)), SOURCE)
        assert label.elements is not None
        assert label.elements[0].viewport is None
        assert label.viewports is not None
        assert [v.scale for v in label.viewports] == [50, 100]
        assert label.sheet.scale is None


# ---------------------------------------------------------------------------
# Attaching, and the samples
# ---------------------------------------------------------------------------
class TestAttach:
    """The label goes onto the PDF drawn from the SVG, and only that PDF."""

    def test_a_pdf_of_another_size_is_refused(self, tmp_path: Path) -> None:
        """An A4 PDF is not a rendering of an A3 SVG."""
        page = tmp_path / "sheet.svg"
        page.write_bytes(svg(view(product()), root='width="210mm" height="297mm"'))
        pdf = tmp_path / "sheet.pdf"
        pdf.write_bytes(fx.build_drawing_set())
        with pytest.raises(LabelMismatchError, match="not a rendering"):
            attach_from_svg(page, pdf, tmp_path / "out.pdf", SOURCE, mod_date=MOD_DATE)

    def test_a_matching_pdf_is_labelled_and_validates(self, tmp_path: Path) -> None:
        """The two-page drawing set's first page is A3."""
        page = tmp_path / "sheet.svg"
        page.write_bytes(svg(view(product())))
        pdf = tmp_path / "sheet.pdf"
        pdf.write_bytes(fx.build_drawing_set())
        out = tmp_path / "out.pdf"
        label = attach_from_svg(page, pdf, out, SOURCE, mod_date=MOD_DATE)
        assert label.elements is not None
        assert validate(out).error_count == 0

    @pytest.mark.skipif(
        not (SAMPLES / "floorplan" / "sheet.svg").is_file(),
        reason="samples are not built; run make samples",
    )
    @pytest.mark.parametrize("name", NAMES)
    def test_each_sample_svg_gives_back_what_the_exporter_wrote(
        self, name: str, tmp_path: Path
    ) -> None:
        """Same elements, same boxes, same transform: from the SVG, not the model."""
        authored = json.loads((SAMPLES / name / "labels.json").read_text("utf-8"))
        source = SheetSource(sheet_id=authored["sheet"]["id"], unit_scale_to_m=1.0, length_unit="m")
        out = tmp_path / "out.pdf"
        label = attach_from_svg(
            SAMPLES / name / "sheet.svg",
            SAMPLES / name / "sheet.pdf",
            out,
            source,
            mod_date=MOD_DATE,
        )
        derived = json.loads(canonical_json(label))
        by_guid = {e["ifcGuid"]: e for e in derived["elements"]}
        for element in authored["elements"]:
            mine = by_guid[element["ifcGuid"]]
            assert mine["ifcClass"] == element["ifcClass"]
            assert mine["paperBBox"] == element["paperBBox"]
        assert len(by_guid) == len(authored["elements"])
        assert derived["viewports"][0]["paperToPlane"] == pytest.approx(
            authored["viewports"][0]["paperToPlane"]
        )
        assert derived["viewports"][0]["scale"] == authored["viewports"][0]["scale"]
        assert validate(out).error_count == 0


def test_bounding_box_of_a_circle_is_close() -> None:
    """Flattening a circle into 32 sides loses under 0.5 % of its radius."""
    assert 1 - math.cos(math.pi / 32) < 0.005


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------
class TestFromSvgCommand:
    """``plannotation from-svg``: the same path, outside any authoring tool."""

    @staticmethod
    def _files(tmp_path: Path) -> tuple[Path, Path]:
        """Write an A3 sheet SVG and a PDF whose first page is A3.

        Args:
            tmp_path: Where to write.

        Returns:
            The SVG and the PDF.
        """
        page = tmp_path / "sheet.svg"
        page.write_bytes(svg(view(product())))
        pdf = tmp_path / "sheet.pdf"
        pdf.write_bytes(fx.build_drawing_set())
        return page, pdf

    @pytest.mark.parametrize(("unit", "scale"), [("m", 0.05), ("mm", 50.0)])
    def test_the_unit_scales_the_transform(self, tmp_path: Path, unit: str, scale: float) -> None:
        """Without a model, ``--unit`` says what the model plane is measured in."""
        from typer.testing import CliRunner

        from plannotation.cli import app

        page, pdf = self._files(tmp_path)
        out = tmp_path / "out.pdf"
        result = CliRunner().invoke(
            app,
            [
                "from-svg",
                str(page),
                str(pdf),
                "-o",
                str(out),
                "--sheet-id",
                "A-101",
                "--unit",
                unit,
                "--mod-date",
                "2024-01-01T00:00:00+00:00",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        label = json.loads(result.stdout)
        assert label["viewports"][0]["paperToPlane"][0] == scale
        assert label["model"]["lengthUnit"] == unit
        assert out.is_file()

    def test_a_mismatch_is_an_error(self, tmp_path: Path) -> None:
        """Exit 1, with the reason."""
        from typer.testing import CliRunner

        from plannotation.cli import app

        page, pdf = self._files(tmp_path)
        page.write_bytes(svg(view(product()), root='width="210mm" height="297mm"'))
        result = CliRunner().invoke(
            app, ["from-svg", str(page), str(pdf), "-o", str(tmp_path / "o.pdf"), "--sheet-id", "X"]
        )
        assert result.exit_code == 1
        assert "not a rendering" in result.output

    @pytest.mark.skipif(
        not (SAMPLES / "floorplan" / "model.ifc").is_file(),
        reason="samples are not built; run make samples",
    )
    def test_the_model_supplies_units_schema_and_marks(self, tmp_path: Path) -> None:
        """What the SVG does not carry, the model does."""
        pytest.importorskip("ifcopenshell")
        from typer.testing import CliRunner

        from plannotation.cli import app

        out = tmp_path / "out.pdf"
        result = CliRunner().invoke(
            app,
            [
                "from-svg",
                str(SAMPLES / "floorplan" / "sheet.svg"),
                str(SAMPLES / "floorplan" / "sheet.pdf"),
                "-o",
                str(out),
                "--sheet-id",
                "ARC-101",
                "--ifc",
                str(SAMPLES / "floorplan" / "model.ifc"),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "level L2" in result.stdout
        label = next(iter(read_pdf_labels(out).values()))
        assert label.source_model is not None
        assert label.source_model.ifc_schema == "IFC4"
        marks = {e.tag for e in label.elements or []}
        assert marks == {f"Pos. {n}" for n in range(1, 6)} | {"T1", "T2", "W1", "W2"}


def read_pdf_labels(path: Path) -> dict[int, PageLabel]:
    """Read the labels a PDF carries.

    Args:
        path: The PDF.

    Returns:
        Its labels by page.
    """
    from plannotation.pdf import embed

    return dict(embed.read(path).pages)


class TestModelSource:
    """Units and marks from the model, and the units Plannotation can name."""

    @pytest.mark.parametrize(("scale", "name"), [(1.0, "m"), (0.01, "cm"), (0.001, "mm")])
    def test_metric_units_are_named(self, scale: float, name: str) -> None:
        """The three a label can state."""
        from plannotation.units import length_unit_for

        assert length_unit_for(scale) == name

    def test_feet_are_refused(self) -> None:
        """Plannotation 0.1 has no word for them."""
        from plannotation.units import length_unit_for

        with pytest.raises(ValueError, match="metres, centimetres or millimetres"):
            length_unit_for(0.3048)

    def test_without_the_extra_the_message_names_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``pip install 'plannotation[ifc]'`` is the fix."""
        import importlib

        from plannotation.svg.label import source_from_ifc

        def missing(name: str) -> object:
            raise ImportError(name)

        monkeypatch.setattr(importlib, "import_module", missing)
        with pytest.raises(CarrierError, match=r"plannotation\[ifc\]"):
            source_from_ifc(Path("model.ifc"), sheet_id="A-101")
