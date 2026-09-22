# SPDX-License-Identifier: Apache-2.0
"""The MCP server, exercised through the MCP interface on the samples.

Design brief section 11's gate is "pytest with the mcp client exercising every tool on the
samples", so every tool is called here the way a host calls it -- by name, with JSON
arguments, through the server -- rather than by calling the Python function underneath.

Two properties matter more than any single tool and are tested hardest. The server is
read-only by default, because a host can be talked into writing a file by a prompt
embedded in a drawing it is reading. And every path is confined to a root, because a
path is attacker-controlled whenever the conversation is.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from planlabel_mcp.server import (
    PathOutsideRootError,
    ServerConfig,
    WriteNotAllowedError,
    build_server,
)

SAMPLES = Path(__file__).parent.parent / "samples"
TOOLS = {"list_sheets", "get_label", "find_elements", "measure", "validate", "attach", "infer"}

pytestmark = pytest.mark.skipif(
    not (SAMPLES / "floorplan" / "sheet.labelled.pdf").exists(),
    reason="samples are not built; run make samples",
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Copy the samples into a fresh root the server may read.

    Args:
        tmp_path: pytest's temporary directory.

    Returns:
        The root.
    """
    target = tmp_path / "drawings"
    shutil.copytree(SAMPLES, target)
    return target


def call(config: ServerConfig, tool: str, **arguments: Any) -> Any:  # noqa: ANN401
    """Call a tool through the MCP server, as a host would.

    Args:
        config: The server's confinement.
        tool: The tool's name.
        **arguments: Its arguments, as a host would send them.

    Returns:
        The tool's structured result.

    Raises:
        RuntimeError: If the server reports an error, with its message.
    """
    server = build_server(config)
    try:
        result = asyncio.run(server.call_tool(tool, arguments))
    except Exception as exc:
        # mcp 2.x raises rather than returning an error result, and chains the tool's
        # own exception as the cause. The cause's message is what a host would show.
        cause = exc.__cause__ or exc
        raise RuntimeError(str(cause)) from exc
    if getattr(result, "is_error", False):
        text = " ".join(getattr(item, "text", "") for item in result.content)
        raise RuntimeError(text)
    return result.structured_content


class TestTheToolSurface:
    """The tools and resources design brief section 11 names, and nothing else."""

    def test_every_named_tool_is_registered(self, root: Path) -> None:
        """Under the names the design brief gives them."""
        server = build_server(ServerConfig(root=root))
        assert {tool.name for tool in asyncio.run(server.list_tools())} == TOOLS

    def test_every_tool_describes_itself(self, root: Path) -> None:
        """A host shows the description to a model deciding what to call."""
        server = build_server(ServerConfig(root=root))
        for tool in asyncio.run(server.list_tools()):
            assert tool.description

    def test_the_resources_are_the_schema_the_spec_and_an_index(self, root: Path) -> None:
        """Section 11: the schema, the specification, and the index of a folder."""
        server = build_server(ServerConfig(root=root))
        uris = {str(resource.uri) for resource in asyncio.run(server.list_resources())}
        assert uris == {"planlabel://schema/page", "planlabel://spec", "planlabel://index"}

    def test_the_schema_resource_is_the_schema(self, root: Path) -> None:
        """So a host can validate what it is handed without fetching anything."""
        server = build_server(ServerConfig(root=root))
        contents = list(asyncio.run(server.read_resource("planlabel://schema/page")))
        schema = json.loads(contents[0].content)
        assert schema["title"] == "PlanLabel 0.1"


class TestTheReadingTools:
    """Every read-only tool, on the samples."""

    def test_list_sheets_finds_all_three(self, root: Path) -> None:
        """One labelled sheet per sample, with its level."""
        result = call(ServerConfig(root=root), "list_sheets", path=".")
        sheets = {sheet["sheetId"]: sheet for sheet in result["sheets"]}
        assert set(sheets) == {"ARC-101", "TWP-201", "ARC-301"}
        assert all(sheet["level"] == "L3" for sheet in sheets.values())

    def test_get_label_returns_the_whole_label(self, root: Path) -> None:
        """Exactly what the carrier holds."""
        result = call(
            ServerConfig(root=root), "get_label", pdf="floorplan/sheet.labelled.pdf", page=0
        )
        assert result["label"]["sheet"]["id"] == "ARC-101"
        assert len(result["label"]["elements"]) == 9

    def test_get_label_on_an_unlabelled_page_says_so(self, root: Path) -> None:
        """And names the pages that are labelled, so the next call can be right."""
        with pytest.raises(RuntimeError, match="carries no label"):
            call(ServerConfig(root=root), "get_label", pdf="floorplan/sheet.labelled.pdf", page=9)

    def test_find_elements_by_class(self, root: Path) -> None:
        """Case-insensitive, on class, tag and name."""
        result = call(
            ServerConfig(root=root),
            "find_elements",
            pdf="positionsplan/sheet.labelled.pdf",
            query="ifccolumn",
        )
        assert len(result["matches"]) == 9
        assert {match["ifcClass"] for match in result["matches"]} == {"IfcColumn"}

    def test_find_elements_by_mark(self, root: Path) -> None:
        """A person looking for Pos. 3 searches for Pos. 3."""
        result = call(
            ServerConfig(root=root),
            "find_elements",
            pdf="floorplan/sheet.labelled.pdf",
            query="Pos. 3",
        )
        assert [match["tag"] for match in result["matches"]] == ["Pos. 3"]

    def test_measure_between_two_grids(self, root: Path) -> None:
        """The outer grids on the floor plan are eight metres apart in the model."""
        result = call(
            ServerConfig(root=root),
            "measure",
            pdf="floorplan/sheet.labelled.pdf",
            page=0,
            id_a="g-A",
            id_b="g-D",
        )
        assert result["paperMm"] > 0
        assert result["modelUnit"] == "m"
        assert result["model"] == pytest.approx(8.0, abs=0.01)

    def test_measure_an_unknown_id_says_which(self, root: Path) -> None:
        """So the host can correct the call rather than guess."""
        with pytest.raises(RuntimeError, match="nonesuch"):
            call(
                ServerConfig(root=root),
                "measure",
                pdf="floorplan/sheet.labelled.pdf",
                page=0,
                id_a="g-A",
                id_b="nonesuch",
            )

    def test_validate_reports_the_sample_clean(self, root: Path) -> None:
        """The validator's own report, through the server."""
        result = call(ServerConfig(root=root), "validate", pdf="section/sheet.labelled.pdf")
        assert result["findings"] == []


class TestReadOnlyByDefault:
    """A host can be talked into writing by a drawing it reads, so it cannot by default."""

    def test_attach_is_refused_without_allow_write(self, root: Path) -> None:
        """Refused, and told how to enable it."""
        with pytest.raises(RuntimeError, match="allow-write"):
            call(
                ServerConfig(root=root),
                "attach",
                pdf="floorplan/sheet.pdf",
                labels_json="floorplan/labels.json",
                out="floorplan/again.pdf",
            )

    def test_infer_is_refused_without_allow_write(self, root: Path) -> None:
        """The same for the other writing tool."""
        with pytest.raises(RuntimeError, match="allow-write"):
            call(ServerConfig(root=root), "infer", pdf="floorplan/sheet.pdf", out="x.pdf")

    def test_a_refused_write_writes_nothing(self, root: Path) -> None:
        """Refusing after writing would be worse than not refusing."""
        with pytest.raises(RuntimeError):
            call(ServerConfig(root=root), "infer", pdf="floorplan/sheet.pdf", out="new.pdf")
        assert not (root / "new.pdf").exists()

    def test_the_config_refuses_directly_too(self, tmp_path: Path) -> None:
        """The refusal lives in the confinement, not only in the tool wrapper."""
        with pytest.raises(WriteNotAllowedError):
            ServerConfig(root=tmp_path).require_write("attach")


class TestWritingWhenAllowed:
    """With --allow-write, both writing tools work on the samples."""

    def test_attach_writes_a_labelled_copy(self, root: Path) -> None:
        """Labels from labels.json onto the unlabelled sheet."""
        config = ServerConfig(root=root, allow_write=True)
        result = call(
            config,
            "attach",
            pdf="floorplan/sheet.pdf",
            labels_json="floorplan/labels.json",
            out="floorplan/attached.pdf",
        )
        assert result["written"] == "floorplan/attached.pdf"
        assert (root / "floorplan" / "attached.pdf").exists()

    def test_infer_writes_a_labelled_copy(self, root: Path) -> None:
        """And reports what it found."""
        config = ServerConfig(root=root, allow_write=True)
        result = call(config, "infer", pdf="positionsplan/sheet.pdf", out="inferred.pdf")
        assert result["pages"] == 1
        assert result["elements"] == 16
        assert (root / "inferred.pdf").exists()


class TestConfinement:
    """Every path is resolved and checked against the root, symlinks included."""

    @pytest.mark.parametrize("escape", ["../../etc/passwd", "/etc/passwd", "floorplan/../../.."])
    def test_a_path_out_of_the_root_is_refused(self, root: Path, escape: str) -> None:
        """Relative, absolute and dressed-up escapes alike."""
        with pytest.raises(PathOutsideRootError):
            ServerConfig(root=root).resolve(escape)

    def test_a_tool_refuses_an_escaping_path(self, root: Path) -> None:
        """Through the MCP interface, not only through the config."""
        with pytest.raises(RuntimeError, match="outside the directory"):
            call(ServerConfig(root=root), "get_label", pdf="../../etc/passwd", page=0)

    def test_a_symlink_out_of_the_root_is_refused(self, root: Path, tmp_path: Path) -> None:
        """The check is on the resolved path, so a link cannot smuggle a read out."""
        outside = tmp_path / "secret.pdf"
        outside.write_bytes(b"%PDF-1.4 not yours")
        link = root / "innocent.pdf"
        link.symlink_to(outside)
        with pytest.raises(PathOutsideRootError):
            ServerConfig(root=root).resolve("innocent.pdf")

    def test_a_path_inside_the_root_is_allowed(self, root: Path) -> None:
        """The control case: confinement must not refuse what it should allow."""
        resolved = ServerConfig(root=root).resolve("floorplan/sheet.pdf")
        assert resolved == (root / "floorplan" / "sheet.pdf").resolve()

    def test_a_listing_never_reveals_the_path_above_the_root(self, root: Path) -> None:
        """Documents are reported relative to the root, which is all a host needs."""
        result = call(ServerConfig(root=root), "list_sheets", path=".")
        for sheet in result["sheets"]:
            assert not sheet["document"].startswith("/")
            assert str(root) not in sheet["document"]
