# planlabel-mcp

An [MCP](https://modelcontextprotocol.io) server that serves PlanLabel-labelled
drawing sets to MCP hosts such as Claude Desktop, over stdio or streamable HTTP.

```bash
uvx planlabel-mcp --root ~/Drawings
```

Tools: `list_sheets`, `get_label`, `find_elements`, `measure`, `validate`, and — only
with `--allow-write` — `attach` and `infer`. Resources: the page schema, the
specification and a folder index.

It is read-only by default, and every path is confined to the configured root: a path
that resolves outside it, symlinks included, is refused. Host configuration and the
full tool reference are in
[docs/mcp.md](https://github.com/srtgn/planlabel/blob/main/docs/mcp.md).

Part of [PlanLabel](https://github.com/srtgn/planlabel). Apache-2.0.
