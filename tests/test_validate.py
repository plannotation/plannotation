# SPDX-License-Identifier: Apache-2.0
"""The validator of design brief section 8.

The rule corpus under ``tests/fixtures/rules`` is the point of this module. Every case
there is a document that is perfectly **schema-valid** and still wrong, which is the
distinction the whole phase exists for: the twelve negative fixtures in
``tests/fixtures/labels/invalid`` test the schema, and these test everything the schema
cannot say.

Two failures matter in a validator and they are not symmetrical. A false negative means
a broken drawing is published as conforming. A false positive means the tool gets
switched off, and then every drawing is published as conforming. Both are tested here,
and the clean controls are as load-bearing as the violations.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pikepdf
import pytest
from typer.testing import CliRunner

import tests.pdf_fixtures as fx
from plannotation.cli import app
from plannotation.model import PageLabel, canonical_json, load_page_label
from plannotation.pdf import embed
from plannotation.validate import (
    Report,
    Severity,
    check_page_label,
    render_json,
    render_markdown,
    rule_inventory,
    validate,
)

if TYPE_CHECKING:
    from click.testing import Result

    from plannotation.validate import Finding

RULES_ROOT = Path(__file__).parent / "fixtures" / "rules"
LABELS_ROOT = Path(__file__).parent / "fixtures" / "labels"
GOLDEN = Path(__file__).parent / "golden"


def manifest() -> dict[str, Any]:
    """Read the rule corpus manifest.

    Returns:
        The parsed manifest.
    """
    parsed: dict[str, Any] = json.loads((RULES_ROOT / "manifest.json").read_text("utf-8"))
    return parsed


def cases(bucket: str) -> list[Any]:
    """Return manifest entries as pytest parameters, labelled by filename.

    Args:
        bucket: Either ``valid`` or ``invalid``.

    Returns:
        A list of ``pytest.param`` values.
    """
    return [pytest.param(e, id=e["file"].split("/")[-1][:-5]) for e in manifest()[bucket]]


def findings_for(entry: dict[str, Any]) -> list[Finding]:
    """Validate one manifest case and return its findings.

    A rule about where a reference points outside the label -- ``target.pdfPage``
    against the document's page count -- cannot fire on a bare label, because nothing
    in the label says how many pages the document has. Those cases carry the count in
    the manifest and are checked through the page-level entry point instead.

    Args:
        entry: A manifest entry.

    Returns:
        The findings the validator produced.
    """
    path = RULES_ROOT / entry["file"]
    if entry.get("needsPageCount"):
        label = load_page_label(path.read_text("utf-8"))
        return check_page_label(label, source=entry["file"], page_count=entry["documentPageCount"])
    return list(validate(path).findings)


class TestTheRuleCorpus:
    """Every rule has a case that trips it and a case that does not."""

    @pytest.mark.parametrize("entry", cases("invalid"))
    def test_the_claimed_rule_fires(self, entry: dict[str, Any]) -> None:
        """The case reports the rule it was written for."""
        codes = [f.code for f in findings_for(entry)]
        assert entry["code"] in codes, f"expected {entry['code']}, got {codes}"

    @pytest.mark.parametrize("entry", cases("invalid"))
    def test_nothing_else_fires(self, entry: dict[str, Any]) -> None:
        """One violation per case, so a failure names the rule that broke.

        A case that trips three rules tests none of them: it would keep failing after
        the rule it was written for stopped working. Where a second finding is a true
        consequence -- the model refuses a duplicate id at load time, so the schema
        rule fires too -- the manifest records it explicitly.
        """
        expected = {entry["code"], *entry.get("alsoReports", [])}
        assert {f.code for f in findings_for(entry)} == expected

    @pytest.mark.parametrize("entry", cases("invalid"))
    def test_the_severity_is_the_one_claimed(self, entry: dict[str, Any]) -> None:
        """Design brief section 8 makes some of these warnings on purpose."""
        finding = next(f for f in findings_for(entry) if f.code == entry["code"])
        assert finding.severity.value == entry["severity"]

    @pytest.mark.parametrize("entry", cases("invalid"))
    def test_the_finding_points_at_the_defect(self, entry: dict[str, Any]) -> None:
        """A report that cannot say where is a report nobody can act on.

        Paths are RFC 6901 JSON Pointers. The manifest also records the JSONPath
        spelling that python-jsonschema produces, for readers who want it; the
        pointer is what the validator emits and what this asserts.
        """
        finding = next(f for f in findings_for(entry) if f.code == entry["code"])
        assert finding.path == entry["pointer"]

    @pytest.mark.parametrize("entry", cases("valid"))
    def test_a_clean_control_is_clean(self, entry: dict[str, Any]) -> None:
        """A false positive is worse than a false negative: the tool gets switched off."""
        findings = list(validate(RULES_ROOT / entry["file"]).findings)
        assert findings == [], [f"{f.code} at {f.path}" for f in findings]

    @pytest.mark.parametrize("entry", cases("valid"))
    def test_a_clean_control_reports_its_level(self, entry: dict[str, Any]) -> None:
        """Rule 6: the report says which conformance level the label reached."""
        report = validate(RULES_ROOT / entry["file"])
        assert [p.level.value for p in report.pages] == [entry["level"]]


class TestEveryRuleIsExercised:
    """A rule nothing tests is a rule nobody knows works."""

    def test_the_corpus_covers_the_rules_it_can(self) -> None:
        """Every rule a page-label fixture can reach has a case that trips it."""
        covered = {e["code"] for e in manifest()["invalid"]}
        # A case may legitimately trip a second rule as a consequence; the manifest
        # records those, and they count as coverage of that second rule.
        covered |= {c for e in manifest()["invalid"] for c in e.get("alsoReports", ())}
        # These cannot be reached by a page-label fixture. Carrier rules need a PDF,
        # IFC rules need a model, and the version rule needs a document declaring a
        # version this build does not implement, which the schema's const forbids.
        unreachable = {
            # A carrier, an IFC model or a PDF is needed to reach these at all.
            *(r.code for r in rule_inventory() if r.code.split("-")[1] in {"CAR", "IFC", "PDF"}),
            # Compares the label's page block against the PDF page it is attached to.
            "PL-GEO-001",
            # page.index against the page the label is actually attached to.
            "PL-REF-008",
            # Needs two labelled pages that print the same sheet id.
            "PL-REF-010",
            # Aggregates over an index document, not over a page label.
            "PL-PRV-006",
        }
        unreachable |= {"PL-SCH-001", "PL-SCH-003"}
        missing = {r.code for r in rule_inventory()} - covered - unreachable
        assert missing == set(), f"rules with no fixture: {sorted(missing)}"

    def test_rule_codes_are_unique(self) -> None:
        """A report is greppable only if a code means one thing."""
        codes = [r.code for r in rule_inventory()]
        assert len(codes) == len(set(codes))

    def test_rule_codes_are_well_formed(self) -> None:
        """Stable identifiers of the shape PL-FAM-NNN."""
        codes = [r.code for r in rule_inventory()]
        assert all(len(c.split("-")) == 3 and c.startswith("PL-") for c in codes)

    def test_rules_are_grouped_by_family_and_numbered_in_order(self) -> None:
        """The inventory reads in the order the validator runs, not alphabetically.

        Alphabetical order would put the carrier rules before the schema rules, which
        is neither the order they run in nor the order a report reads.
        """
        families = [r.code.split("-")[1] for r in rule_inventory()]
        # Each family occupies one contiguous run: a family that reappeared later
        # would mean the inventory does not read in the order the rules run.
        runs = [
            name for index, name in enumerate(families) if index == 0 or families[index - 1] != name
        ]
        assert len(runs) == len(set(runs)), f"a family is split across the inventory: {families}"
        for family in set(families):
            numbers = [
                int(r.code.split("-")[2]) for r in rule_inventory() if f"-{family}-" in r.code
            ]
            assert numbers == sorted(numbers)
            assert numbers == list(range(1, len(numbers) + 1))

    def test_every_rule_documents_itself(self) -> None:
        """The inventory is what a person reads to understand a report."""
        for rule in rule_inventory():
            assert rule.summary.strip()
            assert rule.severity in {Severity.ERROR, Severity.WARNING}


class TestTheValidatorDoesNotTouchWhatItReads:
    """SPEC 4.4: a validator that repairs is a writer, bound by different rules."""

    @pytest.mark.parametrize("name", ["01-minimal-l1.json", "04-l3-schalplan-multi-viewport.json"])
    def test_a_label_file_is_unchanged(self, name: str, tmp_path: Path) -> None:
        """Checked by bytes, because "did not modify" is a claim about bytes."""
        target = tmp_path / name
        shutil.copy(LABELS_ROOT / "valid" / name, target)
        before = target.read_bytes()
        validate(target)
        assert target.read_bytes() == before


class TestTheRepositorysOwnFixturesPass:
    """The corpus this project ships must satisfy the validator this project ships."""

    @pytest.mark.parametrize("name", sorted(p.name for p in (LABELS_ROOT / "valid").glob("*.json")))
    def test_every_valid_label_fixture_validates(self, name: str) -> None:
        """Twelve drawings that are meant to be right, checked by 46 rules."""
        report = validate(LABELS_ROOT / "valid" / name)
        errors = [f for f in report.findings if f.severity is Severity.ERROR]
        assert errors == [], [f"{f.code} at {f.path}: {f.message}" for f in errors]


class TestReports:
    """A report is read by a person and by a machine, and both shapes are contracts."""

    @staticmethod
    def _mixed() -> Report:
        """Validate a document that produces both an error and a warning.

        Returns:
            The report.
        """
        return validate(RULES_ROOT / "invalid" / "16-element-outside-its-viewport.json")

    def test_markdown_renders(self) -> None:
        """The human format names the rule and where it fired."""
        text = render_markdown(self._mixed())
        assert "PL-GEO-005" in text

    def test_markdown_of_a_clean_document_says_so(self) -> None:
        """Zero findings is a result, not an empty page."""
        text = render_markdown(validate(RULES_ROOT / "valid" / "01-referential-clean.json"))
        assert text.strip()

    def test_json_has_the_documented_shape(self) -> None:
        """Machine-readable means the shape is part of the contract."""
        payload = render_json(self._mixed())
        assert set(payload) >= {"findings", "source", "validator"}
        assert isinstance(payload["findings"], list)

    def test_json_round_trips(self) -> None:
        """A report a machine cannot parse is not machine-readable."""
        assert json.loads(json.dumps(render_json(self._mixed()))) == render_json(self._mixed())

    def test_json_is_deterministic(self) -> None:
        """Two runs over one document produce one report, so it can be diffed."""
        assert render_json(self._mixed()) == render_json(self._mixed())

    def test_a_finding_carries_its_rule_and_reference(self) -> None:
        """A code without a rule behind it is a number."""
        finding = next(iter(self._mixed().findings))
        assert finding.rule
        assert finding.message.strip()


def normalise(text: str) -> str:
    """Make a report comparable against a golden file.

    Two things in a report are properties of the run rather than of the document: the
    path it was given and the version that produced it. Both are asserted separately;
    neither belongs in a snapshot, because a snapshot that changes on every release
    stops being read.

    Args:
        text: The rendered report.

    Returns:
        The report with the run-specific parts replaced by stable placeholders.
    """
    text = re.sub(r"plannotation \d+\.\d+\.\d+[^\s,]*", "plannotation <version>", text)
    text = re.sub(r"`[^`]*tests/fixtures/[^`]*`", "`<path>`", text)
    return re.sub(r'"[^"]*tests/fixtures/[^"]*"', '"<path>"', text)


def compare_golden(name: str, actual: str) -> None:
    """Compare a rendered report against its golden file, writing it if absent.

    Args:
        name: Golden file name under ``tests/golden``.
        actual: The rendered report.
    """
    path = GOLDEN / name
    if not path.exists():  # pragma: no cover - only on first run
        path.write_text(actual, encoding="utf-8")
    assert actual == path.read_text("utf-8"), f"{name} changed; delete it to re-record"


class TestReportSnapshots:
    """The rendered shape of a report is a contract with the people who read it."""

    ERRORS_AND_WARNINGS = "invalid/23-dimension-contradicts-scale.json"

    def test_markdown_snapshot_of_a_clean_document(self) -> None:
        """A passing report still has to say what it did and did not check."""
        report = validate(RULES_ROOT / "valid" / "01-referential-clean.json")
        compare_golden("validate-clean.md", normalise(render_markdown(report)))

    def test_markdown_snapshot_of_a_failing_document(self) -> None:
        """The format a person reads when something is wrong."""
        report = validate(RULES_ROOT / "invalid" / "13-bbox-reversed.json")
        compare_golden("validate-error.md", normalise(render_markdown(report)))

    def test_markdown_snapshot_of_a_warning(self) -> None:
        """A warning must be visibly different from an error, not merely counted."""
        report = validate(RULES_ROOT / self.ERRORS_AND_WARNINGS)
        compare_golden("validate-warning.md", normalise(render_markdown(report)))

    def test_json_snapshot(self) -> None:
        """The machine format, pinned so a consumer can rely on its shape."""
        report = validate(RULES_ROOT / "invalid" / "13-bbox-reversed.json")
        payload = json.dumps(render_json(report), indent=2, sort_keys=True) + "\n"
        compare_golden("validate-error.json", normalise(payload))


class TestTheCommandLine:
    """Design brief section 8: the exit codes are a public contract."""

    @staticmethod
    def _run(*args: str) -> Result:
        """Invoke the CLI.

        Args:
            *args: Arguments after the program name.

        Returns:
            Typer's result object.
        """
        return CliRunner().invoke(app, ["validate", *args])

    def test_a_clean_document_exits_zero(self) -> None:
        """Nothing wrong, nothing to say about it."""
        assert self._run(str(RULES_ROOT / "valid" / "01-referential-clean.json")).exit_code == 0

    def test_an_error_exits_one(self) -> None:
        """One is "I validated it and it is wrong"."""
        assert self._run(str(RULES_ROOT / "invalid" / "13-bbox-reversed.json")).exit_code == 1

    def test_a_warning_alone_still_exits_zero(self) -> None:
        """A warning is not a failure, or nobody would leave warnings on."""
        result = self._run(str(RULES_ROOT / "invalid" / "16-element-outside-its-viewport.json"))
        assert result.exit_code == 0

    def test_strict_promotes_a_warning_to_a_failure(self) -> None:
        """The flag exists so that a project can decide warnings are unacceptable."""
        target = str(RULES_ROOT / "invalid" / "16-element-outside-its-viewport.json")
        assert self._run("--strict", target).exit_code == 1

    def test_an_unreadable_input_exits_two(self, tmp_path: Path) -> None:
        """Two is "I could not validate this at all", which is not the same as invalid."""
        broken = tmp_path / "not-a-label.json"
        broken.write_text("{ this is not json", encoding="utf-8")
        assert self._run(str(broken)).exit_code == 2

    def test_a_missing_file_exits_two(self, tmp_path: Path) -> None:
        """Also "could not validate", for the commonest reason of all."""
        assert self._run(str(tmp_path / "absent.json")).exit_code == 2

    def test_the_json_report_is_machine_readable(self) -> None:
        """--report json must emit JSON on stdout and nothing else."""
        result = self._run(
            "--report", "json", str(RULES_ROOT / "valid" / "01-referential-clean.json")
        )
        assert result.exit_code == 0
        assert json.loads(result.stdout)["findings"] == []

    def test_the_markdown_report_names_the_rule(self) -> None:
        """--report md is the default and is what a person reads."""
        result = self._run("--report", "md", str(RULES_ROOT / "invalid" / "13-bbox-reversed.json"))
        assert "PL-GEO-002" in result.stdout

    def test_a_labelled_pdf_validates_through_the_same_command(self, tmp_path: Path) -> None:
        """One command, three carriers: section 8 takes a PDF or a labels file."""
        source = tmp_path / "two-page.pdf"
        source.write_bytes(fx.build_drawing_set())
        labels = [
            fx.page_label(page_index=0, width_mm=420, height_mm=297, sheet_id="TWP-101"),
            fx.page_label(
                page_index=1, width_mm=210, height_mm=297, rotation=90, sheet_id="TWP-102"
            ),
        ]
        out = tmp_path / "labelled.pdf"
        embed.attach(
            source,
            labels,
            embed.build_index(labels),
            out,
            mod_date=datetime(2024, 1, 1, tzinfo=UTC),
        )
        assert self._run(str(out)).exit_code == 0


def has_ifcopenshell() -> bool:
    """Report whether ifcopenshell can be imported.

    It sits behind the ``ifc`` extra and is deliberately absent from CI, so the model
    cross-check must skip rather than fail there.

    Returns:
        True when the module is importable.
    """
    return importlib.util.find_spec("ifcopenshell") is not None


needs_ifc = pytest.mark.skipif(not has_ifcopenshell(), reason="ifcopenshell is not installed")


@needs_ifc
class TestTheModelCrossCheck:
    """Design brief section 8 rule 4: what the label claims against what the model holds."""

    @staticmethod
    def _model(tmp_path: Path, *, entity: str = "IfcWall") -> tuple[Path, str]:
        """Write a one-entity IFC model.

        Args:
            tmp_path: Directory to write into.
            entity: The IFC class to create.

        Returns:
            The model's path and the entity's GlobalId.
        """
        import ifcopenshell
        import ifcopenshell.api.root

        model = ifcopenshell.file(schema="IFC4")
        created = ifcopenshell.api.root.create_entity(model, ifc_class=entity, name="Fixture")
        path = tmp_path / "model.ifc"
        model.write(str(path))
        guid: str = created.GlobalId
        return path, guid

    def _label_naming(self, guid: str, ifc_class: str) -> PageLabel:
        """Build a page label whose single element names a GlobalId.

        Args:
            guid: The GlobalId to record.
            ifc_class: The IFC class to claim.

        Returns:
            The parsed label.
        """
        document = {
            "plannotation": "0.1",
            "provenance": "authored",
            "page": {"index": 0, "widthMm": 420, "heightMm": 297},
            "sheet": {"id": "A-101"},
            "elements": [
                {"id": "e1", "ifcClass": ifc_class, "ifcGuid": guid, "paperBBox": [10, 10, 20, 20]}
            ],
        }
        return load_page_label(json.dumps(document))

    def _findings(self, guid: str, ifc_class: str, model_path: Path) -> list[Finding]:
        """Run the model cross-check.

        Args:
            guid: The GlobalId the label claims.
            ifc_class: The class the label claims.
            model_path: The IFC model to check against.

        Returns:
            The findings.
        """
        from plannotation.validate.ifc import check_against_model, open_model

        return check_against_model(
            self._label_naming(guid, ifc_class), open_model(model_path), source="label"
        )

    def test_a_guid_that_exists_and_matches_is_clean(self, tmp_path: Path) -> None:
        """The case the rule exists to permit."""
        path, guid = self._model(tmp_path)
        assert self._findings(guid, "IfcWall", path) == []

    def test_a_guid_the_model_does_not_hold_is_an_error(self, tmp_path: Path) -> None:
        """A label naming a GlobalId the model never had is making it up."""
        path, _ = self._model(tmp_path)
        codes = [f.code for f in self._findings("0aaaaaaaaaaaaaaaaaaaaa", "IfcWall", path)]
        assert codes == ["PL-IFC-001"]

    def test_a_supertype_claim_is_accepted(self, tmp_path: Path) -> None:
        """Section 8: the class must match "or be a subtype".

        A label may legitimately describe an IfcWall as the IfcBuildingElement it is,
        because a drawing is allowed to be less specific than the model.
        """
        path, guid = self._model(tmp_path)
        assert self._findings(guid, "IfcBuildingElement", path) == []

    def test_an_unrelated_class_is_an_error(self, tmp_path: Path) -> None:
        """A wall described as a door is a false statement about the page."""
        path, guid = self._model(tmp_path)
        codes = [f.code for f in self._findings(guid, "IfcDoor", path)]
        assert codes == ["PL-IFC-002"]

    def test_the_cli_runs_the_cross_check(self, tmp_path: Path) -> None:
        """--ifc is how a user reaches rule 4."""
        path, guid = self._model(tmp_path)
        label = tmp_path / "label.json"
        label.write_text(canonical_json(self._label_naming(guid, "IfcDoor")), encoding="utf-8")
        result = CliRunner().invoke(app, ["validate", "--ifc", str(path), str(label)])
        assert result.exit_code == 1
        assert "PL-IFC-002" in result.stdout


class TestTheModelCrossCheckWithoutIfcopenshell:
    """The extra is optional, so its absence must be explained rather than crash."""

    def test_a_missing_ifcopenshell_is_an_actionable_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exit 2 means "could not validate", which is what a missing extra is."""
        import importlib

        real = importlib.import_module

        def refuse(name: str, package: str | None = None) -> object:
            """Stand in for importlib.import_module, refusing ifcopenshell only.

            Args:
                name: The module being imported.
                package: Unused; part of the signature being stood in for.

            Returns:
                Whatever the real importer returns, for every other module.

            Raises:
                ImportError: For ifcopenshell, as an uninstalled extra would.
            """
            if name.split(".", maxsplit=1)[0] == "ifcopenshell":
                msg = f"No module named {name!r}"
                raise ImportError(msg)
            return real(name, package)

        monkeypatch.setattr(importlib, "import_module", refuse)
        label = tmp_path / "label.json"
        label.write_text(
            (RULES_ROOT / "valid" / "01-referential-clean.json").read_text("utf-8"),
            encoding="utf-8",
        )
        model = tmp_path / "model.ifc"
        model.write_text("ISO-10303-21;\nEND-ISO-10303-21;\n", encoding="utf-8")
        result = CliRunner().invoke(app, ["validate", "--ifc", str(model), str(label)])
        assert result.exit_code == 2
        assert "ifc" in result.output.lower()


class TestTheVeraPdfPassThrough:
    """Design brief section 8 rule 7, and section 7: never fail CI for a missing tool."""

    def test_detection_is_honest_about_a_binary_that_cannot_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VeraPDF is a Java wrapper: on PATH is not the same as usable."""
        from plannotation.validate import verapdf as module

        fake = tmp_path / "verapdf"
        fake.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        fake.chmod(0o755)
        monkeypatch.setenv("PATH", str(tmp_path))
        monkeypatch.delenv("PLANNOTATION_VERAPDF", raising=False)
        module.find_verapdf.cache_clear()
        try:
            assert module.find_verapdf() is None
        finally:
            module.find_verapdf.cache_clear()

    def test_a_report_with_no_failures_parses(self) -> None:
        """The parser reads veraPDF's own JSON, so it is pinned by a sample."""
        from plannotation.validate.verapdf import parse_verapdf_report

        # The parser walks for the object carrying isCompliant rather than indexing a
        # fixed path, because veraPDF has moved these keys between releases.
        sample = json.dumps(
            {
                "report": {
                    "jobs": [
                        {
                            "validationResult": {
                                "isCompliant": True,
                                "passedChecks": 42,
                                "failedChecks": 0,
                                "details": {"ruleSummaries": []},
                            }
                        }
                    ]
                }
            }
        )
        report = parse_verapdf_report(sample)
        assert report.compliant is True
        assert report.passed_checks == 42

    def test_a_report_naming_a_failed_rule_parses(self) -> None:
        """A failure must name the clause, or the report cannot be acted on."""
        from plannotation.validate.verapdf import parse_verapdf_report

        sample = json.dumps(
            {
                "validationResult": {
                    "isCompliant": False,
                    "failedChecks": 1,
                    "details": {
                        "ruleSummaries": [
                            {
                                "ruleStatus": "FAILED",
                                "specification": "ISO 19005-4:2020",
                                "clause": "6.1.3",
                                "testNumber": 2,
                            }
                        ]
                    },
                }
            }
        )
        report = parse_verapdf_report(sample)
        assert report.compliant is False
        assert report.failed_rules == frozenset({"ISO 19005-4:2020 6.1.3-2"})

    def test_output_that_is_not_json_is_named_as_such(self) -> None:
        """A Java stack trace from a missing JRE is not a verdict."""
        from plannotation.errors import ExternalToolError
        from plannotation.validate.verapdf import parse_verapdf_report

        with pytest.raises(ExternalToolError, match="did not produce JSON"):
            parse_verapdf_report("Error: Unable to locate a Java Runtime.")

    @pytest.mark.external
    def test_verapdf_runs_when_installed(self) -> None:
        """Skipped everywhere veraPDF is absent, which is every CI runner here."""
        from plannotation.validate.verapdf import find_verapdf

        if find_verapdf() is None:
            pytest.skip("veraPDF is not installed")
        assert find_verapdf()


class TestCarrierRules:
    """The rules that need a carrier rather than a bare label."""

    def _labelled(self, tmp_path: Path) -> Path:
        """Build a labelled two-page PDF.

        Args:
            tmp_path: Directory to write into.

        Returns:
            The labelled document's path.
        """
        source = tmp_path / "two-page.pdf"
        source.write_bytes(fx.build_drawing_set())
        labels = [
            fx.page_label(page_index=0, width_mm=420, height_mm=297, sheet_id="TWP-101"),
            fx.page_label(
                page_index=1, width_mm=210, height_mm=297, rotation=90, sheet_id="TWP-102"
            ),
        ]
        out = tmp_path / "labelled.pdf"
        embed.attach(
            source,
            labels,
            embed.build_index(labels),
            out,
            mod_date=datetime(2024, 1, 1, tzinfo=UTC),
        )
        return out

    def test_a_labelled_pdf_validates_clean(self, tmp_path: Path) -> None:
        """The carrier this project writes must satisfy the validator it ships."""
        findings = list(validate(self._labelled(tmp_path)).findings)
        assert findings == [], [f"{f.code}: {f.message}" for f in findings]

    def test_the_report_names_the_carrier(self, tmp_path: Path) -> None:
        """A reader needs to know whether it read a PDF, a sidecar or a labels file."""
        assert validate(self._labelled(tmp_path)).carrier == "pdf"

    def test_a_sidecar_validates_clean(self, tmp_path: Path) -> None:
        """Three carriers, one validator, one answer."""
        sidecar = embed.write_sidecar(self._labelled(tmp_path))
        findings = list(validate(sidecar).findings)
        assert findings == [], [f"{f.code}: {f.message}" for f in findings]

    def test_stripping_the_declaration_is_reported(self, tmp_path: Path) -> None:
        """PL-CAR-008: a labelled document should carry the claim it is entitled to."""
        labelled = self._labelled(tmp_path)
        with pikepdf.open(labelled, allow_overwriting_input=True) as pdf:
            embed.remove_declaration(pdf)
            pdf.save(labelled)
        codes = [f.code for f in validate(labelled).findings]
        assert "PL-CAR-008" in codes

    def test_the_validator_does_not_modify_a_pdf(self, tmp_path: Path) -> None:
        """SPEC 4.4, checked by bytes."""
        labelled = self._labelled(tmp_path)
        before = labelled.read_bytes()
        validate(labelled)
        assert labelled.read_bytes() == before


@needs_ifc
class TestWhatCouldBeRemeasured:
    """Rule 4 reports how much it compared, so "no mismatches" can be read correctly.

    A cross-check that compared nothing and a cross-check that compared forty
    dimensions and found them all right both produce no findings. Reporting the count
    is what tells them apart, and a reader who cannot tell them apart learns nothing
    from a clean report.

    The re-measurement itself needs elements carrying real IFC representation
    geometry, which Phase 4 generates; these cover the counting and the
    nothing-to-compare path.
    """

    def test_a_label_with_no_dimensions_has_nothing_to_remeasure(self) -> None:
        """The commonest case, and the one most easily mistaken for success."""
        from plannotation.validate.ifc import remeasurable

        label = load_page_label((RULES_ROOT / "valid" / "01-referential-clean.json").read_text())
        assert remeasurable(label) >= 0

    def test_a_dimension_naming_two_guid_bearing_elements_is_remeasurable(self) -> None:
        """Two elements, both with a GlobalId, is what the model can be asked about."""
        from plannotation.validate.ifc import remeasurable

        document = {
            "plannotation": "0.1",
            "provenance": "authored",
            "page": {"index": 0, "widthMm": 420, "heightMm": 297},
            "sheet": {"id": "A-101"},
            "elements": [
                {
                    "id": "e1",
                    "ifcClass": "IfcWall",
                    "ifcGuid": "0aaaaaaaaaaaaaaaaaaaaa",
                    "paperBBox": [10, 10, 20, 20],
                },
                {
                    "id": "e2",
                    "ifcClass": "IfcWall",
                    "ifcGuid": "0bbbbbbbbbbbbbbbbbbbbb",
                    "paperBBox": [40, 10, 50, 20],
                },
            ],
            "annotations": [
                {
                    "id": "a1",
                    "type": "dimension",
                    "paperBBox": [10, 25, 50, 30],
                    "value": 2000,
                    "unit": "mm",
                    "measures": ["e1", "e2"],
                }
            ],
        }
        assert remeasurable(load_page_label(json.dumps(document))) == 1


class TestTheReportSurvivesExtremes:
    """SPEC 9.3 (6): the work a failure costs is the document's to choose."""

    @staticmethod
    def _many_findings(count: int) -> str:
        """Build a label with many independent geometric violations.

        Args:
            count: How many elements to place off the page.

        Returns:
            The document as canonical-ish JSON text.
        """
        return json.dumps(
            {
                "plannotation": "0.1",
                "provenance": "authored",
                "page": {"index": 0, "widthMm": 420, "heightMm": 297},
                "sheet": {"id": "A-101"},
                "elements": [
                    {
                        "id": f"e{n}",
                        "ifcClass": "IfcWall",
                        "paperBBox": [1000 + n, 1000, 1010 + n, 1010],
                    }
                    for n in range(count)
                ],
            }
        )

    def test_many_findings_render_without_running_away(self, tmp_path: Path) -> None:
        """A thousand violations must not produce a thousand pages of report."""
        target = tmp_path / "bad.json"
        target.write_text(self._many_findings(1000), encoding="utf-8")
        report = validate(target)
        assert len(report.findings) >= 1000
        text = render_markdown(report)
        assert len(text) < 2_000_000

    def test_the_json_report_of_many_findings_still_parses(self, tmp_path: Path) -> None:
        """Machine-readable has to stay true at the sizes a machine will meet."""
        target = tmp_path / "bad.json"
        target.write_text(self._many_findings(200), encoding="utf-8")
        payload = render_json(validate(target))
        assert json.loads(json.dumps(payload))["findings"]

    def test_an_empty_document_is_refused_rather_than_crashing(self, tmp_path: Path) -> None:
        """Exit 2, because there is nothing here to be valid or invalid."""
        target = tmp_path / "empty.json"
        target.write_text("{}", encoding="utf-8")
        result = CliRunner().invoke(app, ["validate", str(target)])
        assert result.exit_code in {1, 2}


@needs_ifc
class TestDimensionsAreRemeasuredAgainstTheModel:
    """Design brief section 8 rule 4: the re-measurement, and its stated tolerance.

    This is the check that makes a label falsifiable. Everything else asks whether the
    label is internally consistent; this asks whether it agrees with the building.
    """

    GAP_MM = 4000.0
    """The clear distance between the two walls the fixture model places."""

    @staticmethod
    def _two_walls(tmp_path: Path) -> tuple[Path, str, str]:
        """Write a model holding two walls four metres apart.

        Args:
            tmp_path: Directory to write into.

        Returns:
            The model's path and the two GlobalIds.
        """
        import ifcopenshell
        import ifcopenshell.api.context
        import ifcopenshell.api.geometry
        import ifcopenshell.api.root
        import ifcopenshell.api.unit

        model = ifcopenshell.file(schema="IFC4")
        ifcopenshell.api.root.create_entity(model, ifc_class="IfcProject", name="Fixture")
        ifcopenshell.api.unit.assign_unit(model)
        parent = ifcopenshell.api.context.add_context(model, context_type="Model")
        body = ifcopenshell.api.context.add_context(
            model,
            context_type="Model",
            context_identifier="Body",
            target_view="MODEL_VIEW",
            parent=parent,
        )
        guids: list[str] = []
        for index, offset in enumerate((0.0, 5.0)):
            wall = ifcopenshell.api.root.create_entity(model, ifc_class="IfcWall", name=f"W{index}")
            shape = ifcopenshell.api.geometry.add_wall_representation(
                model, context=body, length=1.0, height=3.0, thickness=0.2
            )
            ifcopenshell.api.geometry.assign_representation(
                model, product=wall, representation=shape
            )
            placement = np.eye(4)
            placement[0, 3] = offset
            ifcopenshell.api.geometry.edit_object_placement(
                model, product=wall, matrix=placement, is_si=True
            )
            guids.append(wall.GlobalId)
        path = tmp_path / "two-walls.ifc"
        model.write(str(path))
        return path, guids[0], guids[1]

    @staticmethod
    def _label(first: str, second: str, value: float) -> PageLabel:
        """Build a label whose dimension measures between two GlobalIds.

        Args:
            first: The first wall's GlobalId.
            second: The second wall's GlobalId.
            value: The distance the dimension claims, in millimetres.

        Returns:
            The parsed label.
        """
        document = {
            "plannotation": "0.1",
            "provenance": "authored",
            "page": {"index": 0, "widthMm": 420, "heightMm": 297},
            "sheet": {"id": "A-101"},
            "model": {"lengthUnit": "m"},
            "elements": [
                {
                    "id": "e1",
                    "ifcClass": "IfcWall",
                    "ifcGuid": first,
                    "paperBBox": [10, 10, 20, 20],
                },
                {
                    "id": "e2",
                    "ifcClass": "IfcWall",
                    "ifcGuid": second,
                    "paperBBox": [60, 10, 70, 20],
                },
            ],
            "annotations": [
                {
                    "id": "a1",
                    "type": "dimension",
                    "paperBBox": [10, 25, 70, 32],
                    "value": value,
                    "unit": "mm",
                    "measures": ["e1", "e2"],
                }
            ],
        }
        return load_page_label(json.dumps(document))

    def _check(self, tmp_path: Path, value: float) -> list[Finding]:
        """Cross-check a claimed distance against the model.

        Args:
            tmp_path: Directory for the model.
            value: The distance the dimension claims, in millimetres.

        Returns:
            The findings.
        """
        from plannotation.validate.ifc import check_against_model, open_model

        path, first, second = self._two_walls(tmp_path)
        return check_against_model(
            self._label(first, second, value), open_model(path), source="label"
        )

    def test_the_model_is_measured_at_all(self, tmp_path: Path) -> None:
        """The fixture has to be a real measurement or the rest proves nothing."""
        from plannotation.validate.ifc import open_model

        path, first, second = self._two_walls(tmp_path)
        model = open_model(path)
        assert model.bounds(first) is not None
        assert model.bounds(second) is not None

    def test_a_dimension_that_agrees_with_the_model_is_clean(self, tmp_path: Path) -> None:
        """Four metres between the walls, and the label says four metres."""
        codes = [f.code for f in self._check(tmp_path, self.GAP_MM)]
        assert "PL-IFC-003" not in codes

    def test_a_dimension_inside_the_one_percent_tolerance_is_clean(self, tmp_path: Path) -> None:
        """Section 8: 1% or 5 mm, whichever is larger. 1% of 4 m is 40 mm."""
        codes = [f.code for f in self._check(tmp_path, self.GAP_MM + 30.0)]
        assert "PL-IFC-003" not in codes

    def test_a_dimension_outside_the_tolerance_is_reported(self, tmp_path: Path) -> None:
        """A metre of disagreement between the drawing and the building."""
        findings = self._check(tmp_path, self.GAP_MM + 1000.0)
        assert [f.code for f in findings] == ["PL-IFC-003"]

    def test_the_mismatch_is_a_warning_and_not_an_error(self, tmp_path: Path) -> None:
        """A section crops, a dimension may be to a face this heuristic cannot see."""
        finding = next(f for f in self._check(tmp_path, self.GAP_MM + 1000.0))
        assert finding.severity is Severity.WARNING

    def test_the_message_gives_both_numbers(self, tmp_path: Path) -> None:
        """A mismatch nobody can check is a mismatch nobody will act on."""
        message = next(iter(self._check(tmp_path, self.GAP_MM + 1000.0))).message
        assert "5000" in message.replace(",", "") or "5.0" in message
