# SPDX-License-Identifier: Apache-2.0
"""The Bonsai add-on, driven outside Blender through stand-ins for ``bpy`` and Bonsai.

Blender cannot run in CI, so ``docs/bonsai.md`` is the add-on's real test. What can
be checked here is everything that is not Blender: that Blender can read ``bl_info``
without executing the file, that the file avoids the one import that breaks operator
properties, that the operators register and unregister cleanly, that the guesses at a
sheet's files are right, and -- with a stand-in for Bonsai that serves a real sample
model -- that the operator labels a sample sheet end to end.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
import time
import types
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import pytest

from plannotation.validate import validate

if TYPE_CHECKING:
    from collections.abc import Iterator

ADDON = Path(__file__).parent.parent / "bonsai_ext" / "plannotation_bonsai.py"
SAMPLES = Path(__file__).parent.parent / "samples"


class FakeOperator:
    """What the add-on needs of ``bpy.types.Operator``."""

    def __init__(self) -> None:
        """Start with no reports."""
        self.reports: list[tuple[set[str], str]] = []

    def report(self, kind: set[str], message: str) -> None:
        """Record a report.

        Args:
            kind: Its level.
            message: Its text.
        """
        self.reports.append((kind, message))


class FakeMenu:
    """What the add-on needs of a Blender menu type."""

    entries: ClassVar[list[Any]] = []

    @classmethod
    def append(cls, entry: Any) -> None:  # noqa: ANN401
        """Add a menu entry.

        Args:
            entry: The draw function.
        """
        cls.entries.append(entry)

    @classmethod
    def remove(cls, entry: Any) -> None:  # noqa: ANN401
        """Remove a menu entry.

        Args:
            entry: The draw function.
        """
        cls.entries.remove(entry)


@pytest.fixture
def addon(monkeypatch: pytest.MonkeyPatch) -> Iterator[types.ModuleType]:
    """Import the add-on with a stand-in ``bpy`` and no Bonsai.

    Args:
        monkeypatch: pytest's monkeypatch.

    Yields:
        The add-on module.
    """
    registered: list[type] = []
    bpy = types.ModuleType("bpy")
    bpy.types = types.SimpleNamespace(  # type: ignore[attr-defined]
        Operator=FakeOperator, TOPBAR_MT_file_export=FakeMenu
    )
    bpy.props = types.SimpleNamespace(  # type: ignore[attr-defined]
        StringProperty=lambda **options: ("StringProperty", options)
    )
    bpy.utils = types.SimpleNamespace(  # type: ignore[attr-defined]
        register_class=registered.append, unregister_class=registered.remove
    )
    bpy.path = types.SimpleNamespace(abspath=lambda path: path)  # type: ignore[attr-defined]
    bpy.ops = types.SimpleNamespace()  # type: ignore[attr-defined]
    bpy.registered = registered  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    monkeypatch.setitem(sys.modules, "bonsai", None)
    spec = importlib.util.spec_from_file_location("plannotation_bonsai", ADDON)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    FakeMenu.entries.clear()


def fake_bonsai(monkeypatch: pytest.MonkeyPatch, model: Any, path: Path | None) -> None:  # noqa: ANN401
    """Stand in for ``bonsai.tool`` with a model and its path.

    Args:
        monkeypatch: pytest's monkeypatch.
        model: What ``tool.Ifc.get()`` returns.
        path: What ``tool.Ifc.get_path()`` returns.
    """
    tool = types.ModuleType("bonsai.tool")
    tool.Ifc = types.SimpleNamespace(  # type: ignore[attr-defined]
        get=lambda: model, get_path=lambda: None if path is None else str(path)
    )
    package = types.ModuleType("bonsai")
    package.tool = tool  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "bonsai", package)
    monkeypatch.setitem(sys.modules, "bonsai.tool", tool)


class TestWhatBlenderReads:
    """Blender parses the file before it runs it."""

    def test_bl_info_is_a_literal(self) -> None:
        """Blender reads it with ``ast.literal_eval``, never by importing."""
        tree = ast.parse(ADDON.read_text("utf-8"))
        assignment = next(
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "bl_info" for t in node.targets)
        )
        info = ast.literal_eval(assignment.value)
        assert info["name"] == "Plannotation for Bonsai"
        assert info["blender"] >= (4, 2, 0)
        assert info["category"] == "Import-Export"

    def test_annotations_are_not_postponed(self) -> None:
        """Postponed annotations would leave Blender strings instead of properties."""
        tree = ast.parse(ADDON.read_text("utf-8"))
        futures = [
            alias.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "__future__"
            for alias in node.names
        ]
        assert "annotations" not in futures


class TestRegistration:
    """The operators and the menu entry come and go together."""

    def test_register_and_unregister(self, addon: types.ModuleType) -> None:
        """Nothing is left behind."""
        bpy = sys.modules["bpy"]
        addon.register()
        assert [cls.bl_idname for cls in bpy.registered] == [  # type: ignore[attr-defined]
            "plannotation.attach_to_sheet",
            "plannotation.create_sheets_and_attach",
        ]
        assert FakeMenu.entries == [addon.menu_entry]
        addon.unregister()
        assert bpy.registered == []  # type: ignore[attr-defined]
        assert FakeMenu.entries == []

    def test_properties_are_declared_as_annotations(self, addon: types.ModuleType) -> None:
        """Evaluated, so Blender sees property definitions."""
        annotations = addon.PLANNOTATION_OT_attach_to_sheet.__annotations__
        assert set(annotations) == {"svg_path", "pdf_path", "out_path", "sheet_id", "title"}
        assert all(value[0] == "StringProperty" for value in annotations.values())


class TestGuesses:
    """Where the active sheet's files are, and whose sheet it is."""

    def test_the_newest_unlabelled_files_are_taken(
        self, addon: types.ModuleType, tmp_path: Path
    ) -> None:
        """A labelled copy from an earlier run is never mistaken for the sheet."""
        sheets = tmp_path / "sheets"
        sheets.mkdir()
        old = sheets / "A-101 - Plan.pdf"
        new = sheets / "A-101 - Plan v2.pdf"
        for path in (
            old,
            sheets / "A-101 - Plan.svg",
            new,
            sheets / "A-101 - Plan.plannotated.pdf",
        ):
            path.write_bytes(b"x")
        stamp = time.time()
        os.utime(old, (stamp - 100, stamp - 100))
        os.utime(new, (stamp, stamp))
        os.utime(sheets / "A-101 - Plan.plannotated.pdf", (stamp + 100, stamp + 100))
        svg, pdf = addon.sheet_files(tmp_path / "model.ifc", "A-101")
        assert svg == sheets / "A-101 - Plan.svg"
        assert pdf == new

    def test_no_model_or_number_guesses_nothing(self, addon: types.ModuleType) -> None:
        """Blank fields for the user, rather than a wrong file."""
        assert addon.sheet_files(None, "A-101") == (None, None)
        assert addon.sheet_files(Path("model.ifc"), "") == (None, None)

    def test_the_labelled_copy_sits_beside_the_pdf(self, addon: types.ModuleType) -> None:
        """Named so it is never taken for the sheet next time."""
        assert addon.plannotated_path(Path("s/A-101.pdf")) == Path("s/A-101.plannotated.pdf")

    def test_without_bonsai_there_is_no_model(self, addon: types.ModuleType) -> None:
        """And no exception either."""
        assert addon.bonsai_model() is None
        assert addon.bonsai_model_path() is None

    def test_the_active_sheet_is_read_from_bonsai_s_properties(
        self, addon: types.ModuleType
    ) -> None:
        """Only an IfcDocumentInformation counts as a sheet."""

        class Entity:
            def __init__(self, kind: str) -> None:
                self.kind = kind

            def is_a(self, kind: str) -> bool:
                return self.kind == kind

        class Model:
            def by_id(self, definition: int) -> Entity:
                if definition == 99:
                    raise RuntimeError(definition)
                return Entity("IfcDocumentInformation" if definition == 1 else "IfcWall")

        def context(definition: int, index: int = 0) -> Any:  # noqa: ANN401
            item = types.SimpleNamespace(ifc_definition_id=definition)
            props = types.SimpleNamespace(sheets=[item], active_sheet_index=index)
            return types.SimpleNamespace(scene=types.SimpleNamespace(DocProperties=props))

        assert addon.active_sheet(context(1), Model()).is_a("IfcDocumentInformation")
        assert addon.active_sheet(context(2), Model()) is None
        assert addon.active_sheet(context(99), Model()) is None
        assert addon.active_sheet(context(1, index=5), Model()) is None
        assert addon.active_sheet(context(1), None) is None


class TestExecute:
    """The operator's own checks, and a whole run against a sample."""

    @staticmethod
    def _operator(addon: types.ModuleType, **values: str) -> Any:  # noqa: ANN401
        """Make an operator with its properties set.

        Args:
            addon: The add-on module.
            **values: Property values.

        Returns:
            The operator.
        """
        operator = addon.PLANNOTATION_OT_attach_to_sheet()
        for name in ("svg_path", "pdf_path", "out_path", "sheet_id", "title"):
            setattr(operator, name, values.get(name, ""))
        return operator

    def test_no_model_is_reported(self, addon: types.ModuleType) -> None:
        """In Blender's status bar, not as a traceback."""
        operator = self._operator(addon)
        assert operator.execute(None) == {"CANCELLED"}
        assert "no IFC model" in operator.reports[0][1]

    def test_missing_fields_are_reported(
        self, addon: types.ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The sheet number and both files are needed."""
        fake_bonsai(monkeypatch, object(), None)
        operator = self._operator(addon, sheet_id="A-101")
        assert operator.execute(None) == {"CANCELLED"}
        assert "all needed" in operator.reports[0][1]

    def test_a_plannotation_error_is_reported(
        self, addon: types.ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A file that is not an SVG ends the run with the reason."""
        fake_bonsai(monkeypatch, object(), None)
        (tmp_path / "s.svg").write_text("<html/>", encoding="utf-8")
        import plannotation.svg.derive as derive_module

        monkeypatch.setattr(
            derive_module,
            "source_from_model",
            lambda model, **kw: derive_module.SheetSource(  # noqa: ARG005
                sheet_id=kw["sheet_id"], unit_scale_to_m=1.0, length_unit="m"
            ),
        )
        operator = self._operator(
            addon,
            sheet_id="A-101",
            svg_path=str(tmp_path / "s.svg"),
            pdf_path=str(tmp_path / "s.pdf"),
        )
        assert operator.execute(None) == {"CANCELLED"}
        assert "not an SVG" in operator.reports[0][1]

    def test_create_sheets_needs_bonsai(self, addon: types.ModuleType) -> None:
        """Without Bonsai's operator there is nothing to run first."""
        operator = addon.PLANNOTATION_OT_create_sheets_and_attach()
        assert operator.execute(None) == {"CANCELLED"}
        assert "not available" in operator.reports[0][1]

    @pytest.mark.skipif(
        not (SAMPLES / "floorplan" / "model.ifc").is_file(),
        reason="samples are not built; run make samples",
    )
    def test_a_sample_sheet_is_labelled_as_bonsai_would_ask(
        self, addon: types.ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Prefilled from the active sheet, labelled, and valid."""
        ifcopenshell = pytest.importorskip("ifcopenshell")
        model = ifcopenshell.open(str(SAMPLES / "floorplan" / "model.ifc"))
        (tmp_path / "sheets").mkdir()
        for suffix in (".svg", ".pdf"):
            source = (SAMPLES / "floorplan" / "sheet").with_suffix(suffix)
            (tmp_path / "sheets" / f"ARC-101 - Grundriss{suffix}").write_bytes(source.read_bytes())
        sheet = model.create_entity(
            "IfcDocumentInformation", Identification="ARC-101", Name="Grundriss Erdgeschoss"
        )
        fake_bonsai(monkeypatch, model, tmp_path / "model.ifc")
        item = types.SimpleNamespace(ifc_definition_id=sheet.id())
        props = types.SimpleNamespace(sheets=[item], active_sheet_index=0)
        context = types.SimpleNamespace(scene=types.SimpleNamespace(DocProperties=props))

        operator = self._operator(addon)
        operator.prefill(context)
        assert operator.sheet_id == "ARC-101"
        assert operator.title == "Grundriss Erdgeschoss"
        assert operator.pdf_path.endswith("ARC-101 - Grundriss.pdf")
        assert operator.execute(None) == {"FINISHED"}, operator.reports
        out = tmp_path / "sheets" / "ARC-101 - Grundriss.plannotated.pdf"
        assert "9 element(s)" in operator.reports[0][1]
        assert validate(out).error_count == 0
