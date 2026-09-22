# SPDX-License-Identifier: Apache-2.0
"""Rule 1: schema validity, with a location a person can follow.

Schema validity is the floor of everything else. Section 4.3 (1) requires a reader to
validate a label before trusting any value in it, and 4.3 (2) requires it to treat an
invalid label as absent -- no partial parse, no repair. So every other rule in this
package runs only on a document that got past this module.

Readable paths
--------------
``jsonschema`` reports a failure as a deque of keys and indices. This module renders
that as an RFC 6901 JSON Pointer -- ``/elements/2/paperBBox`` -- which is what
:attr:`planlabel.validate.report.Finding.path` carries and what section 4.4 (2) means
by "a machine-readable location". A bare index is not a location: ``2`` tells a person
nothing, and a person reading a report is the point.

Bounded work
------------
Section 9.3 (6) is explicit that the number of violations reported and the length of
anything quoted back are both the document's to choose, and must therefore be bounded.
A schema failure deep inside a large array can produce thousands of sub-errors, each
quoting the instance that failed; :data:`MAX_REPORTED_ERRORS` and
:data:`MAX_QUOTED_CHARS` are this module's limits, and match the ones the PDF reader
already applies to the same payloads. :data:`MAX_DOCUMENT_BYTES` bounds a file read
straight off disk, which is the one path into the validator that does not go through
the PDF reader's own bound.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from jsonschema import Draft202012Validator

from planlabel.constants import SCHEMA_VERSION
from planlabel.errors import InputNotValidatableError
from planlabel.model import index_schema, page_schema, sidecar_schema
from planlabel.validate.codes import finding

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

    from planlabel.validate.report import Finding

__all__ = [
    "MAX_DOCUMENT_BYTES",
    "MAX_QUOTED_CHARS",
    "MAX_REPORTED_ERRORS",
    "DocumentKind",
    "check_schema",
    "check_version",
    "detect_kind",
    "json_pointer",
    "page_labels_of",
    "parse_json",
    "read_json_file",
]

#: The most a JSON file read straight off disk may hold, in bytes.
#:
#: The same figure the PDF reader allows one embedded payload to decode to. A label for
#: one drawing page is a few kilobytes; this is three orders of magnitude of headroom,
#: and a bound that generous still refuses the file whose only purpose is to be large.
MAX_DOCUMENT_BYTES: Final = 16 * 1024 * 1024

#: The most schema violations reported for one document -- section 9.3 (6).
MAX_REPORTED_ERRORS: Final = 5

#: The most characters of the document quoted back in one message -- section 9.3 (6).
MAX_QUOTED_CHARS: Final = 200


class DocumentKind(StrEnum):
    """Which of the three PlanLabel schemas a document is held to.

    ``LABELS`` is not a schema of its own: it is an array of page labels, which is a
    shape a person reasonably has on disk and which the ``attach`` verb already
    accepts. Each member is held to the page-label schema.
    """

    PAGE = "page"
    INDEX = "index"
    SIDECAR = "sidecar"
    LABELS = "labels"


_SCHEMAS: Final = {
    DocumentKind.PAGE: page_schema,
    DocumentKind.INDEX: index_schema,
    DocumentKind.SIDECAR: sidecar_schema,
}


def _validator(kind: DocumentKind) -> Draft202012Validator:
    """Build a validator for one of the three schemas.

    Args:
        kind: Which schema. ``LABELS`` is held to the page-label schema, member by
            member.

    Returns:
        A draft 2020-12 validator.
    """
    lookup = DocumentKind.PAGE if kind is DocumentKind.LABELS else kind
    return Draft202012Validator(_SCHEMAS[lookup]())


def json_pointer(parts: Iterable[object]) -> str:
    """Render a path into a document as an RFC 6901 JSON Pointer.

    Args:
        parts: The keys and indices, outermost first, as ``jsonschema`` reports them.

    Returns:
        The pointer, such as ``/elements/2/paperBBox``. The empty string points at the
        document itself. ``~`` and ``/`` inside a key are escaped as the standard
        requires, which matters because ``extensions`` keys and property-set names are
        the document's to choose.
    """
    return "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _clip(value: object) -> str:
    """Render a value from the document for a message, bounded in length.

    Args:
        value: Whatever the document held there.

    Returns:
        Its repr, cut to :data:`MAX_QUOTED_CHARS` with an ellipsis. Section 9.3 (6):
        the length of a fragment quoted back is the document's to choose, so it is the
        validator's to bound.
    """
    text = repr(value)
    if len(text) <= MAX_QUOTED_CHARS:
        return text
    return text[:MAX_QUOTED_CHARS] + "..."


def detect_kind(document: object) -> DocumentKind | None:
    """Decide which PlanLabel document a parsed JSON value is.

    The four shapes are distinguished by members the schemas make required, so the
    decision never depends on a member a valid document may omit:

    * an array is an array of page labels;
    * an object with ``pages`` **and** ``index`` is a sidecar (6.5.1 requires both);
    * an object with ``pages`` alone is an index;
    * an object with ``page`` is a page label.

    Args:
        document: The parsed JSON.

    Returns:
        The kind, or None when the value is not a PlanLabel document at all.
    """
    if isinstance(document, list):
        return DocumentKind.LABELS
    if not isinstance(document, dict):
        return None
    if "index" in document and "pages" in document:
        return DocumentKind.SIDECAR
    if "pages" in document:
        return DocumentKind.INDEX
    if "page" in document:
        return DocumentKind.PAGE
    return None


def check_version(document: object, *, source: str) -> Finding | None:
    """Check the format version a document declares, before its schema.

    Done first and separately because every PlanLabel schema pins ``planlabel`` with a
    ``const``: a 0.2 document validated against the 0.1 schema fails on that const and
    on every member 0.2 added, which buries the one fact the reader needs. Section
    4.3 (3) says what to do instead -- treat an unknown version as absent -- and this
    is that, said once.

    Args:
        document: The parsed JSON.
        source: Which document it is, for the finding.

    Returns:
        A finding when the declared version is not the one implemented, and None when
        it is, or when there is none to read.
    """
    if not isinstance(document, dict):
        return None
    declared = document.get("planlabel")
    if not isinstance(declared, str) or declared == SCHEMA_VERSION:
        return None
    return finding(
        "PL-SCH-003",
        message=(
            f"the document declares planlabel {declared!r}, which this validator does "
            f"not implement; it implements {SCHEMA_VERSION}. A reader must treat an "
            f"unknown version as absent rather than parse it partly"
        ),
        path="/planlabel",
        source=source,
    )


def check_schema(document: object, kind: DocumentKind, *, source: str) -> list[Finding]:
    """Validate a parsed document against its schema.

    Args:
        document: The parsed JSON.
        kind: Which schema to hold it to. ``LABELS`` is not used here; validate each
            member of the array as a ``PAGE`` instead.
        source: Which document it is, for the findings.

    Returns:
        One finding per violation, in document order, at most
        :data:`MAX_REPORTED_ERRORS` of them. A final finding says how many were
        suppressed when there were more.
    """
    validator = _validator(kind)
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.absolute_path))
    found: list[Finding] = []
    for error in errors[:MAX_REPORTED_ERRORS]:
        pointer = json_pointer(error.absolute_path)
        keyword = error.validator
        found.append(
            finding(
                "PL-SCH-001",
                message=(
                    f"{error.message} (schema keyword {keyword!r}, value {_clip(error.instance)})"
                ),
                path=pointer,
                source=source,
            )
        )
    suppressed = len(errors) - len(found)
    if suppressed > 0:
        found.append(
            finding(
                "PL-SCH-001",
                message=(
                    f"and {suppressed} further schema violation(s), not reported: a "
                    f"validator bounds the work it does reporting a failure, because "
                    f"the number of violations is the document's to choose"
                ),
                path="",
                source=source,
            )
        )
    return found


def parse_json(raw: bytes, *, source: str) -> object:
    """Parse a PlanLabel document's bytes, bounding what the parse may cost.

    Args:
        raw: The bytes.
        source: Which document they are, for the message.

    Returns:
        The parsed JSON.

    Raises:
        InputNotValidatableError: If the bytes are not UTF-8, are not JSON, or nest
            deeply enough to exhaust the parser. Section 9.3 (5) allows the depth
            failure to be turned into an ordinary "this document is absent" outcome,
            and for a validator that outcome is "there was nothing here to validate".
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        msg = f"{source} is not valid UTF-8: {exc}"
        raise InputNotValidatableError(msg) from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        msg = f"{source} is not parseable JSON: {exc}"
        raise InputNotValidatableError(msg) from exc
    except RecursionError as exc:
        msg = (
            f"{source} nests too deeply for this parser. A validator bounds the depth "
            f"of the JSON it parses; a document past that bound is treated as absent"
        )
        raise InputNotValidatableError(msg) from exc


def read_json_file(path: Path, *, limit: int = MAX_DOCUMENT_BYTES) -> bytes:
    """Read a JSON file from disk, refusing one that is too large to be a label.

    Args:
        path: The file.
        limit: The most it may hold, in bytes.

    Returns:
        Its bytes.

    Raises:
        InputNotValidatableError: If the file is larger than ``limit``. The size is
            taken from the filesystem before anything is read, so nothing is allocated
            from a number the document chose.
        OSError: If the file cannot be read.
    """
    size = path.stat().st_size
    if size > limit:
        msg = (
            f"{path} is {size} bytes, past the {limit}-byte bound this validator puts "
            f"on a document read from disk. A label for one drawing page is kilobytes; "
            f"raise the bound deliberately rather than going without one"
        )
        raise InputNotValidatableError(msg)
    return path.read_bytes()


def page_labels_of(document: object) -> Sequence[object]:
    """Return the page-label members of a parsed array of labels.

    Args:
        document: The parsed JSON, which the caller has decided is
            :attr:`DocumentKind.LABELS`.

    Returns:
        The array's members, or an empty sequence when it is not an array.
    """
    return document if isinstance(document, list) else []
