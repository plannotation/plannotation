# SPDX-License-Identifier: Apache-2.0
"""Packaged JSON Schema files: the single source of truth for the label format.

Three documents ship here as package data, so that a validator works from an
installed wheel with no network and no repository checkout:

``plannotation-0.1.json``
    The page label. One document describes one drawing page.

``plannotation-index-0.1.json``
    The document-level index, embedded once per PDF, recording which pages are
    labelled and at what conformance level. Deliberately self-contained, with no
    cross-file ``$ref``, so a third-party reader can validate an index without also
    fetching the page-label schema.

``plannotation-sidecar-0.1.json``
    The sidecar twin: an index and every page label of one document, in one file
    written beside it as ``X.plannotation.json``. Self-contained on the same terms, by
    inlining the other two documents verbatim rather than referencing them.

Load them through :func:`plannotation.model.page_schema`,
:func:`plannotation.model.index_schema` and :func:`plannotation.model.sidecar_schema`
rather than by filesystem path. The pydantic models in :mod:`plannotation.model` follow
these documents, never the reverse.
"""

from __future__ import annotations
