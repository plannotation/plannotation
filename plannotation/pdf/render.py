# SPDX-License-Identifier: Apache-2.0
"""Rasterise PDF pages, and prove that two documents look identical.

This module exists to hold one promise to account: writing a label must not change
how a page looks (specification section 1.2 (1)). :func:`assert_same_appearance`
renders both documents and compares them pixel for pixel, with no tolerance at all,
and when they differ it says which page, how many pixels, and where.

Why exact equality is the right test
------------------------------------
Anti-aliasing is what makes it work. With smoothing on, moving one line by 0.01 pt
-- a fiftieth of a pixel at 150 dpi -- changes 4722 pixels by up to 5 levels, so
exact comparison detects a displacement of 1/7200 inch. Turning anti-aliasing off,
or allowing a tolerance, would blunt exactly the check that is wanted.

Why every render option is pinned
---------------------------------
:data:`RENDER_OPTIONS` sets every flag pdfium offers, including the ones whose
values are already the library's defaults. A default that changes in a future
release would otherwise silently change what the guarantee means. Two of them are
worth naming: ``optimize_mode="lcd"`` turns on subpixel text and must never be used,
and ``rev_byteorder`` changed one page of the test fixture and not the other,
because the second page is greyscale and BGR equals RGB there -- a flag can look
inert on one page and matter on the next.

What this module cannot police
------------------------------
Rendering is not the whole promise. Rewriting a content stream into a different
encoding changes no pixels at all, so a comparison of renders would pass; only a
comparison of the streams themselves catches it. That is why
:func:`plannotation.pdf.embed.save_labelled` pins ``normalize_content=False`` rather
than relying on this test to notice.

Memory
------
An A3 page at 150 dpi is 13 MB as RGB, an A0 page 105 MB. Documents are therefore
compared page by page, each pair released before the next is read, rather than
rasterised whole and then compared.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

import numpy as np
import pypdfium2 as pdfium

from plannotation.errors import AppearanceChangedError, RenderError

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

    from numpy.typing import NDArray

__all__ = [
    "DPI",
    "RENDER_OPTIONS",
    "PageDiff",
    "assert_same_appearance",
    "compare_documents",
    "describe_diffs",
    "diff_arrays",
    "page_count",
    "render_options",
    "render_page",
]

_LOGGER: Final = logging.getLogger(__name__)

#: The resolution the appearance guarantee is asserted at, in dots per inch. Fine
#: enough that a one-point displacement moves two pixels; coarse enough that an A3
#: sheet is about 13 MB per page as 8-bit RGB.
DPI: Final = 150.0

#: One PDF canvas unit is 1/72 inch, and pdfium scales in pixels per canvas unit.
_POINTS_PER_INCH: Final = 72.0

#: Every pdfium render option, pinned. Defaults are written out rather than
#: inherited, so that a library upgrade cannot quietly change what a comparison
#: means. ``scale`` is not here because it follows from the resolution; see
#: :func:`render_options`.
RENDER_OPTIONS: Final[Mapping[str, object]] = MappingProxyType(
    {
        "rotation": 0,  # honour the page's own /Rotate and add nothing
        "crop": (0, 0, 0, 0),
        "draw_annots": True,  # annotations are part of how a page looks
        "may_draw_forms": False,  # form rendering needs init_forms(); keep it off
        "grayscale": False,
        "optimize_mode": None,  # never "lcd": subpixel text is display-dependent
        "no_smoothtext": False,  # anti-aliasing on: it is what makes this test sharp
        "no_smoothimage": False,
        "no_smoothpath": False,
        "force_halftone": False,
        "limit_image_cache": False,
        "rev_byteorder": True,  # RGB rather than BGR, so the arrays read naturally
        "prefer_bgrx": False,  # three bytes per pixel
        "maybe_alpha": False,  # never let page transparency change the pixel format
        "fill_color": (255, 255, 255, 255),
        "extra_flags": 0,
    }
)


def render_options(dpi: float = DPI) -> dict[str, object]:
    """Return the full set of render options for one resolution.

    Args:
        dpi: The resolution to render at.

    Returns:
        :data:`RENDER_OPTIONS` with ``scale`` added, ready to pass to pdfium.

    Raises:
        ValueError: If ``dpi`` is not positive.
    """
    if dpi <= 0:
        msg = f"dpi must be positive, got {dpi!r}"
        raise ValueError(msg)
    return {**RENDER_OPTIONS, "scale": dpi / _POINTS_PER_INCH}


class _OpenDocument:
    """A pdfium document held open, with the library's untyped surface kept inside.

    pypdfium2 ships no type information, and this package forbids an unchecked type
    from reaching a signature. Wrapping the document in one small class keeps the
    untyped calls in one place and gives every other function in this module a typed
    object to work with.
    """

    def __init__(self, source: Path | str) -> None:
        """Open a document for rendering.

        Args:
            source: The PDF to open.

        Raises:
            RenderError: If pdfium cannot open the file.
        """
        self._source = source
        try:
            self._document = pdfium.PdfDocument(str(source))
        except Exception as exc:
            msg = f"{source} could not be opened for rendering: {exc}"
            raise RenderError(msg) from exc

    @property
    def page_count(self) -> int:
        """Return how many pages the document has.

        Returns:
            The page count.
        """
        count: int = len(self._document)
        return count

    def render(self, page_index: int, dpi: float) -> NDArray[np.uint8]:
        """Rasterise one page.

        Args:
            page_index: The zero-based page to render.
            dpi: The resolution.

        Returns:
            An ``(height, width, 3)`` array of 8-bit RGB, copied out of pdfium's own
            buffer so that it stays valid after the page is closed.

        Raises:
            RenderError: If the document has no such page.
        """
        if not 0 <= page_index < self.page_count:
            msg = f"{self._source} has {self.page_count} page(s); there is no page {page_index}"
            raise RenderError(msg)
        page = self._document[page_index]
        try:
            bitmap = page.render(**render_options(dpi))
            array: NDArray[np.uint8] = bitmap.to_numpy().copy()
        finally:
            page.close()
        return array

    def close(self) -> None:
        """Close the document and release its memory."""
        self._document.close()


@contextmanager
def _opened(source: Path | str) -> Iterator[_OpenDocument]:
    """Open a document for rendering and close it again.

    Args:
        source: The PDF to open.

    Yields:
        The open document.
    """
    document = _OpenDocument(source)
    try:
        yield document
    finally:
        document.close()


def page_count(source: Path | str) -> int:
    """Return how many pages a document has.

    Args:
        source: The PDF to count.

    Returns:
        The page count.

    Raises:
        RenderError: If the file cannot be opened.
    """
    with _opened(source) as document:
        return document.page_count


def render_page(source: Path | str, page_index: int, *, dpi: float = DPI) -> NDArray[np.uint8]:
    """Rasterise one page of a document.

    The page's own ``/Rotate`` is honoured, so the array has the page's displayed
    shape: a ``/Rotate 90`` A4 page renders landscape. Paper coordinates in a label
    are unrotated (specification section 3.7), so do not read a page's size off this
    array -- :func:`plannotation.pdf.embed.page_geometry` is what measures a page.

    Args:
        source: The PDF to read.
        page_index: The zero-based page to render.
        dpi: The resolution.

    Returns:
        An ``(height, width, 3)`` array of 8-bit RGB.

    Raises:
        RenderError: If the file cannot be opened or has no such page.
    """
    with _opened(source) as document:
        return document.render(page_index, dpi)


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PageDiff:
    """How two renderings of one page differ.

    An assertion that says only "the arrays differ" is useless when it fires at
    three in the morning, so every field here exists to answer a question a person
    will immediately ask: which page, how much, where, and how badly.

    Attributes:
        page_index: The zero-based page.
        dpi: The resolution both renders were taken at.
        shape_before: The shape of the first render.
        shape_after: The shape of the second. A different shape means the displayed
            size changed, which is what a corrupted ``/Rotate`` or a rewritten page
            box looks like.
        differing_pixels: How many pixels differ in any channel, or -1 when the
            shapes differ and pixels were never compared.
        total_pixels: How many pixels the page has, or -1 in the same case.
        bbox: The smallest box containing every differing pixel, as
            ``(left, top, right, bottom)`` in pixels with the origin at the top left
            -- the way a viewer reports a position -- or None when the shapes differ.
        max_channel_delta: The largest difference in any single channel, or -1.
        sample: The first differing pixel as ``((x, y), before_rgb, after_rgb)``, or
            None.
    """

    page_index: int
    dpi: float
    shape_before: tuple[int, ...]
    shape_after: tuple[int, ...]
    differing_pixels: int
    total_pixels: int
    bbox: tuple[int, int, int, int] | None
    max_channel_delta: int
    sample: tuple[tuple[int, int], tuple[int, ...], tuple[int, ...]] | None

    @property
    def fraction(self) -> float:
        """Return the share of the page's pixels that differ.

        Returns:
            A number between 0 and 1, and 0 when the shapes differ and no pixels
            were compared.
        """
        return self.differing_pixels / self.total_pixels if self.total_pixels > 0 else 0.0

    def describe(self) -> str:
        """Describe the difference in a form that can be acted on.

        Returns:
            A multi-line report naming the page, the number and share of differing
            pixels, the bounding box of the difference in pixels and in points, the
            largest channel delta and the first differing pixel with its colours
            before and after.
        """
        if self.shape_before != self.shape_after:
            return (
                f"page {self.page_index}: rendered size changed, "
                f"{self.shape_before} -> {self.shape_after} at {self.dpi:g} dpi"
            )
        if self.bbox is None:  # pragma: no cover - only reachable via a hand-built diff
            return f"page {self.page_index}: differs"
        left, top, right, bottom = self.bbox
        per_pixel = _POINTS_PER_INCH / self.dpi
        lines = [
            (
                f"page {self.page_index}: {self.differing_pixels} of "
                f"{self.total_pixels} pixels differ ({self.fraction * 100:.4f}%) "
                f"at {self.dpi:g} dpi"
            ),
            (
                f"  difference bbox (px, origin top-left): left={left} top={top} "
                f"right={right} bottom={bottom} "
                f"({right - left + 1}x{bottom - top + 1})"
            ),
            (
                f"  difference bbox (pt, origin top-left): "
                f"x={left * per_pixel:.2f}..{(right + 1) * per_pixel:.2f} "
                f"y={top * per_pixel:.2f}..{(bottom + 1) * per_pixel:.2f}"
            ),
            f"  largest channel delta: {self.max_channel_delta}",
        ]
        if self.sample is not None:
            (x, y), before, after = self.sample
            lines.append(f"  first differing pixel: (x={x}, y={y}) {before} -> {after}")
        return "\n".join(lines)


def diff_arrays(
    page_index: int,
    before: NDArray[np.uint8],
    after: NDArray[np.uint8],
    *,
    dpi: float = DPI,
) -> PageDiff | None:
    """Compare two renders of one page.

    Args:
        page_index: The zero-based page, for the report.
        before: The first render.
        after: The second.
        dpi: The resolution both were taken at, for the report.

    Returns:
        None when the two are bit-identical, and a :class:`PageDiff` when they are
        not. A shape mismatch is reported as its own case rather than compared, since
        two arrays of different sizes have no meaningful per-pixel difference.
    """
    if before.shape != after.shape:
        return PageDiff(
            page_index=page_index,
            dpi=dpi,
            shape_before=before.shape,
            shape_after=after.shape,
            differing_pixels=-1,
            total_pixels=-1,
            bbox=None,
            max_channel_delta=-1,
            sample=None,
        )
    if np.array_equal(before, after):
        return None
    differs = np.any(before != after, axis=-1)
    rows, columns = np.nonzero(differs)
    first = (int(rows[0]), int(columns[0]))
    # int16, because unsigned 8-bit subtraction wraps and would report 255 as 1.
    delta = np.abs(before.astype(np.int16) - after.astype(np.int16)).max()
    return PageDiff(
        page_index=page_index,
        dpi=dpi,
        shape_before=before.shape,
        shape_after=after.shape,
        differing_pixels=int(differs.sum()),
        total_pixels=int(differs.size),
        bbox=(
            int(columns.min()),
            int(rows.min()),
            int(columns.max()),
            int(rows.max()),
        ),
        max_channel_delta=int(delta),
        sample=(
            (first[1], first[0]),
            tuple(int(channel) for channel in before[first]),
            tuple(int(channel) for channel in after[first]),
        ),
    )


def compare_documents(
    before: Path | str,
    after: Path | str,
    *,
    dpi: float = DPI,
) -> list[PageDiff]:
    """Compare two documents page by page.

    Every page is compared and every difference reported, rather than stopping at the
    first: "pages 3 and 17 differ" is a more useful thing to be told than "page 3
    differs", twice.

    Args:
        before: The first document.
        after: The second.
        dpi: The resolution to render both at.

    Returns:
        One :class:`PageDiff` per differing page, in page order. An empty list means
        every page is bit-identical.

    Raises:
        RenderError: If either file cannot be opened, or they have different page
            counts, which is a difference no per-page comparison could describe.
    """
    with _opened(before) as first, _opened(after) as second:
        if first.page_count != second.page_count:
            msg = (
                f"page count changed: {before} has {first.page_count} page(s), "
                f"{after} has {second.page_count}"
            )
            raise RenderError(msg)
        diffs: list[PageDiff] = []
        for page_index in range(first.page_count):
            diff = diff_arrays(
                page_index,
                first.render(page_index, dpi),
                second.render(page_index, dpi),
                dpi=dpi,
            )
            if diff is not None:
                diffs.append(diff)
    _LOGGER.debug(
        "compared %s and %s at %g dpi: %d differing page(s)", before, after, dpi, len(diffs)
    )
    return diffs


def describe_diffs(diffs: list[PageDiff]) -> str:
    """Render a list of page differences as one report.

    Args:
        diffs: The differences, as :func:`compare_documents` returns them.

    Returns:
        Every difference described, one after another, or a line saying there were
        none.
    """
    if not diffs:
        return "every page renders identically"
    return "\n".join(diff.describe() for diff in diffs)


def assert_same_appearance(before: Path | str, after: Path | str, *, dpi: float = DPI) -> None:
    """Assert that two documents render to identical pixels on every page.

    This is the appearance guarantee, as a callable: the test the whole project rests
    on. Equality is exact, with no tolerance.

    Args:
        before: The document as it was.
        after: The document as it is now.
        dpi: The resolution to compare at.

    Raises:
        AppearanceChangedError: If any page differs, with the full report.
        RenderError: If either file cannot be opened or the page counts differ.
    """
    diffs = compare_documents(before, after, dpi=dpi)
    if not diffs:
        return
    msg = (
        f"appearance changed between {before} and {after}:\n"
        f"{describe_diffs(diffs)}\n"
        "The PDF page is the leading document: writing or removing a label must "
        "leave every page rendering exactly as it did."
    )
    raise AppearanceChangedError(msg)
