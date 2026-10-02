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

The psdata document describes the product around that schema, and is mapped onto
the contract's top level:

* purpose              -> ``description.purpose``
* lisens/begrensninger -> ``description.usage``/``.limitations``, ``price``, ``tags``
* nøkkelord + tema     -> ``tags``
* leveranser           -> ``servers`` (``type: api`` med endepunktet som ``location``)
* uniqueId, metadata-
  lenke, produktark,
  tegneregler          -> ``authoritativeDefinitions``
* kontakter            -> ``team`` og ``support``
* oppdateringsfrekvens -> ``slaProperties``
* målestokk, språk,
  representasjonstype  -> ``customProperties``
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


# ---------------------------------------------------------------------------
# psdata -> contract metadata
#
# The product specification already fetches the dataset metadata from Geonorge
# and keeps it in the psdata document. Everything below carries those values
# into the contract, so the contract describes the product and not only its
# schema. No value is invented: a missing source simply leaves the field out.
# ---------------------------------------------------------------------------

# Access constraints that mean the data is free to obtain. Used only to decide
# whether a zero price can be stated; anything unrecognised leaves price unset.
_OPEN_ACCESS = {"åpne data", "apne data", "open data", "no limitations", "ingen"}


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _strings(value: Any) -> list[str]:
    """``value`` as a list of non-empty strings, whether it holds one or many."""
    if isinstance(value, (list, tuple)):
        return [text for item in value if (text := _text(item))]
    text = _text(value)
    return [text] if text else []


def _unique(values: list[str]) -> list[str]:
    """Order-preserving, case-insensitive deduplication."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _constraint_values(constraints: Any) -> dict[str, str]:
    """Flatten the psdata restriction block into the values we map.

    ``identificationSection.restriction`` groups them the way ISO 19115 does:
    ``resourceConstraints``, ``legalConstraints`` and ``securityConstraints``.
    """
    if not isinstance(constraints, dict):
        return {}
    values: dict[str, str] = {}
    for group in ("resourceConstraints", "legalConstraints", "securityConstraints"):
        block = constraints.get(group)
        if isinstance(block, dict):
            for key, value in block.items():
                text = _text(value)
                if text:
                    values[key] = text
    return values


def _contract_terms(constraints: Any) -> dict[str, Any]:
    """Map the licence and constraint values onto ODCS v3.1.0 fields.

    The values are already fetched for the product specification, so carrying
    them into the contract costs nothing. ODCS has no licence field, so the raw
    values are kept in ``customProperties`` alongside the prose summaries -- a
    reader gets the summary, a machine gets the exact value.
    """
    values = _constraint_values(constraints)
    if not values:
        return {}

    terms: dict[str, Any] = {}

    description: dict[str, str] = {}
    usage = values.get("useLimitations")
    if usage:
        description["usage"] = usage
    limitations = [
        values[key]
        for key in ("accessConstraints", "useConstraints", "classification")
        if values.get(key)
    ]
    if limitations:
        description["limitations"] = ". ".join(limitations)
    if description:
        terms["description"] = description

    tags = [
        values[key]
        for key in ("accessConstraints", "classification")
        if values.get(key)
    ]
    if tags:
        terms["tags"] = tags

    # Stating a price is an inference, so it is only made when the access
    # constraint says plainly that the data is open.
    access = values.get("accessConstraints", "").strip().casefold()
    if access in _OPEN_ACCESS:
        terms["price"] = {"priceAmount": 0, "priceUnit": "dataset"}

    custom = [
        {"property": key, "value": values[key]}
        for key in (
            "license",
            "licenseUrl",
            "accessConstraints",
            "useConstraints",
            "useLimitations",
            "classification",
        )
        if values.get(key)
    ]
    if custom:
        terms["customProperties"] = custom

    return terms


def _server_name(label: str, qualifiers: list[str], taken: set[str]) -> str:
    """A stable, unique identifier for a delivery channel.

    Several deliveries share a channel name -- Geonorge publishes one Atom feed
    per format -- so the format qualifies the name before a counter has to.
    """
    base = _slugify(label) or "leveranse"
    for candidate in [base] + [f"{base}-{_slugify(q)}" for q in qualifiers if _slugify(q)]:
        if candidate not in taken:
            taken.add(candidate)
            return candidate
    counter = 2
    while f"{base}-{counter}" in taken:
        counter += 1
    taken.add(f"{base}-{counter}")
    return f"{base}-{counter}"


def _contract_servers(deliveries: Any) -> list[dict[str, Any]]:
    """``deliverySection`` -> ODCS ``servers``.

    ODCS only knows typed servers. A Geonorge delivery is always an HTTP
    endpoint -- a download API, a WMS, an Atom feed -- so ``api`` with the
    endpoint as ``location`` is the closest honest fit. Deliveries without an
    endpoint (a file handed over by other means) have nothing to point at and
    are left out.
    """
    servers: list[dict[str, Any]] = []
    taken: set[str] = set()
    seen_locations: set[str] = set()
    for entry in deliveries if isinstance(deliveries, list) else []:
        delivery = _mapping(_mapping(entry).get("delivery"))
        medium = _mapping(delivery.get("deliveryMedium"))
        endpoint = _text(_mapping(medium.get("deliveryService")).get("serviceEndpoint"))
        if not endpoint.lower().startswith(("http://", "https://")):
            continue
        if endpoint in seen_locations:
            continue
        seen_locations.add(endpoint)

        label = _text(medium.get("deliveryMediumName"))
        formats = _unique(
            [
                _text(fmt.get("formatName"))
                for fmt in (delivery.get("deliveryFormat") or [])
                if isinstance(fmt, dict) and _text(fmt.get("formatName"))
            ]
        )
        server: dict[str, Any] = {
            "server": _server_name(label or endpoint, formats, taken),
            "type": "api",
            "location": endpoint,
        }
        parts = [part for part in (label, ", ".join(formats)) if part]
        if parts:
            server["description"] = " - ".join(parts)
        servers.append(server)
    return servers


def _contract_definitions(psdata: dict[str, Any], model_uri: str | None) -> list[dict[str, str]]:
    """The links psdata carries, as ODCS ``authoritativeDefinitions``.

    ODCS names five standard ``type`` values; ``businessDefinition`` covers the
    links that say what the product *is*, ``implementation`` the ones that say
    how it is rendered. The ``description`` keeps them apart for a reader.
    """
    identification = _mapping(psdata.get("identificationSection"))
    metadata_identifier = _mapping(
        _mapping(psdata.get("metadataSection")).get("metadataIdentifier")
    )

    candidates: list[tuple[str, str, str]] = []
    if model_uri:
        candidates.append((model_uri, "semanticModel", "Datamodell"))
    candidates.append((_text(identification.get("uniqueId")), "businessDefinition", "Produktets identifikator"))
    candidates.append((_text(metadata_identifier.get("metadataLinkage")), "businessDefinition", "Metadata i Kartkatalogen"))
    for reference in psdata.get("additionalReferences") or []:
        if isinstance(reference, dict):
            candidates.append(
                (_text(reference.get("href")), "businessDefinition", _text(reference.get("title")))
            )
    portrayal = _mapping(psdata.get("portrayal"))
    candidates.append(
        (_text(portrayal.get("linkage")), "implementation", _text(portrayal.get("name")))
    )

    definitions: list[dict[str, str]] = []
    seen: set[str] = set()
    for url, kind, description in candidates:
        # ``uniqueId`` falls back to a bare UUID when the metadata has no
        # namespace, and that is not something a reader can follow.
        if not url.lower().startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        definition = {"url": url, "type": kind}
        if description:
            definition["description"] = description
        definitions.append(definition)
    return definitions


def _contract_team(contacts: Any) -> dict[str, Any]:
    """``identificationSection.contact`` -> the ODCS ``team`` object.

    ``username`` is required, and the e-mail address is the only identifier
    Geonorge gives, so a contact without one cannot become a member. The same
    address usually appears under several roles (owner, pointOfContact,
    publisher); that is one member holding several roles, not several members.
    """
    members: dict[str, dict[str, Any]] = {}
    roles: dict[str, list[str]] = {}
    for contact in contacts if isinstance(contacts, list) else []:
        if not isinstance(contact, dict):
            continue
        email = _text(contact.get("electronicMailAddress"))
        if not email:
            continue
        key = email.casefold()
        if key not in members:
            member: dict[str, Any] = {"username": email}
            name = _text(contact.get("individualName"))
            organization = _text(contact.get("organizationName"))
            if name or organization:
                member["name"] = name or organization
            if organization and organization != member.get("name"):
                member["description"] = organization
            members[key] = member
            roles[key] = []
        role = _text(contact.get("role"))
        if role and role not in roles[key]:
            roles[key].append(role)

    for key, member in members.items():
        if roles[key]:
            member["role"] = ", ".join(roles[key])
    return {"members": list(members.values())} if members else {}


def _contract_support(contacts: Any) -> list[dict[str, Any]]:
    """The first contact that can be reached, as an ODCS support channel."""
    for contact in contacts if isinstance(contacts, list) else []:
        if not isinstance(contact, dict):
            continue
        email = _text(contact.get("electronicMailAddress"))
        if not email:
            continue
        organization = _text(contact.get("organizationName"))
        item: dict[str, Any] = {
            "channel": organization or email,
            "tool": "email",
            "url": f"mailto:{email}",
            "scope": "issues",
        }
        if organization:
            item["description"] = f"Kontaktpunkt hos {organization}"
        return [item]
    return []


def _contract_sla(maintenance: dict[str, Any]) -> list[dict[str, Any]]:
    """``maintenanceSection`` -> ODCS ``slaProperties``.

    The update frequency is the one service level Geonorge states, and it is
    stated in words ("Årlig"), not as a duration, so it is passed through as-is.
    """
    frequency = _text(maintenance.get("maintenanceAndUpdateFrequency"))
    if not frequency:
        return []
    item: dict[str, Any] = {
        "property": "frequency",
        "value": frequency,
        "driver": "operational",
    }
    statement = _text(maintenance.get("maintenanceAndUpdateStatement"))
    if statement:
        item["description"] = statement
    return [item]


def _contract_metadata(psdata: Any, model_uri: str | None) -> dict[str, Any]:
    """Everything the psdata document contributes to the contract's top level."""
    psdata = _mapping(psdata)
    identification = _mapping(psdata.get("identificationSection"))
    maintenance = _mapping(psdata.get("maintenanceSection"))
    terms = _contract_terms(identification.get("restriction"))

    meta: dict[str, Any] = {}

    description = dict(terms.get("description") or {})
    purpose = _text(_mapping(identification.get("purpose")).get("summary"))
    if purpose:
        description["purpose"] = purpose
    if description:
        meta["description"] = description

    tags = _unique(
        _strings(identification.get("topicCategory"))
        + _strings(identification.get("keyword"))
        + list(terms.get("tags") or [])
    )
    if tags:
        meta["tags"] = tags

    if terms.get("price"):
        meta["price"] = terms["price"]

    definitions = _contract_definitions(psdata, model_uri)
    if definitions:
        meta["authoritativeDefinitions"] = definitions

    servers = _contract_servers(psdata.get("deliverySection"))
    if servers:
        meta["servers"] = servers

    team = _contract_team(identification.get("contact"))
    if team:
        meta["team"] = team

    support = _contract_support(identification.get("contact"))
    if support:
        meta["support"] = support

    sla = _contract_sla(maintenance)
    if sla:
        meta["slaProperties"] = sla

    # Values ODCS has no field for, but that a consumer of the data needs.
    extras = [
        ("equivalentScale", _text(_mapping(identification.get("spatialResolution")).get("equivalentScale"))),
        ("spatialRepresentationType", _text(identification.get("spatialRepresentationType"))),
        ("language", _text(identification.get("language")) or _text(psdata.get("language"))),
        ("maintenanceAndUpdateStatement", _text(maintenance.get("maintenanceAndUpdateStatement"))),
    ]
    custom = list(terms.get("customProperties") or [])
    custom += [{"property": key, "value": value} for key, value in extras if value]
    if custom:
        meta["customProperties"] = custom

    return meta


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
    psdata: Any = None,
) -> dict[str, Any]:
    """Build the ODCS v3.1.0 contract as a dict.

    ``psdata`` is the product-specification document built from the Geonorge
    metadata. The values it already holds -- purpose, keywords, licence,
    contacts, deliveries, update frequency -- are mapped onto the ODCS fields
    that carry the same meaning, so the contract is not schema-only.

    ``servers`` overrides the servers derived from ``psdata.deliverySection``.
    """
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

    meta = _contract_metadata(psdata, model_uri)
    # Ordered the way ODCS documents are usually read: what the product is,
    # then what it costs and who stands behind it, then where to get it.
    for key in (
        "tags",
        "price",
        "description",
        "authoritativeDefinitions",
        "team",
        "support",
        "slaProperties",
        "customProperties",
    ):
        if meta.get(key):
            doc[key] = meta[key]

    if servers or meta.get("servers"):
        doc["servers"] = servers or meta["servers"]
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
    psdata: Any = None,
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
        psdata=psdata,
    )
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, default_flow_style=False)
    out.write_text(
        "# Open Data Contract Standard (ODCS) v3.1.0 — generert fra datamodellen.\n" + body,
        encoding="utf-8",
    )
    return out
