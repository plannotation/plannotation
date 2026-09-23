# SPDX-License-Identifier: Apache-2.0
"""Stage the public Plannotation site: every schema at its ``$id``, the spec at its URI.

A published URL is a promise. The schemas' ``$id`` and the PDF Declaration's
``conformsTo`` are written into every plannotated document, so the site must serve
each file at exactly that address. The paths are therefore read from the schemas
and from :data:`plannotation.constants.SPEC_URI`, never spelled out a second time:

* ``plannotation/schema/*.json`` -> the path of its ``$id`` under the base URL;
* ``spec/SPEC.md`` -> ``<SPEC_URI path>/index.md``, which the site renders to HTML;
* ``README.md`` -> ``index.md``.

Usage:
    python tools/stage_site.py [OUT]     # default OUT: site/
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from plannotation.constants import BASE_URL, SPEC_URI

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = REPO_ROOT / "plannotation" / "schema"


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


def stage(out: Path) -> list[tuple[Path, Path]]:
    """Copy every public file to its place under ``out``.

    Args:
        out: The site root to write.

    Returns:
        Each source and the path it was staged at, relative to ``out``.
    """
    plan = [
        (schema, site_path(json.loads(schema.read_text("utf-8"))["$id"]))
        for schema in sorted(SCHEMA_DIR.glob("*.json"))
    ]
    plan += [
        (REPO_ROOT / "spec" / "SPEC.md", site_path(SPEC_URI) / "index.md"),
        (REPO_ROOT / "README.md", Path("index.md")),
    ]
    for source, target in plan:
        (out / target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, out / target)
    return plan


def main(argv: list[str] | None = None) -> int:
    """Stage the site.

    Args:
        argv: Command-line arguments, or None to read ``sys.argv``.

    Returns:
        0 when every file was staged.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", nargs="?", default="site", help="site root (default: site)")
    args = parser.parse_args(argv)
    out = Path(args.out)
    for source, target in stage(out):
        print(f"{source.relative_to(REPO_ROOT)} -> {(out / target).as_posix()}")
    print(f"staged in {out}/ -- publish with GitHub Pages at {BASE_URL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
