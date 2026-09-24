# SPDX-License-Identifier: Apache-2.0
"""The inspector: the single-file viewer and the terminal command.

The HTML cannot be driven from pytest without a browser, so what is tested here is
what a browser-free test can honestly establish: that the file is self-contained, that
it reads plannotations the way this project writes them, and that the conventions it
hard-codes still match the format. A manual check against the samples is recorded in
``docs/inspector.md``; this catches the ways it would silently stop working.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from plannotation.cli import app
from plannotation.constants import plannotation_filename

if TYPE_CHECKING:
    from types import ModuleType

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
        """No relative script or stylesheet: nothing to be missing when it is copied.

        A data: URI, such as the icon, is inline and cannot go missing.
        """
        pattern = r'<(?:script|link)[^>]*(?:src|href)="(?!https://|data:)[^"]+"'
        assert not re.findall(pattern, html())

    def test_its_only_remote_dependency_is_pdfjs(self) -> None:
        """Documented in the README, and the only thing a first load needs."""
        remote = set(re.findall(r'https://[^"\s]+', html()))
        assert remote
        assert all("pdf.js" in url or "pdfjs" in url.lower() for url in remote), remote

    def test_it_is_small_enough_to_read(self) -> None:
        """It is meant to be auditable by the person relying on it."""
        assert len(html()) < 80_000


class TestItReadsWhatThisProjectWrites:
    """The conventions the inspector hard-codes must match the ones Plannotation emits."""

    def test_it_looks_for_plannotations_in_the_name_tree(self) -> None:
        """It reads the EmbeddedFiles name tree.

        pdf.js exposes that tree through getAttachments, which is why the carrier
        registers every file there as well as on the page's own /AF.
        """
        assert "getAttachments" in html()

    def test_its_filename_pattern_matches_the_one_we_write(self) -> None:
        """A pattern that drifted from plannotation_filename would find nothing at all."""
        pattern = r"^plannotation-p([0-9]{4}|[1-9][0-9]{4,})\.json$"
        assert f"/{pattern}/" in html(), "the inspector's filename pattern has changed shape"
        compiled = re.compile(pattern)
        for index in (0, 7, 1234, 12345):
            assert compiled.match(plannotation_filename(index))
        for foreign in ("plannotation-p1.json", "plannotation-p00000.json"):
            assert not compiled.match(foreign)

    def test_it_flips_paper_coordinates(self) -> None:
        """SPEC 3.3: paper is y-up from the bottom left, a canvas is y-down."""
        assert "h - y" in html() or "(h - y)" in html()

    def test_it_knows_the_conformance_levels(self) -> None:
        """So it can say what it is looking at without asking anything else."""
        source = html()
        for level in ("L1", "L2", "L3"):
            assert f'"{level}"' in source

    def test_it_draws_every_kind_of_thing_a_plannotation_holds(self) -> None:
        """Viewports, elements and annotations, each separately switchable."""
        source = html()
        for kind in ("viewports", "elements", "annotations"):
            assert kind in source

    def test_it_copies_json_and_exports_csv(self) -> None:
        """Copy JSON and export CSV."""
        source = html()
        assert "clipboard.writeText" in source
        assert "text/csv" in source

    def test_it_escapes_what_it_prints(self) -> None:
        """A sheet title is text somebody typed, and it goes into the DOM."""
        assert "&amp;" in html()

    def test_a_plannotation_that_will_not_parse_is_treated_as_absent(self) -> None:
        """SPEC 4.3 (2), which binds a reader whatever language it is written in."""
        assert "catch" in html()


class TestItOpensADrawingFromALink:
    """The site links straight to a drawing; a copy beside the drawings works as before."""

    def test_it_reads_the_pdf_and_page_from_the_address(self) -> None:
        """``?pdf=<url>&page=<n>``, the page a whole number from 1."""
        source = html()
        assert "new URLSearchParams(location.search)" in source
        assert 'linked.get("pdf")' in source
        assert 'Number.parseInt(linked.get("page")' in source

    def test_it_opens_only_pdfs_on_its_own_site(self) -> None:
        """Nobody can show a stranger's PDF under this site's name."""
        source = html()
        assert "url?.origin !== location.origin" in source
        assert "is not on this site" in source

    def test_pdfjs_never_evaluates_code_from_the_pdf(self) -> None:
        """A PDF from a link is opened with pdf.js's eval path switched off."""
        assert "isEvalSupported: false" in html()

    def test_its_examples_are_where_the_site_stages_them(self) -> None:
        """The picker reads the index stage_site.py writes, beside the inspector's folder."""
        stage_site = _stage_site()
        assert f'const EXAMPLES = "../{stage_site.EXAMPLES.as_posix()}/";' in html()
        assert "${EXAMPLES}index.json" in html()

    def test_its_picker_reads_fields_the_index_has(self) -> None:
        """The option names the sheet by its number and title, and opens its PDF."""
        source = html()
        for field in ("pdf", "sheet", "title"):
            assert f"entry.{field}" in source
            assert field in _stage_site().EXAMPLE_FIELDS

    def test_it_opens_the_first_example_when_the_address_names_none(self) -> None:
        """So the hosted inspector never opens empty."""
        assert "EXAMPLES + list[0].pdf" in html()

    def test_its_address_stays_a_link_to_what_is_shown(self) -> None:
        """Choosing an example or turning a page rewrites the address."""
        assert "history.replaceState" in html()


class TestItFitsTheScreen:
    """A phone gets the whole sheet, sharp, and a tap does what a hover does."""

    def test_it_fits_the_width_by_default(self) -> None:
        """The zoom starts at fit, and fit is re-measured when the stage changes size."""
        source = html()
        assert '<option value="fit" selected>' in source
        assert 'scale: "fit"' in source
        assert "new ResizeObserver" in source

    def test_it_renders_at_the_screen_density_within_a_canvas_limit(self) -> None:
        """Sharp on a high-density screen, but under the 16.7 M-pixel iOS canvas limit."""
        source = html()
        assert "devicePixelRatio" in source
        assert "16e6" in source

    def test_a_tap_shows_the_tooltip(self) -> None:
        """Pointer events, and a touch's tip is not hidden by its own pointerleave."""
        source = html()
        assert 'addEventListener("pointerdown", tipAt)' in source
        assert 'event.pointerType === "mouse"' in source

    def test_the_overlay_keeps_paper_colours_in_the_dark_theme(self) -> None:
        """The page is white paper in either theme, so the dark theme leaves them alone."""
        dark = re.findall(r'(?:prefers-color-scheme: dark\)|data-theme="dark"\]) \{[^}]*\}', html())
        assert len(dark) == 2
        for block in dark:
            for colour in ("--element", "--annotation", "--viewport"):
                assert colour not in block

    def test_a_failure_says_why(self) -> None:
        """An address that cannot be opened gets a note, not a blank stage."""
        assert "Could not open ${name}: ${error.message}" in html()

    def test_a_failure_forgets_the_last_drawing(self) -> None:
        """No empty box the size of the last drawing, and no tooltip for what was in it.

        This only reads the source; docs/inspector.md's manual check tries it.
        """
        failure = re.search(
            r"\} catch \(error\) \{\n    if \(opening !== state\.opening\) return;\n.*?\n  \}\n",
            html(),
            re.DOTALL,
        )
        assert failure is not None
        for reset in (
            "state.ticket++",
            "state.task?.cancel()",
            "hit: []",
            "size: null",
            'canvas.style.width = canvas.style.height = ""',
            'el("tip").style.display = "none"',
        ):
            assert reset in failure.group(0)


def _stage_site() -> ModuleType:
    """Load tools/stage_site.py, which is a script rather than a module.

    Returns:
        The module.
    """
    loader = importlib.util.spec_from_file_location(
        "stage_site", Path(__file__).parent.parent / "tools" / "stage_site.py"
    )
    assert loader is not None
    assert loader.loader is not None
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    return module


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
        not (SAMPLES / "floorplan" / "sheet.plannotated.pdf").exists(),
        reason="samples are not built; run make samples",
    )
    def test_it_prints_a_table_for_a_sample(self) -> None:
        """The sheet, its viewports, its elements and its annotations."""
        result = self._run(str(SAMPLES / "floorplan" / "sheet.plannotated.pdf"))
        assert result.exit_code == 0
        assert "ARC-101" in result.stdout

    @pytest.mark.skipif(
        not (SAMPLES / "floorplan" / "sheet.plannotated.pdf").exists(),
        reason="samples are not built; run make samples",
    )
    def test_json_output_parses(self) -> None:
        """--json is for a pipe, so it must be JSON and nothing else."""
        result = self._run("--json", str(SAMPLES / "floorplan" / "sheet.plannotated.pdf"))
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["pages"]["0"]["sheet"]["id"] == "ARC-101"

    def test_a_document_with_no_plannotations_says_so(self, tmp_path: Path) -> None:
        """Rather than printing an empty table, which reads like an empty drawing."""
        import tests.pdf_fixtures as fx

        plain = tmp_path / "plain.pdf"
        plain.write_bytes(fx.build_drawing_set())
        result = self._run(str(plain))
        assert result.exit_code == 0
        assert "no plannotations" in result.stdout

    def test_an_unreadable_document_exits_two(self, tmp_path: Path) -> None:
        """Two is "could not read this", which is not the same as "nothing in it"."""
        broken = tmp_path / "broken.json"
        broken.write_text("{ not json", encoding="utf-8")
        assert self._run(str(broken)).exit_code == 2

    def test_asking_for_a_page_with_no_plannotation_exits_two(self, tmp_path: Path) -> None:
        """A silent empty result would read as "this page has nothing on it"."""
        import tests.pdf_fixtures as fx

        plain = tmp_path / "plain.pdf"
        plain.write_bytes(fx.build_drawing_set())
        assert self._run("--page", "5", str(plain)).exit_code == 2
