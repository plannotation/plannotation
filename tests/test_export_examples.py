# SPDX-License-Identifier: Apache-2.0
"""``make examples``: the pinned cache, the contract the site reads, and the thumbnails.

No test here reaches the network. The pinned model is stood in for by a small house
built with ``ifcopenshell.api``, placed in a temporary cache under a pinned hash of its
own, and a download is a patched ``urlopen``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import math
from dataclasses import replace
from datetime import UTC, datetime
from itertools import pairwise
from typing import TYPE_CHECKING, Any

import pytest

from plannotation.errors import ExportError
from plannotation.export import examples
from plannotation.export.examples import MALEVA_18, Example, Source, cache_dir, fetch

if TYPE_CHECKING:
    from pathlib import Path

    from plannotation.export.ifc_svg_pdf import SheetSpec
    from plannotation.export.models import BuiltModel

needs_ifc = pytest.mark.skipif(
    importlib.util.find_spec("ifcopenshell") is None, reason="ifcopenshell is not installed"
)
needs_cairo = pytest.mark.skipif(
    importlib.util.find_spec("cairosvg") is None, reason="cairosvg is not installed"
)

MOD_DATE = datetime(2024, 1, 1, tzinfo=UTC)


def _pinned(content: bytes, name: str = "model.ifc") -> Source:
    """Return a source pinned to some bytes.

    Args:
        content: The file's bytes.
        name: The file's name in the cache.

    Returns:
        The source.
    """
    return replace(
        MALEVA_18,
        filename=name,
        url="https://example.invalid/model.ifc",
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )


class TestThePinnedModels:
    """The pin is the brief's: a changed number is a different model."""

    def test_maleva_18_is_pinned(self) -> None:
        """URL, size and SHA-256 as published."""
        assert MALEVA_18.url.endswith("/Esplanades/1807_EP_AR_v18.ifc")
        assert MALEVA_18.size == 12_109_172
        assert MALEVA_18.sha256 == (
            "b97db10a2cb679a6543ccf4e34cfa8e77753f14e67f51b403e6cd9a819e3748f"
        )

    def test_the_attribution_credits_the_author_and_disowns_the_drawing(self) -> None:
        """CC BY 4.0 asks for the credit, the licence and a note of changes."""
        for part in ("© Esplan OÜ", "CC BY 4.0", "changes made", "Not an Esplan drawing"):
            assert part in MALEVA_18.attribution

    def test_the_examples_are_the_two_sheets_the_site_shows(self) -> None:
        """Their names are the files' names, and the index's."""
        assert [example.name for example in examples.EXAMPLES] == ["M18-101", "M18-301"]


class TestTheCache:
    """Downloaded once, verified always, and never trusted on its name alone."""

    def test_the_command_line_beats_the_environment_beats_the_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """So an existing cache can be pointed at without copying 12 MB."""
        monkeypatch.delenv(examples.CACHE_ENV, raising=False)
        assert cache_dir() == examples.DEFAULT_CACHE
        monkeypatch.setenv(examples.CACHE_ENV, str(tmp_path / "env"))
        assert cache_dir() == tmp_path / "env"
        assert cache_dir(tmp_path / "flag") == tmp_path / "flag"

    def test_a_cached_file_with_the_pinned_hash_is_used(self, tmp_path: Path) -> None:
        """Without a download."""
        content = b"ISO-10303-21;\n"
        (tmp_path / "model.ifc").write_bytes(content)
        assert fetch(_pinned(content), tmp_path) == tmp_path / "model.ifc"

    def test_a_cached_file_with_another_hash_is_refused(self, tmp_path: Path) -> None:
        """A model that is not the pinned one would draw a different building."""
        (tmp_path / "model.ifc").write_bytes(b"something else")
        with pytest.raises(ExportError, match="SHA-256"):
            fetch(_pinned(b"ISO-10303-21;\n"), tmp_path)

    def test_a_missing_file_is_downloaded_and_verified(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Into the cache, under its pinned name, with nothing left half-written."""
        content = b"ISO-10303-21;\nEND-ISO-10303-21;\n"
        asked: list[str] = []

        def urlopen(url: str, timeout: float) -> io.BytesIO:
            asked.append(url)
            assert timeout > 0
            return io.BytesIO(content)

        monkeypatch.setattr(examples.urllib.request, "urlopen", urlopen)
        path = fetch(_pinned(content), tmp_path / "cache")
        assert path.read_bytes() == content
        assert asked == ["https://example.invalid/model.ifc"]
        assert sorted(p.name for p in (tmp_path / "cache").iterdir()) == ["model.ifc"]

    def test_a_download_with_another_hash_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Whatever the server sends."""
        monkeypatch.setattr(
            examples.urllib.request, "urlopen", lambda *_, **__: io.BytesIO(b"tampered")
        )
        with pytest.raises(ExportError, match="pinned"):
            fetch(_pinned(b"ISO-10303-21;\n"), tmp_path)


@needs_ifc
@needs_cairo
class TestTheContract:
    """What ``make examples`` writes is what the site reads."""

    @staticmethod
    def _build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Build one stand-in example into ``tmp_path / "out"``.

        The raised house of ``test_export_real``, with the low box outside it that its
        plan sees below the cut, goes into a cache under a pin of its own, and the example
        list is swapped for one sheet drawn from it.

        Args:
            tmp_path: Scratch space.
            monkeypatch: pytest's monkeypatch.

        Returns:
            The output directory.
        """
        from tests.test_export_real import _drawn_plan

        cache = tmp_path / "cache"
        cache.mkdir(parents=True)
        built, _ = _drawn_plan(tmp_path, behind=True)
        content = built.path.read_bytes()
        (cache / "raised.ifc").write_bytes(content)
        source = _pinned(content, "raised.ifc")

        def draw(model: Path) -> tuple[BuiltModel, SheetSpec]:
            from plannotation.export.drafting import ViewTitle
            from tests.test_export_real import _spec

            spec = _spec(sheet_id="X-101", page_size="A2", view_title=ViewTitle("Ground floor"))
            return replace(built, path=model), spec

        monkeypatch.setattr(examples, "EXAMPLES", (Example("X-101", source, draw),))
        out = tmp_path / "out"
        examples.build_examples(out, cache=cache, mod_date=MOD_DATE, version="0.0.0-test")
        return out

    def test_each_sheet_has_its_pdf_its_png_and_an_index_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing more: the model stays in the cache."""
        out = self._build(tmp_path, monkeypatch)
        assert sorted(p.name for p in out.iterdir()) == ["X-101.pdf", "X-101.png", "index.json"]

    def test_the_index_has_the_contract_s_shape(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Files, sheet, title, paper, scale, counts and the source's credit."""
        from plannotation.pdf import embed

        out = self._build(tmp_path, monkeypatch)
        (entry,) = json.loads((out / "index.json").read_text("utf-8"))["examples"]
        plannotation = embed.read(out / "X-101.pdf").pages[0]
        assert entry == {
            "name": "X-101",
            "pdf": "X-101.pdf",
            "png": "X-101.png",
            "sheet": "X-101",
            "title": "Ground floor",
            "paper": "A2",
            "scale": "1:50",
            "elements": len(plannotation.elements or []),
            "annotations": len(plannotation.annotations or []),
            "source": {
                "title": MALEVA_18.title,
                "url": MALEVA_18.page,
                "author": "Esplan OÜ",
                "license": "CC BY 4.0",
                "license_url": "https://creativecommons.org/licenses/by/4.0/",
            },
        }

    def test_the_thumbnail_is_1200_px_wide_with_the_inspector_s_outlines(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Element blue, lighter beyond the cut, annotation amber, viewport green."""
        from PIL import Image

        out = self._build(tmp_path, monkeypatch)
        with Image.open(out / "X-101.png") as image:
            assert image.width == 1200
            assert image.height == pytest.approx(1200 * 420 / 594, abs=1)
            colours = {colour for _, colour in image.convert("RGB").getcolors(1 << 20) or []}
        assert (0x1D, 0x6F, 0xA5) in colours
        assert (0xA4, 0xC5, 0xDB) in colours
        assert (0x9A, 0x5B, 0x00) in colours
        assert (0x3F, 0x7D, 0x3F) in colours

    def test_the_light_blue_marks_what_is_seen_beyond_the_cut(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On the box below the plan's cut the light blue, on a cut wall the element blue.

        Each is sampled where the thumbnail draws the element's outline: the middle of the
        box's long side, and the middle of the north wall's outer face, taken from paper
        millimetres to pixels as the thumbnail takes them.
        """
        from PIL import Image

        from plannotation.pdf import embed

        out = self._build(tmp_path, monkeypatch)
        plannotation = embed.read(out / "X-101.pdf").pages[0]
        elements = {e.name: e for e in plannotation.elements or []}
        box, wall = elements["Behind"], elements["North"]
        assert (box.representation, wall.representation) == ("projection", "cut")
        seen, cut = (0xA4, 0xC5, 0xDB), (0x1D, 0x6F, 0xA5)
        with Image.open(out / "X-101.png") as image:
            pixels = image.convert("RGB")
        scale = pixels.width / plannotation.page.width_mm

        def around(x_mm: float, y_mm: float) -> set[tuple[int, int, int]]:
            """Return the colours within a pixel of a paper point.

            Args:
                x_mm: Paper x.
                y_mm: Paper y, up from the page's bottom edge.

            Returns:
                The colours of the nine pixels around it.
            """
            x, y = round(x_mm * scale), round((plannotation.page.height_mm - y_mm) * scale)
            return {pixels.getpixel((x + dx, y + dy)) for dx in (-1, 0, 1) for dy in (-1, 0, 1)}

        a, b = max(
            (side for outline in box.paper_outlines or [] for side in pairwise(outline)),
            key=lambda side: math.dist(*side),
        )
        on_box = around((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        assert seen in on_box
        assert cut not in on_box
        top = max(y for outline in wall.paper_outlines or [] for _, y in outline)
        (a, b), *_ = [
            (a, b)
            for outline in wall.paper_outlines or []
            for a, b in pairwise(outline)
            if a[1] == b[1] == top
        ]
        on_wall = around((a[0] + b[0]) / 2.0, top)
        assert cut in on_wall
        assert seen not in on_wall

    def test_a_rebuild_is_byte_identical(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PDF, PNG and index alike."""
        first = self._build(tmp_path / "one", monkeypatch)
        second = self._build(tmp_path / "two", monkeypatch)
        for path in sorted(first.iterdir()):
            assert path.read_bytes() == (second / path.name).read_bytes(), path.name


def _build_script() -> Any:  # noqa: ANN401 - a script loaded by path
    """Load tools/build_examples.py, which is a script rather than a module.

    Returns:
        The module.
    """
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "tools" / "build_examples.py"
    loader = importlib.util.spec_from_file_location("build_examples", script)
    assert loader is not None
    assert loader.loader is not None
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    return module


@needs_ifc
@needs_cairo
class TestTheCheck:
    """``make examples-check``: each sheet validated against its own model, any finding fatal."""

    def test_a_sheet_drawn_from_its_model_has_nothing_to_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The stand-in sheet, against the stand-in model in the cache: no finding."""
        out = TestTheContract._build(tmp_path, monkeypatch)
        reports = examples.check_examples(out, cache=tmp_path / "cache")
        assert list(reports) == ["X-101"]
        assert reports["X-101"].findings == ()
        assert len(reports["X-101"].pages) == 1

    def test_the_script_fails_on_any_finding_and_says_which(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A warning is enough; without ``--check`` the same build succeeds."""
        from plannotation.validate import Finding, Severity

        TestTheContract._build(tmp_path, monkeypatch)
        script = _build_script()
        arguments = ["--out", str(tmp_path / "again"), "--cache", str(tmp_path / "cache")]
        assert script.main([*arguments, "--check"]) == 0
        real = examples.validate

        def one_warning(source: Path, *, ifc_model: Path) -> Any:  # noqa: ANN401
            report = real(source, ifc_model=ifc_model)
            finding = Finding(
                code="PL-IFC-003",
                severity=Severity.WARNING,
                message="a dimension disagrees with the model",
                path="/annotations/0",
                source="page 0",
                rule="",
                reference="",
            )
            return report.with_findings([finding])

        monkeypatch.setattr(examples, "validate", one_warning)
        capsys.readouterr()
        assert script.main(arguments) == 0
        assert script.main([*arguments, "--check"]) == 1
        printed = capsys.readouterr().out
        assert "X-101: warning PL-IFC-003 at /annotations/0" in printed
        assert "X-101: 1 finding(s) against its model" in printed
