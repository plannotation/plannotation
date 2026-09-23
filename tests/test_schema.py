# SPDX-License-Identifier: Apache-2.0
"""The packaged JSON Schemas are the single source of truth, so they are tested first.

These tests deliberately do not import :mod:`plannotation.model`. The schema must stand
on its own for a third-party reader that has no Python at all, and testing it through
the models would hide a schema defect behind a matching model defect.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from plannotation.constants import INDEX_SCHEMA_ID, SCHEMA_ID, SCHEMA_VERSION

SCHEMA_FILES = ["plannotation-0.1.json", "plannotation-index-0.1.json"]


def load_schema(filename: str) -> dict[str, Any]:
    """Load a packaged schema through importlib.resources.

    Args:
        filename: Bare filename inside the ``plannotation.schema`` package.

    Returns:
        The parsed schema document.
    """
    text = (resources.files("plannotation.schema") / filename).read_text(encoding="utf-8")
    parsed: dict[str, Any] = json.loads(text)
    return parsed


def read_schema_text(filename: str) -> str:
    """Return the raw text of a packaged schema.

    Args:
        filename: Bare filename inside the ``plannotation.schema`` package.

    Returns:
        The file's contents, decoded as UTF-8.
    """
    return (resources.files("plannotation.schema") / filename).read_text(encoding="utf-8")


def collect_refs(node: object, found: set[str]) -> None:
    """Collect every ``$ref`` value reachable from ``node``.

    Args:
        node: Any node of a parsed schema document.
        found: Accumulator, mutated in place.
    """
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            found.add(ref)
        for value in node.values():
            collect_refs(value, found)
    elif isinstance(node, list):
        for value in node:
            collect_refs(value, found)


@pytest.mark.parametrize("filename", SCHEMA_FILES)
class TestSchemaDocuments:
    """Every packaged schema is a well-formed, self-consistent JSON Schema."""

    def test_ships_as_package_data(self, filename: str) -> None:
        """The schema is readable from the installed package, not just the source tree.

        A validator has to work from a wheel with no network and no repository, so this
        must go through importlib.resources rather than a filesystem path.
        """
        assert read_schema_text(filename).strip(), f"{filename} is empty"

    def test_is_a_valid_json_schema(self, filename: str) -> None:
        """The document validates against the draft it declares."""
        schema = load_schema(filename)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        Draft202012Validator.check_schema(schema)

    def test_every_ref_resolves(self, filename: str) -> None:
        """No ``$ref`` points at a definition that does not exist."""
        schema = load_schema(filename)
        refs: set[str] = set()
        collect_refs(schema, refs)
        defs = set(schema.get("$defs", {}))
        dangling = [
            r
            for r in refs
            if not r.startswith("#/$defs/") or r.removeprefix("#/$defs/") not in defs
        ]
        assert not dangling, f"dangling $refs in {filename}: {dangling}"

    def test_no_unreferenced_definitions(self, filename: str) -> None:
        """A ``$defs`` entry nothing points at is dead weight and probably a mistake."""
        schema = load_schema(filename)
        refs: set[str] = set()
        collect_refs(schema, refs)
        used = {r.removeprefix("#/$defs/") for r in refs}
        unused = sorted(set(schema.get("$defs", {})) - used)
        assert not unused, f"unreferenced $defs in {filename}: {unused}"

    def test_version_const_matches_the_package(self, filename: str) -> None:
        """The ``plannotation`` const and the package's SCHEMA_VERSION cannot drift apart."""
        assert load_schema(filename)["properties"]["plannotation"]["const"] == SCHEMA_VERSION

    def test_is_canonically_formatted(self, filename: str) -> None:
        """Two-space indent, LF endings, exactly one trailing newline, no trailing spaces."""
        text = read_schema_text(filename)
        assert "\r" not in text, "CRLF line endings"
        assert text.endswith("\n"), "missing trailing newline"
        assert not text.endswith("\n\n"), "more than one trailing newline"
        lines = text.splitlines()
        assert not [ln for ln in lines if ln != ln.rstrip()], "trailing whitespace"
        indents = {len(ln) - len(ln.lstrip(" ")) for ln in lines if ln.startswith(" ")}
        assert all(i % 2 == 0 for i in indents), f"non-two-space indentation: {sorted(indents)}"


class TestSchemaIdentity:
    """The published URLs are derived from one constant and must match the documents."""

    def test_page_schema_id(self) -> None:
        """The page schema's ``$id`` is the URL the package advertises."""
        assert load_schema("plannotation-0.1.json")["$id"] == SCHEMA_ID

    def test_index_schema_id(self) -> None:
        """The index schema's ``$id`` is the URL the package advertises."""
        assert load_schema("plannotation-index-0.1.json")["$id"] == INDEX_SCHEMA_ID

    def test_ids_are_distinct(self) -> None:
        """Two schemas sharing an ``$id`` would break any registry that loads both."""
        assert SCHEMA_ID != INDEX_SCHEMA_ID


class TestPageSchemaShape:
    """Spot-checks on the page schema, pinned against the design brief."""

    def test_required_top_level_keys(self) -> None:
        """The four keys the design brief makes mandatory."""
        assert load_schema("plannotation-0.1.json")["required"] == [
            "plannotation",
            "provenance",
            "page",
            "sheet",
        ]

    def test_root_forbids_unknown_keys(self) -> None:
        """Unknown top-level keys are rejected; extras go in ``extensions``."""
        assert load_schema("plannotation-0.1.json")["additionalProperties"] is False

    def test_extensions_are_namespaced(self) -> None:
        """Extension keys must be ``x-`` prefixed so they can never collide with the spec."""
        schema = load_schema("plannotation-0.1.json")
        assert schema["properties"]["extensions"]["propertyNames"]["pattern"] == "^x-"

    def test_provenance_values(self) -> None:
        """Provenance is a closed vocabulary."""
        assert load_schema("plannotation-0.1.json")["$defs"]["provenance"]["enum"] == [
            "authored",
            "inferred",
            "mixed",
        ]

    def test_bbox_is_four_numbers(self) -> None:
        """A bbox is exactly ``[x0, y0, x1, y1]`` in paper millimetres."""
        bbox = load_schema("plannotation-0.1.json")["$defs"]["bbox"]
        assert bbox["minItems"] == bbox["maxItems"] == 4

    def test_paper_to_plane_is_six_numbers(self) -> None:
        """The affine is ``[a, b, c, d, e, f]``, matching the PDF matrix convention."""
        affine = load_schema("plannotation-0.1.json")["$defs"]["viewport"]["properties"][
            "paperToPlane"
        ]
        assert affine["minItems"] == affine["maxItems"] == 6


class TestIndexSchemaShape:
    """The document-level index records what is labelled and to what level."""

    def test_requires_pages(self) -> None:
        """An index without a page list says nothing."""
        assert load_schema("plannotation-index-0.1.json")["required"] == ["plannotation", "pages"]

    def test_page_entry_required_fields(self) -> None:
        """Each entry identifies the page, the sheet and the level reached."""
        schema = load_schema("plannotation-index-0.1.json")
        assert schema["properties"]["pages"]["items"]["required"] == [
            "pageIndex",
            "sheetId",
            "level",
        ]

    def test_levels_are_the_three_conformance_levels(self) -> None:
        """Only L1, L2 and L3 exist."""
        schema = load_schema("plannotation-index-0.1.json")
        level = schema["properties"]["pages"]["items"]["properties"]["level"]
        assert level["enum"] == ["L1", "L2", "L3"]

    def test_carries_the_model_hash(self) -> None:
        """The design brief requires the index to record the source model's hash."""
        schema = load_schema("plannotation-index-0.1.json")
        sha = schema["properties"]["model"]["properties"]["sha256"]
        assert sha["pattern"] == "^[a-f0-9]{64}$"

    def test_is_self_contained(self) -> None:
        """The index schema must validate without fetching the page schema.

        A third-party reader should be able to check an index on its own, so the
        document deliberately carries no cross-file ``$ref``.
        """
        refs: set[str] = set()
        collect_refs(load_schema("plannotation-index-0.1.json"), refs)
        assert not refs, f"index schema should have no $refs, found {refs}"
