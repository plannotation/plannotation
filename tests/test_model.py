# SPDX-License-Identifier: Apache-2.0
"""The pydantic models, the canonical serialiser, and the rules of SPEC §4.1, §4.5, §4.6.

These tests use documents built in the test rather than fixture files, so that a
rule and its counter-example sit next to each other and a failure names the rule.
Fixture-driven coverage lives in ``test_fixtures.py``.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from pydantic import JsonValue, ValidationError

from plannotation.constants import SCHEMA_VERSION
from plannotation.model import (
    ConformanceLevel,
    Element,
    Plannotation,
    Provenance,
    aggregate_provenance,
    canonical_bytes,
    canonical_json,
    conformance_level,
    index_schema,
    load_plannotation,
    page_schema,
)

PAGE = {"index": 0, "widthMm": 420, "heightMm": 297}
SHEET = {"id": "A-101"}


def plannotation(**extra: JsonValue) -> dict[str, Any]:
    """Build a minimal valid plannotation document.

    Args:
        **extra: Members merged into the document, overriding defaults.

    Returns:
        A dictionary ready to be serialised or loaded.
    """
    return {
        "plannotation": SCHEMA_VERSION,
        "provenance": "authored",
        "page": dict(PAGE),
        "sheet": dict(SHEET),
        **extra,
    }


def element(**extra: JsonValue) -> dict[str, Any]:
    """Build a minimal valid element.

    Args:
        **extra: Members merged into the element.

    Returns:
        An element dictionary.
    """
    return {"id": "e1", "ifcClass": "IfcWall", "paperBBox": [10, 10, 20, 20], **extra}


def load(doc: dict[str, Any]) -> Plannotation:
    """Load a document through the public loader.

    Args:
        doc: The document.

    Returns:
        The parsed :class:`Plannotation`.
    """
    return load_plannotation(json.dumps(doc))


class TestCanonicalSerialisation:
    """Canonical form is what the byte-for-byte gate and Phase 2 checksums rest on."""

    def test_round_trip_is_byte_for_byte(self) -> None:
        """Loading a canonical document and re-emitting it reproduces the bytes."""
        text = canonical_json(load(plannotation()))
        assert canonical_json(load_plannotation(text)) == text

    def test_round_trip_is_a_fixed_point(self) -> None:
        """Serialising twice changes nothing the second time."""
        once = canonical_json(load(plannotation()))
        twice = canonical_json(load_plannotation(once))
        assert once == twice

    def test_keys_are_sorted(self) -> None:
        """Sorted keys are what makes two writers produce the same bytes."""
        emitted = json.loads(canonical_json(load(plannotation())))
        assert list(emitted) == sorted(emitted)

    def test_two_space_indent_lf_and_one_trailing_newline(self) -> None:
        """The serialised form is stable across platforms."""
        text = canonical_json(load(plannotation()))
        assert "\r" not in text
        assert text.endswith("\n")
        assert not text.endswith("\n\n")
        assert '\n  "page"' in text

    def test_canonical_bytes_is_the_utf8_of_canonical_json(self) -> None:
        """Phase 2 embeds these exact bytes, so the two must not drift."""
        parsed = load(plannotation())
        assert canonical_bytes(parsed) == canonical_json(parsed).encode("utf-8")

    @pytest.mark.parametrize(
        ("written", "expected"),
        [
            (841.0, "841"),  # integral floats lose the spurious .0
            (-0.0, "0"),  # negative zero is folded away
            (1 / 3, "0.333"),  # truncated to the precision the format allows
            (2.5e-9, "0"),  # far below the precision, so it is simply zero
            (0.8125, "0.812"),  # an exact binary tie, resolved to even
            (0.0015, "0.002"),  # rounds up, and keeps exactly three decimals
        ],
    )
    def test_numbers_are_rounded_to_three_decimals(self, written: float, expected: str) -> None:
        """At most three decimals, round-to-nearest ties-to-even, as SPEC §3.8 requires.

        Fixing the precision is what makes byte-for-byte golden comparison possible,
        and fixing the tie-break is what makes two implementations agree on 0.8125.
        """
        doc = plannotation(elements=[element(paperBBox=[written, 0, 100, 100])])
        emitted = canonical_json(load(doc))
        first = re.search(r'"paperBBox": \[\s*([^,\s]+),', emitted)
        assert first is not None, emitted
        assert first.group(1) == expected

    def test_non_ascii_is_written_literally(self) -> None:
        """German terms appear in this format; escaping them helps nobody."""
        doc = plannotation(sheet={"id": "A-101", "title": "Maßstab 1:50 Übersicht"})
        assert "Maßstab" in canonical_json(load(doc))
        assert "\\u00df" not in canonical_json(load(doc))

    def test_absent_optionals_stay_absent(self) -> None:
        """A default is an annotation, not a value; materialising one breaks the trip."""
        emitted = json.loads(canonical_json(load(plannotation())))
        assert "rotation" not in emitted["page"]
        assert "generator" not in emitted


class TestSchemaFidelity:
    """The models mirror the schema; they may not be stricter about what they accept."""

    def test_unknown_member_is_rejected(self) -> None:
        """``additionalProperties: false`` means extras are an error, not a warning."""
        with pytest.raises(ValidationError):
            load(plannotation(unexpected="x"))

    @pytest.mark.parametrize("bbox", [[1, 2, 3], [1, 2, 3, 4, 5]])
    def test_bbox_arity_is_exact(self, bbox: list[int]) -> None:
        """A bbox is exactly ``[x0, y0, x1, y1]``."""
        with pytest.raises(ValidationError):
            load(plannotation(elements=[element(paperBBox=bbox)]))

    @pytest.mark.parametrize("value", [True, "420"])
    def test_a_number_is_not_a_bool_or_a_string(self, value: object) -> None:
        """``True`` is an int in Python; a JSON number is neither it nor a string."""
        with pytest.raises(ValidationError):
            load(plannotation(page={"index": 0, "widthMm": value, "heightMm": 297}))

    @pytest.mark.parametrize("bad", ["not-a-guid", "0123456789012345678901234567890"])
    def test_ifc_guid_pattern(self, bad: str) -> None:
        """A GlobalId is 22 characters of the IFC base64 alphabet."""
        with pytest.raises(ValidationError):
            load(plannotation(elements=[element(ifcGuid=bad)]))

    def test_extension_keys_must_be_namespaced(self) -> None:
        """Un-namespaced extension keys could collide with a future member."""
        with pytest.raises(ValidationError):
            load(plannotation(extensions={"vendor": 1}))

    @pytest.mark.parametrize(
        "bag",
        [
            {"Pset_WallCommon": {"FireRating": "REI90"}},
            {"Pset_WallCommon": None},
            {"FireRating": "REI90"},
        ],
    )
    def test_properties_is_as_permissive_as_the_schema(self, bag: dict[str, Any]) -> None:
        """The schema says only ``"type": "object"``.

        A reader stricter than the format would reject conforming third-party
        plannotations, which for an interoperability format is the worse failure.
        """
        assert load(plannotation(elements=[element(properties=bag)])) is not None

    def test_pset_gives_typed_access_to_the_described_shape(self) -> None:
        """Ergonomics live in an accessor rather than in the field's type."""
        el = Element(
            id="w1",
            ifcClass="IfcWall",
            paperBBox=[0, 0, 10, 10],
            properties={"Pset_WallCommon": {"FireRating": "REI90"}, "flat": "x"},
        )
        assert el.pset("Pset_WallCommon") == {"FireRating": "REI90"}
        assert el.pset("flat") is None
        assert el.pset("absent") is None

    @pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
    def test_json_has_no_nan_or_infinity(self, token: str) -> None:
        """Python's json accepts these by default; JSON itself does not."""
        with pytest.raises(ValueError, match=r"(?i)nan|infinit|valid"):
            load_plannotation(
                '{"plannotation":"0.1","provenance":"authored","sheet":{"id":"A"},'
                '"page":{"index":0,"widthMm":' + token + ',"heightMm":297}}'
            )


class TestProvenanceRules:
    """SPEC §4.6: the aggregation rule and the confidence obligation."""

    def test_explicit_inferred_requires_a_confidence(self) -> None:
        """An inferred value that will not say how sure it is withholds the point."""
        with pytest.raises(ValidationError):
            load(plannotation(provenance="inferred", elements=[element(provenance="inferred")]))

    @pytest.mark.parametrize(
        ("declared", "expected"),
        [
            ("authored", Provenance.AUTHORED),
            ("inferred", Provenance.INFERRED),
            ("mixed", Provenance.INFERRED),
        ],
    )
    def test_an_item_without_provenance_inherits_the_declaration(
        self, declared: str, expected: Provenance
    ) -> None:
        """The top-level value is load-bearing, not decorative.

        Under ``mixed`` the inherited value is ``inferred``: nothing in the format
        could say which half an unmarked item came from, and reading silence as
        ``authored`` would overstate what the writer knew.
        """
        parsed = load(plannotation(provenance=declared, elements=[element()]))
        assert parsed.aggregate_provenance is expected

    def test_a_wholly_inferred_plannotation_need_not_repeat_itself(self) -> None:
        """This is the case the inheritance rule exists for."""
        parsed = load(plannotation(provenance="inferred", elements=[element(), element(id="e2")]))
        assert parsed.provenance_is_consistent

    def test_authored_and_inferred_together_is_mixed(self) -> None:
        """Both kinds are items and are counted together."""
        doc = plannotation(
            provenance="mixed",
            elements=[
                element(provenance="authored"),
                element(id="e2", provenance="inferred", confidence=0.7),
            ],
        )
        assert load(doc).aggregate_provenance is Provenance.MIXED
        assert load(doc).provenance_is_consistent

    def test_an_item_less_plannotation_determines_nothing(self) -> None:
        """With no items there is no evidence either way, so the declaration stands.

        Reading it as ``inferred`` would force a title-block sheet written straight
        from the model to call itself reconstructed; reading it as ``authored`` would
        let one recovered from a legacy PDF claim it came from a model.
        """
        assert aggregate_provenance([]) is None
        assert load(plannotation(provenance="inferred")).provenance_is_consistent
        assert load(plannotation(provenance="authored")).provenance_is_consistent


class TestConformanceLevels:
    """SPEC §4.1: L1 page+sheet, L2 adds elements, L3 adds a linked annotation."""

    def test_minimal_plannotation_is_l1(self) -> None:
        """``page`` and ``sheet`` are schema-required, so every valid plannotation reaches L1."""
        assert conformance_level(load(plannotation())) is ConformanceLevel.L1

    def test_viewports_alone_do_not_reach_l2(self) -> None:
        """L1 admits viewports; elements are what L2 is about."""
        doc = plannotation(viewports=[{"id": "vp1", "kind": "plan", "paperBBox": [0, 0, 100, 100]}])
        assert conformance_level(load(doc)) is ConformanceLevel.L1

    def test_one_element_reaches_l2(self) -> None:
        """``ifcClass`` and ``paperBBox`` are required, so presence is the whole test."""
        assert conformance_level(load(plannotation(elements=[element()]))) is ConformanceLevel.L2

    def test_unlinked_annotations_stay_at_l2(self) -> None:
        """A north arrow links to nothing and cannot lift a sheet to L3."""
        doc = plannotation(
            elements=[element()],
            annotations=[{"id": "a1", "type": "northArrow", "paperBBox": [0, 0, 10, 10]}],
        )
        assert conformance_level(load(doc)) is ConformanceLevel.L2

    @pytest.mark.parametrize(
        "link",
        [
            {"measures": ["e1"]},
            {"shows": {"element": "e1"}},
            {"target": {"sheetId": "A-102"}},
            {"axis": "B"},
            {"ifcGuid": "0Xd3E$fGH1jKlMnOpQrStU"},
        ],
    )
    def test_any_one_link_reaches_l3(self, link: dict[str, Any]) -> None:
        """SPEC §4.1 lists five link kinds and any one of them counts."""
        doc = plannotation(
            elements=[element()],
            annotations=[{"id": "a1", "type": "dimension", "paperBBox": [0, 0, 10, 10], **link}],
        )
        assert conformance_level(load(doc)) is ConformanceLevel.L3

    @pytest.mark.parametrize("empty", [{"shows": {}}, {"target": {}}])
    def test_an_empty_link_is_not_a_link(self, empty: dict[str, Any]) -> None:
        """``shows: {}`` is schema-valid and refers to nothing."""
        doc = plannotation(
            elements=[element()],
            annotations=[{"id": "a1", "type": "tag", "paperBBox": [0, 0, 10, 10], **empty}],
        )
        assert conformance_level(load(doc)) is ConformanceLevel.L2


class TestIdentity:
    """Ids are resolved page-wide, so they must be unique page-wide."""

    def test_duplicate_id_across_collections_is_rejected(self) -> None:
        """``measures`` may name an element or a grid, so one namespace is required."""
        doc = plannotation(
            elements=[element(id="x1")],
            annotations=[{"id": "x1", "type": "grid", "paperBBox": [0, 0, 5, 5], "axis": "A"}],
        )
        with pytest.raises(ValidationError):
            load(doc)

    def test_distinct_ids_are_accepted(self) -> None:
        """The control case for the rule above."""
        doc = plannotation(
            elements=[element(id="x1")],
            annotations=[{"id": "x2", "type": "grid", "paperBBox": [0, 0, 5, 5], "axis": "A"}],
        )
        assert load(doc) is not None


class TestPackagedSchemas:
    """The models and the schemas ship together and must agree on identity."""

    def test_page_schema_loads(self) -> None:
        """Available from an installed wheel, with no filesystem assumptions."""
        assert page_schema()["properties"]["plannotation"]["const"] == SCHEMA_VERSION

    def test_index_schema_loads(self) -> None:
        """The document-level index schema ships alongside the page schema."""
        assert index_schema()["properties"]["plannotation"]["const"] == SCHEMA_VERSION

    def test_a_mutated_schema_does_not_poison_the_next_caller(self) -> None:
        """Jsonschema resolvers mutate what they are given, so each caller gets a copy."""
        page_schema()["properties"]["plannotation"]["const"] = "tampered"
        assert page_schema()["properties"]["plannotation"]["const"] == SCHEMA_VERSION
