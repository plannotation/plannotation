# SPDX-License-Identifier: Apache-2.0
"""The fixture corpus, driven by its own manifests.

Two corpora share one manifest shape and are therefore tested by one module:

``tests/fixtures/plannotations/``
    12 valid and 12 invalid plannotations -- the Phase 1 gate.

``tests/fixtures/index/``
    3 valid and 3 invalid document indexes.

A negative fixture must fail for exactly the one reason its manifest claims. A
fixture that happens to fail for a second, unintended reason is a useless
regression test: it would keep passing after the rule it was written for was
broken, so the error count is asserted, not merely that validation failed.

Geometric coherence -- that a dimension's value matches the length it draws at its
viewport's scale, that a level agrees with its own transform -- is checked by
``tools/check_fixtures.py``, which ``make check`` runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

import pytest
from jsonschema import Draft202012Validator

from plannotation.model import (
    canonical_json,
    conformance_level,
    index_schema,
    load_plannotation,
    load_plannotation_index,
    page_schema,
)

if TYPE_CHECKING:
    from collections.abc import Callable

FIXTURES = Path(__file__).parent / "fixtures"

EXPECTED_COUNTS = {"plannotations": (12, 12), "index": (3, 3)}


class Corpus(NamedTuple):
    """One fixture corpus and everything needed to check it."""

    name: str
    schema: dict[str, Any]
    loader: Callable[[str], object]


CORPORA = [
    Corpus("plannotations", page_schema(), load_plannotation),
    Corpus("index", index_schema(), load_plannotation_index),
]


def manifest(corpus: str) -> dict[str, Any]:
    """Read a corpus manifest.

    Args:
        corpus: Directory name under ``tests/fixtures``.

    Returns:
        The parsed manifest.
    """
    parsed: dict[str, Any] = json.loads((FIXTURES / corpus / "manifest.json").read_text("utf-8"))
    return parsed


def cases(corpus: str, bucket: str) -> list[Any]:
    """Return manifest entries as pytest parameters, labelled by filename.

    Args:
        corpus: Directory name under ``tests/fixtures``.
        bucket: Either ``valid`` or ``invalid``.

    Returns:
        A list of ``pytest.param`` values.
    """
    return [pytest.param(e, id=f"{corpus}/{e['file']}") for e in manifest(corpus)[bucket]]


def text_of(corpus: str, entry: dict[str, Any]) -> str:
    """Read a fixture's raw text.

    Args:
        corpus: Directory name under ``tests/fixtures``.
        entry: A manifest entry.

    Returns:
        The file's contents.
    """
    return (FIXTURES / corpus / entry["file"]).read_text("utf-8")


ALL_VALID = [(c, e) for c in CORPORA for e in manifest(c.name)["valid"]]
ALL_INVALID = [(c, e) for c in CORPORA for e in manifest(c.name)["invalid"]]
VALID_PARAMS = [pytest.param(c, e, id=f"{c.name}/{e['file']}") for c, e in ALL_VALID]
INVALID_PARAMS = [pytest.param(c, e, id=f"{c.name}/{e['file']}") for c, e in ALL_INVALID]


class TestManifestsMatchDisk:
    """A manifest that has drifted from the files is worse than none."""

    @pytest.mark.parametrize("corpus", ["plannotations", "index"])
    def test_counts(self, corpus: str) -> None:
        """The Phase 1 gate fixes the plannotation counts at 12 and 12."""
        expected_valid, expected_invalid = EXPECTED_COUNTS[corpus]
        man = manifest(corpus)
        assert len(man["valid"]) == expected_valid
        assert len(man["invalid"]) == expected_invalid

    @pytest.mark.parametrize("corpus", ["plannotations", "index"])
    @pytest.mark.parametrize("bucket", ["valid", "invalid"])
    def test_no_orphans_either_way(self, corpus: str, bucket: str) -> None:
        """Every file is listed and every listing exists."""
        on_disk = {f"{bucket}/{p.name}" for p in (FIXTURES / corpus / bucket).glob("*.json")}
        listed = {e["file"] for e in manifest(corpus)[bucket]}
        assert on_disk == listed

    @pytest.mark.parametrize("corpus", ["plannotations", "index"])
    def test_schema_path_is_real(self, corpus: str) -> None:
        """The manifest names the schema its fixtures were written against."""
        assert (Path(manifest(corpus)["schema"])).is_file()


class TestValidFixtures:
    """Positive fixtures validate, round-trip, and are canonically formatted."""

    @pytest.mark.parametrize(("corpus", "entry"), VALID_PARAMS)
    def test_validates_with_no_errors(self, corpus: Corpus, entry: dict[str, Any]) -> None:
        """A positive fixture must be schema-clean."""
        doc = json.loads(text_of(corpus.name, entry))
        errors = sorted(Draft202012Validator(corpus.schema).iter_errors(doc), key=str)
        assert not errors, [f"{e.json_path}: {e.message}" for e in errors]

    @pytest.mark.parametrize(("corpus", "entry"), VALID_PARAMS)
    def test_round_trips_byte_for_byte(self, corpus: Corpus, entry: dict[str, Any]) -> None:
        """The Phase 1 gate: canonical in, canonical out, identical bytes."""
        text = text_of(corpus.name, entry)
        assert canonical_json(corpus.loader(text)) == text  # type: ignore[arg-type]

    @pytest.mark.parametrize(("corpus", "entry"), VALID_PARAMS)
    def test_is_canonically_formatted(self, corpus: Corpus, entry: dict[str, Any]) -> None:
        """Sorted keys, LF, exactly one trailing newline, at most 3 decimals."""
        text = text_of(corpus.name, entry)
        assert "\r" not in text
        assert text.endswith("\n")
        assert not text.endswith("\n\n")
        doc = json.loads(text)
        assert list(doc) == sorted(doc)

    @pytest.mark.parametrize(("corpus", "entry"), VALID_PARAMS)
    def test_no_number_exceeds_three_decimals(self, corpus: Corpus, entry: dict[str, Any]) -> None:
        """Precision is fixed so that golden comparison is possible."""
        offenders = [
            token
            for token in text_of(corpus.name, entry).replace(",", " ").split()
            if "." in token
            and token.replace("-", "").replace(".", "").isdigit()
            and len(token.split(".")[1]) > 3
        ]
        assert not offenders, offenders

    @pytest.mark.parametrize("entry", cases("plannotations", "valid"))
    def test_conformance_level_matches_the_manifest(self, entry: dict[str, Any]) -> None:
        """The level a fixture claims is the level the rules actually yield."""
        plannotation = load_plannotation(text_of("plannotations", entry))
        assert conformance_level(plannotation).value == entry["level"]

    @pytest.mark.parametrize("entry", cases("plannotations", "valid"))
    def test_filename_matches_the_level(self, entry: dict[str, Any]) -> None:
        """The filename says the level, so a mismatch is visible in a directory listing."""
        assert entry["level"].lower() in entry["file"].lower()

    def test_every_conformance_level_is_represented(self) -> None:
        """A corpus that never reaches L3 would not test the level rules at all."""
        levels = {e["level"] for e in manifest("plannotations")["valid"]}
        assert levels == {"L1", "L2", "L3"}


class TestInvalidFixtures:
    """Negative fixtures fail, once, for the reason they claim."""

    @pytest.mark.parametrize(("corpus", "entry"), INVALID_PARAMS)
    def test_fails_with_exactly_one_error(self, corpus: Corpus, entry: dict[str, Any]) -> None:
        """Exactly one, so the fixture keeps testing the rule it was written for.

        A second unintended violation would keep the fixture failing even after the
        rule it targets was broken.
        """
        doc = json.loads(text_of(corpus.name, entry))
        errors = list(Draft202012Validator(corpus.schema).iter_errors(doc))
        assert len(errors) == 1, [f"{e.json_path}: {e.message}" for e in errors]

    @pytest.mark.parametrize(("corpus", "entry"), INVALID_PARAMS)
    def test_fails_on_the_claimed_keyword_and_path(
        self, corpus: Corpus, entry: dict[str, Any]
    ) -> None:
        """The manifest is documentation, so it has to be true."""
        doc = json.loads(text_of(corpus.name, entry))
        error = next(iter(Draft202012Validator(corpus.schema).iter_errors(doc)))
        assert error.validator == entry["keyword"]
        assert error.json_path == entry["errorPath"]

    @pytest.mark.parametrize("corpus", ["plannotations", "index"])
    def test_each_negative_targets_a_distinct_rule(self, corpus: str) -> None:
        """Twelve fixtures all testing `required` would be one fixture twelve times."""
        entries = manifest(corpus)["invalid"]
        pointers = {e["schemaPointer"] for e in entries}
        assert len(pointers) == len(entries)

    def test_negative_keyword_coverage_is_broad(self) -> None:
        """The plannotation negatives span the schema's keyword families, not one or two."""
        keywords = {e["keyword"] for e in manifest("plannotations")["invalid"]}
        # `type` and `minimum` were added deliberately: `type` has the most sites in
        # the schema and a wrong JSON type is the likeliest real exporter bug.
        assert {"type", "minimum", "const", "required", "enum", "pattern"} <= keywords


class TestCorporaAgree:
    """The index fixtures describe the plannotation fixtures, so the two must not drift."""

    def test_index_levels_match_the_plannotations_they_describe(self) -> None:
        """A level recorded in an index is the level that plannotation actually reaches."""
        by_sheet = {
            load_plannotation(text_of("plannotations", e)).sheet.sheet_id: conformance_level(
                load_plannotation(text_of("plannotations", e))
            ).value
            for e in manifest("plannotations")["valid"]
        }
        checked = 0
        for entry in manifest(
            "index",
        )["valid"]:
            for page in json.loads(text_of("index", entry))["pages"]:
                if page["sheetId"] in by_sheet:
                    assert page["level"] == by_sheet[page["sheetId"]], page["sheetId"]
                    checked += 1
        assert checked, "no index entry referred to a known plannotation fixture"
