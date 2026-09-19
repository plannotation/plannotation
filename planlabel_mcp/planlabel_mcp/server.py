# SPDX-License-Identifier: Apache-2.0
"""FastMCP server exposing PlanLabel tools and resources.

Planned tools: ``list_sheets``, ``get_label``, ``find_elements``, ``measure``,
``validate``, ``attach``, ``infer`` and ``bench_run``. Planned resources: the JSON
Schema, the specification, and the index of a folder of PDFs.

The server is read-only by default; writing tools require ``--allow-write`` and every
path is confined to a configured root.

Implemented in Phase 6.
"""

from __future__ import annotations

from typing import NoReturn


def main() -> NoReturn:
    """Console-script entry point for ``planlabel-mcp``.

    Raises:
        SystemExit: Always, until Phase 6 implements the server. Exiting with a
            message rather than starting a half-built server keeps an MCP host from
            silently connecting to something that cannot answer.
    """
    msg = "planlabel-mcp is not implemented yet; the MCP server lands in Phase 6."
    raise SystemExit(msg)
