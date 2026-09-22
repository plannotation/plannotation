# The MCP server

`planlabel-mcp` lets an MCP host -- Claude Desktop, ChatGPT, Copilot, or anything else
that speaks the Model Context Protocol -- ask what is on a construction drawing and get
answers read from the drawing's label rather than guessed from its pixels.

## Run it

```bash
uvx planlabel-mcp --root ~/Drawings
```

Over stdio by default. Add `--http` for streamable HTTP.

## Claude Desktop

Add this to `claude_desktop_config.json` (on macOS,
`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "planlabel": {
      "command": "uvx",
      "args": ["planlabel-mcp", "--root", "/Users/you/Drawings"]
    }
  }
}
```

Restart Claude Desktop, and the tools appear.

## Tools

| Tool | What it does | Writes? |
| --- | --- | --- |
| `list_sheets` | Every labelled sheet in a PDF, a sidecar, or a folder, with its level. | no |
| `get_label` | One page's label in full. | no |
| `find_elements` | Elements whose IFC class, tag or name contains a query. | no |
| `measure` | Paper and model distance between two items on a page. | no |
| `validate` | The validator's report, optionally against an IFC model. | no |
| `attach` | Attach page labels to a PDF, writing a new file. | **yes** |
| `infer` | Reconstruct labels for an unlabelled PDF, writing a copy. | **yes** |

## Resources

| URI | Content |
| --- | --- |
| `planlabel://schema/page` | The page-label JSON Schema. |
| `planlabel://spec` | Where the specification is published. |
| `planlabel://index` | Every labelled sheet under the root. |

## Safety

**Read-only by default.** `attach` and `infer` are refused unless the server was started
with `--allow-write`. A host can be talked into writing a file by a prompt hidden in a
drawing it is reading; the safe default is that it cannot.

```bash
uvx planlabel-mcp --root ~/Drawings --allow-write
```

**Confined to a root.** Every path a tool receives is resolved -- symlinks included --
and refused unless it lies inside `--root`. `../`, an absolute path elsewhere and a
symlink pointing out of the tree are all refused. Paths in results are reported relative
to the root, so a host never learns what lies above it.

**Labels are data.** A label's text is returned as data. The server's instructions tell
the host not to follow instructions that appear inside a label, but a host that obeys
text it reads in a drawing has a problem no server can fully fix.

## Measuring

`measure` reports the paper distance between the centres of two items, in millimetres.
It reports a model distance as well only when both items sit in the same viewport, that
viewport carries a `paperToPlane`, and the label declares `model.lengthUnit`. Otherwise
it says why it did not: a number without a unit is not a length (SPEC 3.5), and a
distance between two different viewports means nothing.
