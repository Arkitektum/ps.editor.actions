"""Write an ODCS (Open Data Contract Standard) v3.1.0 data contract from a feature
catalogue.

Mirrors the ``geopackage``/``shapechange`` emitters: takes the assembled feature-type
dicts and writes a validated YAML contract. The feature-catalogue -> ODCS mapping is
documented in ``ps.editor.web/docs/odcs-mapping.md``:

* objekttype        -> ``schema[]`` (logicalType ``object``, physicalType ``table``)
* attributt         -> ``properties[]`` (logicalType from the type; ``required`` from
                       cardinality; ``primaryKey`` from ``ogcRole: id``)
* kodeliste (enum)  -> ``logicalTypeOptions.pattern`` + ``customProperties.allowedValues``
* ekstern kodeliste -> ``authoritativeDefinitions``
* geometri          -> ``logicalType: object`` + geometri/CRS i ``customProperties``
* arv               -> materialiserte attributter + ``customProperties.inheritsFrom``

ODCS v3.1.0 has no native enum, geometry or CRS, so those go into ``customProperties``
(strict validation rejects ad-hoc top-level keys).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

API_VERSION = "v3.1.0"

# Feature-catalogue attribute type -> ODCS logicalType (the portable type enum).
_LOGICAL_TYPE = {
    "integer": "integer",
    "int": "integer",
    "long": "integer",
    "string": "string",
    "characterstring": "string",
    "text": "string",
    "number": "number",
    "real": "number",
    "double": "number",
    "float": "number",
    "decimal": "number",
    "boolean": "boolean",
    "bool": "boolean",
    "date": "date",
    "datetime": "timestamp",
    "date-time": "timestamp",
    "timestamp": "timestamp",
    "time": "time",
}

# Feature-catalogue geometry type -> readable geometry name (customProperties.geometryType).
_GEOM_NAME = {
    "geometry-point": "Point",
    "geometry-multipoint": "MultiPoint",
    "geometry-line": "LineString",
    "geometry-multiline": "MultiLineString",
    "geometry-polygon": "Polygon",
    "geometry-multipolygon": "MultiPolygon",
    "geometry": "Geometry",
    "gm_point": "Point",
    "gm_multipoint": "MultiPoint",
    "gm_curve": "LineString",
    "gm_linestring": "LineString",
    "gm_multicurve": "MultiLineString",
    "gm_surface": "Surface",
    "gm_polygon": "Polygon",
    "gm_multisurface": "MultiSurface",
    "gm_object": "Geometry",
}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _logical_type(raw: Any) -> str:
    return _LOGICAL_TYPE.get(_text(raw).lower(), "string")


def _is_geometry(raw: Any) -> bool:
    key = _text(raw).lower()
    return key.startswith("gm_") or key.startswith("geometry")


def _geometry_name(raw: Any) -> str:
    return _GEOM_NAME.get(_text(raw).lower(), "Geometry")


def _epsg_code(crs: Any) -> int | None:
    if isinstance(crs, (list, tuple)):
        for candidate in crs:
            code = _epsg_code(candidate)
            if code is not None:
                return code
        return None
    if not isinstance(crs, str):
        return None
    match = re.search(r"epsg[:/](?:0/)?(\d+)", crs, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")


def _physical_name(name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_") or "table"


def _is_multi(cardinality: str) -> bool:
    return cardinality.endswith("*") or cardinality.endswith("n") or cardinality.endswith("N")


def _is_required(cardinality: str) -> bool:
    return cardinality == "1" or cardinality.startswith("1..") or cardinality.startswith("1.")


def _effective_attributes(
    ft: dict[str, Any],
    by_name: dict[str, dict[str, Any]],
    _seen: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Attributes for a feature type INCLUDING those inherited from supertypes.

    ODCS has no inheritance, so supertype attributes are materialised into the
    concrete schema object (supertype fields first), deduplicated by name.
    """
    if _seen is None:
        _seen = set()
    attrs: list[dict[str, Any]] = []
    relationships = ft.get("relationships")
    parents = (
        relationships.get("inheritance", []) if isinstance(relationships, dict) else []
    )
    for parent_name in parents:
        if not isinstance(parent_name, str) or parent_name in _seen:
            continue
        _seen.add(parent_name)
        parent = by_name.get(parent_name)
        if parent:
            attrs.extend(_effective_attributes(parent, by_name, _seen))
    attrs.extend(a for a in (ft.get("attributes") or []) if isinstance(a, dict))

    seen_names: set[str] = set()
    result: list[dict[str, Any]] = []
    for attribute in attrs:
        name = attribute.get("name")
        if isinstance(name, str) and name:
            if name in seen_names:
                continue
            seen_names.add(name)
        result.append(attribute)
    return result


def _value_shape(attr: dict[str, Any]) -> dict[str, Any]:
    """The ODCS fields describing ONE value (logicalType + options), no name/required."""
    nested = attr.get("attributes")
    if isinstance(nested, list) and nested:
        properties = [p for a in nested if isinstance(a, dict) and (p := _property(a))]
        return {"logicalType": "object", "properties": properties}

    shape: dict[str, Any] = {"logicalType": _logical_type(attr.get("type"))}
    value_domain = attr.get("valueDomain")
    if isinstance(value_domain, dict):
        listed = value_domain.get("listedValues")
        code_list = value_domain.get("codeList")
        if isinstance(listed, list) and listed:
            values = [str(v.get("value")) for v in listed if isinstance(v, dict) and v.get("value") is not None]
            if values:
                shape["logicalTypeOptions"] = {
                    "pattern": "^(" + "|".join(re.escape(v) for v in values) + ")$"
                }
            allowed = [
                {"value": str(v.get("value")), "label": _text(v.get("label"))}
                for v in listed
                if isinstance(v, dict) and v.get("value") is not None
            ]
            shape["customProperties"] = [{"property": "allowedValues", "value": allowed}]
        elif isinstance(code_list, str) and code_list.strip():
            shape["authoritativeDefinitions"] = [
                {"url": code_list.strip(), "type": "businessDefinition"}
            ]
            custom = [{"property": "sosiCodeList", "value": _text(attr.get("type"))}]
            as_dict = _text(value_domain.get("asDictionary"))
            if as_dict:
                custom.append({"property": "asDictionary", "value": as_dict})
            shape["customProperties"] = custom
    return shape


def _geometry_property(name: str, geometry: Any) -> dict[str, Any]:
    if isinstance(geometry, dict):
        gname = _geometry_name(geometry.get("type"))
        epsg = _epsg_code(geometry.get("storageCrs")) or _epsg_code(geometry.get("crs"))
    else:
        gname, epsg = "Geometry", None
    physical = f"geometry({gname},{epsg})" if epsg else f"geometry({gname})"
    custom = [{"property": "geometryType", "value": gname}]
    if epsg:
        custom.append({"property": "crs", "value": f"EPSG:{epsg}"})
    return {
        "name": name or "geometri",
        "logicalType": "object",
        "physicalType": physical,
        "required": True,
        "customProperties": custom,
    }


def _property(attr: dict[str, Any]) -> dict[str, Any] | None:
    name = attr.get("name")
    if not isinstance(name, str) or not name:
        return None
    if _is_geometry(attr.get("type")):
        return _geometry_property(name, {"type": attr.get("type")})

    cardinality = _text(attr.get("cardinality"))
    shape = _value_shape(attr)
    description = _text(attr.get("description"))

    if _is_multi(cardinality):
        prop: dict[str, Any] = {"name": name, "logicalType": "array", "items": shape}
    else:
        prop = {"name": name, **shape}
    if description:
        prop["description"] = description
    if _is_required(cardinality):
        prop["required"] = True
    if attr.get("ogcRole") == "id":
        prop["primaryKey"] = True
    return prop


def _schema_object(
    ft: dict[str, Any], by_name: dict[str, dict[str, Any]], model_uri: str | None
) -> dict[str, Any]:
    name = ft["name"]
    obj: dict[str, Any] = {
        "name": name,
        "physicalName": _physical_name(name),
        "logicalType": "object",
        "physicalType": "table",
    }
    description = _text(ft.get("description"))
    if description:
        obj["description"] = description

    custom: list[dict[str, Any]] = [{"property": "sosiObjektType", "value": name}]
    relationships = ft.get("relationships") if isinstance(ft.get("relationships"), dict) else {}
    inheritance = [p for p in (relationships.get("inheritance") or []) if isinstance(p, str)]
    if inheritance:
        custom.append({"property": "inheritsFrom", "value": ",".join(inheritance)})

    geometry = ft.get("geometry")
    epsg = _epsg_code(geometry.get("storageCrs")) or _epsg_code(geometry.get("crs")) if isinstance(geometry, dict) else None
    if epsg:
        custom.append({"property": "defaultCrs", "value": f"EPSG:{epsg}"})

    associations = [
        {
            "role": _text(a.get("role")),
            "target": _text(a.get("target")),
            "cardinality": _text(a.get("cardinality")),
        }
        for a in (relationships.get("associations") or [])
        if isinstance(a, dict) and _text(a.get("target")) and _text(a.get("target")) in by_name
    ]
    if associations:
        custom.append({"property": "associations", "value": associations})
    obj["customProperties"] = custom

    if model_uri:
        obj["authoritativeDefinitions"] = [
            {"url": f"{model_uri}#{name}", "type": "semanticModel"}
        ]

    properties: list[dict[str, Any]] = []
    if isinstance(geometry, dict):
        properties.append(_geometry_property(_text(geometry.get("name")) or "geometri", geometry))
    for attr in _effective_attributes(ft, by_name):
        prop = _property(attr)
        if prop:
            properties.append(prop)
    if properties:
        obj["properties"] = properties
    return obj


def build_odcs(
    feature_types: list[dict[str, Any]],
    *,
    identifier: str,
    version: str = "1.0.0",
    status: str = "active",
    name: str | None = None,
    domain: str | None = None,
    tenant: str | None = None,
    model_uri: str | None = None,
    servers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the ODCS v3.1.0 contract as a dict."""
    by_name = {
        ft["name"]: ft
        for ft in feature_types
        if isinstance(ft, dict) and isinstance(ft.get("name"), str)
    }
    schema = [
        _schema_object(ft, by_name, model_uri)
        for ft in feature_types
        if isinstance(ft, dict)
        and isinstance(ft.get("name"), str)
        and ft.get("name").strip()
        and ft.get("abstract") is not True
    ]

    slug = _slugify(identifier) or "produkt"
    doc: dict[str, Any] = {
        "apiVersion": API_VERSION,
        "kind": "DataContract",
        "id": f"urn:odcs:{slug}:{version}",
        "version": version,
        "status": status,
    }
    if name or identifier:
        doc["name"] = name or identifier
    if domain:
        doc["domain"] = domain
    if tenant:
        doc["tenant"] = tenant
    if model_uri:
        doc["description"] = {
            "authoritativeDefinitions": [{"url": model_uri, "type": "semanticModel"}]
        }
    if servers:
        doc["servers"] = servers
    if schema:
        doc["schema"] = schema
    return doc


def write_odcs(
    feature_types: list[dict[str, Any]],
    path: str | Path,
    *,
    identifier: str,
    version: str = "1.0.0",
    status: str = "active",
    name: str | None = None,
    domain: str | None = None,
    tenant: str | None = None,
    model_uri: str | None = None,
    servers: list[dict[str, Any]] | None = None,
) -> Path:
    """Write an ODCS v3.1.0 data contract (YAML) for ``feature_types`` to ``path``."""
    doc = build_odcs(
        feature_types,
        identifier=identifier,
        version=version,
        status=status,
        name=name,
        domain=domain,
        tenant=tenant,
        model_uri=model_uri,
        servers=servers,
    )
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, default_flow_style=False)
    out.write_text(
        "# Open Data Contract Standard (ODCS) v3.1.0 — generert fra datamodellen.\n" + body,
        encoding="utf-8",
    )
    return out
