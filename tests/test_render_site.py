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


#: Where two sources are served, as render_site works it out from the layout.
URL_OF = {"spec/SPEC.md": "/spec/0.1/", "plannotation/schema/x.json": "/schema/0.1/x.json"}


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
