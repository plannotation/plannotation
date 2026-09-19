# planlabel-mcp

An [MCP](https://modelcontextprotocol.io) server that exposes PlanLabel-labelled
drawing sets to MCP hosts such as Claude Desktop, ChatGPT and Copilot, over stdio or
streamable HTTP.

**Status: scaffold. The server lands in Phase 6.**

It is read-only by default: tools that write require `--allow-write`, and every path
is confined to a configured root directory.

Part of [PlanLabel](https://github.com/srtgn/planlabel). Apache-2.0.
