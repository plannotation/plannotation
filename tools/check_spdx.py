# SPDX-License-Identifier: Apache-2.0
"""Check that every source file carries the SPDX Apache-2.0 header.

Ruff's CPY001 enforces this for Python. This script extends the same rule to the
files ruff does not lint -- Makefile, YAML, TOML -- so the licence policy holds
across the whole tree rather than only the parts ruff happens to see.

Usage:
    python tools/check_spdx.py [PATH ...]

Exits 0 when every given file carries the header, 1 otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path

EXPECTED = "SPDX-License-Identifier: Apache-2.0"

#: Only the first few lines are inspected: a header buried in the middle of a
#: file is not a header. YAML documents may open with `---`, and shell-style
#: files with a shebang, so allow a little room.
HEAD_LINES = 5


def has_header(path: Path) -> bool:
    """Return whether ``path`` carries the SPDX header near its top.

    Args:
        path: File to inspect.

    Returns:
        True if the header is present in the first few lines, or the file is
        empty. Empty files are exempt, matching ruff's ``min-file-size = 1``.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return True  # unreadable or binary: not this check's business
    if not text.strip():
        return True
    return any(EXPECTED in line for line in text.splitlines()[:HEAD_LINES])


def main(argv: list[str]) -> int:
    """Check each path given on the command line.

    Args:
        argv: Paths to check.

    Returns:
        Process exit code: 0 when every file passes, 1 otherwise.
    """
    missing = [p for p in (Path(a) for a in argv) if p.is_file() and not has_header(p)]
    for path in missing:
        print(f"{path}: missing '# {EXPECTED}'")
    if missing:
        print(f"\n{len(missing)} file(s) missing the SPDX Apache-2.0 header.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
