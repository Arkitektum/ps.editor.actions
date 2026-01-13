"""Utilities for deriving feature type metadata from an OGC API - Features service."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
import xml.etree.ElementTree as ElementTree

try:  # pragma: no cover - optional dependency when testing
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore[assignment]

HTTPGet = Callable[[str], Any]


_GEOJSON_GEOMETRY_NAMES = {
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
    "GeometryCollection",
    "Curve",
    "MultiCurve",
    "Surface",
    "MultiSurface",
}


def _default_http_get(url: str) -> Any:
    if requests is None:  # pragma: no cover - defensive, requests should be available
        raise RuntimeError("The 'requests' library is required to fetch data.")
    return requests.get(url, timeout=30)


def load_feature_types(collections_url: str, http_get: HTTPGet | None = None) -> list[dict[str, Any]]:
    """Load feature type metadata from an OGC API collections endpoint.

    Parameters
    ----------
    collections_url:
        Fully qualified URL to the ``/collections`` endpoint of an OGC API - Features
        service.
    http_get:
        Optional callable used to perform HTTP GET requests. The callable must accept
        a URL and return an object that exposes ``status_code`` and ``json()`` similar
        to the response object provided by :mod:`requests`. When omitted, ``requests``
        will be used.

    Returns
    -------
    list[dict[str, Any]]
        A list of dictionaries that follow the ``featureTypes`` structure used in
        ``data/psdata.json``.

    Raises
    ------
    RuntimeError
        If fetching the collections document fails or responds with an HTTP error.
    ValueError
        If the payload is missing required keys or contains invalid data.
    """

    getter = http_get or _default_http_get

    try:
        response = getter(collections_url)
    except Exception as exc:  # pragma: no cover - simple network error conversion
        raise RuntimeError(f"Failed to fetch collections from '{collections_url}'.") from exc

    status_code = getattr(response, "status_code", None)
    if status_code is not None and int(status_code) >= 400:
        raise RuntimeError(
            f"Request to '{collections_url}' failed with status code {status_code}."
        )

    if hasattr(response, "raise_for_status"):
        try:
            response.raise_for_status()
        except Exception as exc:  # pragma: no cover - handled above in most cases
            raise RuntimeError(
                f"Request to '{collections_url}' failed: {exc}."
            ) from exc

    payload = _response_json(response)
    if payload is None:
        raise ValueError("Collections response did not contain valid JSON.")
    if isinstance(payload, Mapping):
        collections = payload.get("collections")
    else:
        collections = None
    if collections is None:
        collections_link = _find_collections_link(payload)
        if collections_link:
            follow = _load_json_mapping(collections_link, getter)
            if isinstance(follow, Mapping):
                collections = follow.get("collections")
                if collections is None and isinstance(
                    follow.get("links"), Sequence
                ):
                    collections = follow.get("collections")
        if collections is None and isinstance(payload, Sequence) and not isinstance(
            payload, (str, bytes)
        ):
            collections = payload

    if not (isinstance(collections, Sequence) and not isinstance(collections, (str, bytes))):
        raise ValueError("Collections response missing 'collections' array.")

    feature_types: list[dict[str, Any]] = []
    for collection in collections:
        if not isinstance(collection, Mapping):
            continue

        name = collection.get("id") or collection.get("title")
        if not isinstance(name, str):
            raise ValueError("Each collection must include an 'id' or 'title'.")

        description = collection.get("description")
        if not isinstance(description, str):
            description = ""

        schema_candidates: list[Mapping[str, Any]] = []
        additional_sources: list[Mapping[str, Any]] = []

        schema_url = _find_schema_link(collection)
        queryables_url = _find_queryables_link(collection)

        if schema_url:
            schema_mapping = _load_schema(schema_url, getter)
            if schema_mapping:
                schema_candidates.append(schema_mapping)

        if schema_url is None or queryables_url is None:
            detail = _load_collection_detail(collection, getter)
            if detail:
                additional_sources.append(detail)
                if schema_url is None:
                    schema_url = _find_schema_link(detail)
                    if schema_url:
                        schema_mapping = _load_schema(schema_url, getter)
                        if schema_mapping:
                            schema_candidates.append(schema_mapping)
                if queryables_url is None:
                    queryables_url = _find_queryables_link(detail)

        if queryables_url:
            queryables_mapping = _load_schema(queryables_url, getter)
            if queryables_mapping:
                schema_candidates.append(queryables_mapping)

        primary_schema: Mapping[str, Any] | None = None
        if schema_candidates:
            primary_schema = schema_candidates[0]
            additional_sources = schema_candidates[1:] + additional_sources

        schema_title: str | None = None
        if isinstance(primary_schema, Mapping):
            title_candidate = primary_schema.get("title")
            if isinstance(title_candidate, str) and title_candidate.strip():
                schema_title = title_candidate.strip()

        if schema_title:
            name = schema_title

        geometry = _extract_geometry(
            collection,
            schema=primary_schema,
            extra_sources=additional_sources,
        )
        attributes = _extract_attributes(
            collection,
            primary_schema,
            additional_sources,
        )

        feature_types.append(
            {
                "name": name,
                "description": description,
                "geometry": geometry,
                "attributes": attributes,
            }
        )

    return feature_types


def _find_schema_link(collection: Mapping[str, Any]) -> str | None:
    return _find_link_href(
        collection,
        rel_candidates={
            "http://www.opengis.net/def/rel/ogc/1.0/schema",
            "http://www.opengis.net/def/rel/ogc/0.0/schema",
            "describedby",
        },
    )


def _find_queryables_link(collection: Mapping[str, Any]) -> str | None:
    return _find_link_href(
        collection,
        rel_candidates={
            "http://www.opengis.net/def/rel/ogc/1.0/queryables",
            "http://www.opengis.net/def/rel/ogc/0.0/queryables",
            "queryables",
        },
    )


def _find_self_link(collection: Mapping[str, Any]) -> str | None:
    return _find_link_href(collection, rel_candidates={"self"})


def _find_collections_link(document: Mapping[str, Any]) -> str | None:
    return _find_link_href(
        document,
        rel_candidates={
            "http://www.opengis.net/def/rel/ogc/1.0/collections",
            "http://www.opengis.net/def/rel/ogc/0.0/collections",
            "collections",
            "data",
        },
    )


def _find_link_href(
    collection: Mapping[str, Any],
    *,
    rel_candidates: Iterable[str],
) -> str | None:
    links = collection.get("links")
    if not isinstance(links, Sequence) or isinstance(links, (str, bytes)):
        return None

    rel_values = {candidate.lower() for candidate in rel_candidates}

    for link in links:
        if not isinstance(link, Mapping):
            continue

        rel = link.get("rel")
        href = link.get("href")

        if (
            isinstance(rel, str)
            and rel.lower() in rel_values
            and isinstance(href, str)
            and href
        ):
            return href

    return None


def _load_schema(url: str, getter: HTTPGet) -> Mapping[str, Any] | None:
    response = _fetch_response(url, getter)
    if response is None:
        return None

    if _response_looks_like_xml(response, url):
        xml_text = _response_text(response)
        if xml_text:
            schema = _parse_gml_schema(xml_text)
            if schema:
                return schema

    json_mapping = _response_json_mapping(response)
    if json_mapping is not None:
        return json_mapping

    return None


def _load_collection_detail(
    collection: Mapping[str, Any], getter: HTTPGet
) -> Mapping[str, Any] | None:
    self_link = _find_self_link(collection)
    if not self_link:
        return None

    return _load_json_mapping(self_link, getter)


def _load_json_mapping(url: str, getter: HTTPGet) -> Mapping[str, Any] | None:
    response = _fetch_response(url, getter)
    if response is None:
        return None
    return _response_json_mapping(response)


def _fetch_response(url: str, getter: HTTPGet) -> Any | None:
    try:
        response = getter(url)
    except Exception:  # pragma: no cover - tolerate fetch failures
        return None

    status_code = getattr(response, "status_code", None)
    if status_code is not None and int(status_code) >= 400:
        return None

    if hasattr(response, "raise_for_status"):
        try:
            response.raise_for_status()
        except Exception:  # pragma: no cover - tolerate fetch failures
            return None

    return response


def _response_json_mapping(response: Any) -> Mapping[str, Any] | None:
    payload = _response_json(response)
    if payload is None:
        return None

    if isinstance(payload, Mapping):
        return payload

    return None


def _response_json(response: Any) -> Any | None:
    try:
        return response.json()
    except Exception:  # pragma: no cover - invalid JSON
        return None


def _response_text(response: Any) -> str | None:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text
    content = getattr(response, "content", None)
    if isinstance(content, (bytes, bytearray)):
        try:
            decoded = content.decode("utf-8", errors="ignore")
        except Exception:  # pragma: no cover - defensive
            return None
        if decoded.strip():
            return decoded
    return None


def _response_looks_like_xml(response: Any, url: str) -> bool:
    content_type = ""
    headers = getattr(response, "headers", None)
    if isinstance(headers, Mapping):
        content_type = str(headers.get("Content-Type", "")).lower()

    url_lower = url.lower()
    if any(url_lower.endswith(suffix) for suffix in (".xsd", ".xml")):
        return True

    return any(marker in content_type for marker in ("xml", "gml", "xsd"))


def _parse_gml_schema(xml_text: str) -> Mapping[str, Any] | None:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:  # pragma: no cover - invalid XML
        return None

    xsd_namespace = _detect_xsd_namespace(root)
    qname = _qualify_tag(xsd_namespace)

    complex_types: dict[str, ElementTree.Element] = {}
    for complex_type in root.findall(f".//{qname('complexType')}"):
        name = complex_type.get("name")
        if isinstance(name, str) and name:
            complex_types[name] = complex_type

    feature_type_name: str | None = None
    feature_element_name: str | None = None
    for element in root.findall(f".//{qname('element')}"):
        substitution_group = element.get("substitutionGroup") or ""
        if "AbstractFeature" in substitution_group:
            feature_type_name = _strip_namespace(element.get("type"))
            feature_element_name = element.get("name")
            if feature_type_name:
                break

    if not feature_type_name and len(complex_types) == 1:
        feature_type_name = next(iter(complex_types))

    selected_complex_type = None
    if feature_type_name and feature_type_name in complex_types:
        selected_complex_type = complex_types[feature_type_name]
    elif complex_types:
        selected_complex_type = next(iter(complex_types.values()))

    if selected_complex_type is None:
        return None

    properties = _parse_complex_type_properties(selected_complex_type, xsd_namespace)
    if not properties:
        return None

    title = feature_type_name or feature_element_name or selected_complex_type.get("name")
    schema: dict[str, Any] = {
        "properties": properties,
    }
    if isinstance(title, str) and title:
        schema["title"] = title

    return schema


def _detect_xsd_namespace(root: ElementTree.Element) -> str:
    if root.tag.startswith("{"):
        namespace = root.tag.split("}", 1)[0][1:]
        if namespace:
            return namespace
    return "http://www.w3.org/2001/XMLSchema"


def _qualify_tag(namespace: str) -> Callable[[str], str]:
    def _qualifier(tag: str) -> str:
        return f"{{{namespace}}}{tag}"

    return _qualifier


def _strip_namespace(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    if ":" in value:
        return value.split(":", 1)[1]
    return value


def _parse_complex_type_properties(
    complex_type: ElementTree.Element, xsd_namespace: str
) -> dict[str, Any]:
    qname = _qualify_tag(xsd_namespace)
    element_paths = [
        f"./{qname('sequence')}/{qname('element')}",
        f"./{qname('complexContent')}/{qname('extension')}/{qname('sequence')}/{qname('element')}",
        f"./{qname('complexContent')}/{qname('extension')}//{qname('element')}",
        f"./{qname('sequence')}//{qname('element')}",
    ]

    elements: list[ElementTree.Element] = []
    for path in element_paths:
        found = complex_type.findall(path)
        if found:
            elements = found
            break

    if not elements:
        elements = complex_type.findall(f".//{qname('element')}")

    properties: dict[str, Any] = {}
    seen: set[str] = set()
    for element in elements:
        parsed = _parse_xsd_element(element, xsd_namespace)
        if parsed is None:
            continue
        name, details = parsed
        if name in seen:
            continue
        seen.add(name)
        properties[name] = details

    return properties


def _parse_xsd_element(
    element: ElementTree.Element, xsd_namespace: str
) -> tuple[str, dict[str, Any]] | None:
    name = element.get("name")
    if not name:
        name = _strip_namespace(element.get("ref"))
    if not name:
        return None

    details: dict[str, Any] = {}
    type_value = element.get("type")
    if isinstance(type_value, str) and type_value:
        details["type"] = type_value
    elif element.get("ref"):
        details["type"] = element.get("ref")

    substitution_group = element.get("substitutionGroup")
    if isinstance(substitution_group, str) and substitution_group:
        details["substitutionGroup"] = substitution_group

    for key in ("minOccurs", "maxOccurs"):
        value = element.get(key)
        if value is None:
            continue
        if value == "unbounded":
            details[key] = value
            continue
        try:
            details[key] = int(value)
        except (TypeError, ValueError):
            continue

    nillable = element.get("nillable")
    if isinstance(nillable, str) and nillable:
        details["nillable"] = nillable

    description = _extract_xsd_documentation(element, xsd_namespace)
    if description:
        details["description"] = description

    if _looks_like_geometry_type(details):
        details.setdefault("format", "gml")

    return name, details


def _extract_xsd_documentation(
    element: ElementTree.Element, xsd_namespace: str
) -> str | None:
    qname = _qualify_tag(xsd_namespace)
    doc = element.find(f"./{qname('annotation')}/{qname('documentation')}")
    if doc is not None and isinstance(doc.text, str):
        text = doc.text.strip()
        if text:
            return text
    return None


def _extract_geometry(
    collection: Mapping[str, Any],
    *,
    schema: Mapping[str, Any] | None = None,
    extra_sources: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    geometry: dict[str, Any] = {}

    item_type = collection.get("itemType") or collection.get("item_type")
    if isinstance(item_type, str) and item_type:
        geometry["itemType"] = item_type

    documents: list[Mapping[str, Any]] = [collection]
    if schema is not None:
        documents.insert(0, schema)
    if extra_sources:
        for source in extra_sources:
            if isinstance(source, Mapping):
                documents.append(source)

    document_sequence: Sequence[Mapping[str, Any]] = tuple(
        doc for doc in documents if isinstance(doc, Mapping)
    )

    geometry_definitions = list(_iter_geometry_definitions(document_sequence))
    geometry_types = _collect_geometry_types(geometry_definitions)
    geometry_format = _select_geometry_format(geometry_definitions)
    if geometry_types:
        primary_type = geometry_types[0]
        geometry["type"] = primary_type
        if len(geometry_types) > 1:
            geometry["types"] = geometry_types
    else:
        if geometry_format:
            geometry["type"] = geometry_format
        else:
            parsed_geometry_type = _select_geometry_type(
                geometry_definitions,
                document_sequence,
            )
            if parsed_geometry_type:
                geometry["type"] = parsed_geometry_type
            elif isinstance(item_type, str) and item_type:
                geometry["type"] = item_type

    crs_values: list[str] = []

    collection_crs = collection.get("crs")
    if isinstance(collection_crs, Sequence) and not isinstance(
        collection_crs, (str, bytes)
    ):
        for value in collection_crs:
            if isinstance(value, str) and value and value not in crs_values:
                crs_values.append(value)

    extent = collection.get("extent")
    if isinstance(extent, Mapping):
        spatial = extent.get("spatial")
        if isinstance(spatial, Mapping):
            crs = spatial.get("crs") or spatial.get("srs")
            if isinstance(crs, str) and crs and crs not in crs_values:
                crs_values.append(crs)

    if crs_values:
        geometry["crs"] = crs_values

    storage_crs = collection.get("storageCrs")
    if isinstance(storage_crs, str) and storage_crs:
        geometry["storageCrs"] = storage_crs

    if geometry_format:
        geometry["format"] = geometry_format

    geometry_role = _select_geometry_role(
        geometry_definitions,
        document_sequence,
    )
    if geometry_role is not None:
        geometry["ogcRole"] = geometry_role

    if "type" not in geometry:
        geometry["type"] = "Unknown"

    return geometry


@dataclass
class _GeometryDefinition:
    details: Mapping[str, Any]
    source: Mapping[str, Any] | None


def _collect_geometry_types(
    definitions: Sequence[_GeometryDefinition] | None,
) -> list[str]:
    if not definitions:
        return []

    geometry_types: list[str] = []
    seen: set[str] = set()

    for definition in definitions:
        for geometry_type in _extract_geojson_type_names(definition.details):
            if geometry_type not in seen:
                seen.add(geometry_type)
                geometry_types.append(geometry_type)

    return geometry_types


def _iter_geometry_definitions(
    documents: Sequence[Mapping[str, Any]] | None,
) -> Iterable[_GeometryDefinition]:
    if not documents:
        return

    for document in documents:
        if not isinstance(document, Mapping):
            continue

        container = _get_properties_container(document)
        if container is None:
            continue

        for name, details in _iter_attribute_definitions(container):
            if not _is_geometry_attribute(name, details):
                continue

            resolved_details = _resolve_attribute_details(
                details,
                source=document,
                schema_sources=documents,
            )

            if isinstance(resolved_details, Mapping):
                yield _GeometryDefinition(resolved_details, document)
            elif isinstance(details, Mapping):
                yield _GeometryDefinition(details, document)


def _select_geometry_type(
    definitions: Sequence[_GeometryDefinition],
    documents: Sequence[Mapping[str, Any]],
) -> str | None:
    geometry_name_markers = {name.lower() for name in _GEOJSON_GEOMETRY_NAMES}
    for definition in definitions:
        candidate = _parse_attribute_type(
            definition.details,
            source=definition.source,
            schema_sources=documents,
        )
        if isinstance(candidate, str):
            normalized = candidate.strip()
            if not normalized:
                continue
            lowered = normalized.lower()
            if lowered in {"unknown", "object"}:
                continue
            if lowered not in geometry_name_markers and "geometry" not in lowered:
                continue
            return normalized
    return None


def _select_geometry_format(
    definitions: Sequence[_GeometryDefinition],
) -> str | None:
    for definition in definitions:
        fmt = definition.details.get("format")
        if isinstance(fmt, str):
            stripped = fmt.strip()
            if stripped:
                return stripped
    return None


def _select_geometry_role(
    definitions: Sequence[_GeometryDefinition],
    documents: Sequence[Mapping[str, Any]],
) -> Any:
    for definition in definitions:
        role = _extract_ogc_role(
            definition.details,
            documents,
            current_document=definition.source,
        )
        if role is not None:
            return role
    return None


def _extract_geojson_type_names(details: Any) -> list[str]:
    collected: list[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        if value in _GEOJSON_GEOMETRY_NAMES and value not in seen:
            seen.add(value)
            collected.append(value)

    def _walk(node: Any) -> None:
        if isinstance(node, Mapping):
            geometry_type = node.get("geometryType")
            if isinstance(geometry_type, str):
                _add(geometry_type)

            direct_type = node.get("type")
            if isinstance(direct_type, str):
                _add(direct_type)
            elif isinstance(direct_type, Sequence) and not isinstance(
                direct_type, (str, bytes)
            ):
                for entry in direct_type:
                    if isinstance(entry, str):
                        _add(entry)

            enum_values = node.get("enum")
            if isinstance(enum_values, Sequence) and not isinstance(
                enum_values, (str, bytes)
            ):
                for value in enum_values:
                    if isinstance(value, str):
                        _add(value)

            const_value = node.get("const")
            if isinstance(const_value, str):
                _add(const_value)

            properties = node.get("properties")
            if isinstance(properties, Mapping):
                for key in ("type", "geometryType"):
                    prop = properties.get(key)
                    if prop is not None:
                        _walk(prop)

            items = node.get("items")
            if items is not None:
                _walk(items)

            for key in ("allOf", "anyOf", "oneOf"):
                group = node.get(key)
                if isinstance(group, Sequence) and not isinstance(group, (str, bytes)):
                    for entry in group:
                        _walk(entry)

        elif isinstance(node, Sequence) and not isinstance(node, (str, bytes)):
            for entry in node:
                _walk(entry)

    _walk(details)

    return collected


@dataclass
class _AttributeNode:
    name: str
    path: tuple[str, ...]
    type: str | None = None
    description: str | None = None
    ogc_role: Any | None = None
    details: Any | None = None
    source: Mapping[str, Any] | None = None
    children: dict[str, "_AttributeNode"] = field(default_factory=dict)
    child_order: list[str] = field(default_factory=list)
    value_domain: Mapping[str, Any] | None = None
    required: bool | None = None
    min_occurs: int | None = None
    max_occurs: int | None = None
    is_array: bool | None = None


def _extract_attributes(
    collection: Mapping[str, Any],
    schema: Mapping[str, Any] | None = None,
    extra_sources: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    sources: list[Mapping[str, Any]] = []
    if schema:
        sources.append(schema)
    if extra_sources:
        for source in extra_sources:
            if isinstance(source, Mapping):
                sources.append(source)
    sources.append(collection)

    documents: Sequence[Mapping[str, Any]] = tuple(sources)

    nodes: dict[tuple[str, ...], _AttributeNode] = {}
    top_level_order: list[str] = []

    def _get_or_create_node(path: tuple[str, ...]) -> _AttributeNode:
        if path in nodes:
            return nodes[path]

        name = path[-1]
        node = _AttributeNode(name=name, path=path)
        nodes[path] = node

        if len(path) == 1:
            if name not in top_level_order:
                top_level_order.append(name)
        else:
            parent = _get_or_create_node(path[:-1])
            if name not in parent.children:
                parent.children[name] = node
                parent.child_order.append(name)

        return node

    for source in sources:
        properties = _get_properties_container(source)
        for raw_name, details in _iter_attribute_definitions(properties):
            if _is_geometry_attribute(raw_name, details):
                continue

            if not isinstance(raw_name, str):
                continue

            segments = tuple(part for part in raw_name.split(".") if part)
            if not segments:
                continue

            node = _get_or_create_node(segments)

            if len(segments) > 1:
                for depth in range(1, len(segments)):
                    parent_node = _get_or_create_node(segments[:depth])
                    if parent_node.type is None:
                        parent_node.type = "object"

            _update_attribute_node(
                node,
                details,
                source if isinstance(source, Mapping) else None,
                documents,
                _get_or_create_node,
                segments,
            )

    attributes: list[dict[str, Any]] = []
    for name in top_level_order:
        path = (name,)
        node = nodes.get(path)
        if node is None:
            continue
        attributes.append(_node_to_attribute(node))

    return attributes


def _update_attribute_node(
    node: _AttributeNode,
    details: Any,
    source: Mapping[str, Any] | None,
    documents: Sequence[Mapping[str, Any]],
    get_node: Callable[[tuple[str, ...]], _AttributeNode],
    path: tuple[str, ...],
) -> None:
    if node.details is None and isinstance(details, Mapping):
        node.details = details
        if source is not None:
            node.source = source

    if node.type is None:
        node.type = _parse_attribute_type(
            details,
            source=source,
            schema_sources=documents,
        )

    description = _extract_description(details)
    if description and node.description is None:
        node.description = description

    role = _extract_ogc_role(details, documents, current_document=source)
    if role is not None and node.ogc_role is None:
        node.ogc_role = role

    resolved_details = _resolve_attribute_details(
        details,
        source=source,
        schema_sources=documents,
    )

    _apply_cardinality_metadata(
        node,
        details,
        resolved_details,
        source,
        path,
    )

    if isinstance(resolved_details, Mapping):
        child_properties = _get_properties_container(resolved_details)
        if child_properties:
            node.type = "object"
            for child_name, child_details in _iter_attribute_definitions(child_properties):
                if child_name == "geometry":
                    continue
                child_segments = node.path + (child_name,)
                child_node = get_node(child_segments)
                _update_attribute_node(
                    child_node,
                    child_details,
                    resolved_details if isinstance(resolved_details, Mapping) else None,
                    documents,
                    get_node,
                    child_segments,
                )

    if node.value_domain is None:
        value_domain = _extract_enumeration_domain(
            resolved_details,
            attribute_type=node.type,
        )
        if value_domain is not None:
            node.value_domain = value_domain


def _apply_cardinality_metadata(
    node: _AttributeNode,
    raw_details: Any,
    resolved_details: Any,
    source: Mapping[str, Any] | None,
    path: tuple[str, ...],
) -> None:
    required_flag = _extract_required_flag(raw_details)
    if required_flag is None:
        required_flag = _extract_required_flag(resolved_details)
    if required_flag is None:
        required_flag = _extract_required_from_document(source, path)
    if required_flag is not None:
        if node.required is None:
            node.required = required_flag
        else:
            node.required = node.required or required_flag

    min_occurs = _extract_min_occurs(raw_details)
    if min_occurs is None:
        min_occurs = _extract_min_occurs(resolved_details)
    if min_occurs is not None:
        if node.min_occurs is None or min_occurs > node.min_occurs:
            node.min_occurs = min_occurs

    max_occurs = _extract_max_occurs(raw_details)
    if max_occurs is None:
        max_occurs = _extract_max_occurs(resolved_details)
    if max_occurs is not None:
        if node.max_occurs is None or max_occurs < node.max_occurs:
            node.max_occurs = max_occurs

    array_flag = _determine_is_array(raw_details)
    if array_flag is None:
        array_flag = _determine_is_array(resolved_details)
    if array_flag is not None:
        if node.is_array is None:
            node.is_array = array_flag
        else:
            node.is_array = node.is_array or array_flag

    if node.is_array:
        min_items = _extract_min_items(raw_details)
        if min_items is None:
            min_items = _extract_min_items(resolved_details)
        if min_items is not None:
            if node.min_occurs is None or min_items > node.min_occurs:
                node.min_occurs = min_items

        max_items = _extract_max_items(raw_details)
        if max_items is None:
            max_items = _extract_max_items(resolved_details)
        if max_items is not None:
            if node.max_occurs is None or max_items < node.max_occurs:
                node.max_occurs = max_items


def _extract_required_flag(details: Any) -> bool | None:
    if not isinstance(details, Mapping):
        return None

    value = details.get("required")
    bool_value = _coerce_to_bool(value)
    if bool_value is not None:
        return bool_value

    min_occurs = _extract_min_occurs(details)
    if min_occurs is not None:
        return min_occurs > 0

    return None


def _extract_required_from_document(
    document: Mapping[str, Any] | None,
    path: tuple[str, ...],
) -> bool | None:
    if document is None or not isinstance(document, Mapping) or not path:
        return None

    required_names, present = _normalize_required_names(document.get("required"))
    if required_names:
        joined = ".".join(path)
        if joined in required_names:
            return True
        if len(path) == 1 and path[0] in required_names:
            return True

    if present and len(path) == 1 and path[0] not in required_names:
        return False

    return None


def _normalize_required_names(value: Any) -> tuple[set[str], bool]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        names: set[str] = set()
        for item in value:
            if isinstance(item, str) and item:
                names.add(item)
        return names, True
    return set(), False


def _extract_min_occurs(details: Any) -> int | None:
    if not isinstance(details, Mapping):
        return None
    for key in ("minOccurs", "min_occurs", "minoccurs", "minItems", "min_items", "minitems"):
        value = details.get(key)
        if isinstance(value, int):
            return value
    return None


def _extract_max_occurs(details: Any) -> int | None:
    if not isinstance(details, Mapping):
        return None
    for key in ("maxOccurs", "max_occurs", "maxoccurs", "maxItems", "max_items", "maxitems"):
        value = details.get(key)
        if isinstance(value, int):
            return value
    return None


def _extract_min_items(details: Any) -> int | None:
    if not isinstance(details, Mapping):
        return None
    for key in ("minItems", "min_items", "minitems"):
        value = details.get(key)
        if isinstance(value, int):
            return value
    return None


def _extract_max_items(details: Any) -> int | None:
    if not isinstance(details, Mapping):
        return None
    for key in ("maxItems", "max_items", "maxitems"):
        value = details.get(key)
        if isinstance(value, int):
            return value
    return None


def _determine_is_array(details: Any) -> bool | None:
    if not isinstance(details, Mapping):
        return None

    type_value = details.get("type") or details.get("dataType")
    if isinstance(type_value, str):
        if type_value.lower() == "array":
            return True
    elif isinstance(type_value, Sequence) and not isinstance(type_value, (str, bytes)):
        for entry in type_value:
            if isinstance(entry, str) and entry.lower() == "array":
                return True

    if "items" in details:
        return True

    max_occurs = details.get("maxOccurs") or details.get("max_occurs") or details.get(
        "maxoccurs"
    )
    if isinstance(max_occurs, str) and max_occurs.lower() == "unbounded":
        return True
    if isinstance(max_occurs, int) and max_occurs > 1:
        return True

    min_occurs = details.get("minOccurs") or details.get("min_occurs") or details.get(
        "minoccurs"
    )
    if isinstance(min_occurs, int) and min_occurs > 1:
        return True

    return None


def _is_geometry_attribute(name: Any, details: Any) -> bool:
    if isinstance(name, str) and name == "geometry":
        return True

    if not isinstance(details, Mapping):
        return False

    if isinstance(name, str):
        lowered = name.lower()
        if lowered in {"geom", "geometry", "shape", "the_geom", "wkb_geometry"}:
            return True

    return _looks_like_geometry_type(details)


def _looks_like_geometry_type(details: Mapping[str, Any]) -> bool:
    type_value = details.get("type") or details.get("dataType") or details.get("ref")
    if isinstance(type_value, str):
        lowered = type_value.lower()
        if "gml" in lowered or "geometry" in lowered:
            return True

    fmt = details.get("format")
    if isinstance(fmt, str) and ("gml" in fmt.lower() or "geometry" in fmt.lower()):
        return True

    substitution_group = details.get("substitutionGroup") or details.get(
        "substitution_group"
    )
    if isinstance(substitution_group, str) and "geometry" in substitution_group.lower():
        return True

    return False


def _coerce_to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return None


def _extract_enumeration_domain(
    details: Any,
    *,
    attribute_type: str | None,
) -> Mapping[str, Any] | None:
    if not isinstance(details, Mapping):
        return None

    if attribute_type and "object" in attribute_type.lower():
        return None

    values = _collect_enumeration_values(details, attribute_type)
    if not values:
        return None

    listed_values = [
        {"value": value, "label": label}
        for value, label in values
    ]

    return {"type": "enumeration", "listedValues": listed_values}


def _collect_enumeration_values(
    details: Mapping[str, Any], attribute_type: str | None
) -> list[tuple[Any, str]]:
    values: list[tuple[Any, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(value: Any, label: str) -> None:
        if not _is_simple_enum_value(value):
            return
        marker = (
            type(value).__name__,
            json.dumps(value, sort_keys=True, ensure_ascii=False),
        )
        if marker in seen:
            return
        seen.add(marker)
        values.append((value, label))

    def _walk(node: Any) -> None:
        if isinstance(node, Mapping):
            enum_values = node.get("enum")
            if isinstance(enum_values, Sequence) and not isinstance(enum_values, (str, bytes)):
                labels = _extract_enum_labels(node, len(enum_values))
                for index, enum_value in enumerate(enum_values):
                    if not _is_simple_enum_value(enum_value):
                        continue
                    label = (
                        labels[index]
                        if labels and index < len(labels)
                        else str(enum_value)
                    )
                    _add(enum_value, label)

            if "const" in node:
                const_value = node.get("const")
                if _is_simple_enum_value(const_value):
                    label = _get_enum_entry_label(node, const_value)
                    _add(const_value, label)

            if isinstance(attribute_type, str) and "array" in attribute_type.lower():
                items = node.get("items")
                if items is not None:
                    _walk(items)
            elif node.get("type") == "array":
                items = node.get("items")
                if items is not None:
                    _walk(items)

            for key in ("anyOf", "oneOf", "allOf"):
                group = node.get(key)
                if isinstance(group, Sequence) and not isinstance(group, (str, bytes)):
                    for entry in group:
                        _walk(entry)

        elif isinstance(node, Sequence) and not isinstance(node, (str, bytes)):
            for entry in node:
                _walk(entry)

    _walk(details)

    return values


def _is_simple_enum_value(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


def _extract_enum_labels(details: Mapping[str, Any], count: int) -> list[str] | None:
    label_keys = (
        "enumNames",
        "enumTitles",
        "enum_titles",
        "x-enumNames",
        "x-enumTitles",
        "x-enum-names",
        "x-enum-titles",
        "enumDescriptions",
        "x-enumDescriptions",
        "x-enum-descriptions",
    )
    for key in label_keys:
        labels = details.get(key)
        if isinstance(labels, Sequence) and not isinstance(labels, (str, bytes)):
            extracted: list[str] = []
            for label in labels[:count]:
                if isinstance(label, str):
                    extracted.append(label)
                else:
                    extracted.append(str(label))
            if extracted:
                return extracted
    return None


def _get_enum_entry_label(details: Mapping[str, Any], value: Any) -> str:
    for key in ("title", "label", "name", "description"):
        label_value = details.get(key)
        if isinstance(label_value, str) and label_value.strip():
            return label_value.strip()
    return str(value)


def _extract_description(details: Any) -> str | None:
    if isinstance(details, Mapping):
        for key in ("description", "title"):
            raw_value = details.get(key)
            if isinstance(raw_value, str):
                stripped = raw_value.strip()
                if stripped:
                    return stripped
    return None


def _resolve_attribute_details(
    details: Any,
    *,
    source: Mapping[str, Any] | None,
    schema_sources: Sequence[Mapping[str, Any]] | None,
    _ref_stack: set[str] | None = None,
) -> Any:
    if not isinstance(details, Mapping):
        return details

    ref = details.get("$ref")
    if isinstance(ref, str) and ref:
        if _ref_stack is None:
            _ref_stack = set()
        if ref in _ref_stack:
            return details
        _ref_stack.add(ref)
        try:
            resolved = _resolve_schema_reference(
                ref,
                source=source,
                schema_sources=schema_sources,
            )
        finally:
            _ref_stack.discard(ref)

        if isinstance(resolved, Mapping):
            merged: dict[str, Any] = dict(resolved)
            overrides = {k: v for k, v in details.items() if k != "$ref"}
            merged.update(overrides)
            return _resolve_attribute_details(
                merged,
                source=resolved if isinstance(resolved, Mapping) else source,
                schema_sources=schema_sources,
                _ref_stack=_ref_stack,
            )

    return details


def _node_to_attribute(node: _AttributeNode) -> dict[str, Any]:
    attr_type = node.type
    if not isinstance(attr_type, str) or not attr_type:
        attr_type = "object" if node.children else "unknown"

    attribute: dict[str, Any] = {
        "name": node.name,
        "type": attr_type,
    }

    attribute["cardinality"] = _format_node_cardinality(node, attr_type)

    if node.description is not None:
        attribute["description"] = node.description

    if node.ogc_role is not None:
        attribute["ogcRole"] = node.ogc_role

    if node.value_domain is not None:
        attribute["valueDomain"] = node.value_domain

    if node.children:
        attribute["attributes"] = [
            _node_to_attribute(node.children[name])
            for name in node.child_order
            if name in node.children
        ]

    return attribute


def _format_node_cardinality(node: _AttributeNode, attr_type: str) -> str:
    min_occurs = node.min_occurs
    if min_occurs is not None:
        min_part = "1" if min_occurs > 0 else "0"
    else:
        min_part = "1" if node.required else "0"

    is_array = node.is_array
    attr_type_lower = attr_type.lower() if isinstance(attr_type, str) else ""
    if is_array is None:
        is_array = "array" in attr_type_lower

    if is_array:
        max_occurs = node.max_occurs
        if max_occurs is not None and max_occurs <= 1:
            max_part = "1"
        else:
            max_part = "*"
    else:
        max_part = "1"

    return f"{min_part}..{max_part}"


def _get_properties_container(source: Mapping[str, Any]) -> Any:
    for key in (
        "properties",
        "itemProperties",
        "item_properties",
        "itemproperties",
    ):
        value = source.get(key)
        if value is not None:
            return value
    return None


def _iter_attribute_definitions(properties: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(properties, Mapping):
        for name, details in properties.items():
            if isinstance(name, str):
                yield name, details
    elif isinstance(properties, Sequence) and not isinstance(properties, (str, bytes)):
        for entry in properties:
            if isinstance(entry, Mapping):
                name = entry.get("name")
                if isinstance(name, str):
                    yield name, entry


def _resolve_schema_reference(
    ref: str,
    *,
    source: Mapping[str, Any] | None,
    schema_sources: Sequence[Mapping[str, Any]] | None,
) -> Any:
    if not isinstance(ref, str) or not ref:
        return None

    pointer: str | None = None
    if ref.startswith("#"):
        pointer = ref
        if source is not None:
            resolved = _resolve_json_pointer(source, pointer)
            if resolved is not None:
                return resolved
    else:
        hash_index = ref.find("#")
        if hash_index != -1:
            pointer = "#" + ref[hash_index + 1 :]

    candidate_documents: list[Mapping[str, Any]] = []
    seen_ids: set[int] = set()

    if source is not None and isinstance(source, Mapping):
        candidate_documents.append(source)
        seen_ids.add(id(source))

    if schema_sources:
        for document in schema_sources:
            if not isinstance(document, Mapping):
                continue
            if id(document) in seen_ids:
                continue
            candidate_documents.append(document)
            seen_ids.add(id(document))

    if pointer:
        for document in candidate_documents:
            resolved = _resolve_json_pointer(document, pointer)
            if resolved is not None:
                return resolved

    definition_name: str | None = None
    if pointer:
        stripped_pointer = pointer[1:]
        if stripped_pointer.startswith("/"):
            stripped_pointer = stripped_pointer[1:]
        if stripped_pointer:
            parts = [part for part in stripped_pointer.split("/") if part]
            if parts:
                definition_name = parts[-1]
    else:
        if "#" in ref:
            ref = ref.split("#", 1)[1]
        definition_name = ref.rsplit("/", 1)[-1]

    if definition_name:
        for document in candidate_documents:
            resolved = _find_definition_by_name(document, definition_name)
            if resolved is not None:
                return resolved

    return None


def _find_definition_by_name(document: Mapping[str, Any], name: str) -> Any:
    containers = []
    for key in ("$defs", "definitions"):
        container = document.get(key)
        if isinstance(container, Mapping):
            containers.append(container)

    components = document.get("components")
    if isinstance(components, Mapping):
        schemas = components.get("schemas")
        if isinstance(schemas, Mapping):
            containers.append(schemas)

    for container in containers:
        value = container.get(name)
        if value is not None:
            return value

    return None


def _parse_attribute_type(
    details: Any,
    *,
    source: Mapping[str, Any] | None = None,
    schema_sources: Sequence[Mapping[str, Any]] | None = None,
    _ref_stack: set[str] | None = None,
) -> str:
    if _ref_stack is None:
        _ref_stack = set()

    if isinstance(details, Mapping):
        ref = details.get("$ref")
        if isinstance(ref, str) and ref:
            if ref in _ref_stack:
                return "unknown"
            _ref_stack.add(ref)
            try:
                resolved = _resolve_schema_reference(
                    ref,
                    source=source,
                    schema_sources=schema_sources,
                )
            finally:
                _ref_stack.discard(ref)
            if isinstance(resolved, Mapping):
                overrides = {k: v for k, v in details.items() if k != "$ref"}
                merged_dict: dict[str, Any] = {}
                merged_dict.update(resolved)
                merged_dict.update(overrides)
                return _parse_attribute_type(
                    merged_dict,
                    source=source,
                    schema_sources=schema_sources,
                    _ref_stack=_ref_stack,
                )

        fmt = details.get("format")
        if isinstance(fmt, str) and fmt:
            type_value = details.get("type")
            if isinstance(type_value, str) and type_value:
                if fmt.lower() != type_value.lower():
                    return f"{fmt} ({type_value})"
            return fmt

        type_value = details.get("type") or details.get("dataType")
        if isinstance(type_value, str) and type_value:
            return type_value
        if isinstance(type_value, Sequence) and not isinstance(type_value, (str, bytes)):
            joined = " | ".join(str(item) for item in type_value if item)
            if joined:
                return joined
    elif details is not None:
        return type(details).__name__
    return "unknown"


def _extract_ogc_role(
    details: Any,
    documents: Sequence[Mapping[str, Any]],
    *,
    current_document: Mapping[str, Any] | None = None,
    _seen_refs: set[tuple[int, str]] | None = None,
) -> Any:
    if isinstance(details, Mapping):
        direct_role = _extract_role_from_mapping(details)
        if direct_role is not None:
            return direct_role

        ref = details.get("$ref")
        if isinstance(ref, str):
            if _seen_refs is None:
                _seen_refs = set()
            for document in _iter_ref_documents(current_document, documents):
                marker = (id(document), ref)
                if marker in _seen_refs:
                    continue
                _seen_refs.add(marker)
                resolved = _resolve_json_pointer(document, ref)
                if isinstance(resolved, Mapping):
                    resolved_role = _extract_ogc_role(
                        resolved,
                        documents,
                        current_document=document,
                        _seen_refs=_seen_refs,
                    )
                    if resolved_role is not None:
                        return resolved_role

        for key in ("allOf", "anyOf", "oneOf"):
            group = details.get(key)
            if isinstance(group, Sequence) and not isinstance(group, (str, bytes)):
                for entry in group:
                    role = _extract_ogc_role(
                        entry,
                        documents,
                        current_document=current_document,
                        _seen_refs=_seen_refs,
                    )
                    if role is not None:
                        return role

    elif isinstance(details, Sequence) and not isinstance(details, (str, bytes)):
        for entry in details:
            role = _extract_ogc_role(
                entry,
                documents,
                current_document=current_document,
                _seen_refs=_seen_refs,
            )
            if role is not None:
                return role

    return None


def _iter_ref_documents(
    current_document: Mapping[str, Any] | None,
    documents: Sequence[Mapping[str, Any]],
) -> Iterable[Mapping[str, Any]]:
    if current_document is not None:
        yield current_document

    for document in documents:
        if document is current_document:
            continue
        yield document


def _extract_role_from_mapping(details: Mapping[str, Any]) -> Any:
    for key, value in details.items():
        if not isinstance(key, str):
            continue
        normalized_key = key.lower().replace("_", "-")
        if normalized_key.endswith("ogc-role") or normalized_key.endswith(
            "ogc-property-role"
        ):
            normalized_value = _normalize_role_value(value)
            if normalized_value is not None:
                return normalized_value
    return None


def _normalize_role_value(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        normalized_list = [
            item.strip()
            for item in value
            if isinstance(item, str) and item.strip()
        ]
        if normalized_list:
            return normalized_list
    return None


def _resolve_json_pointer(document: Mapping[str, Any], pointer: str) -> Any:
    if not isinstance(pointer, str) or not pointer.startswith("#"):
        return None

    if pointer == "#":
        return document

    if not pointer.startswith("#/"):
        return None

    parts = pointer[2:].split("/")
    current: Any = document
    for raw_part in parts:
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            current = current.get(part)
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            try:
                index = int(part)
            except ValueError:
                return None
            if index < 0 or index >= len(current):
                return None
            current = current[index]
        else:
            return None
        if current is None:
            return None

    return current


def _cli(collections_url: str, output: Path | None = None, http_get: HTTPGet | None = None) -> None:
    feature_types = load_feature_types(collections_url, http_get=http_get)
    serialized = json.dumps(feature_types, indent=2, ensure_ascii=False)

    if output:
        output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized)


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Fetch OGC API collections metadata and extract feature types."
    )
    parser.add_argument(
        "collections_url",
        help="URL to an OGC API - Features /collections endpoint",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Optional path to write the resulting feature type list as JSON.",
    )

    args = parser.parse_args(argv)
    _cli(args.collections_url, args.output)


if __name__ == "__main__":  # pragma: no cover - CLI integration
    main()
