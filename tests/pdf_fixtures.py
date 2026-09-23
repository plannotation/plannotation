# SPDX-License-Identifier: Apache-2.0
"""Deterministic PDF and plannotation fixtures, built with pikepdf alone.

Nothing here is committed as a binary and nothing here reads the clock, the locale or
the network. Every builder returns bytes, and building the same fixture twice -- in
one process or in two -- produces the same bytes, which is what lets
``tests/test_pdf.py`` assert that :func:`plannotation.pdf.embed.attach` is reproducible
without ever committing a golden hash.

Why pikepdf and not cairosvg
----------------------------
``cairosvg`` is a cffi binding to a system libcairo that no wheel ships, so a fixture
built with it fails to import on any machine without ``libcairo2``. CI has none. A
few hundred lines of PDF operators emitted by hand cost nothing and run everywhere.

What the main fixture carries, and why every part of it is needed
-----------------------------------------------------------------
:func:`build_drawing_set` is a two-page drawing set. Each feature below exists to
catch one specific way a carrier can go wrong; they are not decoration.

* **Two different page sizes, one of them rotated.** Page 0 is A3 landscape with
  ``/Rotate 0``; page 1 is A4 portrait with ``/Rotate 90``. A reader that measures a
  page as it is displayed rather than as it is stored reports page 1 as 297 x 210 mm,
  and specification section 3.7 says 210 x 297 mm. The two pages share a height, so
  page 0 still catches a writer that swaps width and height everywhere.
* **Text and vector content.** Base-14 Helvetica and Times-Roman (pdfium carries its
  own faces, so no system font is involved), strokes at five line widths, fills,
  hatching, Bezier curves, a dashed grid line and a rotated text matrix. Without
  content, a rendering difference would have nothing to show up in.
* **A pre-existing XMP packet**, written as literal bytes rather than through
  ``pikepdf.open_metadata()``, which stamps a metadata date and a producer line. It
  carries ``pdf:PDFVersion`` on purpose: pikepdf's ``save`` rewrites that property
  unless ``fix_metadata_version=False``, so its survival is a real test.
* **A pre-existing foreign PDF Declaration** in the same packet, naming another
  specification. Removing it, or re-serialising the packet around it, would be
  exactly the sin :func:`plannotation.pdf.embed.strip` promises not to commit.
* **A foreign document-level attachment** in the name tree *and* in the catalog's
  ``/AF``, and **a foreign page-level attachment** already on page 0's ``/AF``. The
  second is the only thing that catches a writer which assigns ``page.AF`` instead of
  appending to it -- the mistake a Factur-X file would expose in production.
* **Two annotations on page 0**: an invisible ``/Link`` and a ``/Square`` with a real
  appearance stream. The first catches a structural drop, the second catches it in
  pixels as well.
* **A document information dictionary** with fixed dates.
"""

from __future__ import annotations

import hashlib
import io
import zlib
from typing import TYPE_CHECKING, Final

import pikepdf
from pikepdf import Array, Dictionary, Name, Object, Pdf, String

from plannotation.constants import plannotation_filename
from plannotation.model import (
    Annotation,
    Element,
    Generator,
    Model,
    Page,
    Plane,
    Plannotation,
    Project,
    Provenance,
    Sheet,
    Shows,
    Viewport,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from plannotation.model import Rotation

__all__ = [
    "A3_HEIGHT_MM",
    "A3_WIDTH_MM",
    "A4_HEIGHT_MM",
    "A4_WIDTH_MM",
    "BOMB_MEGABYTES",
    "CDATA_DECLARATIONS_PACKET",
    "COMMENTED_DECLARATIONS_PACKET",
    "FIXED_PDF_DATE",
    "FOREIGN_DOC_FILENAME",
    "FOREIGN_PAGE_FILENAME",
    "FOREIGN_SPEC_URI",
    "NAMED_SUBJECT",
    "NAMED_SUBJECT_PACKET",
    "SELF_CLOSING_DECLARATIONS_PACKET",
    "TRAILING_COMMENT_PACKET",
    "XMP_PACKET",
    "build_aliased_attachment",
    "build_bare",
    "build_damaged_metadata",
    "build_drawing_set",
    "build_encrypted",
    "build_filtered_plannotation",
    "build_geometry_set",
    "build_metadata_bomb",
    "build_pdfa",
    "build_plannotation_bomb",
    "build_signed",
    "build_unspliceable_metadata",
    "build_with_xmp",
    "drawing_set_plannotations",
    "flate_bomb_bytes",
    "plannotation",
    "png_encode",
    "raw_pdf",
    "run_length_encode",
    "signature_digest",
]

#: Millimetres to PDF points.
_MM: Final = 72.0 / 25.4

#: The main fixture's page sizes, in millimetres, unrotated.
A3_WIDTH_MM: Final = 420.0
A3_HEIGHT_MM: Final = 297.0
A4_WIDTH_MM: Final = 210.0
A4_HEIGHT_MM: Final = 297.0

_A3_W: Final = A3_WIDTH_MM * _MM
_A3_H: Final = A3_HEIGHT_MM * _MM
_A4_W: Final = A4_WIDTH_MM * _MM
_A4_H: Final = A4_HEIGHT_MM * _MM

#: Every date in every fixture. Nothing here may depend on when the tests are run.
FIXED_PDF_DATE: Final = "D:20240101000000Z"
_FIXED_XMP_DATE: Final = "2024-01-01T00:00:00Z"

#: The specification some *other* producer declares conformance to in the fixture's
#: XMP. It must survive attach and strip untouched.
FOREIGN_SPEC_URI: Final = "https://example.invalid/some-other-spec/2.0"

#: The two attachments the fixture already carries before Plannotation sees it.
FOREIGN_DOC_FILENAME: Final = "site-notes.txt"
FOREIGN_PAGE_FILENAME: Final = "page0-source.txt"
_FOREIGN_DOC_BYTES: Final = b"Third-party site notes. Must survive plannotating.\n"
_FOREIGN_PAGE_BYTES: Final = b"Third-party page source for page 0. Must survive plannotating.\n"

#: Number of digits a content-stream number is written with.
_DECIMALS: Final = 4

#: The four numbers of a ``/ByteRange`` array.
_BYTE_RANGE_LENGTH: Final = 4


def _n(value: float) -> str:
    """Format a number the way a content stream wants it.

    Args:
        value: The number to write.

    Returns:
        A fixed-point spelling with no exponent and no trailing zeros, which is what
        keeps the emitted operators byte-stable across platforms.
    """
    return f"{value:.{_DECIMALS}f}".rstrip("0").rstrip(".") or "0"


class Ops:
    """A tiny PDF content-stream operator emitter.

    Only the operators the fixtures need, written out rather than pulled from a
    drawing library, so that the bytes of a page are entirely determined by this
    file.
    """

    def __init__(self) -> None:
        """Start an empty content stream."""
        self._out: list[str] = []

    def raw(self, text: str) -> Ops:
        """Append a line of operators verbatim.

        Args:
            text: The operators.

        Returns:
            This emitter, for chaining.
        """
        self._out.append(text)
        return self

    def graphics_state(self, width: float, gray: float = 0.0) -> Ops:
        """Set the line width and the stroking and non-stroking grey level.

        Args:
            width: Line width in points.
            gray: Grey level, 0 black to 1 white.

        Returns:
            This emitter, for chaining.
        """
        return self.raw(f"{_n(width)} w {_n(gray)} G {_n(gray)} g")

    def dash(self, pattern: str = "[] 0") -> Ops:
        """Set the dash pattern.

        Args:
            pattern: The pattern and phase, as PDF writes them.

        Returns:
            This emitter, for chaining.
        """
        return self.raw(f"{pattern} d")

    def line(self, x0: float, y0: float, x1: float, y1: float) -> Ops:
        """Stroke a straight line.

        Args:
            x0: Start x, in points.
            y0: Start y, in points.
            x1: End x, in points.
            y1: End y, in points.

        Returns:
            This emitter, for chaining.
        """
        return self.raw(f"{_n(x0)} {_n(y0)} m {_n(x1)} {_n(y1)} l S")

    def rect(self, x: float, y: float, width: float, height: float, operator: str = "S") -> Ops:
        """Draw a rectangle.

        Args:
            x: Lower-left x, in points.
            y: Lower-left y, in points.
            width: Width in points.
            height: Height in points.
            operator: ``S`` to stroke, ``f`` to fill.

        Returns:
            This emitter, for chaining.
        """
        return self.raw(f"{_n(x)} {_n(y)} {_n(width)} {_n(height)} re {operator}")

    def polygon(self, points: Sequence[tuple[float, float]]) -> Ops:
        """Fill a closed polygon.

        Args:
            points: The vertices, in points.

        Returns:
            This emitter, for chaining.
        """
        x0, y0 = points[0]
        body = " ".join(f"{_n(x)} {_n(y)} l" for x, y in points[1:])
        return self.raw(f"{_n(x0)} {_n(y0)} m {body} h f")

    def circle(self, cx: float, cy: float, radius: float) -> Ops:
        """Stroke a circle as four Bezier segments.

        Args:
            cx: Centre x, in points.
            cy: Centre y, in points.
            radius: Radius in points.

        Returns:
            This emitter, for chaining.
        """
        k = 0.5522847498 * radius
        return self.raw(
            f"{_n(cx + radius)} {_n(cy)} m "
            f"{_n(cx + radius)} {_n(cy + k)} {_n(cx + k)} {_n(cy + radius)} "
            f"{_n(cx)} {_n(cy + radius)} c "
            f"{_n(cx - k)} {_n(cy + radius)} {_n(cx - radius)} {_n(cy + k)} "
            f"{_n(cx - radius)} {_n(cy)} c "
            f"{_n(cx - radius)} {_n(cy - k)} {_n(cx - k)} {_n(cy - radius)} "
            f"{_n(cx)} {_n(cy - radius)} c "
            f"{_n(cx + k)} {_n(cy - radius)} {_n(cx + radius)} {_n(cy - k)} "
            f"{_n(cx + radius)} {_n(cy)} c S"
        )

    def text(self, font: str, size: float, x: float, y: float, body: str) -> Ops:
        """Show a run of text.

        Args:
            font: Resource name of the font, without the slash.
            size: Font size in points.
            x: Baseline origin x, in points.
            y: Baseline origin y, in points.
            body: The text, which must be WinAnsi-representable.

        Returns:
            This emitter, for chaining.
        """
        return self.raw(
            f"BT /{font} {_n(size)} Tf 1 0 0 1 {_n(x)} {_n(y)} Tm ({_escape(body)}) Tj ET"
        )

    def text_turned(self, font: str, size: float, x: float, y: float, body: str) -> Ops:
        """Show a run of text turned a quarter turn anticlockwise.

        Args:
            font: Resource name of the font, without the slash.
            size: Font size in points.
            x: Baseline origin x, in points.
            y: Baseline origin y, in points.
            body: The text.

        Returns:
            This emitter, for chaining.
        """
        return self.raw(
            f"BT /{font} {_n(size)} Tf 0 1 -1 0 {_n(x)} {_n(y)} Tm ({_escape(body)}) Tj ET"
        )

    def to_bytes(self) -> bytes:
        """Return the accumulated content stream.

        Returns:
            The operators as ASCII bytes, one line each.
        """
        return ("\n".join(self._out) + "\n").encode("ascii")


def _escape(body: str) -> str:
    """Escape a string for a PDF literal string.

    Args:
        body: The text to escape.

    Returns:
        The text with backslashes and parentheses escaped.
    """
    return body.replace("\\", "\\\\").replace("(", r"\(").replace(")", r"\)")


def _title_block(ops: Ops, width: float, height: float, sheet: str, title: str, scale: str) -> None:
    """Draw a bottom-right title block of rules and text.

    Args:
        ops: The emitter to draw into.
        width: Page width in points.
        height: Page height in points.
        sheet: The sheet number to print.
        title: The sheet title to print.
        scale: The scale to print.
    """
    block_width, block_height = 90 * _MM, 45 * _MM
    x0, y0 = width - 10 * _MM - block_width, 10 * _MM
    ops.graphics_state(1.0).rect(x0, y0, block_width, block_height)
    ops.graphics_state(0.4)
    for fraction in (0.30, 0.55, 0.78):
        ops.line(x0, y0 + block_height * fraction, x0 + block_width, y0 + block_height * fraction)
    ops.line(x0 + block_width * 0.62, y0, x0 + block_width * 0.62, y0 + block_height * 0.30)
    ops.text("F1", 11, x0 + 3 * _MM, y0 + block_height - 7 * _MM, title)
    ops.text("F2", 7, x0 + 3 * _MM, y0 + block_height * 0.62, "Carrier conformance fixture")
    ops.text("F2", 7, x0 + 3 * _MM, y0 + block_height * 0.38, "Drawn by: fixture builder")
    ops.text("F1", 14, x0 + 3 * _MM, y0 + 4 * _MM, sheet)
    ops.text("F2", 8, x0 + block_width * 0.64, y0 + 4 * _MM, f"SCALE {scale}")
    _ = height


def _sheet_frame(ops: Ops, width: float, height: float) -> None:
    """Draw the sheet border, the margin frame and the edge ticks.

    Args:
        ops: The emitter to draw into.
        width: Page width in points.
        height: Page height in points.
    """
    ops.graphics_state(1.4).rect(5 * _MM, 5 * _MM, width - 10 * _MM, height - 10 * _MM)
    ops.graphics_state(0.3).rect(10 * _MM, 10 * _MM, width - 20 * _MM, height - 20 * _MM)
    x = 10 * _MM
    while x < width - 10 * _MM:
        ops.line(x, 5 * _MM, x, 10 * _MM)
        x += 50 * _MM


def _plan_content(width: float, height: float) -> bytes:
    """Draw page 0: a floor plan with grids, dimensions and a north arrow.

    Args:
        width: Page width in points.
        height: Page height in points.

    Returns:
        The content stream.
    """
    ops = Ops()
    _sheet_frame(ops, width, height)

    ops.graphics_state(1.2)
    ops.rect(40 * _MM, 80 * _MM, 200 * _MM, 150 * _MM)
    ops.graphics_state(0.6)
    ops.rect(40 * _MM, 80 * _MM, 90 * _MM, 75 * _MM)
    ops.rect(130 * _MM, 80 * _MM, 110 * _MM, 75 * _MM)
    ops.rect(40 * _MM, 155 * _MM, 200 * _MM, 75 * _MM)

    ops.raw("0.75 g")
    ops.rect(150 * _MM, 170 * _MM, 30 * _MM, 40 * _MM, operator="f")
    ops.raw("0 g")

    ops.graphics_state(0.25, gray=0.35)
    y = 85 * _MM
    while y < 150 * _MM:
        ops.line(45 * _MM, y, 125 * _MM, y)
        y += 6 * _MM
    ops.graphics_state(0.6)

    for position, letter in enumerate("ABCD"):
        cx = (40 + 60 * position) * _MM
        ops.graphics_state(0.5).circle(cx, 245 * _MM, 5 * _MM)
        ops.text("F1", 8, cx - 2.2 * _MM, 243 * _MM, letter)
        ops.dash("[3 2] 0").graphics_state(0.25, gray=0.4)
        ops.line(cx, 240 * _MM, cx, 75 * _MM)
        ops.dash().graphics_state(0.6)

    ops.graphics_state(0.4)
    ops.line(40 * _MM, 65 * _MM, 240 * _MM, 65 * _MM)
    for x in (40 * _MM, 240 * _MM):
        ops.line(x - 2 * _MM, 63 * _MM, x + 2 * _MM, 67 * _MM)
        ops.line(x, 62 * _MM, x, 78 * _MM)
    ops.text("F2", 8, 130 * _MM, 67 * _MM, "20000")

    ops.graphics_state(0.8)
    ops.raw(
        f"{_n(260 * _MM)} {_n(100 * _MM)} m "
        f"{_n(300 * _MM)} {_n(190 * _MM)} {_n(350 * _MM)} {_n(60 * _MM)} "
        f"{_n(395 * _MM)} {_n(150 * _MM)} c S"
    )

    ops.graphics_state(0.5).circle(300 * _MM, 240 * _MM, 10 * _MM)
    ops.polygon(
        [
            (300 * _MM, 250 * _MM),
            (295 * _MM, 232 * _MM),
            (300 * _MM, 236 * _MM),
            (305 * _MM, 232 * _MM),
        ]
    )
    ops.text("F1", 7, 298 * _MM, 252 * _MM, "N")
    ops.text_turned("F2", 8, 32 * _MM, 100 * _MM, "SECTION A-A")

    _title_block(ops, width, height, "A-101", "GROUND FLOOR PLAN", "1:100")
    return ops.to_bytes()


def _schedule_content(width: float, height: float) -> bytes:
    """Draw page 1: a ruled schedule table and a detail.

    Args:
        width: Page width in points.
        height: Page height in points.

    Returns:
        The content stream.
    """
    ops = Ops()
    _sheet_frame(ops, width, height)

    x0, y0 = 20 * _MM, 185 * _MM
    columns = (0.0, 30 * _MM, 95 * _MM, 130 * _MM, 170 * _MM)
    rows = [
        ("MARK", "DESCRIPTION", "WIDTH", "HEIGHT"),
        ("D01", "Timber door, single leaf", "900", "2100"),
        ("D02", "Steel door, fire rated", "1000", "2100"),
        ("W01", "Aluminium window", "1800", "1500"),
        ("W02", "Rooflight", "1200", "1200"),
    ]
    row_height = 9 * _MM
    ops.graphics_state(0.6)
    ops.rect(x0, y0 - row_height * len(rows), columns[-1], row_height * len(rows))
    for number in range(1, len(rows)):
        ops.graphics_state(0.3).line(
            x0, y0 - row_height * number, x0 + columns[-1], y0 - row_height * number
        )
    for offset in columns[1:-1]:
        ops.graphics_state(0.3).line(x0 + offset, y0 - row_height * len(rows), x0 + offset, y0)
    for number, row in enumerate(rows):
        font = "F1" if number == 0 else "F2"
        for offset, cell in zip(columns[:-1], row, strict=True):
            ops.text(font, 7.5, x0 + offset + 2 * _MM, y0 - row_height * number - 6 * _MM, cell)

    ops.graphics_state(1.0).rect(20 * _MM, 70 * _MM, 95 * _MM, 55 * _MM)
    ops.graphics_state(0.4).rect(28 * _MM, 78 * _MM, 79 * _MM, 39 * _MM)
    ops.line(20 * _MM, 70 * _MM, 115 * _MM, 125 * _MM)
    ops.raw("0.5 g")
    ops.rect(125 * _MM, 70 * _MM, 65 * _MM, 55 * _MM, operator="f")
    ops.raw("0 g")
    ops.text("F1", 9, 20 * _MM, 192 * _MM, "DOOR AND WINDOW SCHEDULE")
    ops.text("F2", 7, 20 * _MM, 132 * _MM, "Detail 1 - jamb, scale 1:5")

    _title_block(ops, width, height, "A-201", "SCHEDULES", "1:5")
    return ops.to_bytes()


#: The fixture's XMP packet, written as literal bytes.
#:
#: Two things in it are traps on purpose. ``pdf:PDFVersion`` is rewritten by pikepdf's
#: ``save`` unless ``fix_metadata_version=False``. The second ``rdf:Description`` is a
#: PDF Declaration made by another producer: Plannotation's own declaration is spliced in
#: beside it, and stripping must restore this packet byte for byte.
XMP_PACKET: Final = (
    '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
    ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    '  <rdf:Description rdf:about=""\n'
    '    xmlns:dc="http://purl.org/dc/elements/1.1/"\n'
    '    xmlns:xmp="http://ns.adobe.com/xap/1.0/"\n'
    '    xmlns:pdf="http://ns.adobe.com/pdf/1.3/">\n'
    "   <dc:title><rdf:Alt>"
    '<rdf:li xml:lang="x-default">Fixture drawing set</rdf:li>'
    "</rdf:Alt></dc:title>\n"
    "   <dc:creator><rdf:Seq><rdf:li>Fixture builder</rdf:li></rdf:Seq></dc:creator>\n"
    f"   <xmp:CreateDate>{_FIXED_XMP_DATE}</xmp:CreateDate>\n"
    f"   <xmp:ModifyDate>{_FIXED_XMP_DATE}</xmp:ModifyDate>\n"
    f"   <xmp:MetadataDate>{_FIXED_XMP_DATE}</xmp:MetadataDate>\n"
    "   <xmp:CreatorTool>Fixture builder</xmp:CreatorTool>\n"
    "   <pdf:Producer>Fixture builder</pdf:Producer>\n"
    "   <pdf:PDFVersion>2.0</pdf:PDFVersion>\n"
    "  </rdf:Description>\n"
    '  <rdf:Description xmlns:pdfd="http://pdfa.org/declarations/" rdf:about="">'
    "<pdfd:declarations><rdf:Bag>"
    '<rdf:li rdf:parseType="Resource">'
    f"<pdfd:conformsTo>{FOREIGN_SPEC_URI}</pdfd:conformsTo>"
    "</rdf:li>"
    "</rdf:Bag></pdfd:declarations></rdf:Description>\n"
    " </rdf:RDF>\n"
    "</x:xmpmeta>\n"
    '<?xpacket end="w"?>\n'
).encode()

#: A packet whose ``pdfd:declarations`` array is present, self-closing and therefore
#: empty. It is the one corner an implementation that joins an existing array is most
#: likely to miss: there is an array, so writing a second ``pdfd:declarations`` property
#: beside it is invalid XMP, and there is no ``</rdf:Bag>`` to splice a member before.
#: Adobe's own serialiser writes an empty array this way, so it is not a contrived shape.
SELF_CLOSING_DECLARATIONS_PACKET: Final = (
    '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
    ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    '  <rdf:Description xmlns:pdfd="http://pdfa.org/declarations/" rdf:about="">'
    "<pdfd:declarations><rdf:Bag /></pdfd:declarations></rdf:Description>\n"
    " </rdf:RDF>\n"
    "</x:xmpmeta>\n"
    '<?xpacket end="w"?>\n'
).encode()

#: A packet whose only ``pdfd:declarations`` property is commented out.
#:
#: Nothing inside an XML comment is markup, so this packet declares nothing and has no
#: array to join: a writer must add the whole property, outside the comment. A writer
#: that scans the bytes without masking comments finds the ``<pdfd:declarations>`` and
#: the ``</rdf:Bag>`` inside one, splices its claim between them, and reports that it
#: wrote a declaration that no XMP reader will ever see.
COMMENTED_DECLARATIONS_PACKET: Final = (
    '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
    ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    '  <rdf:Description rdf:about="">\n'
    '   <!-- <pdfd:declarations xmlns:pdfd="http://pdfa.org/declarations/">'
    "<rdf:Bag></rdf:Bag></pdfd:declarations> -->\n"
    "  </rdf:Description>\n"
    " </rdf:RDF>\n"
    "</x:xmpmeta>\n"
    '<?xpacket end="w"?>\n'
).encode()

#: The same shape with a CDATA section instead of a comment. CDATA is character data by
#: definition: the angle brackets inside it are text, not tags.
CDATA_DECLARATIONS_PACKET: Final = (
    '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
    ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    '  <rdf:Description rdf:about="">\n'
    '   <dc:description xmlns:dc="http://purl.org/dc/elements/1.1/"><![CDATA['
    '<pdfd:declarations xmlns:pdfd="http://pdfa.org/declarations/">'
    "<rdf:Bag></rdf:Bag></pdfd:declarations>]]></dc:description>\n"
    "  </rdf:Description>\n"
    " </rdf:RDF>\n"
    "</x:xmpmeta>\n"
    '<?xpacket end="w"?>\n'
).encode()

#: A packet whose *last* ``</rdf:RDF>`` is inside a trailing comment.
#:
#: A writer that splices its property before the last closing tag it can find writes the
#: whole declaration inside that comment, and outside the RDF element as well.
TRAILING_COMMENT_PACKET: Final = (
    '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
    ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    '  <rdf:Description rdf:about=""/>\n'
    " </rdf:RDF>\n"
    " <!-- the tool that wrote this left a copy of </rdf:RDF> behind -->\n"
    "</x:xmpmeta>\n"
    '<?xpacket end="w"?>\n'
).encode()

#: The subject every ``rdf:Description`` in :data:`NAMED_SUBJECT_PACKET` describes.
NAMED_SUBJECT: Final = "uuid:1f2e3d4c-5b6a-7980-9102-3a4b5c6d7e8f"

#: A packet whose ``rdf:Description`` elements carry a non-empty ``rdf:about``. XMP
#: Part 1 requires every ``rdf:Description`` in a packet to describe the same resource,
#: so a producer that always writes ``rdf:about=""`` adds a second subject to this
#: packet. It carries no ``pdfd:declarations``, which is what sends a writer down the
#: whole-property path where the subject is chosen.
NAMED_SUBJECT_PACKET: Final = (
    '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
    ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    f'  <rdf:Description rdf:about="{NAMED_SUBJECT}"\n'
    '    xmlns:pdf="http://ns.adobe.com/pdf/1.3/">\n'
    "   <pdf:Producer>Fixture builder</pdf:Producer>\n"
    "  </rdf:Description>\n"
    " </rdf:RDF>\n"
    "</x:xmpmeta>\n"
    '<?xpacket end="w"?>\n'
).encode()


def build_with_xmp(packet: bytes) -> bytes:
    """Build a one-page document carrying an XMP packet of the caller's choosing.

    Args:
        packet: The exact bytes of the catalog's ``/Metadata`` stream. They are stored
            uncompressed, so a test can read them back and compare byte for byte.

    Returns:
        The document's bytes.
    """
    with pikepdf.open(io.BytesIO(build_bare())) as pdf:
        metadata = pdf.make_stream(packet)
        metadata.stream_dict[Name.Type] = Name.Metadata
        metadata.stream_dict[Name.Subtype] = Name.XML
        pdf.Root[Name.Metadata] = pdf.make_indirect(metadata)
        return _save(pdf, object_streams=False, compress=False)


def _square_appearance(pdf: Pdf, width: float, height: float) -> Object:
    """Build the normal appearance stream of the fixture's ``/Square`` annotation.

    Args:
        pdf: The document to create the stream in.
        width: The annotation's width in points.
        height: The annotation's height in points.

    Returns:
        A form XObject drawing a red box, so that dropping the annotation shows up in
        pixels and not only in the object structure.
    """
    body = f"1 w 0.85 0.1 0.1 RG 0.5 0.5 {_n(width - 1)} {_n(height - 1)} re S\n".encode("ascii")
    return pdf.make_stream(
        body,
        Type=Name.XObject,
        Subtype=Name.Form,
        BBox=Array([0, 0, width, height]),
        Resources=Dictionary(),
    )


def _save(pdf: Pdf, *, object_streams: bool, compress: bool, version: str = "2.0") -> bytes:
    """Save a fixture document reproducibly.

    Args:
        pdf: The document to save.
        object_streams: Whether to pack objects into an ``/ObjStm`` and write a
            cross-reference stream, rather than a classic cross-reference table.
        compress: Whether to Flate-encode the streams.
        version: The version to write in the header.

    Returns:
        The document's bytes. ``deterministic_id`` is what makes them reproducible:
        the default ``/ID`` is seeded from the clock and differs between runs that are
        more than a second apart, which an in-process test would never notice.
        ``fix_metadata_version=False`` keeps the XMP packet as written even when it
        disagrees with the header, which is what the 1.7 variant depends on.
    """
    buffer = io.BytesIO()
    pdf.save(
        buffer,
        deterministic_id=True,
        compress_streams=compress,
        object_stream_mode=(
            pikepdf.ObjectStreamMode.generate
            if object_streams
            else pikepdf.ObjectStreamMode.disable
        ),
        normalize_content=False,
        linearize=False,
        force_version=version,
        fix_metadata_version=False,
    )
    return buffer.getvalue()


def _attach_foreign(
    pdf: Pdf,
    data: bytes,
    *,
    filename: str,
    description: str,
    relationship: Name,
) -> Object:
    """Embed a third party's file and register it in the name tree.

    ``/AFRelationship`` is set on the registered dictionary rather than passed to
    ``AttachedFileSpec``: pikepdf's typed signature omits the argument although its
    docstring documents it, and passing the value as a string raises an opaque
    ``RuntimeError: std::bad_cast``.

    Args:
        pdf: The document to embed into.
        data: The file's bytes.
        filename: The name-tree key, which is also ``/F`` and ``/UF``.
        description: The ``/Desc`` a viewer shows.
        relationship: The ``/AFRelationship`` to record.

    Returns:
        The file specification, as the indirect object an ``/AF`` array can share.
    """
    spec = pikepdf.AttachedFileSpec(
        pdf,
        data,
        description=description,
        filename=filename,
        mime_type="text/plain",
        creation_date=FIXED_PDF_DATE,
        mod_date=FIXED_PDF_DATE,
    )
    pdf.attachments[filename] = spec
    registered = pdf.attachments[filename].obj
    registered[Name.AFRelationship] = relationship
    return registered


def build_drawing_set(
    *,
    nudge: float = 0.0,
    object_streams: bool = True,
    compress: bool = True,
    version: str = "2.0",
) -> bytes:
    """Build the two-page drawing set the carrier tests run against.

    Args:
        nudge: Points by which one line on page 0 is displaced. Zero builds the
            fixture; any other value builds its twin, a document that genuinely looks
            different, which is what proves the appearance comparison can fail.
        object_streams: Whether to write a cross-reference stream and an ``/ObjStm``,
            rather than a classic cross-reference table. Both are exercised, because a
            carrier that only ever saw one would not be known to handle the other.
        compress: Whether to Flate-encode the streams.
        version: The PDF version to write in the header. ``"1.7"`` builds a document
            whose header and whose XMP ``pdf:PDFVersion`` disagree, which is the only
            shape in which pikepdf's ``fix_metadata_version`` trap can be observed:
            with the default save it rewrites the packet to agree with the header, and
            that packet belongs to another producer.

    Returns:
        The document's bytes.
    """
    pdf = Pdf.new()

    helvetica = Dictionary(
        Type=Name.Font,
        Subtype=Name.Type1,
        BaseFont=Name.Helvetica,
        Encoding=Name.WinAnsiEncoding,
    )
    times = Dictionary(
        Type=Name.Font,
        Subtype=Name.Type1,
        BaseFont=Name("/Times-Roman"),
        Encoding=Name.WinAnsiEncoding,
    )
    resources = pdf.make_indirect(
        Dictionary(Font=Dictionary(F1=pdf.make_indirect(helvetica), F2=pdf.make_indirect(times)))
    )

    plan = _plan_content(_A3_W, _A3_H)
    if nudge:
        # One number changes: the building outline's bottom edge moves up by `nudge`.
        plan = plan.replace(
            f"{_n(40 * _MM)} {_n(80 * _MM)} {_n(200 * _MM)} {_n(150 * _MM)} re".encode(),
            f"{_n(40 * _MM)} {_n(80 * _MM + nudge)} {_n(200 * _MM)} {_n(150 * _MM)} re".encode(),
            1,
        )
    page0 = pdf.add_blank_page(page_size=(_A3_W, _A3_H))
    page0.Contents = pdf.make_stream(plan)
    page0.Resources = resources
    page0.Rotate = 0

    page1 = pdf.add_blank_page(page_size=(_A4_W, _A4_H))
    page1.Contents = pdf.make_stream(_schedule_content(_A4_W, _A4_H))
    page1.Resources = resources
    page1.Rotate = 90

    link = pdf.make_indirect(
        Dictionary(
            Type=Name.Annot,
            Subtype=Name.Link,
            Rect=Array([300 * _MM, 20 * _MM, 380 * _MM, 28 * _MM]),
            Border=Array([0, 0, 0]),
            A=Dictionary(
                Type=Name.Action,
                S=Name.URI,
                URI=String("https://example.invalid/fixture-link"),
            ),
        )
    )
    square_width, square_height = 40 * _MM, 15 * _MM
    square = pdf.make_indirect(
        Dictionary(
            Type=Name.Annot,
            Subtype=Name.Square,
            Rect=Array([250 * _MM, 250 * _MM, 250 * _MM + square_width, 250 * _MM + square_height]),
            F=4,
            C=Array([0.85, 0.1, 0.1]),
            CA=1,
            AP=Dictionary(N=_square_appearance(pdf, square_width, square_height)),
        )
    )
    page0.Annots = Array([link, square])

    document_spec = _attach_foreign(
        pdf,
        _FOREIGN_DOC_BYTES,
        filename=FOREIGN_DOC_FILENAME,
        description="Third-party site notes: must survive plannotating",
        relationship=Name.Supplement,
    )
    page_spec = _attach_foreign(
        pdf,
        _FOREIGN_PAGE_BYTES,
        filename=FOREIGN_PAGE_FILENAME,
        description="Third-party page source: must survive plannotating",
        relationship=Name.Source,
    )
    page0.obj[Name.AF] = Array([page_spec])
    pdf.Root[Name.AF] = Array([document_spec])

    metadata = pdf.make_stream(XMP_PACKET)
    metadata.stream_dict[Name.Type] = Name.Metadata
    metadata.stream_dict[Name.Subtype] = Name.XML
    pdf.Root[Name.Metadata] = pdf.make_indirect(metadata)
    pdf.docinfo = pdf.make_indirect(
        Dictionary(
            Title=String("Fixture drawing set"),
            Author=String("Fixture builder"),
            Producer=String("Fixture builder"),
            Creator=String("Fixture builder"),
            CreationDate=String(FIXED_PDF_DATE),
            ModDate=String(FIXED_PDF_DATE),
        )
    )

    try:
        return _save(pdf, object_streams=object_streams, compress=compress, version=version)
    finally:
        pdf.close()


def build_bare() -> bytes:
    """Build a one-page document with no metadata, no attachments and no annotations.

    Returns:
        The document's bytes. It exists so that the tests can watch Plannotation create
        an XMP packet where there was none and then take it away again, which is a
        different code path from splicing into an existing packet.
    """
    pdf = Pdf.new()
    page = pdf.add_blank_page(page_size=(_A4_W, _A4_H))
    ops = Ops()
    ops.graphics_state(1.0).rect(20 * _MM, 20 * _MM, 170 * _MM, 257 * _MM)
    page.Contents = pdf.make_stream(ops.to_bytes())
    page.Rotate = 0
    try:
        return _save(pdf, object_streams=False, compress=False)
    finally:
        pdf.close()


def build_geometry_set() -> bytes:
    """Build a document whose pages exercise ``/CropBox`` and ``/UserUnit``.

    Returns:
        The document's bytes. Page 0 has a crop box strictly inside its media box and
        a ``/UserUnit`` of 2; page 1 has a crop box larger than its media box, which a
        viewer clips and so must a plannotation.
    """
    pdf = Pdf.new()
    ops = Ops()
    ops.graphics_state(1.0).rect(30 * _MM, 30 * _MM, 100 * _MM, 100 * _MM)
    body = ops.to_bytes()

    page0 = pdf.add_blank_page(page_size=(_A3_W, _A3_H))
    page0.Contents = pdf.make_stream(body)
    page0.obj[Name.CropBox] = Array([20, 30, 1120.5512, 791.8898])
    page0.obj[Name.UserUnit] = 2
    page0.Rotate = 0

    page1 = pdf.add_blank_page(page_size=(_A4_W, _A4_H))
    page1.Contents = pdf.make_stream(body)
    page1.obj[Name.CropBox] = Array([-50, -50, 700, 900])
    page1.Rotate = 270

    try:
        return _save(pdf, object_streams=False, compress=False)
    finally:
        pdf.close()


def build_encrypted() -> bytes:
    """Build an encrypted document.

    Returns:
        The document's bytes, encrypted with an empty user password so that it opens
        without one and the carrier's refusal is about encryption rather than about a
        password it does not have.
    """
    buffer = io.BytesIO()
    with pikepdf.open(io.BytesIO(build_bare())) as pdf:
        pdf.save(
            buffer,
            encryption=pikepdf.Encryption(owner="owner-secret", user=""),
            compress_streams=False,
            normalize_content=False,
            fix_metadata_version=False,
        )
    return buffer.getvalue()


def build_damaged_metadata() -> bytes:
    """Build a document whose ``/Metadata`` is a dictionary rather than a stream.

    ISO 32000-2 requires the catalog's ``/Metadata`` to be a stream. A document that
    holds a dictionary there is damaged, and a carrier that reaches straight for
    ``read_bytes()`` dies with whatever its PDF library raises -- which reaches a
    person as a traceback rather than as an error message.

    Returns:
        The document's bytes.
    """
    with pikepdf.open(io.BytesIO(build_bare())) as pdf:
        pdf.Root[Name.Metadata] = pdf.make_indirect(
            Dictionary(Type=Name.Metadata, Subtype=Name.XML)
        )
        return _save(pdf, object_streams=False, compress=False)


#: An XMP packet with no closing ``rdf:RDF`` element, so that nothing can be spliced
#: into it. It is well-formed enough for a PDF library to store and hand back.
_UNSPLICEABLE_PACKET: Final = (
    '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
    "  <!-- this packet carries no RDF at all -->\n"
    "</x:xmpmeta>\n"
    '<?xpacket end="w"?>\n'
).encode()


def build_unspliceable_metadata() -> bytes:
    """Build a document whose XMP packet cannot take a PDF Declaration.

    Returns:
        The document's bytes. It exists so that a test can watch the declaration fail
        and then check that nothing else was written first.
    """
    return build_with_xmp(_UNSPLICEABLE_PACKET)


#: The ``pdfaExtension`` block a PDF/A-1, -2 or -3 file needs before it may carry any
#: property from a namespace ISO 19005 does not itself define. This is a stand-in
#: shaped like the real thing, not the PDF Association's own schema: the point is only
#: that a detector can tell a file that has one from a file that has not.
_PDFA_EXTENSION_BLOCK: Final = (
    '  <rdf:Description rdf:about=""\n'
    '    xmlns:pdfaExtension="http://www.aiim.org/pdfa/ns/extension/"\n'
    '    xmlns:pdfaSchema="http://www.aiim.org/pdfa/ns/schema#">\n'
    "   <pdfaExtension:schemas><rdf:Bag>"
    '<rdf:li rdf:parseType="Resource">'
    "<pdfaSchema:namespaceURI>http://pdfa.org/declarations/</pdfaSchema:namespaceURI>"
    "<pdfaSchema:prefix>pdfd</pdfaSchema:prefix>"
    "</rdf:li>"
    "</rdf:Bag></pdfaExtension:schemas>\n"
    "  </rdf:Description>\n"
)


def build_pdfa(part: int, *, with_extension_schema: bool = False) -> bytes:
    """Build a one-page document claiming conformance to one part of PDF/A.

    Nothing here makes the file actually conform -- it has no output intent and no
    embedded fonts. What it carries is the ``pdfaid:part`` a reader must notice before
    it adds an XMP property from a namespace ISO 19005 does not define, which for
    parts 1, 2 and 3 requires an extension schema and for part 4 does not.

    Args:
        part: The PDF/A part to claim, 1 to 4.
        with_extension_schema: Whether to include a ``pdfaExtension`` block covering
            the PDF Declarations namespace.

    Returns:
        The document's bytes.
    """
    packet = (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about=""\n'
        '    xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/">\n'
        f"   <pdfaid:part>{part}</pdfaid:part>\n"
        "   <pdfaid:conformance>B</pdfaid:conformance>\n"
        "  </rdf:Description>\n"
        + (_PDFA_EXTENSION_BLOCK if with_extension_schema else "")
        + " </rdf:RDF>\n"
        "</x:xmpmeta>\n"
        '<?xpacket end="w"?>\n'
    ).encode()
    return build_with_xmp(packet)


#: How large :func:`build_plannotation_bomb` inflates to by default, in mebibytes. Comfortably
#: over any bound a reader should accept, and small enough that a test which runs
#: *without* the bound -- which is what a regression test must do once -- costs about a
#: tenth of a second and 64 MB, rather than the 2 GB the original report measured.
BOMB_MEGABYTES: Final = 64

#: One mebibyte of the byte a bomb is made of. Zeros: they compress to nothing and are
#: not valid JSON, so a reader that survives the decompression still refuses the
#: plannotation.
_BOMB_CHUNK: Final = bytes(1024 * 1024)

#: The name the bomb is filed under, which is what makes a reader open it at all.
_BOMB_FILENAME: Final = plannotation_filename(0)


def build_plannotation_bomb(*, megabytes: int = BOMB_MEGABYTES, honest_size: bool = True) -> bytes:
    """Build a small document carrying a Flate decompression bomb as a plannotation.

    The bomb is registered both in the ``EmbeddedFiles`` name tree and on page 0's
    ``/AF``, because a reader consults both and each path must be bounded.

    Args:
        megabytes: How many mebibytes the embedded stream decompresses to.
        honest_size: Whether ``/Params /Size`` states the decompressed length. A real
            attacker lies about it, which is why a reader may use it to refuse early
            but may never rely on it.

    Returns:
        The document's bytes -- a few tens of kilobytes of them.
    """
    compressor = zlib.compressobj(9)
    parts = [compressor.compress(_BOMB_CHUNK) for _ in range(megabytes)]
    parts.append(compressor.flush())
    raw = b"".join(parts)
    with pikepdf.open(io.BytesIO(build_bare())) as pdf:
        spec = pikepdf.AttachedFileSpec(
            pdf,
            b"{}",
            description="A plannotation that is not what it says it is",
            filename=_BOMB_FILENAME,
            mime_type="application/json",
            creation_date=FIXED_PDF_DATE,
            mod_date=FIXED_PDF_DATE,
        )
        pdf.attachments[_BOMB_FILENAME] = spec
        registered = pdf.attachments[_BOMB_FILENAME].obj
        registered[Name.AFRelationship] = Name.Data
        stream = registered[Name.EF][Name.F]
        stream.write(raw, filter=Name.FlateDecode)
        if honest_size:
            stream.stream_dict[Name.Params][Name.Size] = megabytes * len(_BOMB_CHUNK)
        pdf.pages[0].obj[Name.AF] = Array([registered])
        return _save(pdf, object_streams=False, compress=False)


def flate_bomb_bytes(megabytes: int = BOMB_MEGABYTES) -> bytes:
    """Flate-compress a payload of zeros without ever holding it whole.

    Args:
        megabytes: How many mebibytes of zeros the result decompresses to.

    Returns:
        The compressed bytes, a few tens of kilobytes of them.
    """
    compressor = zlib.compressobj(9)
    parts = [compressor.compress(_BOMB_CHUNK) for _ in range(megabytes)]
    parts.append(compressor.flush())
    return b"".join(parts)


#: The PNG filter type that predicts from three neighbours at once, and the highest
#: type there is.
_PNG_PAETH: Final = 4


def png_encode(data: bytes, *, columns: int, tag: int, step: int = 1) -> bytes:
    """Apply one PNG predictor filter to a payload, the way a writer would.

    The inverse of what a reader must do for ``/DecodeParms /Predictor`` 10 to 15.
    Only whole-byte components are covered, which is the only shape a JSON payload would
    ever be stored under and is enough to exercise all five filter types.

    Args:
        data: The payload. Its length must be a whole number of rows.
        columns: How many bytes a row holds once decoded.
        tag: The PNG filter type to apply to every row, 0 (None) to 4 (Paeth).
        step: The distance in bytes to the byte one pixel to the left, which is
            ``/Colors`` times ``/BitsPerComponent`` over eight. One for a single
            eight-bit component; larger when a reader has lanes to keep apart.

    Returns:
        The filtered rows, each preceded by its one-byte tag.

    Raises:
        ValueError: If the payload is not a whole number of rows, or the tag is not
            one of the five PNG filter types.
    """
    if len(data) % columns:
        msg = f"a payload of {len(data)} bytes is not a whole number of {columns}-byte rows"
        raise ValueError(msg)
    if not 0 <= tag <= _PNG_PAETH:
        msg = f"{tag} is not a PNG filter type"
        raise ValueError(msg)
    out = bytearray()
    previous = bytes(columns)
    for start in range(0, len(data), columns):
        row = data[start : start + columns]
        out.append(tag)
        for at, byte in enumerate(row):
            left = row[at - step] if at >= step else 0
            up = previous[at]
            upper_left = previous[at - step] if at >= step else 0
            out.append((byte - _png_predict(tag, left, up, upper_left)) & 0xFF)
        previous = row
    return bytes(out)


def _png_predict(tag: int, left: int, up: int, upper_left: int) -> int:
    """Return the value one PNG filter type predicts for a byte.

    Args:
        tag: The PNG filter type, 0 to 4.
        left: The byte one pixel to the left, or 0 at the start of a row.
        up: The byte in the same column of the previous row.
        upper_left: The byte one pixel to the left of ``up``.

    Returns:
        The prediction, which an encoder subtracts and a decoder adds back.
    """
    if tag == 1:
        return left
    if tag == 2:
        return up
    if tag == 3:
        return (left + up) // 2
    if tag == _PNG_PAETH:
        estimate = left + up - upper_left
        deltas = (abs(estimate - left), abs(estimate - up), abs(estimate - upper_left))
        if deltas[0] <= deltas[1] and deltas[0] <= deltas[2]:
            return left
        return up if deltas[1] <= deltas[2] else upper_left
    return 0


def run_length_encode(data: bytes) -> bytes:
    """Encode a payload the way ``/RunLengthDecode`` expects to find it.

    Args:
        data: The payload, which must not be empty.

    Returns:
        The encoded bytes, ending in the 128 that marks the end of the data. A run of
        two or more bytes is emitted as a repeat count and one byte, and a single byte
        as a one-byte literal, which is what ISO 32000-2 table 8 defines. A payload of
        zeros therefore encodes to a fraction of its length -- which is the point: a
        reader that decodes this to see how large it is has already lost.
    """
    out = bytearray()
    at = 0
    while at < len(data):
        run = 1
        while run < 128 and at + run < len(data) and data[at + run] == data[at]:
            run += 1
        if run == 1:
            out.append(0)
        else:
            out.append(257 - run)
        out.append(data[at])
        at += run
    out.append(128)
    return bytes(out)


def build_filtered_plannotation(
    raw: bytes,
    *,
    filters: Object | None = None,
    decode_parms: Object | None = None,
    stated_size: int | None = None,
) -> bytes:
    """Build a document whose page-0 plannotation is stored under a filter chain of choice.

    The bound a reader places on what it will decompress has to hold for every chain,
    not for the one its author had in mind, because the chain is the attacker's to
    choose. This builder is how a test hands the reader each of them.

    Args:
        raw: The stored bytes of the embedded-file stream, already encoded under
            ``filters``.
        filters: The ``/Filter`` entry: a name, or an array for a chain, or None for a
            stream stored as it stands.
        decode_parms: The ``/DecodeParms`` entry, or None to write none. An empty
            dictionary is not the same as none, and a reader that treats it as none
            has a hole in its bound.
        stated_size: The ``/Params /Size`` to record. None leaves the honest size of
            the two-byte placeholder in place, which is the lie an attacker tells.

    Returns:
        The document's bytes.
    """
    with pikepdf.open(io.BytesIO(build_bare())) as pdf:
        spec = pikepdf.AttachedFileSpec(
            pdf,
            b"{}",
            description="A plannotation stored under a filter chain of the writer's choosing",
            filename=_BOMB_FILENAME,
            mime_type="application/json",
            creation_date=FIXED_PDF_DATE,
            mod_date=FIXED_PDF_DATE,
        )
        pdf.attachments[_BOMB_FILENAME] = spec
        registered = pdf.attachments[_BOMB_FILENAME].obj
        registered[Name.AFRelationship] = Name.Data
        stream = registered[Name.EF][Name.F]
        if filters is None:
            stream.write(raw)
        elif decode_parms is None:
            stream.write(raw, filter=filters)
        else:
            stream.write(raw, filter=filters, decode_parms=decode_parms)
        if stated_size is not None:
            stream.stream_dict[Name.Params][Name.Size] = stated_size
        pdf.pages[0].obj[Name.AF] = Array([registered])
        return _save(pdf, object_streams=False, compress=False)


def build_aliased_attachment(
    *, key: str = "plannotation-p0000.json", filename: str = FOREIGN_DOC_FILENAME
) -> bytes:
    """Build a document where one file specification is filed under two names.

    A name tree maps names to objects, and nothing stops two names mapping to the same
    object. One of them here is a name Plannotation owns and the other is not, and the
    specification's own ``/UF`` and ``/F`` are the foreign one -- so the file is somebody
    else's, and only its key looks like ours.

    ``del pdf.attachments[key]`` deletes the key **and** replaces the specification with
    a null object, so a stripper that used it destroyed the other producer's bytes and
    left the other producer's key pointing at nothing.

    Args:
        key: The extra name-tree key to file the shared specification under.
        filename: The specification's own ``/UF`` and ``/F``, and its other key.

    Returns:
        The document's bytes.
    """
    with pikepdf.open(io.BytesIO(build_bare())) as pdf:
        spec = pikepdf.AttachedFileSpec(
            pdf,
            _FOREIGN_DOC_BYTES,
            description="Another producer's file, filed under two names",
            filename=filename,
            mime_type="text/plain",
            creation_date=FIXED_PDF_DATE,
            mod_date=FIXED_PDF_DATE,
        )
        pdf.attachments[filename] = spec
        shared = pdf.attachments[filename].obj
        pikepdf.NameTree(pdf.Root[Name.Names][Name.EmbeddedFiles])[key] = shared
        pdf.pages[0].obj[Name.AF] = Array([shared])
        pdf.Root[Name.AF] = Array([shared])
        return _save(pdf, object_streams=False, compress=False)


#: The head of the packet :func:`build_metadata_bomb` inflates to, up to the comment
#: the filler sits inside. The packet is well-formed XMP: a reader that gets as far as
#: parsing it has already spent the memory, so the defect is the reading, not the
#: parsing.
_BOMB_PACKET_HEAD: Final = (
    b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/">'
    b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description rdf:about=""><!--'
)

#: The tail of the same packet.
_BOMB_PACKET_TAIL: Final = b'--></rdf:Description></rdf:RDF></x:xmpmeta>\n<?xpacket end="w"?>\n'


def raw_pdf(bodies: Sequence[bytes]) -> bytes:
    """Assemble numbered objects into a document with a classic cross-reference table.

    pikepdf cannot build some of these fixtures. qpdf decompresses a ``/Type /Metadata``
    stream when it writes a file, so a packet that is small on disk and enormous once
    inflated survives only if the bytes are assembled by hand; and pikepdf's typed API
    declines to write most of what a hostile document contains at all -- a ``/Length``
    that lies, an ``/AF`` that is not an array, a name tree with two entries under one
    key. A raw writer can express anything the syntax can.

    Args:
        bodies: The body of object 1, object 2 and so on, without the ``N 0 obj`` and
            ``endobj`` around it. Object 1 is taken to be the catalog.

    Returns:
        The document's bytes, with a fixed ``/ID`` so that two runs agree.
    """
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    start_xref = len(out)
    out += f"xref\n0 {len(bodies) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    identifier = b"<0123456789abcdef0123456789abcdef>"
    out += f"trailer\n<< /Size {len(bodies) + 1} /Root 1 0 R /ID [".encode()
    out += identifier + identifier
    out += f"] >>\nstartxref\n{start_xref}\n%%EOF\n".encode()
    return bytes(out)


def build_metadata_bomb(*, megabytes: int = BOMB_MEGABYTES) -> bytes:
    """Build a small document whose catalog XMP packet inflates to something enormous.

    The packet is the same attacker-controlled input as a plannotation, and it is read
    on every operation: to decide whether a declaration is already there, whether one
    could be spliced in, and what part of PDF/A the document claims. A reader that
    bounds the plannotation and not the packet has bounded nothing.

    Args:
        megabytes: How many mebibytes the packet inflates to.

    Returns:
        The document's bytes -- a few hundred kilobytes of them.
    """
    compressor = zlib.compressobj(9)
    filler = b"A" * len(_BOMB_CHUNK)
    parts = [compressor.compress(_BOMB_PACKET_HEAD)]
    parts.extend(compressor.compress(filler) for _ in range(megabytes))
    parts.append(compressor.compress(_BOMB_PACKET_TAIL))
    parts.append(compressor.flush())
    raw = b"".join(parts)
    content = b"0.5 w 56.7 56.7 481.9 728.5 re S\n"
    page = (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595.2756 841.8898] "
        b"/Resources << >> /Contents 4 0 R /Rotate 0 >>"
    )
    contents = f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"endstream"
    metadata = (
        (
            f"<< /Type /Metadata /Subtype /XML /Filter /FlateDecode /Length {len(raw)} >>\nstream\n"
        ).encode()
        + raw
        + b"\nendstream"
    )
    return raw_pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R /Metadata 5 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            page,
            contents,
            metadata,
        ]
    )


def build_signed() -> bytes:
    """Build a document that is signed in all three ways a detector must notice.

    A signature field ``Signature1`` holding a ``/V``, an AcroForm ``/SigFlags`` of 3,
    and a certification signature in the catalog's ``/Perms /DocMDP``. The
    ``/ByteRange`` is patched after saving so that it really spans the file around the
    signature bytes, which is what makes :func:`signature_digest` meaningful: a test
    can then show that rewriting the file destroys the digest the signature covers.

    Returns:
        The document's bytes.
    """
    with pikepdf.open(io.BytesIO(build_drawing_set())) as pdf:
        signature = pdf.make_indirect(
            Dictionary(
                Type=Name.Sig,
                Filter=Name("/Adobe.PPKLite"),
                SubFilter=Name("/ETSI.CAdES.detached"),
                ByteRange=Array([1000000000, 1000000000, 1000000000, 1000000000]),
                Contents=String(bytes(256)),
                M=String(FIXED_PDF_DATE),
                Name=String("Test Signer"),
            )
        )
        field = pdf.make_indirect(
            Dictionary(
                FT=Name.Sig,
                T=String("Signature1"),
                V=signature,
                Type=Name.Annot,
                Subtype=Name.Widget,
                Rect=Array([0, 0, 0, 0]),
                F=132,
                P=pdf.pages[0].obj,
            )
        )
        annots = pdf.pages[0].obj[Name.Annots]
        annots.append(field)
        pdf.Root[Name.AcroForm] = pdf.make_indirect(Dictionary(Fields=Array([field]), SigFlags=3))
        pdf.Root[Name.Perms] = pdf.make_indirect(Dictionary(DocMDP=signature))
        data = bytearray(_save(pdf, object_streams=False, compress=False))

    start = data.find(b"/Contents <")
    if start < 0:  # pragma: no cover - only reachable if pikepdf changes its spelling
        msg = "the signed fixture has no hex /Contents to place a /ByteRange around"
        raise RuntimeError(msg)
    first_hex = start + len(b"/Contents <")
    last_hex = data.find(b">", first_hex)
    placeholder = b"/ByteRange [ 1000000000 1000000000 1000000000 1000000000 ]"
    at = data.find(placeholder)
    if at < 0:  # pragma: no cover - same
        msg = "the signed fixture has no /ByteRange placeholder to patch"
        raise RuntimeError(msg)
    spans = f"/ByteRange [ 0 {first_hex - 1} {last_hex + 1} {len(data) - last_hex - 1} ]".encode()
    data[at : at + len(placeholder)] = spans.ljust(len(placeholder))
    return bytes(data)


def signature_digest(data: bytes) -> str:
    """Digest exactly the bytes a signature verifier would hash.

    Args:
        data: A signed document's bytes.

    Returns:
        The SHA-256 of the two spans the first ``/ByteRange`` names, which is what a
        verifier checks the signature against. Two documents with the same digest
        carry the same signed content; a document whose digest has changed has a
        signature that no longer verifies.

    Raises:
        ValueError: If the document carries no readable ``/ByteRange``.
    """
    at = data.find(b"/ByteRange [")
    if at < 0:
        msg = "this document carries no /ByteRange"
        raise ValueError(msg)
    numbers = data[at + len(b"/ByteRange [") : data.index(b"]", at)].split()
    if len(numbers) != _BYTE_RANGE_LENGTH:
        msg = f"a /ByteRange has four numbers, this one has {len(numbers)}"
        raise ValueError(msg)
    start, length, resume, rest = (int(value) for value in numbers)
    covered = data[start : start + length] + data[resume : resume + rest]
    return hashlib.sha256(covered).hexdigest()


# ---------------------------------------------------------------------------
# Plannotations
# ---------------------------------------------------------------------------
#: A valid IFC GlobalId, reused wherever one is needed.
_GUID: Final = "14t7YOs$QNL7Hfom5OO5uF"

#: The tool the fixture plannotations record as their writer.
_GENERATOR: Final = Generator(
    name="plannotation-tests", version="0.1", created="2024-01-01T00:00:00Z"
)


def plannotation(
    *,
    page_index: int,
    width_mm: float,
    height_mm: float,
    rotation: Rotation = 0,
    sheet_id: str = "A-101",
    level: str = "L1",
) -> Plannotation:
    """Build a valid plannotation at a chosen conformance level.

    Args:
        page_index: The zero-based page the plannotation describes.
        width_mm: The page's unrotated width in millimetres.
        height_mm: The page's unrotated height in millimetres.
        rotation: The page's ``/Rotate``.
        sheet_id: The sheet number to record.
        level: ``"L1"`` for page and sheet with a viewport, ``"L2"`` to add an
            element, ``"L3"`` to add an annotation that links to it.

    Returns:
        The plannotation.

    Raises:
        ValueError: If ``level`` is not one of the three.
    """
    if level not in {"L1", "L2", "L3"}:
        msg = f"level must be L1, L2 or L3, got {level!r}"
        raise ValueError(msg)
    viewport = Viewport(
        id="vp-plan",
        name="Ground floor plan",
        kind="plan",
        scale=100,
        paperBBox=(20.0, 40.0, width_mm - 20.0, height_mm - 40.0),
        plane=Plane(origin=(0, 0, 0), xAxis=(1, 0, 0), yAxis=(0, 1, 0)),
        paperToPlane=(100, 0, 0, 100, -2000, -4000),
    )
    elements = (
        [
            Element(
                id="e-wall-01",
                ifcClass="IfcWall",
                ifcGuid=_GUID,
                name="Exterior wall",
                paperBBox=(40.0, 80.0, 240.0, 86.0),
                representation="cut",
                provenance=Provenance.AUTHORED,
                viewport="vp-plan",
            )
        ]
        if level in {"L2", "L3"}
        else None
    )
    annotations = (
        [
            Annotation(
                id="a-tag-01",
                type="tag",
                text="W-01",
                paperBBox=(100.0, 92.0, 120.0, 98.0),
                shows=Shows(element="e-wall-01", property="Name"),
                provenance=Provenance.AUTHORED,
                viewport="vp-plan",
            ),
            Annotation(
                id="a-dim-01",
                type="dimension",
                value=20000,
                unit="mm",
                paperBBox=(40.0, 60.0, 240.0, 70.0),
                measures=["e-wall-01"],
                provenance=Provenance.AUTHORED,
                viewport="vp-plan",
            ),
        ]
        if level == "L3"
        else None
    )
    return Plannotation(
        plannotation="0.1",
        generator=_GENERATOR,
        provenance=Provenance.AUTHORED,
        # The viewport carries a paperToPlane, and SPEC 3.5 requires a plannotation that
        # does so to declare the unit its output is in: without it a derived number is
        # not a length. The validator reports the omission as PL-GEO-013, which is how
        # this was noticed.
        model=Model(lengthUnit="mm"),
        page=Page(index=page_index, widthMm=width_mm, heightMm=height_mm, rotation=rotation),
        sheet=Sheet(
            id=sheet_id,
            title="Ground floor plan",
            revision="C",
            discipline="architecture",
            drawingType="plan",
            scale=100,
            project=Project(name="Fixture project", number="2024-001"),
        ),
        viewports=[viewport],
        elements=elements,
        annotations=annotations,
    )


def drawing_set_plannotations() -> list[Plannotation]:
    """Build one plannotation per page of :func:`build_drawing_set`.

    Returns:
        A plannotation for page 0 at L3 and one for page 1 at L2, each stating the
        unrotated size and the rotation of the page it belongs to.
    """
    return [
        plannotation(
            page_index=0,
            width_mm=A3_WIDTH_MM,
            height_mm=A3_HEIGHT_MM,
            rotation=0,
            sheet_id="A-101",
            level="L3",
        ),
        plannotation(
            page_index=1,
            width_mm=A4_WIDTH_MM,
            height_mm=A4_HEIGHT_MM,
            rotation=90,
            sheet_id="A-201",
            level="L2",
        ),
    ]


def build_many_attachments(count: int) -> bytes:
    """Build a document carrying many foreign attachments and no Plannotation data.

    The number of attachments is the document's to choose, so anything a reader or a
    writer does once per attachment must be linear in it. This fixture is how a test
    says so: it is small on disk and large in the one dimension that matters.

    Args:
        count: How many foreign attachments to register.

    Returns:
        The document's bytes.
    """
    with pikepdf.open(io.BytesIO(build_bare())) as pdf:
        for n in range(count):
            name = f"foreign-{n:05d}.bin"
            spec = pikepdf.AttachedFileSpec(
                pdf,
                b"x",
                description="A third party's file, which Plannotation must leave alone",
                filename=name,
                mime_type="application/octet-stream",
                creation_date=FIXED_PDF_DATE,
                mod_date=FIXED_PDF_DATE,
                relationship=Name.Supplement,
            )
            pdf.attachments[name] = spec
        return _save(pdf, object_streams=False, compress=False)
