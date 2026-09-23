# SPDX-License-Identifier: Apache-2.0
"""What a model is shown of one page: its picture, its text, and its label.

The picture is rendered with the same pinned pdfium options the appearance tests use,
at 150 dpi, and encoded as PNG here rather than through an imaging library: a PNG of
an RGB array is a zlib stream with a small header, and writing it directly keeps the
bytes -- and so the prompt cache key -- identical from one machine to the next.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from plannotation.model import canonical_json
from plannotation.pdf import embed
from plannotation.pdf.extract import page_text
from plannotation.pdf.render import render_page

if TYPE_CHECKING:
    from pathlib import Path

    import numpy as np
    from numpy.typing import NDArray

#: The resolution the page is shown at (design brief section 13).
BENCH_DPI: Final = 150.0

_PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
_RGB_DEPTH: Final = 8
_RGB_COLOUR_TYPE: Final = 2
_RGB_CHANNELS: Final = 3
_ZLIB_LEVEL: Final = 9


@dataclass(frozen=True)
class PageInput:
    """One page, as each condition presents it.

    Attributes:
        png: The page rendered at :data:`BENCH_DPI`, as PNG bytes.
        text: The page's extracted text.
        label: The page's label as canonical JSON, or None when it has none.
    """

    png: bytes
    text: str
    label: str | None


def load_page(document: Path, page: int, *, dpi: float = BENCH_DPI) -> PageInput:
    """Prepare one page of a PDF for both conditions.

    Args:
        document: The PDF.
        page: The one-based page number.
        dpi: The resolution to render at.

    Returns:
        The page's picture, text and label.

    Raises:
        FileNotFoundError: If the PDF is not there.
    """
    if not document.is_file():
        msg = f"{document} does not exist; run `plannotation samples build` first"
        raise FileNotFoundError(msg)
    index = page - 1
    labels = embed.read(document).pages
    label = labels.get(index)
    return PageInput(
        png=encode_png(render_page(document, index, dpi=dpi)),
        text=page_text(document, index),
        label=None if label is None else canonical_json(label),
    )


def encode_png(pixels: NDArray[np.uint8]) -> bytes:
    """Encode an 8-bit RGB array as a PNG.

    Args:
        pixels: A ``(height, width, 3)`` array, as :func:`render_page` returns.

    Returns:
        The PNG file's bytes.

    Raises:
        ValueError: If the array is not ``(height, width, 3)``.
    """
    if pixels.ndim != _RGB_CHANNELS or pixels.shape[2] != _RGB_CHANNELS:
        msg = f"expected an (height, width, 3) RGB array, got shape {pixels.shape}"
        raise ValueError(msg)
    height, width = int(pixels.shape[0]), int(pixels.shape[1])
    header = struct.pack(">IIBBBBB", width, height, _RGB_DEPTH, _RGB_COLOUR_TYPE, 0, 0, 0)
    # Filter type 0 on every scanline: one zero byte, then the row as it is.
    rows = b"".join(b"\x00" + pixels[row].tobytes() for row in range(height))
    return (
        _PNG_SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(rows, _ZLIB_LEVEL))
        + _chunk(b"IEND", b"")
    )


def _chunk(kind: bytes, data: bytes) -> bytes:
    """Frame one PNG chunk: length, type, data, and the CRC of type and data.

    Args:
        kind: The four-byte chunk type.
        data: Its payload.

    Returns:
        The framed chunk.
    """
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
