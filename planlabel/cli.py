# SPDX-License-Identifier: Apache-2.0
"""Command-line interface for PlanLabel.

Four verbs arrive with the PDF carrier in Phase 2, and the validator in Phase 3::

    planlabel attach in.pdf labels.json -o out.pdf
    planlabel read out.pdf [--page N] [--json]
    planlabel strip out.pdf -o clean.pdf
    planlabel sidecar out.pdf
    planlabel validate file.pdf|labels.json [--ifc model.ifc] [--strict] [--report md|json]

The rest arrive with the phases that implement them:

==================  =======
Command             Phase
==================  =======
``attach``          2
``read``            2
``strip``           2
``sidecar``         2
``validate``        3
``samples build``   4
``inspect``         5
``infer``           7
==================  =======

Two output modes, and they are kept apart on purpose. Without ``--json`` the
output is for a person, formatted with rich. With ``--json`` it is exactly one JSON
document on standard output, in PlanLabel's canonical form, with every log line on
standard error -- so that ``planlabel read x.pdf --json | jq`` works and keeps
working.

``validate`` spells the same distinction ``--report md|json``, because that is what
the design brief names it and because its human output is a Markdown document rather
than a table. Both formats go to standard output as plain text, unrendered: a
validation report is something people redirect into a file, attach to a pull request
or pipe to ``jq``, and ANSI escapes in a file called ``report.md`` help nobody.

Exit codes are part of the contract for ``validate`` and are documented in
:mod:`planlabel.validate`: 0 clean, 1 errors found, 2 could not validate.

This is the only module permitted to write to standard output directly. Everything
else in the package reports through :mod:`logging`.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path  # noqa: TC003 - typer resolves annotations at run time
from typing import TYPE_CHECKING, Annotated, Final

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from planlabel import __version__
from planlabel.constants import SCHEMA_VERSION, SPEC_URI
from planlabel.errors import PlanLabelError, ValidatorError
from planlabel.model import (
    Generator,
    LabelIndex,
    PageLabel,
    canonical_json,
    conformance_level,
    load_label_index,
    load_page_label,
    load_sidecar,
)
from planlabel.pdf import embed
from planlabel.validate import render_json_text, render_markdown
from planlabel.validate import validate as run_validation

if TYPE_CHECKING:
    from planlabel.pdf.embed import CarrierReport

console = Console()
errors = Console(stderr=True)

_LOGGER: Final = logging.getLogger("planlabel")


class ReportFormat(StrEnum):
    """How ``planlabel validate`` renders its report.

    ``md`` is for a person and ``json`` for a machine; neither is derived from the
    other, and the JSON shape is documented and versioned in
    :mod:`planlabel.validate.report`.
    """

    MD = "md"
    JSON = "json"


app = typer.Typer(
    name="planlabel",
    help=(
        "Machine-readable semantics for 2D construction drawings. "
        "The PDF page stays the leading document; the label is auxiliary."
    ),
    no_args_is_help=True,
    add_completion=True,
)


def _version_callback(*, value: bool) -> None:
    """Print version information and exit.

    Args:
        value: Whether the ``--version`` flag was given.

    Raises:
        typer.Exit: Always, when ``value`` is true. This is how Typer implements
            eager options.
    """
    if not value:
        return
    console.print(f"planlabel {__version__}")
    console.print(f"schema     {SCHEMA_VERSION}")
    console.print(f"spec       {SPEC_URI}")
    raise typer.Exit


@app.callback()
def main(
    version: Annotated[  # noqa: ARG001 - consumed by its own eager callback
        bool,
        typer.Option(
            "--version",
            "-V",
            help="Show the package, schema and specification versions, then exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Log what is being done, not only what went wrong."),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Log nothing but errors."),
    ] = False,
) -> None:
    """Entry point for the ``planlabel`` command."""
    level = logging.INFO if verbose else (logging.ERROR if quiet else logging.WARNING)
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=errors, show_time=False, show_path=False, markup=False)],
        force=True,
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _fail(message: str) -> typer.Exit:
    """Report an error to standard error and produce the exception that ends the run.

    Args:
        message: What went wrong, and where possible what to do about it.

    Returns:
        The exception to raise. It is returned rather than raised so that the call
        site reads ``raise _fail(...)`` and a type checker can see the function ends
        there.
    """
    errors.print(f"[bold red]error[/bold red] {message}")
    return typer.Exit(1)


def _echo_json(document: object) -> None:
    """Write one JSON document to standard output, in canonical form.

    Args:
        document: Anything :mod:`json` can serialise.
    """
    typer.echo(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True))


def _resolve_mod_date(given: str | None) -> datetime:
    """Decide the timestamp to write into the embedded files.

    The library never reads the clock, so that two runs over the same inputs produce
    the same bytes. The command line is where a clock is allowed, and where a build
    system can take it away again: ``--mod-date`` wins, then ``SOURCE_DATE_EPOCH``,
    then now.

    Args:
        given: The value of ``--mod-date``, an ISO 8601 timestamp, or None.

    Returns:
        A timezone-aware datetime.

    Raises:
        typer.Exit: If either value cannot be read as a timestamp.
    """
    if given is not None:
        try:
            parsed = datetime.fromisoformat(given)
        except ValueError as exc:
            msg = f"--mod-date {given!r} is not an ISO 8601 timestamp ({exc})"
            raise _fail(msg) from exc
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch:
        try:
            return datetime.fromtimestamp(int(epoch), tz=UTC)
        except ValueError as exc:
            msg = f"SOURCE_DATE_EPOCH={epoch!r} is not a whole number of seconds"
            raise _fail(msg) from exc
    return datetime.now(UTC)


def _read_json(path: Path) -> object:
    """Read and parse a JSON file.

    Args:
        path: The file to read.

    Returns:
        The parsed document.

    Raises:
        typer.Exit: If the file cannot be read or is not JSON.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        msg = f"{path} could not be read: {exc}"
        raise _fail(msg) from exc
    except ValueError as exc:
        msg = f"{path} is not valid JSON: {exc}"
        raise _fail(msg) from exc


def _load_labels(path: Path) -> tuple[list[PageLabel], LabelIndex | None]:
    """Read a labels file in any of the three shapes a person may reasonably have.

    A sidecar document carries both the labels and an index; a bare array carries
    several labels; a single object carries one. All three are accepted, because all
    three are things a writer in this project produces.

    Args:
        path: The labels file.

    Returns:
        The page labels, and the index when the file carried one.

    Raises:
        typer.Exit: If the file is not one of the three shapes, or does not validate.
    """
    document = _read_json(path)
    try:
        if isinstance(document, list):
            return [load_page_label(json.dumps(item)) for item in document], None
        if isinstance(document, dict) and "index" in document:
            sidecar = load_sidecar(json.dumps(document))
            return list(sidecar.pages), sidecar.index
        if isinstance(document, dict) and "page" in document:
            return [load_page_label(json.dumps(document))], None
    except ValueError as exc:
        msg = f"{path} is not a valid PlanLabel document:\n{exc}"
        raise _fail(msg) from exc
    msg = (
        f"{path} does not look like labels. Pass a page label, an array of page "
        "labels, or a sidecar document ({'planlabel', 'index', 'pages'}). An index "
        "on its own goes to --index"
    )
    raise _fail(msg)


def _load_index(path: Path) -> LabelIndex:
    """Read an index file.

    Args:
        path: The index file.

    Returns:
        The index.

    Raises:
        typer.Exit: If it does not validate.
    """
    try:
        return load_label_index(json.dumps(_read_json(path)))
    except ValueError as exc:
        msg = f"{path} is not a valid PlanLabel index:\n{exc}"
        raise _fail(msg) from exc


def _generator(moment: datetime) -> Generator:
    """Return the generator block to record in a derived index or sidecar.

    Args:
        moment: The timestamp to record, which the caller has already made
            injectable.

    Returns:
        A generator naming this tool and its version.
    """
    return Generator(name="planlabel", version=__version__, created=moment.isoformat())


def _carrier(source: Path, *, strict: bool = True) -> CarrierReport:
    """Read a carrier, turning any PlanLabel failure into a clean exit.

    Args:
        source: The document or sidecar to read.
        strict: Whether an invalid label is an error.

    Returns:
        The report.

    Raises:
        typer.Exit: If the file cannot be read or a label does not validate.
    """
    try:
        return embed.carrier_report(source, strict=strict)
    except FileNotFoundError as exc:
        msg = f"{source} does not exist"
        raise _fail(msg) from exc
    except OSError as exc:
        msg = f"{source} could not be read: {exc}"
        raise _fail(msg) from exc
    except PlanLabelError as exc:
        raise _fail(str(exc)) from exc


def _carrier_line(report: CarrierReport) -> str:
    """Describe the carrier itself: what it is, how big, and what it claims.

    Args:
        report: What the carrier holds.

    Returns:
        One line for a person. A sidecar has no page count and cannot be signed or
        carry a declaration, so it says less.
    """
    parts: list[str] = [report.carrier]
    if report.carrier == "pdf":
        parts.append(f"{report.page_count} page(s)")
        parts.append("signed" if report.signature and report.signature.signed else "not signed")
        parts.append(f"declaration {'present' if report.declaration else 'absent'}")
    return "; ".join(parts)


def _index_line(index: LabelIndex | None) -> str:
    """Describe the document-level index.

    Args:
        index: The index, or None when the carrier has none.

    Returns:
        One line for a person.
    """
    if index is None:
        return "absent"
    parts = ["present", f"listing {len(index.pages)} page(s)"]
    if index.provenance is not None:
        parts.append(f"provenance {index.provenance.value}")
    return ", ".join(parts)


def _summary_table(report: CarrierReport) -> Table:
    """Build the table of labelled pages shown by ``planlabel read``.

    Args:
        report: What the carrier holds.

    Returns:
        A rich table, one row per labelled page.
    """
    table = Table(title=None, header_style="bold", box=None, pad_edge=False)
    for column in ("page", "sheet", "level", "provenance", "viewports", "elements", "annotations"):
        table.add_column(column, justify="right" if column != "sheet" else "left")
    table.add_column("title", overflow="fold")
    for page_index, label in sorted(report.labels.pages.items()):
        table.add_row(
            str(page_index),
            label.sheet.sheet_id,
            conformance_level(label).value,
            label.provenance.value,
            str(len(label.viewports or [])),
            str(len(label.elements or [])),
            str(len(label.annotations or [])),
            label.sheet.title or "",
        )
    return table


# ---------------------------------------------------------------------------
# attach
# ---------------------------------------------------------------------------
@app.command()
def attach(
    pdf_in: Annotated[Path, typer.Argument(help="The drawing PDF to label.", exists=True)],
    labels: Annotated[
        Path,
        typer.Argument(
            help="A page label, an array of page labels, or a sidecar document.",
            exists=True,
        ),
    ],
    out: Annotated[Path, typer.Option("--out", "-o", help="Where to write the labelled PDF.")],
    index: Annotated[
        Path | None,
        typer.Option("--index", help="A separate index file. Derived from the labels if omitted."),
    ] = None,
    mod_date: Annotated[
        str | None,
        typer.Option(
            "--mod-date",
            help=(
                "ISO 8601 timestamp recorded on every embedded file. "
                "Defaults to SOURCE_DATE_EPOCH, then to now."
            ),
        ),
    ] = None,
    break_signature: Annotated[
        bool,
        typer.Option(
            "--break-signature",
            help="Label a signed document anyway, accepting that its signature is voided.",
        ),
    ] = False,
    no_compress: Annotated[
        bool,
        typer.Option("--no-compress", help="Store the embedded labels as plain, readable JSON."),
    ] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="Report what was written as JSON.")
    ] = False,
) -> None:
    """Attach page labels to a PDF, leaving every page exactly as it looks now."""
    moment = _resolve_mod_date(mod_date)
    page_labels, carried_index = _load_labels(labels)
    if index is not None and carried_index is not None:
        msg = f"{labels} already carries an index; pass one or the other, not both"
        raise _fail(msg)
    document_index = _load_index(index) if index is not None else carried_index
    if document_index is None:
        document_index = embed.build_index(page_labels, generator=_generator(moment))
        _LOGGER.info("no index given; derived one from the %d label(s)", len(page_labels))
    try:
        report = embed.attach(
            pdf_in,
            page_labels,
            document_index,
            out,
            mod_date=moment,
            break_signature=break_signature,
            compress_labels=not no_compress,
        )
    except PlanLabelError as exc:
        raise _fail(str(exc)) from exc
    except OSError as exc:
        msg = f"{out} could not be written: {exc}"
        raise _fail(msg) from exc

    if as_json:
        _echo_json(
            {
                "output": str(out),
                "pages": list(report.page_indices),
                "files": list(report.filenames),
                "indexWritten": report.index_written,
                "declarationAdded": report.declaration_added,
                "pdfVersion": report.pdf_version,
                "modDate": embed.pdf_date(moment),
            }
        )
        return
    console.print(f"[bold green]labelled[/bold green] {out}")
    console.print(f"  pages    {', '.join(str(page) for page in report.page_indices)}")
    console.print(f"  files    {', '.join(report.filenames)}")
    console.print(
        f"  declaration {'added' if report.declaration_added else 'already present'}"
        f"; PDF {report.pdf_version}; /ModDate {embed.pdf_date(moment)}"
    )


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------
@app.command()
def read(
    source: Annotated[
        Path,
        typer.Argument(help="A labelled PDF, or a sidecar JSON file.", exists=True),
    ],
    page: Annotated[
        int | None,
        typer.Option("--page", help="Show only this zero-based page."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Write the labels to standard output as JSON."),
    ] = False,
    lenient: Annotated[
        bool,
        typer.Option(
            "--lenient",
            help="Skip labels that do not validate instead of failing, as a reader must.",
        ),
    ] = False,
) -> None:
    """Read the labels a document carries, from either carrier."""
    report = _carrier(source, strict=not lenient)
    labels = report.labels
    if labels.is_empty:
        msg = (
            f"{source} carries no PlanLabel data. `planlabel attach` puts some there; "
            "a document that was never labelled is not an error, only empty"
        )
        raise _fail(msg)
    if page is not None and page not in labels.pages:
        listed = ", ".join(str(index) for index in sorted(labels.pages)) or "none"
        msg = f"page {page} carries no label; labelled page(s): {listed}"
        raise _fail(msg)

    if as_json:
        if page is not None:
            typer.echo(canonical_json(labels.pages[page]), nl=False)
            return
        typer.echo(canonical_json(labels.to_sidecar()), nl=False)
        return

    console.print(f"[bold]{source}[/bold]")
    console.print(f"  carrier {_carrier_line(report)}")
    console.print(f"  index   {_index_line(labels.index)}")
    if page is None:
        console.print(_summary_table(report))
        console.print(f"  files   {', '.join(report.filenames)}")
        return
    console.print(_summary_table(report))
    console.print()
    typer.echo(canonical_json(labels.pages[page]), nl=False)


# ---------------------------------------------------------------------------
# strip
# ---------------------------------------------------------------------------
@app.command()
def strip(
    pdf_in: Annotated[Path, typer.Argument(help="The labelled PDF.", exists=True)],
    out: Annotated[Path, typer.Option("--out", "-o", help="Where to write the stripped PDF.")],
    as_json: Annotated[
        bool, typer.Option("--json", help="Report what was removed as JSON.")
    ] = False,
) -> None:
    """Remove every trace of PlanLabel from a PDF, and nothing else."""
    try:
        report = embed.strip(pdf_in, out)
    except PlanLabelError as exc:
        raise _fail(str(exc)) from exc
    except OSError as exc:
        msg = f"{out} could not be written: {exc}"
        raise _fail(msg) from exc

    if as_json:
        _echo_json(
            {
                "output": str(out),
                "files": list(report.filenames),
                "associationsCleared": report.associations_cleared,
                "declarationRemoved": report.declaration_removed,
                "declarationRemaining": report.declaration_remaining,
                "metadataRemoved": report.metadata_removed,
            }
        )
        return
    if report.is_empty:
        console.print(f"[bold]{pdf_in}[/bold] carried no PlanLabel data; wrote a copy to {out}")
        return
    console.print(f"[bold green]stripped[/bold green] {out}")
    console.print(f"  removed  {', '.join(report.filenames)}")
    console.print(
        f"  cleared  {report.associations_cleared} /AF entr"
        f"{'y' if report.associations_cleared == 1 else 'ies'}"
        f"; declaration {'removed' if report.declaration_removed else 'not found'}"
    )
    if report.declaration_remaining:
        console.print(
            "  [yellow]note[/yellow] a PlanLabel declaration written by another tool "
            "remains; removing it would mean rewriting that tool's metadata"
        )


# ---------------------------------------------------------------------------
# sidecar
# ---------------------------------------------------------------------------
@app.command()
def sidecar(
    pdf_in: Annotated[Path, typer.Argument(help="The drawing PDF.", exists=True)],
    out: Annotated[
        Path | None,
        typer.Option("--out", "-o", help="Where to write it. Defaults to X.planlabel.json."),
    ] = None,
    labels: Annotated[
        Path | None,
        typer.Option(
            "--labels",
            help=(
                "Labels for a PDF that does not carry any -- a signed document, say. "
                "Read from the PDF itself when omitted."
            ),
            exists=True,
        ),
    ] = None,
    index: Annotated[
        Path | None,
        typer.Option("--index", help="An index to write with --labels. Derived if omitted."),
    ] = None,
    mod_date: Annotated[
        str | None,
        typer.Option("--mod-date", help="ISO 8601 timestamp recorded as generator.created."),
    ] = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Report what was written as JSON.")
    ] = False,
) -> None:
    """Write the sidecar twin of a PDF, without touching the PDF."""
    moment = _resolve_mod_date(mod_date)
    page_labels: list[PageLabel] | None = None
    document_index: LabelIndex | None = None
    if labels is not None:
        page_labels, carried_index = _load_labels(labels)
        if index is not None and carried_index is not None:
            msg = f"{labels} already carries an index; pass one or the other, not both"
            raise _fail(msg)
        document_index = _load_index(index) if index is not None else carried_index
    elif index is not None:
        msg = "--index only makes sense with --labels"
        raise _fail(msg)
    try:
        written = embed.write_sidecar(
            pdf_in,
            out,
            labels=page_labels,
            index=document_index,
            generator=_generator(moment),
        )
    except PlanLabelError as exc:
        raise _fail(str(exc)) from exc
    except OSError as exc:
        msg = f"the sidecar could not be written: {exc}"
        raise _fail(msg) from exc

    written_labels = embed.read_sidecar(written)
    if as_json:
        _echo_json(
            {
                "output": str(written),
                "pages": sorted(written_labels.pages),
                "bytes": written.stat().st_size,
            }
        )
        return
    console.print(f"[bold green]wrote[/bold green] {written}")
    console.print(
        f"  {len(written_labels.pages)} page label(s), {written.stat().st_size} bytes; "
        "the PDF was not modified"
    )


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------
@app.command()
def validate(
    source: Annotated[
        Path,
        typer.Argument(
            help="A labelled PDF, a sidecar, a page label, an array of them, or an index.",
            exists=True,
        ),
    ],
    ifc: Annotated[
        Path | None,
        typer.Option(
            "--ifc",
            help=(
                "Cross-check every ifcGuid and re-measure dimensions against this IFC "
                "model. Needs the 'ifc' extra."
            ),
            exists=True,
        ),
    ] = None,
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Count warnings as errors when deciding the exit code."),
    ] = False,
    report: Annotated[
        ReportFormat,
        typer.Option("--report", help="md for a person, json for a machine."),
    ] = ReportFormat.MD,
    verapdf: Annotated[
        bool,
        typer.Option(
            "--verapdf",
            help="Also run veraPDF over the document. Fails the run if veraPDF cannot run.",
        ),
    ] = False,
) -> None:
    """Validate a label against the schema, the page, its own references and a model.

    Exits 0 when there are no errors, 1 when there are, and 2 when the input could not
    be validated at all -- it is not a PlanLabel document, or a check that was asked
    for could not be made. A label with warnings and no errors is conforming and exits
    0; pass --strict to hold it to the SHOULDs as well.
    """
    try:
        result = run_validation(source, ifc_model=ifc, strict=strict, run_verapdf=verapdf)
    except ValidatorError as exc:
        errors.print(f"[bold red]cannot validate[/bold red] {exc}")
        raise typer.Exit(2) from exc
    except PlanLabelError as exc:
        raise _fail(str(exc)) from exc
    except OSError as exc:
        msg = f"{source} could not be read: {exc}"
        raise _fail(msg) from exc

    rendered = render_json_text(result) if report is ReportFormat.JSON else render_markdown(result)
    typer.echo(rendered, nl=False)
    if result.exit_code:
        raise typer.Exit(result.exit_code)


if __name__ == "__main__":  # pragma: no cover
    app()
