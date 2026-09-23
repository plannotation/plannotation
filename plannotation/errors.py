# SPDX-License-Identifier: Apache-2.0
"""The exception hierarchy every Plannotation module raises from.

One root, :class:`PlannotationError`, so that a program embedding Plannotation can catch
everything this library raises deliberately with a single ``except`` clause, and
distinguish it from the ``OSError`` of an unreadable file or the
``pydantic.ValidationError`` of a malformed model.

Three branches hang off it: :class:`CarrierError` for anything about getting label
data into or out of a document, :class:`RenderError` for anything about rasterising
one, and :class:`ValidatorError` for a validation run that could not be made at all.
Each leaf names a failure a caller can actually do something about, and every message
raised in this package is expected to say what was wrong and what to do instead.

:class:`ValidatorError` is the branch that is *not* about the document being wrong.
A validator has three outcomes and only two of them are about the label: it found
nothing, it found something, or it could not look. The third is what this branch
carries, and it is why ``plannotation validate`` has an exit code 2 distinct from its
exit code 1 -- a pipeline must be able to tell a broken drawing from a broken
toolchain, and a validator that reported "no errors" because it never ran a check has
said something false.

This module deliberately imports nothing. It is the one module every other module
may depend on without creating a cycle.
"""

from __future__ import annotations

__all__ = [
    "AppearanceChangedError",
    "AttachmentConflictError",
    "BenchError",
    "CarrierError",
    "DeclarationError",
    "EncryptedPdfError",
    "ExportError",
    "ExternalToolError",
    "InputNotValidatableError",
    "InvalidLabelError",
    "LabelMismatchError",
    "LabelNotFoundError",
    "MissingExtraError",
    "PlannotationError",
    "RenderError",
    "SignedPdfError",
    "ValidatorError",
]


class PlannotationError(Exception):
    """Base class for every error Plannotation raises on purpose."""


class CarrierError(PlannotationError):
    """Reading or writing label data in a carrier failed."""


class SignedPdfError(CarrierError):
    """The input PDF carries a digital signature that writing to it would void.

    pikepdf and qpdf rewrite a document in full and cannot append an incremental
    update, so there is no way to add an attachment to a signed file without
    invalidating the signature. The two honest ways forward -- a sidecar file, or an
    explicit decision to break the signature -- are named in the message.
    """


class EncryptedPdfError(CarrierError):
    """The input PDF is encrypted, and saving it would change its encryption."""


class AttachmentConflictError(CarrierError):
    """A file Plannotation would embed is already attached to the document.

    Raised rather than overwriting it: the existing file may belong to another
    producer, and replacing it silently would both destroy data and leave a foreign
    file for :func:`plannotation.pdf.embed.strip` to delete later.
    """


class LabelNotFoundError(CarrierError):
    """The document carries no Plannotation data at all."""


class InvalidLabelError(CarrierError):
    """A label or index was found but does not validate against its schema.

    Section 4.3 of the specification requires a reader to treat such a label as
    absent. The reading functions in this package raise by default -- a person
    running a tool wants to be told -- and behave as 4.3 requires when asked to be
    lenient.
    """


class LabelMismatchError(CarrierError):
    """A label contradicts the document it is being attached to, or its index.

    A page size that disagrees with the page, a rotation that disagrees with
    ``/Rotate``, two labels claiming one page, or an index entry naming a page that
    is not being labelled. Each would produce a document whose label is wrong about
    the page it is stapled to, which is worse than no label at all.
    """


class DeclarationError(CarrierError):
    """The XMP PDF Declaration could not be written or removed."""


class RenderError(PlannotationError):
    """A page could not be rasterised, or two documents cannot be compared."""


class AppearanceChangedError(RenderError):
    """Two renderings of the same page differ.

    The governing principle of the specification is that writing a label never
    changes how a page looks. This is the error that fires when it did.
    """


class ValidatorError(PlannotationError):
    """A validation run could not be made, so its result says nothing.

    Distinct from every finding a validator reports. A finding is a statement about
    the document; this is a statement about the run. ``plannotation validate`` turns it
    into exit code 2, which is neither the 0 of a clean document nor the 1 of a
    defective one.
    """


class InputNotValidatableError(ValidatorError):
    """The input is not something this validator can examine.

    An unreadable file, a file that is not JSON and not a PDF, a PDF carrying no
    Plannotation data at all, or a document past one of the reader's bounds. In each case
    there was nothing to validate, which is not the same as validating something and
    finding it clean.
    """


class MissingExtraError(ValidatorError):
    """A check was asked for whose optional dependency is not installed.

    ``--ifc`` needs ifcopenshell, which lives behind the ``ifc`` extra and is
    deliberately absent from a default install. Silently skipping the check would
    report a clean cross-check that was never made, so the run ends instead. The
    message names the extra to install.
    """


class ExternalToolError(ValidatorError):
    """An external command a check depends on is absent or cannot run.

    ``--verapdf`` shells out to veraPDF, which is a Java application behind a shell
    wrapper and can therefore sit on ``PATH`` while being unusable. As with
    :class:`MissingExtraError`, the run ends rather than reporting a pass it did not
    establish.
    """


class ExportError(PlannotationError):
    """The authored exporter could not produce a drawing it would stand behind.

    Raised where continuing would write a document whose label describes something
    other than the page it is attached to -- a converted page that is not the size the
    sheet declared, or a view the serializer drew nothing into.
    """


class BenchError(PlannotationError):
    """The benchmark could not run: no credentials, no ``bench`` extra, or an API error.

    Every answer obtained before the failure is already in the response cache, so the
    same command resumes where this one stopped without paying for anything twice.
    """
