# SPDX-License-Identifier: Apache-2.0
"""Pydantic v2 models mirroring the Plannotation JSON Schemas.

The two JSON Schemas shipped in :mod:`plannotation.schema` are the single source of
truth for the Plannotation format. The models here follow them and never the other way
round: if this module and a schema disagree, the schema is right and this module is
a bug.

What lives here
---------------
* One model per object in ``plannotation-0.1.json`` (the plannotation) and in
  ``plannotation-index-0.1.json`` (the document-level index), plus the enumerations and
  constrained scalars the schemas define under ``$defs``.
* :class:`Sidecar`, the root of ``plannotation-sidecar-0.1.json``: an index and every
  plannotation of one document in one file, which is what the sidecar carrier writes
  beside a document that cannot or must not carry them itself.
* The rules of SPEC 4.1, 4.5 and 4.6 as pure functions with thin model
  properties over them: :func:`aggregate_provenance`, :func:`conformance_level`,
  :func:`annotation_has_link`, :func:`local_ids`.
* Canonical serialisation, :func:`canonical_json`, and its inverses
  :func:`load_plannotation`, :func:`load_plannotation_index` and :func:`load_sidecar`.
* Access to the packaged schemas through :mod:`importlib.resources`, so that
  :func:`page_schema`, :func:`index_schema` and :func:`sidecar_schema` work from an
  installed wheel.

Canonical JSON
--------------
A canonical document has sorted keys, a two-space indent, LF line endings, UTF-8
text with non-ASCII characters left literal, no trailing whitespace, exactly one
trailing newline, and every number rounded to at most
:data:`plannotation.units.COORD_DECIMALS` decimal places. Integral numbers are written
without a fractional part, so a page width of 841 mm is ``841`` and never ``841.0``.
Rounding is Python's round-half-to-even on the binary value, so ``0.8125`` is
written ``0.812``; the tie-break matters less than that it is the same everywhere.
Round-tripping a canonical document through :func:`load_plannotation` and
:func:`canonical_json` reproduces it byte for byte. The reverse trip is lossy by
design: a model holding more than three decimals loses them on the way out.

Absent is not null
------------------
Every optional property defaults to ``None`` and is omitted from the output. The
models never fill in a schema ``default`` (JSON Schema defaults annotate, they do
not modify an instance), so a page that omits ``rotation`` still omits it after a
round trip. Use :attr:`Page.effective_rotation` when the applied default is wanted.

For the same reason ``plannotation`` is required rather than defaulted to its one legal
value: the schema lists it in ``required``, and a model that supplied it silently
would load any JSON object at all as a plannotation. Writers pass
:data:`plannotation.constants.SCHEMA_VERSION`, whose ``Final`` literal type mypy checks
against the ``Literal["0.1"]`` on the field, so the two cannot drift apart unnoticed.

Naming
------
JSON property names are camelCase; Python field names are snake_case and carry an
explicit alias. Four fields are renamed rather than merely re-cased, because the
JSON name is a Python builtin or collides with pydantic itself:

============================  =====================  ==========================
JSON                          Python                 reason
============================  =====================  ==========================
``sheet.id`` / item ``id``    ``sheet_id``           ``id`` is a builtin
``annotation.type``           ``annotation_type``    ``type`` is a builtin
``annotation.shows.property`` ``property_name``      ``property`` is a builtin
``model.schema``              ``ifc_schema``         shadows ``BaseModel.schema``
``<root>.model``              ``source_model``       pydantic's ``model_`` space
============================  =====================  ==========================

``schema`` is the load-bearing one: pydantic 2.13 emits ``UserWarning: Field name
"schema" ... shadows an attribute in parent "BaseModel"``, and this project runs
pytest with ``filterwarnings = ["error"]``, so the warning would fail the build.
``model`` does not currently warn on pydantic 2.13 (``protected_namespaces`` no
longer covers the bare name), but it is renamed anyway rather than left to depend on
a pydantic implementation detail. Both are reached from JSON by their alias and from
Python by either name, because ``populate_by_name`` is enabled.
"""

from __future__ import annotations

import json
from enum import StrEnum
from functools import cache
from importlib import resources
from math import isfinite
from typing import TYPE_CHECKING, Annotated, Final, Literal, NoReturn, Self, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

from plannotation.constants import BASE_URL, SCHEMA_VERSION
from plannotation.units import COORD_DECIMALS

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = [
    "SIDECAR_SCHEMA_ID",
    "Affine6",
    "Annotation",
    "AnnotationType",
    "BBox",
    "Confidence",
    "ConformanceLevel",
    "Discipline",
    "DrawingType",
    "Element",
    "Extensions",
    "Generator",
    "IfcClass",
    "IfcGuid",
    "IndexPage",
    "LengthUnit",
    "LocalId",
    "MeasureUnit",
    "Measures",
    "Model",
    "Number",
    "Page",
    "PageIndex",
    "Plane",
    "Plannotation",
    "PlannotationIndex",
    "Point",
    "Polyline",
    "PositiveNumber",
    "Project",
    "Provenance",
    "PsetProperties",
    "Representation",
    "Rotation",
    "Sha256",
    "Sheet",
    "Shows",
    "Sidecar",
    "Storey",
    "Target",
    "Vec3",
    "Viewport",
    "ViewportKind",
    "aggregate_provenance",
    "annotation_has_link",
    "canonical_bytes",
    "canonical_json",
    "conformance_level",
    "index_schema",
    "load_plannotation",
    "load_plannotation_index",
    "load_sidecar",
    "local_ids",
    "page_schema",
    "sidecar_schema",
]

#: Import path of the package that carries the schema files as package data.
_SCHEMA_PACKAGE = "plannotation.schema"

#: Filename of the plannotation schema inside that package.
_PAGE_SCHEMA_FILE = f"plannotation-{SCHEMA_VERSION}.json"

#: Filename of the document-level index schema inside that package.
_INDEX_SCHEMA_FILE = f"plannotation-index-{SCHEMA_VERSION}.json"

#: Filename of the sidecar schema inside that package.
_SIDECAR_SCHEMA_FILE = f"plannotation-sidecar-{SCHEMA_VERSION}.json"

#: ``$id`` of the sidecar JSON Schema.
#:
#: It lives here rather than beside its two siblings in :mod:`plannotation.constants`
#: because the sidecar is a carrier detail introduced with the carriers themselves,
#: and because nothing outside this module needs it to build a URL. Like every other
#: public Plannotation URL it is derived from :data:`plannotation.constants.BASE_URL`, so
#: the rule that no module may hard-code the host still holds.
SIDECAR_SCHEMA_ID: Final = f"{BASE_URL}/schema/{SCHEMA_VERSION}/plannotation-sidecar.schema.json"


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------
class Provenance(StrEnum):
    """Where a plannotation, an element or an annotation came from.

    Mirrors ``#/$defs/provenance``. ``authored`` means written from the model by the
    authoring tool; ``inferred`` means reconstructed from the drawing by a reader,
    which is why an inferred item must also carry a confidence; ``mixed`` is only
    meaningful at document level, where it means the document contains both.
    """

    AUTHORED = "authored"
    INFERRED = "inferred"
    MIXED = "mixed"


class ConformanceLevel(StrEnum):
    """How much of the format a plannotation actually fills in.

    ``L1`` is page and sheet (plus viewports, when there are any); ``L2`` adds
    elements; ``L3`` adds annotations that link to something. The index schema stores
    this value per page, and the validator reports it.
    """

    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


Discipline = Literal["architecture", "structure", "mep", "civil", "landscape", "other"]
"""Value set of ``sheet.discipline``."""

DrawingType = Literal[
    "plan",
    "section",
    "elevation",
    "detail",
    "schedule",
    "positionsplan",
    "schalplan",
    "bewehrungsplan",
    "lageplan",
    "other",
]
"""Value set of ``sheet.drawingType``. The German terms are drawing kinds with no
English equivalent in common use and are kept as they are spoken on site."""

LengthUnit = Literal["m", "mm", "cm"]
"""Value set of ``model.lengthUnit``: the unit the model's plane coordinates use."""

ViewportKind = Literal[
    "plan",
    "section",
    "elevation",
    "detail",
    "3d",
    "schedule",
    "legend",
    "titleblock",
]
"""Value set of ``viewport.kind``."""

Representation = Literal["cut", "projection", "hidden", "symbol"]
"""Value set of ``element.representation``: how the element is drawn on the page."""

AnnotationType = Literal[
    "dimension",
    "tag",
    "text",
    "callout",
    "sectionMark",
    "grid",
    "level",
    "revisionCloud",
    "keynote",
    "symbol",
    "leader",
    "hatch",
    "northArrow",
    "scaleBar",
]
"""Value set of ``annotation.type``."""

MeasureUnit = Literal["mm", "cm", "m", "deg", "pct"]
"""Value set of ``annotation.unit``: the unit of ``annotation.value``."""


# ---------------------------------------------------------------------------
# Constrained scalars and fixed-shape arrays
# ---------------------------------------------------------------------------
def _json_number(value: object) -> object:
    """Reject values that JSON Schema would not accept as a number.

    Python makes ``bool`` a subclass of ``int`` and pydantic's lax mode parses
    numeric strings, so without this both ``true`` and ``"3"`` would be accepted for
    a schema ``number`` or ``integer``. JSON Schema accepts neither.

    Args:
        value: The raw value handed to a numeric field.

    Returns:
        The value unchanged, when it is not a boolean and not a string.

    Raises:
        ValueError: If the value is a boolean or a string. Pydantic only converts
            ``ValueError`` and ``AssertionError`` into validation errors, so this
            cannot be the ``TypeError`` that the failing check would otherwise
            suggest.
    """
    if not isinstance(value, bool | str):
        return value
    msg = f"expected a JSON number, got {type(value).__name__}"
    raise ValueError(msg)


_NUMERIC = BeforeValidator(_json_number)

Number = Annotated[float, _NUMERIC, Field(allow_inf_nan=False)]
"""A schema ``number``. JSON has no NaN and no Infinity, so neither is accepted."""

PositiveNumber = Annotated[float, _NUMERIC, Field(gt=0, allow_inf_nan=False)]
"""A schema ``number`` with ``exclusiveMinimum: 0`` -- a width, a height, a scale."""

PageIndex = Annotated[int, _NUMERIC, Field(ge=0)]
"""A zero-based PDF page index: a schema ``integer`` with ``minimum: 0``."""

Confidence = Annotated[float, _NUMERIC, Field(ge=0, le=1, allow_inf_nan=False)]
"""Mirrors ``#/$defs/confidence``: how much an inferred item is to be trusted."""

LocalId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.:-]+$")]
"""Mirrors ``#/$defs/localId``: an identifier unique within one page."""

IfcGuid = Annotated[str, StringConstraints(pattern=r"^[0-9A-Za-z_$]{22}$")]
"""Mirrors ``#/$defs/ifcGuid``: an IFC GlobalId in its 22-character base64 form."""

IfcClass = Annotated[str, StringConstraints(pattern=r"^Ifc[A-Za-z0-9]+$")]
"""An IFC entity name, such as ``IfcWall``. The vocabulary is IFC's, never a
vendor's."""

Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
"""A lowercase hexadecimal SHA-256 digest of the source model file."""

Point = tuple[Number, Number]
"""Mirrors ``#/$defs/point``: ``[x, y]`` in paper millimetres."""

Vec3 = tuple[Number, Number, Number]
"""Mirrors ``#/$defs/vec3``: a direction or a position in model space."""

BBox = tuple[Number, Number, Number, Number]
"""Mirrors ``#/$defs/bbox``: ``[x0, y0, x1, y1]`` in paper millimetres, origin
bottom-left, y up. The schema does not require ``x0 <= x1``, and neither does this
model; the geometric validator is where that is checked."""

Affine6 = tuple[Number, Number, Number, Number, Number, Number]
"""``viewport.paperToPlane``: the affine ``[a, b, c, d, e, f]`` described in
:mod:`plannotation.units`, mapping paper millimetres to the viewport's model plane."""

Polyline = Annotated[list[Point], Field(min_length=2)]
"""Mirrors ``#/$defs/polyline``: two or more points in paper millimetres."""

Measures = Annotated[list[LocalId], Field(min_length=1)]
"""``annotation.measures``: the element or grid ids a dimension runs between."""

ExtensionKey = Annotated[str, StringConstraints(pattern=r"^x-")]
"""A key of an ``extensions`` object, which the schema constrains with
``propertyNames``."""

Extensions = dict[ExtensionKey, JsonValue]
"""Namespaced extras. Readers may ignore them; they are never required."""

PsetProperties = dict[str, JsonValue]
"""``element.properties``: the schema says only ``"type": "object"``, and this
matches it exactly.

The schema's description is ``PsetName -> {property: value}``, and that is the
shape a writer should emit -- but it is a description, not a constraint. Typing it
as ``dict[str, dict[str, JsonValue]]`` would make the reference implementation
reject property bags that a conforming third-party writer may legitimately produce
(a flat bag, or a pset carried with no value), and for a format whose whole purpose
is that any program can read the plannotation, a reader stricter than the format
is the worse failure. Use :meth:`Element.pset` for typed access to the nested shape."""

Rotation = Annotated[Literal[0, 90, 180, 270], _NUMERIC]
"""Value set of ``page.rotation``, in degrees. ``_NUMERIC`` is what stops ``false``
being accepted as ``0``, since ``False == 0`` in Python."""


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class _PlannotationModel(BaseModel):
    """Configuration shared by every Plannotation model.

    Both schemas set ``"additionalProperties": false`` on every object they define
    -- root, generator, page, sheet, project, model, viewport, plane, storey,
    element, annotation, shows, target and the index's page entry -- so every model
    forbids extras. The two objects that do *not* forbid extras, ``extensions`` and
    ``element.properties``, are plain mappings rather than models, which is exactly
    how the schema treats them.

    ``validate_assignment`` keeps a model that was valid at construction valid
    afterwards. The one sharp edge is that flipping an item to
    :attr:`Provenance.INFERRED` before giving it a confidence raises; set both in one
    call, or build a new item with :meth:`pydantic.BaseModel.model_copy`.
    """

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        validate_assignment=True,
    )


class Generator(_PlannotationModel):
    """The tool that wrote a plannotation. Mirrors the ``generator`` object of both schemas.

    ``created`` is a date-time string. It stays a string rather than becoming a
    :class:`datetime.datetime`, because JSON Schema's ``format`` is an annotation
    that validators do not assert by default, and because a string survives a
    byte-for-byte round trip whatever offset or sub-second precision it was written
    with.
    """

    name: str
    version: str
    created: str | None = None


class Page(_PlannotationModel):
    """The PDF page a plannotation describes. Mirrors the root ``page`` object.

    The page is the leading document: these three required numbers say which page and
    how big it is, and everything else in the plannotation is positioned against them.
    """

    index: PageIndex
    width_mm: PositiveNumber = Field(alias="widthMm")
    height_mm: PositiveNumber = Field(alias="heightMm")
    rotation: Rotation | None = None

    @property
    def effective_rotation(self) -> int:
        """Return the rotation in degrees, applying the schema default of 0.

        Returns:
            ``rotation`` when it is present, and 0 when it is absent.
        """
        return 0 if self.rotation is None else self.rotation


class Project(_PlannotationModel):
    """The project a sheet belongs to. Mirrors ``sheet.project``."""

    name: str | None = None
    number: str | None = None


class Sheet(_PlannotationModel):
    """The sheet printed on the page. Mirrors the root ``sheet`` object.

    ``date`` is a date string for the same reason :attr:`Generator.created` is.
    """

    sheet_id: str = Field(alias="id")
    title: str | None = None
    revision: str | None = None
    date: str | None = None
    document_id: str | None = Field(default=None, alias="documentId")
    discipline: Discipline | None = None
    drawing_type: DrawingType | None = Field(default=None, alias="drawingType")
    scale: PositiveNumber | None = None
    project: Project | None = None
    author: str | None = None
    checker: str | None = None
    status: str | None = None
    title_block_bbox: BBox | None = Field(default=None, alias="titleBlockBBox")


class Model(_PlannotationModel):
    """The source model the plannotation was written from. Mirrors the ``model`` object.

    The same shape appears in the plannotation schema and in the index schema, which is
    why one class serves both.
    """

    file: str | None = None
    sha256: Sha256 | None = None
    ifc_schema: str | None = Field(default=None, alias="schema")
    length_unit: LengthUnit | None = Field(default=None, alias="lengthUnit")


class Plane(_PlannotationModel):
    """The model-space plane of a view. Mirrors ``viewport.plane``.

    The view direction is ``xAxis`` cross ``yAxis``.
    """

    origin: Vec3
    x_axis: Vec3 = Field(alias="xAxis")
    y_axis: Vec3 = Field(alias="yAxis")


class Storey(_PlannotationModel):
    """The building storey a viewport shows. Mirrors ``viewport.storey``."""

    ifc_guid: IfcGuid | None = Field(default=None, alias="ifcGuid")
    name: str | None = None
    elevation: Number | None = None


class Viewport(_PlannotationModel):
    """One view placed on the sheet. Mirrors ``#/$defs/viewport``.

    A viewport is what ties paper to model: ``paperBBox`` says where it sits on the
    page, and ``plane`` with ``paperToPlane`` says what paper coordinates inside it
    mean in the model.
    """

    local_id: LocalId = Field(alias="id")
    name: str | None = None
    kind: ViewportKind
    scale: PositiveNumber | None = None
    paper_bbox: BBox = Field(alias="paperBBox")
    plane: Plane | None = None
    paper_to_plane: Affine6 | None = Field(default=None, alias="paperToPlane")
    cut_height: Number | None = Field(default=None, alias="cutHeight")
    storey: Storey | None = None


class _ProvenancedItem(_PlannotationModel):
    """The five properties an element and an annotation define identically.

    Both carry ``id``, ``viewport``, ``paperBBox``, ``provenance`` and ``confidence``
    with the same schemas, and both are subject to the same provenance rule, so the
    rule is written once here.
    """

    local_id: LocalId = Field(alias="id")
    viewport: LocalId | None = None
    paper_bbox: BBox = Field(alias="paperBBox")
    provenance: Provenance | None = None
    confidence: Confidence | None = None

    @model_validator(mode="after")
    def _confidence_required_when_inferred(self) -> Self:
        """Enforce the rule of SPEC 4.6.5 that an inferred item carries a confidence.

        Returns:
            The validated item.

        Raises:
            ValueError: If ``provenance`` is ``inferred`` and ``confidence`` is
                absent. The schema cannot express this dependency, so the model is
                deliberately stricter here.
        """
        if self.provenance is Provenance.INFERRED and self.confidence is None:
            msg = f"{self.local_id!r}: provenance 'inferred' requires a confidence"
            raise ValueError(msg)
        return self


class Element(_ProvenancedItem):
    """A building element shown on the page. Mirrors ``#/$defs/element``.

    ``ifcClass`` and ``paperBBox`` are required, which is what makes the presence of
    a single element enough to reach :attr:`ConformanceLevel.L2`.
    """

    ifc_guid: IfcGuid | None = Field(default=None, alias="ifcGuid")
    ifc_class: IfcClass = Field(alias="ifcClass")
    predefined_type: str | None = Field(default=None, alias="predefinedType")
    name: str | None = None
    tag: str | None = None
    paper_outlines: list[Polyline] | None = Field(default=None, alias="paperOutlines")
    representation: Representation | None = None
    properties: PsetProperties | None = None

    def pset(self, name: str) -> Mapping[str, JsonValue] | None:
        """Return one IFC property set from :attr:`properties`, if it is one.

        ``properties`` is typed exactly as the schema constrains it -- a bare object
        -- so that no conforming plannotation is unreadable. This accessor gives back
        the nested ``PsetName -> {property: value}`` shape the schema *describes*, and
        returns None rather than raising when a writer emitted something else.

        Args:
            name: Property-set name, such as ``Pset_WallCommon``.

        Returns:
            The property set as a mapping, or None when it is absent or is not an
            object.

        Examples:
            >>> element = Element(
            ...     id="w1",
            ...     ifcClass="IfcWall",
            ...     paperBBox=[0, 0, 10, 10],
            ...     properties={"Pset_WallCommon": {"FireRating": "REI90"}},
            ... )
            >>> element.pset("Pset_WallCommon")["FireRating"]
            'REI90'
            >>> element.pset("Pset_Missing") is None
            True
        """
        if self.properties is None:
            return None
        value = self.properties.get(name)
        return value if isinstance(value, dict) else None


class Shows(_PlannotationModel):
    """What a tag displays. Mirrors ``annotation.shows``.

    ``property`` is renamed to :attr:`property_name` because ``property`` is a
    builtin; the JSON name is unchanged.
    """

    element: LocalId | None = None
    property_name: str | None = Field(default=None, alias="property")


class Target(_PlannotationModel):
    """Where a callout or a section mark points. Mirrors ``annotation.target``.

    This is the only cross-page reference in the format: ``localId`` values are
    unique within one page and mean nothing outside it, so a reference to another
    sheet or page travels through here and nowhere else.
    """

    sheet_id: str | None = Field(default=None, alias="sheetId")
    viewport_id: LocalId | None = Field(default=None, alias="viewportId")
    detail: str | None = None
    pdf_page: PageIndex | None = Field(default=None, alias="pdfPage")


class Annotation(_ProvenancedItem):
    """A dimension, tag, grid, level, callout or other mark on the page.

    Mirrors ``#/$defs/annotation``. An annotation that links to something -- through
    ``measures``, ``shows``, ``target``, ``axis`` or ``ifcGuid`` -- is what lifts a
    plannotation to :attr:`ConformanceLevel.L3`; see :func:`annotation_has_link`.
    """

    annotation_type: AnnotationType = Field(alias="type")
    text: str | None = None
    value: Number | None = None
    unit: MeasureUnit | None = None
    measures: Measures | None = None
    geometry: Polyline | None = None
    shows: Shows | None = None
    target: Target | None = None
    axis: str | None = None
    elevation: Number | None = None
    ifc_guid: IfcGuid | None = Field(default=None, alias="ifcGuid")


class Plannotation(_PlannotationModel):
    """The plannotation of one drawing page: the root of ``plannotation-0.1.json``.

    The PDF page stays the leading document; this object is auxiliary and may be
    removed without changing what the page shows or prints.
    """

    plannotation: Literal["0.1"]
    generator: Generator | None = None
    provenance: Provenance
    page: Page
    sheet: Sheet
    source_model: Model | None = Field(default=None, alias="model")
    viewports: list[Viewport] | None = None
    elements: list[Element] | None = None
    annotations: list[Annotation] | None = None
    extensions: Extensions | None = None

    @model_validator(mode="after")
    def _unique_local_ids(self) -> Self:
        """Enforce that every ``localId`` on the page is unique.

        Returns:
            The validated plannotation.

        Raises:
            ValueError: If a ``localId`` is used more than once, counting viewports,
                elements and annotations together.
        """
        seen: set[str] = set()
        duplicates: set[str] = set()
        for identifier in local_ids(self):
            if identifier in seen:
                duplicates.add(identifier)
            seen.add(identifier)
        if duplicates:
            listed = ", ".join(sorted(duplicates))
            msg = f"localId must be unique within a page; repeated: {listed}"
            raise ValueError(msg)
        return self

    @property
    def aggregate_provenance(self) -> Provenance | None:
        """Return the provenance the top level of this plannotation ought to declare.

        Returns:
            The aggregate over every element and annotation, as
            :func:`aggregate_provenance` computes it, or None when the page has
            neither and the rule therefore decides nothing. Use
            :attr:`provenance_is_consistent` for the check itself.
        """
        items = [item.provenance for item in (self.elements or [])]
        items += [item.provenance for item in (self.annotations or [])]
        return aggregate_provenance(items, declared=self.provenance)

    @property
    def provenance_is_consistent(self) -> bool:
        """Report whether the declared provenance matches the page's own items.

        This is the provenance rule as the validator applies it. It is a
        report rather than a validation error, because a plannotation that misreports
        itself is still a well-formed plannotation: the schema does not constrain the
        aggregate, and refusing to load such a document would leave no way to inspect it.

        Returns:
            True when the aggregate agrees with :attr:`provenance`, and True as well
            when there are no items to aggregate, since then nothing contradicts the
            declaration.
        """
        aggregate = self.aggregate_provenance
        return aggregate is None or aggregate is self.provenance

    @property
    def level(self) -> ConformanceLevel:
        """Return the conformance level this plannotation reaches.

        Returns:
            The level, as :func:`conformance_level` computes it.
        """
        return conformance_level(self)


class IndexPage(_PlannotationModel):
    """One plannotated page as the document index lists it.

    Mirrors ``pages.items`` of ``plannotation-index-0.1.json``. Unplannotated pages are
    absent from the index rather than listed as empty.
    """

    page_index: PageIndex = Field(alias="pageIndex")
    sheet_id: str = Field(alias="sheetId")
    title: str | None = None
    revision: str | None = None
    level: ConformanceLevel
    file: str | None = None


class PlannotationIndex(_PlannotationModel):
    """The document-level index: the root of ``plannotation-index-0.1.json``.

    It exists so that a reader can see what is plannotated, and at which level, without
    opening every page attachment.
    """

    plannotation: Literal["0.1"]
    generator: Generator | None = None
    provenance: Provenance | None = None
    source_model: Model | None = Field(default=None, alias="model")
    pages: list[IndexPage]
    extensions: Extensions | None = None


class Sidecar(_PlannotationModel):
    """A whole document's plannotations in one file: the root of ``plannotation-sidecar-0.1.json``.

    The sidecar is the third carrier. It holds exactly what a plannotated PDF holds --
    the document-level index and one plannotation per plannotated page -- as a single
    JSON file written beside the document as ``X.plannotation.json``. It exists for two
    consumers: one that cannot read PDF attachments, and one that must not rewrite the
    document at all, a signed PDF above all.

    Carrying the index as well as the pages is what makes the sidecar a twin rather
    than a bag of plannotations: a reader can see which pages are plannotated and at
    what level without parsing every page, exactly as it could from the embedded index.

    ``index`` is required because a sidecar is written by a program that has all the
    plannotations in front of it, and can therefore always derive an index from them. A
    sidecar whose index is missing would be a weaker document than the PDF it stands
    in for.
    """

    plannotation: Literal["0.1"]
    generator: Generator | None = None
    index: PlannotationIndex
    pages: list[Plannotation]
    extensions: Extensions | None = None

    @model_validator(mode="after")
    def _one_plannotation_per_page(self) -> Self:
        """Enforce that no two plannotations claim the same page.

        Returns:
            The validated sidecar.

        Raises:
            ValueError: If two plannotations carry the same ``page.index``. The schema
                cannot express this, and a reader keying plannotations by page -- which is
                the only useful way to read them -- would silently lose one of the two.
        """
        seen: set[int] = set()
        duplicates: set[int] = set()
        for plannotation in self.pages:
            if plannotation.page.index in seen:
                duplicates.add(plannotation.page.index)
            seen.add(plannotation.page.index)
        if duplicates:
            listed = ", ".join(str(index) for index in sorted(duplicates))
            msg = f"two plannotations claim the same page; repeated page.index: {listed}"
            raise ValueError(msg)
        return self

    @property
    def levels(self) -> dict[int, ConformanceLevel]:
        """Return the conformance level of each plannotation, keyed by page index.

        Returns:
            One entry per plannotation in :attr:`pages`, as :func:`conformance_level`
            grades it. The index's own ``level`` values are a claim inside a document;
            these are computed from the plannotations themselves.
        """
        return {
            plannotation.page.index: conformance_level(plannotation) for plannotation in self.pages
        }


# ---------------------------------------------------------------------------
# Rules of SPEC 4.1, 4.5 and 4.6, as pure functions
# ---------------------------------------------------------------------------
def _inherited_provenance(declared: Provenance | None) -> Provenance:
    """Return the provenance an item inherits when it declares none.

    Args:
        declared: The plannotation's top-level provenance, or None if unknown.

    Returns:
        The declared value, ``inferred`` when the plannotation declares ``mixed``
        (reading silence conservatively rather than as ``authored``), and ``authored``
        when there is no declaration to inherit.
    """
    if declared is None:
        return Provenance.AUTHORED
    if declared is Provenance.MIXED:
        return Provenance.INFERRED
    return declared


def aggregate_provenance(
    items: Iterable[Provenance | None],
    *,
    declared: Provenance | None = None,
) -> Provenance | None:
    """Aggregate the provenance of a page's elements and annotations.

    SPEC 4.6.4 gives three rules: the top level is ``authored`` only if every
    element and annotation is authored, ``inferred`` only if every one is inferred,
    and ``mixed`` otherwise. Read literally they leave the boundary cases open, so
    those are resolved as follows.

    *No elements and no annotations at all.* Nothing is aggregated and the answer is
    None: this rule does not determine such a document's provenance, and the
    plannotation's own declaration stands unchallenged. Reading it as ``inferred``
    would force an L1 sheet written straight from the model -- a title block and a
    few empty viewports -- to describe itself as reconstructed, which is false;
    reading it as ``authored`` would let a sheet recovered from a legacy PDF claim it
    came from a model, which is equally false. With no items there is no evidence
    either way, and a format whose principle is that the page leads and the
    plannotation is auxiliary should not invent one. Either of the other readings is
    one line away, in this function, if the specification later decides otherwise.

    *Items that omit ``provenance``.* The property is optional on an item and
    required at the top level, so an item that does not say **inherits the
    plannotation's own declaration**. That makes the top-level value load-bearing
    rather than decorative, and it is the only reading under which a wholly
    reconstructed plannotation need not repeat ``"provenance": "inferred"`` on every
    one of its items. Where the top level is ``mixed`` the inherited value is
    ``inferred``, because nothing in the format could say which half an unmarked item
    came from and reading silence as ``authored`` would overstate what the writer
    knew. With no declaration to inherit, silence means ``authored``.

    *Elements authored, annotations inferred.* Both kinds are items and are counted
    together, so this is ``mixed`` -- as is any other combination.

    *An item that declares itself ``mixed``.* Such an item contains authored content,
    so it cannot make a document purely inferred: "every one is inferred" does not
    hold, and the answer is ``mixed``.

    Args:
        items: The ``provenance`` of every element and annotation on the page, in any
            order, with ``None`` for an item that omits it.
        declared: The plannotation's own top-level provenance, which items that omit
            theirs inherit. None when there is no declaration to inherit from.

    Returns:
        ``authored`` when there is at least one item and every item is authored,
        ``inferred`` when there is at least one item and every item is inferred,
        ``mixed`` for any other combination, and None when there are no items at all.
    """
    inherited = _inherited_provenance(declared)
    states = {item if item is not None else inherited for item in items}
    if not states:
        return None
    if states == {Provenance.INFERRED}:
        return Provenance.INFERRED
    if states == {Provenance.AUTHORED}:
        return Provenance.AUTHORED
    return Provenance.MIXED


def annotation_has_link(annotation: Annotation) -> bool:
    """Report whether an annotation links to anything.

    The L3 test of SPEC 4.1 is an annotation with at least one link: ``measures``,
    ``shows``, ``target``, ``axis`` or ``ifcGuid``. A link must be substantive -- an
    empty ``shows`` object or an empty ``axis`` string is schema-valid and links
    nothing, so neither counts.

    Args:
        annotation: The annotation to test.

    Returns:
        True if the annotation carries at least one substantive link.
    """
    return bool(
        annotation.measures
        or annotation.axis
        or annotation.ifc_guid
        or _carries_a_value(annotation.shows)
        or _carries_a_value(annotation.target)
    )


def local_ids(plannotation: Plannotation) -> list[str]:
    """Collect every ``localId`` declared on a page, in document order.

    Viewports, elements and annotations share one identifier space. They must,
    because ``annotation.measures`` holds "element or grid ids" -- that is, element
    ids and the ids of grid *annotations* -- so a reference cannot be resolved by
    looking in one collection. Per-collection uniqueness would make a reference to a
    name used by both an element and an annotation ambiguous; page-wide uniqueness
    makes every reference resolvable by a single lookup. That is what
    :class:`Plannotation` enforces.

    Args:
        plannotation: The plannotation to read.

    Returns:
        The identifiers, with any duplicates still present, viewports first, then
        elements, then annotations.
    """
    collected = [viewport.local_id for viewport in (plannotation.viewports or [])]
    collected += [element.local_id for element in (plannotation.elements or [])]
    collected += [annotation.local_id for annotation in (plannotation.annotations or [])]
    return collected


def conformance_level(plannotation: Plannotation) -> ConformanceLevel:
    """Return the conformance level a plannotation reaches.

    ``L1`` is page and sheet, plus viewports when there are any. Both are required by
    the schema, so every valid plannotation reaches L1 and L1 is the floor rather
    than a test. ``L2`` adds elements with ``ifcClass`` and ``paperBBox``; both are
    required on an element, so one element is enough. ``L3`` adds annotations with at
    least one link, and one linked annotation is enough -- a sheet is not demoted
    because its north arrow links to nothing.

    Args:
        plannotation: The plannotation to grade.

    Returns:
        The highest level the plannotation satisfies.
    """
    if not plannotation.elements:
        return ConformanceLevel.L1
    if any(annotation_has_link(item) for item in (plannotation.annotations or [])):
        return ConformanceLevel.L3
    return ConformanceLevel.L2


def _carries_a_value(model: BaseModel | None) -> bool:
    """Report whether an optional nested object exists and says anything.

    Args:
        model: The nested object, or None when the property is absent.

    Returns:
        True if the object is present and at least one of its properties is set.
    """
    return model is not None and bool(model.model_dump(exclude_none=True))


# ---------------------------------------------------------------------------
# Canonical serialisation
# ---------------------------------------------------------------------------
def _canonical_number(value: float) -> float | int:
    """Round one number for canonical output.

    Args:
        value: The number to round.

    Returns:
        The value rounded to :data:`plannotation.units.COORD_DECIMALS` decimal places,
        as an :class:`int` when the result is integral. Rounding is
        :func:`round`, that is round-half-to-even on the binary value, which is
        deterministic on every platform. That is what keeps a page
        width of 841 mm out of the file as ``841.0``, and it also folds ``-0.0`` into
        ``0``. A JSON number carries no type distinction, so nothing is lost; readers
        of a schema ``number`` accept both spellings, and the shorter one is what a
        person writing the file by hand would type.

    Raises:
        ValueError: If the value is NaN or an infinity, neither of which JSON can
            represent.
    """
    if not isfinite(value):
        msg = f"{value} cannot be written to JSON"
        raise ValueError(msg)
    rounded = round(value, COORD_DECIMALS)
    return int(rounded) if rounded.is_integer() else rounded


def _canonical_value(value: object) -> object:
    """Walk a dumped document, rounding every number it contains.

    Args:
        value: A value from :meth:`pydantic.BaseModel.model_dump` in JSON mode.

    Returns:
        The same structure with every float rounded by :func:`_canonical_number`.
        Booleans are checked first, since ``bool`` is a subclass of ``int``.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return _canonical_number(value)
    if isinstance(value, dict):
        return {key: _canonical_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    return value


def canonical_json(model: BaseModel) -> str:
    """Serialise a model to Plannotation's canonical JSON text.

    The canonical form is what every Plannotation writer emits and what golden files
    contain: keys sorted, two-space indent, LF line endings, UTF-8 with non-ASCII
    left literal, no trailing whitespace, exactly one trailing newline, absent
    properties still absent, and every number rounded to at most
    :data:`plannotation.units.COORD_DECIMALS` decimal places with integral values
    written without a fractional part.

    Args:
        model: Any Plannotation model, normally a :class:`Plannotation` or a
            :class:`PlannotationIndex`.

    Returns:
        The canonical text, ending in exactly one newline.

    Raises:
        ValueError: If the document contains a number JSON cannot represent.
    """
    payload = _canonical_value(model.model_dump(mode="json", by_alias=True, exclude_none=True))
    text = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return f"{text}\n"


def canonical_bytes(model: BaseModel) -> bytes:
    """Serialise a model to canonical JSON encoded as UTF-8.

    This is the form that goes into a PDF as an embedded file and onto disk as a
    sidecar, so that the checksum of a plannotation does not depend on who wrote it.

    Args:
        model: Any Plannotation model.

    Returns:
        :func:`canonical_json` encoded as UTF-8, with no byte-order mark.
    """
    return canonical_json(model).encode("utf-8")


def _reject_json_constant(name: str) -> NoReturn:
    """Refuse the non-standard constants Python's JSON parser would otherwise allow.

    Args:
        name: The constant found in the text: ``NaN``, ``Infinity`` or ``-Infinity``.

    Raises:
        ValueError: Always. None of the three is JSON, and none can appear in a
            document that a schema ``number`` would accept.
    """
    msg = f"{name} is not valid JSON"
    raise ValueError(msg)


def _loads(text: str | bytes) -> object:
    """Parse JSON text strictly.

    Args:
        text: The document, as text or as UTF-8 bytes.

    Returns:
        The parsed value.

    Raises:
        ValueError: If the text is not valid JSON, or contains NaN or an infinity.
    """
    return json.loads(text, parse_constant=_reject_json_constant)


def load_plannotation(text: str | bytes) -> Plannotation:
    """Parse and validate the plannotation of one page.

    The inverse of :func:`canonical_json` for a plannotation: for canonical text,
    ``canonical_json(load_plannotation(text)) == text``, byte for byte.

    Args:
        text: The plannotation document, as text or as UTF-8 bytes.

    Returns:
        The validated plannotation.

    Raises:
        ValueError: If the text is not valid JSON.
        pydantic.ValidationError: If the document does not match the schema.
    """
    return Plannotation.model_validate(_loads(text))


def load_plannotation_index(text: str | bytes) -> PlannotationIndex:
    """Parse and validate a document-level index.

    The inverse of :func:`canonical_json` for an index, on the same terms as
    :func:`load_plannotation`.

    Args:
        text: The index document, as text or as UTF-8 bytes.

    Returns:
        The validated index.

    Raises:
        ValueError: If the text is not valid JSON.
        pydantic.ValidationError: If the document does not match the schema.
    """
    return PlannotationIndex.model_validate(_loads(text))


def load_sidecar(text: str | bytes) -> Sidecar:
    """Parse and validate a sidecar document.

    The inverse of :func:`canonical_json` for a sidecar, on the same terms as
    :func:`load_plannotation`.

    Args:
        text: The sidecar document, as text or as UTF-8 bytes.

    Returns:
        The validated sidecar.

    Raises:
        ValueError: If the text is not valid JSON.
        pydantic.ValidationError: If the document does not match the schema.
    """
    return Sidecar.model_validate(_loads(text))


# ---------------------------------------------------------------------------
# The packaged schemas
# ---------------------------------------------------------------------------
@cache
def _schema_text(filename: str) -> str:
    """Read one packaged schema file.

    :mod:`importlib.resources` is used rather than a path relative to this file, so
    that the schemas are found in an installed wheel, in a zip import and in a
    checkout alike. The text is cached; each caller parses it afresh and therefore
    owns the object it gets back.

    Args:
        filename: The file's name inside the :mod:`plannotation.schema` package.

    Returns:
        The file's contents, decoded as UTF-8.
    """
    return resources.files(_SCHEMA_PACKAGE).joinpath(filename).read_text(encoding="utf-8")


def page_schema() -> dict[str, JsonValue]:
    """Load the plannotation JSON Schema.

    Returns:
        ``plannotation-0.1.json`` parsed, as a fresh object the caller may keep or
        mutate. It is the single source of truth for what a plannotation may contain.
    """
    return cast("dict[str, JsonValue]", json.loads(_schema_text(_PAGE_SCHEMA_FILE)))


def index_schema() -> dict[str, JsonValue]:
    """Load the document-level index JSON Schema.

    Returns:
        ``plannotation-index-0.1.json`` parsed, as a fresh object the caller may keep or
        mutate.
    """
    return cast("dict[str, JsonValue]", json.loads(_schema_text(_INDEX_SCHEMA_FILE)))


def sidecar_schema() -> dict[str, JsonValue]:
    """Load the sidecar JSON Schema.

    The sidecar schema inlines the plannotation and index schemas rather than
    referencing them, so that a reader can validate a sidecar with this one document
    and no network access. The three are generated from one source and cannot drift
    apart unnoticed: the inlined copies are the other two files verbatim.

    Returns:
        ``plannotation-sidecar-0.1.json`` parsed, as a fresh object the caller may keep
        or mutate.
    """
    return cast("dict[str, JsonValue]", json.loads(_schema_text(_SIDECAR_SCHEMA_FILE)))
