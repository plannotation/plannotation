# SPDX-License-Identifier: Apache-2.0
"""Inference, measured against its gate.

The gate is recall on the three sample drawings with their plannotations stripped: at
least 90% of tags, 80% of dimensions and every grid, with precision reported. It is
measured here against the *unplannotated* PDFs, so that nothing can be read off an
attached plannotation; the authored ``plannotations.json`` beside each is only the
answer key.

Recall alone is easy to game -- call every word a tag and every tag is found -- so
precision is asserted too, and the patterns are tested on their own so that a regression
in the vocabulary shows up as itself rather than as a falling recall number.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from plannotation.infer.evaluate import GATE
from plannotation.infer.match_ifc import normalise_mark
from plannotation.infer.patterns import (
    GRID_AXIS,
    REVISION,
    SCALE,
    SHEET_ID,
    parse_dimension,
    parse_level,
    tag_family,
)

SAMPLES = Path(__file__).parent.parent / "samples"
NAMES = ("floorplan", "positionsplan", "section")


def has(module: str) -> bool:
    """Report whether an optional module is importable.

    Args:
        module: The module name.

    Returns:
        True when it is.
    """
    return importlib.util.find_spec(module) is not None


needs_samples = pytest.mark.skipif(
    not all((SAMPLES / name / "sheet.pdf").exists() for name in NAMES),
    reason="samples are not built; run make samples",
)


class TestTheVocabulary:
    """The patterns are the part of inference that a drawing office's habits break."""

    @pytest.mark.parametrize("text", ["ARC-101", "TWP-201", "S-12", "TGA-104A"])
    def test_sheet_numbers(self, text: str) -> None:
        """Letters, a dash, digits: the shape nearly every office uses."""
        assert SHEET_ID.match(text)

    @pytest.mark.parametrize("text", ["arc-101", "ARC101", "A-1", "Pos. 3"])
    def test_things_that_are_not_sheet_numbers(self, text: str) -> None:
        """A tag is not a sheet number, which the title block depends on."""
        assert not SHEET_ID.match(text)

    @pytest.mark.parametrize(
        ("text", "denominator"),
        [("1:50", "50"), ("M 1:50", "50"), ("M1:100", "100"), ("Maßstab 1:20", "20")],
    )
    def test_scales(self, text: str, denominator: str) -> None:
        """German and English spellings, with and without the M."""
        match = SCALE.match(text)
        assert match is not None
        assert match.group(1) == denominator

    @pytest.mark.parametrize(
        ("text", "index"), [("Index A", "A"), ("IndexA", "A"), ("Rev. C", "C")]
    )
    def test_revisions(self, text: str, index: str) -> None:
        """The PDF reader drops the space in "Index A" as often as it keeps it."""
        match = REVISION.match(text)
        assert match is not None
        assert match.group(1) == index

    @pytest.mark.parametrize(
        ("text", "value"),
        [("±0,00", 0.0), ("+3,00", 3.0), ("-0,25", -0.25), ("+12.500", 12.5)],
    )
    def test_levels_carry_their_sign(self, text: str, value: float) -> None:
        """The sign is what tells a level from a dimension."""
        assert parse_level(text) == value

    @pytest.mark.parametrize("text", ["3,00", "3000", "+3", "Pos. 3"])
    def test_things_that_are_not_levels(self, text: str) -> None:
        """An unsigned number is a dimension, and a level has two decimals."""
        assert parse_level(text) is None

    def test_a_level_is_not_a_dimension(self) -> None:
        """Otherwise every level mark would be counted twice."""
        assert parse_dimension("+3,00") is None

    @pytest.mark.parametrize(
        ("text", "value"), [("8000", 8000.0), ("2,50", 2.5), ("2.50", 2.5), ("12", 12.0)]
    )
    def test_dimensions_read_the_decimal_comma(self, text: str, value: float) -> None:
        """A German drawing writes two and a half as 2,50."""
        assert parse_dimension(text) == value

    @pytest.mark.parametrize("text", ["A-1", "Pos.3", "", "1:50"])
    def test_things_that_are_not_dimensions(self, text: str) -> None:
        """A scale is not a dimension, nor is a mark."""
        assert parse_dimension(text) is None

    @pytest.mark.parametrize(
        ("text", "ifc_class"),
        [
            ("Pos.3", "IfcBuildingElement"),
            ("Pos. 12", "IfcBuildingElement"),
            ("St.4", "IfcColumn"),
            ("UZ-1", "IfcBeam"),
            ("W-2", "IfcWindow"),
        ],
    )
    def test_mark_families_imply_a_class(self, text: str, ifc_class: str) -> None:
        """A guess from the mark's prefix, carried with a confidence to match."""
        family = tag_family(text)
        assert family is not None
        assert family[0] == ifc_class
        assert 0.0 < family[1] < 1.0

    def test_a_trailing_newline_is_not_part_of_a_mark(self) -> None:
        """The dollar-anchor bug Phase 2 found, which this vocabulary is built to avoid."""
        assert tag_family("Pos. 3\n") is not None
        assert not GRID_AXIS.match("A\n")

    def test_marks_normalise_across_whitespace(self) -> None:
        """The drawing prints Pos.3; the model stores Pos. 3; they are one mark."""
        assert normalise_mark("Pos.3") == normalise_mark("Pos. 3") == normalise_mark(" pos 3 ")


@needs_samples
class TestTheGate:
    """Recall on the stripped samples, and precision beside it."""

    @staticmethod
    def _scores() -> dict[str, list[int]]:
        """Score inference on every sample, totalled by category.

        Returns:
            ``[expected, found, correct]`` for each category.
        """
        from plannotation.infer import infer_plannotations
        from plannotation.infer.evaluate import score
        from plannotation.model import load_plannotation

        totals: dict[str, list[int]] = {}
        for name in NAMES:
            authored = load_plannotation((SAMPLES / name / "plannotations.json").read_text("utf-8"))
            [inferred], _ = infer_plannotations(SAMPLES / name / "sheet.pdf")
            for item in score(authored, inferred):
                total = totals.setdefault(item.category, [0, 0, 0])
                total[0] += item.expected
                total[1] += item.found
                total[2] += item.correct
        return totals

    @pytest.mark.parametrize(("category", "floor"), sorted(GATE.items()))
    def test_recall_meets_the_gate(self, category: str, floor: float) -> None:
        """90% of tags, 80% of dimensions, every grid and every level."""
        expected, _, correct = self._scores()[category]
        assert correct / expected >= floor

    @pytest.mark.parametrize("category", ["tag", "dimension", "grid", "level", "callout"])
    def test_precision_is_high_too(self, category: str) -> None:
        """Recall alone is gamed by reporting everything; precision is what says no."""
        _, found, correct = self._scores()[category]
        assert found
        assert correct / found >= 0.9

    def test_the_title_block_is_read(self) -> None:
        """Sheet number, scale, revision and drawing type, on every sheet."""
        expected, _, correct = self._scores()["sheet"]
        assert correct == expected

    def test_the_sheet_number_is_not_the_callout_target(self) -> None:
        """A callout prints another sheet's number in the corner a title block sits in.

        Reading it as this sheet's own number named the wrong drawing, on the floor
        plan, and that is the defect this guards.
        """
        from plannotation.infer import infer_plannotations

        [inferred], _ = infer_plannotations(SAMPLES / "floorplan" / "sheet.pdf")
        assert inferred.sheet.sheet_id == "ARC-101"


@needs_samples
class TestWhatInferenceWrites:
    """SPEC 4.6: everything reconstructed says so."""

    @staticmethod
    def _plannotation(name: str = "positionsplan") -> Any:  # noqa: ANN401
        """Infer one sample's plannotation.

        Args:
            name: The sample.

        Returns:
            The inferred plannotation.
        """
        from plannotation.infer import infer_plannotations

        [plannotation], _ = infer_plannotations(SAMPLES / name / "sheet.pdf")
        return plannotation

    def test_the_plannotation_is_inferred(self) -> None:
        """Top-level provenance, and it must not claim to be authored."""
        from plannotation.model import Provenance

        assert self._plannotation().provenance is Provenance.INFERRED

    def test_every_item_is_inferred_with_a_confidence(self) -> None:
        """SPEC 4.6.5: an inferred value that will not say how sure it is withholds the point."""
        plannotation = self._plannotation()
        items = [*(plannotation.elements or []), *(plannotation.annotations or [])]
        assert items
        for item in items:
            assert item.provenance.value == "inferred"
            assert item.confidence is not None
            assert 0.0 <= item.confidence <= 1.0

    @pytest.mark.parametrize("name", NAMES)
    def test_dimensions_link_to_the_grids_they_span(self, name: str) -> None:
        """So a dimension is a statement about the building, not a number beside a line."""
        plannotation = self._plannotation(name)
        dimensions = [a for a in plannotation.annotations or [] if a.annotation_type == "dimension"]
        assert dimensions
        assert all(d.measures and len(d.measures) == 2 for d in dimensions)

    def test_an_overall_dimension_printed_across_a_grid_line_still_links(self) -> None:
        """11400 prints across grid B; the grid's line is not the dimension's.

        Taking the nearest line to the value took grid B's, and the overall dimension
        measured nothing. Grid and level lines are now never a dimension's own.
        """
        plannotation = self._plannotation("positionsplan")
        by_id = {a.local_id: a for a in plannotation.annotations or []}
        overall = next(a for a in plannotation.annotations or [] if a.text == "11400")
        assert sorted(by_id[end].axis for end in overall.measures or []) == ["A", "C"]

    def test_each_bay_of_a_chain_links_to_its_own_grids(self) -> None:
        """Not the whole chain's first and last: 1-2, 2-3 and 3-4 on the floor plan."""
        plannotation = self._plannotation("floorplan")
        by_id = {a.local_id: a for a in plannotation.annotations or []}
        spans = sorted(
            "".join(sorted(str(by_id[end].axis) for end in a.measures or []))
            for a in plannotation.annotations or []
            if a.annotation_type == "dimension" and a.text == "2000"
        )
        assert spans == ["12", "23", "34"]

    def test_levels_are_read_and_storey_heights_link_to_them(self) -> None:
        """A section's storey heights run between level lines, and say so."""
        plannotation = self._plannotation("section")
        levels = {
            a.local_id: a for a in plannotation.annotations or [] if a.annotation_type == "level"
        }
        assert sorted(level.elevation for level in levels.values()) == [0.0, 3.0, 6.0]
        heights = [
            a
            for a in plannotation.annotations or []
            if a.annotation_type == "dimension" and set(a.measures or []) <= set(levels)
        ]
        assert sorted(a.value for a in heights) == [3000.0, 3000.0, 6000.0]
        for height in heights:
            low, high = sorted(levels[end].elevation for end in height.measures or [])
            assert (high - low) * 1000.0 == height.value

    @pytest.mark.parametrize("name", NAMES)
    def test_what_it_writes_validates_clean(self, name: str, tmp_path: Path) -> None:
        """An inferred plannotation must still satisfy every rule the validator checks."""
        from plannotation.infer import infer_document
        from plannotation.validate import validate

        out = tmp_path / "inferred.pdf"
        infer_document(SAMPLES / name / "sheet.pdf", out, mod_date=datetime(2024, 1, 1, tzinfo=UTC))
        assert list(validate(out).findings) == []

    def test_it_never_modifies_its_input(self, tmp_path: Path) -> None:
        """The input is never modified: a tool that edits the only copy is untriable."""
        from plannotation.infer import infer_document

        source = SAMPLES / "floorplan" / "sheet.pdf"
        before = source.read_bytes()
        infer_document(source, tmp_path / "out.pdf", mod_date=datetime(2024, 1, 1, tzinfo=UTC))
        assert source.read_bytes() == before

    def test_it_refuses_to_write_over_its_input(self, tmp_path: Path) -> None:
        """The same promise, enforced rather than hoped for."""
        from plannotation.infer import infer_document

        copy = tmp_path / "sheet.pdf"
        copy.write_bytes((SAMPLES / "floorplan" / "sheet.pdf").read_bytes())
        with pytest.raises(ValueError, match="never modifies its input"):
            infer_document(copy, copy)


@needs_samples
@pytest.mark.skipif(not has("ifcopenshell"), reason="ifcopenshell is not installed")
class TestMatchingToTheModel:
    """--ifc turns a guessed class into the model's own, and adds the GlobalId."""

    @pytest.mark.parametrize("name", NAMES)
    def test_every_mark_recovers_its_global_id(self, name: str) -> None:
        """Checked against the authored plannotation, which the exporter wrote from the model."""
        from plannotation.infer import infer_plannotations
        from plannotation.model import load_plannotation

        authored = load_plannotation((SAMPLES / name / "plannotations.json").read_text("utf-8"))
        truth = {
            normalise_mark(e.tag or ""): (e.ifc_guid, e.ifc_class) for e in authored.elements or []
        }
        [inferred], matched = infer_plannotations(
            SAMPLES / name / "sheet.pdf", ifc_model=SAMPLES / name / "model.ifc"
        )
        assert matched == len(truth)
        for element in inferred.elements or []:
            assert truth[normalise_mark(element.tag or "")] == (element.ifc_guid, element.ifc_class)

    def test_a_match_stays_inferred(self) -> None:
        """SPEC 4.6.6: however confident, reconstructed data is never promoted to authored."""
        from plannotation.infer import infer_plannotations

        [inferred], _ = infer_plannotations(
            SAMPLES / "floorplan" / "sheet.pdf", ifc_model=SAMPLES / "floorplan" / "model.ifc"
        )
        for element in inferred.elements or []:
            assert element.provenance.value == "inferred"


@pytest.mark.skipif(
    not (Path(__file__).parent / "fixtures" / "realworld").is_dir(),
    reason="no real-world PDFs in tests/fixtures/realworld (git-ignored by design)",
)
def test_real_world_drawings_do_not_crash_it() -> None:
    """No crash on real drawings, which are never committed."""
    from plannotation.infer import infer_plannotations

    documents = sorted((Path(__file__).parent / "fixtures" / "realworld").glob("*.pdf"))
    if not documents:
        pytest.skip("tests/fixtures/realworld holds no PDFs")
    for document in documents:
        infer_plannotations(document)
