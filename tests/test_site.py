# SPDX-License-Identifier: Apache-2.0
"""The public site as tools/stage_site.py stages it: the landing page and its examples.

The fixture in ``tests/fixtures/site-examples`` stands in for what ``make examples``
writes: an ``index.json`` and, per sheet, a PDF and a thumbnail. Its PDFs are never
opened, so they are stand-ins; its PNGs are real, so their sizes can be read.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from plannotation.constants import BASE_URL

if TYPE_CHECKING:
    from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "site-examples"
#: How a credit begins: Plannotation drew the sheets, not the model's author.
CREDIT = "Drawn and annotated by Plannotation"


def _load() -> ModuleType:
    """Load tools/stage_site.py, which is a script rather than a module.

    Returns:
        The module.
    """
    loader = importlib.util.spec_from_file_location(
        "stage_site", REPO_ROOT / "tools" / "stage_site.py"
    )
    assert loader is not None
    assert loader.loader is not None
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    return module


stage_site = _load()


@pytest.fixture
def site(tmp_path: Path) -> Path:
    """Stage the site with the fixture's examples.

    Args:
        tmp_path: pytest's temporary directory.

    Returns:
        The site root.
    """
    stage_site.stage(tmp_path / "site", FIXTURE)
    return tmp_path / "site"


def landing(site: Path) -> str:
    """Read the staged landing page.

    Args:
        site: The site root.

    Returns:
        Its HTML.
    """
    return (site / "index.html").read_text("utf-8")


def examples_copy(tmp_path: Path, change: dict[str, Any] | None = None) -> Path:
    """Copy the fixture, changing the second example's fields.

    Args:
        tmp_path: Where to put the copy.
        change: Fields to set on the second example.

    Returns:
        The copy's directory.
    """
    copy = tmp_path / "examples"
    shutil.copytree(FIXTURE, copy)
    index = json.loads((copy / "index.json").read_text("utf-8"))
    index["examples"][1].update(change or {})
    (copy / "index.json").write_text(json.dumps(index), "utf-8")
    return copy


class TestTheLandingPageShowsTheExamples:
    """One card per sheet, each opening that sheet in the inspector."""

    def test_each_example_opens_in_the_inspector(self, site: Path) -> None:
        """The thumbnail and the title both link to ``inspector/?pdf=../examples/<pdf>``."""
        page = landing(site)
        for name in ("A-101", "A-301"):
            assert page.count(f'href="inspector/?pdf=../examples/{name}.pdf"') == 2
            assert f'src="examples/{name}.png"' in page
            assert f'href="examples/{name}.pdf"' in page
        assert page.index("A-101") < page.index("A-301"), "the index's order is kept"

    def test_the_files_are_staged_as_they_are(self, site: Path) -> None:
        """The inspector's picker reads the same index the cards were made from."""
        for name in ("index.json", "A-101.pdf", "A-101.png", "A-301.pdf", "A-301.png"):
            assert (site / "examples" / name).read_bytes() == (FIXTURE / name).read_bytes()

    def test_a_card_gives_paper_scale_and_counts(self, site: Path) -> None:
        """Counted in words that agree with the number."""
        page = landing(site)
        assert "A1, 1:100. 3 elements, 1 annotation." in page
        assert "A2, 1:50. 1 element, 0 annotations." in page

    def test_what_the_index_says_is_escaped(self, site: Path) -> None:
        """A sheet title is text somebody typed."""
        page = landing(site)
        assert "A-101&ensp;Ground floor &amp; stair</a>" in page
        assert "A-301&ensp;Section &lt;A-A&gt;</a>" in page
        assert "<A-A>" not in page

    def test_a_placeholder_in_a_title_stays_text(self, tmp_path: Path) -> None:
        """The template is filled in one pass, so a value is never filled in itself."""
        examples = examples_copy(tmp_path, {"title": "{{og}}"})
        stage_site.stage(tmp_path / "site", examples)
        assert "A-301&ensp;{{og}}</a>" in landing(tmp_path / "site")

    def test_a_thumbnail_carries_its_size(self, site: Path) -> None:
        """So the page does not jump as the images arrive."""
        page = landing(site)
        assert 'src="examples/A-101.png" width="4" height="3"' in page
        assert 'src="examples/A-301.png" width="3" height="2"' in page

    def test_one_source_is_credited_once(self, site: Path) -> None:
        """As CC BY asks: title, copyright, licence, links to both, and what changed.

        The author made the model, not the sheets, and endorses nothing.
        """
        page = landing(site)
        assert page.count(CREDIT) == 1
        assert (
            f'{CREDIT} from <a href="https://example.org/model">Test model</a> (changes made);'
            " model &copy; Test Author, "
            '<a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>. '
            "Not drawn or endorsed by Test Author."
        ) in page
        assert "via" not in page

    def test_a_source_can_say_where_the_model_came_from(self, tmp_path: Path) -> None:
        """``source.via``, when the model was obtained from someone other than its author."""
        examples = examples_copy(tmp_path)
        index = json.loads((examples / "index.json").read_text("utf-8"))
        for entry in index["examples"]:
            entry["source"]["via"] = "Sample Files & Co."
        (examples / "index.json").write_text(json.dumps(index), "utf-8")
        stage_site.stage(tmp_path / "site", examples)
        assert "model &copy; Test Author, via Sample Files &amp; Co., <a " in landing(
            tmp_path / "site"
        )

    def test_several_sources_are_credited_on_each_card(self, tmp_path: Path) -> None:
        """Each sheet then names its own."""
        other = {
            "title": "Other model",
            "url": "https://example.org/other",
            "author": "Other Author",
            "license": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
        }
        stage_site.stage(tmp_path / "site", examples_copy(tmp_path, {"source": other}))
        page = landing(tmp_path / "site")
        assert page.count(CREDIT) == 2
        assert "Other model</a> (changes made); model &copy; Other Author," in page
        assert "Not drawn or endorsed by Other Author." in page
        assert page.index("Test model") < page.index("A-301") < page.index("Other model")

    def test_a_shared_link_shows_the_first_example(self, site: Path) -> None:
        """Open Graph names the first thumbnail by its absolute URL."""
        page = landing(site)
        assert f'<meta property="og:image" content="{BASE_URL}/examples/A-101.png">' in page
        assert 'content="summary_large_image"' in page

    def test_the_command_to_run_uses_the_first_example(self, site: Path) -> None:
        """A first run that works on a real file, with no drawing of one's own."""
        page = landing(site)
        assert f"curl -O {BASE_URL}/examples/A-101.pdf\nuv run plannotation inspect A-101.pdf" in (
            page
        )

    def test_no_placeholder_is_left(self, site: Path) -> None:
        """Every ``{{...}}`` in the template is filled, and no block marker is left."""
        page = landing(site)
        assert not re.search(r"\{\{\w+\}\}", page)
        assert "<!-- card -->" not in page
        assert "<!-- examples -->" not in page

    def test_it_links_the_spec_and_every_schema_at_their_urls(self, site: Path) -> None:
        """The same paths the schemas' $id and SPEC_URI name, read from them."""
        page = landing(site)
        assert 'href="spec/0.1/"' in page
        for schema in sorted((REPO_ROOT / "plannotation" / "schema").glob("*.json")):
            schema_id = json.loads(schema.read_text("utf-8"))["$id"]
            assert f'href="{schema_id.removeprefix(f"{BASE_URL}/")}"' in page


class TestWithoutExamples:
    """``make docs`` before ``make examples``: the rest of the page still stands."""

    def test_the_examples_are_left_out(self, tmp_path: Path) -> None:
        """No cards, no credit, no preview image, and nothing staged under examples/."""
        stage_site.stage(tmp_path)
        page = landing(tmp_path)
        assert '<ul class="sheets">' not in page
        assert CREDIT not in page
        assert "og:image" not in page
        assert "curl" not in page
        assert "uv run plannotation inspect drawing.plannotated.pdf" in page
        assert not (tmp_path / "examples").exists()


class TestTheIndexIsChecked:
    """A malformed index stops the staging instead of publishing a broken card."""

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ({"title": None}, "title must be a string"),
            ({"elements": True}, "elements must be an integer"),
            ({"annotations": -1}, "cannot be negative"),
            ({"pdf": "../A-101.pdf"}, "must be a .pdf file name"),
            ({"png": "A-301.jpg"}, "must be a .png file name"),
            ({"png": "missing.png"}, "does not exist"),
            ({"source": {"title": "t", "url": "u", "author": "a", "license": "l"}}, "license_url"),
            (
                {
                    "source": {
                        "title": "t",
                        "url": "https://example.org/",
                        "author": "a",
                        "license": "l",
                        "license_url": "https://example.org/",
                        "via": "",
                    }
                },
                "source.via must be a non-empty string",
            ),
            (
                {
                    "source": {
                        "title": "t",
                        "url": "javascript:alert(1)",
                        "author": "a",
                        "license": "l",
                        "license_url": "https://example.org/",
                    }
                },
                "must be an http",
            ),
        ],
    )
    def test_a_bad_entry_is_refused(
        self, tmp_path: Path, change: dict[str, Any], message: str
    ) -> None:
        """Each field has the type the landing page and the inspector expect."""
        with pytest.raises(stage_site.SiteError, match=re.escape(message)):
            stage_site.stage(tmp_path / "site", examples_copy(tmp_path, change))

    def test_an_empty_or_missing_index_is_refused(self, tmp_path: Path) -> None:
        """An examples directory with nothing to show is a mistake, not an empty page."""
        with pytest.raises(stage_site.SiteError, match="not an examples index"):
            stage_site.stage(tmp_path / "site", tmp_path)
        (tmp_path / "index.json").write_text('{"examples": []}', "utf-8")
        with pytest.raises(stage_site.SiteError, match="non-empty list"):
            stage_site.stage(tmp_path / "site", tmp_path)

    def test_a_thumbnail_that_is_not_a_png_is_refused(self, tmp_path: Path) -> None:
        """Its size is read from the PNG header."""
        examples = examples_copy(tmp_path)
        (examples / "A-301.png").write_bytes(b"GIF89a")
        with pytest.raises(stage_site.SiteError, match="is not a PNG"):
            stage_site.stage(tmp_path / "site", examples)


class TestEveryLinkLands:
    """A published URL is a promise; a staged page may not link to nothing."""

    @staticmethod
    def _page(tmp_path: Path, body: str, staged: set[str]) -> list[str]:
        for page in (path for path in staged if path.endswith(".html")):
            (tmp_path / page).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / page).write_text("<!DOCTYPE html>", "utf-8")
        (tmp_path / "index.html").write_text(f"<!DOCTYPE html><body>{body}</body>", "utf-8")
        return stage_site.broken_links(tmp_path, {"index.html", *staged})

    def test_the_staged_site_has_no_broken_link(self, site: Path) -> None:
        """Every href and src on the landing page and the inspector reaches a file."""
        staged = {path.relative_to(site).as_posix() for path in site.rglob("*") if path.is_file()}
        assert stage_site.broken_links(site, staged) == []

    def test_a_link_to_a_missing_file_is_found(self, tmp_path: Path) -> None:
        """Relative, root-relative and absolute links to the site all count."""
        broken = self._page(
            tmp_path,
            '<a href="gone.html">x</a><img src="/gone.png">'
            f'<a href="{BASE_URL}/gone.json">x</a><a href="here.pdf">x</a>',
            {"here.pdf"},
        )
        assert broken == [
            "index.html: gone.html",
            "index.html: /gone.png",
            f"index.html: {BASE_URL}/gone.json",
        ]

    def test_the_pdf_a_link_hands_the_inspector_must_be_staged(self, tmp_path: Path) -> None:
        """``?pdf=`` resolves against the inspector's own address."""
        staged = {"inspector/index.html", "examples/here.pdf"}
        body = (
            '<a href="inspector/?pdf=../examples/here.pdf">x</a>'
            '<a href="inspector/?pdf=../examples/gone.pdf">x</a>'
        )
        assert self._page(tmp_path, body, staged) == [
            "index.html: inspector/?pdf=../examples/gone.pdf"
        ]

    def test_a_directory_needs_a_page_in_it(self, tmp_path: Path) -> None:
        """An index.html, or an index.md the site renders to one."""
        staged = {"spec/0.1/index.md", "docs/readme.md"}
        body = '<a href="spec/0.1/">x</a><a href="docs/">x</a><a href="./">x</a>'
        assert self._page(tmp_path, body, staged) == ["index.html: docs/"]

    def test_links_elsewhere_are_not_followed(self, tmp_path: Path) -> None:
        """Other sites, data: URIs, mail and fragments are not the site's to stage."""
        body = (
            '<a href="https://github.com/plannotation/plannotation">x</a>'
            '<link rel="icon" href="data:image/svg+xml,%3Csvg%3E">'
            '<a href="mailto:someone@example.org">x</a><a href="#top">x</a>'
        )
        assert self._page(tmp_path, body, set()) == []

    def test_a_template_linking_to_nothing_stops_the_staging(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The check runs on every stage, not only in this test."""
        template = tmp_path / "index.html"
        text = stage_site.LANDING.read_text("utf-8")
        template.write_text(text.replace("</main>", '<a href="gone/">x</a></main>'), "utf-8")
        monkeypatch.setattr(stage_site, "LANDING", template)
        with pytest.raises(stage_site.SiteError, match=r"index\.html: gone/"):
            stage_site.stage(tmp_path / "site", FIXTURE)


class TestThePlan:
    """The layout, for a step that runs after the staging and must not stage again."""

    def test_it_is_what_staging_returns_and_it_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The same pairs, in the same order, and not a file written."""
        monkeypatch.chdir(tmp_path)
        planned = stage_site.plan(FIXTURE)
        assert not any(tmp_path.iterdir())
        assert planned == stage_site.stage(tmp_path / "site", FIXTURE)
        assert planned[-1] == (stage_site.LANDING, Path("index.html"))

    def test_its_sources_are_absolute_when_the_examples_path_is_not(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """So a caller can take each one relative to the repository."""
        monkeypatch.chdir(FIXTURE.parent)
        planned = stage_site.plan(Path(FIXTURE.name))
        assert all(source.is_absolute() for source, _ in planned)
        assert (FIXTURE.resolve() / "A-101.pdf", Path("examples/A-101.pdf")) in planned

    def test_staging_again_with_the_same_examples_keeps_the_cards(self, site: Path) -> None:
        """Only a second call without the examples would take them off."""
        first = landing(site)
        stage_site.stage(site, FIXTURE)
        assert landing(site) == first
        assert '<ul class="sheets">' in first


class TestTheCommand:
    """``python tools/stage_site.py OUT --examples DIR``."""

    def test_it_stages_the_examples(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """And says where each file went."""
        assert stage_site.main([str(tmp_path), "--examples", str(FIXTURE)]) == 0
        assert (tmp_path / "examples" / "A-101.pdf").is_file()
        assert "web/index.html -> " in capsys.readouterr().out

    def test_a_bad_index_exits_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """With the reason, and no traceback."""
        assert stage_site.main([str(tmp_path / "site"), "--examples", str(tmp_path)]) == 1
        assert "error: " in capsys.readouterr().err
