# SPDX-License-Identifier: Apache-2.0
"""Project-wide constants.

Every public Plannotation URL is derived from :data:`BASE_URL`. Nothing else in the
code base may hard-code the host: if the specification ever moves, this module is
the only file that changes.
"""

from __future__ import annotations

from typing import Final

#: Root of every public Plannotation URL. Published via GitHub Pages.
BASE_URL: Final = "https://plannotation.github.io"

#: Version of the label format implemented here. Distinct from the package version.
SCHEMA_VERSION: Final = "0.1"

#: ``$id`` of the page-label JSON Schema.
SCHEMA_ID: Final = f"{BASE_URL}/schema/{SCHEMA_VERSION}/plannotation.schema.json"

#: ``$id`` of the document-level index JSON Schema.
INDEX_SCHEMA_ID: Final = f"{BASE_URL}/schema/{SCHEMA_VERSION}/plannotation-index.schema.json"

#: Canonical URI of the specification, and the exact value written as ``conformsTo``
#: in the XMP PDF Declaration of a labelled PDF.
SPEC_URI: Final = f"{BASE_URL}/spec/{SCHEMA_VERSION}"

#: Filename of the document-level index embedded in a labelled PDF.
INDEX_FILENAME: Final = "plannotation-index.json"

#: MIME type recorded as ``/Subtype`` on every embedded Plannotation file.
LABEL_MIME_TYPE: Final = "application/json"

#: Suffix of the sidecar twin written for consumers that cannot read attachments.
SIDECAR_SUFFIX: Final = ".plannotation.json"


def page_label_filename(page_index: int) -> str:
    """Return the embedded filename for a page label.

    Args:
        page_index: Zero-based PDF page index.

    Returns:
        The attachment filename, zero-padded to four digits so that attachment
        listings sort in page order.

    Raises:
        ValueError: If ``page_index`` is negative.

    Examples:
        >>> page_label_filename(0)
        'plannotation-p0000.json'
        >>> page_label_filename(42)
        'plannotation-p0042.json'
    """
    if page_index < 0:
        msg = f"page_index must be non-negative, got {page_index}"
        raise ValueError(msg)
    return f"plannotation-p{page_index:04d}.json"
