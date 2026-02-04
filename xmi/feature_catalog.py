"""Load feature type metadata from SOSI UML XMI feature catalogues."""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from xml.etree import ElementTree as ET

try:  # pragma: no cover - optional dependency for tests
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore[assignment]

HTTPGet = Callable[..., Any]

_NS = {"UML": "omg.org/UML1.3"}
_GEOMETRY_NAMES = {
    "PUNKT",
    "LINJE",
    "KURVE",
    "FLATE",
    "OMRÅDE",
    "OMRADE",
    "GRENSE",
    "REPRESENTASJONSPUNKT",
    "REPRESENTASJONSLINJE",
    "REPRESENTASJONSFLATE",
}


@dataclass
class _UmlAttribute:
    owner_id: str
    name: str
    type_name: str | None
    description: str
    lower: str | None
    upper: str | None
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class _UmlClass:
    id: str
    name: str
    stereotype: str | None
    tagged_values: dict[str, str]
    abstract: bool = False
    attributes: list[_UmlAttribute] = field(default_factory=list)


def load_feature_types_from_xmi(
    xmi_source: str | Path,
    *,
    username: str = "sosi",
    password: str = "sosi",
    http_get: HTTPGet | None = None,
) -> list[dict[str, Any]]:
    """Return feature type metadata extracted from an Enterprise Architect XMI file.

    Parameters
    ----------
    xmi_source:
        Either a local path to the XMI file or a fully qualified HTTP(S) URL.
    username, password:
        Optional basic-auth credentials used when ``xmi_source`` is fetched
        over HTTP. Defaults to the public ``sosi``/``sosi`` credentials.
    http_get:
        Optional HTTP function compatible with :func:`requests.get`. The callable
        can either accept ``(url, auth=...)`` or only ``(url)``. The return value
        must expose ``status_code``, ``raise_for_status()`` and either ``content``
        or ``text``.
    """

    text = _load_xmi_text(xmi_source, username=username, password=password, http_get=http_get)
    return _parse_feature_types(text)


def _load_xmi_text(
    source: str | Path,
    *,
    username: str,
    password: str,
    http_get: HTTPGet | None,
) -> str:
    path_candidate = Path(str(source))
    if path_candidate.exists():
        return _read_file(path_candidate)

    source_str = str(source)
    if not source_str.lower().startswith(("http://", "https://")):
        raise FileNotFoundError(f"XMI source '{source}' does not exist.")

    getter = http_get or _default_http_get
    auth = (username, password) if username or password else None

    response = _invoke_getter(getter, source_str, auth=auth)

    status_code = getattr(response, "status_code", None)
    if status_code is not None and int(status_code) >= 400:
        raise RuntimeError(f"Request to '{source}' failed with status code {status_code}.")

    if hasattr(response, "raise_for_status"):
        response.raise_for_status()

    content: bytes | str
    if hasattr(response, "content"):
        content = response.content  # type: ignore[assignment]
    elif hasattr(response, "text"):
        content = response.text  # type: ignore[assignment]
    else:  # pragma: no cover - defensive fallback
        content = response.read()

    if isinstance(content, bytes):
        for encoding in ("utf-8", "cp1252", "latin-1"):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        return content.decode("utf-8", errors="replace")

    return content


def _default_http_get(url: str, *, auth: tuple[str, str] | None = None) -> Any:
    if requests is None:  # pragma: no cover - defensive: requests should be installed
        raise RuntimeError("The 'requests' library is required to fetch remote XMI files.")
    return requests.get(url, auth=auth, timeout=60)


def _invoke_getter(getter: HTTPGet, url: str, *, auth: tuple[str, str] | None) -> Any:
    try:
        return getter(url, auth=auth)
    except TypeError:
        return getter(url)


def _read_file(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _parse_feature_types(text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(text)
    classes, order = _collect_classes(root)
    parents = _collect_generalizations(root)
    associations = _collect_associations(root, classes)

    classes_by_name = {info.name: info for info in classes.values()}
    codelists = _build_code_lists(
        {cid: info for cid, info in classes.items() if info.stereotype == "CodeList"}
    )

    feature_types: list[dict[str, Any]] = []
    for class_id in order:
        info = classes[class_id]
        if info.stereotype != "FeatureType":
            continue
        feature_types.append(
            _build_feature_type(
                info,
                classes,
                classes_by_name,
                parents,
                codelists,
                associations,
            )
        )

    return feature_types


def _collect_classes(root: ET.Element) -> tuple[dict[str, _UmlClass], list[str]]:
    classes: dict[str, _UmlClass] = {}
    order: list[str] = []

    for class_elem in root.findall(".//UML:Class", _NS):
        class_id = _get_identifier(class_elem)
        if not class_id:
            continue
        name = class_elem.get("name", "").strip()
        stereotype = _extract_stereotype(class_elem)
        tagged_values = _extract_tagged_values(class_elem)
        attributes = _collect_attributes(class_elem, class_id)
        abstract = class_elem.get("isAbstract", "false").lower() == "true"
        info = _UmlClass(
            id=class_id,
            name=name,
            stereotype=stereotype,
            tagged_values=tagged_values,
            abstract=abstract,
            attributes=attributes,
        )
        classes[class_id] = info
        order.append(class_id)

    return classes, order


def _collect_attributes(class_elem: ET.Element, owner_id: str) -> list[_UmlAttribute]:
    attributes: list[_UmlAttribute] = []
    feature_container = class_elem.find("UML:Classifier.feature", _NS)
    if feature_container is None:
        return attributes

    for attr_elem in feature_container.findall("UML:Attribute", _NS):
        tags = _extract_tagged_values(attr_elem)
        type_name = tags.get("type") or _extract_type_name(attr_elem)
        lower, upper = _extract_bounds(attr_elem, tags)
        description = _clean_text(tags.get("description"))
        attribute = _UmlAttribute(
            owner_id=owner_id,
            name=attr_elem.get("name", "").strip(),
            type_name=type_name,
            description=description,
            lower=lower,
            upper=upper,
            tags=tags,
        )
        attributes.append(attribute)

    return attributes


def _collect_generalizations(root: ET.Element) -> dict[str, list[str]]:
    parents: dict[str, list[str]] = {}
    for generalization in root.findall(".//UML:Generalization", _NS):
        subtype = generalization.get("subtype")
        supertype = generalization.get("supertype")
        if not subtype or not supertype:
            continue
        parents.setdefault(subtype, []).append(supertype)
    return parents


def _collect_associations(root: ET.Element, classes: Mapping[str, _UmlClass]) -> dict[str, list[dict[str, Any]]]:
    associations: dict[str, list[dict[str, Any]]] = {}
    for association in root.findall(".//UML:Association", _NS):
        ends = association.findall("UML:Association.connection/UML:AssociationEnd", _NS)
        if len(ends) < 2:
            continue

        end_infos: list[tuple[ET.Element, str, bool | None]] = []
        explicit_true = False
        for end in ends:
            source_id = end.get("type")
            if not source_id:
                continue
            nav_value = _association_end_is_navigable(end)
            if nav_value is True:
                explicit_true = True
            end_infos.append((end, source_id, nav_value))

        for idx, (end, source_id, _) in enumerate(end_infos):
            for other_idx, (other, target_id, other_nav) in enumerate(end_infos):
                if idx == other_idx:
                    continue
                if explicit_true and other_nav is not True:
                    continue
                target_info = classes.get(target_id)
                if not target_info:
                    continue

                role = other.get("name") or ""
                lower, upper = _extract_association_bounds(other)
                cardinality = _format_cardinality(lower, upper) if (lower or upper) else ""

                entry: dict[str, Any] = {"target": target_info.name}
                if role:
                    entry["role"] = role
                if cardinality:
                    entry["cardinality"] = cardinality
                associations.setdefault(source_id, []).append(entry)

    return associations


def _build_code_lists(classes: Mapping[str, _UmlClass]) -> dict[str, dict[str, Any]]:
    listings: dict[str, dict[str, Any]] = {}
    for class_info in classes.values():
        values: list[dict[str, str]] = []
        for attribute in class_info.attributes:
            value = attribute.tags.get("code") or attribute.name
            label = _clean_text(attribute.tags.get("description")) or value
            values.append(
                {
                    "value": value,
                    "label": label,
                }
            )
        definition = _clean_text(class_info.tagged_values.get("documentation"))
        entry: dict[str, Any] = {}
        if values:
            entry["listedValues"] = values
        if definition:
            entry["definition"] = definition
        as_dictionary = class_info.tagged_values.get("asDictionary")
        if as_dictionary:
            entry["asDictionary"] = as_dictionary
        code_list = class_info.tagged_values.get("codeList")
        if code_list:
            entry["codeList"] = code_list
        if entry:
            listings[class_info.name] = entry
    return listings


def _build_feature_type(
    class_info: _UmlClass,
    classes_by_id: Mapping[str, _UmlClass],
    classes_by_name: Mapping[str, _UmlClass],
    parents: Mapping[str, Sequence[str]],
    codelists: Mapping[str, Mapping[str, Any]],
    associations: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    inherited = _collect_attributes_with_inheritance(class_info.id, classes_by_id, parents)

    geometry: dict[str, Any] | None = None
    attributes: list[dict[str, Any]] = []

    for attribute in inherited:
        if geometry is None and _is_geometry_attribute(attribute):
            geometry = _build_geometry_attribute(attribute)
            continue
        converted = _convert_attribute(
            attribute,
            classes_by_id,
            classes_by_name,
            parents,
            codelists,
            visited_types=set(),
        )
        if converted:
            attributes.append(converted)

    description = _clean_text(class_info.tagged_values.get("documentation"))

    parent_names = [classes_by_id[parent].name for parent in parents.get(class_info.id, []) if parent in classes_by_id]
    association_entries = list(associations.get(class_info.id, []))
    relationships: dict[str, Any] = {
        "inheritance": parent_names,
        "associations": association_entries,
    }

    feature_dict: dict[str, Any] = {
        "name": class_info.name,
        "description": description,
        "attributes": attributes,
        "abstract": class_info.abstract,
        "relationships": relationships,
    }

    if geometry:
        feature_dict["geometry"] = geometry

    return feature_dict


def _collect_attributes_with_inheritance(
    class_id: str,
    classes_by_id: Mapping[str, _UmlClass],
    parents: Mapping[str, Sequence[str]],
) -> list[_UmlAttribute]:
    info = classes_by_id.get(class_id)
    if not info:
        return []
    # Only include attributes declared directly on the class; inheritance is captured separately
    direct_attrs: list[_UmlAttribute] = []
    positions: dict[str, int] = {}
    for attribute in info.attributes:
        key = _attribute_display_name(attribute)
        if not key:
            continue
        if key in positions:
            direct_attrs[positions[key]] = attribute
        else:
            positions[key] = len(direct_attrs)
            direct_attrs.append(attribute)
    return direct_attrs


def _build_inheritance_chain(
    class_id: str,
    parents: Mapping[str, Sequence[str]],
) -> list[str]:
    chain: list[str] = []
    visited: set[str] = set()

    def visit(current: str) -> None:
        if current in visited:
            return
        visited.add(current)
        for parent in parents.get(current, []):
            visit(parent)
        chain.append(current)

    visit(class_id)
    return chain


def _convert_attribute(
    attribute: _UmlAttribute,
    classes_by_id: Mapping[str, _UmlClass],
    classes_by_name: Mapping[str, _UmlClass],
    parents: Mapping[str, Sequence[str]],
    codelists: Mapping[str, Mapping[str, Any]],
    visited_types: set[str],
) -> dict[str, Any]:
    name = _attribute_display_name(attribute)
    if not name:
        return {}

    attr_type = attribute.tags.get("type") or attribute.type_name or "CharacterString"

    entry: dict[str, Any] = {
        "name": name,
        "type": attr_type,
    }

    if attribute.description:
        entry["description"] = attribute.description

    cardinality = _format_cardinality(
        attribute.lower or attribute.tags.get("lowerBound"),
        attribute.upper or attribute.tags.get("upperBound"),
    )
    if cardinality:
        entry["cardinality"] = cardinality

    value_domain = _build_value_domain(attr_type, codelists)
    external_codelist = attribute.tags.get("defaultCodeSpace")
    if external_codelist:
        if value_domain is None:
            value_domain = {}
        else:
            value_domain = dict(value_domain)
        value_domain["codeList"] = external_codelist
    as_dictionary = attribute.tags.get("asDictionary")
    if as_dictionary:
        if value_domain is None:
            value_domain = {}
        else:
            value_domain = dict(value_domain)
        value_domain["asDictionary"] = as_dictionary
    if value_domain:
        entry["valueDomain"] = value_domain

    data_type = classes_by_name.get(attr_type)
    if data_type and data_type.stereotype == "dataType" and data_type.id not in visited_types:
        nested_types = set(visited_types)
        nested_types.add(data_type.id)
        nested_attributes = _collect_attributes_with_inheritance(data_type.id, classes_by_id, parents)
        nested_entries: list[dict[str, Any]] = []
        for nested in nested_attributes:
            converted = _convert_attribute(
                nested,
                classes_by_id,
                classes_by_name,
                parents,
                codelists,
                visited_types=nested_types,
            )
            if converted:
                nested_entries.append(converted)
        if nested_entries:
            entry["attributes"] = nested_entries

    return entry


def _build_value_domain(
    attr_type: str | None,
    codelists: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    if not attr_type:
        return None
    codelist = codelists.get(attr_type)
    if not codelist:
        return None
    if not isinstance(codelist, Mapping):
        return None

    domain: dict[str, Any] = {}
    listed_values = codelist.get("listedValues")
    if isinstance(listed_values, Sequence) and not isinstance(listed_values, (str, bytes)):
        domain["listedValues"] = list(listed_values)
    definition = codelist.get("definition")
    if isinstance(definition, str) and definition.strip():
        domain["definition"] = definition.strip()
    as_dictionary = codelist.get("asDictionary")
    if isinstance(as_dictionary, str) and as_dictionary.strip():
        domain["asDictionary"] = as_dictionary.strip()
    code_list = codelist.get("codeList")
    if isinstance(code_list, str) and code_list.strip():
        domain["codeList"] = code_list.strip()

    return domain or None


def _build_geometry_attribute(attribute: _UmlAttribute) -> dict[str, Any]:
    geom_type = attribute.tags.get("type") or attribute.type_name or "geometry"
    name = attribute.name or "geometry"
    geometry: dict[str, Any] = {
        "name": name,
        "type": geom_type,
        "ogcRole": "primary-geometry",
    }
    if attribute.description:
        geometry["description"] = attribute.description
    return geometry


def _is_geometry_attribute(attribute: _UmlAttribute) -> bool:
    type_name = (attribute.tags.get("type") or attribute.type_name or "").upper()
    if type_name.startswith("GM_") or type_name in {
        "GMPOINT",
        "GMCURVE",
        "GMSURFACE",
        "GMOBJECT",
    }:
        return True

    name = (attribute.name or "").upper()
    if any(token in name for token in _GEOMETRY_NAMES):
        return True

    return False


def _attribute_display_name(attribute: _UmlAttribute) -> str:
    name = attribute.name
    return name.strip() if name else ""


def _format_cardinality(lower: str | None, upper: str | None) -> str:
    lower_value = (lower or "").strip()
    upper_value = (upper or "").strip()

    if not lower_value:
        lower_value = "0"
    if not upper_value:
        upper_value = "1"
    if upper_value in {"-1", "*", "n"}:
        upper_value = "*"

    if lower_value == upper_value:
        return lower_value
    return f"{lower_value}..{upper_value}"


def _extract_stereotype(element: ET.Element) -> str | None:
    stereotype = element.find("UML:ModelElement.stereotype/UML:Stereotype", _NS)
    if stereotype is not None:
        name = stereotype.get("name")
        if name:
            return name
        ref = stereotype.get("xmi.idref")
        if ref:
            return ref
    return None


def _extract_tagged_values(element: ET.Element) -> dict[str, str]:
    values: dict[str, str] = {}
    container = element.find("UML:ModelElement.taggedValue", _NS)
    if container is None:
        return values
    for tagged in container.findall("UML:TaggedValue", _NS):
        tag = tagged.get("tag")
        value = tagged.get("value")
        if tag:
            values[tag] = value if value is not None else ""
    return values


def _extract_type_name(attribute: ET.Element) -> str | None:
    type_elem = attribute.find("UML:StructuralFeature.type/UML:Classifier", _NS)
    if type_elem is None:
        return None
    for child in type_elem:
        name = child.get("name")
        if name:
            return name
    return None


def _extract_bounds(
    attribute: ET.Element,
    tags: Mapping[str, str],
) -> tuple[str | None, str | None]:
    multiplicity = attribute.find(
        "UML:StructuralFeature.multiplicity/UML:Multiplicity/UML:Multiplicity.range/UML:MultiplicityRange",
        _NS,
    )
    if multiplicity is not None:
        return multiplicity.get("lower"), multiplicity.get("upper")
    return tags.get("lowerBound"), tags.get("upperBound")


def _extract_association_bounds(end: ET.Element) -> tuple[str | None, str | None]:
    multiplicity_attr = end.get("multiplicity")
    if multiplicity_attr:
        return _split_range(multiplicity_attr)

    multiplicity = end.find(
        "UML:AssociationEnd.multiplicity/UML:Multiplicity/UML:Multiplicity.range/UML:MultiplicityRange",
        _NS,
    )
    if multiplicity is not None:
        return multiplicity.get("lower"), multiplicity.get("upper")
    return None, None


def _association_end_is_navigable(end: ET.Element) -> bool | None:
    raw = end.get("isNavigable") or end.get("navigable")
    parsed = _parse_bool(raw)
    if parsed is not None:
        return parsed

    nav_elem = end.find("UML:AssociationEnd.isNavigable", _NS)
    if nav_elem is not None:
        raw = nav_elem.get("xmi.value") or nav_elem.get("value")
        parsed = _parse_bool(raw)
        if parsed is not None:
            return parsed
        bool_expr = nav_elem.find(".//UML:BooleanExpression", _NS)
        if bool_expr is not None:
            raw = bool_expr.get("body") or (bool_expr.text or "")
            parsed = _parse_bool(raw)
            if parsed is not None:
                return parsed

    return None


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    text = value.strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _get_identifier(element: ET.Element) -> str | None:
    return element.get("xmi.id") or element.get("{http://www.omg.org/XMI}id")


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = html.unescape(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(part.strip() for part in text.splitlines()).strip()
    return text


def _split_range(value: str) -> tuple[str | None, str | None]:
    if ".." in value:
        lower, upper = value.split("..", 1)
        return lower or None, upper or None
    return value, value
