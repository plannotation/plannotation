# SPDX-License-Identifier: Apache-2.0
"""Write and read Plannotation data in a PDF, without changing how the PDF looks.

This module is the project's central claim in code: a plannotation can be attached
to a drawing, and taken off again, without the drawing changing. Everything here is
arranged around that.

What a plannotated document carries
-----------------------------------
For each plannotated page, one embedded file named ``plannotation-pNNNN.json``
(:func:`plannotation.constants.plannotation_filename`) holding
:func:`plannotation.model.canonical_bytes` of the page's plannotation, with ``/Subtype``
``application/json``, ``/AFRelationship /Data`` and a ``/Desc``. The file is listed
in that page's ``/AF`` array **and** in the document's ``EmbeddedFiles`` name tree.
One further embedded file, ``plannotation-index.json``, holds the document-level index
and is listed in the catalog's ``/AF`` array and in the same name tree. The catalog's
XMP metadata carries one PDF Declaration whose ``pdfd:conformsTo`` is
:data:`plannotation.constants.SPEC_URI` -- as one more member of the ``pdfd:declarations``
array another producer had already written, where there is one, because a subject holds
one property of a given name and no more.

Both registrations are required and neither is automatic. A file reachable only from
``/AF`` is invisible to every reader that walks the name tree -- pdf.js's
``getAttachments()``, pikepdf's own ``attachments`` mapping, Acrobat's attachment
pane -- and a file reachable only from the name tree is associated with nothing,
which PDF/A-4 forbids. Registering in both shares one indirect object, so the file is
stored once.

What is guaranteed
------------------
:func:`attach` and :func:`strip` never touch a page's content streams, its
annotations, its boxes, its ``/Rotate``, any attachment they did not write, any
``/AF`` entry they did not write, or any metadata beyond splicing in and out their
own declaration. Rendering any page of the output is bit-identical to rendering the
same page of the input (:mod:`plannotation.pdf.render` is where that is asserted).

The file's bytes necessarily change. qpdf rewrites a document in full, so object
numbers are renumbered and the ``/ID`` changes; a plannotated PDF is never a byte-wise
superset of its input. The guarantee is about the page, not about the file.

What this reader will decode, and what it will not
--------------------------------------------------
An embedded plannotation and the catalog's XMP packet arrive from the same untrusted
file, so both are read under a bound -- :data:`_MAX_PLANNOTATION_BYTES` and
:data:`_MAX_XMP_BYTES` -- and both are decoded here rather than by the PDF library: a
single ``/FlateDecode``, inflated incrementally, with its ``/DecodeParms`` predictor
applied to output that is already inside the bound. Any other filter chain is refused,
by name. Section 4.3 (7) of the specification requires a reader to bound what it
spends, and a bound applied after the library has decoded the stream is not a bound at
all, because the document chooses the filter. A plannotation this reader will not
decode is a plannotation that is absent (4.3 (2)).

The predictor parameters are part of that input and are checked before they are used:
``/Predictor``, ``/Colors``, ``/BitsPerComponent`` and ``/Columns`` must each be an
integer in the range ISO 32000-2 gives it, and the row length the three of them imply
must fit inside the same bound -- because they are multiplied together and the product
is allocated. Three of the five PNG filters and the TIFF one are undone with numpy;
Average and Paeth cannot be, since each reconstructed byte is an input to the next, so
they carry their own smaller cap (:data:`_MAX_SEQUENTIAL_PREDICTED_BYTES`). A cap on
bytes alone would bound the memory and leave the time unbounded.

The bound is on writing as well as on reading, which is not the same thing. Saving
rewrites every stream in a document, and qpdf writes a ``/Type /Metadata`` stream
uncompressed whatever it found there, so saving a document whose packet this reader
declined to decompress is the one operation that spends exactly what declining had
saved. Both operations that save -- :func:`attach` and :func:`strip` -- therefore refuse
a packet they cannot read, before they have removed or written anything. A tool that
promises not to alter what it did not write cannot keep that promise over a packet it
cannot see.

Determinism
-----------
Nothing here reads the clock. :func:`attach` takes ``mod_date`` and writes it into
every embedded file's ``/Params /ModDate``; the ``/ID`` is computed from the content.
Two runs over the same inputs produce byte-identical output.

Signed documents
----------------
pikepdf and qpdf cannot append an incremental update -- they rewrite the whole file
-- so a signature over the original bytes cannot survive saving. :func:`attach`
detects signatures and refuses, naming the two real options: write a sidecar with
:func:`write_sidecar`, which never touches the document, or pass
``break_signature=True`` and accept that the signature is void.
"""

from __future__ import annotations

import json
import logging
import re
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cache
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, TypeVar, cast

import numpy as np
import pikepdf
from jsonschema import Draft202012Validator
from pikepdf import Array, Dictionary, Name, Object, Pdf

from plannotation.constants import (
    INDEX_FILENAME,
    PLANNOTATION_MIME_TYPE,
    SCHEMA_VERSION,
    SIDECAR_SUFFIX,
    SPEC_URI,
    plannotation_filename,
)
from plannotation.errors import (
    AttachmentConflictError,
    CarrierError,
    DeclarationError,
    EncryptedPdfError,
    InvalidPlannotationError,
    PlannotationMismatchError,
    PlannotationNotFoundError,
    SignedPdfError,
)
from plannotation.model import (
    ConformanceLevel,
    Generator,
    IndexPage,
    Model,
    Plannotation,
    PlannotationIndex,
    Sidecar,
    aggregate_provenance,
    canonical_bytes,
    canonical_json,
    conformance_level,
    index_schema,
    load_plannotation,
    load_plannotation_index,
    load_sidecar,
    page_schema,
    sidecar_schema,
)
from plannotation.units import MM_PER_PT

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence
    from datetime import datetime

    from numpy.typing import NDArray
    from pydantic import BaseModel

__all__ = [
    "PAGE_DIMENSION_TOLERANCE_MM",
    "AttachReport",
    "CarrierReport",
    "PageGeometry",
    "PlannotationSet",
    "SignatureReport",
    "StripReport",
    "add_declaration",
    "attach",
    "attach_in_place",
    "build_index",
    "build_sidecar",
    "carrier_report",
    "check_plannotations_against",
    "has_declaration",
    "is_plannotation_filename",
    "page_geometry",
    "pdf_date",
    "read",
    "read_pdf",
    "read_sidecar",
    "remove_declaration",
    "save_plannotated",
    "sidecar_path",
    "signature_report",
    "strip",
    "strip_in_place",
    "write_sidecar",
]

_LOGGER: Final = logging.getLogger(__name__)

#: The three document kinds this module loads, each with its own schema and model.
_ModelT = TypeVar("_ModelT", bound="BaseModel")

#: XMP namespace of a PDF Declaration. Scheme ``http``, with the trailing slash, as
#: the PDF Association's specification gives it in section 7.1 and as its own PDF/A
#: extension schema repeats. The registry page's informative annex spells it with
#: ``https``; the specification is normative and this is a string that is matched,
#: never normalised, so it is written exactly once, here.
NS_PDFD: Final = "http://pdfa.org/declarations/"

#: The RDF namespace, for the same reason.
NS_RDF: Final = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"

#: The XMP wrapper namespace.
NS_X: Final = "adobe:ns:meta/"

#: The PDF/A identification namespace, which is where ``pdfaid:part`` lives.
NS_PDFAID: Final = "http://www.aiim.org/pdfa/ns/id/"

#: The PDF/A extension-schema namespace. Parts 1, 2 and 3 of ISO 19005 require every
#: XMP property from a namespace they do not themselves define to be described by an
#: extension schema in the same packet; part 4 dropped extension schemas entirely.
NS_PDFA_EXTENSION: Final = "http://www.aiim.org/pdfa/ns/extension/"

#: The parts of PDF/A whose conformance a bare PDF Declaration puts at risk.
_PDFA_EXTENSION_PARTS: Final = frozenset({1, 2, 3})

#: How far a plannotation's page dimensions may differ from the page it is attached to
#: before :func:`attach` refuses, in millimetres. The specification's geometric
#: tolerance (section 4.4) is the same 0.5 mm.
PAGE_DIMENSION_TOLERANCE_MM: Final = 0.5

#: Filenames Plannotation owns inside a document, and the only ones :func:`strip`
#: removes.
#:
#: Anchored with ``\A`` and ``\Z`` and never with ``^`` and ``$``. In Python's dialect
#: ``$`` also matches immediately *before* a trailing newline, so ``^...$`` accepted
#: ``"plannotation-p0000.json\n"`` -- a name a document is free to write and which is not
#: one of Plannotation's. The consequences ran both ways: :func:`attach` refused to
#: plannotate a document over a file it did not own, and :func:`strip` deleted that file.
#: Section 3 of the specification already notes this difference between Python ``re``
#: and the Rust engine pydantic validates with, which has no such rule; ``\Z`` means the
#: same thing in both.
#:
#:
#: The digits are the ones :func:`~plannotation.constants.plannotation_filename` writes
#: (SPEC 6.2.2): exactly four, or five and more without a leading zero, so page 12345
#: is ``plannotation-p12345.json`` and ``plannotation-p00000.json`` is nobody's page.
#: ASCII only, because ``\d`` would also admit other scripts' digits.
_PLANNOTATION_FILENAME: Final = re.compile(r"\Aplannotation-p(?:[0-9]{4}|[1-9][0-9]{4,})\.json\Z")

#: An upper bound on the form-field nodes the signature walk will visit. The field
#: tree comes from an untrusted document and may be cyclic or enormous; section 9 of
#: the specification requires a reader to bound what it spends.
_MAX_FIELD_NODES: Final = 10_000

#: An upper bound on the bytes one embedded plannotation may decompress to, for the
#: same reason. Section 4.3 (7) and section 9 are normative: a reader must bound what
#: it spends parsing, and must treat a plannotation it will not accept as absent.
#:
#: Sixteen mebibytes is deliberately generous. A plannotation is a few kilobytes -- the
#: largest fixture in this repository is about one -- and a document's whole index is
#: smaller still, so nothing legitimate comes close. Every reading function takes
#: ``max_plannotation_bytes`` so that a caller with a genuinely enormous plannotation is
#: inconvenienced rather than stopped.
_MAX_PLANNOTATION_BYTES: Final = 16 * 1024 * 1024

#: An upper bound on the bytes the catalog's XMP packet may decompress to.
#:
#: The packet is the same attacker-controlled input as a plannotation -- it arrives in
#: the same file, from the same producer -- and it is read on every operation: to
#: decide whether a declaration is already there, whether one could be spliced in, and
#: what part of PDF/A the document claims. Bounding the plannotation and not the packet
#: bounds nothing, because a document carrying a 522 KB metadata stream that inflates
#: to half a gibibyte is as easy to write as one carrying a plannotation that does.
#:
#: One mebibyte is generous by two orders of magnitude and deliberately far below
#: :data:`_MAX_PLANNOTATION_BYTES`: a real packet is a few kilobytes, and the largest thing
#: legitimately found in one -- a PDF/A extension schema describing every property of
#: several namespaces -- is tens of kilobytes.
_MAX_XMP_BYTES: Final = 1024 * 1024

#: How many schema violations a message lists before it says "and N more".
_MAX_REPORTED_ERRORS: Final = 5

#: Longest document-derived fragment any error message will quote. A refusal is
#: what "treat as absent" invites a caller to log, so an unbounded message is an
#: unbounded cost in the one path taken when the document is already hostile.
_MAX_QUOTED_CHARS: Final = 200

#: A PDF rectangle is two diagonally opposite corners: four numbers.
_RECTANGLE_LENGTH: Final = 4

_XPACKET_HEAD: Final = b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
_XPACKET_TAIL: Final = b'\n<?xpacket end="w"?>\n'

#: Plannotation's claim, as one member of a ``pdfd:declarations`` array.
#:
#: It is a constant, and deliberately minimal: one ``pdfd:conformsTo`` and no optional
#: ``pdfd:claimData``. Two reasons. Because the member never varies,
#: :func:`remove_declaration` can take exactly these bytes back out and leave a packet
#: byte-identical to the one Plannotation found -- which is the only way to keep the
#: promise not to alter another producer's metadata. And because a ``claimBy`` carrying
#: a version number would mean a file written by one release of Plannotation could not be
#: stripped cleanly by another.
#:
#: It declares the PDF Declarations namespace on itself. When this member is spliced
#: into a ``rdf:Bag`` that another producer wrote, there is no knowing which prefix that
#: producer bound the namespace to -- ``pdfd`` is only the preferred prefix, not a
#: required one -- and an unbound prefix would make the whole packet ill-formed XML.
_DECLARATION_LI: Final = (
    b'<rdf:li rdf:parseType="Resource" xmlns:pdfd="'
    + NS_PDFD.encode()
    + b'">'
    + b"<pdfd:conformsTo>"
    + SPEC_URI.encode()
    + b"</pdfd:conformsTo>"
    + b"</rdf:li>"
)

#: The same claim, for the one case where Plannotation expands an empty array.
#:
#: A ``pdfd:declarations`` array written self-closing -- ``<rdf:Bag/>`` -- exists but has
#: no closing tag to splice a member before, so Plannotation replaces the element with its
#: expanded form and writes this member inside it. :func:`remove_declaration` has to put
#: the self-closing spelling back, and here is the difficulty: expanding ``<rdf:Bag/>``
#: and adding a member to ``<rdf:Bag></rdf:Bag>`` produce the same bytes, so a packet
#: carrying the result holds no evidence of which it was. A distinguishing mark is
#: therefore not a nicety but the only way both shapes can be restored byte for byte.
#:
#: The mark is the order of two attributes, which XML does not consider significant and
#: XMP therefore does not either. Both spellings are the same claim to every reader;
#: this one additionally means "the Bag around me was self-closing before Plannotation
#: touched it", and :func:`remove_declaration` collapses the Bag back when it sees it.
_SOLE_DECLARATION_LI: Final = (
    b'<rdf:li xmlns:pdfd="'
    + NS_PDFD.encode()
    + b'" rdf:parseType="Resource">'
    + b"<pdfd:conformsTo>"
    + SPEC_URI.encode()
    + b"</pdfd:conformsTo>"
    + b"</rdf:li>"
)

#: The whole ``pdfd:declarations`` property, for a packet that carries none, with the
#: subject left to be filled in by :func:`_declaration_block`.
#:
#: A subject may hold one property of a given name and no more, so this is written only
#: when there is no existing ``pdfd:declarations`` to join: two such properties on one
#: ``rdf:about`` subject is not valid XMP, and a reader that took the first Bag would
#: never see the second claim.
#:
#: The namespace declaration is written before ``rdf:about`` because that is the order
#: lxml emits, so a later re-serialisation by some other tool leaves these bytes alone.
#: It contains :data:`_DECLARATION_LI` verbatim, which is why
#: :func:`remove_declaration` looks for this block first and the member afterwards.
_DECLARATION_BLOCK_HEAD: Final = (
    b'<rdf:Description xmlns:pdfd="' + NS_PDFD.encode() + b'" rdf:about='
)

#: The rest of that property, after the subject.
_DECLARATION_BLOCK_TAIL: Final = (
    b"><pdfd:declarations><rdf:Bag>"
    + _DECLARATION_LI
    + b"</rdf:Bag></pdfd:declarations></rdf:Description>"
)

#: The packet :func:`add_declaration` synthesises when a document has no XMP at all,
#: with the declaration removed again. A packet equal to this is one Plannotation
#: created and nothing else has touched, so :func:`strip` deletes it rather than
#: leaving an empty packet behind as a trace.
_EMPTY_PACKET: Final = (
    _XPACKET_HEAD
    + b'<x:xmpmeta xmlns:x="'
    + NS_X.encode()
    + b'">'
    + b'<rdf:RDF xmlns:rdf="'
    + NS_RDF.encode()
    + b'">'
    + b"</rdf:RDF></x:xmpmeta>"
    + _XPACKET_TAIL
)

#: Recognises a Plannotation declaration in a packet whatever prefix it was written
#: with and however it is spaced. Used only for reading: nothing is parsed as XML,
#: because the packet is attacker-controlled and an XML parser is a large thing to
#: point at attacker-controlled input for a yes-or-no answer.
_CONFORMS_TO = re.compile(
    rb"<([A-Za-z_][\w.\-]*:)?conformsTo\s*>\s*" + re.escape(SPEC_URI.encode()) + rb"\s*</",
)

#: Recognises the PDF/A part a packet claims, written either as an attribute or as an
#: element. Both spellings are legal XMP and both occur in the wild.
_PDFA_PART = re.compile(
    rb"pdfaid:part\s*=\s*[\"'](\d+)[\"']|<[A-Za-z_][\w.\-]*:part\s*>\s*(\d+)\s*</",
)

#: Recognises an extension schema that describes the PDF Declarations namespace, which
#: is what a PDF/A-1, -2 or -3 file needs before it may carry ``pdfd:declarations``.
_PDFA_SCHEMA_FOR_PDFD = re.compile(
    rb"<([A-Za-z_][\w.\-]*:)?namespaceURI\s*>\s*" + re.escape(NS_PDFD.encode()) + rb"\s*</",
)


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------
def is_plannotation_filename(name: str) -> bool:
    """Report whether an embedded-file name is one Plannotation owns.

    Args:
        name: The name of an embedded file, as the name tree or a file spec's ``/UF``
            gives it.

    Returns:
        True for the document index and for the zero-padded name of a page's
        plannotation. A file Plannotation did not write is never removed, so this
        predicate is what keeps :func:`strip` honest.

    Examples:
        >>> is_plannotation_filename("plannotation-p0007.json")
        True
        >>> is_plannotation_filename("factur-x.xml")
        False
    """
    return name == INDEX_FILENAME or bool(_PLANNOTATION_FILENAME.match(name))


def _spec_names(spec: Object) -> list[str]:
    """Return every filename a file specification declares, preferred first.

    Args:
        spec: A ``/Filespec`` dictionary.

    Returns:
        Its ``/UF`` and then its ``/F``, omitting whichever it does not carry. Both are
        read, and separately: the format permits them to differ, and a file whose ``/F``
        is innocuous while its ``/UF`` is one of Plannotation's names is exactly the shape a
        conflict check must not miss. ``/UF`` comes first because PDF 2.0 requires it
        and deprecates ``/F``.

        A specification that is not a dictionary declares no names at all. An ``/AF``
        array in a damaged document can hold anything, and asking a number for its
        ``/UF`` raises out of the PDF library rather than returning an answer.
    """
    if not isinstance(spec, Dictionary):
        return []
    names: list[str] = []
    for key in (Name.UF, Name.F):
        value = spec.get(key)
        if value is not None:
            names.append(str(value))
    return names


def pdf_date(moment: datetime) -> str:
    """Format a moment as a PDF date string.

    Args:
        moment: A timezone-aware datetime. Naive datetimes are refused rather than
            guessed at, since a wrong offset is silently wrong.

    Returns:
        The moment as ``D:YYYYMMDDHHmmSSZ`` at UTC, or ``D:YYYYMMDDHHmmSS+HH'mm'``
        at any other offset, which is the syntax of ISO 32000-2 table 4.

    Raises:
        ValueError: If ``moment`` carries no timezone.

    Examples:
        >>> from datetime import UTC, datetime
        >>> pdf_date(datetime(2026, 4, 9, 17, 26, 5, tzinfo=UTC))
        'D:20260409172605Z'
    """
    offset = moment.utcoffset()
    if offset is None:
        msg = (
            "mod_date must be timezone-aware so that the same input always produces "
            "the same output; pass datetime(..., tzinfo=UTC) or an aware local time"
        )
        raise ValueError(msg)
    stamp = moment.strftime("D:%Y%m%d%H%M%S")
    seconds = int(offset.total_seconds())
    if seconds == 0:
        return f"{stamp}Z"
    sign = "+" if seconds > 0 else "-"
    hours, remainder = divmod(abs(seconds), 3600)
    return f"{stamp}{sign}{hours:02d}'{remainder // 60:02d}'"


def _as_path(value: Path | str) -> Path:
    """Return a filesystem path.

    Args:
        value: A path or a string holding one.

    Returns:
        ``value`` as a :class:`pathlib.Path`.
    """
    return value if isinstance(value, Path) else Path(value)


def _is_same_file(left: Path, right: Path) -> bool:
    """Report whether two paths name the same file.

    Args:
        left: One path.
        right: The other, which need not exist yet.

    Returns:
        True when both resolve to the same location. pikepdf reads lazily and
        refuses to overwrite its own input unless told at open time, so this decides
        how the input is opened.
    """
    return left.resolve() == right.resolve()


@contextmanager
def _opened(path: Path, *, allow_overwriting_input: bool = False) -> Iterator[Pdf]:
    """Open a document, with one exception root over everything the PDF library refuses.

    Every function in this module that takes a path opens it through here, and the
    ``with`` block is inside the guard rather than outside it: qpdf parses lazily, so a
    document whose page tree or cross-reference table is damaged raises when it is walked
    and not when it is opened. Either way what escapes is a
    :class:`~plannotation.errors.PlannotationError`, which is what the package promises a
    caller: a program embedding Plannotation catches one root, and a person running the CLI
    reads a message rather than a traceback out of a C++ library.

    Args:
        path: The document to open.
        allow_overwriting_input: Whether the caller intends to save over this same file,
            which pikepdf must be told at open time because it reads lazily.

    Yields:
        The open document.

    Raises:
        EncryptedPdfError: If the file needs a password to be opened at all.
        CarrierError: If the PDF library will not read the file, or will not read some
            part of it that this module reached for.
    """
    try:
        with pikepdf.open(path, allow_overwriting_input=allow_overwriting_input) as pdf:
            yield pdf
    except pikepdf.PasswordError as exc:
        msg = (
            f"{path} is encrypted with a password, so it cannot be opened at all: {exc}. "
            "Remove the encryption with a tool meant for it and plannotate the result, "
            "or write a sidecar, which does not touch the document"
        )
        raise EncryptedPdfError(msg) from exc
    except pikepdf.PikepdfError as exc:
        msg = (
            f"{path} is not a PDF this library will read: {exc}. The file is damaged "
            "beyond what qpdf repairs, so there is nothing here to read a plannotation "
            "out of or to write one into"
        )
        raise CarrierError(msg) from exc


def _catalog(pdf: Pdf) -> Dictionary:
    """Return the document catalog as a dictionary.

    Args:
        pdf: An open document.

    Returns:
        ``pdf.Root``. pikepdf types it as a bare object because a damaged file can
        hold anything there, so it is checked once, here, rather than at each use.

    Raises:
        CarrierError: If the catalog is not a dictionary, which means the document is
            damaged beyond anything this module should try to write to.
    """
    root = pdf.Root
    if not isinstance(root, Dictionary):
        msg = f"the document catalog is not a dictionary: {root!r}"
        raise CarrierError(msg)
    return root


def _af_entries(owner: Object) -> list[Object | None]:
    """Return the entries of an ``/AF`` array, tolerating dangling references.

    An entry reads as None when the file specification it pointed at has been
    deleted -- which is exactly what a document looks like after somebody removed an
    attachment without tidying the arrays that referenced it. pikepdf's types do not
    admit that possibility, so the cast is where the knowledge lives.

    Args:
        owner: A page or catalog dictionary.

    Returns:
        The entries, with None for any that no longer resolves, or an empty list when
        there is no ``/AF`` at all.
    """
    value = owner.get(Name.AF)
    if value is None:
        return []
    if not isinstance(value, Array):
        msg = f"the document has an /AF entry that is not an array: {value!r}"
        raise CarrierError(msg)
    return [cast("Object | None", entry) for entry in value]


def _identity(entry: object) -> tuple[int, int] | None:
    """Return the object identity of an ``/AF`` entry, or None when it has none.

    pikepdf converts a PDF integer, real, boolean or string into the Python type, so an
    ``/AF`` array that holds ``42`` hands back an ``int``. An ``int`` has no ``objgen``,
    and asking it for one raised an :class:`AttributeError` out of this module and
    through :func:`attach` and :func:`strip` to whoever called them -- which is not a
    :class:`~plannotation.errors.PlannotationError`, so it reached a person as a traceback.

    Args:
        entry: Whatever the array held.

    Returns:
        Its object identity, or None when it is not a PDF object at all and therefore is
        not a file specification this module wrote, shares or may remove.
    """
    return entry.objgen if isinstance(entry, pikepdf.Object) else None


@cache
def _validator(kind: Literal["page", "index", "sidecar"]) -> Draft202012Validator:
    """Return a cached JSON Schema validator for one Plannotation document kind.

    Args:
        kind: Which packaged schema to validate against.

    Returns:
        A draft 2020-12 validator. Compiling one costs more than validating with it,
        and a reader opens many plannotations, so the validators are built once.
    """
    schema = {"page": page_schema, "index": index_schema, "sidecar": sidecar_schema}[kind]()
    return Draft202012Validator(schema)


def _clip(value: object, limit: int = _MAX_QUOTED_CHARS) -> str:
    """Render a document-derived value for a message, bounded.

    Everything quoted in a refusal comes from the document being refused, so
    without a bound the message is as long as the attacker likes -- and a refusal
    is precisely what a caller following SPEC 4.3 (2) will log.

    Args:
        value: The value to render.
        limit: Longest rendering to return, before the ellipsis is added.

    Returns:
        ``str(value)`` truncated to ``limit`` characters, with a single-line
        ellipsis naming how much was dropped.

    Examples:
        >>> _clip("short")
        'short'
        >>> _clip("x" * 250)[-20:]
        '... (+50 characters)'
    """
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... (+{len(text) - limit} characters)"


def _check_schema(document: object, kind: Literal["page", "index", "sidecar"], source: str) -> None:
    """Validate a parsed document against its packaged schema.

    The schema is the single source of truth for the format, so a reader validates
    against it rather than trusting the models to agree with it.

    Args:
        document: The parsed JSON document.
        kind: Which schema it must satisfy.
        source: Where the document came from, for the message.

    Raises:
        InvalidPlannotationError: If the document does not validate, with a JSON Pointer to
            each of the first few failures.
    """
    # Take only what is reported, and only then sort. The number of violations is
    # chosen by the document and is unbounded inside the byte cap, so materialising
    # them all would make the refusal cost far more than the validation it reports.
    # One extra is taken so that "and more" can be said without counting the rest.
    found = list(islice(_validator(kind).iter_errors(document), _MAX_REPORTED_ERRORS + 1))
    if not found:
        return
    reported = sorted(found[:_MAX_REPORTED_ERRORS], key=lambda error: error.json_path)
    listed = [f"  {error.json_path}: {_clip(error.message)}" for error in reported]
    if len(found) > _MAX_REPORTED_ERRORS:
        listed.append("  ... and more")
    body = "\n".join(listed)
    msg = f"{source} is not a valid Plannotation {kind} document:\n{body}"
    raise InvalidPlannotationError(msg)


def _load_model(
    data: bytes,
    kind: Literal["page", "index", "sidecar"],
    source: str,
    loader: Callable[[bytes], _ModelT],
) -> _ModelT:
    """Parse, schema-check and model-load one Plannotation document.

    Both checks are made, in this order, and they are not redundant. The schema is
    the format, and it is what a third-party reader would apply; the model adds the
    few rules the schema cannot express, such as the page-wide uniqueness of a
    ``localId``. Reporting a schema failure first means the message names the format
    and not this implementation.

    Args:
        data: The document's bytes, as they were stored.
        kind: Which schema it must satisfy.
        source: Where it came from, for the message.
        loader: The model loader for that kind.

    Returns:
        The loaded model.

    Raises:
        InvalidPlannotationError: If the bytes are not UTF-8, not JSON, do not validate
            against the schema, or do not load into the model.
    """
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        msg = f"{source} is not readable as UTF-8 JSON: {_clip(str(exc))}"
        raise InvalidPlannotationError(msg) from exc
    except RecursionError as exc:
        # A document may nest as deeply as it likes: `extensions` and an element's
        # `properties` hold arbitrary JSON, so no depth limit could be both generous
        # enough for legitimate data and tight enough to be worth pre-scanning for.
        # CPython's scanner raises here rather than crashing, and it raises before
        # the payload is consumed -- measured at 3 ms and 0.5 MB over the input for
        # 16 MiB of pure nesting -- so converting the error is the whole fix.
        # RecursionError is not a ValueError, so without this it escapes read(),
        # read(strict=False) and carrier_report() alike, which SPEC 4.3(2) forbids.
        msg = f"{source} nests too deeply for this reader to parse"
        raise InvalidPlannotationError(msg) from exc
    _check_schema(document, kind, source)
    try:
        return loader(data)
    except ValueError as exc:  # pydantic's ValidationError is a ValueError
        msg = f"{source} validates against the schema but not against the model: {exc}"
        raise InvalidPlannotationError(msg) from exc


# ---------------------------------------------------------------------------
# Signatures
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SignatureReport:
    """What a document says about being signed.

    Attributes:
        signed: True if any marker of a signature was found. The three markers are
            reported separately below, because a document can carry one without the
            others and each means something different.
        fields: The ``/T`` names of signature fields that hold a value, that is,
            fields that have actually been signed rather than merely prepared.
        sig_flags: The AcroForm ``/SigFlags`` value. Bit 1 means the document
            contains at least one signature field.
        doc_mdp: True if the catalog's ``/Perms`` carries ``/DocMDP``, a
            certification signature that states what later changes are permitted.
        ur3: True if ``/Perms`` carries ``/UR3``, Reader-extension usage rights,
            which are also invalidated by rewriting the file.
    """

    signed: bool
    fields: tuple[str, ...]
    sig_flags: int
    doc_mdp: bool
    ur3: bool

    def describe(self) -> str:
        """Describe the findings in one line.

        Returns:
            A human-readable summary naming whichever markers fired.
        """
        if not self.signed:
            return "no signature"
        parts: list[str] = []
        if self.fields:
            parts.append("signed fields " + ", ".join(self.fields))
        if self.sig_flags & 1:
            parts.append(f"AcroForm /SigFlags {self.sig_flags}")
        if self.doc_mdp:
            parts.append("/Perms /DocMDP (certified)")
        if self.ur3:
            parts.append("/Perms /UR3 (Reader extensions)")
        return "; ".join(parts)


def signature_report(pdf: Pdf) -> SignatureReport:
    """Report whether a document carries a digital signature.

    Three independent markers are checked, because a document may carry any of them
    alone: a signature field (``/FT /Sig``) that holds a value, anywhere in the
    AcroForm field tree including under ``/Kids``; bit 1 of the AcroForm
    ``/SigFlags``; and ``/DocMDP`` or ``/UR3`` in the catalog's ``/Perms``.

    Args:
        pdf: An open document.

    Returns:
        The report. It is a description, not a decision: :func:`attach` decides.
    """
    acroform = pdf.Root.get(Name.AcroForm)
    fields: list[str] = []
    flags = 0
    if acroform is not None:
        flags = int(acroform.get(Name.SigFlags, 0))
        fields = _signed_field_names(acroform.get(Name.Fields, Array()))
    perms = pdf.Root.get(Name.Perms)
    doc_mdp = perms is not None and Name.DocMDP in perms
    ur3 = perms is not None and Name.UR3 in perms
    return SignatureReport(
        signed=bool(fields) or doc_mdp or bool(flags & 1),
        fields=tuple(fields),
        sig_flags=flags,
        doc_mdp=doc_mdp,
        ur3=ur3,
    )


def _signed_field_names(roots: object) -> list[str]:
    """Walk an AcroForm field tree and name every signature field that holds a value.

    Args:
        roots: The ``/Fields`` array of the AcroForm dictionary.

    Returns:
        The ``/T`` of each ``/FT /Sig`` field that has a ``/V``, in the order found.
        The walk is bounded and cycle-safe: the tree comes from an untrusted document
        and ``/Kids`` may point anywhere, including back at an ancestor.
    """
    found: list[str] = []
    if not isinstance(roots, Array):
        return found
    pending: list[Dictionary] = [node for node in roots if isinstance(node, Dictionary)]
    seen: set[tuple[int, int]] = set()
    visited = 0
    while pending and visited < _MAX_FIELD_NODES:
        node = pending.pop()
        visited += 1
        if node.is_indirect:
            if node.objgen in seen:
                continue
            seen.add(node.objgen)
        if node.get(Name.FT) == Name.Sig and Name.V in node:
            found.append(str(node.get(Name.T, "?")))
        kids = node.get(Name.Kids)
        if isinstance(kids, Array):
            pending.extend(kid for kid in kids if isinstance(kid, Dictionary))
    if visited >= _MAX_FIELD_NODES:
        _LOGGER.warning(
            "stopped walking the AcroForm field tree after %d nodes; "
            "treating the document as signed",
            _MAX_FIELD_NODES,
        )
        found.append("?")
    return found


# ---------------------------------------------------------------------------
# Page geometry
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PageGeometry:
    """The unrotated size of one page, in the terms a plannotation uses.

    Section 3.1 of the specification defines a page as the box that is displayed and
    printed -- the ``CropBox``, falling back to the ``MediaBox`` -- and section 3.7
    requires ``page.widthMm`` and ``page.heightMm`` to be that box's **unrotated**
    dimensions, never swapped for a rotated page. That is why this is read from the
    page dictionary and not from a renderer: a rasteriser reports the size it
    displays, which for a ``/Rotate 90`` page has width and height the other way
    round.

    Attributes:
        page_index: The zero-based index of the page described.
        width_mm: The box's width in millimetres, unrotated.
        height_mm: The box's height in millimetres, unrotated.
        rotation: The page's effective ``/Rotate``, normalised to 0, 90, 180 or 270.
        user_unit: The page's ``/UserUnit``, which scales user space and is common on
            large-format drawings. It is already applied to the dimensions above.
        box: The box itself in PDF points, normalised to ``(x0, y0, x1, y1)`` with
            ``x0 <= x1`` and ``y0 <= y1``. Its lower-left corner is the origin of
            paper coordinates and must be subtracted from any point read off the
            page.
        box_source: ``"CropBox"`` when the page has a crop box that differs from its
            media box, ``"MediaBox"`` otherwise.
    """

    page_index: int
    width_mm: float
    height_mm: float
    rotation: int
    user_unit: float
    box: tuple[float, float, float, float]
    box_source: Literal["CropBox", "MediaBox"]


def _rectangle(value: object) -> tuple[float, float, float, float]:
    """Read a PDF rectangle as a normalised tuple.

    Args:
        value: A four-element PDF array holding two diagonally opposite corners, in
            either order -- which the format permits and real files use.

    Returns:
        ``(x0, y0, x1, y1)`` with the lower-left corner first.

    Raises:
        CarrierError: If the value is not a four-number array.
    """
    if not isinstance(value, Array) or len(value) != _RECTANGLE_LENGTH:
        msg = f"page box is not a four-element rectangle: {value!r}"
        raise CarrierError(msg)
    try:
        numbers = [float(item) for item in value]
    except (TypeError, ValueError) as exc:
        msg = f"page box holds something that is not a number: {value!r}"
        raise CarrierError(msg) from exc
    return (
        min(numbers[0], numbers[2]),
        min(numbers[1], numbers[3]),
        max(numbers[0], numbers[2]),
        max(numbers[1], numbers[3]),
    )


def _effective_rotation(page: Dictionary, page_index: int) -> int:
    """Return a page's ``/Rotate`` normalised to 0, 90, 180 or 270.

    Args:
        page: The page dictionary.
        page_index: Which page it is, for the log message.

    Returns:
        The rotation in degrees, clockwise. A negative or out-of-range multiple of 90
        is normalised the way a viewer normalises it; anything else is rounded to the
        nearest quarter turn and reported, since the document is malformed and
        refusing to read it would help nobody.
    """
    raw = page.get(Name.Rotate, 0)
    try:
        degrees = float(raw)
    except (TypeError, ValueError):
        _LOGGER.warning("page %d has a non-numeric /Rotate %r; reading it as 0", page_index, raw)
        return 0
    quarters = round(degrees / 90.0)
    normalised = int(quarters * 90) % 360
    if degrees != normalised:
        _LOGGER.debug("page %d has /Rotate %g, normalised to %d", page_index, degrees, normalised)
    return normalised


def page_geometry(pdf: Pdf, page_index: int) -> PageGeometry:
    """Measure one page the way a plannotation must describe it.

    The crop box is clipped to the media box first, as a viewer clips it, so that a
    crop box larger than the sheet cannot inflate a plannotation's page size.

    Args:
        pdf: An open document.
        page_index: The zero-based page to measure.

    Returns:
        The page's unrotated dimensions in millimetres, its normalised rotation, its
        ``/UserUnit`` and the box the dimensions came from.

    Raises:
        IndexError: If the document has no such page.
        CarrierError: If the page's boxes are malformed.
    """
    page = pdf.pages[page_index]
    media = _rectangle(page.mediabox)
    crop = _rectangle(page.cropbox)
    clipped = (
        max(crop[0], media[0]),
        max(crop[1], media[1]),
        min(crop[2], media[2]),
        min(crop[3], media[3]),
    )
    if clipped[0] >= clipped[2] or clipped[1] >= clipped[3]:
        _LOGGER.warning(
            "page %d has a /CropBox that does not overlap its /MediaBox; using the media box",
            page_index,
        )
        clipped = media
    user_unit = float(page.obj.get(Name.UserUnit, 1))
    if user_unit <= 0:
        msg = f"page {page_index} has a non-positive /UserUnit {user_unit!r}"
        raise CarrierError(msg)
    scale = user_unit * MM_PER_PT
    return PageGeometry(
        page_index=page_index,
        width_mm=(clipped[2] - clipped[0]) * scale,
        height_mm=(clipped[3] - clipped[1]) * scale,
        rotation=_effective_rotation(page.obj, page_index),
        user_unit=user_unit,
        box=clipped,
        box_source="MediaBox" if clipped == media else "CropBox",
    )


# ---------------------------------------------------------------------------
# Bounded decoding
# ---------------------------------------------------------------------------
#: The one filter chain this reader decodes: a single ``/FlateDecode``, with or without
#: ``/DecodeParms``.
#:
#: Section 6.2.4 of the specification lets a writer compress an embedded file with any
#: standard filter, so this restriction is the reader's and not the format's. It is
#: deliberate. The bound of section 4.3 (7) is a bound only if it holds for every input,
#: and the input chooses the filter: handing the stream to the PDF library to decode
#: applies the limit after the memory has been spent, which is not a limit at all.
#: Decoding here, incrementally, is what makes it real, and Flate is the only filter
#: worth implementing that way -- everything Plannotation writes is stored either as it is
#: or Flate-encoded, and a JSON plannotation arriving under LZW, RunLength or a chain of
#: two filters is either hostile or broken. Section 4.3 (2) says what becomes of a
#: plannotation this reader will not accept: it is absent.
_FLATE_ONLY: Final = [str(Name.FlateDecode)]

#: Where an oversize was noticed, which decides how the message describes it: the
#: stream's own ``/Params /Size``, the ``/Length`` its dictionary declares for its stored
#: bytes, the length those bytes turned out to be, or what they decoded to.
_Measured = Literal["stated", "declared", "stored", "decoded"]

#: How each of those reads in a sentence.
_MEASURED_PHRASE: Final = {
    "stated": "says it holds",
    "declared": "declares a stored length of",
    "stored": "is stored as",
    "decoded": "expands to",
}

#: What to tell someone whose *plannotation* would not fit inside the bound.
_PLANNOTATION_ADVICE: Final = (
    "A plannotation is a few kilobytes, so this is either damage or a decompression "
    "bomb, and section 4.3 (7) of the specification requires a reader to bound what it "
    "spends parsing. Pass max_plannotation_bytes to raise the limit if a plannotation "
    "really is this large"
)

#: What to tell someone whose *XMP packet* would not, where there is no such parameter
#: because there is no legitimate packet anywhere near the bound.
_PACKET_ADVICE: Final = (
    "An XMP packet is a few kilobytes, so this is either damage or a decompression "
    "bomb, and section 9 of the specification requires a reader to bound what it spends "
    "on attacker-supplied input. The packet is being treated as unreadable"
)

#: ``/Predictor`` 1, and anything below it, means the data is not predicted.
_PREDICTOR_NONE: Final = 1

#: ``/Predictor`` 2 is TIFF horizontal differencing.
_PREDICTOR_TIFF: Final = 2

#: ``/Predictor`` 10 and above are the PNG filters, one tag byte per row.
_PREDICTOR_PNG: Final = 10

#: The five PNG filter types, by the tag byte that introduces a row.
_PNG_NONE: Final = 0
_PNG_SUB: Final = 1
_PNG_UP: Final = 2
_PNG_AVERAGE: Final = 3
_PNG_PAETH: Final = 4

#: Bits in a byte, spelled out where the arithmetic would otherwise be a bare 8.
_BITS_PER_BYTE: Final = 8

#: Every ``/Predictor`` ISO 32000-2 table 10 defines. Anything else is refused by name
#: rather than treated as "no predictor": a document that asks for a predictor this
#: reader does not implement is a document whose bytes this reader cannot reconstruct,
#: and guessing would mean handing a caller data that is not what the file says it is.
_PREDICTORS: Final = frozenset({1, 2, 10, 11, 12, 13, 14, 15})

#: Every ``/BitsPerComponent`` the same table allows.
_BITS_PER_COMPONENT: Final = frozenset({1, 2, 4, 8, 16})

#: An upper bound on ``/Colors``. The format says only "greater than or equal to 1", so
#: this is the reader's limit and not the format's: 32 is the number of colourants a
#: DeviceN space may name, which is the largest number of interleaved components any
#: real stream carries, and an unbounded ``/Colors`` multiplies straight into the row
#: length that is about to be allocated.
_MAX_COLORS: Final = 32

#: How many bytes this reader will undo an Average or a Paeth predictor over.
#:
#: The other three PNG filters and the TIFF one are array arithmetic: they are undone
#: with numpy in one pass per row, or in one pass for the whole stream. Average and
#: Paeth are not. Each reconstructed byte is an input to the next byte in the same row,
#: so undoing them is a sequential loop in Python at roughly a tenth of a microsecond a
#: byte, and at that rate :data:`_MAX_PLANNOTATION_BYTES` is a bound on memory and not a
#: bound on time -- sixteen mebibytes of Paeth cost seconds, and the document chooses how
#: many plannotations a reader opens. Section 4.3 (7) asks for both bounds, so the two
#: sequential predictors get a tighter one.
#:
#: One mebibyte is about a tenth of a second and is three orders of magnitude above any
#: real payload. Nothing legitimately stores a JSON file under a PNG Average or Paeth
#: predictor at all: predictors exist for image samples and for cross-reference streams,
#: which use Up.
_MAX_SEQUENTIAL_PREDICTED_BYTES: Final = 1024 * 1024


def _oversize(
    source: str, limit: int, *, measured: _Measured, advice: str
) -> InvalidPlannotationError:
    """Build the error raised when something will not fit inside the bound.

    Args:
        source: The file or packet that is too large, named as a reader would name it.
        limit: The bound in bytes, so that the message says what was exceeded.
        measured: Where the size came from. A size the stream states about itself is a
            claim by whoever wrote the file, and is worth an early refusal and nothing
            as reassurance; the other two were measured here.
        advice: What the reader should be told to do about it, which differs between a
            plannotation and the document's metadata.

    Returns:
        The error to raise. It is returned rather than raised so that the call site
        reads ``raise _oversize(...)``.
    """
    msg = (
        f"{source} {_MEASURED_PHRASE[measured]} more than the {limit} bytes this reader "
        f"will decompress. {advice}"
    )
    return InvalidPlannotationError(msg)


def _unsupported_filter(source: str, filters: Sequence[str]) -> InvalidPlannotationError:
    """Build the error raised for a filter chain this reader will not decode.

    Args:
        source: The file or packet, named as a reader would name it.
        filters: The chain as the stream declares it, which the message names because
            the filter is the one thing the person looking at the file cannot see.

    Returns:
        The error to raise.
    """
    named = ", ".join(filters) if filters else "an empty filter array"
    msg = (
        f"{source} is stored under {named}, which this reader does not decode. A "
        "conforming writer stores an embedded file either unencoded or under a single "
        "/FlateDecode, and decoding anything else would mean handing an "
        "attacker-chosen filter chain to the PDF library and applying the bound "
        "afterwards -- which is not a bound (section 4.3 (7) and section 9). A "
        "plannotation this reader will not accept is a plannotation that is absent "
        "(section 4.3 (2))"
    )
    return InvalidPlannotationError(msg)


def _stream_filters(stream: Object) -> list[str]:
    """Name the filters an embedded stream's stored bytes are encoded with.

    Args:
        stream: An embedded file stream.

    Returns:
        The ``/Filter`` entry as a list of names, empty when the stream is stored as it
        is. A single name and an array of one are the same thing, and both occur.
    """
    value = stream.stream_dict.get(Name.Filter)
    if value is None:
        return []
    if isinstance(value, Array):
        return [str(item) for item in value]
    return [str(value)]


def _bounded_inflate(raw: bytes, *, limit: int, source: str, advice: str) -> bytes:
    """Inflate a Flate stream, stopping rather than allocating past the bound.

    ``max_length`` is what makes this bounded: the decompressor stops at ``limit + 1``
    bytes and leaves the rest of the input in ``unconsumed_tail``, so a stream that
    expands to gibibytes costs the bound and an error rather than the machine.
    ``flush()`` is only reached once the input is known to be fully consumed, because
    it would otherwise decompress that tail.

    A stream whose zlib header is missing -- which qpdf tolerates and real files
    contain -- is retried as raw deflate. That is the whole of this reader's leniency:
    what neither attempt decodes is treated as absent rather than handed to the PDF
    library, because the library's decoder has no bound and the stream chose its own
    size.

    Args:
        raw: The stream's stored bytes.
        limit: The most the output may be.
        source: The file or packet being read, for the message.
        advice: What to tell the reader if it does not fit.

    Returns:
        The decompressed bytes.

    Raises:
        InvalidPlannotationError: If the stream decompresses to more than ``limit`` bytes, or
            zlib will not read it at all.
    """
    for window in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
        decompressor = zlib.decompressobj(window)
        try:
            data = decompressor.decompress(raw, limit + 1)
        except zlib.error:
            continue
        if len(data) > limit or (decompressor.unconsumed_tail and not decompressor.eof):
            raise _oversize(source, limit, measured="decoded", advice=advice)
        try:
            data += decompressor.flush()
        except zlib.error:  # pragma: no cover - flush after a fully consumed input
            continue
        if len(data) > limit:
            raise _oversize(source, limit, measured="decoded", advice=advice)
        return data
    msg = (
        f"{source} is stored under /FlateDecode and zlib will not decompress it, as a "
        "zlib stream or as raw deflate. The stream is damaged, and section 4.3 (2) of "
        "the specification requires a plannotation a reader will not accept to be "
        "treated as absent rather than repaired"
    )
    raise InvalidPlannotationError(msg)


def _decode_parms(stream: Object, *, source: str) -> Object | None:
    """Return the ``/DecodeParms`` dictionary of a single-filter stream.

    Args:
        stream: An embedded file stream carrying exactly one filter.
        source: The file or packet being read, for the message.

    Returns:
        The parameters, or None when the stream declares none. An array of one is the
        same thing as a dictionary, because ``/DecodeParms`` parallels ``/Filter`` and
        this stream has one filter.

    Raises:
        InvalidPlannotationError: If ``/DecodeParms`` is neither, which means it does not
            parallel the one ``/Filter`` and the stream cannot be decoded as written.
    """
    value = stream.stream_dict.get(Name.DecodeParms)
    if isinstance(value, Array):
        if len(value) != 1:
            msg = (
                f"{source} declares one filter and {len(value)} sets of /DecodeParms; "
                "the two arrays must have the same length, so this stream is damaged"
            )
            raise InvalidPlannotationError(msg)
        value = value[0]
    if value is None:
        return None
    if not isinstance(value, Dictionary):
        msg = f"{source} has a /DecodeParms that is not a dictionary: {value!r}"
        raise InvalidPlannotationError(msg)
    return value


def _parms_integer(parms: Object, key: Name, default: int, *, source: str) -> int:
    """Read one integer out of a ``/DecodeParms`` dictionary, without judging it.

    Range is not checked here, because each of the four parameters has its own range and
    naming the parameter in the message is the whole value of checking it. What is
    checked is that the entry is an integer: pikepdf hands back a :class:`decimal.Decimal`
    for ``2.5`` and a :class:`pikepdf.Object` for a name or a string, and ``int()`` would
    quietly truncate the first and raise out of the PDF library on the second.

    Args:
        parms: The parameters.
        key: Which one to read.
        default: What the format says it is when absent.
        source: The file or packet being read, for the message.

    Returns:
        The value, or ``default``.

    Raises:
        InvalidPlannotationError: If the entry is present and is not an integer. ``True`` is an
            integer to Python and is not one to PDF, so booleans are refused with the
            rest.
    """
    # pikepdf converts a PDF integer to a Python ``int`` and a PDF real to a
    # ``Decimal``, but its stubs type every lookup as ``Object``; the cast is where that
    # knowledge lives, and it is what lets the check below be written at all.
    value = cast("object", parms.get(key))
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool):
        msg = (
            f"{source} has a /DecodeParms {key} that is not an integer: {value!r}. ISO "
            "32000-2 table 10 makes all four predictor parameters integers, so this "
            "stream cannot be decoded as written"
        )
        raise InvalidPlannotationError(msg)
    return value


@dataclass(frozen=True)
class _PredictorPlan:
    """A ``/DecodeParms`` predictor, checked against the format and against the bound.

    Every field is validated before it reaches this class, which is the point of the
    class: the arithmetic that follows -- a row length, a stride, an allocation -- is
    then arithmetic on numbers that are known to be small, rather than on three integers
    the document chose.

    Attributes:
        predictor: ``/Predictor``, one of the values ISO 32000-2 defines.
        colors: ``/Colors``, between 1 and :data:`_MAX_COLORS`.
        bits: ``/BitsPerComponent``, one of 1, 2, 4, 8 and 16.
        columns: ``/Columns``, at least 1 and small enough that ``row_length`` fits
            inside the bound.
        row_length: The bytes one decoded row occupies, at most the bound.
        step: The distance in bytes to the byte one pixel to the left, at least 1.
    """

    predictor: int
    colors: int
    bits: int
    columns: int
    row_length: int
    step: int


def _out_of_range(source: str, key: Name, value: int, allowed: str) -> InvalidPlannotationError:
    """Build the error raised for a predictor parameter outside its range.

    Args:
        source: The file or packet being read, named as a reader would name it.
        key: The parameter, which the message names because it is the one thing the
            person looking at the file cannot see.
        value: What the document asked for.
        allowed: What the format allows, in words.

    Returns:
        The error to raise.
    """
    msg = (
        f"{source} has a /DecodeParms {key} of {value}, and {allowed}. The parameters "
        "are read and checked before anything is allocated, because they are three "
        "integers the document chooses and they multiply straight into the size of the "
        "allocation (section 4.3 (7) and section 9). A plannotation this reader will "
        "not accept is a plannotation that is absent (section 4.3 (2))"
    )
    return InvalidPlannotationError(msg)


def _predictor_plan(parms: Object, *, limit: int, source: str) -> _PredictorPlan | None:
    """Validate a stream's ``/DecodeParms`` before a single byte is allocated for it.

    This function is the answer to a 1,253-byte PDF that cost 1,963 MB. The row length
    was computed from ``/Columns``, ``/Colors`` and ``/BitsPerComponent`` -- three
    integers with no upper bound, taken straight from the file -- and then allocated. A
    ``/Columns`` of two thousand million asked for two gibibytes; a little more asked
    for a :class:`MemoryError`, which is not a :class:`~plannotation.errors.PlannotationError`,
    so ``strict=False`` did not deliver the absence section 4.3 (2) promises and the CLI
    printed a traceback.

    Args:
        parms: The stream's ``/DecodeParms``.
        limit: The most the decoded stream may be, which is also the most one row may be.
        source: The file or packet being read, for the message.

    Returns:
        The validated plan, or None when the stream declares no predictor at all.

    Raises:
        InvalidPlannotationError: If any parameter is not an integer, is outside the range ISO
            32000-2 table 10 gives it, or implies a row this reader will not allocate.
            The message names the parameter.
    """
    predictor = _parms_integer(parms, Name.Predictor, _PREDICTOR_NONE, source=source)
    if predictor not in _PREDICTORS:
        raise _out_of_range(
            source, Name.Predictor, predictor, "ISO 32000-2 defines 1, 2, and 10 to 15"
        )
    if predictor == _PREDICTOR_NONE:
        return None
    colors = _parms_integer(parms, Name.Colors, 1, source=source)
    if not 1 <= colors <= _MAX_COLORS:
        raise _out_of_range(
            source, Name.Colors, colors, f"this reader decodes 1 to {_MAX_COLORS} components"
        )
    bits = _parms_integer(parms, Name.BitsPerComponent, _BITS_PER_BYTE, source=source)
    if bits not in _BITS_PER_COMPONENT:
        raise _out_of_range(
            source, Name.BitsPerComponent, bits, "ISO 32000-2 defines 1, 2, 4, 8 and 16"
        )
    columns = _parms_integer(parms, Name.Columns, 1, source=source)
    # Bounded before it is multiplied: /Columns arrives as an arbitrary-precision
    # integer, and a row cannot be shorter than one bit per column.
    if columns < 1 or columns > limit * _BITS_PER_BYTE:
        raise _out_of_range(source, Name.Columns, columns, f"a row must be 1 to {limit} bytes long")
    row_length = (columns * colors * bits + _BITS_PER_BYTE - 1) // _BITS_PER_BYTE
    if row_length > limit:
        msg = (
            f"{source} declares /DecodeParms describing rows of {row_length} bytes, more "
            f"than the {limit} bytes this reader will decompress the whole stream to. "
            f"/Columns {columns}, /Colors {colors} and /BitsPerComponent {bits} are "
            "multiplied together before anything is allocated, exactly so that a row "
            "this large is refused rather than allocated (section 4.3 (7))"
        )
        raise InvalidPlannotationError(msg)
    return _PredictorPlan(
        predictor=predictor,
        colors=colors,
        bits=bits,
        columns=columns,
        row_length=row_length,
        step=max(1, (colors * bits) // _BITS_PER_BYTE),
    )


def _apply_predictor(data: bytes, parms: Object, *, limit: int, source: str) -> bytes:
    """Undo the predictor a stream's ``/DecodeParms`` declares.

    This is work the PDF library would do, and it is done here for the reason the whole
    of this section exists: the library would do it to the unbounded output of its own
    unbounded decoder. Applied to output this reader has already bounded, the cost is
    bounded with it.

    Args:
        data: The inflated bytes, already inside the bound.
        parms: The stream's ``/DecodeParms``.
        limit: The bound those bytes were read under, which also bounds a row.
        source: The file or packet being read, for the message.

    Returns:
        The unpredicted bytes, which are never longer than ``data``.

    Raises:
        InvalidPlannotationError: If the parameters are not ones this reader will decode, or do
            not describe the data. An empty stream is refused rather than accepted: zero
            bytes are a whole number of rows of any length, so the arithmetic that checks
            the shape of the data passes vacuously on them, and a stream that declares a
            predictor and carries nothing to unpredict is damaged.
    """
    plan = _predictor_plan(parms, limit=limit, source=source)
    if plan is None:
        return data
    if not data:
        msg = (
            f"{source} declares /Predictor {plan.predictor} and decompresses to nothing. "
            "Zero bytes are a whole number of rows of every length, so this is a stream "
            "whose shape cannot be checked rather than one that has been checked; "
            "section 4.3 (2) requires it to be treated as absent"
        )
        raise InvalidPlannotationError(msg)
    if plan.predictor >= _PREDICTOR_PNG:
        return _png_unpredict(data, plan, source=source)
    return _tiff_unpredict(data, plan, source=source)


def _accumulate_left(row: NDArray[np.uint8], *, step: int) -> None:
    """Add each byte to the byte ``step`` to its left, along the whole row, in place.

    That recurrence is the PNG Sub filter and, with ``step`` set to ``/Colors``, TIFF
    horizontal differencing. It is a running sum, so it separates into ``step``
    independent lanes -- byte 0, byte ``step``, byte ``2 * step`` and so on are one lane
    -- and each lane is a cumulative sum, which numpy does in one pass. The previous
    implementation walked it a byte at a time in Python.

    Args:
        row: The row, modified in place. ``uint8`` arithmetic wraps, which is the
            ``& 0xFF`` the format asks for.
        step: The lane width, at least 1.
    """
    width = row.size
    pad = -width % step
    flat = np.concatenate((row, np.zeros(pad, dtype=np.uint8))) if pad else row.copy()
    row[:] = np.cumsum(flat.reshape(-1, step), axis=0, dtype=np.uint8).reshape(-1)[:width]


def _png_average_row(row: NDArray[np.uint8], previous: NDArray[np.uint8], *, step: int) -> None:
    """Undo one PNG Average row in place.

    Args:
        row: The filtered row, replaced with the unfiltered one.
        previous: The unfiltered row above it, zeros for the first row.
        step: The distance in bytes to the byte one pixel to the left.

    Note:
        Sequential by construction: the byte to the left is one this loop has just
        reconstructed, so there is no array form of it. The loop runs over Python lists
        rather than over a numpy array, because indexing a ``uint8`` array returns a
        boxed scalar and boxing dominates.
    """
    filtered = row.tolist()
    up = previous.tolist()
    done: list[int] = []
    for at, raw in enumerate(filtered):
        left = done[at - step] if at >= step else 0
        done.append((raw + ((left + up[at]) >> 1)) & 0xFF)
    row[:] = np.array(done, dtype=np.uint8)


def _png_paeth_row(row: NDArray[np.uint8], previous: NDArray[np.uint8], *, step: int) -> None:
    """Undo one PNG Paeth row in place.

    Args:
        row: The filtered row, replaced with the unfiltered one.
        previous: The unfiltered row above it, zeros for the first row.
        step: The distance in bytes to the byte one pixel to the left.

    Note:
        Sequential for the same reason as Average, and the arithmetic is written out
        here rather than called: a function call per byte was a third of what undoing a
        Paeth predictor cost.
    """
    filtered = row.tolist()
    up = previous.tolist()
    done: list[int] = []
    for at, raw in enumerate(filtered):
        if at >= step:
            left = done[at - step]
            upper_left = up[at - step]
        else:
            left = upper_left = 0
        above = up[at]
        estimate = left + above - upper_left
        to_left = abs(estimate - left)
        to_up = abs(estimate - above)
        to_upper_left = abs(estimate - upper_left)
        if to_left <= to_up and to_left <= to_upper_left:
            predicted = left
        elif to_up <= to_upper_left:
            predicted = above
        else:
            predicted = upper_left
        done.append((raw + predicted) & 0xFF)
    row[:] = np.array(done, dtype=np.uint8)


def _png_unpredict(data: bytes, plan: _PredictorPlan, *, source: str) -> bytes:
    """Undo a PNG predictor, which tags every row with the filter it was written with.

    Three of the five filters are array arithmetic and are undone with numpy: None is a
    copy, Up adds the row above, and Sub is a cumulative sum along each lane. Average and
    Paeth read the byte they have just written, so they keep a loop -- one per row, over
    Python lists, with the arithmetic written out rather than called -- and are bounded
    by :data:`_MAX_SEQUENTIAL_PREDICTED_BYTES` rather than by the byte cap alone.

    Args:
        data: The inflated bytes, already inside the bound: one tag byte and one row,
            repeated.
        plan: The validated ``/DecodeParms``.
        source: The file or packet being read, for the message.

    Returns:
        The unpredicted bytes, one row shorter per row than the input.

    Raises:
        InvalidPlannotationError: If the data is not a whole number of tagged rows, a row
            carries a tag that is not a PNG filter type, or more than
            :data:`_MAX_SEQUENTIAL_PREDICTED_BYTES` are stored under Average or Paeth.
            Both counts are taken from the tag bytes of a view over ``data``, before the
            buffer the rows are unpredicted into is allocated.
    """
    stride = plan.row_length + 1
    if len(data) % stride:
        msg = (
            f"{source} is {len(data)} bytes, which is not a whole number of "
            f"{stride}-byte PNG predictor rows"
        )
        raise InvalidPlannotationError(msg)
    # A view, not a copy: the tag bytes are counted, and both refusals made, before a
    # second buffer the size of the data is allocated to unpredict into.
    grid = np.frombuffer(data, dtype=np.uint8).reshape(-1, stride)
    tag_column = grid[:, 0]
    highest = int(tag_column.max())
    if highest > _PNG_PAETH:
        msg = f"{source} has a PNG predictor row tagged {highest}, which is not a filter type"
        raise InvalidPlannotationError(msg)
    sequential = int(np.count_nonzero((tag_column == _PNG_AVERAGE) | (tag_column == _PNG_PAETH)))
    if sequential * plan.row_length > _MAX_SEQUENTIAL_PREDICTED_BYTES:
        raise _sequential_predictor_refusal(source, highest)
    tags = tag_column.tolist()
    rows: NDArray[np.uint8] = grid[:, 1:].copy()
    previous: NDArray[np.uint8] = np.zeros(plan.row_length, dtype=np.uint8)
    for at, tag in enumerate(tags):
        row = rows[at]
        if tag == _PNG_SUB:
            _accumulate_left(row, step=plan.step)
        elif tag == _PNG_UP:
            row += previous
        elif tag == _PNG_AVERAGE:
            _png_average_row(row, previous, step=plan.step)
        elif tag == _PNG_PAETH:
            _png_paeth_row(row, previous, step=plan.step)
        previous = row
    return rows.tobytes()


def _sequential_predictor_refusal(source: str, tag: int) -> InvalidPlannotationError:
    """Build the error raised for too many bytes under an Average or Paeth predictor.

    Args:
        source: The file or packet being read, named as a reader would name it.
        tag: The PNG filter type that ran out of budget, 3 or 4.

    Returns:
        The error to raise.
    """
    name = "Average" if tag == _PNG_AVERAGE else "Paeth"
    msg = (
        f"{source} stores more than {_MAX_SEQUENTIAL_PREDICTED_BYTES} bytes under the "
        f"PNG {name} predictor, which this reader undoes one byte at a time because each "
        "reconstructed byte is an input to the next. A cap on bytes alone would bound "
        "the memory and not the time, and section 4.3 (7) asks for both. Nothing "
        "legitimately stores a JSON plannotation this way: store it unencoded, or under "
        "/FlateDecode with no predictor, or with /Predictor 12"
    )
    return InvalidPlannotationError(msg)


def _tiff_unpredict(data: bytes, plan: _PredictorPlan, *, source: str) -> bytes:
    """Undo TIFF horizontal differencing.

    Rows do not depend on one another here, and within a row the recurrence is the same
    running sum the PNG Sub filter uses, so the whole stream is one numpy pass.

    Args:
        data: The inflated bytes, one row after another with no tag between them.
        plan: The validated ``/DecodeParms``. Only eight bits per component is
            implemented: a sub-byte component would have to be unpacked and repacked, and
            no JSON payload has ever been stored that way, so refusing is honest where
            guessing would not be.
        source: The file or packet being read, for the message.

    Returns:
        The unpredicted bytes, which are the same length as the input.

    Raises:
        InvalidPlannotationError: If the components are not whole bytes, or the data is not a
            whole number of rows.
    """
    if plan.bits != _BITS_PER_BYTE:
        msg = (
            f"{source} declares a TIFF predictor over {plan.bits}-bit components, which "
            "this reader does not unpack"
        )
        raise InvalidPlannotationError(msg)
    row_length = plan.columns * plan.colors
    if len(data) % row_length:
        msg = (
            f"{source} is {len(data)} bytes, which is not a whole number of "
            f"{row_length}-byte predictor rows"
        )
        raise InvalidPlannotationError(msg)
    lanes = np.frombuffer(data, dtype=np.uint8).reshape(-1, plan.columns, plan.colors)
    return np.cumsum(lanes, axis=1, dtype=np.uint8).tobytes()


def _stated_size(stream: Object) -> int | None:
    """Return the decompressed size an embedded file stream claims for itself.

    Args:
        stream: An embedded file stream.

    Returns:
        Its ``/Params /Size``, or None when it states none or states nonsense. It is a
        claim by whoever wrote the file, so it is worth an early refusal and worth
        nothing as reassurance.
    """
    params = stream.stream_dict.get(Name.Params)
    if params is None:
        return None
    stated = cast("object", params.get(Name.Size))
    if stated is None or not isinstance(stated, int) or isinstance(stated, bool):
        return None
    return stated


def _declared_length(stream: Object) -> int | None:
    """Return the length a stream's dictionary declares for its stored bytes.

    Args:
        stream: Any stream.

    Returns:
        Its ``/Length``, or None when it declares none or declares nonsense. Every stream
        in a PDF carries one -- it is how the parser found the end of the stream -- so
        this answers "how much is stored here" without copying a byte of it. The previous
        reader answered the same question with ``len(bytes(stream.read_raw_bytes()))``,
        which copies the whole stored stream into Python first: a 256 MiB plannotation
        cost 567 MB to find out it was too large.
    """
    length = cast("object", stream.stream_dict.get(Name.Length))
    if length is None or not isinstance(length, int) or isinstance(length, bool):
        return None
    return length


def _bounded_stream_bytes(
    stream: Object, *, limit: int, source: str, advice: str = _PLANNOTATION_ADVICE
) -> bytes:
    """Decode a stream without letting the document decide how much memory to use.

    Nothing here is allocated on the strength of a number the document chose, because
    the one thing an attacker controls completely is which path is taken:

    * a ``/Params /Size`` over the bound is refused before the stream is touched;
    * a ``/Length`` over the bound is refused next. It is a claim about the stored size,
      like the one before it, and it is the answer the stream dictionary already holds:
      the previous reader copied the whole stored stream into a Python ``bytes`` object
      to measure the same thing, at 567 MB for a 256 MiB plannotation. A ``/Length``
      that understates the stream is caught by the measurement below instead;
    * stored bytes that turn out to be over the bound are refused once read. Reading them
      costs what the file itself carries -- the bytes are in the file, so a document that
      makes this expensive is a document that is itself that large -- and decoding them
      could only be the same work again;
    * an unfiltered stream is its stored bytes, already measured;
    * a single ``/FlateDecode`` has its ``/DecodeParms`` read and every one of its four
      predictor parameters checked against the format and against this bound *before*
      the row length they imply is computed, let alone allocated; the stream is then
      inflated incrementally, and the predictor applied to output already inside the
      bound;
    * any other filter chain is refused by name.

    The two early refusals are refusals on a claim, so a stream whose ``/Length`` or
    ``/Params /Size`` overstates it by enough is treated as absent although it would have
    decoded. That is section 4.3 (2) working as intended: a document that lies about its
    own sizes has said what it is.

    Args:
        stream: The stream to decode.
        limit: The most its contents may decode to, in bytes.
        source: The file or packet being read, for the message.
        advice: What to tell the reader when the bound is exceeded.

    Returns:
        The decoded contents.

    Raises:
        InvalidPlannotationError: If the contents exceed ``limit``, or the stream is stored
            under a filter chain this reader does not decode, or its ``/DecodeParms``
            are not ones it will decode, or Flate will not read it.
    """
    stated = _stated_size(stream)
    if stated is not None and stated > limit:
        raise _oversize(source, limit, measured="stated", advice=advice)
    declared = _declared_length(stream)
    if declared is not None and declared > limit:
        raise _oversize(source, limit, measured="declared", advice=advice)
    try:
        raw = bytes(stream.read_raw_bytes())
    except pikepdf.PdfError as exc:
        msg = (
            f"{source} cannot be read out of the document at all: {exc}. The stream is "
            "damaged, and section 4.3 (2) of the specification requires a plannotation "
            "a reader will not accept to be treated as absent"
        )
        raise InvalidPlannotationError(msg) from exc
    if len(raw) > limit:
        raise _oversize(source, limit, measured="stored", advice=advice)
    filters = _stream_filters(stream)
    if not filters:
        return raw
    if filters != _FLATE_ONLY:
        raise _unsupported_filter(source, filters)
    parms = _decode_parms(stream, source=source)
    data = _bounded_inflate(raw, limit=limit, source=source, advice=advice)
    return data if parms is None else _apply_predictor(data, parms, limit=limit, source=source)


# ---------------------------------------------------------------------------
# The XMP PDF Declaration
# ---------------------------------------------------------------------------
#: Matches the closing tag of the outermost RDF element, whatever prefix it carries.
_RDF_CLOSE: Final = re.compile(rb"</([A-Za-z_][\w.\-]*:)?RDF\s*>")

#: The opening tag of a ``declarations`` property, whatever prefix it carries. Group 3
#: is ``/`` for the self-closing form, which is a property that is not an array at all.
_DECLARATIONS_OPEN: Final = re.compile(rb"<([A-Za-z_][\w.\-]*:)?declarations(\s[^>]*?)?(/?)>")

#: The closing tag of a ``declarations`` property, which bounds the search for its Bag:
#: without it, a packet whose declarations property holds no array would have a member
#: spliced into whatever ``rdf:Bag`` happened to come next, in another property.
_DECLARATIONS_CLOSE: Final = re.compile(rb"</([A-Za-z_][\w.\-]*:)?declarations\s*>")

#: The opening tag of an ``rdf:Bag``. Group 3 is ``/`` for the self-closing, and
#: therefore empty, form.
_BAG_OPEN: Final = re.compile(rb"<([A-Za-z_][\w.\-]*:)?Bag(\s[^>]*?)?(/?)>")

#: The closing tag of an ``rdf:Bag``.
_BAG_CLOSE: Final = re.compile(rb"</([A-Za-z_][\w.\-]*:)?Bag\s*>")

#: The opening tag of an ``rdf:Description``, with its attributes as group 2.
_DESCRIPTION_OPEN: Final = re.compile(rb"<([A-Za-z_][\w.\-]*:)?Description(\s[^>]*?)?/?>")

#: An ``rdf:about`` attribute inside such a tag, with the quoted literal as group 1 so
#: that the value can be reused exactly as the packet spells it, quotes and all.
_ABOUT_ATTRIBUTE: Final = re.compile(rb"(?:[A-Za-z_][\w.\-]*:)?about\s*=\s*(\"[^\"]*\"|'[^']*')")

#: An expanded self-closing Bag holding nothing but Plannotation's claim, which is what
#: :func:`remove_declaration` collapses back to ``<rdf:Bag/>``. Group 1 is the original
#: opening tag without its ``>``, so restoring the packet is a matter of putting the
#: solidus back where it was.
_SOLE_MEMBER: Final = re.compile(
    rb"(<(?:[A-Za-z_][\w.\-]*:)?Bag(?:\s[^>]*?)?)>"
    + re.escape(_SOLE_DECLARATION_LI)
    + rb"</(?:[A-Za-z_][\w.\-]*:)?Bag\s*>"
)

#: The two spans of an XML document whose contents are not markup, as ``(open, close)``
#: pairs. A comment and a CDATA section are text: nothing inside either is an element,
#: an attribute or a namespace declaration, and no XMP processor sees anything in them.
#:
#: They are masked with NUL, which cannot appear in a well-formed XML document and
#: matches none of the patterns above, and they are masked byte for byte, so that every
#: offset in the masked packet is the same offset in the real one -- which is what lets a
#: splice point found in the first be applied to the second.
_MARKUP_HOLES: Final = ((b"<!--", b"-->"), (b"<![CDATA[", b"]]>"))


def _visible(packet: bytes) -> bytes:
    """Blank out every comment and CDATA section, keeping every other byte where it is.

    Every scan of a packet in this module runs over the result, and every splice is
    applied to the original at the offsets the scan found. Without this, a packet
    carrying a commented-out ``declarations`` property had Plannotation's claim spliced into
    the comment, and one whose last ``</rdf:RDF>`` was inside a trailing comment had the
    whole property written after the real end of the RDF -- and :func:`attach` reported,
    in both cases, that it had written a declaration that no XMP reader would ever see.

    Args:
        packet: The packet as the document stores it.

    Returns:
        A packet of exactly the same length with the bytes of each comment and CDATA
        section, delimiters included, replaced by NUL. An unterminated comment swallows
        the rest of the packet, which is what an XML parser would make of it too.
    """
    if b"<!" not in packet:
        return packet
    masked = bytearray(packet)
    at = 0
    while True:
        start = packet.find(b"<!", at)
        if start < 0:
            return bytes(masked)
        for opener, closer in _MARKUP_HOLES:
            if packet.startswith(opener, start):
                found = packet.find(closer, start + len(opener))
                stop = len(packet) if found < 0 else found + len(closer)
                masked[start:stop] = bytes(stop - start)
                at = stop
                break
        else:
            at = start + 2


def _metadata_stream(pdf: Pdf) -> Object | None:
    """Return the catalog's XMP metadata stream, or None.

    Args:
        pdf: An open document.

    Returns:
        The ``/Metadata`` stream object, or None when the document has no XMP.

    Raises:
        CarrierError: If ``/Metadata`` is present but is not a stream. ISO 32000-2
            requires it to be one; a document that holds something else there is
            damaged, and reading it as a stream raises whatever the PDF library
            chooses, which reaches a person as a traceback rather than as a message.
    """
    value = _catalog(pdf).get(Name.Metadata)
    if value is None:
        return None
    if not isinstance(value, pikepdf.Stream):
        msg = (
            f"the document's /Metadata is not a stream but {value!r}; ISO 32000-2 "
            "requires the catalog's XMP to be a stream, so this document is damaged"
        )
        raise CarrierError(msg)
    return value


@dataclass
class _Packet:
    """The catalog's XMP packet, read once and bounded.

    Every operation on a document asks the packet two or three questions -- is a
    declaration already there, could one be spliced in, what part of PDF/A does this
    file claim -- and the packet is attacker-controlled input like any other. Reading
    it once, through :func:`_bounded_stream_bytes`, is what keeps those questions from
    costing three unbounded decompressions.

    Attributes:
        stream: The ``/Metadata`` stream, or None when the document carries no XMP.
        data: The packet's bytes, or None when there is no packet **or** it could not
            be read inside :data:`_MAX_XMP_BYTES`. The two are told apart by
            :attr:`missing` and :attr:`unreadable`, because they mean opposite things
            to a writer: one is a document to add a packet to, the other a document to
            refuse.
    """

    stream: Object | None
    data: bytes | None

    @property
    def missing(self) -> bool:
        """Report whether the document carries no XMP at all.

        Returns:
            True when there is no ``/Metadata``.
        """
        return self.stream is None

    @property
    def unreadable(self) -> bool:
        """Report whether there is a packet that this reader would not decode.

        Returns:
            True when ``/Metadata`` is a stream whose contents exceed the bound or are
            stored under a filter chain this reader does not decode.
        """
        return self.stream is not None and self.data is None

    def write(self, updated: bytes) -> None:
        """Replace the packet's contents, in the document and in this cache.

        Args:
            updated: The new bytes.

        Raises:
            DeclarationError: If there is no stream to write to, which is a
                programming error rather than a property of the document.
        """
        if self.stream is None:
            msg = "there is no XMP packet to write to"
            raise DeclarationError(msg)
        self.stream.write(updated)
        self.data = updated


def _read_packet(pdf: Pdf, *, limit: int = _MAX_XMP_BYTES) -> _Packet:
    """Read the catalog's XMP packet once, under a bound.

    Args:
        pdf: An open document.
        limit: The most the packet may decode to.

    Returns:
        The packet. A packet over the bound, or under a filter chain this reader does
        not decode, is reported as :attr:`_Packet.unreadable` rather than raised on:
        reading metadata is not itself an operation a person asked for, and every
        caller has its own answer to a packet it cannot see.

    Raises:
        CarrierError: If ``/Metadata`` is present but is not a stream.
    """
    meta = _metadata_stream(pdf)
    if meta is None:
        return _Packet(stream=None, data=None)
    try:
        data = _bounded_stream_bytes(
            meta,
            limit=limit,
            source="the document's XMP metadata",
            advice=_PACKET_ADVICE,
        )
    except InvalidPlannotationError as exc:
        _LOGGER.warning("the document's XMP metadata will not be read: %s", exc)
        return _Packet(stream=meta, data=None)
    return _Packet(stream=meta, data=data)


def _packet_subject(visible: bytes) -> bytes:
    """Return the resource every ``rdf:Description`` in a packet describes.

    XMP Part 1 requires the ``rdf:Description`` elements of one packet to describe one
    resource, so a property added under a different subject is not another property of
    the same document -- it is a statement about something else, which no reader of the
    first subject will see.

    Args:
        visible: The existing XMP packet, masked by :func:`_visible`. A description
            inside a comment describes nothing, so it must not decide the subject.

    Returns:
        The ``rdf:about`` of the first ``rdf:Description`` that carries one, as the
        quoted literal the packet spells it with, so that reusing it cannot change how
        it is escaped. ``""`` when no element carries one, which is both the commonest
        case and the right default: an absent ``rdf:about`` means the empty subject.
    """
    for description in _DESCRIPTION_OPEN.finditer(visible):
        attributes = description.group(2)
        if attributes is None:
            continue
        about = _ABOUT_ATTRIBUTE.search(attributes)
        if about is not None:
            return about.group(1)
    return b'""'


def _declaration_block(visible: bytes) -> bytes:
    """Return the whole ``pdfd:declarations`` property as it goes into one packet.

    Args:
        visible: The packet the property is for, masked by :func:`_visible`, which
            decides the subject it is written under.

    Returns:
        The bytes to insert. They are computed rather than constant because of the
        subject, and :func:`remove_declaration` computes them the same way from the
        packet it is taking them out of -- the subject it finds is the one the packet
        already used, which is the one the block was written with.
    """
    return _DECLARATION_BLOCK_HEAD + _packet_subject(visible) + _DECLARATION_BLOCK_TAIL


def _declarations_splice(visible: bytes) -> tuple[int, int, bytes] | None:
    """Find where Plannotation's claim joins a ``declarations`` property that already exists.

    Args:
        visible: The existing XMP packet, masked by :func:`_visible`. A commented-out
            declarations property is not one, and splicing a claim into it wrote a
            declaration no XMP reader would ever see.

    Returns:
        The span to replace and what to replace it with, or None when the packet
        carries no ``declarations`` property and the caller should write the whole
        thing. The span is empty for the ordinary case -- one more member before the
        ``rdf:Bag``'s closing tag -- and covers the tag itself for a self-closing, and
        therefore empty, Bag, which has no closing tag to splice before and is expanded
        instead.

        The scan tracks nesting, because a ``pdfd:claimData`` inside a declaration is
        itself an ``rdf:Bag``; it is bounded by the property's own closing tag, so that
        a packet whose declarations property holds no array cannot have a member
        spliced into some other property's Bag; it ignores comments and CDATA, because
        neither holds markup; and it does not parse the packet as XML, because the
        packet is attacker-controlled (section 9).

    Raises:
        DeclarationError: If there is a ``declarations`` property but it is not an
            ``rdf:Bag`` that can be joined. Section 6.3.3 of the specification makes
            the array required and its type fixed, so such a packet is malformed before
            Plannotation touches it; adding a second ``declarations`` property beside it
            would make it invalid XMP as well, and rewriting another producer's
            metadata is not this module's to do.
    """
    if NS_PDFD.encode() not in visible:
        return None
    opened = _DECLARATIONS_OPEN.search(visible)
    if opened is None:
        return None
    if opened.group(3):
        msg = (
            "the document's XMP metadata carries a self-closing declarations property, "
            "which holds no rdf:Bag and is therefore not a PDF Declaration (section "
            "6.3.3). Plannotation will not add a second declarations property beside it, "
            "because a subject holds one property of a given name and no more, and "
            "will not rewrite another producer's packet to repair it"
        )
        raise DeclarationError(msg)
    closed = _DECLARATIONS_CLOSE.search(visible, opened.end())
    if closed is None:
        msg = (
            "the document's XMP metadata opens a declarations property and never "
            "closes it, so the file is damaged and there is nowhere to add a claim"
        )
        raise DeclarationError(msg)
    return _bag_splice(visible, opened.end(), closed.start())


def _bag_splice(visible: bytes, start: int, end: int) -> tuple[int, int, bytes]:
    """Find where a member joins the ``rdf:Bag`` inside a ``declarations`` property.

    Args:
        visible: The existing XMP packet, masked by :func:`_visible`. Every byte this
            function reads lies inside a real declarations property, so it is the same
            byte in the packet itself, and the offsets it returns are offsets into both.
        start: The offset just after the property's opening tag.
        end: The offset of the property's closing tag, which bounds every search.

    Returns:
        The span to replace and what to replace it with.

    Raises:
        DeclarationError: If the property holds no ``rdf:Bag``, or one that is never
            closed.
    """
    opened = _BAG_OPEN.search(visible, start, end)
    if opened is None:
        msg = (
            "the document's XMP metadata carries a declarations property that holds no "
            "rdf:Bag; section 6.3.3 of the specification requires the declarations to "
            "be an unordered array, so this packet is malformed and Plannotation will not "
            "rewrite it"
        )
        raise DeclarationError(msg)
    if opened.group(3):
        tag = visible[opened.start() : opened.end()]
        prefix = opened.group(1) or b""
        expanded = tag[:-2] + b">" + _SOLE_DECLARATION_LI + b"</" + prefix + b"Bag>"
        return opened.start(), opened.end(), expanded
    depth = 1
    at = opened.end()
    while True:
        closed = _BAG_CLOSE.search(visible, at, end)
        if closed is None:
            msg = (
                "the document's XMP metadata opens an rdf:Bag inside its declarations "
                "property and never closes it, so the file is damaged"
            )
            raise DeclarationError(msg)
        nested = _BAG_OPEN.search(visible, at, end)
        if nested is not None and nested.start() < closed.start():
            depth += 0 if nested.group(3) else 1
            at = nested.end()
            continue
        depth -= 1
        if depth == 0:
            return closed.start(), closed.start(), _DECLARATION_LI
        at = closed.end()


def _declaration_splice(packet: bytes) -> tuple[int, int, bytes]:
    """Decide what to write into a packet, and where.

    Splicing bytes is what keeps the rest of the packet untouched. Parsing the packet
    and writing it out again would preserve every property but normalise attribute
    order across the whole document, so a third party's packet would not come back as
    it went in -- which section 1.2 (2) of the specification forbids.

    Args:
        packet: The existing XMP packet.

    Returns:
        The span to replace, and the bytes to replace it with. A packet that already
        carries a ``declarations`` array gets one more member inside that array's
        ``rdf:Bag``, because a subject holds one property of a given name and no more.
        A packet that carries none gets the whole property, in a self-contained
        ``rdf:Description`` that edits no existing element and describes the subject
        the packet already describes.

        Every scan runs over the masked packet and every offset returned is an offset
        into the real one, which is the same number: a comment is markup to nobody, so
        the last ``</rdf:RDF>`` inside one is not the last ``</rdf:RDF>``.

    Raises:
        DeclarationError: If the packet has no RDF element to splice into, or carries a
            ``declarations`` property that cannot be joined.
    """
    visible = _visible(packet)
    existing = _declarations_splice(visible)
    if existing is not None:
        return existing
    matches = list(_RDF_CLOSE.finditer(visible))
    if not matches:
        msg = (
            "the document's XMP metadata contains no closing rdf:RDF element outside a "
            "comment, so the PDF Declaration cannot be added without rewriting the "
            "packet; the file is probably damaged"
        )
        raise DeclarationError(msg)
    at = matches[-1].start()
    return at, at, _declaration_block(visible)


def _splice_declaration(packet: bytes) -> bytes:
    """Add Plannotation's claim to an XMP packet, leaving every other byte alone.

    Args:
        packet: The existing XMP packet.

    Returns:
        The packet with the claim added: as one more member of an existing
        ``pdfd:declarations`` array where there is one, as the sole member of an empty
        one that has been expanded to hold it, and as a whole new property where the
        packet carries no declarations at all.

    Raises:
        DeclarationError: If the packet cannot be spliced into.
    """
    start, end, block = _declaration_splice(packet)
    return packet[:start] + block + packet[end:]


def _without_declaration(packet: bytes) -> bytes | None:
    """Take exactly the bytes Plannotation added back out of a packet.

    The whole property is looked for first, because it contains the member verbatim.
    It is looked for under the subject the packet now describes and, failing that,
    under the empty subject, so that a packet which has gained an ``rdf:about`` since it
    was plannotated is still restored rather than left with a property nothing removes.
    The sole member comes next, and takes the ``rdf:Bag`` that was expanded to hold it
    back to the self-closing spelling it had before.

    Every search runs over the masked packet, for the same reason writing does: a copy
    of Plannotation's claim sitting inside somebody else's comment is text, not a claim,
    and deleting it would be editing another producer's metadata.

    Args:
        packet: The packet to restore.

    Returns:
        The packet without Plannotation's claim, or None when it carries none that this
        implementation wrote.
    """
    visible = _visible(packet)
    for subject in (_packet_subject(visible), b'""'):
        block = _DECLARATION_BLOCK_HEAD + subject + _DECLARATION_BLOCK_TAIL
        at = visible.find(block)
        if at >= 0:
            return packet[:at] + packet[at + len(block) :]
    sole = visible.find(_SOLE_DECLARATION_LI)
    if sole >= 0:
        collapsed = _SOLE_MEMBER.search(visible)
        if collapsed is not None:
            opening = packet[collapsed.start(1) : collapsed.end(1)]
            return packet[: collapsed.start()] + opening + b"/>" + packet[collapsed.end() :]
        _LOGGER.debug(
            "the rdf:Bag Plannotation expanded has been re-serialised by another tool; "
            "removing the member and leaving the Bag as it now stands",
        )
        return packet[:sole] + packet[sole + len(_SOLE_DECLARATION_LI) :]
    member = visible.find(_DECLARATION_LI)
    if member >= 0:
        return packet[:member] + packet[member + len(_DECLARATION_LI) :]
    return None


def _declared(packet: _Packet) -> bool:
    """Report whether a packet carries a claim naming the Plannotation specification.

    Args:
        packet: The packet, as :func:`_read_packet` returned it.

    Returns:
        True when the bytes carry a ``pdfd:conformsTo`` naming
        :data:`plannotation.constants.SPEC_URI` outside a comment or a CDATA section. A
        packet that was not read is not a packet that declares anything, so it reads as
        False -- and every caller that would go on to write is stopped by
        :func:`_can_splice` first.
    """
    data = packet.data
    if data is None:
        return False
    visible = _visible(data)
    return NS_PDFD.encode() in visible and bool(_CONFORMS_TO.search(visible))


def has_declaration(pdf: Pdf) -> bool:
    """Report whether the document declares conformance to the Plannotation spec.

    The test is deliberately tolerant: it recognises the claim whatever XML prefix it
    was written with and however it is spaced, so a declaration written by another
    implementation, or one that has survived being re-serialised by another tool, is
    still found. It does not parse the packet as XML, because the packet is
    attacker-controlled and this is a yes-or-no question (section 9).

    Args:
        pdf: An open document.

    Returns:
        True when the catalog's XMP carries a ``pdfd:conformsTo`` naming
        :data:`plannotation.constants.SPEC_URI`.
    """
    return _declared(_read_packet(pdf))


def add_declaration(pdf: Pdf) -> bool:
    """Add the XMP PDF Declaration, creating a metadata packet only if there is none.

    This is the only metadata a conforming writer adds, and nothing else in the
    packet is touched. It is idempotent: a document that already declares Plannotation
    conformance is left exactly as it is.

    Args:
        pdf: An open document, modified in place.

    Returns:
        True if a declaration was written, False if one was already there.

    Raises:
        DeclarationError: If an existing packet cannot be spliced into, or could not be
            read.
    """
    return _add_declaration(pdf, _read_packet(pdf))


def _add_declaration(pdf: Pdf, packet: _Packet) -> bool:
    """Add the declaration to a packet that has already been read.

    Args:
        pdf: An open document, modified in place.
        packet: Its XMP packet, from :func:`_read_packet`.

    Returns:
        True if a declaration was written, False if one was already there.

    Raises:
        DeclarationError: If the packet could not be read or cannot be spliced into.
    """
    if packet.missing:
        stream = pdf.make_stream(_splice_declaration(_EMPTY_PACKET))
        stream.stream_dict[Name.Type] = Name.Metadata
        stream.stream_dict[Name.Subtype] = Name.XML
        _catalog(pdf)[Name.Metadata] = pdf.make_indirect(stream)
        _LOGGER.debug("added an XMP packet carrying the Plannotation declaration")
        return True
    if packet.data is None:
        raise _unreadable_packet()
    if _declared(packet):
        _LOGGER.debug("the document already declares Plannotation conformance")
        return False
    packet.write(_splice_declaration(packet.data))
    return True


def _unreadable_packet() -> DeclarationError:
    """Build the error raised for a document whose XMP will not be read.

    Returns:
        The error. A writer that carried on here would produce a plannotated document
        with no declaration in it, which section 6.2.1 (4) requires and which a reader
        looking only at metadata would never find; and it would have to decide what to
        do with a packet it never saw. Refusing says both things at once.
    """
    msg = (
        "the document's XMP metadata could not be read inside the bound this reader "
        f"places on it ({_MAX_XMP_BYTES} bytes), so the PDF Declaration of section 6.3 "
        "cannot be added to it and Plannotation will not write a plannotated document "
        "without one. The packet is either damaged or hostile: a real one is a few "
        "kilobytes. Write a sidecar instead (`plannotation sidecar this.pdf "
        "--plannotations plannotations.json`), which does not touch the document at all"
    )
    return DeclarationError(msg)


def _unstrippable_packet() -> DeclarationError:
    """Build the error raised when :func:`strip` meets a packet it will not read.

    Returns:
        The error. :func:`attach` has always refused a packet it cannot read, and
        :func:`strip` used to read one, find it unreadable, remove nothing, say so --
        and save the document anyway. That is a bound on reading and not on writing, and
        the two are not the same bound: qpdf writes a ``/Type /Metadata`` stream
        uncompressed whatever it found, so saving a document whose packet this reader
        declined to decompress is the one operation that spends exactly what declining
        to decompress it saved. A 522 KB input produced a 536,871,752-byte output.

        The deeper reason is the one :func:`attach` already gave: a tool that promises
        not to alter what it did not write cannot keep that promise over a packet it
        cannot see.
    """
    msg = (
        "the document's XMP metadata could not be read inside the bound this reader "
        f"places on it ({_MAX_XMP_BYTES} bytes), so Plannotation will not save a copy of "
        "this document. Saving rewrites every stream in the file, including the packet "
        "-- uncompressed, because that is how a /Type /Metadata stream is written -- so "
        "a packet this reader declines to decompress is one it would decompress into "
        "the output. Nothing has been removed and nothing has been written. The packet "
        "is either damaged or hostile: a real one is a few kilobytes"
    )
    return DeclarationError(msg)


def _can_splice(packet: _Packet) -> None:
    """Refuse up front if the declaration could not be written at the end.

    :func:`attach_in_place` writes the embedded files, the ``/AF`` arrays and the name
    tree before it adds the declaration. Discovering only then that the packet cannot
    take one would leave the caller's open document half-plannotated, so the question is
    asked first, beside the other refusals and before anything is written.

    Args:
        packet: The document's XMP packet, from :func:`_read_packet`.

    Raises:
        DeclarationError: If the packet could not be read, or cannot be spliced into.
    """
    if packet.missing:
        return
    if packet.data is None:
        raise _unreadable_packet()
    if _declared(packet):
        return
    _declaration_splice(packet.data)


def _pdfa_status(packet: _Packet) -> tuple[int | None, bool]:
    """Report the part of PDF/A a document claims, and whether the declaration risks it.

    Parts 1, 2 and 3 of ISO 19005 require an XMP extension schema for every property
    from a namespace they do not define, and ``pdfd:declarations`` is such a property.
    A file that claims one of those parts and carries no schema describing the PDF
    Declarations namespace is no longer conforming once a declaration is added -- which
    matters here in particular, because this module declines to raise the PDF version
    precisely in order to protect a PDF/A-3 claim. Part 4 dropped extension schemas and
    is unaffected.

    Args:
        packet: The document's XMP packet, from :func:`_read_packet`.

    Returns:
        The claimed part, or None when the document claims none, and whether a PDF
        Declaration in this packet would go undescribed.
    """
    data = packet.data
    if data is None:
        return None, False
    visible = _visible(data)
    if NS_PDFAID.encode() not in visible:
        return None, False
    found = _PDFA_PART.search(visible)
    if found is None:
        return None, False
    part = int(found.group(1) or found.group(2))
    if part not in _PDFA_EXTENSION_PARTS:
        return part, False
    described = NS_PDFA_EXTENSION.encode() in visible and bool(
        _PDFA_SCHEMA_FOR_PDFD.search(visible)
    )
    return part, not described


def remove_declaration(pdf: Pdf) -> bool:
    """Remove exactly the declaration Plannotation writes, and nothing else.

    The removed bytes are the ones :func:`add_declaration` inserts -- the whole
    property where Plannotation created it, or the single array member where Plannotation
    added one to a Bag another producer wrote, or that member and the expansion of the
    empty Bag it was written into. Either way the packet is restored byte for byte,
    including that producer's own PDF Declaration sitting beside ours in the same
    array.

    A declaration made by some other implementation of Plannotation, or one that another
    tool has since re-serialised, is left alone: taking it out would mean rewriting
    another producer's packet, which is a larger sin than leaving a claim behind.
    :func:`has_declaration` still reports it, and :func:`strip_in_place` says so.

    Args:
        pdf: An open document, modified in place.

    Returns:
        True if the declaration was found and removed.
    """
    return _remove_declaration(_read_packet(pdf))


def _remove_declaration(packet: _Packet) -> bool:
    """Remove the declaration from a packet that has already been read.

    Args:
        packet: The document's XMP packet, from :func:`_read_packet`. Its cached bytes
            are updated in place, so that a caller may go on asking questions of it.

    Returns:
        True if the declaration was found and removed.
    """
    if packet.data is None:
        if packet.unreadable:
            _LOGGER.warning(
                "the document's XMP metadata was not read, so any Plannotation "
                "declaration in it has been left where it is",
            )
        return False
    updated = _without_declaration(packet.data)
    if updated is None:
        return False
    packet.write(updated)
    return True


def _drop_plannotation_packet(pdf: Pdf, packet: _Packet) -> bool:
    """Delete an XMP packet that Plannotation created and that now says nothing.

    A document that had no metadata before being plannotated must have none after being
    stripped: an empty packet is a trace of Plannotation, and the point of
    :func:`strip` is to leave none. Only a packet byte-identical to the one
    :func:`add_declaration` synthesises is removed, so a packet anything else has
    written to is never touched.

    Args:
        pdf: An open document, modified in place.
        packet: Its XMP packet, from :func:`_read_packet`, after the declaration has
            been removed.

    Returns:
        True if the metadata stream was removed.
    """
    if packet.data != _EMPTY_PACKET:
        return False
    del _catalog(pdf)[Name.Metadata]
    packet.stream, packet.data = None, None
    _LOGGER.debug("removed the empty XMP packet Plannotation had created")
    return True


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------
def _embed_file(
    pdf: Pdf,
    data: bytes,
    *,
    filename: str,
    description: str,
    mod_date: str,
    compress: bool,
) -> Object:
    """Embed one file and register it in the ``EmbeddedFiles`` name tree.

    pikepdf writes every member the format requires -- ``/Type /Filespec``, ``/F``,
    ``/UF``, ``/Desc``, ``/EF`` with ``/F`` and ``/UF`` pointing at one shared
    stream, and on the stream ``/Type /EmbeddedFile``, ``/Subtype`` holding the MIME
    type and ``/Params`` with ``/Size``, ``/CheckSum`` and ``/ModDate``. Only
    ``/AFRelationship`` is set here, because pikepdf's typed signature does not
    accept it and omitting it would silently write ``/Unspecified``.

    ``/Params /Size`` and ``/CheckSum`` are computed once, at construction, from the
    plaintext, and are never recomputed. That is why the stream is compressed
    afterwards rather than before: the checksum stays the MD5 of the plannotation as the
    format requires, while the stored bytes are small enough that the whole document
    can be saved without compressing anything else.

    Args:
        pdf: The document to embed into.
        data: The final bytes of the file. They must not be mutated afterwards.
        filename: The name in the name tree, in ``/F`` and in ``/UF``. Kept identical
            in all three so that a reader matching on either finds the same file.
        description: The ``/Desc``, which is what a viewer's attachment pane shows.
        mod_date: A PDF date string from :func:`pdf_date`.
        compress: Whether to store the stream Flate-encoded.

    Returns:
        The file specification dictionary, as an indirect object that the ``/AF``
        arrays can share with the name tree.
    """
    spec = pikepdf.AttachedFileSpec(
        pdf,
        data,
        description=description,
        filename=filename,
        mime_type=PLANNOTATION_MIME_TYPE,
        creation_date="",
        mod_date=mod_date,
    )
    pdf.attachments[filename] = spec
    registered = pdf.attachments[filename].obj
    registered[Name.AFRelationship] = Name.Data
    if compress:
        registered[Name.EF][Name.F].write(zlib.compress(data, 9), filter=Name.FlateDecode)
    return registered


def _append_af(owner: Object, spec: Object) -> None:
    """Add a file specification to an ``/AF`` array, appending rather than replacing.

    A page or a catalog may already carry another producer's associated files -- a
    Factur-X invoice does, on both -- and assigning the array would silently drop
    them while leaving them in the name tree.

    Args:
        owner: The page or catalog dictionary to associate the file with.
        spec: The file specification to add.

    Raises:
        CarrierError: If the existing ``/AF`` is not an array.
    """
    entries = _af_entries(owner)
    if not entries:
        owner[Name.AF] = Array([spec])
        return
    for entry in entries:
        if _identity(entry) == spec.objgen:
            return
    array = owner[Name.AF]
    array.append(spec)


def _page_description(page_index: int) -> str:
    """Return the ``/Desc`` of the embedded file holding a page's plannotation.

    The page number is the zero-based PDF page index, the same number that appears in
    the filename and in the plannotation's own ``page.index``. The design brief does not
    say which counting to use; one of the two has to be chosen, and a description that
    disagreed with the filename beside it in the attachment pane would be worse than
    one that counts from zero.

    Args:
        page_index: The zero-based page index.

    Returns:
        The description text.
    """
    return f"Plannotation {SCHEMA_VERSION} for page {page_index}"


def save_plannotated(pdf: Pdf, path: Path | str) -> None:
    """Save a document with every option that could disturb it pinned.

    Four of pikepdf's defaults would break a promise this module makes, so all of
    them are passed explicitly rather than inherited:

    * ``fix_metadata_version=False`` -- the default re-parses the XMP packet to
      correct ``pdf:PDFVersion``, which rewrites metadata another producer placed.
    * ``normalize_content=False`` -- the default rewrites content streams. It changes
      no pixels, which is exactly why the appearance test cannot catch it.
    * ``compress_streams=False`` -- the default Flate-encodes a page's previously
      uncompressed content stream, so its raw bytes change although its meaning does
      not. Streams that arrived compressed stay compressed either way, and the
      plannotation streams are compressed by hand, so this costs almost nothing.
    * ``deterministic_id=True`` -- the default ``/ID`` is seeded from the clock, and
      two identical runs would produce different files.

    The PDF version is deliberately left alone. Page-level ``/AF`` is a PDF 2.0
    feature, but raising the header of a PDF/A-3 file that declares PDF 1.7 would
    break its conformance claim, and to an older reader ``/AF`` is simply a key it
    does not know.

    This function is the primitive, not the policy. Saving is where a document's own
    bound is spent -- qpdf rewrites every stream, and writes a ``/Type /Metadata``
    stream uncompressed whatever it found -- so the refusal belongs to the operations
    that decide to save, before they have changed anything. There are exactly two,
    :func:`attach` and :func:`strip`, and both refuse a document whose XMP packet they
    could not read inside :data:`_MAX_XMP_BYTES`.

    Args:
        pdf: The document to write.
        path: Where to write it.
    """
    pdf.save(
        path,
        deterministic_id=True,
        compress_streams=False,
        normalize_content=False,
        fix_metadata_version=False,
        preserve_pdfa=True,
        object_stream_mode=pikepdf.ObjectStreamMode.preserve,
    )


# ---------------------------------------------------------------------------
# Building an index and a sidecar from plannotations
# ---------------------------------------------------------------------------
def _shared_model(plannotations: Sequence[Plannotation]) -> Model | None:
    """Return the source model every plannotation agrees on, if there is one.

    Args:
        plannotations: The plannotations of one document.

    Returns:
        The common :class:`~plannotation.model.Model`, or None when the plannotations
        name different models or none at all. Nothing is merged or guessed: a document
        whose pages came from two models has no single source model, and saying so is
        the truthful answer.
    """
    models = [
        plannotation.source_model
        for plannotation in plannotations
        if plannotation.source_model is not None
    ]
    if len(models) != len(plannotations) or not models:
        return None
    first = canonical_json(models[0])
    return models[0] if all(canonical_json(model) == first for model in models[1:]) else None


def build_index(
    plannotations: Iterable[Plannotation],
    *,
    generator: Generator | None = None,
    with_filenames: bool = True,
) -> PlannotationIndex:
    """Derive a document-level index from the plannotations it describes.

    Nothing is invented. Every value is copied from a plannotation: the sheet number,
    title and revision from ``sheet``, the conformance level computed from the
    plannotation itself, the filename from the page index, the provenance aggregated
    over the pages exactly as section 4.6.4 of the specification requires, and the
    source model only where every page agrees on one.

    Args:
        plannotations: The plannotations of one document, in any order.
        generator: The tool to record as having written the index, if any.
        with_filenames: Whether to record each entry's embedded filename. True for a
            PDF, where the file exists; False for a sidecar, where naming a file that
            is not there would mislead a reader.

    Returns:
        An index listing every plannotation given, ascending by page index.
    """
    ordered = sorted(plannotations, key=lambda plannotation: plannotation.page.index)
    entries = [
        IndexPage(
            pageIndex=plannotation.page.index,
            sheetId=plannotation.sheet.sheet_id,
            title=plannotation.sheet.title,
            revision=plannotation.sheet.revision,
            level=conformance_level(plannotation),
            file=plannotation_filename(plannotation.page.index) if with_filenames else None,
        )
        for plannotation in ordered
    ]
    provenance = aggregate_provenance(plannotation.provenance for plannotation in ordered)
    return PlannotationIndex(
        plannotation=SCHEMA_VERSION,
        generator=generator,
        provenance=provenance,
        model=_shared_model(ordered),
        pages=entries,
    )


def build_sidecar(
    plannotations: Iterable[Plannotation],
    index: PlannotationIndex | None = None,
    *,
    generator: Generator | None = None,
) -> Sidecar:
    """Assemble the sidecar document for one PDF.

    Args:
        plannotations: Every plannotation of the document.
        index: The document's index. When None, one is derived from the plannotations
            with :func:`build_index`, since a program holding every plannotation can
            always say what is plannotated.
        generator: The tool to record as having written the sidecar file.

    Returns:
        The sidecar, with its pages ascending by page index so that two runs over the
        same plannotations produce the same bytes.
    """
    ordered = sorted(plannotations, key=lambda plannotation: plannotation.page.index)
    return Sidecar(
        plannotation=SCHEMA_VERSION,
        generator=generator,
        index=index if index is not None else build_index(ordered, with_filenames=False),
        pages=ordered,
    )


def sidecar_path(document: Path | str) -> Path:
    """Return the sidecar path for a document.

    Args:
        document: The document the sidecar stands beside.

    Returns:
        The document's path with its extension replaced by
        :data:`plannotation.constants.SIDECAR_SUFFIX`, so that ``drawings/TWP-101.pdf``
        becomes ``drawings/TWP-101.plannotation.json``.
    """
    path = _as_path(document)
    return path.with_name(path.stem + SIDECAR_SUFFIX)


# ---------------------------------------------------------------------------
# attach
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AttachReport:
    """What :func:`attach` wrote.

    Attributes:
        page_indices: The zero-based pages that were plannotated, ascending.
        filenames: The embedded files that were written, in the order written, the
            index last when there is one.
        index_written: Whether a document-level index was embedded.
        declaration_added: Whether the XMP PDF Declaration was added. False when the
            document already carried one.
        pdf_version: The document's PDF version, unchanged by plannotating.
        pdfa_part: The part of PDF/A the input claimed conformance to, or None when it
            claimed none. Plannotating never changes it.
        pdfa_extension_schema_missing: Whether the output's PDF Declaration is a
            property that the document's PDF/A part requires an XMP extension schema
            for and does not have. True only for parts 1, 2 and 3; part 4 dropped
            extension schemas. A caller that cares about the conformance claim should
            act on this -- Plannotation does not splice the PDF Association's schema in.
    """

    page_indices: tuple[int, ...]
    filenames: tuple[str, ...]
    index_written: bool
    declaration_added: bool
    pdf_version: str
    pdfa_part: int | None
    pdfa_extension_schema_missing: bool


def _plannotations_by_page(plannotations: Iterable[Plannotation]) -> dict[int, Plannotation]:
    """Key the plannotations by the page each one claims.

    Args:
        plannotations: The plannotations to attach.

    Returns:
        The plannotations keyed by ``page.index``, which is where each one goes. The
        page a plannotation describes is stated in the plannotation, so a caller cannot
        staple a plannotation to the wrong page by passing the list in the wrong order.

    Raises:
        ValueError: If there are no plannotations at all.
        PlannotationMismatchError: If two plannotations claim the same page.
    """
    by_page: dict[int, Plannotation] = {}
    for plannotation in plannotations:
        page_index = plannotation.page.index
        if page_index in by_page:
            msg = (
                f"two plannotations claim page {page_index}; a page carries at most one "
                "plannotation, and page.index is what says which page a plannotation "
                "belongs to"
            )
            raise PlannotationMismatchError(msg)
        by_page[page_index] = plannotation
    if not by_page:
        msg = "no plannotations were given, so there is nothing to attach"
        raise ValueError(msg)
    return by_page


def check_plannotations_against(
    pdf: Pdf, plannotations: Iterable[Plannotation]
) -> dict[int, Plannotation]:
    """Check a set of plannotations against the document they describe.

    This is the check :func:`attach` makes before it writes anything, exposed so that
    a writer which is not going to touch the document -- :func:`write_sidecar` on a
    signed PDF, say -- can make exactly the same one.

    Args:
        pdf: The document the plannotations describe.
        plannotations: The plannotations. Each describes the page its ``page.index``
            names.

    Returns:
        The plannotations keyed by page index.

    Raises:
        ValueError: If there are no plannotations at all.
        PlannotationMismatchError: If two plannotations claim one page, or a plannotation
            names a page the document does not have, or its dimensions or rotation
            disagree with that page.
    """
    by_page = _plannotations_by_page(plannotations)
    _check_pages(pdf, by_page)
    return by_page


def _check_pages(pdf: Pdf, by_page: dict[int, Plannotation]) -> None:
    """Check every plannotation against the page it claims.

    A plannotation that misstates its page is worse than no plannotation: a reader that
    trusts it reads geometry against the wrong sheet. Rather than write one,
    :func:`attach` refuses and says which number disagrees with which.

    Args:
        pdf: The document being plannotated.
        by_page: The plannotations, keyed by the page each claims.

    Raises:
        PlannotationMismatchError: If a plannotation names a page the document does not
            have, or its page dimensions differ from that page's by more than
            :data:`PAGE_DIMENSION_TOLERANCE_MM`, or its rotation differs from the
            page's ``/Rotate``.
    """
    page_count = len(pdf.pages)
    for page_index, plannotation in sorted(by_page.items()):
        if page_index >= page_count:
            msg = (
                f"the plannotation for page {page_index} cannot be attached to a document "
                f"of {page_count} page(s); page.index is a zero-based index into this "
                "document"
            )
            raise PlannotationMismatchError(msg)
        geometry = page_geometry(pdf, page_index)
        _check_one_page(plannotation, geometry)


def _check_one_page(plannotation: Plannotation, geometry: PageGeometry) -> None:
    """Compare one plannotation's page block with the page itself.

    Args:
        plannotation: The plannotation being attached.
        geometry: The measured page, from :func:`page_geometry`.

    Raises:
        PlannotationMismatchError: If the dimensions or the rotation disagree.
    """
    index = geometry.page_index
    width_off = abs(plannotation.page.width_mm - geometry.width_mm)
    height_off = abs(plannotation.page.height_mm - geometry.height_mm)
    if max(width_off, height_off) > PAGE_DIMENSION_TOLERANCE_MM:
        swapped = (
            " -- the plannotation's width and height look swapped, which is what happens"
            " when a rotated page is measured as it is displayed rather than as it is stored"
            if abs(plannotation.page.width_mm - geometry.height_mm) <= PAGE_DIMENSION_TOLERANCE_MM
            and abs(plannotation.page.height_mm - geometry.width_mm) <= PAGE_DIMENSION_TOLERANCE_MM
            else ""
        )
        msg = (
            f"the plannotation for page {index} says the page is "
            f"{plannotation.page.width_mm:g} x {plannotation.page.height_mm:g} mm, but its "
            f"{geometry.box_source} is {geometry.width_mm:g} x {geometry.height_mm:g} mm "
            f"(/UserUnit {geometry.user_unit:g}); paper dimensions are unrotated and "
            f"measured from the displayed box{swapped}"
        )
        raise PlannotationMismatchError(msg)
    if plannotation.page.effective_rotation != geometry.rotation:
        msg = (
            f"the plannotation for page {index} records rotation "
            f"{plannotation.page.effective_rotation}, but the page's /Rotate is "
            f"{geometry.rotation}"
        )
        raise PlannotationMismatchError(msg)


def _check_index(
    index: PlannotationIndex, by_page: dict[int, Plannotation], *, with_filenames: bool = True
) -> None:
    """Check that the index describes the plannotations being written.

    Args:
        index: The index to write.
        by_page: The plannotations, keyed by page.
        with_filenames: Whether an entry's ``file`` must name the embedded file the
            plannotation will be stored in. True for a PDF, where that file exists; False
            for a sidecar, which embeds nothing, so a filename in its index describes some
            other carrier rather than contradicting this one. This is the same
            distinction :func:`build_index` makes.

    Raises:
        PlannotationMismatchError: If the index lists a page that is not being
            plannotated, or names an embedded file that will not exist, or records a
            conformance level that the plannotation does not reach.
    """
    for entry in index.pages:
        plannotation = by_page.get(entry.page_index)
        if plannotation is None:
            listed = ", ".join(str(page) for page in sorted(by_page))
            msg = (
                f"the index lists page {entry.page_index}, but no plannotation was given "
                f"for it; plannotations were given for page(s) {listed}"
            )
            raise PlannotationMismatchError(msg)
        expected_file = plannotation_filename(entry.page_index)
        if with_filenames and entry.file is not None and entry.file != expected_file:
            msg = (
                f"the index says page {entry.page_index}'s plannotation is in "
                f"{entry.file!r}, but it will be embedded as {expected_file!r}"
            )
            raise PlannotationMismatchError(msg)
        reached = conformance_level(plannotation)
        if entry.level != reached:
            msg = (
                f"the index records page {entry.page_index} as {entry.level.value}, but "
                f"its plannotation reaches {reached.value}"
            )
            raise PlannotationMismatchError(msg)
    missing = sorted(set(by_page) - {entry.page_index for entry in index.pages})
    if missing:
        listed = ", ".join(str(page) for page in missing)
        _LOGGER.warning(
            "the index does not list page(s) %s although a plannotation is being attached "
            "to each; readers that consult only the index will not find them",
            listed,
        )


def _plannotation_spec_names(key: str, spec: Object) -> list[str]:
    """Return every Plannotation-owned name one name-tree entry carries.

    This is the single predicate that decides both what :func:`attach` refuses to
    plannotate over and what :func:`strip` removes, and it is one function because the
    two must not drift apart. When they did, a file specification whose ``/UF`` was a
    Plannotation name but which was filed under some other key was refused by
    :func:`attach` and left behind by :func:`strip`, so the document could be neither
    plannotated nor repaired and the refusal's advice -- run ``plannotation strip`` --
    was false.

    Args:
        key: The name-tree key the specification is filed under.
        spec: The file specification itself.

    Returns:
        The key and the specification's ``/UF`` and ``/F``, keeping only those that are
        names Plannotation owns. Empty for a file that is none of Plannotation's business.
    """
    return [name for name in (key, *_spec_names(spec)) if is_plannotation_filename(name)]


def _is_plannotation_spec(spec: Object) -> bool:
    """Report whether a file specification names a file Plannotation owns.

    Args:
        spec: A ``/Filespec`` dictionary, as an ``/AF`` array references one. It has no
            name-tree key, because an ``/AF`` array is not a name tree.

    Returns:
        True when either of its filenames is a Plannotation name.
    """
    return bool(_plannotation_spec_names("", spec))


def _existing_plannotation_files(pdf: Pdf) -> dict[str, set[str]]:
    """Find every Plannotation-owned filename the document already carries, and where.

    Both registrations are scanned, because both are places a plannotation lives and
    :func:`read_pdf` consults both. A document whose plannotation files are reachable
    only from ``/AF`` -- which is what is left after a tool rebuilds the name tree --
    reads perfectly well, so it is a document that is already plannotated.

    Args:
        pdf: The document being plannotated.

    Returns:
        Each Plannotation-owned filename found, mapped to the places it was found in.
    """
    found: dict[str, set[str]] = {}

    def note(name: str, where: str) -> None:
        found.setdefault(name, set()).add(where)

    # One walk of the name tree. Looking each key up again inside the loop would be
    # quadratic -- pikepdf resolves a key by walking the tree -- and the number of
    # attachments is chosen by the document.
    for raw_key, attached in pdf.attachments.items():
        key = str(raw_key)
        spec = attached.obj
        if is_plannotation_filename(key):
            note(key, "the EmbeddedFiles name tree")
        for name in _plannotation_spec_names(key, spec):
            if name != key:
                note(name, f"the file specification registered as {key!r}")
    owners = [(_catalog(pdf), "the catalog's /AF")] + [
        (page.obj, f"page {page_index}'s /AF") for page_index, page in enumerate(pdf.pages)
    ]
    for owner, where in owners:
        for entry in _af_entries(owner):
            if entry is None:
                continue
            for name in _plannotation_spec_names("", entry):
                note(name, where)
    return found


def _refuse_conflicts(pdf: Pdf) -> None:
    """Refuse to plannotate a document that already carries a Plannotation file.

    Assigning to the attachments mapping replaces an existing file of the same name
    silently, so a foreign file called ``plannotation-index.json`` would be destroyed
    here and deleted by a later :func:`strip` as though Plannotation had written it. And a
    file that is not in the name tree at all is not protected by that mapping:
    plannotating over it appends a second ``plannotation-p0000.json`` to the same page's
    ``/AF``, after which :func:`read_pdf` returns whichever of the two comes first.

    Args:
        pdf: The document being plannotated.

    Raises:
        AttachmentConflictError: If the document already carries any file Plannotation
            owns, under either registration and under either of a specification's two
            filename entries.
    """
    found = _existing_plannotation_files(pdf)
    if not found:
        return
    listed = "; ".join(
        f"{name!r} in {' and '.join(sorted(places))}" for name, places in sorted(found.items())
    )
    msg = (
        f"refusing to overwrite {listed}: a file Plannotation owns is already attached to "
        "this document. If Plannotation wrote it, run `plannotation strip` first; if "
        "something else did, that file is not Plannotation's to replace"
    )
    raise AttachmentConflictError(msg)


def _refuse_signed(pdf: Pdf, *, break_signature: bool) -> None:
    """Refuse to rewrite a signed document unless explicitly told to.

    Args:
        pdf: The document being plannotated.
        break_signature: Whether the caller has accepted that the signature will be
            invalidated.

    Raises:
        SignedPdfError: If the document is signed and ``break_signature`` is False.
    """
    report = signature_report(pdf)
    if not report.signed:
        return
    if break_signature:
        _LOGGER.warning(
            "the input is signed (%s); saving rewrites the whole file, so the "
            "signature in the output is no longer valid",
            report.describe(),
        )
        return
    msg = (
        f"this document is signed ({report.describe()}). Saving it would invalidate "
        "the signature: pikepdf and qpdf rewrite a PDF in full and cannot append an "
        "incremental update, so the bytes the signature covers cannot be preserved. "
        "Two options: write a sidecar, which does not touch the document at all "
        "(`plannotation sidecar this.pdf --plannotations plannotations.json`, or "
        "write_sidecar(..., plannotations=...)), or pass --break-signature "
        "(break_signature=True) to plannotate it anyway and void the signature"
    )
    raise SignedPdfError(msg)


def _refuse_encrypted(pdf: Pdf) -> None:
    """Refuse to rewrite an encrypted document.

    Args:
        pdf: The document being plannotated.

    Raises:
        EncryptedPdfError: If the document is encrypted. Saving would either drop the
            encryption or re-encrypt with parameters Plannotation chose, and neither is
            a decision a tool that plannotates may take quietly; a deterministic ``/ID``
            is not available for an encrypted file either.
    """
    if not pdf.is_encrypted:
        return
    msg = (
        "this document is encrypted. Plannotating it would rewrite its encryption, "
        "which is not Plannotation's decision to make. Write a sidecar instead "
        "(`plannotation sidecar this.pdf --plannotations plannotations.json`), or remove "
        "the encryption first with a tool meant for it and plannotate the result"
    )
    raise EncryptedPdfError(msg)


def attach_in_place(
    pdf: Pdf,
    plannotations: Iterable[Plannotation],
    index: PlannotationIndex | None = None,
    *,
    mod_date: datetime,
    break_signature: bool = False,
    compress_plannotations: bool = True,
) -> AttachReport:
    """Embed plannotations into an open document, changing nothing else about it.

    The document is modified but not saved; use :func:`save_plannotated` to write it,
    or :func:`attach`, which does both.

    Args:
        pdf: An open document, modified in place.
        plannotations: The plannotations. Each goes on the page its ``page.index``
            names.
        index: The document-level index, or None to embed none.
        mod_date: The timestamp recorded on every embedded file. Injected rather than
            read from the clock, so that the output is reproducible.
        break_signature: Proceed although the document is signed, accepting that the
            signature will not survive.
        compress_plannotations: Store the embedded JSON Flate-encoded. The checksum and size
            in ``/Params`` still describe the plaintext, as the format requires.

    Returns:
        A report of what was written.

    Raises:
        ValueError: If no plannotations were given, or ``mod_date`` is naive.
        EncryptedPdfError: If the document is encrypted.
        SignedPdfError: If the document is signed and ``break_signature`` is False.
        PlannotationMismatchError: If a plannotation contradicts its page, or the index
            contradicts the plannotations.
        AttachmentConflictError: If a file of the same name is already attached.
    """
    stamp = pdf_date(mod_date)
    _refuse_encrypted(pdf)
    _refuse_signed(pdf, break_signature=break_signature)
    packet = _read_packet(pdf)
    _can_splice(packet)
    by_page = check_plannotations_against(pdf, plannotations)
    if index is not None:
        _check_index(index, by_page)
    _refuse_conflicts(pdf)
    pdfa_part, pdfa_extension_schema_missing = _pdfa_status(packet)

    planned = [plannotation_filename(page_index) for page_index in sorted(by_page)]
    if index is not None:
        planned.append(INDEX_FILENAME)

    for page_index, plannotation in sorted(by_page.items()):
        spec = _embed_file(
            pdf,
            canonical_bytes(plannotation),
            filename=plannotation_filename(page_index),
            description=_page_description(page_index),
            mod_date=stamp,
            compress=compress_plannotations,
        )
        _append_af(pdf.pages[page_index].obj, spec)
    if index is not None:
        spec = _embed_file(
            pdf,
            canonical_bytes(index),
            filename=INDEX_FILENAME,
            description=f"Plannotation {SCHEMA_VERSION} index",
            mod_date=stamp,
            compress=compress_plannotations,
        )
        _append_af(_catalog(pdf), spec)

    declared = _add_declaration(pdf, packet)
    if pdfa_extension_schema_missing:
        _LOGGER.warning(
            "this document claims PDF/A-%d conformance, and ISO 19005-%d requires an "
            "XMP extension schema for every property from a namespace it does not "
            "define. The PDF Declaration adds pdfd:declarations (%s) and no schema "
            "describes it, so the output no longer conforms to PDF/A-%d. Plannotation "
            "does not splice the PDF Association's extension schema in; add it, or "
            "write a sidecar instead, or re-validate the output before relying on the "
            "claim. PDF/A-4 is unaffected: it dropped extension schemas",
            pdfa_part,
            pdfa_part,
            NS_PDFD,
            pdfa_part,
        )
    _LOGGER.info("plannotated %d page(s): %s", len(by_page), ", ".join(planned))
    return AttachReport(
        page_indices=tuple(sorted(by_page)),
        filenames=tuple(planned),
        index_written=index is not None,
        declaration_added=declared,
        pdf_version=str(pdf.pdf_version),
        pdfa_part=pdfa_part,
        pdfa_extension_schema_missing=pdfa_extension_schema_missing,
    )


def attach(
    pdf_in: Path | str,
    plannotations: Iterable[Plannotation],
    index: PlannotationIndex | None,
    pdf_out: Path | str,
    *,
    mod_date: datetime,
    break_signature: bool = False,
    compress_plannotations: bool = True,
) -> AttachReport:
    """Write a plannotated copy of a PDF.

    Guarantees, all of them testable: every page of ``pdf_out`` rasterises to pixels
    identical to the same page of ``pdf_in``; no content stream, annotation, page box
    or ``/Rotate`` is modified; no attachment, ``/AF`` entry or metadata property
    that Plannotation did not write is modified or removed; and running this twice with
    the same arguments produces the same bytes.

    What is *not* guaranteed is that the file's bytes are a superset of the input's.
    qpdf rewrites the document, so object numbers and the ``/ID`` change.

    Args:
        pdf_in: The document to plannotate.
        plannotations: The plannotations. Each goes on the page its ``page.index``
            names.
        index: The document-level index to embed, or None for none.
        pdf_out: Where to write the plannotated copy. May be ``pdf_in``, in which case
            the input is opened for overwriting.
        mod_date: The timestamp recorded on every embedded file.
        break_signature: Proceed although the input is signed, accepting that the
            signature will not survive.
        compress_plannotations: Store the embedded JSON Flate-encoded.

    Returns:
        A report of what was written.

    Raises:
        ValueError: If no plannotations were given, or ``mod_date`` is naive.
        EncryptedPdfError: If the input is encrypted.
        SignedPdfError: If the input is signed and ``break_signature`` is False.
        PlannotationMismatchError: If a plannotation contradicts its page, or the index
            contradicts the plannotations.
        AttachmentConflictError: If a file of the same name is already attached.
    """
    source = _as_path(pdf_in)
    target = _as_path(pdf_out)
    overwriting = source.exists() and _is_same_file(source, target)
    with _opened(source, allow_overwriting_input=overwriting) as pdf:
        report = attach_in_place(
            pdf,
            plannotations,
            index,
            mod_date=mod_date,
            break_signature=break_signature,
            compress_plannotations=compress_plannotations,
        )
        save_plannotated(pdf, target)
    _LOGGER.info("wrote %s", target)
    return report


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PlannotationSet:
    """Everything a carrier holds: the index, and the plannotations keyed by page.

    It unpacks as a two-tuple, so the reading functions can be used exactly as the
    design brief writes them, while a caller that wants either half by name -- or the
    levels -- can ask for it instead::

        index, pages = read("drawings/TWP-101.pdf")
        plannotations = read("drawings/TWP-101.pdf")
        plannotations.pages[0].sheet.sheet_id

    A type checker gives the unpacked names the union of the two member types, so
    typed code should prefer the attributes.

    Attributes:
        index: The document-level index, or None when the carrier has none. A reader
            must not conclude from a missing index that there are no plannotations, nor
            from an index that every page it lists is present.
        pages: The plannotations, keyed by the **page they were found on**, not by the
            ``page.index`` inside them. Section 4.5 of the specification is explicit:
            where the two disagree the attachment is the fact and the plannotation's
            own number is a claim.
    """

    index: PlannotationIndex | None
    pages: dict[int, Plannotation]

    def __iter__(self) -> Iterator[PlannotationIndex | dict[int, Plannotation] | None]:
        """Yield the index and then the pages, so that the pair can be unpacked.

        Yields:
            :attr:`index`, then :attr:`pages`.
        """
        yield self.index
        yield self.pages

    @property
    def is_empty(self) -> bool:
        """Report whether the carrier held nothing at all.

        Returns:
            True when there is neither an index nor a single plannotation.
        """
        return self.index is None and not self.pages

    @property
    def levels(self) -> dict[int, ConformanceLevel]:
        """Return the conformance level each plannotation reaches.

        Returns:
            One entry per plannotation, computed from the plannotation itself rather
            than taken from the index, which only records what a writer claimed.
        """
        return {
            index: conformance_level(plannotation)
            for index, plannotation in sorted(self.pages.items())
        }

    def to_sidecar(self, *, generator: Generator | None = None) -> Sidecar:
        """Assemble the sidecar document for this carrier's contents.

        Args:
            generator: The tool to record as having written the sidecar.

        Returns:
            The sidecar, carrying this index -- or one derived from the plannotations
            when there is none -- and every plannotation, ascending by page.

        Raises:
            PlannotationNotFoundError: If there is nothing to write.
        """
        if self.is_empty:
            msg = "there are no plannotations to write"
            raise PlannotationNotFoundError(msg)
        plannotations = [self.pages[index] for index in sorted(self.pages)]
        return build_sidecar(plannotations, self.index, generator=generator)


def _spec_filename(spec: Object) -> str:
    """Return the filename a file specification declares.

    Args:
        spec: A ``/Filespec`` dictionary.

    Returns:
        Its ``/UF``, falling back to ``/F``, or the empty string when it has neither.
        ``/UF`` is preferred because PDF 2.0 requires it and deprecates ``/F``.
    """
    names = _spec_names(spec)
    return names[0] if names else ""


def _spec_bytes(spec: Object, *, limit: int, source: str) -> bytes | None:
    """Return the bytes a file specification embeds.

    Args:
        spec: A ``/Filespec`` dictionary.
        limit: The most the embedded file may decode to, in bytes.
        source: The file being read, for the message.

    Returns:
        The embedded file's decoded contents, or None when the specification carries
        no embedded stream -- which is legal, and means the file lives elsewhere.

    Raises:
        InvalidPlannotationError: If the contents exceed ``limit``, or the specification is
            malformed. A file specification comes from an untrusted document and may
            hold anything under ``/EF``; reaching for a stream's members on whatever is
            there raises out of the PDF library, which reaches a person as a traceback
            rather than as a message, and which no caller can treat as a plannotation
            being absent.
    """
    if not isinstance(spec, Dictionary):
        return None
    embedded = spec.get(Name.EF)
    if embedded is None:
        return None
    if not isinstance(embedded, Dictionary):
        msg = (
            f"{source} has an /EF that is not a dictionary but {embedded!r}, so the "
            "embedded file it names cannot be found; the file specification is damaged"
        )
        raise InvalidPlannotationError(msg)
    for key in (Name.UF, Name.F):
        stream = embedded.get(key)
        if stream is None:
            continue
        if not isinstance(stream, pikepdf.Stream):
            msg = (
                f"{source} has an /EF {key} that is not a stream but {stream!r}; an "
                "embedded file is a stream, so this file specification is damaged"
            )
            raise InvalidPlannotationError(msg)
        return _bounded_stream_bytes(stream, limit=limit, source=source)
    return None


def _read_spec(spec: Object, *, limit: int, source: str, strict: bool) -> bytes | None:
    """Read one file specification, obeying the bound and the reader's strictness.

    Args:
        spec: A ``/Filespec`` dictionary.
        limit: The most the embedded file may decode to, in bytes.
        source: The file being read, for the message.
        strict: Whether a file this reader will not decompress is an error. When False
            it is reported and skipped, which is what section 4.3 requires of a
            conforming reader: a plannotation it will not accept is a plannotation that
            is absent.

    Returns:
        The embedded file's contents, or None when there are none or they were skipped.

    Raises:
        InvalidPlannotationError: If the contents exceed ``limit`` and ``strict`` is True.
    """
    try:
        return _spec_bytes(spec, limit=limit, source=source)
    except InvalidPlannotationError as exc:
        if strict:
            raise
        _LOGGER.warning("ignoring a plannotation this reader will not decompress: %s", exc)
        return None


def _from_associated_files(
    pdf: Pdf, *, limit: int, strict: bool
) -> tuple[dict[int, bytes], bytes | None]:
    """Collect Plannotation files through the ``/AF`` arrays.

    Args:
        pdf: An open document.
        limit: The most one embedded file may decode to, in bytes.
        strict: Whether a file that exceeds the bound is an error rather than absent.

    Returns:
        The plannotation bytes keyed by the page whose ``/AF`` named them, and the
        index bytes from the catalog's ``/AF`` if it names one. A None entry inside
        an ``/AF`` array is skipped rather than fatal: it is what a dangling
        reference looks like after somebody deleted an attachment.

    Raises:
        InvalidPlannotationError: If a file exceeds the bound and ``strict`` is True.
    """
    pages: dict[int, bytes] = {}
    for page_index, page in enumerate(pdf.pages):
        for spec in _af_entries(page.obj):
            if spec is None or not _PLANNOTATION_FILENAME.match(_spec_filename(spec)):
                continue
            source = f"{_spec_filename(spec)} (page {page_index})"
            data = _read_spec(spec, limit=limit, source=source, strict=strict)
            if data is not None:
                pages[page_index] = data
                break
    index: bytes | None = None
    for spec in _af_entries(_catalog(pdf)):
        if spec is not None and _spec_filename(spec) == INDEX_FILENAME:
            index = _read_spec(spec, limit=limit, source=INDEX_FILENAME, strict=strict)
            break
    return pages, index


def _from_name_tree(
    pdf: Pdf, page_count: int, *, limit: int, strict: bool
) -> tuple[dict[int, bytes], bytes | None]:
    """Collect Plannotation files through the ``EmbeddedFiles`` name tree.

    Args:
        pdf: An open document.
        page_count: How many pages the document has, so that an attachment naming a
            page that does not exist can be reported rather than returned.
        limit: The most one embedded file may decode to, in bytes.
        strict: Whether a file that exceeds the bound is an error rather than absent.

    Returns:
        The plannotation bytes keyed by the page index in their filename, and the index
        bytes. This is the fallback path, needed because a producer may have written
        only one of the two registrations and a later tool may have dropped the other.

    Raises:
        InvalidPlannotationError: If a file exceeds the bound and ``strict`` is True.
    """
    wanted = {plannotation_filename(page_index): page_index for page_index in range(page_count)}
    pages: dict[int, bytes] = {}
    for filename, page_index in wanted.items():
        if filename not in pdf.attachments:
            continue
        source = f"{filename} (page {page_index})"
        data = _read_spec(pdf.attachments[filename].obj, limit=limit, source=source, strict=strict)
        if data is not None:
            pages[page_index] = data
    orphans = [
        name
        for name in pdf.attachments
        if _PLANNOTATION_FILENAME.match(name) and name not in wanted
    ]
    if orphans:
        _LOGGER.warning(
            "the document carries plannotation file(s) %s for pages it does not have; "
            "ignoring them",
            ", ".join(sorted(orphans)),
        )
    index = (
        _read_spec(
            pdf.attachments[INDEX_FILENAME].obj,
            limit=limit,
            source=INDEX_FILENAME,
            strict=strict,
        )
        if INDEX_FILENAME in pdf.attachments
        else None
    )
    return pages, index


def read_pdf(
    pdf: Pdf, *, strict: bool = True, max_plannotation_bytes: int = _MAX_PLANNOTATION_BYTES
) -> PlannotationSet:
    """Read every Plannotation file a document carries.

    Page-level ``/AF`` is preferred, because it is the association the format is
    built on and it says which page a plannotation belongs to. The ``EmbeddedFiles``
    name tree is the fallback, by filename. Both are needed: a document may carry only one
    of them, and a reader that consulted only one would find nothing in a file that
    is perfectly readable.

    Every document found is validated against its packaged JSON Schema and then
    loaded into its model.

    Args:
        pdf: An open document.
        strict: Raise on a plannotation that does not validate. When False, an invalid
            plannotation is reported and skipped, which is the behaviour section 4.3
            requires of a conforming reader: an invalid plannotation is to be treated as
            absent.
        max_plannotation_bytes: The most one embedded file may decompress to. The default,
            :data:`_MAX_PLANNOTATION_BYTES`, is generous by three orders of magnitude; raise it
            for a genuinely enormous plannotation rather than going without a bound.

    Returns:
        The index and the plannotations, keyed by the page they were found on.

    Raises:
        InvalidPlannotationError: If a document does not validate or exceeds
            ``max_plannotation_bytes``, and ``strict`` is True.
    """
    page_count = len(pdf.pages)
    associated, index_bytes = _from_associated_files(
        pdf, limit=max_plannotation_bytes, strict=strict
    )
    named, named_index = _from_name_tree(
        pdf, page_count, limit=max_plannotation_bytes, strict=strict
    )
    raw_pages = {**named, **associated}
    if index_bytes is None:
        index_bytes = named_index

    pages: dict[int, Plannotation] = {}
    for page_index in sorted(raw_pages):
        source = f"{plannotation_filename(page_index)} (page {page_index})"
        try:
            plannotation = _load_model(raw_pages[page_index], "page", source, load_plannotation)
        except InvalidPlannotationError as exc:
            if strict:
                raise
            _LOGGER.warning("ignoring an invalid plannotation: %s", exc)
            continue
        if plannotation.page.index != page_index:
            _LOGGER.warning(
                "the plannotation attached to page %d says it describes page %d; the "
                "attachment is the fact and page.index is a claim, so it is being "
                "read as page %d",
                page_index,
                plannotation.page.index,
                page_index,
            )
        pages[page_index] = plannotation

    index: PlannotationIndex | None = None
    if index_bytes is not None:
        try:
            index = _load_model(index_bytes, "index", INDEX_FILENAME, load_plannotation_index)
        except InvalidPlannotationError as exc:
            if strict:
                raise
            _LOGGER.warning("ignoring an invalid index: %s", exc)
    return PlannotationSet(index=index, pages=pages)


def read_sidecar(source: Path | str | bytes, *, strict: bool = True) -> PlannotationSet:
    """Read a sidecar file.

    Args:
        source: The sidecar's path, or its bytes.
        strict: Raise on a sidecar that does not validate. When False an invalid
            sidecar reads as empty, as section 4.3 requires.

    Returns:
        The index and plannotations the sidecar carries, keyed by each plannotation's
        own ``page.index`` -- in a sidecar that number is all there is to key by.

    Raises:
        InvalidPlannotationError: If the sidecar does not validate and ``strict`` is True.
    """
    if isinstance(source, bytes):
        data, name = source, "<bytes>"
    else:
        path = _as_path(source)
        data, name = path.read_bytes(), path.name
    try:
        sidecar = _load_model(data, "sidecar", name, load_sidecar)
    except InvalidPlannotationError as exc:
        if strict:
            raise
        _LOGGER.warning("ignoring an invalid sidecar: %s", exc)
        return PlannotationSet(index=None, pages={})
    return PlannotationSet(
        index=sidecar.index,
        pages={plannotation.page.index: plannotation for plannotation in sidecar.pages},
    )


def _looks_like_pdf(path: Path) -> bool:
    """Report whether a file begins with the PDF header.

    Args:
        path: The file to sniff.

    Returns:
        True when the first bytes are ``%PDF-``. The content decides, not the
        extension: a sidecar named ``.pdf`` would otherwise be opened as a document
        and a document named ``.json`` as a sidecar.
    """
    with path.open("rb") as handle:
        return handle.read(5) == b"%PDF-"


def read(
    source: Path | str,
    *,
    strict: bool = True,
    max_plannotation_bytes: int = _MAX_PLANNOTATION_BYTES,
) -> PlannotationSet:
    """Read plannotations from either carrier: a PDF, or a sidecar JSON file.

    Args:
        source: The document or sidecar to read.
        strict: Raise on a plannotation that does not validate, rather than skipping it.
        max_plannotation_bytes: The most one embedded file in a PDF may decompress to. A
            sidecar is plain JSON on disk with nothing to expand, so this does not
            apply to one.

    Returns:
        The index and the plannotations.

    Raises:
        FileNotFoundError: If there is no such file.
        CarrierError: If the file begins like a PDF and the PDF library will not read
            it.
        InvalidPlannotationError: If a document does not validate or exceeds
            ``max_plannotation_bytes``, and ``strict`` is True.
    """
    path = _as_path(source)
    if not _looks_like_pdf(path):
        return read_sidecar(path, strict=strict)
    with _opened(path) as pdf:
        return read_pdf(pdf, strict=strict, max_plannotation_bytes=max_plannotation_bytes)


@dataclass(frozen=True)
class CarrierReport:
    """What one carrier holds, for a tool that has to describe it to a person.

    Attributes:
        source: The file read.
        carrier: ``"pdf"`` or ``"sidecar"``.
        plannotations: The index and plannotations found.
        declaration: Whether a PDF Declaration naming the Plannotation specification is
            present. Only a PDF can carry one.
        signature: What the document says about being signed, or None for a sidecar.
        page_count: How many pages the document has, or None for a sidecar, which
            does not know.
        filenames: The Plannotation files found in the document, sorted.
    """

    source: Path
    carrier: Literal["pdf", "sidecar"]
    plannotations: PlannotationSet
    declaration: bool
    signature: SignatureReport | None
    page_count: int | None
    filenames: tuple[str, ...]


def carrier_report(
    source: Path | str,
    *,
    strict: bool = True,
    max_plannotation_bytes: int = _MAX_PLANNOTATION_BYTES,
) -> CarrierReport:
    """Describe a carrier: its plannotations, its declaration, and whether it is signed.

    Args:
        source: The document or sidecar to examine.
        strict: Raise on a plannotation that does not validate, rather than skipping it.
        max_plannotation_bytes: The most one embedded file in a PDF may decompress to.

    Returns:
        The report.

    Raises:
        FileNotFoundError: If there is no such file.
        CarrierError: If the document is damaged beyond describing.
        InvalidPlannotationError: If a document does not validate or exceeds
            ``max_plannotation_bytes``, and ``strict`` is True.
    """
    path = _as_path(source)
    if not _looks_like_pdf(path):
        plannotations = read_sidecar(path, strict=strict)
        return CarrierReport(
            source=path,
            carrier="sidecar",
            plannotations=plannotations,
            declaration=False,
            signature=None,
            page_count=None,
            filenames=(path.name,),
        )
    with _opened(path) as pdf:
        return CarrierReport(
            source=path,
            carrier="pdf",
            plannotations=read_pdf(
                pdf, strict=strict, max_plannotation_bytes=max_plannotation_bytes
            ),
            declaration=has_declaration(pdf),
            signature=signature_report(pdf),
            page_count=len(pdf.pages),
            filenames=tuple(
                sorted(name for name in pdf.attachments if is_plannotation_filename(name))
            ),
        )


# ---------------------------------------------------------------------------
# strip
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StripReport:
    """What :func:`strip` removed.

    Attributes:
        filenames: The embedded files removed, sorted.
        associations_cleared: How many ``/AF`` entries were dropped.
        declaration_removed: Whether the PDF Declaration was removed.
        declaration_remaining: Whether a Plannotation declaration is still present after
            stripping. True only when the packet was written or rewritten by
            something other than this implementation, in which case removing it would
            mean rewriting another producer's metadata.
        metadata_removed: Whether an XMP packet that Plannotation itself had created was
            removed, leaving the document with none again.
    """

    filenames: tuple[str, ...]
    associations_cleared: int
    declaration_removed: bool
    declaration_remaining: bool
    metadata_removed: bool

    @property
    def is_empty(self) -> bool:
        """Report whether nothing was found to remove.

        Returns:
            True when the document carried no Plannotation data at all.
        """
        return not self.filenames and not self.associations_cleared and not self.declaration_removed


def _clear_associations(pdf: Pdf, victims: set[tuple[int, int]]) -> int:
    """Drop Plannotation's entries from every ``/AF`` array in the document.

    This must happen **before** the attachments are deleted. Deleting an attachment
    replaces its file specification with a null object, and an ``/AF`` array still
    holding it then yields None, which cannot be matched against anything.

    An entry is dropped when it is one of the specifications being deleted, or when it
    names a Plannotation file by either of its two filenames -- the same predicate
    :func:`_refuse_conflicts` refuses on, so that what cannot be plannotated over can
    always be removed.

    A specification that is being *unregistered* rather than deleted -- one shared with
    a name Plannotation does not own -- is not a victim and keeps its association, because
    it is another producer's file and its ``/AF`` entry is another producer's entry.

    Args:
        pdf: The document being stripped.
        victims: The object identities of the file specifications being destroyed, which
            is not every specification a Plannotation key pointed at. An entry that is not a
            PDF object at all has no identity, is in no such set, and is kept.

    Returns:
        How many entries were dropped. When an array is emptied, the key is deleted
        rather than left as an empty array.
    """
    cleared = 0
    owners: list[Object] = [_catalog(pdf), *(page.obj for page in pdf.pages)]
    for owner in owners:
        entries = _af_entries(owner)
        if not entries:
            continue
        keep = [
            entry
            for entry in entries
            if entry is not None
            and _identity(entry) not in victims
            and not _is_plannotation_spec(entry)
        ]
        if len(keep) == len(entries):
            continue
        cleared += len(entries) - len(keep)
        if keep:
            owner[Name.AF] = Array(keep)
        else:
            del owner[Name.AF]
    return cleared


def _is_empty_name_tree(tree: Dictionary) -> bool:
    """Report whether a name tree node holds nothing at all.

    Args:
        tree: A name tree's root node.

    Returns:
        True when it has neither children nor entries. A node with an empty ``/Names``
        array is what pikepdf leaves behind after the last attachment is deleted.
    """
    kids = tree.get(Name.Kids)
    if isinstance(kids, Array) and len(kids) > 0:
        return False
    entries = tree.get(Name.Names)
    return not isinstance(entries, Array) or len(entries) == 0


def _prune_name_tree(pdf: Pdf) -> bool:
    """Delete an ``EmbeddedFiles`` name tree that stripping has emptied.

    An emptied name tree is residue of exactly the kind this module removes everywhere
    else: :func:`_clear_associations` deletes an ``/AF`` key it has emptied, and
    :func:`_drop_plannotation_packet` deletes an XMP packet Plannotation created. A
    document that had no attachments before it was plannotated must have no ``/Names``
    after it is stripped.

    Args:
        pdf: An open document, modified in place.

    Returns:
        True if anything was removed. A tree that still holds another producer's
        attachments is left exactly as it is, and so is a ``/Names`` dictionary holding
        anything else -- destinations, or a JavaScript tree.
    """
    catalog = _catalog(pdf)
    names = catalog.get(Name.Names)
    if not isinstance(names, Dictionary):
        return False
    tree = names.get(Name.EmbeddedFiles)
    if not isinstance(tree, Dictionary) or not _is_empty_name_tree(tree):
        return False
    del names[Name.EmbeddedFiles]
    if len(names.keys()) == 0:
        del catalog[Name.Names]
    _LOGGER.debug("removed the emptied EmbeddedFiles name tree")
    return True


def _embedded_files_tree(pdf: Pdf) -> Dictionary | None:
    """Return the ``EmbeddedFiles`` name tree's root node, or None.

    Args:
        pdf: An open document.

    Returns:
        The node under ``/Names /EmbeddedFiles``, or None when the document has no name
        tree of attachments or has something other than a dictionary there.
    """
    names = _catalog(pdf).get(Name.Names)
    if not isinstance(names, Dictionary):
        return None
    tree = names.get(Name.EmbeddedFiles)
    return tree if isinstance(tree, Dictionary) else None


def _unregister(pdf: Pdf, key: str) -> None:
    """Remove one key from the ``EmbeddedFiles`` name tree, leaving its file alone.

    ``del pdf.attachments[key]`` does two things at once: it takes the key out of the
    name tree **and** it replaces the file specification with a null object, destroying
    the embedded stream. That is right for a file Plannotation wrote and wrong for one that
    merely shares its specification -- a single ``/Filespec`` registered under two keys,
    one of Plannotation's and one somebody else's, lost the other producer's bytes and left
    that producer's key pointing at nothing.

    Args:
        pdf: An open document, modified in place.
        key: The name-tree key to remove.
    """
    tree = _embedded_files_tree(pdf)
    if tree is None:  # pragma: no cover - the key came from this same tree
        return
    del pikepdf.NameTree(tree)[key]


def strip_in_place(pdf: Pdf) -> StripReport:
    """Remove Plannotation's files and declaration from an open document.

    Only what Plannotation writes is removed: its embedded files, its ``/AF`` entries
    and its declaration. A third party's attachment, ``/AF`` entry, declaration or
    metadata property survives byte for byte, and so does every page.

    What is removed is decided by :func:`_plannotation_spec_names`, which is also what
    :func:`_refuse_conflicts` refuses on. The two are one predicate on purpose: a file
    that attach will not write over and strip will not remove is a document that can be
    neither plannotated nor repaired, and the refusal's advice -- run
    ``plannotation strip`` -- would be false.

    Aliasing is the exception the predicate cannot express on its own. One file
    specification may be registered in the name tree under several keys, and only some
    of them Plannotation's. Its embedded stream is then destroyed only when *every* key it
    is filed under is one Plannotation owns; otherwise the Plannotation keys are taken out of
    the tree and the file, its bytes and its other keys are left exactly as they were.

    Args:
        pdf: An open document, modified in place.

    Returns:
        A report of what was removed.

    Raises:
        DeclarationError: If the catalog's XMP packet cannot be read inside
            :data:`_MAX_XMP_BYTES`. The packet is read first, before anything is
            removed, so that this refusal leaves the document untouched.
    """
    packet = _read_packet(pdf)
    if packet.unreadable:
        raise _unstrippable_packet()
    # One walk, for the same reason as _existing_plannotation_files: a per-key lookup
    # here would make strip quadratic in a count the document chooses.
    registered = {str(key): attached.obj for key, attached in pdf.attachments.items()}
    doomed = sorted(key for key, spec in registered.items() if _plannotation_spec_names(key, spec))
    identities = {key: _identity(spec) for key, spec in registered.items()}
    surviving = set(registered) - set(doomed)
    shared: set[tuple[int, int]] = {
        identity for key in surviving if (identity := identities[key]) is not None
    }
    victims: set[tuple[int, int]] = {
        identity for key in doomed if (identity := identities[key]) is not None
    } - shared
    cleared = _clear_associations(pdf, victims)
    destroyed: set[tuple[int, int] | None] = set()
    for key in doomed:
        identity = identities[key]
        if identity is None or (identity in victims and identity not in destroyed):
            del pdf.attachments[key]
            destroyed.add(identity)
        else:
            _LOGGER.debug(
                "the file registered as %r is registered under another name as well; "
                "removing the Plannotation key and leaving the file where it is",
                key,
            )
            _unregister(pdf, key)
    _prune_name_tree(pdf)
    removed = _remove_declaration(packet)
    dropped = _drop_plannotation_packet(pdf, packet) if removed else False
    remaining = _declared(packet)
    if remaining:
        _LOGGER.warning(
            "a Plannotation declaration remains in the XMP metadata in a form this "
            "implementation did not write; removing it would mean rewriting another "
            "producer's packet, so it has been left alone",
        )
    _LOGGER.info("removed %d embedded file(s) and %d /AF entr(ies)", len(doomed), cleared)
    return StripReport(
        filenames=tuple(doomed),
        associations_cleared=cleared,
        declaration_removed=removed,
        declaration_remaining=remaining,
        metadata_removed=dropped,
    )


def strip(pdf_in: Path | str, pdf_out: Path | str) -> StripReport:
    """Write a copy of a PDF with every trace of Plannotation removed.

    The output is the input without Plannotation: same pages, same pixels, same
    annotations, same foreign attachments, same metadata. Embedded plannotation streams
    that nothing references any more are dropped by qpdf on save, so the plannotation
    text does not survive anywhere in the file.

    Args:
        pdf_in: The plannotated document.
        pdf_out: Where to write the stripped copy. May be ``pdf_in``.

    A signed document is stripped rather than refused -- taking Plannotation's files
    back out is the one operation a user must always be able to perform -- but saving
    rewrites the file, so the signature will not survive, and that is said loudly
    before it happens.

    Returns:
        A report of what was removed.

    Raises:
        EncryptedPdfError: If the input is encrypted.
        DeclarationError: If the catalog's XMP packet cannot be read inside
            :data:`_MAX_XMP_BYTES`. Saving a document rewrites every stream in it, so a
            packet this reader will not decompress is one the save would decompress into
            the output; the refusal happens before anything is removed or written.
    """
    source = _as_path(pdf_in)
    target = _as_path(pdf_out)
    overwriting = source.exists() and _is_same_file(source, target)
    with _opened(source, allow_overwriting_input=overwriting) as pdf:
        _refuse_encrypted(pdf)
        signature = signature_report(pdf)
        if signature.signed:
            _LOGGER.warning(
                "the input is signed (%s); saving rewrites the whole file, so the "
                "signature in the output is no longer valid",
                signature.describe(),
            )
        report = strip_in_place(pdf)
        save_plannotated(pdf, target)
    _LOGGER.info("wrote %s", target)
    return report


# ---------------------------------------------------------------------------
# The sidecar twin
# ---------------------------------------------------------------------------
def write_sidecar(
    pdf: Path | str,
    out: Path | str | None = None,
    *,
    plannotations: Iterable[Plannotation] | None = None,
    index: PlannotationIndex | None = None,
    generator: Generator | None = None,
    strict: bool = True,
) -> Path:
    """Write the sidecar twin of a document.

    The sidecar carries the same payload as the embedded files -- the index and every
    plannotation -- in one JSON file beside the document, for a consumer that cannot
    read PDF attachments and for a document that must not be rewritten at all. The
    document is opened read-only and is never modified, whichever way this is called.

    There are two ways to call it. With no ``plannotations``, the plannotations already
    embedded in the document are copied out. With ``plannotations``, they are written
    for a document that carries none -- which is the answer for a signed PDF, where
    attaching would void the signature. In that case the plannotations are checked
    against the document first, and the index against the plannotations, exactly as
    :func:`attach` checks them, so that a sidecar cannot claim a page size the document
    contradicts or list a page no plannotation was given for. This is the route the
    signed-document refusal names, so an artefact written here must be one
    :func:`attach` would have written from the same inputs. The one relaxation is the
    file-name rule: a sidecar embeds nothing, so an index entry naming an embedded file
    describes some other carrier.

    The assembled document is validated against the packaged sidecar schema before it
    is written, so this cannot produce a file its own reader would reject.

    Args:
        pdf: The document. Read for its plannotations, or, when ``plannotations`` is
            given, opened only to check them.
        out: Where to write the sidecar. Defaults to :func:`sidecar_path` of the
            document.
        plannotations: The plannotations to write, for a document that does not carry
            them.
        index: The index to write beside them. Derived from the plannotations when
            omitted.
        generator: The tool to record as having written the sidecar.
        strict: Raise on an embedded plannotation that does not validate, rather than
            skipping it. Not consulted when ``plannotations`` is given.

    Returns:
        The path written.

    Raises:
        PlannotationNotFoundError: If no plannotations were given and the document
            carries none.
        PlannotationMismatchError: If a given plannotation contradicts the page it
            claims.
        InvalidPlannotationError: If a plannotation does not validate, or -- which would
            be a bug here -- the assembled sidecar does not.
    """
    source = _as_path(pdf)
    if plannotations is not None:
        with _opened(source) as document:
            by_page = check_plannotations_against(document, plannotations)
        if index is not None:
            _check_index(index, by_page, with_filenames=False)
        sidecar = build_sidecar(by_page.values(), index, generator=generator)
    else:
        found = read(source, strict=strict)
        if found.is_empty:
            msg = (
                f"{source} carries no Plannotation data, so there is nothing to write a "
                "sidecar from. Pass the plannotations themselves (plannotations=..., or "
                "`plannotation sidecar this.pdf --plannotations plannotations.json`) to "
                "write a sidecar for a document that does not carry any"
            )
            raise PlannotationNotFoundError(msg)
        sidecar = found.to_sidecar(generator=generator)
    data = canonical_bytes(sidecar)
    _check_schema(json.loads(data.decode("utf-8")), "sidecar", "the assembled sidecar")
    target = sidecar_path(source) if out is None else _as_path(out)
    target.write_bytes(data)
    _LOGGER.info("wrote %s (%d plannotation(s))", target, len(sidecar.pages))
    return target
