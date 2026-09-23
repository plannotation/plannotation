# SPDX-License-Identifier: Apache-2.0
"""Plannotation MCP server.

Exposes plannotated drawing projects to MCP hosts such as Claude Desktop, ChatGPT and
Copilot, over stdio or streamable HTTP.

The server is **read-only by default**. Tools that write require ``--allow-write``,
and every path is confined to a configured root directory. The server itself is in
:mod:`plannotation_mcp.server`.
"""

from __future__ import annotations

__version__ = "0.1.0.dev0"

__all__ = ["__version__"]
