# SPDX-License-Identifier: Apache-2.0
"""The public site as tools/render_site.py renders it: its URLs, its links, its promise.

The renderer's two packages are not project dependencies, so these tests pin what can
be checked without them: where each staged file is served, how a page's links are
rewritten, and the check that every schema's ``$id`` and the spec URI resolve.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from plannotation.constants import BASE_URL, SPEC_URI

if TYPE_CHECKING:
    from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = "https://github.com/plannotation/plannotation"


def _load() -> ModuleType:
    """Load tools/render_site.py, which is a script rather than a module.

    Returns:
        The module.
    """
    loader = importlib.util.spec_from_file_location(
        "render_site", REPO_ROOT / "tools" / "render_site.py"
    )
    assert loader is not None
    assert loader.loader is not None
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    return module


render_site = _load()


class TestServed:
    """A Markdown page is served as HTML at a clean URL; anything else as it is."""

    @pytest.mark.parametrize(
        ("target", "url"),
        [
            ("spec/0.1/index.md", "/spec/0.1/"),
            ("index.md", "/"),
            ("docs/examples.md", "/docs/examples.html"),
            ("schema/0.1/plannotation.json", "/schema/0.1/plannotation.json"),
            ("inspector/index.html", "/inspector/index.html"),
        ],
    )
    def test_served(self, target: str, url: str) -> None:
        """Each kind of file."""
        assert render_site.served(Path(target)) == url


#: Where three sources are served, as render_site works it out from the layout.
URL_OF = {
    "spec/SPEC.md": "/spec/0.1/",
    "plannotation/schema/x.json": "/schema/0.1/x.json",
    "examples/M18-101.png": "/examples/M18-101.png",
}


class TestRewrite:
    """A page's relative links go to the site where it serves the file, else to GitHub."""

    @staticmethod
    def _rewrite(url: str, *, image: bool = False) -> str:
        """Rewrite a link written in the spec.

        Args:
            url: The link as the spec writes it.
            image: Whether it is an image's.

        Returns:
            The link as the rendered page carries it.
        """
        return str(render_site.rewrite(url, "spec/SPEC.md", URL_OF, image=image))

    def test_a_staged_image_is_served_by_the_site(self) -> None:
        """Not fetched from GitHub when the site has it."""
        assert self._rewrite("../examples/M18-101.png", image=True) == "/examples/M18-101.png"

    @pytest.mark.parametrize(
        "url", ["https://example.com/a.md", "#46-provenance", "/inspector/", "mailto:a@b.c"]
    )
    def test_absolute_links_and_fragments_are_left_alone(self, url: str) -> None:
        """Nothing to resolve."""
        assert self._rewrite(url) == url

    def test_a_staged_file_is_linked_on_the_site_with_its_fragment(self) -> None:
        """The schema the spec links to is served at its ``$id`` path."""
        assert self._rewrite("../plannotation/schema/x.json#/$defs/a") == (
            "/schema/0.1/x.json#/$defs/a"
        )
        assert self._rewrite("SPEC.md#46-provenance") == "/spec/0.1/#46-provenance"

    def test_a_file_the_site_does_not_serve_is_linked_on_github(self) -> None:
        """A file as a blob, a directory as a tree, an image raw."""
        assert self._rewrite("../README.md") == f"{SOURCE}/blob/main/README.md"
        assert self._rewrite("../README.md#try-it") == f"{SOURCE}/blob/main/README.md#try-it"
        assert self._rewrite("../tests/fixtures") == f"{SOURCE}/tree/main/tests/fixtures"
        assert self._rewrite("../docs/img/a.png", image=True) == (
            "https://raw.githubusercontent.com/plannotation/plannotation/main/docs/img/a.png"
        )


class TestThePromise:
    """Rendering fails unless every schema's ``$id`` and the spec URI resolve."""

    @pytest.fixture
    def staged(self, tmp_path: Path) -> tuple[Path, list[tuple[str, Path]]]:
        """Stage the site without examples, as the staging script does.

        Args:
            tmp_path: pytest's temporary directory.

        Returns:
            The site root and its layout.
        """
        site = tmp_path / "site"
        render_site.load_stage_site().stage(site)
        return site, render_site.layout(None)

    def test_the_layout_holds_every_schema_at_its_id(
        self, staged: tuple[Path, list[tuple[str, Path]]]
    ) -> None:
        """Read from the schemas themselves."""
        _, plan = staged
        targets = {source: target.as_posix() for source, target in plan}
        for schema in sorted((REPO_ROOT / "plannotation" / "schema").glob("*.json")):
            identifier = json.loads(schema.read_text("utf-8"))["$id"]
            source = schema.relative_to(REPO_ROOT).as_posix()
            assert BASE_URL + "/" + targets[source] == identifier

    def test_before_rendering_only_the_spec_page_is_missing(
        self, staged: tuple[Path, list[tuple[str, Path]]]
    ) -> None:
        """The staged Markdown has no HTML yet; everything else is in place."""
        site, plan = staged
        spec = SPEC_URI.removeprefix(f"{BASE_URL}/") + "/index.html"
        assert render_site.missing(site, plan) == [spec]
        (site / spec).write_text("<!doctype html>", "utf-8")
        assert render_site.missing(site, plan) == []

    def test_a_schema_not_staged_is_missing(
        self, staged: tuple[Path, list[tuple[str, Path]]]
    ) -> None:
        """Its ``$id`` would answer 404."""
        site, plan = staged
        schema = next(target for _, target in plan if target.suffix == ".json")
        (site / schema).unlink()
        assert schema.as_posix() in render_site.missing(site, plan)

    def test_the_landing_page_is_required_too(
        self, staged: tuple[Path, list[tuple[str, Path]]]
    ) -> None:
        """A site without its home page keeps no promise worth making."""
        site, plan = staged
        (site / "index.html").unlink()
        assert "index.html" in render_site.missing(site, plan)

    def test_the_layout_holds_the_examples_it_is_given(self) -> None:
        """Their index and each sheet's PDF and PNG, as the staging put them."""
        fixture = REPO_ROOT / "tests" / "fixtures" / "site-examples"
        targets = {target.as_posix() for _, target in render_site.layout(fixture)}
        assert {"examples/index.json", "examples/A-101.pdf", "examples/A-101.png"} <= targets


class TestRender:
    """render() writes each page where it is served and checks the promise.

    markdown-it-py is here through rich, but its plugins are not, so the renderer is
    the plain CommonMark one: no heading anchors, no front matter. What render() does
    with what it renders is the same.
    """

    @pytest.fixture
    def plain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Render with markdown-it-py alone.

        Args:
            monkeypatch: pytest's monkeypatch.
        """
        markdown_it = pytest.importorskip("markdown_it")
        monkeypatch.setattr(
            render_site, "_markdown", lambda: markdown_it.MarkdownIt("commonmark").enable("table")
        )

    @pytest.mark.usefixtures("plain")
    def test_the_spec_page_is_written_where_it_is_served(self, tmp_path: Path) -> None:
        """With its title, its canonical URL and its links rewritten."""
        site = tmp_path / "site"
        render_site.load_stage_site().stage(site)
        spec = site / "spec" / "0.1"
        (spec / "index.md").write_text(
            "# The Spec\n\nSee the [schema](../plannotation/schema/plannotation-0.1.json),"
            " the [readme](../README.md) and ![a picture](../docs/img/x.png).\n",
            "utf-8",
        )
        written = render_site.render(site)
        assert written == [spec / "index.html"]
        page = written[0].read_text("utf-8")
        assert "<title>The Spec</title>" in page
        assert f'<link rel="canonical" href="{SPEC_URI}/">' in page
        # The landing page's inline icon: without one the browser asks for /favicon.ico.
        assert render_site.icon().startswith('<link rel="icon" href="data:image/svg+xml,')
        assert render_site.icon() in page
        schema = json.loads(
            (REPO_ROOT / "plannotation" / "schema" / "plannotation-0.1.json").read_text("utf-8")
        )["$id"]
        assert f'href="{schema.removeprefix(BASE_URL)}"' in page
        assert f'href="{SOURCE}/blob/main/README.md"' in page
        assert (
            'src="https://raw.githubusercontent.com/plannotation/plannotation/main/docs/img/x.png"'
            in page
        )

    @pytest.mark.usefixtures("plain")
    def test_a_promise_not_kept_fails_the_render(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A schema not staged: RenderError, and the command says so and exits 1."""
        site = tmp_path / "site"
        staged = render_site.load_stage_site().stage(site)
        schema = next(target for _, target in staged if target.suffix == ".json")
        (site / schema).unlink()
        with pytest.raises(render_site.RenderError, match="not staged"):
            render_site.render(site)
        assert render_site.main([str(site)]) == 1
        assert "not staged" in capsys.readouterr().err


class TestWithoutTheRenderer:
    """The renderer's packages are not project dependencies."""

    @pytest.fixture
    def staged(self, tmp_path: Path) -> tuple[Path, list[tuple[str, Path]]]:
        """Stage the site without examples.

        Args:
            tmp_path: pytest's temporary directory.

        Returns:
            The site root and its layout.
        """
        site = tmp_path / "site"
        render_site.load_stage_site().stage(site)
        return site, render_site.layout(None)

    @pytest.mark.skipif(
        importlib.util.find_spec("mdit_py_plugins") is not None,
        reason="the renderer's packages are installed here",
    )
    def test_without_the_renderer_it_says_how_to_run_it(
        self, staged: tuple[Path, list[tuple[str, Path]]], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Through make site, which supplies markdown-it-py and mdit-py-plugins."""
        site, _ = staged
        assert render_site.main([str(site)]) == 1
        assert "make site" in capsys.readouterr().err
