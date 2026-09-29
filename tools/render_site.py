# SPDX-License-Identifier: Apache-2.0
r"""Render every Markdown page tools/stage_site.py staged to an ``index.html`` beside it.

A Pages site deployed from an artifact is served as it is, so nothing else would turn
``spec/0.1/index.md`` into a page at ``/spec/0.1/``. The layout is read from
:func:`stage_site.plan`, given the same examples as the staging, never restated here.

Relative links in a page resolve against its source in the repository: to the staged
page when there is one, otherwise to the file on GitHub. When every page is written,
the promise the site exists to keep is checked: every schema's ``$id`` and the spec
URI resolve to a staged file.

The renderer is markdown-it-py with mdit-py-plugins, which the project does not depend
on. ``make site`` runs this script the way the Pages workflow does:

    uv run --no-project --with markdown-it-py==4.2.0 --with mdit-py-plugins==0.6.1 \\
        python tools/render_site.py [SITE] [--examples DIR]

with the checkout on ``PYTHONPATH`` for :mod:`plannotation.constants`.
"""

from __future__ import annotations

import argparse
import html
import importlib.util
import posixpath
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from plannotation.constants import BASE_URL, SPEC_URI

if TYPE_CHECKING:
    from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Where a link to a file the site does not serve goes instead.
SOURCE = "https://github.com/plannotation/plannotation"

#: Where an image the site does not serve is fetched from.
RAW = "https://raw.githubusercontent.com/plannotation/plannotation/main"

#: The page style, inlined into every rendered page.
STYLE_SHEET = REPO_ROOT / "web" / "site.css"


class RenderError(Exception):
    """The site could not be rendered, or does not keep its promise."""


def load_stage_site() -> ModuleType:
    """Load tools/stage_site.py, which is a script rather than a module.

    Returns:
        The module.

    Raises:
        RenderError: If it cannot be loaded.
    """
    spec = importlib.util.spec_from_file_location(
        "stage_site", REPO_ROOT / "tools" / "stage_site.py"
    )
    if spec is None or spec.loader is None:
        msg = "tools/stage_site.py could not be loaded"
        raise RenderError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def layout(examples: Path | None) -> list[tuple[str, Path]]:
    """Say where the staging put every file, with sources relative to the repository.

    Args:
        examples: The examples directory the site was staged with, or None.

    Returns:
        Each source, as a POSIX path relative to the repository root, and its path
        relative to the site root.
    """
    plan = load_stage_site().plan(examples)
    return [
        (
            source.relative_to(REPO_ROOT).as_posix()
            if source.is_relative_to(REPO_ROOT)
            else source.as_posix(),
            target,
        )
        for source, target in plan
    ]


def served(target: Path) -> str:
    """Return the URL path a staged file is reachable at once rendered.

    Args:
        target: Its path relative to the site root.

    Returns:
        ``/spec/0.1/`` for ``spec/0.1/index.md``, ``/docs/x.html`` for
        ``docs/x.md``, and the path itself for anything that is not Markdown.
    """
    if target.suffix != ".md":
        return "/" + target.as_posix()
    if target.name != "index.md":
        return "/" + target.with_suffix(".html").as_posix()
    parent = target.parent.as_posix()
    return "/" if parent == "." else f"/{parent}/"


def rewrite(
    url: str, page_source: str, url_of: dict[str, str], *, image: bool, repo: Path = REPO_ROOT
) -> str:
    """Point a repository-relative link at the site, or else at GitHub.

    Args:
        url: The link as the page writes it.
        page_source: The page's source, relative to the repository root.
        url_of: The URL path of every staged source.
        image: Whether the link is an image's, which GitHub serves raw.
        repo: The repository root, to tell a directory from a file.

    Returns:
        The link as the rendered page should carry it. Absolute URLs, fragments and
        site-absolute paths are left alone.
    """
    parts = urlsplit(url)
    if parts.scheme or parts.netloc or url.startswith(("#", "/")) or not parts.path:
        return url
    path = posixpath.normpath(posixpath.join(posixpath.dirname(page_source), parts.path))
    fragment = f"#{parts.fragment}" if parts.fragment else ""
    if path in url_of:
        return url_of[path] + fragment
    if image:
        return f"{RAW}/{path}"
    kind = "tree" if (repo / path).is_dir() else "blob"
    return f"{SOURCE}/{kind}/main/{path}{fragment}"


def missing(site: Path, plan: list[tuple[str, Path]]) -> list[str]:
    """List what the site must serve and does not: schemas, the spec, the landing page.

    Args:
        site: The site root.
        plan: The layout, as :func:`layout` returns it.

    Returns:
        The paths, relative to the site root, of every required file that is absent.
    """
    required = [target for _, target in plan if target.suffix == ".json"]
    required += [Path(SPEC_URI.removeprefix(f"{BASE_URL}/")) / "index.html", Path("index.html")]
    return [path.as_posix() for path in required if not (site / path).is_file()]


def _markdown() -> Any:  # noqa: ANN401 - markdown-it-py is not a dependency, so untyped here
    """Return the Markdown renderer, with anchors as GitHub makes them.

    Returns:
        A ``markdown_it.MarkdownIt``.

    Raises:
        RenderError: If markdown-it-py or mdit-py-plugins is not installed.
    """
    try:
        markdown_it = importlib.import_module("markdown_it")
        anchors = importlib.import_module("mdit_py_plugins.anchors")
        front_matter = importlib.import_module("mdit_py_plugins.front_matter")
    except ImportError as error:
        msg = (
            f"{error.name} is missing; run this through `make site`, which provides "
            "markdown-it-py and mdit-py-plugins"
        )
        raise RenderError(msg) from error
    return (
        markdown_it.MarkdownIt("commonmark")
        .enable(["table", "strikethrough"])
        .use(front_matter.front_matter_plugin)
        .use(anchors.anchors_plugin, max_level=6, permalink=True, permalinkSymbol="¶")
    )


def page(title: str | None, canonical: str, body: str) -> str:
    """Wrap a rendered body in the site's page.

    Args:
        title: The page title, or None for the project's name.
        canonical: The page's canonical URL.
        body: The rendered Markdown.

    Returns:
        The HTML document.
    """
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title or 'Plannotation')}</title>\n"
        f'<link rel="canonical" href="{html.escape(canonical)}">\n'
        f"<style>\n{STYLE_SHEET.read_text('utf-8')}</style>\n</head>\n<body>\n<main>\n{body}</main>\n</body>\n</html>\n"
    )


def render(site: Path, examples: Path | None = None) -> list[Path]:
    """Render every staged Markdown page, then check that every promised URL resolves.

    Args:
        site: The staged site root.
        examples: The examples directory the site was staged with, or None.

    Returns:
        The HTML files written.

    Raises:
        RenderError: If the renderer is missing, or a schema, the spec or the landing
            page is not staged.
    """
    md = _markdown()
    plan = layout(examples)
    url_of = {source: served(target) for source, target in plan}
    written: list[Path] = []
    for source, target in plan:
        if target.suffix != ".md":
            continue
        tokens = md.parse((site / target).read_text("utf-8"))
        title = None
        for i, token in enumerate(tokens):
            if token.type == "front_matter":
                found = re.search(r"^title:\s*[\"']?(.+?)[\"']?\s*$", token.content, re.M)
                title = found.group(1) if found else None
            elif token.type == "heading_open" and token.tag == "h1" and title is None:
                title = tokens[i + 1].content
            for child in token.children or []:
                if child.type == "link_open":
                    child.attrSet(
                        "href", rewrite(str(child.attrGet("href")), source, url_of, image=False)
                    )
                elif child.type == "image":
                    child.attrSet(
                        "src", rewrite(str(child.attrGet("src")), source, url_of, image=True)
                    )
        body = md.renderer.render(tokens, md.options, {})
        out = site / url_of[source].lstrip("/")
        if url_of[source].endswith("/"):
            out /= "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(page(title, BASE_URL + url_of[source], body), "utf-8")
        written.append(out)
    absent = missing(site, plan)
    if absent:
        msg = f"not staged: {absent}"
        raise RenderError(msg)
    return written


def main(argv: list[str] | None = None) -> int:
    """Render the staged site.

    Args:
        argv: Command-line arguments, or None to read ``sys.argv``.

    Returns:
        0 when every page was rendered and every promised URL resolves, 1 otherwise.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "site", nargs="?", default="site", help="the staged site root (default: site)"
    )
    parser.add_argument(
        "--examples",
        type=Path,
        help="the examples directory the site was staged with (default: none)",
    )
    args = parser.parse_args(argv)
    site = Path(args.site)
    try:
        written = render(site, args.examples)
    except RenderError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for out in written:
        print(f"rendered {out.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
