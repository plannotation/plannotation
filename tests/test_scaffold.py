# SPDX-License-Identifier: Apache-2.0
"""Phase 0 gate tests: the scaffold is importable, versioned and self-consistent."""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

import plannotation
from plannotation.cli import app
from plannotation.constants import (
    BASE_URL,
    INDEX_SCHEMA_ID,
    SCHEMA_ID,
    SCHEMA_VERSION,
    SPEC_URI,
    page_label_filename,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Every subpackage and module that must import cleanly from an empty scaffold.
CORE_MODULES = [
    "plannotation",
    "plannotation.cli",
    "plannotation.constants",
    "plannotation.model",
    "plannotation.units",
    "plannotation.schema",
    "plannotation.pdf",
    "plannotation.pdf.embed",
    "plannotation.pdf.extract",
    "plannotation.pdf.render",
    "plannotation.svg",
    "plannotation.svg.carrier",
    "plannotation.validate",
    "plannotation.export",
    "plannotation.export.ifc_svg_pdf",
    "plannotation.infer",
    "plannotation_mcp",
    "plannotation_mcp.server",
]


@pytest.mark.parametrize("module_name", CORE_MODULES)
def test_module_imports(module_name: str) -> None:
    """Every scaffolded module imports without side effects."""
    assert importlib.import_module(module_name) is not None


def test_version_is_pep440() -> None:
    """The package version is a PEP 440 string."""
    assert re.fullmatch(r"\d+\.\d+\.\d+(\.(dev|a|b|rc)\d+)?", plannotation.__version__)


def test_cli_version_flag() -> None:
    """``plannotation --version`` exits zero and reports the version. Phase 0 gate."""
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert plannotation.__version__ in result.output
    assert SCHEMA_VERSION in result.output


def test_cli_short_version_flag() -> None:
    """``-V`` behaves identically to ``--version``."""
    assert CliRunner().invoke(app, ["-V"]).exit_code == 0


def test_cli_no_args_shows_help() -> None:
    """Invoked bare, the CLI shows help rather than doing nothing."""
    result = CliRunner().invoke(app, [])
    assert "plannotation" in result.output.lower()


class TestConstants:
    """The public URL surface is derived from a single constant."""

    @pytest.mark.parametrize("url", [SCHEMA_ID, INDEX_SCHEMA_ID, SPEC_URI])
    def test_public_urls_derive_from_base_url(self, url: str) -> None:
        """No public URL may hard-code a host of its own."""
        assert url.startswith(BASE_URL)

    def test_base_url_has_no_trailing_slash(self) -> None:
        """Derived URLs concatenate a leading slash, so the base must not end in one."""
        assert not BASE_URL.endswith("/")

    def test_spec_uri_matches_schema_version(self) -> None:
        """The declaration URI and the schema version move together."""
        assert SPEC_URI.endswith(f"/{SCHEMA_VERSION}")

    @pytest.mark.parametrize(
        ("page_index", "expected"),
        [
            (0, "plannotation-p0000.json"),
            (7, "plannotation-p0007.json"),
            (1234, "plannotation-p1234.json"),
        ],
    )
    def test_page_label_filename(self, page_index: int, expected: str) -> None:
        """Page attachment names are zero-padded so listings sort in page order."""
        assert page_label_filename(page_index) == expected

    def test_page_label_filename_rejects_negative(self) -> None:
        """A negative page index is a programming error, not a silent oddity."""
        with pytest.raises(ValueError, match="non-negative"):
            page_label_filename(-1)


def _tracked_python_files() -> list[Path]:
    """Return every Python source file that ships in the repository."""
    return sorted(
        path
        for pattern in ("plannotation", "plannotation_mcp", "tests", "tools")
        for path in (REPO_ROOT / pattern).rglob("*.py")
        if ".venv" not in path.parts
    )


def test_every_source_file_has_an_spdx_header() -> None:
    """Licence policy: an SPDX one-liner on every source file, checked mechanically."""
    expected = "# SPDX-License-Identifier: Apache-2.0"
    missing = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in _tracked_python_files()
        if path.read_text(encoding="utf-8").split("\n", 1)[0].strip() != expected
    ]
    assert not missing, f"missing SPDX header: {missing}"


def test_scaffold_covers_the_architecture() -> None:
    """Every directory named in the architecture section exists."""
    required = [
        "plannotation/schema",
        "plannotation/pdf",
        "plannotation/svg",
        "plannotation/validate",
        "plannotation/export",
        "plannotation/infer",
        "plannotation_mcp/plannotation_mcp",
        "tools",
        "inspector",
        "bench",
        "samples",
        "spec",
        "docs",
        "tests",
    ]
    missing = [name for name in required if not (REPO_ROOT / name).is_dir()]
    assert not missing, f"missing directories: {missing}"
