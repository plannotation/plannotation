# SPDX-License-Identifier: Apache-2.0
"""The inspector: the single-file viewer and the terminal command.

The HTML cannot be driven from pytest without a browser, so what is tested here is
what a browser-free test can honestly establish: that the file is self-contained, that
it reads labels the way this project writes them, and that the conventions it hard-codes
still match the format. A manual check against the samples is recorded in
``docs/inspector.md``; this catches the ways it would silently stop working.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from planlabel.cli import app
from planlabel.constants import page_label_filename

INSPECTOR = Path(__file__).parent.parent / "inspector" / "index.html"
SAMPLES = Path(__file__).parent.parent / "samples"


def html() -> str:
    """Read the inspector.

    Returns:
        Its source.
    """
    return INSPECTOR.read_text("utf-8")


class TestTheInspectorIsOneFile:
    """A drawing office should be able to keep it next to the drawings."""

    def test_it_exists_and_is_html(self) -> None:
        """One file, no build step, no package manager."""
        assert html().lstrip().startswith("<!DOCTYPE html>")

    def test_it_carries_the_licence(self) -> None:
        """Every source file in this project says what it is licensed under."""
        assert "SPDX-License-Identifier: Apache-2.0" in html()

    def test_it_has_no_local_dependencies(self) -> None:
        """No relative script or stylesheet: nothing to be missing when it is copied."""
        assert not re.findall(r'<(?:script|link)[^>]*(?:src|href)="(?!https://)[^"]+"', html())

    def test_its_only_remote_dependency_is_pdfjs(self) -> None:
        """Documented in the README, and the only thing a first load needs."""
        remote = set(re.findall(r'https://[^"\s]+', html()))
        assert remote
        assert all("pdf.js" in url or "pdfjs" in url.lower() for url in remote), remote

    def test_it_is_small_enough_to_read(self) -> None:
        """It is meant to be auditable by the person relying on it."""
        assert len(html()) < 80_000


class TestItReadsWhatThisProjectWrites:
    """The conventions the inspector hard-codes must match the ones PlanLabel emits."""

    def test_it_looks_for_labels_in_the_name_tree(self) -> None:
        """It reads the EmbeddedFiles name tree.

        pdf.js exposes that tree through getAttachments, which is why the carrier
        registers every file there as well as on the page's own /AF.
        """
        assert "getAttachments" in html()

    def test_its_filename_pattern_matches_the_one_we_write(self) -> None:
        """A pattern that drifted from page_label_filename would find nothing at all."""
        assert r"/^planlabel-p(\d+)\.json$/" in html(), (
            "the inspector's filename pattern has changed shape"
        )
        compiled = re.compile(r"^planlabel-p(\d+)\.json$")
        for index in (0, 7, 1234):
            assert compiled.match(page_label_filename(index))

    def test_it_flips_paper_coordinates(self) -> None:
        """SPEC 3.3: paper is y-up from the bottom left, a canvas is y-down."""
        assert "h - y" in html() or "(h - y)" in html()

    def test_it_knows_the_conformance_levels(self) -> None:
        """So it can say what it is looking at without asking anything else."""
        source = html()
        for level in ("L1", "L2", "L3"):
            assert f'"{level}"' in source

    def test_it_draws_every_kind_of_thing_a_label_holds(self) -> None:
        """Viewports, elements and annotations, each separately switchable."""
        source = html()
        for kind in ("viewports", "elements", "annotations"):
            assert kind in source

    def test_it_offers_the_two_exports_the_design_brief_asks_for(self) -> None:
        """Copy JSON and export CSV."""
        source = html()
        assert "clipboard.writeText" in source
        assert "text/csv" in source

    def test_it_escapes_what_it_prints(self) -> None:
        """A sheet title is text somebody typed, and it goes into the DOM."""
        assert "&amp;" in html()

    def test_a_label_that_will_not_parse_is_treated_as_absent(self) -> None:
        """SPEC 4.3 (2), which binds a reader whatever language it is written in."""
        assert "catch" in html()


class TestTheInspectCommand:
    """The same information, for a terminal and for a pipe."""

    @staticmethod
    def _run(*args: str) -> object:
        """Invoke the CLI.

        Args:
            *args: Arguments after the program name.

        Returns:
            Typer's result.
        """
        return CliRunner().invoke(app, ["inspect", *args])

    @pytest.mark.skipif(
        not (SAMPLES / "floorplan" / "sheet.labelled.pdf").exists(),
        reason="samples are not built; run make samples",
    )
    def test_it_prints_a_table_for_a_sample(self) -> None:
        """The sheet, its viewports, its elements and its annotations."""
        result = self._run(str(SAMPLES / "floorplan" / "sheet.labelled.pdf"))
        assert result.exit_code == 0
        assert "ARC-101" in result.stdout

    @pytest.mark.skipif(
        not (SAMPLES / "floorplan" / "sheet.labelled.pdf").exists(),
        reason="samples are not built; run make samples",
    )
    def test_json_output_parses(self) -> None:
        """--json is for a pipe, so it must be JSON and nothing else."""
        result = self._run("--json", str(SAMPLES / "floorplan" / "sheet.labelled.pdf"))
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["pages"]["0"]["sheet"]["id"] == "ARC-101"

    def test_a_document_with_no_labels_says_so(self, tmp_path: Path) -> None:
        """Rather than printing an empty table, which reads like an empty drawing."""
        import tests.pdf_fixtures as fx

        plain = tmp_path / "plain.pdf"
        plain.write_bytes(fx.build_drawing_set())
        result = self._run(str(plain))
        assert result.exit_code == 0
        assert "no PlanLabel labels" in result.stdout

    def test_an_unreadable_document_exits_two(self, tmp_path: Path) -> None:
        """Two is "could not read this", which is not the same as "nothing in it"."""
        broken = tmp_path / "broken.json"
        broken.write_text("{ not json", encoding="utf-8")
        assert self._run(str(broken)).exit_code == 2

    def test_asking_for_a_page_with_no_label_exits_two(self, tmp_path: Path) -> None:
        """A silent empty result would read as "this page has nothing on it"."""
        import tests.pdf_fixtures as fx

        plain = tmp_path / "plain.pdf"
        plain.write_bytes(fx.build_drawing_set())
        assert self._run("--page", "5", str(plain)).exit_code == 2
