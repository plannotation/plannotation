# SPDX-License-Identifier: Apache-2.0
"""Reading the three kinds of input, and running every rule over what came out.

The rules live in their own modules; this one decides what to run them on. It is where
the two obligations section 4.4 and section 9 put on a validator *as a reader* are
kept: it never writes to what it validates, and it never reads an input except through
a bounded path.

Three inputs, one set of rules
------------------------------
``planlabel validate`` accepts a labelled PDF, a sidecar, or a bare labels JSON --
which may be one page label or an array of them -- and an index on its own, because
someone with a ``planlabel-index.json`` in front of them will try it and deserves an
answer rather than exit 2.

Every page label reaches the rules as a pair: the **parsed document** and the **loaded
model**. Two rules must see the parsed document, because :class:`planlabel.model.
PageLabel` refuses to construct at all when they are broken -- a repeated ``localId``
and an inferred item with no confidence -- and a rule whose violation stops the model
loading cannot be checked afterwards. Where only a model is in hand, as it is for every
label read out of a PDF, the parsed document is reconstructed from it by a canonical
round trip. On that document the two rules cannot fire, which is correct: the model
enforced them, and they are the model's to enforce there.

Bounded reading
---------------
A PDF is read through :func:`planlabel.pdf.embed.carrier_report`, which is the bounded
reader: it caps what an embedded payload may decode to, restricts the filter chain it
will decode at all, validates ``/DecodeParms`` before allocating from it, and bounds
the walk over the document's own structures. Going around it to fetch raw attachment
bytes would be going around every one of those. A JSON file on disk does not pass
through that reader -- there is no PDF to read it out of -- so
:func:`planlabel.validate.schema.read_json_file` bounds it instead, from the size on
the filesystem, before anything is allocated.

The PDF is opened a second time, read-only, for one rule: PL-GEO-001, which compares
each label's ``page`` block with the page it is attached to and is delegated to
:func:`planlabel.pdf.embed.check_labels_against` so that the validator and ``attach``
cannot disagree about it. That function takes an open document and the carrier module
exposes no way to borrow the one the reader used, so the file is parsed twice. The
second parse is bounded by the same library as the first, and a second parse is a much
smaller price than a second implementation of the rule.

Nothing is written. No function in this package opens a file for writing, and the
second open is read-only.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pikepdf

from planlabel.constants import SCHEMA_VERSION, SIDECAR_SUFFIX
from planlabel.errors import CarrierError, InputNotValidatableError, InvalidLabelError
from planlabel.model import (
    canonical_json,
    conformance_level,
    load_label_index,
    load_page_label,
    load_sidecar,
)
from planlabel.pdf import embed
from planlabel.validate import carrier, geometric, ifc, provenance, referential, schema, verapdf
from planlabel.validate.codes import finding
from planlabel.validate.report import PageSummary, Report

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from planlabel.model import LabelIndex, PageLabel
    from planlabel.validate.report import Finding

__all__ = ["check_page_document", "check_page_label", "validate"]

_LOGGER: Final = logging.getLogger(__name__)

#: Bytes read to decide whether a file is a PDF.
_MAGIC: Final = b"%PDF-"


def validate(
    source: Path | str,
    *,
    ifc_model: Path | str | None = None,
    strict: bool = False,
    run_verapdf: bool = False,
) -> Report:
    """Validate a labelled PDF, a sidecar, or a labels JSON file.

    Args:
        source: The file to validate. Never modified.
        ifc_model: An IFC model to cross-check against, or None to skip rule 4.
        strict: Whether warnings count as errors for the exit code.
        run_verapdf: Whether to run the optional veraPDF pass over a PDF input.

    Returns:
        The report. Its :attr:`~planlabel.validate.report.Report.exit_code` is 0 or 1;
        the exit code 2 cases raise instead.

    Raises:
        InputNotValidatableError: If the file does not exist, cannot be read, is not a
            PlanLabel document, or carries no PlanLabel data.
        MissingExtraError: If ``ifc_model`` is given and ifcopenshell is not installed.
        ExternalToolError: If ``run_verapdf`` is set and veraPDF is absent or unusable.
    """
    path = Path(source)
    if not path.is_file():
        msg = f"{path} does not exist, or is not a file"
        raise InputNotValidatableError(msg)
    model = ifc.open_model(Path(ifc_model)) if ifc_model is not None else None
    if _looks_like_pdf(path):
        return _validate_pdf(path, model=model, strict=strict, run_verapdf=run_verapdf)
    return _validate_json(path, model=model, strict=strict, run_verapdf=run_verapdf)


def _looks_like_pdf(path: Path) -> bool:
    """Report whether a file begins with the PDF header.

    Args:
        path: The file to sniff.

    Returns:
        True when the first bytes are ``%PDF-``. The content decides and never the
        extension: a sidecar named ``.pdf`` would otherwise be opened as a document.

    Raises:
        InputNotValidatableError: If the file cannot be read at all.
    """
    try:
        with path.open("rb") as handle:
            return handle.read(len(_MAGIC)) == _MAGIC
    except OSError as exc:
        msg = f"{path} could not be read: {exc}"
        raise InputNotValidatableError(msg) from exc


# ---------------------------------------------------------------------------
# One page label
# ---------------------------------------------------------------------------
def check_page_document(document: Mapping[str, object], *, source: str = "page") -> list[Finding]:
    """Run the rules that must see the parsed page label rather than the model.

    Two of them, and they are here rather than beside their families because of when
    they have to run. :class:`planlabel.model.PageLabel` refuses to construct a label
    with a repeated ``localId``, and :class:`planlabel.model.Element` refuses to
    construct an inferred item with no confidence. Run after the model had its say,
    both violations would have become "this file would not load", which names neither
    rule and tells a person nothing they can fix. So they run first, on the parsed
    document, while they are still visible -- and their companions in 4.6, which the
    model does not refuse, run with them so that everything a reader can learn from
    the document alone is learnt in one pass.

    Args:
        document: The parsed page label. It must already be schema-valid: these rules
            read members the schema constrains and do not re-check their shapes.
        source: Which document it is, for the findings.

    Returns:
        Every violation, in document order.
    """
    return [
        *referential.check_duplicate_ids(document, source=source),
        *provenance.check_inferred_confidence(document, source=source),
    ]


def check_page_label(
    label: PageLabel,
    *,
    source: str = "page",
    page_count: int | None = None,
) -> list[Finding]:
    """Run every rule that applies to one loaded page label.

    Rules 2, 3 and 5 -- references, geometry and provenance -- less the two that must
    see the parsed document, which are :func:`check_page_document`. This is the entry
    point for a caller that already holds a loaded label, and it is what
    ``tools/check_fixtures.py`` calls, so that the fixture corpus is held to exactly
    the rules the validator applies and not to a second copy of them.

    Args:
        label: The loaded page label.
        source: Which document the label is, for the findings.
        page_count: The page count of the document the label belongs to, when one is in
            front of the validator. None when there is not, in which case
            ``target.pdfPage`` is not bounded.

    Returns:
        Every violation, in no particular order; the caller sorts.
    """
    return [
        *referential.check_references(label, source=source, page_count=page_count),
        *geometric.check_geometry(label, source=source),
        *provenance.check_provenance(label, source=source),
    ]


def _round_trip(label: PageLabel) -> Mapping[str, object]:
    """Reconstruct the parsed document of a label that arrived as a model.

    Used where the label was read out of a PDF, and there is therefore no parsed
    document to hand: the bounded reader returns models. On the reconstruction neither
    rule of :func:`check_page_document` can fire, which is correct -- the model refused
    to load a label that broke either, and there it is the model's business to enforce
    them.

    Args:
        label: The loaded page label.

    Returns:
        Its canonical JSON, parsed. Numbers come back rounded to three decimals, which
        is the form the label was written in and the form it would have been read in.
    """
    parsed = json.loads(canonical_json(label))
    return parsed if isinstance(parsed, dict) else {}


def _page_source(document: object, fallback: str) -> str:
    """Name a page label for a finding.

    Args:
        document: The parsed page label, which may not have loaded.
        fallback: What to call it when it does not say which page it describes.

    Returns:
        ``"page 4"`` where the document says so, and ``fallback`` otherwise.
    """
    if isinstance(document, dict):
        page = document.get("page")
        if isinstance(page, dict):
            index = page.get("index")
            if isinstance(index, int) and not isinstance(index, bool) and index >= 0:
                return f"page {index}"
    return fallback


def _summary(label: PageLabel, page_index: int) -> PageSummary:
    """Summarise one loaded page label.

    Args:
        label: The label.
        page_index: The page it was found on.

    Returns:
        Its summary.
    """
    return PageSummary(
        page_index=page_index,
        sheet_id=label.sheet.sheet_id,
        level=conformance_level(label),
        declared_version=label.planlabel,
        implemented=label.planlabel == SCHEMA_VERSION,
        viewports=len(label.viewports or []),
        elements=len(label.elements or []),
        annotations=len(label.annotations or []),
    )


def _unloadable_summary(document: object, page_index: int) -> PageSummary:
    """Summarise a page label that did not load.

    Args:
        document: The parsed page label, or whatever was found in its place.
        page_index: The page it was found on, or claims.

    Returns:
        A summary saying what little is known: no level, and whatever version and sheet
        number can be read without trusting the rest.
    """
    declared: str | None = None
    sheet_id: str | None = None
    if isinstance(document, dict):
        version = document.get("planlabel")
        declared = version if isinstance(version, str) else None
        sheet = document.get("sheet")
        if isinstance(sheet, dict) and isinstance(sheet.get("id"), str):
            sheet_id = str(sheet["id"])
    return PageSummary(
        page_index=page_index,
        sheet_id=sheet_id,
        level=None,
        declared_version=declared,
        implemented=declared == SCHEMA_VERSION,
        viewports=0,
        elements=0,
        annotations=0,
    )


# ---------------------------------------------------------------------------
# The PDF carrier
# ---------------------------------------------------------------------------
def _validate_pdf(
    path: Path,
    *,
    model: ifc.IfcModel | None,
    strict: bool,
    run_verapdf: bool,
) -> Report:
    """Validate a labelled PDF.

    Args:
        path: The document.
        model: An opened IFC model, or None.
        strict: Whether warnings count as errors.
        run_verapdf: Whether to run the veraPDF pass.

    Returns:
        The report.

    Raises:
        InputNotValidatableError: If the document cannot be read, or carries no
            PlanLabel data at all and nothing went wrong that could be reported.
    """
    findings: list[Finding] = []
    try:
        read = embed.carrier_report(path, strict=True)
    except InvalidLabelError as exc:
        findings.append(finding("PL-SCH-001", message=str(exc), path="", source=path.name))
        read = embed.carrier_report(path, strict=False)
    except (CarrierError, OSError) as exc:
        msg = f"{path} could not be read as a PDF: {exc}"
        raise InputNotValidatableError(msg) from exc
    if read.labels.is_empty and not findings:
        msg = (
            f"{path} carries no PlanLabel data. `planlabel attach` puts some there; a "
            f"document that was never labelled is not invalid, only unlabelled"
        )
        raise InputNotValidatableError(msg)

    pairs = sorted(read.labels.pages.items())
    page_count = read.page_count
    notes: list[str] = []
    findings += _pair_with_document(path, pairs, attached=True, notes=notes)
    for page_index, label in pairs:
        source = f"page {page_index}"
        findings += check_page_document(_round_trip(label), source=source)
        findings += check_page_label(label, source=source, page_count=page_count)
    findings += referential.check_sheet_ids(pairs)
    findings += carrier.check_declaration(
        present=read.declaration, labelled=not read.labels.is_empty, source=path.name
    )
    findings += _check_index(read.labels.index, pairs, source="index")
    findings += _check_model(pairs, model, notes)
    if run_verapdf:
        findings += verapdf.check_verapdf(path, source=path.name)
    else:
        notes.append("the veraPDF pass (PL-PDF-001) was not run; add --verapdf to run it")
    report = Report(
        source=path,
        carrier="pdf",
        pages=tuple(_summary(label, page_index) for page_index, label in pairs),
        notes=tuple(notes),
        strict=strict,
    )
    return report.with_findings(findings)


def _pair_with_document(
    document: Path,
    pairs: Sequence[tuple[int, PageLabel]],
    *,
    attached: bool,
    notes: list[str],
) -> list[Finding]:
    """Check a payload against the document it belongs to.

    Three rules live here and nowhere else, because only a document can decide them:
    PL-REF-008, that each label names a page that exists (and, in a PDF, the page it is
    attached to); PL-GEO-001, that the label's ``page`` block matches that page; and
    PL-CAR-007, that at least one label pairs with something -- 6.5.5's three
    obligations on a reader that pairs a payload with a document.

    Args:
        document: The PDF.
        pairs: The page labels, each with the page it was found on -- which for a PDF
            is where it was attached, and for a sidecar is the page it claims.
        attached: True when the labels came out of this document, so that a
            ``page.index`` disagreeing with the attachment is reportable; False for a
            sidecar, where there is no attachment to disagree with.
        notes: Collected notes, appended to when the pairing cannot be made at all.

    Returns:
        Every finding about the payload's fit to the document.
    """
    findings: list[Finding] = []
    try:
        with pikepdf.open(document) as pdf:
            page_count = len(pdf.pages)
            for page_index, label in pairs:
                source = f"page {page_index}"
                identity = carrier.check_page_identity(
                    label,
                    page_count=page_count,
                    attached_to=page_index if attached else None,
                    source=source,
                )
                findings += identity
                if not identity:
                    findings += geometric.check_page_against_document(pdf, label, source=source)
            findings += carrier.check_pairing(pairs, page_count=page_count, source=document.name)
    except (pikepdf.PdfError, OSError) as exc:
        _LOGGER.warning("could not open %s to measure its pages: %s", document, exc)
        notes.append(
            f"the payload was not checked against {document.name} (PL-REF-008, "
            f"PL-GEO-001, PL-CAR-007): {exc}"
        )
    return findings


# ---------------------------------------------------------------------------
# JSON on disk
# ---------------------------------------------------------------------------
def _validate_json(
    path: Path,
    *,
    model: ifc.IfcModel | None,
    strict: bool,
    run_verapdf: bool,
) -> Report:
    """Validate a sidecar, an index, a page label or an array of page labels.

    Args:
        path: The file.
        model: An opened IFC model, or None.
        strict: Whether warnings count as errors.
        run_verapdf: Whether the caller asked for the veraPDF pass, which needs a PDF.

    Returns:
        The report.

    Raises:
        InputNotValidatableError: If the file is not a PlanLabel document.
        ExternalToolError: If ``run_verapdf`` is set; there is no PDF to run it on.
    """
    if run_verapdf:
        from planlabel.errors import ExternalToolError  # noqa: PLC0415 - one call site

        msg = (
            f"--verapdf validates a PDF, and {path} is not one. Run it against the "
            f"labelled document rather than against its labels"
        )
        raise ExternalToolError(msg)
    raw = schema.read_json_file(path)
    document = schema.parse_json(raw, source=path.name)
    kind = schema.detect_kind(document)
    if kind is None:
        msg = (
            f"{path} is not a PlanLabel document. Pass a labelled PDF, a sidecar "
            f"({{planlabel, index, pages}}), an index, a page label, or an array of "
            f"page labels"
        )
        raise InputNotValidatableError(msg)
    beside = _document_beside(path) if kind is schema.DocumentKind.SIDECAR else None
    page_count = _page_count(beside)
    notes = ["the veraPDF pass (PL-PDF-001) was not run: it needs a PDF"]
    notes.append(_pairing_note(kind, beside))
    findings, pages, carrier_name = _dispatch(
        document, kind, path=path, notes=notes, page_count=page_count
    )
    if beside is not None:
        findings += _pair_with_document(beside, pages, attached=False, notes=notes)
    findings += _check_model(pages, model, notes)
    report = Report(
        source=path,
        carrier=carrier_name,
        pages=tuple(_summary(label, page_index) for page_index, label in pages),
        notes=tuple(notes),
        strict=strict,
    )
    return report.with_findings(findings)


def _page_count(document: Path | None) -> int | None:
    """Return how many pages a document has, when there is one.

    Args:
        document: The PDF, or None.

    Returns:
        Its page count, or None when there is no document or it cannot be opened. The
        count is what makes ``target.pdfPage`` checkable at all.
    """
    if document is None:
        return None
    try:
        with pikepdf.open(document) as pdf:
            return len(pdf.pages)
    except (pikepdf.PdfError, OSError):  # pragma: no cover - reported when it is paired
        return None


def _pairing_note(kind: schema.DocumentKind, beside: Path | None) -> str:
    """Say, in the report, whether a payload on disk was paired with a document.

    Section 6.5.4 permits a reader to be given a sidecar with no document, and requires
    it to make clear that it used the payload without one: with no page to consult,
    every geometric value in the payload is unverified. A reader that paired should say
    so too, so that "no findings" can be told from "nothing was compared".

    Args:
        kind: What the file turned out to be.
        beside: The document found beside it, or None.

    Returns:
        One line for the report's notes.
    """
    if beside is not None:
        return (
            f"the payload was paired with {beside.name}, which 6.5.3 names as this "
            f"sidecar's document, and was checked against its pages"
        )
    if kind is schema.DocumentKind.SIDECAR:
        return (
            "no document was found beside this sidecar, so the payload was validated "
            "without one: page.index, the page dimensions and target.pdfPage "
            "(PL-REF-008, PL-GEO-001, PL-REF-006, PL-CAR-007) are unverified, and no "
            "geometric value in it was checked against a page"
        )
    return (
        "target.pdfPage (PL-REF-006) and the page block against its page (PL-GEO-001) "
        "were not checked: there is no document to check them against"
    )


def _dispatch(
    document: object,
    kind: schema.DocumentKind,
    *,
    path: Path,
    notes: list[str],
    page_count: int | None,
) -> tuple[list[Finding], list[tuple[int, PageLabel]], str]:
    """Run the rules that belong to one shape of JSON document.

    Args:
        document: The parsed JSON.
        kind: Which shape it is.
        path: The file, for naming findings.
        notes: Collected notes.
        page_count: The page count of the document this payload was paired with, or
            None when it was not paired with one.

    Returns:
        The findings, the page labels that loaded paired with the page each claims, and
        the carrier name for the report.
    """
    if kind is schema.DocumentKind.SIDECAR:
        return (*_validate_sidecar(document, path=path, page_count=page_count), "sidecar")
    if kind is schema.DocumentKind.INDEX:
        notes.append(
            "an index on its own names pages whose labels are not here, so the rules "
            "that compare the two (PL-CAR-001 to PL-CAR-003, PL-PRV-006) were not run"
        )
        return (_validate_index_alone(document, path=path), [], "index")
    members = [document] if kind is schema.DocumentKind.PAGE else schema.page_labels_of(document)
    findings: list[Finding] = []
    pages: list[tuple[int, PageLabel]] = []
    for position, member in enumerate(members):
        member_findings, loaded = _validate_one_label(
            member, fallback=f"{path.name} entry {position}", page_count=page_count
        )
        findings += member_findings
        if loaded is not None:
            pages.append((loaded.page.index, loaded))
    findings += referential.check_sheet_ids(sorted(pages, key=lambda pair: pair[0]))
    return (findings, pages, "labels")


def _validate_one_label(
    member: object,
    *,
    fallback: str,
    page_count: int | None = None,
) -> tuple[list[Finding], PageLabel | None]:
    """Validate one page label read from a JSON file.

    Args:
        member: The parsed page label.
        fallback: What to call it when it does not say which page it describes.
        page_count: The page count of the document this label belongs to, when one was
            found; None otherwise.

    Returns:
        Its findings, and the loaded label when it loaded. A label that fails its
        schema is not examined further: 4.3 (2) makes it absent, and every rule below
        the schema would be reading a document the specification says is not there.
    """
    source = _page_source(member, fallback)
    version = schema.check_version(member, source=source)
    if version is not None:
        return ([version], None)
    schema_findings = schema.check_schema(member, schema.DocumentKind.PAGE, source=source)
    if schema_findings:
        return (schema_findings, None)
    parsed = member if isinstance(member, dict) else {}
    findings = check_page_document(parsed, source=source)
    try:
        label = load_page_label(json.dumps(member))
    except ValueError as exc:
        findings.append(
            finding(
                "PL-SCH-002",
                message=f"the schema accepts this label but the model does not: {exc}",
                path="",
                source=source,
            )
        )
        return (findings, None)
    return (
        findings + check_page_label(label, source=source, page_count=page_count),
        label,
    )


def _validate_sidecar(
    document: object,
    *,
    path: Path,
    page_count: int | None,
) -> tuple[list[Finding], list[tuple[int, PageLabel]]]:
    """Validate a sidecar and every page label in it.

    Args:
        document: The parsed sidecar.
        path: The file, for naming findings.
        page_count: The page count of the document beside it, when there is one.

    Returns:
        The findings, and the page labels that loaded paired with the page each claims.
    """
    version = schema.check_version(document, source=path.name)
    if version is not None:
        return ([version], [])
    raw = document if isinstance(document, dict) else {}
    findings = carrier.check_sidecar_versions(raw, source=path.name)
    findings += schema.check_schema(document, schema.DocumentKind.SIDECAR, source=path.name)
    if any(item.code.startswith("PL-SCH") for item in findings):
        return (findings, [])
    try:
        sidecar = load_sidecar(json.dumps(document))
    except ValueError as exc:
        return (
            [
                finding(
                    "PL-SCH-002",
                    message=f"the schema accepts this sidecar but the model does not: {exc}",
                    path="",
                    source=path.name,
                )
            ],
            [],
        )
    findings += carrier.check_sidecar_consistency(sidecar, source=path.name)
    raw_pages = raw.get("pages")
    members = raw_pages if isinstance(raw_pages, list) else []
    pages: list[tuple[int, PageLabel]] = []
    for position, label in enumerate(sidecar.pages):
        parsed = members[position] if position < len(members) else None
        source = f"page {label.page.index}"
        findings += check_page_document(
            parsed if isinstance(parsed, dict) else _round_trip(label), source=source
        )
        findings += check_page_label(label, source=source, page_count=page_count)
        pages.append((label.page.index, label))
    findings += referential.check_sheet_ids(sorted(pages, key=lambda pair: pair[0]))
    findings += _check_index(sidecar.index, pages, source="index")
    return (findings, pages)


def _document_beside(sidecar: Path) -> Path | None:
    """Return the document a sidecar was written for, if it is beside it.

    Section 6.5.3 names a sidecar after its document: ``NAME.pdf`` is described by
    ``NAME.planlabel.json``, and a document with no extension by that name with the
    suffix appended. This is that rule read backwards, and only that: the two
    candidate names, in the sidecar's own directory. 6.5.4 forbids searching parent
    directories, a configured location, or any path found inside the payload, and none
    of the three happens here.

    Pairing is worth the trouble because three normative rules exist only for a reader
    that does it -- 6.5.5's three, which a validator MUST report and which no amount of
    reading the sidecar alone can decide. A sidecar is the carrier for a document that
    must not be modified, and "this payload was written for a different revision" is
    exactly the failure it is exposed to.

    Args:
        sidecar: The sidecar's path.

    Returns:
        The document, or None when neither candidate exists or the sidecar is not
        named by the rule. The document is never required: 6.5.4 permits a reader to
        be given a sidecar alone, and says only that it must be clear it was.
    """
    if not sidecar.name.endswith(SIDECAR_SUFFIX):
        return None
    stem = sidecar.name[: -len(SIDECAR_SUFFIX)]
    for candidate in (sidecar.with_name(f"{stem}.pdf"), sidecar.with_name(stem)):
        if candidate.is_file() and _looks_like_pdf(candidate):
            return candidate
    return None


def _validate_index_alone(document: object, *, path: Path) -> list[Finding]:
    """Validate an index file with no labels beside it.

    Args:
        document: The parsed index.
        path: The file, for naming findings.

    Returns:
        Its findings: the version, the schema, and whether the model accepts it. There
        is nothing else an index can be held to on its own -- every other rule about an
        index compares it with the labels it describes.
    """
    version = schema.check_version(document, source=path.name)
    if version is not None:
        return [version]
    findings = schema.check_schema(document, schema.DocumentKind.INDEX, source=path.name)
    if findings:
        return findings
    try:
        load_label_index(json.dumps(document))
    except ValueError as exc:
        return [
            finding(
                "PL-SCH-002",
                message=f"the schema accepts this index but the model does not: {exc}",
                path="",
                source=path.name,
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Shared tails
# ---------------------------------------------------------------------------
def _check_index(
    index: LabelIndex | None,
    pages: Sequence[tuple[int, PageLabel]],
    *,
    source: str,
) -> list[Finding]:
    """Run the index rules, when the carrier holds an index.

    Args:
        index: The index, or None when there is none.
        pages: The page labels, paired with the page each was found on.
        source: What to call the index in a finding.

    Returns:
        Every disagreement between the index and the labels beside it.
    """
    if index is None:
        return []
    return [
        *carrier.check_index_against_labels(index, pages, source=source),
        *provenance.check_index_provenance(index, pages, source=source),
    ]


def _check_model(
    pages: Sequence[tuple[int, PageLabel]],
    model: ifc.IfcModel | None,
    notes: list[str],
) -> list[Finding]:
    """Run the IFC cross-check, when the caller asked for one.

    Args:
        pages: The page labels, paired with the page each was found on.
        model: The opened model, or None.
        notes: Collected notes, appended to either way.

    Returns:
        Every cross-check violation.
    """
    if model is None:
        notes.append(
            "the model cross-check (PL-IFC-001 to PL-IFC-003) was not run; pass "
            "--ifc MODEL.ifc to run it"
        )
        return []
    findings: list[Finding] = []
    remeasurable = 0
    for page_index, label in pages:
        findings += ifc.check_against_model(label, model, source=f"page {page_index}")
        remeasurable += ifc.remeasurable(label)
    notes.append(
        f"the model cross-check ran against {model.path.name}; {remeasurable} "
        f"dimension(s) named two elements with a GlobalId and could be re-measured"
    )
    return findings
