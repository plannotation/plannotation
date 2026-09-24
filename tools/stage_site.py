# SPDX-License-Identifier: Apache-2.0
"""Stage the public Plannotation site: every schema at its ``$id``, the spec at its URI.

A published URL is a promise. The schemas' ``$id`` and the PDF Declaration's
``conformsTo`` are written into every plannotated document, so the site must serve
each file at exactly that address. The paths are therefore read from the schemas
and from :data:`plannotation.constants.SPEC_URI`, never spelled out a second time:

* ``plannotation/schema/*.json`` -> the path of its ``$id`` under the base URL;
* ``spec/SPEC.md`` -> ``<SPEC_URI path>/index.md``, which the site renders to HTML;
* ``web/index.html`` -> ``index.html``, the landing page, filled in from the examples;
* ``inspector/index.html`` -> ``inspector/index.html``, served as it is;
* ``<examples>/index.json`` and each example's PDF and PNG -> ``examples/``.

The examples are what ``make examples`` writes. The landing page gets one card per
example, and the inspector's picker reads the same ``examples/index.json``. Without
them the landing page leaves the examples out and says the rest.

Every relative ``href`` and ``src`` in the staged HTML, and the ``pdf`` a link hands
the inspector, must reach a staged file, or nothing is staged as done.

Usage:
    python tools/stage_site.py [OUT] [--examples DIR]     # default OUT: site/
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import struct
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit

from plannotation.constants import BASE_URL, SPEC_URI

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = REPO_ROOT / "plannotation" / "schema"
LANDING = REPO_ROOT / "web" / "index.html"
INSPECTOR = REPO_ROOT / "inspector" / "index.html"

#: Where the examples are served. The inspector's EXAMPLES constant is ``../examples/``
#: from ``inspector/``, which is this directory.
EXAMPLES = Path("examples")

#: What each entry of ``examples/index.json`` must say, and as what type.
EXAMPLE_FIELDS: dict[str, type] = {
    "name": str,
    "pdf": str,
    "png": str,
    "sheet": str,
    "title": str,
    "paper": str,
    "scale": str,
    "elements": int,
    "annotations": int,
    "source": dict,
}
SOURCE_FIELDS = ("title", "url", "author", "license", "license_url")
#: Said when present: where the model was obtained, when that is not its author.
SOURCE_OPTIONAL = ("via",)
TYPE_NAMES = {str: "a string", int: "an integer", dict: "an object"}

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
#: The signature and the IHDR chunk up to the height: all a size needs.
PNG_HEAD = 24


class SiteError(ValueError):
    """The site cannot be staged as asked: a bad example, or a link to nothing."""


def site_path(url: str) -> Path:
    """Return where a public URL lives inside the staged site.

    Args:
        url: An absolute URL under :data:`BASE_URL`.

    Returns:
        The path relative to the site root.

    Raises:
        ValueError: If the URL is not under the base URL.
    """
    prefix = f"{BASE_URL}/"
    if not url.startswith(prefix):
        msg = f"{url} is not under {prefix}; the site cannot serve it"
        raise ValueError(msg)
    return Path(url.removeprefix(prefix))


def read_examples(directory: Path) -> list[dict[str, Any]]:
    """Read and check ``index.json`` in the directory ``make examples`` wrote.

    Args:
        directory: The examples directory.

    Returns:
        The entries, in the order the index lists them.

    Raises:
        SiteError: If the index is missing or malformed, or names a file that is not
            there beside it.
    """
    index = directory / "index.json"
    try:
        entries = json.loads(index.read_text("utf-8"))["examples"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        msg = f"{index}: not an examples index ({error})"
        raise SiteError(msg) from error
    if not isinstance(entries, list) or not entries:
        msg = f"{index}: 'examples' must be a non-empty list"
        raise SiteError(msg)
    for number, entry in enumerate(entries):
        _check_example(entry, f"{index}: examples[{number}]", directory)
    return entries


def _check_example(entry: object, where: str, directory: Path) -> None:
    """Check one entry of the examples index.

    Args:
        entry: The entry.
        where: Where it is, for the message.
        directory: The directory its files must be in.

    Raises:
        SiteError: If the entry is malformed or names a file that is not there.
    """
    if not isinstance(entry, dict):
        msg = f"{where} is not an object"
        raise SiteError(msg)
    for field, kind in EXAMPLE_FIELDS.items():
        value = entry.get(field)
        # bool is an int to Python, and "elements": true is not a count.
        if not isinstance(value, kind) or isinstance(value, bool):
            msg = f"{where}.{field} must be {TYPE_NAMES[kind]}"
            raise SiteError(msg)
    if entry["elements"] < 0 or entry["annotations"] < 0:
        msg = f"{where}: a count cannot be negative"
        raise SiteError(msg)
    for field, suffix in (("pdf", ".pdf"), ("png", ".png")):
        name = entry[field]
        # A bare file name, so an index cannot stage anything from outside its folder.
        if Path(name).name != name or not name.endswith(suffix) or name.startswith("."):
            msg = f"{where}.{field} must be a {suffix} file name, got {name!r}"
            raise SiteError(msg)
        if not (directory / name).is_file():
            msg = f"{where}.{field}: {directory / name} does not exist"
            raise SiteError(msg)
    source = entry["source"]
    for field in SOURCE_FIELDS + tuple(key for key in SOURCE_OPTIONAL if key in source):
        if not isinstance(source.get(field), str) or not source[field]:
            msg = f"{where}.source.{field} must be a non-empty string"
            raise SiteError(msg)
    for field in ("url", "license_url"):
        if urlsplit(source[field]).scheme not in {"https", "http"}:
            msg = f"{where}.source.{field} must be an http(s) URL"
            raise SiteError(msg)


def png_size(path: Path) -> tuple[int, int]:
    """Return a PNG's width and height, read from its header.

    Args:
        path: The PNG.

    Returns:
        Width and height in pixels.

    Raises:
        SiteError: If the file is not a PNG.
    """
    head = path.read_bytes()[:PNG_HEAD]
    if len(head) < PNG_HEAD or not head.startswith(PNG_SIGNATURE) or head[12:16] != b"IHDR":
        msg = f"{path} is not a PNG"
        raise SiteError(msg)
    width, height = struct.unpack(">II", head[16:24])
    return width, height


def _block(page: str, name: str) -> tuple[str, str, str]:
    """Split a page around ``<!-- name -->...<!-- /name -->``.

    Args:
        page: The page.
        name: The block's name.

    Returns:
        The text before the block, the block's content and the text after it.

    Raises:
        SiteError: If the page does not hold the block exactly once.
    """
    parts = re.split(rf"[ \t]*<!-- /?{name} -->\n?", page)
    if len(parts) != len(("before", "inside", "after")):
        msg = f"the landing page needs one <!-- {name} --> ... <!-- /{name} --> block"
        raise SiteError(msg)
    before, inside, after = parts
    return before, inside, after


def _fill(text: str, values: dict[str, str]) -> str:
    """Replace each ``{{key}}`` with its value, in one pass.

    One pass, so a value is never searched for placeholders itself: a sheet title
    that happens to contain ``{{og}}`` stays text.

    Args:
        text: The template.
        values: Each placeholder's replacement, already escaped for where it goes.

    Returns:
        The filled text.

    Raises:
        SiteError: If the template has a placeholder with no value.
    """
    unknown = set()

    def value(match: re.Match[str]) -> str:
        if match.group(1) not in values:
            unknown.add(match.group(0))
            return match.group(0)
        return values[match.group(1)]

    filled = re.sub(r"\{\{(\w+)\}\}", value, text)
    if unknown:
        msg = f"the landing page has placeholders with no value: {sorted(unknown)}"
        raise SiteError(msg)
    return filled


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _credit(source: dict[str, str]) -> str:
    """Credit one source as CC BY asks, and say who made the drawings.

    CC BY wants the title, the copyright notice, the licence, links to the source and
    the licence, and a note that the material was changed, and it forbids implying
    that the author endorses the result. The author made the model, not these sheets.

    Args:
        source: An entry's ``source``.

    Returns:
        The credit, as HTML.
    """
    esc = html.escape
    via = f", via {esc(source['via'])}" if source.get("via") else ""
    return (
        f'Drawn and annotated by Plannotation from <a href="{esc(source["url"])}">'
        f"{esc(source['title'])}</a> (changes made); model &copy; {esc(source['author'])}"
        f'{via}, <a href="{esc(source["license_url"])}">{esc(source["license"])}</a>. '
        f"Not drawn or endorsed by {esc(source['author'])}."
    )


def landing(template: str, examples: list[dict[str, Any]], sizes: list[tuple[int, int]]) -> str:
    """Fill in the landing page.

    Every value from the examples index is HTML-escaped where it goes in. Sheets from
    one source share one attribution under the cards; sheets from several sources each
    carry their own. With no examples, the examples block is left out.

    Args:
        template: ``web/index.html``.
        examples: The entries of ``examples/index.json``; empty for none.
        sizes: Each example's thumbnail width and height, in pixels.

    Returns:
        The page.

    Raises:
        SiteError: If the template is missing a block or a placeholder is left over.
    """
    esc = html.escape
    before, section, after = _block(template, "examples")
    head, card, tail = _block(section, "card")
    above, credit_line, below = _block(head + "{{cards}}" + tail, "credits")
    shared = len({json.dumps(entry["source"], sort_keys=True) for entry in examples}) == 1
    cards = "".join(
        _fill(
            card,
            {
                "pdf": esc(quote(entry["pdf"])),
                "png": esc(quote(entry["png"])),
                "width": str(width),
                "height": str(height),
                "sheet": esc(entry["sheet"]),
                "title": esc(entry["title"]),
                "paper": esc(entry["paper"]),
                "scale": esc(entry["scale"]),
                "counts": esc(
                    f"{_count(entry['elements'], 'element')}, "
                    f"{_count(entry['annotations'], 'annotation')}"
                ),
                "credit": "" if shared else f" {_credit(entry['source'])}",
            },
        )
        for entry, (width, height) in zip(examples, sizes, strict=True)
    )
    first = examples[0] if examples else None
    values = {
        "og": "",
        "fetch": "",
        "inspect": "drawing.plannotated.pdf",
        "spec": esc(f"{site_path(SPEC_URI).as_posix()}/"),
        "schemas": ",\n    ".join(
            f'<a href="{esc(target.as_posix())}">'
            f"{esc(target.name.removesuffix('.schema.json').removeprefix('plannotation-'))}</a>"
            for target in _schema_targets()
        ),
        "base": esc(BASE_URL),
        "cards": cards,
        "credit": _credit(first["source"]) if first and shared else "",
    }
    if first:
        served = f"{BASE_URL}/{EXAMPLES.as_posix()}"
        image = f"{served}/{quote(first['png'])}"
        values["og"] = (
            f'<meta property="og:image" content="{esc(image)}">\n'
            '<meta name="twitter:card" content="summary_large_image">\n'
        )
        values["fetch"] = esc(f"curl -O {served}/{quote(first['pdf'])}\n")
        values["inspect"] = esc(first["pdf"])
    section = above + (credit_line if shared else "") + below if first else ""
    return _fill(before + section + after, values)


def _schema_targets() -> list[Path]:
    return [
        site_path(json.loads(schema.read_text("utf-8"))["$id"])
        for schema in sorted(SCHEMA_DIR.glob("*.json"))
    ]


class _Links(HTMLParser):
    """Collect every ``href`` and ``src``, and the URLs an Open Graph tag names."""

    def __init__(self) -> None:
        super().__init__()
        self.found: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        self.found += [value for key, value in attrs if key in {"href", "src"} and value]
        if tag == "meta" and values.get("property") in {"og:image", "og:url"}:
            self.found += [values["content"] or ""]


#: A stand-in origin for resolving links. Anything under it, or under BASE_URL, is
#: the site's own and must be staged.
_ORIGIN = "https://staged.invalid/"


def _own_path(url: str) -> str | None:
    """Return the site path a URL names, or None when it points elsewhere."""
    for origin in (_ORIGIN, f"{BASE_URL}/"):
        if url.startswith(origin):
            return unquote(urlsplit(url).path).lstrip("/")
    return None


def broken_links(out: Path, staged: set[str]) -> list[str]:
    """List every link in the staged HTML that does not reach a staged file.

    A link to a directory needs an ``index.html`` there, or an ``index.md`` the site
    renders to one. A link that hands the inspector a ``pdf`` needs that PDF too.

    Args:
        out: The site root.
        staged: Every staged path, relative to ``out``, in POSIX form.

    Returns:
        ``page: link`` for each link that reaches nothing.
    """

    def served(path: str) -> bool:
        if path == "" or path.endswith("/"):
            return f"{path}index.html" in staged or f"{path}index.md" in staged
        return path in staged

    broken = []
    for page in sorted(path for path in staged if path.endswith(".html")):
        parser = _Links()
        parser.feed((out / page).read_text("utf-8"))
        for link in parser.found:
            url = urljoin(_ORIGIN + page, link)
            targets = [_own_path(url)]
            query = parse_qs(urlsplit(url).query)
            targets += [_own_path(urljoin(url, pdf)) for pdf in query.get("pdf", [])]
            if any(path is not None and not served(path) for path in targets):
                broken.append(f"{page}: {link}")
    return broken


def stage(out: Path, examples: Path | None = None) -> list[tuple[Path, Path]]:
    """Put every public file in its place under ``out``.

    Args:
        out: The site root to write.
        examples: The directory ``make examples`` wrote, or None to leave them out.

    Returns:
        Each source and the path it was staged at, relative to ``out``.

    Raises:
        SiteError: If an example is malformed, or a staged page links to a file that
            was not staged.
    """
    entries = read_examples(examples) if examples is not None else []
    plan = [
        (schema, site_path(json.loads(schema.read_text("utf-8"))["$id"]))
        for schema in sorted(SCHEMA_DIR.glob("*.json"))
    ]
    plan += [
        (REPO_ROOT / "spec" / "SPEC.md", site_path(SPEC_URI) / "index.md"),
        (INSPECTOR, Path("inspector") / "index.html"),
    ]
    if examples is not None:
        plan.append((examples / "index.json", EXAMPLES / "index.json"))
        for entry in entries:
            plan += [(examples / entry[kind], EXAMPLES / entry[kind]) for kind in ("pdf", "png")]
    for source, target in plan:
        (out / target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, out / target)
    sizes = [png_size(examples / entry["png"]) for entry in entries] if examples else []
    page = landing(LANDING.read_text("utf-8"), entries, sizes)
    (out / "index.html").write_text(page, "utf-8")
    plan.append((LANDING, Path("index.html")))
    broken = broken_links(out, {target.as_posix() for _, target in plan})
    if broken:
        msg = "links to nothing staged:\n  " + "\n  ".join(broken)
        raise SiteError(msg)
    return plan


def main(argv: list[str] | None = None) -> int:
    """Stage the site.

    Args:
        argv: Command-line arguments, or None to read ``sys.argv``.

    Returns:
        0 when every file was staged, 1 when the site could not be.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", nargs="?", default="site", help="site root (default: site)")
    parser.add_argument(
        "--examples", type=Path, help="the directory `make examples` wrote (default: none)"
    )
    args = parser.parse_args(argv)
    out = Path(args.out)
    try:
        plan = stage(out, args.examples)
    except SiteError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for source, target in plan:
        shown = source.relative_to(REPO_ROOT) if source.is_relative_to(REPO_ROOT) else source
        print(f"{shown.as_posix()} -> {(out / target).as_posix()}")
    print(f"staged in {out}/ -- publish with GitHub Pages at {BASE_URL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
