# SPDX-License-Identifier: Apache-2.0
"""A generative harness over malformed and hostile PDFs, and the invariants it asserts.

Why this file exists
--------------------
Four rounds of review each found a defect in the PDF carrier, and each round's fix
introduced the next round's defect. The decompression-bomb fix was bypassable by
choosing a different ``/Filter``; the filter fix was bypassable by choosing a
``/DecodeParms`` predictor; the predictor fix allocated a row from three unchecked
integers. Every one of those was found by a person reading the code, and every one was
one substitution away from a defect the same person would have found if they had looked
one field further.

:mod:`tests.test_pdf` holds a regression test for each of them, and a regression test
proves only that one shape is fixed. This module is the other half: it builds documents
across the parameter space those defects live in -- every filter name and chains of two
and three; every predictor parameter at absurd, zero, negative and non-integer values;
lengths and sizes that lie in both directions; filespecs, ``/AF`` arrays, name trees and
page trees malformed in every way the syntax allows -- and asserts six invariants that
must hold for all of them at once. The next exotic input is meant to be caught here
rather than by the next reviewer.

The seven invariants (asserted for every generated document)
------------------------------------------------------------
I1. Nothing escapes ``read``, ``carrier_report``, ``attach`` or ``strip`` that is not a
    :class:`~plannotation.errors.PlannotationError`. :class:`MemoryError`,
    :class:`RecursionError` and a raw ``pikepdf.PdfError`` are each failures: the first
    two say the bound was not applied, and the third says a caller has to catch a C++
    library's exception hierarchy to use this package.
II. No one document raises peak resident memory by more than
    :data:`PEAK_MEMORY_BUDGET`, eight times the cap a label is read under, and no whole
    sweep raises it past its own budget. Measured with ``getrusage``, not assumed: a
    refusal issued after the allocation reads exactly like one issued before it, which is
    how the last three defects survived review.
III. No single operation takes longer than :data:`OPERATION_TIME_BUDGET`, because a
    bound on bytes is not a bound on time.
IV. ``read(strict=False)`` never raises :class:`~plannotation.errors.InvalidPlannotationError`.
    Section 4.3 (2) says a label a reader will not accept is a label that is absent, and
    absence is a return value.
V.  ``attach`` and ``strip`` either complete or refuse. A refusal leaves no output file
    at all; a completion leaves one the PDF library can open, no larger than the input
    plus what was written into it.
VI. ``strip`` never destroys or alters foreign material: every attachment Plannotation does
    not own survives with its bytes, its associations are not dropped, and a packet with
    no Plannotation declaration in it comes back byte for byte.
VII. A document ``attach`` labelled really declares what it reports declaring -- the
    claim is in the packet, outside every comment and CDATA section, and inside the RDF
    -- and stripping it again restores the packet exactly. This one is here because the
    other six did not reach it: reverting the defect that spliced Plannotation's claim into
    another producer's comment broke none of them, and the document announced a
    conformance no XMP reader would ever see.

Two properties this harness has to have, and had to be taught
-------------------------------------------------------------
It must not ask the module under test what the answers are. The first version used
:func:`plannotation.pdf.embed.is_plannotation_filename` to decide which attachments were
another producer's, so when the module was reverted to a predicate that matched a name
ending in a newline, the harness agreed with it and watched somebody's file be deleted
without noticing. :func:`owns` and :func:`visible` are written out here for that reason.

It must not become the thing it is testing. :func:`decoded_packet` originally read a
packet with ``stream.read_bytes()``, and one document in this very corpus -- a
``/Columns`` of two thousand million -- cost 743 MB to read that way, out of the PDF
library's own unbounded decoder. The harness now decodes under its own bound.

How it stays fast and deterministic
-----------------------------------
Each corpus is swept once per session and every test reads the result, so the cost is one
pass rather than one per assertion. The documents are built by hand
(:func:`tests.pdf_fixtures.raw_pdf`) because pikepdf's typed API declines to write most
of what is needed -- a ``/Length`` that lies, an ``/AF`` that is not an array, two
name-tree entries under one key. Nothing reads the clock, the locale or the network, and
the one place a choice is made at random is seeded from :data:`SEED`, so the corpus is
the same corpus on every machine and in every run.
"""

from __future__ import annotations

import random
import re
import resource
import sys
import time
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, cast

import pikepdf
import pytest

from plannotation.constants import INDEX_FILENAME, SPEC_URI, plannotation_filename
from plannotation.errors import InvalidPlannotationError, PlannotationError
from plannotation.model import canonical_bytes
from plannotation.pdf import embed
from tests import pdf_fixtures as fx

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from pathlib import Path

# ---------------------------------------------------------------------------
# Budgets, stated rather than assumed
# ---------------------------------------------------------------------------
#: The seed behind every choice this module makes at random.
SEED: Final = 20_260_922

#: How much any one document may raise this process's peak resident memory.
#:
#: Stated as a multiple of the cap a label is read under, because that is what bounds it:
#: a reader decoding a label at the cap holds the inflated bytes, the unpredicted copy and
#: the string it then tries to parse, and pays the allocator on top. Measured across both
#: corpora, the worst single document costs 69 MB -- a Flate bomb that is inflated up to
#: the cap and refused there -- and the worst in the malformed corpus costs 4 MB.
#:
#: What it catches is an allocation the document chose rather than the reader: 1,963 MB
#: for a ``/Columns`` of two thousand million, 743 MB for the same parameter handed to the
#: PDF library's own decoder, 567 MB for a stored stream copied into Python to measure it.
#:
#: ``getrusage`` reports a high-water mark, so this measures the rise in that mark while
#: one document was processed. A document that allocates less than an earlier one already
#: did shows nothing -- which is why the whole-sweep budgets below are kept as a backstop
#: and why the two corpora are swept separately.
PEAK_MEMORY_BUDGET: Final = 8 * embed._MAX_PLANNOTATION_BYTES

#: How much more resident memory the malformed corpus may reach than it started with.
#:
#: Every document in it is a few kilobytes on disk and holds a label of about one
#: kilobyte, so nothing in it has any business allocating anything. What this budget
#: covers is the first call's warm-up -- compiling the JSON Schema validators, importing
#: numpy's inner loops -- measured at 43 MB for the whole corpus.
SWEEP_MEMORY_BUDGET: Final = 96 * 1024 * 1024

#: The same, for the handful of documents that are legitimately large once decoded.
#:
#: A label at the cap really is fifteen mebibytes, and decoding one costs several
#: multiples of that at once: the inflated bytes, the unpredicted copy, and the string a
#: reader then tries to parse as JSON. CPython does not hand freed arenas back to the
#: operating system either, so a few such documents in one process accumulate -- measured
#: at 378 MB for this corpus under pytest, against 43 MB for the malformed one.
#:
#: This is the loose backstop; the sharp assertion is :data:`PEAK_MEMORY_BUDGET`, which
#: is per document and the same for both corpora.
HEAVY_MEMORY_BUDGET: Final = 448 * 1024 * 1024

#: How long any one operation on any one of these documents may take.
#:
#: The slowest legitimate thing here is inflating and unpredicting a label at the cap,
#: measured at a quarter of a second. One second is four times that, and below what a
#: single Paeth-predicted label cost before the predictors were vectorised and the two
#: sequential ones capped: 2.8 s.
OPERATION_TIME_BUDGET: Final = 1.0

#: How long the whole sweep may take, so that it stays in every run of ``make check``.
SWEEP_TIME_BUDGET: Final = 30.0

#: How much larger than its input, plus what was written into it, an output may be.
#:
#: qpdf rewrites a document rather than appending to it, so the output is never the input
#: plus a delta; object streams are unpacked, cross-reference tables rebuilt and a
#: ``/Type /Metadata`` stream written uncompressed. A quarter of a mebibyte covers all of
#: that for documents of this size. The one unbounded term is the packet, and it is
#: bounded by the cap the reader places on one: a packet larger than that is refused and
#: no output is written at all, so the allowance is that cap plus room for the rewriting.
#: It still catches the 66 KB input that produced 67 MB.
OUTPUT_GROWTH_ALLOWANCE: Final = embed._MAX_XMP_BYTES + 256 * 1024

#: The timestamp every :func:`plannotation.pdf.embed.attach` here injects.
MOD_DATE: Final = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# A raw PDF writer
# ---------------------------------------------------------------------------
#: The page content stream every generated document carries.
_CONTENT: Final = b"0.5 w 56.7 56.7 481.9 728.5 re S\n"

#: A4 portrait in points, which is what the labels below claim.
_MEDIA_BOX: Final = "[0 0 595.2756 841.8898]"

#: The name Plannotation owns, and a name it does not.
_OWNED_NAME: Final = plannotation_filename(0)
_FOREIGN_NAME: Final = "site-notes.txt"

#: A page label that agrees with the page above, so that a well-formed document really is
#: one and :func:`plannotation.pdf.embed.attach` has something it can write.
PLANNOTATION: Final = fx.plannotation(page_index=0, width_mm=210.0, height_mm=297.0)
PLANNOTATION_BYTES: Final = canonical_bytes(PLANNOTATION)


#: The names Plannotation owns, spelled out here rather than asked of the module.
#:
#: ``re.fullmatch`` and not ``re.match`` with ``$``: Python's ``$`` also matches before a
#: trailing newline, which is the defect one of these documents exists to catch. A
#: harness that asked :func:`plannotation.pdf.embed.is_plannotation_filename` which files are
#: Plannotation's would agree with the module about a name ending in a newline and would
#: watch it delete somebody else's file without noticing -- measured: with this predicate
#: taken from the module, reverting that defect broke no invariant at all.
_PLANNOTATION_NAME: Final = re.compile(r"plannotation-p\d{4}\.json")


def owns(name: str) -> bool:
    """Report whether a filename is one Plannotation owns.

    Args:
        name: A name-tree key, or a specification's ``/UF`` or ``/F``.

    Returns:
        True for the index and for a page label's zero-padded name, and for nothing else.
    """
    return name == INDEX_FILENAME or bool(_PLANNOTATION_NAME.fullmatch(name))


#: The two spans of an XML document whose contents are text rather than markup.
_MARKUP_HOLES: Final = ((b"<!--", b"-->"), (b"<![CDATA[", b"]]>"))


def visible(packet: bytes) -> bytes:
    """Blank out every comment and CDATA section, keeping every byte where it is.

    Written here as well as in the module for the same reason :func:`owns` is: a harness
    that asked the module what a reader can see would agree with it about a declaration
    written inside a comment.

    Args:
        packet: An XMP packet.

    Returns:
        The packet with each comment and CDATA section, delimiters included, replaced by
        NUL, and every other byte at the offset it was at.
    """
    masked = bytearray(packet)
    at = 0
    while (start := packet.find(b"<!", at)) >= 0:
        for opener, closer in _MARKUP_HOLES:
            if packet.startswith(opener, start):
                found = packet.find(closer, start + len(opener))
                stop = len(packet) if found < 0 else found + len(closer)
                masked[start:stop] = bytes(stop - start)
                at = stop
                break
        else:
            at = start + 2
    return bytes(masked)


def pdf_stream(entries: str, data: bytes, *, length: str | None = None) -> bytes:
    """Serialise one stream object's body.

    Args:
        entries: The stream dictionary's entries, without the enclosing angle brackets
            and without ``/Length``.
        data: The stored bytes, exactly as they go into the file.
        length: What to declare as ``/Length``, or None for the truth. A declaration is
            a claim by whoever wrote the file, and this is how a test makes it a lie.

    Returns:
        The object's body, from ``<<`` to ``endstream``.
    """
    declared = str(len(data)) if length is None else length
    head = f"<< {entries} /Length {declared} >>\nstream\n".encode()
    return head + data + b"\nendstream"


def embedded_file(
    data: bytes,
    *,
    filters: str | None = "/FlateDecode",
    decode_parms: str | None = None,
    params: str | None = None,
    length: str | None = None,
) -> bytes:
    """Serialise an embedded-file stream.

    Args:
        data: The stored bytes.
        filters: The ``/Filter`` entry as it is written, or None for none.
        decode_parms: The ``/DecodeParms`` entry as it is written, or None for none.
        params: The ``/Params`` entry as it is written, None for the honest one, or the
            empty string to leave ``/Params`` out altogether.
        length: What to declare as ``/Length``.

    Returns:
        The object's body.
    """
    entries = ["/Type /EmbeddedFile", "/Subtype /application#2Fjson"]
    if params is None:
        entries.append(
            f"/Params << /Size {len(PLANNOTATION_BYTES)} /ModDate ({fx.FIXED_PDF_DATE}) >>"
        )
    elif params:
        entries.append(f"/Params {params}")
    if filters is not None:
        entries.append(f"/Filter {filters}")
    if decode_parms is not None:
        entries.append(f"/DecodeParms {decode_parms}")
    return pdf_stream(" ".join(entries), data, length=length)


def filespec(name: str, *, entries: str | None = None) -> str:
    """Serialise a file specification naming one embedded stream.

    Args:
        name: The ``/F`` and ``/UF``.
        entries: The whole dictionary body, for a test that wants a malformed one.

    Returns:
        The object's body, as text.
    """
    if entries is not None:
        return entries
    return (
        f"<< /Type /Filespec /F ({name}) /UF ({name}) /Desc (a label) "
        "/EF << /F 6 0 R /UF 6 0 R >> /AFRelationship /Data >>"
    )


def name_tree(*pairs: tuple[str, str]) -> str:
    """Serialise an ``EmbeddedFiles`` name tree with the given entries.

    Args:
        *pairs: ``(key, reference)`` pairs, in the order they are written. A name tree's
            keys are supposed to be sorted and unique, and a document is under no
            obligation to make them either.

    Returns:
        The catalog's ``/Names`` entry, as text.
    """
    body = " ".join(f"({key}) {reference}" for key, reference in pairs)
    return f"<< /EmbeddedFiles << /Names [{body}] >> >>"


_DEFAULT_TREE: Final = name_tree((_OWNED_NAME, "5 0 R"))
_FOREIGN_TREE: Final = name_tree((_FOREIGN_NAME, "5 0 R"))


def hostile(
    *,
    embedded: bytes | None = None,
    spec: str | None = None,
    metadata: bytes | None = None,
    page_af: str | None = "[5 0 R]",
    catalog_af: str | None = None,
    names: str | None = _DEFAULT_TREE,
    pages: tuple[str, str] = ("[3 0 R]", "1"),
    extra: Sequence[bytes] = (),
) -> bytes:
    """Assemble one document out of the parts a hostile writer chooses.

    Object numbers are fixed so that every reference in every argument means the same
    thing: 1 is the catalog, 2 the page tree, 3 the page, 4 its content stream, 5 the file
    specification, 6 the embedded stream, 7 the XMP packet, and 8 onwards whatever the
    caller adds.

    Args:
        embedded: Object 6's body, or None for a Flate-compressed copy of :data:`PLANNOTATION`.
        spec: Object 5's body, or None for a well-formed specification.
        metadata: Object 7's body, or None to leave the document without XMP. Object 7 is
            written as ``null`` in that case, so the numbering never shifts.
        page_af: The page's ``/AF`` entry as it is written, or None for none.
        catalog_af: The catalog's ``/AF`` entry, or None for none.
        names: The catalog's ``/Names`` entry, or None for none.
        pages: The page tree's ``/Kids`` and ``/Count``, each as it is written. They are
            one argument because they lie together: a ``/Count`` is only interesting
            beside the ``/Kids`` it disagrees with.
        extra: Bodies for objects 8 onwards.

    Returns:
        The document's bytes.
    """
    catalog = ["/Type /Catalog", "/Pages 2 0 R"]
    if names is not None:
        catalog.append(f"/Names {names}")
    if catalog_af is not None:
        catalog.append(f"/AF {catalog_af}")
    if metadata is not None:
        catalog.append("/Metadata 7 0 R")
    page = ["/Type /Page", "/Parent 2 0 R", f"/MediaBox {_MEDIA_BOX}"]
    page.append("/Resources << >>")
    page.append("/Contents 4 0 R")
    page.append("/Rotate 0")
    if page_af is not None:
        page.append(f"/AF {page_af}")
    kids, count = pages
    bodies: list[bytes] = [
        ("<< " + " ".join(catalog) + " >>").encode(),
        f"<< /Type /Pages /Kids {kids} /Count {count} >>".encode(),
        ("<< " + " ".join(page) + " >>").encode(),
        pdf_stream("", _CONTENT),
        (filespec(_OWNED_NAME) if spec is None else spec).encode(),
        embedded_file(zlib.compress(PLANNOTATION_BYTES, 9)) if embedded is None else embedded,
        b"null" if metadata is None else metadata,
        *extra,
    ]
    return fx.raw_pdf(bodies)


@dataclass(frozen=True)
class Case:
    """One generated document, and the name a failure reports it by.

    Attributes:
        name: A stable identifier, used as the filename and in every message.
        data: The document's bytes.
        owned: Whether the document already carries a file Plannotation owns, which decides
            whether :func:`plannotation.pdf.embed.attach` can get as far as writing.
    """

    name: str
    data: bytes
    owned: bool = True


def xmp(
    body: bytes, *, head: bytes = b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
) -> bytes:
    """Wrap an RDF body in an XMP packet.

    Args:
        body: What goes between ``<x:xmpmeta>`` and its closing tag.
        head: The packet header, which a test may spell differently.

    Returns:
        The packet's bytes.
    """
    return (
        head
        + b'<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        + body
        + b'</x:xmpmeta>\n<?xpacket end="w"?>\n'
    )


def rdf(body: bytes) -> bytes:
    """Wrap descriptions in an ``rdf:RDF`` element inside an XMP packet.

    Args:
        body: The ``rdf:Description`` elements.

    Returns:
        The packet's bytes.
    """
    return xmp(
        b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">' + body + b"</rdf:RDF>"
    )


# ---------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------
#: Every filter ISO 32000-2 table 6 names. A reader that bounds one of them bounds
#: nothing, because the ``/Filter`` entry belongs to the document.
FILTER_NAMES: Final = (
    "/ASCIIHexDecode",
    "/ASCII85Decode",
    "/LZWDecode",
    "/FlateDecode",
    "/RunLengthDecode",
    "/CCITTFaxDecode",
    "/JBIG2Decode",
    "/DCTDecode",
    "/JPXDecode",
    "/Crypt",
)

#: Chains of two and three, and the shapes that are not a chain at all.
FILTER_CHAINS: Final = (
    "[/ASCIIHexDecode /FlateDecode]",
    "[/FlateDecode /FlateDecode]",
    "[/ASCII85Decode /FlateDecode]",
    "[/FlateDecode /ASCIIHexDecode]",
    "[/LZWDecode /FlateDecode]",
    "[/ASCIIHexDecode /ASCII85Decode /FlateDecode]",
    "[/FlateDecode /FlateDecode /FlateDecode]",
    "[/ASCII85Decode /RunLengthDecode /FlateDecode]",
    "[]",
    "[/FlateDecode]",
    "(FlateDecode)",
    "3",
    "<< /Name /FlateDecode >>",
    "null",
    "true",
)


def _filter_cases() -> Iterator[Case]:
    """Build one document per filter name and per chain.

    Yields:
        The cases. Each stores the same Flate-compressed label, so the only thing that
        varies is what the document says it is stored under -- which is the whole point:
        the reader must answer for the declaration, not for the bytes.
    """
    stored = zlib.compress(PLANNOTATION_BYTES, 9)
    for index, written in enumerate((*FILTER_NAMES, *FILTER_CHAINS)):
        slug = written.strip("[]()<>").replace("/", "").replace(" ", "-") or "empty"
        yield Case(
            f"filter-{index:02d}-{slug}", hostile(embedded=embedded_file(stored, filters=written))
        )
    yield Case("filter-absent", hostile(embedded=embedded_file(PLANNOTATION_BYTES, filters=None)))


#: Every value each predictor parameter is given, absurd and ordinary alike. Three of
#: these integers are multiplied together and the product decides an allocation, so a
#: bound that is applied afterwards is not a bound.
PREDICTOR_VALUES: Final = (
    "-2147483648",
    "-1",
    "0",
    "1",
    "2",
    "3",
    "9",
    "10",
    "11",
    "12",
    "13",
    "14",
    "15",
    "16",
    "100",
    "1099511627776",
    "2.5",
    "/Twelve",
    "(12)",
    "true",
    "null",
)

COLORS_VALUES: Final = (
    "-1",
    "0",
    "1",
    "2",
    "3",
    "4",
    "32",
    "33",
    "1000000",
    "1099511627776",
    "2.5",
    "/One",
)

BITS_VALUES: Final = (
    "-8",
    "0",
    "1",
    "2",
    "3",
    "4",
    "8",
    "16",
    "17",
    "32",
    "64",
    "1099511627776",
    "0.5",
)

COLUMNS_VALUES: Final = (
    "-2000000000",
    "-1",
    "0",
    "1",
    "2",
    "511",
    "512",
    "2147483647",
    "2000000000",
    "1099511627776",
    "2.5",
    "(512)",
)

#: The shapes ``/DecodeParms`` itself can take, quite apart from what is inside it.
DECODE_PARMS_SHAPES: Final = (
    "<< >>",
    "[ << /Predictor 12 /Columns 512 >> ]",
    "[ << /Predictor 12 >> << /Predictor 12 >> ]",
    "[]",
    "/FlateDecode",
    "12",
    "null",
    "(<< /Predictor 12 >>)",
    "[ null ]",
)


def _predicted(payload: bytes, columns: int = 512, tag: int = 2) -> bytes:
    """Encode a payload as PNG predictor rows, cheaply.

    Args:
        payload: The bytes to encode. Zeros predict to zeros under every filter type, so
            a payload of zeros needs no arithmetic.
        columns: The decoded row length.
        tag: The PNG filter type each row is tagged with.

    Returns:
        The encoded bytes, Flate-compressed.
    """
    rows = len(payload) // columns
    return zlib.compress((bytes([tag]) + bytes(columns)) * rows, 9)


def _decode_parms_cases() -> Iterator[Case]:
    """Build a document for every value of every predictor parameter, and then some.

    Yields:
        The cases. Each parameter is swept with the others at a sane default, then a
        seeded sample of combinations is drawn, because a defect can hide in a product
        of values each of which is unremarkable on its own.
    """
    stored = _predicted(bytes(64 * 512))
    empty = zlib.compress(b"", 9)
    sweeps = (
        ("predictor", "Predictor", PREDICTOR_VALUES),
        ("colors", "Colors", COLORS_VALUES),
        ("bits", "BitsPerComponent", BITS_VALUES),
        ("columns", "Columns", COLUMNS_VALUES),
    )
    for slug, key, values in sweeps:
        defaults = {"Predictor": "12", "Colors": "1", "BitsPerComponent": "8", "Columns": "512"}
        for index, value in enumerate(values):
            written = dict(defaults, **{key: value})
            parms = "<< " + " ".join(f"/{k} {v}" for k, v in written.items()) + " >>"
            tidy = value.strip("()/").replace(".", "-")
            for payload, suffix in ((stored, ""), (empty, "-empty")):
                yield Case(
                    f"parms-{slug}-{index:02d}-{tidy}{suffix}",
                    hostile(embedded=embedded_file(payload, decode_parms=parms)),
                )
    for index, shape in enumerate(DECODE_PARMS_SHAPES):
        yield Case(
            f"parms-shape-{index}", hostile(embedded=embedded_file(stored, decode_parms=shape))
        )
    # Seeded, so the corpus is the same corpus on every machine; nothing here is a
    # secret and nothing here is drawn more than once.
    chooser = random.Random(SEED)  # noqa: S311
    for index in range(40):
        parms = (
            f"<< /Predictor {chooser.choice(PREDICTOR_VALUES)} "
            f"/Colors {chooser.choice(COLORS_VALUES)} "
            f"/BitsPerComponent {chooser.choice(BITS_VALUES)} "
            f"/Columns {chooser.choice(COLUMNS_VALUES)} >>"
        )
        yield Case(
            f"parms-mixed-{index:02d}",
            hostile(embedded=embedded_file(chooser.choice((stored, empty)), decode_parms=parms)),
        )


#: What ``/Params /Size`` may say about a stream, truthfully or otherwise.
PARAMS_SIZES: Final = (
    "",
    "<< >>",
    f"<< /Size 0 /ModDate ({fx.FIXED_PDF_DATE}) >>",
    f"<< /Size -1 /ModDate ({fx.FIXED_PDF_DATE}) >>",
    f"<< /Size 2 /ModDate ({fx.FIXED_PDF_DATE}) >>",
    f"<< /Size 1099511627776 /ModDate ({fx.FIXED_PDF_DATE}) >>",
    f"<< /Size 2.5 /ModDate ({fx.FIXED_PDF_DATE}) >>",
    f"<< /Size /Large /ModDate ({fx.FIXED_PDF_DATE}) >>",
    f"<< /Size (1064) /ModDate ({fx.FIXED_PDF_DATE}) >>",
    "/Params",
    "[ << /Size 1064 >> ]",
)

#: What a stream's ``/Length`` may say about its stored bytes.
LENGTH_VALUES: Final = ("0", "1", "-1", "16", "1099511627776", "2.5", "/Long", "(1064)")


def _size_and_length_cases() -> Iterator[Case]:
    """Build documents whose declared sizes and lengths disagree with their contents.

    Yields:
        The cases. ``/Params /Size`` and ``/Length`` are both claims by whoever wrote the
        file: useful for an early refusal, worth nothing as reassurance, and each a place
        where trusting the claim or measuring it the expensive way has already gone wrong
        once.
    """
    stored = zlib.compress(PLANNOTATION_BYTES, 9)
    for index, params in enumerate(PARAMS_SIZES):
        yield Case(f"params-{index:02d}", hostile(embedded=embedded_file(stored, params=params)))
    for index, value in enumerate(LENGTH_VALUES):
        tidy = value.strip("()/").replace(".", "-")
        yield Case(
            f"length-{index:02d}-{tidy}", hostile(embedded=embedded_file(stored, length=value))
        )


def _deflate_cases() -> Iterator[Case]:
    """Build documents whose Flate streams are truncated, corrupt or absent.

    Yields:
        The cases. What neither zlib nor raw deflate reads is damage, and section 4.3 (2)
        makes damage absence rather than something to repair.
    """
    stored = zlib.compress(PLANNOTATION_BYTES, 9)
    raw = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    headerless = raw.compress(PLANNOTATION_BYTES) + raw.flush()
    flipped = bytearray(stored)
    flipped[len(flipped) // 2] ^= 0xFF
    variants = {
        "truncated-half": stored[: len(stored) // 2],
        "truncated-one": stored[:1],
        "truncated-none": b"",
        "corrupt": bytes(flipped),
        "headerless": headerless,
        "garbage": b"\xde\xad\xbe\xef" * 64,
        "zeros": bytes(512),
        "stored-blocks": zlib.compress(PLANNOTATION_BYTES, 0),
        "empty-inflate": zlib.compress(b"", 9),
    }
    for name, payload in variants.items():
        yield Case(f"deflate-{name}", hostile(embedded=embedded_file(payload)))


def _metadata_cases() -> Iterator[Case]:
    """Build documents whose catalog ``/Metadata`` is every shape but a sound one.

    Yields:
        The cases. The packet is read on every operation -- to find a declaration, to
        decide whether one can be added, to read ``pdfaid:part`` -- so it is as much
        attacker-supplied input as a label, and is the one stream a save is guaranteed to
        write out in full.
    """
    packet = rdf(b'<rdf:Description rdf:about=""/>')
    shapes: dict[str, bytes] = {
        "dictionary": b"<< /Type /Metadata /Subtype /XML >>",
        "array": b"[ 1 2 3 ]",
        "name": b"/Metadata",
        "number": b"42",
        "string": b"(not a stream)",
        "empty": pdf_stream("/Type /Metadata /Subtype /XML", b""),
        "not-utf8": pdf_stream("/Type /Metadata /Subtype /XML", bytes(range(128, 256))),
        "not-xml": pdf_stream("/Type /Metadata /Subtype /XML", b"this is not markup at all"),
        "plain": pdf_stream("/Type /Metadata /Subtype /XML", packet),
        "flate": pdf_stream(
            "/Type /Metadata /Subtype /XML /Filter /FlateDecode", zlib.compress(packet, 9)
        ),
        "runlength": pdf_stream(
            "/Type /Metadata /Subtype /XML /Filter /RunLengthDecode",
            fx.run_length_encode(packet),
        ),
        "double-flate": pdf_stream(
            "/Type /Metadata /Subtype /XML /Filter [/FlateDecode /FlateDecode]",
            zlib.compress(zlib.compress(packet, 9), 9),
        ),
        "predicted": pdf_stream(
            "/Type /Metadata /Subtype /XML /Filter /FlateDecode "
            "/DecodeParms << /Predictor 12 /Colors 1 /BitsPerComponent 8 /Columns 512 >>",
            _predicted(bytes(4 * 512)),
        ),
        "absurd-columns": pdf_stream(
            "/Type /Metadata /Subtype /XML /Filter /FlateDecode "
            "/DecodeParms << /Predictor 12 /Columns 2000000000 >>",
            zlib.compress(b"", 9),
        ),
        "bomb": pdf_stream(
            "/Type /Metadata /Subtype /XML /Filter /FlateDecode",
            fx.flate_bomb_bytes(4),
        ),
        "lying-length": pdf_stream("/Type /Metadata /Subtype /XML", packet, length="1099511627776"),
    }
    for name, body in shapes.items():
        yield Case(
            f"metadata-{name}",
            hostile(
                metadata=body,
                spec=filespec(_FOREIGN_NAME),
                names=_FOREIGN_TREE,
                page_af=None,
                catalog_af="[5 0 R]",
            ),
            owned=False,
        )


#: XMP packets a writer has to survive, and mostly refuse to alter.
def _packets() -> dict[str, bytes]:
    """Build the XMP packets the corpus carries.

    Returns:
        Each packet by name. Comments and CDATA are here because nothing inside either
        is markup, and a writer that scans bytes without masking them spliced its claim
        into one; the rest are shapes a real producer emits.
    """
    conforms = b"<pdfd:conformsTo>https://example.invalid/other/1.0</pdfd:conformsTo>"
    declarations = (
        b'<rdf:Description xmlns:pdfd="http://pdfa.org/declarations/" rdf:about="">'
        b"<pdfd:declarations><rdf:Bag>"
        b'<rdf:li rdf:parseType="Resource">' + conforms + b"</rdf:li>"
        b"</rdf:Bag></pdfd:declarations></rdf:Description>"
    )
    return {
        "plain": rdf(b'<rdf:Description rdf:about=""/>'),
        "declarations": rdf(declarations),
        "commented": fx.COMMENTED_DECLARATIONS_PACKET,
        "cdata": fx.CDATA_DECLARATIONS_PACKET,
        "trailing-comment": fx.TRAILING_COMMENT_PACKET,
        "unterminated-comment": rdf(b'<rdf:Description rdf:about=""/><!-- never closed'),
        "comment-in-bag": rdf(
            declarations.replace(b"<rdf:Bag>", b"<rdf:Bag><!-- </rdf:Bag> -->", 1)
        ),
        "self-closing-bag": fx.SELF_CLOSING_DECLARATIONS_PACKET,
        "named-subject": fx.NAMED_SUBJECT_PACKET,
        "two-subjects": rdf(
            b'<rdf:Description rdf:about="uuid:one"/><rdf:Description rdf:about="uuid:two"/>'
        ),
        "foreign-prefix": rdf(
            b'<x1:Description xmlns:x1="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
            b'x1:about=""><d1:declarations xmlns:d1="http://pdfa.org/declarations/">'
            b"<x1:Bag/></d1:declarations></x1:Description>"
        ),
        "bom": xmp(
            b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            b'<rdf:Description rdf:about=""/></rdf:RDF>',
            head=b'\xef\xbb\xbf<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>\n',
        ),
        "crlf": rdf(b'<rdf:Description rdf:about=""/>').replace(b"\n", b"\r\n"),
        "no-rdf": xmp(b"<x:nothing/>"),
        "unclosed-rdf": xmp(
            b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            b'<rdf:Description rdf:about=""/>'
        ),
        "declarations-not-a-bag": rdf(
            b'<rdf:Description xmlns:pdfd="http://pdfa.org/declarations/" rdf:about="">'
            b"<pdfd:declarations><rdf:Seq/></pdfd:declarations></rdf:Description>"
        ),
        "unclosed-bag": rdf(
            b'<rdf:Description xmlns:pdfd="http://pdfa.org/declarations/" rdf:about="">'
            b"<pdfd:declarations><rdf:Bag></pdfd:declarations></rdf:Description>"
        ),
        "nested-bags": rdf(
            declarations.replace(
                b"</rdf:li>",
                b"<pdfd:claimData><rdf:Bag><rdf:li>x</rdf:li></rdf:Bag></pdfd:claimData></rdf:li>",
                1,
            )
        ),
        "ours-already": rdf(
            declarations.replace(
                b"https://example.invalid/other/1.0", fx.FOREIGN_SPEC_URI.encode(), 1
            )
        ),
        "pdfa-claim": rdf(
            b'<rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/" '
            b'pdfaid:part="3" pdfaid:conformance="B"/>'
        ),
    }


def _packet_cases() -> Iterator[Case]:
    """Build a document per XMP packet, with no Plannotation file to refuse over.

    Yields:
        The cases. These carry a foreign attachment rather than one of Plannotation's, so
        that :func:`plannotation.pdf.embed.attach` reaches the splice rather than stopping
        at the conflict check -- which is where the packet actually matters.
    """
    for name, packet in _packets().items():
        yield Case(
            f"packet-{name}",
            hostile(
                metadata=pdf_stream("/Type /Metadata /Subtype /XML", packet),
                spec=filespec(_FOREIGN_NAME),
                names=_FOREIGN_TREE,
                page_af=None,
                catalog_af="[5 0 R]",
            ),
            owned=False,
        )


#: File specifications missing what the format requires, or holding the wrong type.
FILESPECS: Final = (
    ("no-ef", "<< /Type /Filespec /F (plannotation-p0000.json) /UF (plannotation-p0000.json) >>"),
    ("ef-not-a-dictionary", f"<< /Type /Filespec /UF ({_OWNED_NAME}) /EF [ 6 0 R ] >>"),
    ("ef-f-not-a-stream", f"<< /Type /Filespec /UF ({_OWNED_NAME}) /EF << /F << >> >> >>"),
    ("ef-f-a-number", f"<< /Type /Filespec /UF ({_OWNED_NAME}) /EF << /F 42 >> >>"),
    ("ef-empty", f"<< /Type /Filespec /UF ({_OWNED_NAME}) /EF << >> >>"),
    ("ef-uf-only", f"<< /Type /Filespec /UF ({_OWNED_NAME}) /EF << /UF 6 0 R >> >>"),
    ("no-uf", f"<< /Type /Filespec /F ({_OWNED_NAME}) /EF << /F 6 0 R >> >>"),
    ("no-f", f"<< /Type /Filespec /UF ({_OWNED_NAME}) /EF << /F 6 0 R >> >>"),
    ("no-names", "<< /Type /Filespec /EF << /F 6 0 R >> >>"),
    ("uf-a-number", f"<< /Type /Filespec /UF 7 /F ({_OWNED_NAME}) /EF << /F 6 0 R >> >>"),
    ("uf-a-name", f"<< /Type /Filespec /UF /{_OWNED_NAME} /EF << /F 6 0 R >> >>"),
    ("uf-an-array", f"<< /Type /Filespec /UF [({_OWNED_NAME})] /EF << /F 6 0 R >> >>"),
    (
        "f-ours-uf-foreign",
        f"<< /Type /Filespec /UF ({_FOREIGN_NAME}) /F ({_OWNED_NAME}) /EF << /F 6 0 R >> >>",
    ),
    ("no-type", f"<< /UF ({_OWNED_NAME}) /EF << /F 6 0 R >> >>"),
    ("wrong-type", f"<< /Type /Page /UF ({_OWNED_NAME}) /EF << /F 6 0 R >> >>"),
    ("an-array", f"[ ({_OWNED_NAME}) 6 0 R ]"),
    ("a-number", "42"),
    ("a-name", "/Filespec"),
    ("null", "null"),
    ("a-stream", None),
)


def _filespec_cases() -> Iterator[Case]:
    """Build a document per malformed file specification.

    Yields:
        The cases. A specification arrives from an untrusted document and may hold
        anything under ``/EF``; reaching for a stream's members on whatever is there
        raises out of the PDF library, which reaches a person as a traceback.
    """
    for name, body in FILESPECS:
        if body is None:
            yield Case(
                "spec-a-stream",
                hostile(spec=None, extra=(pdf_stream("/Type /Filespec", b"x"),)),
            )
            continue
        yield Case(f"spec-{name}", hostile(spec=body))


#: ``/AF`` entries a damaged or hostile document holds.
AF_SHAPES: Final = (
    "null",
    "[ null ]",
    "[ 5 0 R null ]",
    "[ 5 0 R 5 0 R ]",
    "[ 3 0 R ]",
    "[ 42 ]",
    "[ (plannotation-p0000.json) ]",
    "[ /plannotation-p0000.json ]",
    "[]",
    "5 0 R",
    "<< /F 5 0 R >>",
    "42",
    "/AF",
    "(5 0 R)",
    "[ 99 0 R ]",
)


def _associated_file_cases() -> Iterator[Case]:
    """Build a document per ``/AF`` shape, on the page and on the catalog.

    Yields:
        The cases. ``/AF`` is where a reader learns which page a label belongs to, and it
        is an array of references a document wrote: it may hold a deleted object, the
        same file twice, or something that is not a file specification at all.
    """
    for index, shape in enumerate(AF_SHAPES):
        yield Case(f"af-page-{index:02d}", hostile(page_af=shape))
        yield Case(f"af-catalog-{index:02d}", hostile(page_af=None, catalog_af=shape))


#: Name trees that are not the sorted, unique mapping the format describes.
TREES: Final = (
    ("duplicate-keys", name_tree((_OWNED_NAME, "5 0 R"), (_OWNED_NAME, "5 0 R"))),
    ("aliased", name_tree((_OWNED_NAME, "5 0 R"), (_FOREIGN_NAME, "5 0 R"))),
    ("aliased-reversed", name_tree((_FOREIGN_NAME, "5 0 R"), (_OWNED_NAME, "5 0 R"))),
    ("aliased-foreign", name_tree((_OWNED_NAME, "5 0 R"), (_FOREIGN_NAME, "5 0 R"))),
    ("aliased-foreign-index", name_tree((INDEX_FILENAME, "5 0 R"), (_FOREIGN_NAME, "5 0 R"))),
    ("index-too", name_tree((INDEX_FILENAME, "5 0 R"), (_OWNED_NAME, "5 0 R"))),
    ("newline", name_tree((_OWNED_NAME + r"\n", "5 0 R"))),
    ("trailing-space", name_tree((_OWNED_NAME + " ", "5 0 R"))),
    ("leading-space", name_tree((" " + _OWNED_NAME, "5 0 R"))),
    ("uppercase", name_tree(("PLANNOTATION-P0000.JSON", "5 0 R"))),
    ("look-alike", name_tree(("plan\\154abel-p0000.json", "5 0 R"))),
    ("cyrillic", name_tree(("\\320\\260lannotation-p0000.json", "5 0 R"))),
    ("five-digits", name_tree(("plannotation-p00000.json", "5 0 R"))),
    ("three-digits", name_tree(("plannotation-p000.json", "5 0 R"))),
    ("odd-length", "<< /EmbeddedFiles << /Names [(plannotation-p0000.json)] >> >>"),
    ("names-not-an-array", "<< /EmbeddedFiles << /Names 5 0 R >> >>"),
    ("key-not-a-string", "<< /EmbeddedFiles << /Names [/plannotation-p0000.json 5 0 R] >> >>"),
    ("value-null", "<< /EmbeddedFiles << /Names [(plannotation-p0000.json) null] >> >>"),
    ("embeddedfiles-not-a-dictionary", "<< /EmbeddedFiles [ 5 0 R ] >>"),
    ("names-not-a-dictionary", "[ 5 0 R ]"),
    ("empty-tree", "<< /EmbeddedFiles << /Names [] >> >>"),
    ("kids", "<< /EmbeddedFiles << /Kids [ 8 0 R ] >> >>"),
    ("kids-empty", "<< /EmbeddedFiles << /Kids [] >> >>"),
    ("limits-only", "<< /EmbeddedFiles << /Limits [(a) (z)] >> >>"),
)


def _name_tree_cases() -> Iterator[Case]:
    """Build a document per name-tree shape.

    Yields:
        The cases. The tree decides what :func:`plannotation.pdf.embed.strip` deletes, so a
        key that merely looks like one of Plannotation's is another producer's file about to
        be removed -- and a key that maps to a file specification some other key also maps
        to is another producer's file about to be destroyed.
    """
    kid = (
        "<< /Limits [(plannotation-p0000.json) (plannotation-p0000.json)] "
        f"/Names [({_OWNED_NAME}) 5 0 R] >>"
    ).encode()
    # The same trees again, each filing another producer's file -- one whose own /UF and
    # /F are foreign -- under the key in question. This is where a key that merely looks
    # like one of Plannotation's costs somebody their attachment, and it is the only shape
    # in which the difference between `$` and `\Z` is visible from outside.
    for name, tree in TREES:
        yield Case(
            f"tree-theirs-{name}",
            hostile(names=tree, spec=filespec(_FOREIGN_NAME), extra=(kid,)),
        )
    for name, tree in TREES:
        # The two "aliased-foreign" trees file another producer's file -- its own /UF and
        # /F are foreign -- under one of Plannotation's names as well. That file is not
        # Plannotation's to destroy, only to unregister, and destroying it is what took its
        # bytes away and left its other key dangling.
        spec = filespec(_FOREIGN_NAME) if "aliased-foreign" in name else None
        yield Case(f"tree-{name}", hostile(names=tree, spec=spec, extra=(kid,)))


#: Page trees that lie about themselves.
PAGE_TREES: Final = (
    ("zero-pages", "[]", "0"),
    ("count-zero", "[3 0 R]", "0"),
    ("count-enormous", "[3 0 R]", "1000000000"),
    ("count-astronomical", "[3 0 R]", "1099511627776"),
    ("count-negative", "[3 0 R]", "-1"),
    ("count-real", "[3 0 R]", "2.5"),
    ("count-a-name", "[3 0 R]", "/One"),
    ("kids-repeated", "[3 0 R 3 0 R]", "2"),
    ("kids-not-an-array", "3 0 R", "1"),
    ("kids-null", "[null]", "1"),
    ("kids-self", "[2 0 R]", "1"),
)


def _page_tree_cases() -> Iterator[Case]:
    """Build a document per page-tree shape.

    Yields:
        The cases. The page count decides how many labels a reader looks for and which
        page each one is attached to, and it is two numbers the document wrote that need
        not agree with each other or with anything else.
    """
    for name, kids, count in PAGE_TREES:
        yield Case(f"pages-{name}", hostile(pages=(kids, count)))


#: What a case built at the label cap decodes to, in bytes.
#:
#: Only the two Average and Paeth cases are built this large, and they are refused: the
#: tag bytes are counted before anything is allocated, so a document this reader will not
#: unpredict costs it an inflate and nothing more. That is what makes them the sharpest
#: probes in this corpus -- nearly free now, and 1.4 s and 2.6 s to a reader that undid
#: them one byte at a time.
_HEAVY_BYTES: Final = 15 * 1024 * 1024

#: What every other heavy case decodes to.
#:
#: Four mebibytes is enough that a per-byte Python loop over it is visible against the
#: time budget, and small enough that a dozen of them in one process do not turn the
#: memory assertion into a measurement of CPython's allocator.
_MODEST_BYTES: Final = 4 * 1024 * 1024

#: What a heavy case under a sequential predictor decodes to: just under the cap those
#: two filters carry, so that the work is done rather than refused.
_SEQUENTIAL_BYTES: Final = 1024 * 1024 - 512


def _heavy_cases() -> Iterator[Case]:
    """Build documents that are large once decoded and tiny on disk.

    Yields:
        The cases. Without these the corpus is all refusals and the time budget asserts
        nothing: every other document here is decoded in microseconds, so a reader that
        took a second a mebibyte would satisfy the budget on all of them.
    """
    for tag in (0, 1, 2):
        yield Case(
            f"heavy-png-{tag}",
            hostile(
                embedded=embedded_file(
                    _predicted(bytes(_MODEST_BYTES), tag=tag),
                    decode_parms=("<< /Predictor 15 /Colors 1 /BitsPerComponent 8 /Columns 512 >>"),
                )
            ),
        )
    for tag in (3, 4):
        # Two of each: one just under the cap those two filters carry, so the work is
        # really done, and one at the label cap, which is refused now -- and which costs
        # nothing to refuse, so it is the cheapest probe in this corpus and the sharpest.
        # A reader that undid it a byte at a time spent 1.4 s and 2.6 s on these two.
        for size, suffix in ((_SEQUENTIAL_BYTES // 512 * 512, ""), (_HEAVY_BYTES, "-large")):
            yield Case(
                f"heavy-png-{tag}{suffix}",
                hostile(
                    embedded=embedded_file(
                        _predicted(bytes(size), tag=tag),
                        decode_parms=(
                            "<< /Predictor 15 /Colors 1 /BitsPerComponent 8 /Columns 512 >>"
                        ),
                    )
                ),
            )
    yield Case(
        "heavy-tiff",
        hostile(
            embedded=embedded_file(
                zlib.compress(bytes(_MODEST_BYTES), 9),
                decode_parms="<< /Predictor 2 /Colors 1 /BitsPerComponent 8 /Columns 512 >>",
            )
        ),
    )
    yield Case(
        "heavy-flate", hostile(embedded=embedded_file(zlib.compress(bytes(_MODEST_BYTES), 9)))
    )
    yield Case(
        "heavy-bomb",
        hostile(embedded=embedded_file(fx.flate_bomb_bytes(fx.BOMB_MEGABYTES))),
    )
    yield Case(
        "heavy-bomb-chained",
        hostile(
            embedded=embedded_file(
                fx.flate_bomb_bytes(fx.BOMB_MEGABYTES),
                filters="[/ASCIIHexDecode /ASCII85Decode /FlateDecode]",
            )
        ),
    )
    packet = rdf(b'<rdf:Description rdf:about=""/><!--' + b"A" * (900 * 1024) + b"-->")
    yield Case(
        "heavy-packet",
        hostile(
            metadata=pdf_stream(
                "/Type /Metadata /Subtype /XML /Filter /FlateDecode", zlib.compress(packet, 9)
            ),
            spec=filespec(_FOREIGN_NAME),
            names=_FOREIGN_TREE,
            page_af=None,
            catalog_af="[5 0 R]",
        ),
        owned=False,
    )
    yield Case(
        "heavy-packet-bomb",
        hostile(
            metadata=pdf_stream(
                "/Type /Metadata /Subtype /XML /Filter /FlateDecode",
                fx.flate_bomb_bytes(fx.BOMB_MEGABYTES),
            ),
            spec=filespec(_FOREIGN_NAME),
            names=_FOREIGN_TREE,
            page_af=None,
            catalog_af="[5 0 R]",
        ),
        owned=False,
    )


#: A second, foreign attachment, as objects 8 and 9.
_FOREIGN_EXTRA: Final = (
    (
        f"<< /Type /Filespec /F ({_FOREIGN_NAME}) /UF ({_FOREIGN_NAME}) /Desc (theirs) "
        "/EF << /F 9 0 R /UF 9 0 R >> /AFRelationship /Source >>"
    ).encode(),
    pdf_stream("/Type /EmbeddedFile /Subtype /text#2Fplain", b"Another producer's bytes.\n"),
)


def _foreign_material_cases() -> Iterator[Case]:
    """Build documents carrying somebody else's file beside Plannotation's.

    Yields:
        The cases. Every promise :func:`plannotation.pdf.embed.strip` makes is about these
        documents: a file registered only in an ``/AF`` array, one registered only in the
        name tree, one registered in both, and one that shares its specification with a
        key of Plannotation's.
    """
    both = name_tree((_OWNED_NAME, "5 0 R"), (_FOREIGN_NAME, "8 0 R"))
    yield Case(
        "foreign-af-only",
        hostile(page_af="[5 0 R 8 0 R]", extra=_FOREIGN_EXTRA),
    )
    yield Case(
        "foreign-catalog-af-only",
        hostile(catalog_af="[8 0 R]", extra=_FOREIGN_EXTRA),
    )
    yield Case(
        "foreign-tree-only",
        hostile(names=both, extra=_FOREIGN_EXTRA),
    )
    yield Case(
        "foreign-both",
        hostile(names=both, page_af="[5 0 R 8 0 R]", catalog_af="[8 0 R]", extra=_FOREIGN_EXTRA),
    )
    yield Case(
        "foreign-af-twice",
        hostile(names=both, page_af="[8 0 R 5 0 R 8 0 R]", extra=_FOREIGN_EXTRA),
    )
    yield Case(
        "foreign-aliased",
        hostile(
            spec=filespec(_FOREIGN_NAME),
            names=name_tree((_OWNED_NAME, "5 0 R"), (_FOREIGN_NAME, "5 0 R")),
            page_af="[5 0 R]",
            catalog_af="[5 0 R]",
        ),
    )
    yield Case(
        "foreign-aliased-three-ways",
        hostile(
            spec=filespec(_FOREIGN_NAME),
            names=name_tree(
                (INDEX_FILENAME, "5 0 R"), (_OWNED_NAME, "5 0 R"), (_FOREIGN_NAME, "5 0 R")
            ),
            page_af="[5 0 R]",
        ),
    )


def all_cases() -> list[Case]:
    """Build the whole corpus, in a fixed order.

    Returns:
        Every generated document. Names are unique, which a test asserts, because a
        duplicate name would silently drop a case and make the corpus smaller than it
        reads.
    """
    generators: tuple[Callable[[], Iterator[Case]], ...] = (
        _filter_cases,
        _decode_parms_cases,
        _size_and_length_cases,
        _deflate_cases,
        _metadata_cases,
        _packet_cases,
        _filespec_cases,
        _associated_file_cases,
        _name_tree_cases,
        _page_tree_cases,
        _foreign_material_cases,
    )
    return [case for generator in generators for case in generator()]


def heavy_cases() -> list[Case]:
    """Build the documents that are legitimately large once decoded.

    Returns:
        The heavy corpus. It is kept apart from the malformed one so that each can be
        measured against a budget that means something: a document of a few kilobytes
        that allocates a hundred megabytes is a defect, and a label at the cap that
        allocates a hundred megabytes is a label at the cap.
    """
    return list(_heavy_cases())


# ---------------------------------------------------------------------------
# What one document costs, recorded once and asserted six times
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Attempt:
    """What one operation on one document did.

    Attributes:
        operation: Which function was called.
        error: What escaped, or None if nothing did.
        seconds: How long it took.
        size: The output file's size, or None where the operation wrote none. An
            operation that raised and left a file behind is a partial write.
        readable: Whether the PDF library can open the output, or None where there is
            none.
    """

    operation: str
    error: Exception | None
    seconds: float
    size: int | None = None
    readable: bool | None = None


@dataclass
class Probe:
    """Everything one hostile document was observed to do.

    Attributes:
        case: The document.
        size: Its size on disk, which bounds what an output may be.
        attempts: One entry per operation, in the order they ran.
        foreign_before: Attachments Plannotation does not own, before stripping, as
            ``key -> stored bytes``. None when the document could not be opened at all.
        foreign_after: The same, read back out of the stripped copy.
        associations_before: How many ``/AF`` entries name a file Plannotation does not own.
        associations_after: The same, afterwards.
        packet_before: The catalog's XMP packet, decoded, or None when there is none, it
            is too large to decode here, or the document will not open.
        packet_after: The same, afterwards.
        declaration_removed: What :func:`plannotation.pdf.embed.strip` reported removing, or
            None when it refused.
        peak_growth: How much this document raised the process's peak resident memory,
            in bytes.
        plannotated_packet: The XMP packet of the copy :func:`plannotation.pdf.embed.attach`
            wrote, or None where it wrote none or the packet could not be read here.
        round_tripped_packet: The packet of that copy after it has been stripped again.
    """

    case: Case
    size: int
    peak_growth: int = 0
    plannotated_packet: bytes | None = None
    round_tripped_packet: bytes | None = None
    attempts: list[Attempt] = field(default_factory=list)
    foreign_before: dict[str, bytes] | None = None
    foreign_after: dict[str, bytes] | None = None
    associations_before: int | None = None
    associations_after: int | None = None
    packet_before: bytes | None = None
    packet_after: bytes | None = None
    declaration_removed: bool | None = None

    def attempt(self, operation: str) -> Attempt:
        """Return one recorded operation.

        Args:
            operation: Which one.

        Returns:
            The attempt.

        Raises:
            KeyError: If the sweep did not run it, which is a bug in this module.
        """
        for found in self.attempts:
            if found.operation == operation:
                return found
        raise KeyError(operation)


@dataclass
class Sweep:
    """One corpus, run once.

    Attributes:
        name: Which corpus, for a failure message.
        memory_budget: How much more resident memory this corpus may reach than it
            started with. It differs between the two because what the two may cost
            differs: nothing in the malformed corpus has any business allocating, and a
            label at the cap legitimately does.
        probes: One per document.
        peak_before: Peak resident memory when the sweep started, in bytes.
        peak_after: Peak resident memory when it finished.
        seconds: How long the whole sweep took.
    """

    name: str
    memory_budget: int
    probes: list[Probe]
    peak_before: int
    peak_after: int
    seconds: float


def peak_rss() -> int:
    """Return this process's peak resident set size in bytes.

    Returns:
        The high-water mark ``getrusage`` reports. macOS gives it in bytes and every
        other POSIX in kilobytes, which is a difference that has silently turned a
        thousandfold error into a passing assertion before now.
    """
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024


def openable(path: Path) -> bool:
    """Report whether the PDF library will open a file and walk its pages.

    Args:
        path: The file.

    Returns:
        True when it opens. An output that does not is a corrupt output, whatever the
        operation that wrote it reported.
    """
    try:
        with pikepdf.open(path) as pdf:
            _ = len(pdf.pages)
    except pikepdf.PikepdfError:
        return False
    return True


def _stored_bytes(spec: pikepdf.Object) -> bytes:
    """Return an attachment's stored bytes, or a marker.

    Args:
        spec: A file specification.

    Returns:
        The bytes of its embedded stream, exactly as stored, or a marker when there is
        nothing readable there. The marker compares equal to itself, so a file that was
        unreadable before and after counts as unaltered.
    """
    try:
        return bytes(spec[pikepdf.Name.EF][pikepdf.Name.F].read_raw_bytes())
    except (pikepdf.PikepdfError, KeyError, TypeError, ValueError):
        return b"<no readable stream>"


def foreign_files(path: Path) -> dict[str, bytes] | None:
    """Inventory every attachment Plannotation does not own.

    Args:
        path: The document.

    Returns:
        Each such name-tree key mapped to its stored bytes, or None when the document
        cannot be opened or walked. The predicate is the module's own, so a file
        Plannotation does own is excluded here exactly as it is excluded from the promise.
    """
    try:
        with pikepdf.open(path) as pdf:
            found: dict[str, bytes] = {}
            for key in map(str, pdf.attachments):
                spec = pdf.attachments[key].obj
                if owns(key) or any(owns(name) for name in declared_names(spec)):
                    continue
                found[key] = _stored_bytes(spec)
            return found
    except (pikepdf.PikepdfError, KeyError, TypeError, ValueError):
        return None


def declared_names(spec: object) -> list[str]:
    """Return the filenames a file specification declares for itself.

    Args:
        spec: Whatever an ``/AF`` array or a name tree held.

    Returns:
        Its ``/UF`` and ``/F``, and nothing for anything that is not a dictionary.
    """
    if not isinstance(spec, pikepdf.Dictionary):
        return []
    return [
        str(value)
        for value in (spec.get(pikepdf.Name.UF), spec.get(pikepdf.Name.F))
        if value is not None
    ]


def is_foreign(spec: object, keys: Sequence[str]) -> bool:
    """Report whether an associated file is another producer's to keep.

    Args:
        spec: The file specification an ``/AF`` array references.
        keys: Every name-tree key that maps to this same object, which may be none.

    Returns:
        True when the file is not Plannotation's. It is Plannotation's when it says so itself
        -- either of its two filenames is one Plannotation owns -- or when the only names
        the document files it under are Plannotation's. A file registered under one of
        Plannotation's names *and* one of somebody else's is another producer's file that
        was also filed under our name: unregistering our key is Plannotation's to do and
        destroying the file is not. A file the name tree does not list at all has no
        name but its own.
    """
    if any(owns(name) for name in declared_names(spec)):
        return False
    return not keys or any(not owns(key) for key in keys)


def registrations(pdf: pikepdf.Pdf) -> dict[tuple[int, int], list[str]]:
    """Map each registered file specification to every key it is filed under.

    Args:
        pdf: An open document.

    Returns:
        Object identity to name-tree keys. Two keys mapping to one object is legal, and
        is the shape in which somebody else's file was destroyed along with ours.
    """
    found: dict[tuple[int, int], list[str]] = {}
    for key in map(str, pdf.attachments):
        spec = pdf.attachments[key].obj
        if isinstance(spec, pikepdf.Object):
            found.setdefault(spec.objgen, []).append(key)
    return found


def foreign_associations(path: Path) -> int | None:
    """Count the ``/AF`` entries that name a file Plannotation does not own.

    Args:
        path: The document.

    Returns:
        The count, or None when the document cannot be walked.
    """
    try:
        with pikepdf.open(path) as pdf:
            filed = registrations(pdf)
            owners = [pdf.Root, *(page.obj for page in pdf.pages)]
            total = 0
            for owner in owners:
                entries = owner.get(pikepdf.Name.AF)
                if not isinstance(entries, pikepdf.Array):
                    continue
                total += sum(
                    1
                    for item in entries
                    if isinstance(item, pikepdf.Dictionary)
                    and is_foreign(item, filed.get(item.objgen, []))
                )
            return total
    except (pikepdf.PikepdfError, KeyError, TypeError, ValueError):
        return None


#: The most a packet may be stored as, or decode to, before this harness gives up on it.
_PACKET_PROBE_LIMIT: Final = 64 * 1024


def decoded_packet(path: Path) -> bytes | None:
    """Return a document's XMP packet, decoded under this harness's own bound.

    The obvious implementation is ``stream.read_bytes()``, and it is the wrong one: the
    PDF library's decoder has no bound and the document chooses its ``/Filter`` and its
    ``/DecodeParms``. Measured, one packet in this very corpus -- a ``/Columns`` of two
    thousand million over a stream that inflates to nothing -- cost 743 MB to decode that
    way, which would have made the harness the thing it is testing and its memory budget
    a measurement of itself.

    Args:
        path: The document.

    Returns:
        The packet's bytes, or None when there is none, it is stored or decodes larger
        than :data:`_PACKET_PROBE_LIMIT`, or it is stored under anything but a plain
        single ``/FlateDecode``. Comparing the decoded form and not the stored one is the
        point: qpdf writes a ``/Type /Metadata`` stream uncompressed however it found it,
        so the stored bytes change even when the packet does not.
    """
    try:
        with pikepdf.open(path) as pdf:
            stream = pdf.Root.get(pikepdf.Name.Metadata)
            if not isinstance(stream, pikepdf.Stream):
                return None
            raw = bytes(stream.read_raw_bytes())
            entries = stream.stream_dict
            if len(raw) > _PACKET_PROBE_LIMIT or pikepdf.Name.DecodeParms in entries:
                return None
            written = entries.get(pikepdf.Name.Filter)
            if written is None:
                return raw
            if str(written) != "/FlateDecode":
                return None
            decompressed = zlib.decompressobj().decompress(raw, _PACKET_PROBE_LIMIT + 1)
    except (pikepdf.PikepdfError, KeyError, TypeError, ValueError, zlib.error):
        return None
    return None if len(decompressed) > _PACKET_PROBE_LIMIT else decompressed


def _timed(operation: str, action: Callable[[], object]) -> Attempt:
    """Run one read-only operation and record what it did.

    Args:
        operation: Which function is being called, for the message.
        action: A call of it.

    Returns:
        The attempt. Everything is caught, including what must not escape: recording it
        is how the invariant is asserted, rather than the test simply erroring out on the
        first document and saying nothing about the other three hundred.
    """
    started = time.perf_counter()
    error: Exception | None = None
    try:
        action()
    except Exception as exc:  # noqa: BLE001 - what escapes is exactly what is measured
        error = exc
    return Attempt(operation=operation, error=error, seconds=time.perf_counter() - started)


def _written(operation: str, action: Callable[[], object], output: Path) -> Attempt:
    """Run one writing operation and record what it did and what it left behind.

    Args:
        operation: Which function is being called.
        action: A call of it.
        output: Where it was told to write.

    Returns:
        The attempt, carrying the output's size and whether it opens.
    """
    attempt = _timed(operation, action)
    if not output.exists():
        return attempt
    size = output.stat().st_size
    return Attempt(
        operation=attempt.operation,
        error=attempt.error,
        seconds=attempt.seconds,
        size=size,
        readable=openable(output),
    )


def probe(case: Case, directory: Path) -> Probe:
    """Run every operation over one hostile document.

    Args:
        case: The document.
        directory: Somewhere to write it and its two outputs.

    Returns:
        Everything observed.
    """
    source = directory / f"{case.name}.pdf"
    source.write_bytes(case.data)
    before = peak_rss()
    found = Probe(case=case, size=len(case.data))
    found.attempts.append(_timed("read(strict=True)", lambda: embed.read(source)))
    found.attempts.append(_timed("read(strict=False)", lambda: embed.read(source, strict=False)))
    found.attempts.append(_timed("carrier_report()", lambda: embed.carrier_report(source)))

    attached = directory / f"{case.name}-attached.pdf"
    found.attempts.append(
        _written(
            "attach()",
            lambda: embed.attach(source, [PLANNOTATION], None, attached, mod_date=MOD_DATE),
            attached,
        )
    )
    if found.attempts[-1].error is None:
        found.plannotated_packet = decoded_packet(attached)
        returned = directory / f"{case.name}-round-tripped.pdf"
        found.attempts.append(
            _written("strip(attach())", lambda: embed.strip(attached, returned), returned)
        )
        if found.attempts[-1].error is None:
            found.round_tripped_packet = decoded_packet(returned)

    found.foreign_before = foreign_files(source)
    found.associations_before = foreign_associations(source)
    found.packet_before = decoded_packet(source)
    stripped = directory / f"{case.name}-stripped.pdf"
    reports: list[embed.StripReport] = []

    def run_strip() -> None:
        reports.append(embed.strip(source, stripped))

    found.attempts.append(_written("strip()", run_strip, stripped))
    if reports:
        found.declaration_removed = reports[0].declaration_removed
        found.foreign_after = foreign_files(stripped)
        found.associations_after = foreign_associations(stripped)
        found.packet_after = decoded_packet(stripped)
    found.peak_growth = peak_rss() - before
    return found


def run_sweep(directory: Path, cases: Sequence[Case], *, name: str, budget: int) -> Sweep:
    """Run a corpus once.

    Args:
        directory: Where the documents and their outputs are written.
        cases: The documents to run.
        name: Which corpus this is.
        budget: The memory budget it is held to.

    Returns:
        The sweep, with the memory and time it cost.
    """
    before = peak_rss()
    started = time.perf_counter()
    probes = [probe(case, directory) for case in cases]
    seconds = time.perf_counter() - started
    return Sweep(
        name=name,
        memory_budget=budget,
        probes=probes,
        peak_before=before,
        peak_after=peak_rss(),
        seconds=seconds,
    )


@pytest.fixture(scope="session")
def sweep(tmp_path_factory: pytest.TempPathFactory) -> Sweep:
    """Run the malformed corpus once for the whole session.

    Args:
        tmp_path_factory: pytest's session-scoped temporary directory factory.

    Returns:
        The sweep. Every invariant reads it, so the corpus is paid for once rather than
        once per assertion.
    """
    return run_sweep(
        tmp_path_factory.mktemp("hostile"),
        all_cases(),
        name="malformed",
        budget=SWEEP_MEMORY_BUDGET,
    )


@pytest.fixture(scope="session")
def heavy(tmp_path_factory: pytest.TempPathFactory) -> Sweep:
    """Run the heavy corpus once for the whole session.

    Args:
        tmp_path_factory: pytest's session-scoped temporary directory factory.

    Returns:
        The sweep over the documents that are large once decoded.
    """
    return run_sweep(
        tmp_path_factory.mktemp("hostile-heavy"),
        heavy_cases(),
        name="heavy",
        budget=HEAVY_MEMORY_BUDGET,
    )


@pytest.fixture(scope="session", params=["malformed", "heavy"])
def corpus(request: pytest.FixtureRequest) -> Sweep:
    """Hand each invariant both corpora in turn.

    Args:
        request: pytest's request object, carrying which corpus this round wants.

    Returns:
        That corpus's sweep. Every invariant holds for both; only the memory budget
        differs, and each sweep carries its own.
    """
    wanted = "sweep" if request.param == "malformed" else "heavy"
    return cast("Sweep", request.getfixturevalue(wanted))


# ---------------------------------------------------------------------------
# The invariants
# ---------------------------------------------------------------------------
def describe(probe_: Probe, attempt: Attempt) -> str:
    """Describe one failure well enough to reproduce it without this module.

    Args:
        probe_: The document.
        attempt: What it did.

    Returns:
        A message naming the case, the operation and the outcome.
    """
    outcome = "completed" if attempt.error is None else f"{type(attempt.error).__name__}"
    detail = "" if attempt.error is None else f": {str(attempt.error)[:160]}"
    return f"{probe_.case.name} ({probe_.size} bytes) -> {attempt.operation} {outcome}{detail}"


class TestTheCorpus:
    """The harness is only worth its assertions if the corpus is real."""

    def test_it_covers_the_parameter_space(self) -> None:
        """A count, so that a generator quietly yielding nothing is visible."""
        cases = all_cases()
        assert len(cases) > 300
        families = {case.name.split("-")[0] for case in cases}
        assert families == {
            "af",
            "deflate",
            "filter",
            "foreign",
            "length",
            "metadata",
            "packet",
            "pages",
            "params",
            "parms",
            "spec",
            "tree",
        }

    def test_every_case_has_its_own_name(self) -> None:
        """A duplicate name overwrites a document and shrinks the corpus in silence."""
        names = [case.name for case in (*all_cases(), *heavy_cases())]
        assert len(set(names)) == len(names)

    def test_the_heavy_corpus_really_is_heavy(self) -> None:
        """Small on disk and enormous once decoded, which is the only interesting shape."""
        cases = heavy_cases()
        assert len(cases) >= 10
        assert max(len(case.data) for case in cases) < 128 * 1024

    def test_it_is_the_same_corpus_twice(self) -> None:
        """Seeded, never clocked: a harness that varies cannot be bisected against."""
        first = {case.name: case.data for case in all_cases()}
        second = {case.name: case.data for case in all_cases()}
        assert first == second

    def test_every_document_is_small(self) -> None:
        """A hostile document is small; that is what makes it hostile."""
        largest = max(all_cases(), key=lambda case: len(case.data))
        assert len(largest.data) < 16 * 1024, largest.name

    def test_at_least_one_document_is_completely_sound(self) -> None:
        """A corpus of nothing but refusals would assert nothing about the happy path."""
        sound = hostile()
        assert embed.read_sidecar is not None
        assert sound.startswith(b"%PDF-")


class TestTheInvariants:
    """Six properties that must hold for every document the generators produce."""

    def test_i1_nothing_escapes_that_is_not_a_plannotation_error(self, corpus: Sweep) -> None:
        """One exception root, or a caller cannot write ``except PlannotationError``.

        A :class:`MemoryError` says the bound was applied after the allocation; a
        :class:`RecursionError` says a structure walked itself; a raw ``pikepdf.PdfError``
        says the package has leaked its dependency's exception hierarchy into its API,
        and reaches a person running the CLI as a traceback out of a C++ library.
        """
        offenders = [
            describe(found, attempt)
            for found in corpus.probes
            for attempt in found.attempts
            if attempt.error is not None and not isinstance(attempt.error, PlannotationError)
        ]
        assert offenders == [], (
            f"{len(offenders)} operation(s) raised something other than a PlannotationError:\n"
            + "\n".join(offenders[:20])
        )

    def test_i2_no_one_document_raises_peak_memory_past_the_budget(self, corpus: Sweep) -> None:
        """Measured, because a refusal after the allocation reads like one before it.

        Every assertion short of this one passes for a reader that decodes a bomb and
        then refuses it: the error message is the same, the return value is the same, and
        the two gibibytes are invisible to everything but the machine.
        """
        offenders = [
            f"{found.case.name} ({found.size} bytes) raised peak memory by "
            f"{found.peak_growth / 1024**2:.0f} MB"
            for found in corpus.probes
            if found.peak_growth > PEAK_MEMORY_BUDGET
        ]
        assert offenders == [], (
            f"{len(offenders)} document(s) in the {corpus.name} corpus cost more than the "
            f"{PEAK_MEMORY_BUDGET / 1024**2:.0f} MB budget:\n" + "\n".join(offenders[:20])
        )

    def test_i2_the_whole_sweep_stays_inside_its_budget(self, corpus: Sweep) -> None:
        """The backstop behind the per-document measure, which a high-water mark needs.

        ``getrusage`` reports a peak and never a current, so a document that allocates
        less than an earlier one already did raises the mark by nothing. This catches the
        total that the per-document measure can hide.
        """
        spent = corpus.peak_after - corpus.peak_before
        assert spent < corpus.memory_budget, (
            f"the {corpus.name} sweep over {len(corpus.probes)} documents, none larger "
            f"than 64 KiB on disk, raised peak resident memory by {spent / 1024**2:.0f} MB, "
            f"over the {corpus.memory_budget / 1024**2:.0f} MB budget; something in the "
            "corpus is choosing how much memory this reader uses"
        )

    def test_i3_no_operation_takes_longer_than_its_budget(self, corpus: Sweep) -> None:
        """A bound on bytes is not a bound on time, and the corpus is full of both.

        Skipped while a tracer is installed. Under ``coverage`` this measures the
        tracer rather than the reader -- every bytecode line costs a Python call --
        so the budget would be testing the measuring instrument. ``make check`` runs
        it untraced, which is where the number means something.
        """
        if sys.gettrace() is not None:
            pytest.skip("wall-clock under a tracer measures the tracer, not the reader")
        slow = [
            f"{describe(found, attempt)} [{attempt.seconds:.2f} s]"
            for found in corpus.probes
            for attempt in found.attempts
            if attempt.seconds > OPERATION_TIME_BUDGET
        ]
        assert slow == [], (
            f"{len(slow)} operation(s) took longer than {OPERATION_TIME_BUDGET} s:\n"
            + "\n".join(slow[:20])
        )

    def test_i4_lenient_reading_never_raises_about_a_label(self, corpus: Sweep) -> None:
        """Section 4.3 (2): an invalid label is absent, and absence is a return value.

        A document can still be damaged in ways that are not about a label at all -- a
        catalog that is not a dictionary, an ``/AF`` that is not an array -- and those are
        a :class:`~plannotation.errors.CarrierError` either way. What must never happen is an
        :class:`~plannotation.errors.InvalidPlannotationError` from a reader that was asked to be
        lenient.
        """
        offenders = [
            describe(found, found.attempt("read(strict=False)"))
            for found in corpus.probes
            if isinstance(found.attempt("read(strict=False)").error, InvalidPlannotationError)
        ]
        assert offenders == [], (
            f"{len(offenders)} lenient read(s) raised rather than reporting absence:\n"
            + "\n".join(offenders[:20])
        )

    @pytest.mark.parametrize("operation", ["attach()", "strip()"])
    def test_i5_a_refusal_leaves_no_output_at_all(self, corpus: Sweep, operation: str) -> None:
        """Half a labelled document is worse than none, and harder to notice."""
        offenders = [
            f"{describe(found, found.attempt(operation))} and left {found.attempt(operation).size}"
            f" bytes behind"
            for found in corpus.probes
            if found.attempt(operation).error is not None
            and found.attempt(operation).size is not None
        ]
        assert offenders == [], f"{len(offenders)} refusal(s) wrote a file anyway:\n" + "\n".join(
            offenders[:20]
        )

    @pytest.mark.parametrize("operation", ["attach()", "strip()"])
    def test_i5_a_completed_write_is_readable(self, corpus: Sweep, operation: str) -> None:
        """An output the PDF library will not open is a corrupt output."""
        offenders = [
            describe(found, found.attempt(operation))
            for found in corpus.probes
            if found.attempt(operation).error is None and not found.attempt(operation).readable
        ]
        assert offenders == [], (
            f"{len(offenders)} completed write(s) produced a file that will not open:\n"
            + "\n".join(offenders[:20])
        )

    @pytest.mark.parametrize(("operation", "payload"), [("attach()", 4096), ("strip()", 0)])
    def test_i5_an_output_is_its_input_plus_what_was_written(
        self, corpus: Sweep, operation: str, payload: int
    ) -> None:
        """The bound on reading has to be a bound on writing too.

        Stripping a 66 KB document whose packet this reader declined to decompress used
        to produce a 67 MB one, because qpdf writes a ``/Type /Metadata`` stream
        uncompressed whatever it found. The output is the only place that shows.
        """
        offenders = [
            f"{found.case.name}: {found.size} bytes in, "
            f"{found.attempt(operation).size} bytes out of {operation}"
            for found in corpus.probes
            if (size := found.attempt(operation).size) is not None
            and size > found.size + payload + OUTPUT_GROWTH_ALLOWANCE
        ]
        assert offenders == [], (
            f"{len(offenders)} output(s) grew past what was written into them:\n"
            + "\n".join(offenders[:20])
        )

    def test_i6_strip_keeps_every_foreign_attachment_and_its_bytes(self, corpus: Sweep) -> None:
        """A file Plannotation does not own is not Plannotation's to remove or to damage.

        One ``/Filespec`` filed under two name-tree keys, one of them Plannotation's, had its
        stream destroyed with the key -- so another producer's bytes went with it.
        """
        offenders: list[str] = []
        for found in corpus.probes:
            before, after = found.foreign_before, found.foreign_after
            if before is None or after is None:
                continue
            lost = {key: value for key, value in before.items() if after.get(key) != value}
            if lost:
                offenders.append(
                    f"{found.case.name}: strip lost or altered {sorted(lost)} "
                    f"(before {sorted(before)}, after {sorted(after)})"
                )
        assert offenders == [], (
            f"{len(offenders)} document(s) lost foreign material to strip:\n"
            + "\n".join(offenders[:20])
        )

    def test_i6_strip_keeps_every_foreign_association(self, corpus: Sweep) -> None:
        """An ``/AF`` entry naming somebody else's file is somebody else's entry."""
        offenders = [
            f"{found.case.name}: {found.associations_before} foreign /AF entr(ies) before, "
            f"{found.associations_after} after"
            for found in corpus.probes
            if found.associations_before is not None
            and found.associations_after is not None
            and found.associations_after < found.associations_before
        ]
        assert offenders == [], (
            f"{len(offenders)} document(s) lost a foreign /AF entry:\n" + "\n".join(offenders[:20])
        )

    def test_i6_a_packet_with_nothing_of_ours_in_it_comes_back_unchanged(
        self, corpus: Sweep
    ) -> None:
        """Section 1.2 (2): another producer's metadata is returned byte for byte."""
        offenders = [
            found.case.name
            for found in corpus.probes
            if found.declaration_removed is False
            and found.packet_before is not None
            and found.packet_after != found.packet_before
        ]
        assert offenders == [], (
            f"{len(offenders)} document(s) had their XMP packet rewritten by a strip that "
            f"removed nothing:\n" + "\n".join(offenders[:20])
        )

    def test_the_sweep_is_fast_enough_for_every_run_of_make_check(self, corpus: Sweep) -> None:
        """A harness that is skipped is a harness that does not exist."""
        assert corpus.seconds < SWEEP_TIME_BUDGET, (
            f"the {corpus.name} sweep over {len(corpus.probes)} documents took "
            f"{corpus.seconds:.1f} s"
        )

    def test_the_sweep_actually_exercised_both_outcomes(self, sweep: Sweep) -> None:
        """A corpus every operation refuses asserts nothing about completing one.

        Without this, a reader that raised on everything would satisfy five of the seven
        invariants and the other two vacuously.
        """
        primary = {
            "read(strict=True)",
            "read(strict=False)",
            "carrier_report()",
            "attach()",
            "strip()",
        }
        completed = {
            attempt.operation
            for found in sweep.probes
            for attempt in found.attempts
            if attempt.error is None
        }
        refused = {
            attempt.operation
            for found in sweep.probes
            for attempt in found.attempts
            if attempt.error is not None
        }
        assert primary <= completed, f"never completed: {sorted(primary - completed)}"
        assert primary <= refused, f"never refused: {sorted(primary - refused)}"
        assert "strip(attach())" in completed


class TestALabelledDocumentSaysSo:
    """Invariant VII, which the six above do not reach.

    Reverting the defect that spliced Plannotation's claim into another producer's XML
    comment broke none of the first six invariants: the write completed, nothing grew,
    nothing foreign was lost, and the document announced a conformance no XMP reader
    would ever see. An invariant that only watches for exceptions and bytes cannot see a
    declaration written where nothing reads.
    """

    def test_a_completed_attach_leaves_a_declaration_a_reader_can_find(self, sweep: Sweep) -> None:
        """Section 6.2.1 (4) makes the declaration part of labelling, not a nicety.

        Found in the packet with comments and CDATA masked out, and before the last
        ``</rdf:RDF>``, because those are the two ways a claim can be in the bytes and in
        nobody's reading of them.
        """
        offenders: list[str] = []
        for found in sweep.probes:
            if found.attempt("attach()").error is not None or found.plannotated_packet is None:
                continue
            masked = visible(found.plannotated_packet)
            at = masked.find(SPEC_URI.encode())
            closed = masked.rfind(b"</rdf:RDF>")
            if at < 0:
                offenders.append(
                    f"{found.case.name}: the claim is in no readable part of the packet"
                )
            elif closed >= 0 and at > closed:
                offenders.append(f"{found.case.name}: the claim is after the end of the RDF")
        assert offenders == [], (
            f"{len(offenders)} labelled document(s) declare nothing a reader will find:\n"
            + "\n".join(offenders[:20])
        )

    def test_stripping_a_document_this_module_labelled_restores_its_packet(
        self, sweep: Sweep
    ) -> None:
        """What was spliced in must come out again, whatever it was spliced into.

        Section 1.2 (2): another producer's metadata comes back byte for byte. This is the
        assertion that makes the one above safe to satisfy -- a writer could always put
        its claim somewhere visible by rewriting the packet, and rewriting the packet is
        the thing it may not do.
        """
        offenders = [
            found.case.name
            for found in sweep.probes
            if found.round_tripped_packet is not None
            and found.packet_before is not None
            and found.round_tripped_packet != found.packet_before
        ]
        assert offenders == [], (
            f"{len(offenders)} document(s) did not come back as they went in:\n"
            + "\n".join(offenders[:20])
        )

    def test_the_corpus_reaches_this_at_all(self, sweep: Sweep) -> None:
        """An invariant nothing satisfies is an invariant nothing tests."""
        labelled = [found for found in sweep.probes if found.plannotated_packet is not None]
        assert len(labelled) >= 10
        assert sum(1 for found in labelled if found.round_tripped_packet is not None) >= 10
