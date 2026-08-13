"""Convert the feature catalogue JSON structure into a ShapeChange SCXML model.

SCXML is ShapeChange's own model interchange format (``inputModelType=SCXML``).
It is the only input format that is pure Java, platform independent and current
with all ShapeChange features -- ``EA7`` needs Windows plus a proprietary jar,
and the ``XMI10`` reader only accepts XMI *1.0* with a DOCTYPE, so the XMI 1.1
exports from ``sosi.geonorge.no`` cannot be fed to ShapeChange directly.

The structure is defined by ``ShapeChangeExportedModel.xsd``. Three constraints
from that schema drive the implementation:

* every ``sc:id`` in the document must be unique -- packages, classes,
  properties and associations share a single id space;
* ``Property/typeId`` must reference an existing ``Class/id``, so basic ISO
  19103 types are written with ``typeName`` only;
* ``Property/sequenceNumber`` is required and must be unique per class.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from md.feature_types import collect_codelists
from puml.feature_types import build_geometry_attribute, collect_datatypes, map_type

__all__ = ["build_scxml", "write_scxml"]

SCXML_NS = "http://shapechange.net/model"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
SCXML_SCHEMA_LOCATION = (
    "http://shapechange.net/model "
    "http://shapechange.net/resources/schema/ShapeChangeExportedModel.xsd"
)

PRODUCER = "ps.editor.actions"

# Stereotypes must use ShapeChange's normalised (lower case) well-known form.
_ST_APPLICATION_SCHEMA = "application schema"
_ST_FEATURE_TYPE = "featuretype"
_ST_DATA_TYPE = "datatype"
_ST_CODE_LIST = "codelist"
_ST_ENUMERATION = "enumeration"

# Multiplicity strings accepted by the SCXML schema:
#   [1-9]\d* | \* | ((0|[1-9]\d*)\.\.([1-9]\d*|\*))
# Note that a bare "0" is not valid.
_DEFAULT_ROLE_CARDINALITY = "0..*"


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _sub(parent: ET.Element, tag: str, text: str | None = None) -> ET.Element:
    element = ET.SubElement(parent, f"{{{SCXML_NS}}}{tag}")
    if text is not None:
        element.text = text
    return element


def _normalize_cardinality(raw: Any) -> str | None:
    """Return a multiplicity string accepted by the SCXML schema, or ``None``.

    ``None`` means "omit the element", which makes ShapeChange fall back to the
    schema default of ``1``.
    """
    text = _text(raw)
    if not text:
        return None

    text = text.replace(" ", "")
    if text in {"*", "0..*", "1..*"}:
        return text
    if text in {"0", "0..0"}:
        # Not expressible; treat as optional single value.
        return "0..1"

    if ".." in text:
        lower, _, upper = text.partition("..")
    else:
        lower, upper = text, text

    if upper in {"*", "n", "-1", "unbounded"}:
        upper = "*"

    if not lower.isdigit():
        return None
    if upper != "*" and not upper.isdigit():
        return None

    if upper != "*" and int(upper) < 1:
        return "0..1"
    if lower == upper:
        return lower if lower != "0" else "0..1"
    return f"{lower}..{upper}"


def _add_documentation(parent: ET.Element, documentation: str) -> None:
    documentation = _text(documentation)
    if not documentation:
        return
    descriptors = _sub(parent, "descriptors")
    descriptor = _sub(descriptors, "documentation")
    values = _sub(descriptor, "descriptorValues")
    _sub(values, "DescriptorValue", documentation)


def _add_stereotype(parent: ET.Element, stereotype: str) -> None:
    stereotypes = _sub(parent, "stereotypes")
    _sub(stereotypes, "Stereotype", stereotype)


def _add_tagged_values(parent: ET.Element, tags: Sequence[tuple[str, str]]) -> None:
    entries = [(name, _text(value)) for name, value in tags if _text(value)]
    if not entries:
        return
    container = _sub(parent, "taggedValues")
    for name, value in entries:
        tagged_value = _sub(container, "TaggedValue")
        _sub(tagged_value, "name", name)
        values = _sub(tagged_value, "values")
        _sub(values, "Value", value)


# --------------------------------------------------------------------------- #
# Model assembly
# --------------------------------------------------------------------------- #


class _ClassSpec:
    """A class that will be written to the SCXML document."""

    __slots__ = ("name", "stereotype", "documentation", "attributes", "package",
                 "abstract", "supertypes", "tags", "codes", "id")

    def __init__(
        self,
        name: str,
        stereotype: str,
        *,
        documentation: str = "",
        attributes: Sequence[Mapping[str, Any]] | None = None,
        package: str = "",
        abstract: bool = False,
        supertypes: Sequence[str] | None = None,
        tags: Sequence[tuple[str, str]] | None = None,
        codes: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        self.name = name
        self.stereotype = stereotype
        self.documentation = documentation
        self.attributes = list(attributes or [])
        self.package = package
        self.abstract = abstract
        self.supertypes = list(supertypes or [])
        self.tags = list(tags or [])
        self.codes = list(codes or [])
        self.id = ""


def _collect_class_specs(
    feature_types: Sequence[Mapping[str, Any]],
) -> list[_ClassSpec]:
    """Derive every class that ShapeChange needs from the feature catalogue.

    Precedence when names collide: feature type > data type > code list. A
    feature type carries the most structure, so it wins.
    """
    specs: list[_ClassSpec] = []
    taken: set[str] = set()

    for feature_type in feature_types:
        if not isinstance(feature_type, Mapping):
            continue
        name = _text(feature_type.get("name"))
        if not name or name in taken:
            continue
        taken.add(name)

        relationships = feature_type.get("relationships")
        inheritance: list[str] = []
        if isinstance(relationships, Mapping):
            raw_inheritance = relationships.get("inheritance")
            if isinstance(raw_inheritance, Sequence) and not isinstance(
                raw_inheritance, (str, bytes)
            ):
                inheritance = [_text(entry) for entry in raw_inheritance if _text(entry)]

        attributes: list[Mapping[str, Any]] = []
        geometry_attribute = build_geometry_attribute(feature_type.get("geometry"))
        if geometry_attribute:
            # OGC API reports "GM_Surface" and GeoPackage "geometry-polygon";
            # both normalise through map_type. Anything unrecognised would fall
            # through to a scalar type, so pin it to the generic GM_Object.
            if not map_type(_text(geometry_attribute.get("type"))).startswith("GM_"):
                geometry_attribute = dict(geometry_attribute)
                geometry_attribute["type"] = "GM_Object"
            attributes.append(geometry_attribute)
        raw_attributes = feature_type.get("attributes")
        if isinstance(raw_attributes, Sequence) and not isinstance(
            raw_attributes, (str, bytes)
        ):
            attributes.extend(
                attribute for attribute in raw_attributes if isinstance(attribute, Mapping)
            )

        specs.append(
            _ClassSpec(
                name,
                _ST_FEATURE_TYPE,
                documentation=_text(feature_type.get("description")),
                attributes=attributes,
                package=_text(feature_type.get("package")),
                abstract=bool(feature_type.get("abstract")),
                supertypes=inheritance,
            )
        )

    for type_name, nested in collect_datatypes(feature_types).items():
        name = _text(type_name)
        if not name or name in taken:
            continue
        taken.add(name)
        specs.append(_ClassSpec(name, _ST_DATA_TYPE, attributes=nested))

    for entry in collect_codelists(feature_types):
        name = _text(entry.get("name"))
        if not name or name in taken:
            continue
        taken.add(name)

        code_list_uri = _text(entry.get("codeList"))
        listed_values = entry.get("listedValues")
        codes = [value for value in listed_values or [] if isinstance(value, Mapping)]

        tags: list[tuple[str, str]] = []
        if code_list_uri:
            tags.append(("codeList", code_list_uri))
            as_dictionary = _text(entry.get("asDictionary"))
            if as_dictionary:
                tags.append(("asDictionary", as_dictionary))

        specs.append(
            _ClassSpec(
                name,
                _ST_CODE_LIST if code_list_uri else _ST_ENUMERATION,
                documentation=_text(entry.get("definition")),
                tags=tags,
                codes=codes,
            )
        )

    return specs


def _assign_ids(specs: Sequence[_ClassSpec]) -> dict[str, str]:
    """Give every class a stable id and return a name -> id lookup."""
    by_name: dict[str, str] = {}
    for index, spec in enumerate(specs, start=1):
        spec.id = f"C{index}"
        by_name[spec.name] = spec.id
    return by_name


# --------------------------------------------------------------------------- #
# XML writing
# --------------------------------------------------------------------------- #


def _write_property(
    parent: ET.Element,
    attribute: Mapping[str, Any],
    *,
    property_id: str,
    sequence_number: int,
    class_ids: Mapping[str, str],
) -> None:
    name = _text(attribute.get("name"))
    element = _sub(parent, "Property")
    if name:
        _sub(element, "name", name)
    _sub(element, "id", property_id)
    _add_documentation(element, _text(attribute.get("description")))

    cardinality = _normalize_cardinality(attribute.get("cardinality"))
    if cardinality:
        _sub(element, "cardinality", cardinality)

    _sub(element, "sequenceNumber", str(sequence_number))

    type_name = map_type(_text(attribute.get("type")) or "unknown")
    if type_name:
        type_id = class_ids.get(type_name)
        if type_id:
            # typeId must reference a Class/id, so it is only written for types
            # that are actually part of the generated model. ISO 19103/19107
            # types are resolved by ShapeChange's standard map entries instead.
            _sub(element, "typeId", type_id)
        _sub(element, "typeName", type_name)


def _write_code(parent: ET.Element, code: Mapping[str, Any], *, property_id: str,
                sequence_number: int) -> None:
    value = _text(code.get("value"))
    label = _text(code.get("label"))
    element = _sub(parent, "Property")
    if value:
        _sub(element, "name", value)
    _sub(element, "id", property_id)
    if label and label != value:
        _add_documentation(element, label)
    _sub(element, "sequenceNumber", str(sequence_number))


def _write_class(
    parent: ET.Element,
    spec: _ClassSpec,
    *,
    class_ids: Mapping[str, str],
    association_roles: Sequence[tuple[str, Mapping[str, Any]]],
) -> None:
    element = _sub(parent, "Class")
    _sub(element, "name", spec.name)
    _sub(element, "id", spec.id)
    _add_stereotype(element, spec.stereotype)
    _add_documentation(element, spec.documentation)
    _add_tagged_values(element, spec.tags)

    if spec.abstract:
        _sub(element, "isAbstract", "true")

    supertype_ids = [
        class_ids[name] for name in spec.supertypes if name in class_ids
    ]
    if supertype_ids:
        supertypes = _sub(element, "supertypes")
        for supertype_id in supertype_ids:
            _sub(supertypes, "SupertypeId", supertype_id)

    has_members = bool(spec.attributes or spec.codes or association_roles)
    if not has_members:
        return

    properties = _sub(element, "properties")
    sequence_number = 0

    for attribute in spec.attributes:
        sequence_number += 1
        _write_property(
            properties,
            attribute,
            property_id=f"{spec.id}_{sequence_number}",
            sequence_number=sequence_number,
            class_ids=class_ids,
        )

    for code in spec.codes:
        sequence_number += 1
        _write_code(
            properties,
            code,
            property_id=f"{spec.id}_{sequence_number}",
            sequence_number=sequence_number,
        )

    for role_id, association in association_roles:
        sequence_number += 1
        target = _text(association.get("target"))
        role_name = _text(association.get("role")) or f"rolle{sequence_number}"
        role = _sub(properties, "Property")
        _sub(role, "name", role_name)
        _sub(role, "id", role_id)
        cardinality = _normalize_cardinality(association.get("cardinality"))
        if cardinality:
            _sub(role, "cardinality", cardinality)
        _sub(role, "isNavigable", "true")
        _sub(role, "sequenceNumber", str(sequence_number))
        _sub(role, "typeId", class_ids[target])
        _sub(role, "typeName", target)
        _sub(role, "isAttribute", "false")
        _sub(role, "associationId", association["_association_id"])


def _collect_associations(
    feature_types: Sequence[Mapping[str, Any]],
    class_ids: Mapping[str, str],
) -> tuple[dict[str, list[tuple[str, dict[str, Any]]]], list[dict[str, Any]]]:
    """Return per-class navigable roles plus the association definitions.

    Only associations whose target resolves to a generated class are kept --
    ``Property/typeId`` must reference an existing ``Class/id``.
    """
    roles_by_class: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    associations: list[dict[str, Any]] = []

    for feature_type in feature_types:
        if not isinstance(feature_type, Mapping):
            continue
        source = _text(feature_type.get("name"))
        if source not in class_ids:
            continue
        relationships = feature_type.get("relationships")
        if not isinstance(relationships, Mapping):
            continue
        raw = relationships.get("associations")
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            continue

        for entry in raw:
            if not isinstance(entry, Mapping):
                continue
            target = _text(entry.get("target"))
            if not target or target not in class_ids:
                continue

            association_id = f"as{len(associations) + 1}"
            association = dict(entry)
            association["target"] = target
            association["_association_id"] = association_id

            role_id = f"S{association_id}"
            roles_by_class.setdefault(source, []).append((role_id, association))
            associations.append(
                {
                    "id": association_id,
                    "source": source,
                    "target": target,
                    "navigable_role_id": role_id,
                }
            )

    return roles_by_class, associations


def _write_associations(
    parent: ET.Element,
    associations: Sequence[Mapping[str, Any]],
    class_ids: Mapping[str, str],
) -> None:
    if not associations:
        return
    container = _sub(parent, "associations")
    for association in associations:
        source = str(association["source"])
        target = str(association["target"])
        association_id = str(association["id"])

        element = _sub(container, "Association")
        _sub(element, "name", f"{source}_{target}")
        _sub(element, "id", association_id)

        # The navigable end lives in the source class and is referenced by id.
        end1 = _sub(element, "end1")
        end1.set("ref", str(association["navigable_role_id"]))

        # The opposite end is not navigable, so it is encoded inline. It must
        # carry inClassId, and must not be navigable (schema assertions).
        end2 = _sub(element, "end2")
        role = _sub(end2, "Property")
        _sub(role, "id", f"T{association_id}")
        _sub(role, "cardinality", _DEFAULT_ROLE_CARDINALITY)
        _sub(role, "isNavigable", "false")
        _sub(role, "sequenceNumber", "1")
        _sub(role, "typeId", class_ids[source])
        _sub(role, "typeName", source)
        _sub(role, "isAttribute", "false")
        _sub(role, "inClassId", class_ids[target])
        _sub(role, "associationId", association_id)


def build_scxml(
    feature_types: Sequence[Mapping[str, Any]],
    *,
    schema_name: str,
    target_namespace: str,
    xmlns_prefix: str = "app",
    schema_version: str = "1.0",
    xsd_document: str | None = None,
) -> ET.ElementTree:
    """Build a ShapeChange SCXML model from feature catalogue entries.

    The application schema package carries ``targetNamespace``, ``xmlns``,
    ``version`` and ``xsdDocument`` as tagged values. That is what makes
    ShapeChange treat the package as a schema -- ``PackageInfoImpl.isSchema()``
    checks the tagged value, not the ``<<ApplicationSchema>>`` stereotype.
    """
    if not isinstance(feature_types, Sequence) or isinstance(feature_types, (str, bytes)):
        raise TypeError("feature_types must be a sequence of mappings")

    schema_name = _text(schema_name) or "Applikasjonsskjema"
    target_namespace = _text(target_namespace)
    if not target_namespace:
        raise ValueError("target_namespace is required to build a ShapeChange model.")

    entries = [entry for entry in feature_types if isinstance(entry, Mapping)]
    specs = _collect_class_specs(entries)
    class_ids = _assign_ids(specs)
    roles_by_class, associations = _collect_associations(entries, class_ids)

    ET.register_namespace("sc", SCXML_NS)
    ET.register_namespace("xsi", XSI_NS)

    root = ET.Element(f"{{{SCXML_NS}}}Model")
    root.set("encoding", "UTF-8")
    root.set("scxmlProducer", PRODUCER)
    root.set("scxmlProducerVersion", "1.0")
    root.set(f"{{{XSI_NS}}}schemaLocation", SCXML_SCHEMA_LOCATION)

    packages = _sub(root, "packages")
    schema_package = _sub(packages, "Package")
    _sub(schema_package, "name", schema_name)
    _sub(schema_package, "id", "P1")
    _add_stereotype(schema_package, _ST_APPLICATION_SCHEMA)
    file_stem = _safe_file_stem(schema_name)
    _add_tagged_values(
        schema_package,
        [
            ("targetNamespace", target_namespace),
            ("xmlns", _text(xmlns_prefix) or "app"),
            ("version", _text(schema_version) or "1.0"),
            ("xsdDocument", _text(xsd_document) or f"{file_stem}.xsd"),
            # Without jsonDocument the JSON Schema target invents a file name
            # from the package name and logs a warning. Naming it here keeps the
            # two outputs consistent and the log clean.
            ("jsonDocument", f"{file_stem}.json"),
        ],
    )

    # Sub-packages only exist when the source model carried package names (the
    # XMI loader is the only one that does). They inherit the target namespace
    # from the schema package, so they are not selected as separate schemas.
    grouped: dict[str, list[_ClassSpec]] = {}
    for spec in specs:
        grouped.setdefault(spec.package if spec.stereotype == _ST_FEATURE_TYPE else "", []).append(spec)

    root_specs = grouped.pop("", [])
    if root_specs:
        classes = _sub(schema_package, "classes")
        for spec in root_specs:
            _write_class(
                classes,
                spec,
                class_ids=class_ids,
                association_roles=roles_by_class.get(spec.name, []),
            )

    if grouped:
        child_packages = _sub(schema_package, "packages")
        for index, (package_name, package_specs) in enumerate(
            sorted(grouped.items()), start=2
        ):
            child = _sub(child_packages, "Package")
            _sub(child, "name", package_name)
            _sub(child, "id", f"P{index}")
            classes = _sub(child, "classes")
            for spec in package_specs:
                _write_class(
                    classes,
                    spec,
                    class_ids=class_ids,
                    association_roles=roles_by_class.get(spec.name, []),
                )

    _write_associations(root, associations, class_ids)

    tree = ET.ElementTree(root)
    ET.indent(tree, space=" ")
    return tree


def _safe_file_stem(name: str) -> str:
    return "".join(char for char in name if char.isalnum()) or "Applikasjonsskjema"


def write_scxml(
    feature_types: Sequence[Mapping[str, Any]],
    output_path: str | Path,
    *,
    schema_name: str,
    target_namespace: str,
    xmlns_prefix: str = "app",
    schema_version: str = "1.0",
    xsd_document: str | None = None,
) -> Path:
    """Write the SCXML model to ``output_path`` and return the path."""
    tree = build_scxml(
        feature_types,
        schema_name=schema_name,
        target_namespace=target_namespace,
        xmlns_prefix=xmlns_prefix,
        schema_version=schema_version,
        xsd_document=xsd_document,
    )
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(path, encoding="UTF-8", xml_declaration=True)
    return path
