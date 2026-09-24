# SPDX-License-Identifier: Apache-2.0
"""Build the example drawings the site shows, from real, openly licensed IFC models.

Each model is pinned by URL, size and SHA-256 in :mod:`plannotation.export.examples`.
It is downloaded once into a cache that git ignores -- ``.cache/examples/``, or the
directory ``PLANNOTATION_EXAMPLES_CACHE`` or ``--cache`` names -- and refused if its
hash differs. The output is byte-identical from run to run.

Usage:
    python tools/build_examples.py [--out examples] [--cache DIR] [--only M18-101 ...]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from plannotation import __version__
from plannotation.export.examples import EXAMPLES, EXAMPLES_DATE, build_examples, cache_dir


def main(argv: list[str] | None = None) -> int:
    """Build the examples.

    Args:
        argv: Command-line arguments, or None to read ``sys.argv``.

    Returns:
        0 when every example was written.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="examples", help="output directory (default: examples)")
    parser.add_argument("--cache", type=Path, default=None, help="where the models are cached")
    parser.add_argument(
        "--only",
        nargs="+",
        choices=[example.name for example in EXAMPLES],
        help="build just these sheets",
    )
    args = parser.parse_args(argv)
    out = Path(args.out)
    written = build_examples(
        out,
        cache=cache_dir(args.cache),
        mod_date=EXAMPLES_DATE,
        version=__version__,
        only=args.only,
    )
    for pdf in written:
        print(f"built {pdf} and {pdf.with_suffix('.png')}")
    print(f"{len(written)} example sheet(s) and index.json in {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
