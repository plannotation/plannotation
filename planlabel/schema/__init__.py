# SPDX-License-Identifier: Apache-2.0
"""Packaged JSON Schema files: the single source of truth for the label format.

Two documents ship here as package data, so that a validator works from an installed
wheel with no network and no repository checkout:

``planlabel-0.1.json``
    The page label. One document describes one drawing page.

``planlabel-index-0.1.json``
    The document-level index, embedded once per PDF, recording which pages are
    labelled and at what conformance level. Deliberately self-contained, with no
    cross-file ``$ref``, so a third-party reader can validate an index without also
    fetching the page-label schema.

Load them through :func:`planlabel.model.page_schema` and
:func:`planlabel.model.index_schema` rather than by filesystem path. The pydantic
models in :mod:`planlabel.model` follow these documents, never the reverse.
"""

from __future__ import annotations
