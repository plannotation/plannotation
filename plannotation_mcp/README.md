# plannotation-mcp

An [MCP](https://modelcontextprotocol.io) server that serves plannotated drawing sets
to MCP hosts such as Claude Desktop, over stdio or streamable HTTP.

```bash
uvx plannotation-mcp --root ~/Drawings
```

Tools: `list_sheets`, `get_plannotation`, `find_elements`, `measure`, `validate`, and — only
with `--allow-write` — `attach` and `infer`. Resources: the page schema, the
specification and a folder index.

It is read-only by default, and every path is confined to the configured root: a path
that resolves outside it, symlinks included, is refused. Host configuration and the
full tool reference are in
[docs/mcp.md](https://github.com/plannotation/plannotation/blob/main/docs/mcp.md).

Part of [Plannotation](https://github.com/plannotation/plannotation). Apache-2.0.
