"""Write an empty GeoPackage that materialises a feature catalogue's data model.

Given the assembled feature-type dicts (the same structure produced by the OGC/XMI/
GeoPackage loaders), this writes a valid, **empty** GeoPackage (structure only, no
data rows) using the standard-library :mod:`sqlite3` — no GDAL dependency.

Two OGC GeoPackage extensions carry the richer semantics:

* **Schema** (``gpkg_schema``): column metadata in ``gpkg_data_columns`` and
  code lists / enumerations in ``gpkg_data_column_constraints``.
* **Related Tables** (RTE): associations between feature types via
  ``gpkgext_relations`` + (empty) mapping tables.

This is the inverse of :mod:`geopackage.feature_types` (the reader), so a written
GeoPackage read back with ``load_feature_types_from_geopackage`` reproduces the model.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

# Resolves an external code-list URL to ``[{"value","label"}]`` rows, or None when
# it cannot be resolved (unknown host, network/parse failure). Injected so the
# writer stays offline-safe and testable; production passes _fetch_geonorge_codelist.
CodeListResolver = Callable[[str], "list[dict[str, Any]] | None"]

# GeoPackage header pragmas.
_APPLICATION_ID = 1196444487  # 0x47504B47 == b"GPKG"
_USER_VERSION = 10400  # GeoPackage 1.4.0

# Feature-catalogue attribute type -> SQLite/GeoPackage column type.
_COLUMN_TYPE = {
    "integer": "INTEGER",
    "string": "TEXT",
    "number": "REAL",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "datetime": "DATETIME",
    "binary": "BLOB",
}

# Feature-catalogue geometry `type` (OGC/gpkg style) -> GPKG geometry_type_name.
_GPKG_GEOM = {
    "geometry-point": "POINT",
    "geometry-multipoint": "MULTIPOINT",
    "geometry-line": "LINESTRING",
    "geometry-multiline": "MULTILINESTRING",
    "geometry-polygon": "MULTIPOLYGON",
    "geometry-multipolygon": "MULTIPOLYGON",
    "geometry": "GEOMETRY",
}

# SOSI geometry type short-names (Norwegian) used in SOSI UML models instead of the
# ISO GM_* names, e.g. Havnedata/NRL. Without these the object type would be written as
# an aspatial 'attributes' table instead of a 'features' table.
_SOSI_GEOM = {
    "punkt": "POINT",
    "sverm": "MULTIPOINT",
    "kurve": "LINESTRING",
    "linje": "LINESTRING",
    "bue": "LINESTRING",
    "buep": "LINESTRING",
    "sirkel": "LINESTRING",
    "sirkelbue": "LINESTRING",
    "sammensattkurve": "LINESTRING",
    "flate": "MULTIPOLYGON",
    "trekant": "MULTIPOLYGON",
    "sammensattflate": "MULTIPOLYGON",
}

# ISO 19107 GM_* type (XMI geometry attribute) -> GPKG geometry_type_name.
_GM_GEOM = {
    "gm_point": "POINT",
    "gm_multipoint": "MULTIPOINT",
    "gm_curve": "LINESTRING",
    "gm_linestring": "LINESTRING",
    "gm_multicurve": "MULTILINESTRING",
    "gm_surface": "MULTIPOLYGON",
    "gm_polygon": "MULTIPOLYGON",
    "gm_multisurface": "MULTIPOLYGON",
    "gm_object": "GEOMETRY",
    "gm_primitive": "GEOMETRY",
    "gm_aggregate": "GEOMETRY",
}

_WGS84_WKT = (
    'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,'
    'AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,'
    'AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,'
    'AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG","4326"]]'
)


def _q(identifier: str) -> str:
    """Quote a SQL identifier, escaping embedded double quotes."""
    return '"' + str(identifier).replace('"', '""') + '"'


def _ascii_ident(text: str) -> str:
    """ASCII-trygt identifikatornavn (for interne RTE-mapping-tabeller). Non-ASCII
    som «ø/æ/å» droppes og alt ikke-alfanumerisk blir «_»."""
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", ascii_text).strip("_")
    return slug or "rel"


def _sql_column_type(cat_type: str | None) -> str:
    """Map a feature-catalogue attribute type to a SQLite column type.

    Returns ``""`` for geometry (GM_*) types, which are handled separately.
    """
    value = (cat_type or "").strip()
    low = value.lower()
    if low.startswith("gm_"):
        return ""
    if low in _COLUMN_TYPE:
        return _COLUMN_TYPE[low]
    # XMI/UML primitive type names.
    if low in {"integer", "int"}:
        return "INTEGER"
    if low in {"real", "double", "decimal", "float"}:
        return "REAL"
    if low in {"boolean", "bool"}:
        return "BOOLEAN"
    if low == "date":
        return "DATE"
    if low in {"datetime", "dateandtime", "timestamp"}:
        return "DATETIME"
    if low in {"characterstring", "string", "text", "url", "uri", "charactervalue"}:
        return "TEXT"
    return "TEXT"


def _epsg_code(crs: Any) -> int | None:
    """Extract an EPSG code from a CRS URI or list of CRS URIs."""
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


def _geometry_type_name(ft: dict[str, Any]) -> str | None:
    geometry = ft.get("geometry")
    if isinstance(geometry, dict):
        raw = str(geometry.get("type") or "").strip().lower()
        return _GPKG_GEOM.get(raw, "GEOMETRY")
    return None


# --------------------------------------------------------------------------- #
# Base GeoPackage scaffolding
# --------------------------------------------------------------------------- #

_BASE_DDL = """
CREATE TABLE gpkg_spatial_ref_sys (
  srs_name TEXT NOT NULL, srs_id INTEGER NOT NULL PRIMARY KEY,
  organization TEXT NOT NULL, organization_coordsys_id INTEGER NOT NULL,
  definition TEXT NOT NULL, description TEXT
);
CREATE TABLE gpkg_contents (
  table_name TEXT NOT NULL PRIMARY KEY, data_type TEXT NOT NULL,
  identifier TEXT UNIQUE, description TEXT DEFAULT '',
  last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE, srs_id INTEGER
);
CREATE TABLE gpkg_geometry_columns (
  table_name TEXT NOT NULL, column_name TEXT NOT NULL,
  geometry_type_name TEXT NOT NULL, srs_id INTEGER NOT NULL,
  z TINYINT NOT NULL, m TINYINT NOT NULL,
  CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name),
  CONSTRAINT uk_gc_table_name UNIQUE (table_name)
);
CREATE TABLE gpkg_extensions (
  table_name TEXT, column_name TEXT, extension_name TEXT NOT NULL,
  definition TEXT NOT NULL, scope TEXT NOT NULL,
  CONSTRAINT ge_tce UNIQUE (table_name, column_name, extension_name)
);
CREATE TABLE gpkg_data_columns (
  table_name TEXT NOT NULL, column_name TEXT NOT NULL, name TEXT, title TEXT,
  description TEXT, mime_type TEXT, constraint_name TEXT,
  CONSTRAINT pk_gdc PRIMARY KEY (table_name, column_name)
);
CREATE TABLE gpkg_data_column_constraints (
  constraint_name TEXT NOT NULL, constraint_type TEXT NOT NULL, value TEXT,
  min NUMERIC, min_is_inclusive BOOLEAN, max NUMERIC, max_is_inclusive BOOLEAN,
  description TEXT,
  CONSTRAINT gdcc_ntv UNIQUE (constraint_name, constraint_type, value)
);
CREATE TABLE gpkgext_relations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  base_table_name TEXT NOT NULL, base_primary_column TEXT NOT NULL DEFAULT 'id',
  related_table_name TEXT NOT NULL, related_primary_column TEXT NOT NULL DEFAULT 'id',
  relation_name TEXT NOT NULL, mapping_table_name TEXT NOT NULL UNIQUE
);
"""

_SCHEMA_EXTENSION = "gpkg_schema"
_SCHEMA_DEFINITION = "http://www.geopackage.org/spec/#extension_schema"
_RTE_EXTENSION = "related_tables"
_RTE_DEFINITION = "http://www.geopackage.org/spec/related-tables/"


def _init_base(connection: sqlite3.Connection) -> None:
    connection.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
    connection.execute(f"PRAGMA user_version = {_USER_VERSION}")
    connection.executescript(_BASE_DDL)
    connection.executemany(
        "INSERT INTO gpkg_spatial_ref_sys "
        "(srs_name, srs_id, organization, organization_coordsys_id, definition) "
        "VALUES (?,?,?,?,?)",
        [
            ("Undefined cartesian SRS", -1, "NONE", -1, "undefined"),
            ("Undefined geographic SRS", 0, "NONE", 0, "undefined"),
            ("WGS 84 geodetic", 4326, "EPSG", 4326, _WGS84_WKT),
        ],
    )
    # Register the Schema extension (two metadata tables).
    connection.executemany(
        "INSERT INTO gpkg_extensions (table_name, column_name, extension_name, definition, scope) "
        "VALUES (?, NULL, ?, ?, 'read-write')",
        [
            ("gpkg_data_columns", _SCHEMA_EXTENSION, _SCHEMA_DEFINITION),
            ("gpkg_data_column_constraints", _SCHEMA_EXTENSION, _SCHEMA_DEFINITION),
        ],
    )


def _ensure_srs(connection: sqlite3.Connection, srs_id: int, seen: set[int]) -> None:
    if srs_id in seen:
        return
    seen.add(srs_id)
    exists = connection.execute(
        "SELECT 1 FROM gpkg_spatial_ref_sys WHERE srs_id = ?", (srs_id,)
    ).fetchone()
    if exists:
        return
    connection.execute(
        "INSERT INTO gpkg_spatial_ref_sys "
        "(srs_name, srs_id, organization, organization_coordsys_id, definition) "
        "VALUES (?,?,?,?,?)",
        (f"EPSG:{srs_id}", srs_id, "EPSG", srs_id, "undefined"),
    )


# --------------------------------------------------------------------------- #
# Column collection (with flattening of nested/complex attributes)
# --------------------------------------------------------------------------- #


def _effective_attributes(
    ft: dict[str, Any],
    by_name: dict[str, dict[str, Any]],
    _seen: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Attributter for en objekttype INKLUDERT arvede fra supertyper.

    XMI-modeller legger felles felt i abstrakte supertyper som subtypene arver via
    `relationships.inheritance`. Vi materialiserer disse inn i den konkrete tabellen
    (supertypens felt først, deretter egne), ellers mangler feltene i GeoPackagen.
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
    attrs.extend(ft.get("attributes") or [])

    # Dedupliser på navn (behold første forekomst = supertypens rekkefølge først).
    seen_names: set[str] = set()
    result: list[dict[str, Any]] = []
    for attribute in attrs:
        name = attribute.get("name") if isinstance(attribute, dict) else None
        if isinstance(name, str) and name:
            if name in seen_names:
                continue
            seen_names.add(name)
        result.append(attribute)
    return result


def _collect_columns(
    attributes: list[dict[str, Any]],
    *,
    prefix: str = "",
) -> list[dict[str, Any]]:
    """Flatten attributes into column descriptors.

    Each descriptor: {name, sql_type, notnull, is_pk, is_geometry, gm_type,
    title, description, value_domain}.
    """
    columns: list[dict[str, Any]] = []
    for attribute in attributes:
        if not isinstance(attribute, dict):
            continue
        name = attribute.get("name")
        if not isinstance(name, str) or not name:
            continue
        col_name = f"{prefix}{name}"

        nested = attribute.get("attributes")
        if isinstance(nested, list) and nested:
            columns.extend(_collect_columns(nested, prefix=f"{col_name}_"))
            continue

        sql_type = _sql_column_type(attribute.get("type"))
        raw_type = str(attribute.get("type") or "").strip().lower()
        is_geometry = raw_type.startswith("gm_") or raw_type in _SOSI_GEOM
        columns.append(
            {
                "name": col_name,
                "sql_type": sql_type,
                "is_geometry": is_geometry,
                "gm_type": _GM_GEOM.get(raw_type) or _SOSI_GEOM.get(raw_type) or "GEOMETRY",
                "notnull": str(attribute.get("cardinality") or "").strip() == "1",
                "is_pk": attribute.get("ogcRole") == "id",
                "title": attribute.get("name"),
                "description": attribute.get("description"),
                "value_domain": attribute.get("valueDomain"),
            }
        )
    return columns


# --------------------------------------------------------------------------- #
# Per feature-type table + Schema-extension metadata
# --------------------------------------------------------------------------- #


def _write_feature_type(
    connection: sqlite3.Connection,
    ft: dict[str, Any],
    *,
    default_crs: int,
    srs_seen: set[int],
    schema_used: dict[str, bool],
    by_name: dict[str, dict[str, Any]],
    codelist_resolver: CodeListResolver | None = None,
) -> str | None:
    """Create one table for a feature type. Returns the table name (or None)."""
    name = ft.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    if ft.get("abstract") is True:
        return None  # abstract types are not materialised as tables
    table = name.strip()

    # Inkluder arvede attributter fra (evt. abstrakte) supertyper.
    columns = _collect_columns(_effective_attributes(ft, by_name))

    # Determine the geometry column: explicit geometry dict, else a GM_* attribute.
    geom_type = _geometry_type_name(ft)
    geom_column = "geom" if geom_type else None
    srs_id = default_crs
    if geom_type:
        code = _epsg_code((ft.get("geometry") or {}).get("storageCrs")) or _epsg_code(
            (ft.get("geometry") or {}).get("crs")
        )
        srs_id = code or default_crs
    else:
        for column in columns:
            if column["is_geometry"]:
                geom_column = column["name"]
                geom_type = column["gm_type"]
                break
    # En feature-tabell har ÉN geometrikolonne. Ekskluder alle geometri-attributter
    # (ikke bare den valgte) — SOSI-typer har ofte både f.eks. Flate og Punkt.
    data_columns = [c for c in columns if not c["is_geometry"] and c["name"] != geom_column]

    # Build column DDL. Ensure an integer primary key.
    pk_column = next((c for c in data_columns if c["is_pk"]), None)
    ddl_parts: list[str] = []
    if pk_column is None:
        ddl_parts.append("fid INTEGER PRIMARY KEY AUTOINCREMENT")
    for column in data_columns:
        piece = f"{_q(column['name'])} "
        if column is pk_column:
            piece += "INTEGER PRIMARY KEY"
        else:
            piece += column["sql_type"] or "TEXT"
            if column["notnull"]:
                piece += " NOT NULL"
        ddl_parts.append(piece)
    if geom_column:
        ddl_parts.append(f"{_q(geom_column)} {geom_type}")

    connection.execute(f"CREATE TABLE {_q(table)} ({', '.join(ddl_parts)})")

    if geom_type:
        _ensure_srs(connection, srs_id, srs_seen)
        connection.execute(
            "INSERT INTO gpkg_contents "
            "(table_name, data_type, identifier, description, srs_id) VALUES (?,?,?,?,?)",
            (table, "features", name, ft.get("description") or "", srs_id),
        )
        connection.execute(
            "INSERT INTO gpkg_geometry_columns "
            "(table_name, column_name, geometry_type_name, srs_id, z, m) VALUES (?,?,?,?,0,0)",
            (table, geom_column, geom_type, srs_id),
        )
    else:
        connection.execute(
            "INSERT INTO gpkg_contents "
            "(table_name, data_type, identifier, description, srs_id) VALUES (?,?,?,?,?)",
            (table, "attributes", name, ft.get("description") or "", None),
        )

    # Schema extension: column metadata + code-list constraints.
    for column in data_columns:
        value_domain = column["value_domain"]
        constraint_name = None
        description = column["description"]

        if isinstance(value_domain, dict):
            listed = value_domain.get("listedValues")
            if isinstance(listed, list) and listed:
                constraint_name = f"{table}_{column['name']}"
                _write_enum_constraint(connection, constraint_name, listed)
            elif value_domain.get("codeList"):
                code_list = value_domain.get("codeList")
                # Resolve the external code list to enum constraint rows when a
                # resolver is available (e.g. Geonorge SOSI-registeret); otherwise,
                # or on failure, fall back to referencing the URL only.
                resolved = codelist_resolver(code_list) if codelist_resolver else None
                if resolved:
                    constraint_name = f"{table}_{column['name']}"
                    _write_enum_constraint(connection, constraint_name, resolved)
                # Keep the source URL in the description for provenance either way.
                description = (
                    f"{description}\n\nKodeliste: {code_list}"
                    if description
                    else f"Kodeliste: {code_list}"
                )

        if description or column["title"] or constraint_name:
            connection.execute(
                "INSERT INTO gpkg_data_columns "
                "(table_name, column_name, name, title, description, mime_type, constraint_name) "
                "VALUES (?,?,?,?,?,NULL,?)",
                (table, column["name"], column["name"], column["title"], description, constraint_name),
            )
            schema_used["used"] = True

    return table


def _write_enum_constraint(
    connection: sqlite3.Connection, constraint_name: str, listed_values: list[dict[str, Any]]
) -> None:
    rows = []
    seen: set[str] = set()
    for item in listed_values:
        if not isinstance(item, dict):
            continue
        value = item.get("value")
        if value is None or str(value) in seen:
            continue
        seen.add(str(value))
        rows.append((constraint_name, "enum", str(value), item.get("label")))
    if rows:
        connection.executemany(
            "INSERT INTO gpkg_data_column_constraints "
            "(constraint_name, constraint_type, value, min, min_is_inclusive, max, "
            "max_is_inclusive, description) VALUES (?,?,?,NULL,NULL,NULL,NULL,?)",
            rows,
        )


# --------------------------------------------------------------------------- #
# External code-list resolution (Geonorge SOSI register)
# --------------------------------------------------------------------------- #

# Statuses (Geonorge `status`) that mean a code is no longer a valid choice; such
# codes are left out of the enum so it lists only currently valid values.
_RETIRED_STATUSES = {"utgått", "utgatt", "erstattet", "tilbaketrukket", "ugyldig"}


def _geonorge_api_url(url: str) -> str | None:
    """Turn a public Geonorge register URL into its JSON API URL.

    ``https://register.geonorge.no/sosi-kodelister/bygningstype`` ->
    ``https://register.geonorge.no/api/sosi-kodelister/bygningstype``. Returns None
    for URLs that are not Geonorge register URLs (nothing to resolve).
    """
    text = str(url or "").strip()
    if "register.geonorge.no" not in text:
        return None
    if "/api/" in text:
        return text
    return text.replace("register.geonorge.no/", "register.geonorge.no/api/", 1)


def _parse_geonorge_codelist(data: Any) -> list[dict[str, str]]:
    """Extract ``[{value,label}]`` from a Geonorge register JSON payload.

    Codes live in ``containeditems`` with ``codevalue`` + ``label``; retired codes
    are skipped and duplicates de-duplicated.
    """
    items = data.get("containeditems") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        value = item.get("codevalue")
        if value is None or str(value) == "":
            continue
        value = str(value)
        if value in seen:
            continue
        if str(item.get("status") or "").strip().lower() in _RETIRED_STATUSES:
            continue
        seen.add(value)
        result.append({"value": value, "label": item.get("label") or ""})
    return result


def _fetch_geonorge_codelist(
    url: str, *, timeout: float = 15.0
) -> list[dict[str, str]] | None:
    """Fetch a Geonorge SOSI code list and return its valid codes as ``[{value,label}]``.

    Best effort: any network/parse failure (or a non-Geonorge URL) returns None so
    an unresolvable code list never fails GeoPackage generation -- the URL still
    lands in the column description, as before.
    """
    api = _geonorge_api_url(url)
    if not api:
        return None
    request = urllib.request.Request(api, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return _parse_geonorge_codelist(data) or None


def _memoize_resolver(resolver: CodeListResolver) -> CodeListResolver:
    """Cache resolver results per URL so a code list shared by many attributes is
    fetched only once per GeoPackage."""
    cache: dict[str, "list[dict[str, Any]] | None"] = {}

    def wrapped(url: str) -> "list[dict[str, Any]] | None":
        if url not in cache:
            cache[url] = resolver(url)
        return cache[url]

    return wrapped


# --------------------------------------------------------------------------- #
# Related Tables Extension (associations)
# --------------------------------------------------------------------------- #


def _write_relations(
    connection: sqlite3.Connection, feature_types: list[dict[str, Any]], tables: set[str]
) -> None:
    mapping_registered: set[str] = set()
    rte_registered = False
    for ft in feature_types:
        base = ft.get("name")
        if not isinstance(base, str) or base not in tables:
            continue
        relationships = ft.get("relationships")
        associations = (
            relationships.get("associations") if isinstance(relationships, dict) else None
        )
        if not isinstance(associations, list):
            continue
        for association in associations:
            if not isinstance(association, dict):
                continue
            target = association.get("target")
            if not isinstance(target, str) or target not in tables:
                continue
            mapping_table = f"{_ascii_ident(base)}_{_ascii_ident(target)}_map"
            if mapping_table in mapping_registered:
                continue
            mapping_registered.add(mapping_table)

            # id-PK: GDAL/QGIS trenger en heltalls-primærnøkkel (FID) for å laste
            # mapping-tabellen som et gyldig lag — ellers blir RTE-relasjonen
            # «ugyldig». RTE-standarden tillater kolonner utover base_id/related_id.
            connection.execute(
                f"CREATE TABLE {_q(mapping_table)} "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "base_id INTEGER NOT NULL, related_id INTEGER NOT NULL)"
            )
            connection.execute(
                "INSERT INTO gpkgext_relations "
                "(base_table_name, base_primary_column, related_table_name, "
                "related_primary_column, relation_name, mapping_table_name) "
                "VALUES (?, 'fid', ?, 'fid', 'features', ?)",
                (base, target, mapping_table),
            )
            if not rte_registered:
                connection.execute(
                    "INSERT INTO gpkg_extensions "
                    "(table_name, column_name, extension_name, definition, scope) "
                    "VALUES ('gpkgext_relations', NULL, ?, ?, 'read-write')",
                    (_RTE_EXTENSION, _RTE_DEFINITION),
                )
                rte_registered = True
            connection.execute(
                "INSERT INTO gpkg_extensions "
                "(table_name, column_name, extension_name, definition, scope) "
                "VALUES (?, NULL, ?, ?, 'read-write')",
                (mapping_table, _RTE_EXTENSION, _RTE_DEFINITION),
            )


# --------------------------------------------------------------------------- #
# Public entry
# --------------------------------------------------------------------------- #


def write_geopackage(
    feature_types: list[dict[str, Any]],
    path: str | Path,
    *,
    default_crs: int = 25833,
    identifier: str | None = None,
    codelist_resolver: CodeListResolver | None = None,
) -> Path:
    """Write an empty GeoPackage materialising ``feature_types`` to ``path``.

    Returns the written path. Feature tables get a geometry column when the type
    carries geometry; otherwise an aspatial attributes table is written. Inline code
    lists (``valueDomain.listedValues``) always become Schema-extension enum
    constraints; **external** code lists (``valueDomain.codeList`` URL) are resolved
    to enum constraints too when ``codelist_resolver`` is given (pass
    :func:`_fetch_geonorge_codelist` to resolve SOSI code lists from the Geonorge
    register), otherwise the URL is kept in the column description only. Associations
    become Related-Tables relations with empty mapping tables.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    resolver = _memoize_resolver(codelist_resolver) if codelist_resolver else None

    connection = sqlite3.connect(str(path))
    try:
        _init_base(connection)
        srs_seen: set[int] = {-1, 0, 4326}
        schema_used = {"used": False}
        # Navneoppslag for arv (inkluderer abstrakte supertyper).
        by_name = {
            ft["name"]: ft
            for ft in feature_types
            if isinstance(ft, dict) and isinstance(ft.get("name"), str)
        }
        tables: set[str] = set()
        for ft in feature_types:
            if isinstance(ft, dict):
                table = _write_feature_type(
                    connection,
                    ft,
                    default_crs=default_crs,
                    srs_seen=srs_seen,
                    schema_used=schema_used,
                    by_name=by_name,
                    codelist_resolver=resolver,
                )
                if table:
                    tables.add(table)
        _write_relations(connection, feature_types, tables)
        connection.commit()
    finally:
        connection.close()
    return path
