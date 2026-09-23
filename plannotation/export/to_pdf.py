# SPDX-License-Identifier: Apache-2.0
"""Convert a composed sheet from SVG to PDF.

CairoSVG does the conversion. It is LGPL, used unmodified and behind the ``svg`` extra,
and it binds to a system libcairo that no wheel can ship -- so it is imported inside the
function, and a machine without the library gets a message naming what to install rather
than an ``OSError`` about a missing ``cairo-2``.

Two things matter for a drawing and neither is CairoSVG's default:

**The page must be the size it says.** The sheet's SVG declares ``width="420mm"``, and
CairoSVG honours that only when told the output resolution that makes a millimetre a
millimetre. An A3 sheet that comes out 396 mm wide is not an A3 sheet, and every paper
coordinate in the plannotation would then describe a page that does not exist. The size
is asserted after conversion rather than assumed.

**The output must be reproducible.** The design brief requires it, and it is what makes a
plannotated drawing diffable. CairoSVG stamps a creation date into the PDF; the date is
replaced with a supplied one, so two runs over the same inputs produce the same bytes.

Where CairoSVG cannot run and the caller asks for it, the Inkscape command line does the
conversion instead. Inkscape is GPL and is only ever run as a separate program, never
imported, which is the one way the licence policy admits it; it is looked up on the
``PATH`` and nothing is installed. Its output goes through the same size check.
"""

from __future__ import annotations

import importlib
import io
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pikepdf

from plannotation.errors import ExportError, MissingExtraError
from plannotation.units import MM_PER_PT

if TYPE_CHECKING:
    from datetime import datetime

#: Points per inch. CairoSVG scales by dpi/96, taking 96 CSS pixels to the inch, so a
#: dpi of 72 makes one SVG user unit one PDF point and a millimetre a millimetre.
DPI_FOR_MM = 72.0

#: How far a converted page may differ from the size the SVG declared, in millimetres.
#: A tenth of a millimetre is far below anything a drawing cares about and far above
#: the rounding a conversion introduces.
SIZE_TOLERANCE_MM = 0.1

#: How long an Inkscape conversion may take before it is given up, in seconds.
INKSCAPE_TIMEOUT_S = 120

#: Where a Homebrew libcairo usually sits. ``ctypes.util.find_library`` does not search
#: Homebrew's prefix, so cffi cannot find the library even when it is installed.
_HOMEBREW_LIB_PATHS = ("/opt/homebrew/lib", "/usr/local/lib")


def _cairosvg() -> Any:  # noqa: ANN401 - cairosvg ships no types
    """Import CairoSVG, or say what is missing.

    Returns:
        The module.

    Raises:
        MissingExtraError: If the package or the system library is absent.
    """
    if os.uname().sysname == "Darwin" and "DYLD_FALLBACK_LIBRARY_PATH" not in os.environ:
        existing = [path for path in _HOMEBREW_LIB_PATHS if os.path.isdir(path)]  # noqa: PTH112
        if existing:
            os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(existing)
    try:
        return importlib.import_module("cairosvg")
    except ImportError as exc:
        msg = (
            "the authored exporter needs cairosvg, which is not installed. It lives "
            "behind an optional extra: install it with `pip install 'plannotation[svg]'` "
            "or `uv sync --extra svg`"
        )
        raise MissingExtraError(msg) from exc
    except OSError as exc:
        msg = (
            "cairosvg is installed but cannot find the system libcairo it binds to. "
            "Install it with `brew install cairo` on macOS or "
            "`apt-get install libcairo2` on Debian, then run the command again"
        )
        raise MissingExtraError(msg) from exc


def svg_to_pdf(
    svg: str,
    out: Path,
    *,
    width_mm: float,
    height_mm: float,
    mod_date: datetime,
    inkscape_fallback: bool = False,
) -> Path:
    """Convert a composed sheet to a PDF of the size it declares.

    Args:
        svg: The sheet's SVG.
        out: Where to write the PDF.
        width_mm: The page width the sheet claims, which is asserted afterwards.
        height_mm: The page height it claims.
        mod_date: The timestamp to stamp, so that the output is reproducible.
        inkscape_fallback: Convert with the Inkscape command line when CairoSVG cannot
            run, rather than failing.

    Returns:
        The path written.

    Raises:
        ExportError: If the converted page is not the size the sheet declared.
        MissingExtraError: If cairosvg or libcairo is unavailable, and Inkscape is
            either not allowed or not found.
    """
    try:
        raw = _cairosvg().svg2pdf(bytestring=svg.encode("utf-8"), dpi=DPI_FOR_MM)
    except MissingExtraError:
        if not inkscape_fallback:
            raise
        raw = inkscape_pdf(svg)

    with pikepdf.open(io.BytesIO(raw)) as pdf:
        page = pdf.pages[0]
        box = [float(value) for value in page.MediaBox]
        actual = ((box[2] - box[0]) * MM_PER_PT, (box[3] - box[1]) * MM_PER_PT)
        if (
            abs(actual[0] - width_mm) > SIZE_TOLERANCE_MM
            or abs(actual[1] - height_mm) > SIZE_TOLERANCE_MM
        ):
            msg = (
                f"the converted page is {actual[0]:.2f} x {actual[1]:.2f} mm, but the "
                f"sheet declares {width_mm:.2f} x {height_mm:.2f} mm; every paper "
                f"coordinate in the plannotation would describe a page that does not exist"
            )
            raise ExportError(msg)
        stamp = pikepdf.String(mod_date.strftime("D:%Y%m%d%H%M%SZ"))
        with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            meta["xmp:CreateDate"] = mod_date.isoformat()
            meta["xmp:ModifyDate"] = mod_date.isoformat()
        pdf.docinfo["/CreationDate"] = stamp
        pdf.docinfo["/ModDate"] = stamp
        pdf.save(out, deterministic_id=True, compress_streams=False, normalize_content=False)
    return out


def inkscape_pdf(svg: str) -> bytes:
    """Convert an SVG to PDF with the Inkscape command line.

    Args:
        svg: The SVG.

    Returns:
        The PDF's bytes.

    Raises:
        MissingExtraError: If ``inkscape`` is not on the ``PATH``.
        ExportError: If Inkscape fails or writes nothing.
    """
    command = shutil.which("inkscape")
    if command is None:
        msg = (
            "neither cairosvg nor the inkscape command is available; install one: "
            "`pip install 'plannotation[svg]'` with a system libcairo, or Inkscape 1.x"
        )
        raise MissingExtraError(msg)
    with tempfile.TemporaryDirectory(prefix="plannotation-inkscape-") as scratch:
        source = Path(scratch) / "sheet.svg"
        target = Path(scratch) / "sheet.pdf"
        source.write_text(svg, encoding="utf-8")
        completed = subprocess.run(  # noqa: S603 - a fixed argument list, no shell
            [command, str(source), "--export-type=pdf", f"--export-filename={target}"],
            capture_output=True,
            text=True,
            timeout=INKSCAPE_TIMEOUT_S,
            check=False,
        )
        if completed.returncode != 0 or not target.is_file():
            detail = (completed.stderr or completed.stdout).strip()[:500]
            msg = f"inkscape could not convert the sheet (exit {completed.returncode}): {detail}"
            raise ExportError(msg)
        return target.read_bytes()
