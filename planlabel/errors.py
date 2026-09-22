# SPDX-License-Identifier: Apache-2.0
"""The exception hierarchy every PlanLabel module raises from.

One root, :class:`PlanLabelError`, so that a program embedding PlanLabel can catch
everything this library raises deliberately with a single ``except`` clause, and
distinguish it from the ``OSError`` of an unreadable file or the
``pydantic.ValidationError`` of a malformed model.

Two branches hang off it: :class:`CarrierError` for anything about getting label
data into or out of a document, and :class:`RenderError` for anything about
rasterising one. Each leaf names a failure a caller can actually do something
about, and every message raised in this package is expected to say what was wrong
and what to do instead.

This module deliberately imports nothing. It is the one module every other module
may depend on without creating a cycle.
"""

from __future__ import annotations

__all__ = [
    "AppearanceChangedError",
    "AttachmentConflictError",
    "CarrierError",
    "DeclarationError",
    "EncryptedPdfError",
    "InvalidLabelError",
    "LabelMismatchError",
    "LabelNotFoundError",
    "PlanLabelError",
    "RenderError",
    "SignedPdfError",
]


class PlanLabelError(Exception):
    """Base class for every error PlanLabel raises on purpose."""


class CarrierError(PlanLabelError):
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
    """A file PlanLabel would embed is already attached to the document.

    Raised rather than overwriting it: the existing file may belong to another
    producer, and replacing it silently would both destroy data and leave a foreign
    file for :func:`planlabel.pdf.embed.strip` to delete later.
    """


class LabelNotFoundError(CarrierError):
    """The document carries no PlanLabel data at all."""


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


class RenderError(PlanLabelError):
    """A page could not be rasterised, or two documents cannot be compared."""


class AppearanceChangedError(RenderError):
    """Two renderings of the same page differ.

    The governing principle of the specification is that writing a label never
    changes how a page looks. This is the error that fires when it did.
    """
