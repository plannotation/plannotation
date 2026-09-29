# SPDX-License-Identifier: Apache-2.0
"""Build the example drawings the site shows, from real, openly licensed IFC models.

Each model is pinned by URL, size and SHA-256 in :mod:`plannotation.export.examples`.
It is downloaded once into a cache that git ignores -- ``.cache/examples/``, or the
directory ``PLANNOTATION_EXAMPLES_CACHE`` or ``--cache`` names -- and refused if its
hash differs. The output is byte-identical from run to run.

With ``--check``, each sheet is then validated against the model it was drawn from,
and any finding, error or warning, fails the build.

Usage:
    python tools/build_examples.py [--out examples] [--cache DIR] [--only M18-101 ...]
                                   [--check]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from plannotation import __version__
from plannotation.export.examples import (
    EXAMPLES,
    EXAMPLES_DATE,
    build_examples,
    cache_dir,
    check_examples,
)


def main(argv: list[str] | None = None) -> int:
    """Build the examples.

    Args:
        argv: Command-line arguments, or None to read ``sys.argv``.

    Returns:
        0 when every example was written and, with ``--check``, every one validates
        against its model with no finding; 1 otherwise.
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
    parser.add_argument(
        "--check",
        action="store_true",
        help="then validate each sheet against its own model; any finding fails",
    )
    args = parser.parse_args(argv)
    out = Path(args.out)
    cache = cache_dir(args.cache)
    written = build_examples(
        out,
        cache=cache,
        mod_date=EXAMPLES_DATE,
        version=__version__,
        only=args.only,
    )
    for pdf in written:
        print(f"built {pdf} and {pdf.with_suffix('.png')}")
    print(f"{len(written)} example sheet(s) and index.json in {out}/")
    if not args.check:
        return 0
    found = 0
    for name, report in check_examples(out, cache=cache, only=args.only).items():
        for finding in report.findings:
            where = f"{finding.code} at {finding.path or '/'}"
            print(f"{name}: {finding.severity.value} {where}: {finding.message}")
        levels = ", ".join(str(page.level or "none") for page in report.pages)
        print(f"{name}: {len(report.findings)} finding(s) against its model; level {levels}")
        found += len(report.findings)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
