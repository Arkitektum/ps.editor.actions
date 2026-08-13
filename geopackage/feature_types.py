"""Load feature types from a GeoPackage data source.

A GeoPackage (``.gpkg``) is a SQLite database, so its schema is read directly with
the standard-library :mod:`sqlite3` module — no GDAL/fiona/pyogrio dependency. The
source may be:

* a local ``.gpkg`` file path,
* a direct ``.gpkg`` URL, or
* a Geonorge **Atom download feed** that lists one or more ``.gpkg`` files. In that
  case the *Landsdekkende* (nationwide) entry for the preferred CRS is resolved and
  downloaded.

The returned structure mirrors :mod:`ogc_api.feature_types` (a list of feature-type
dicts with ``name``/``description``/``geometry``/``attributes``) so the downstream
feature-catalogue rendering stays generator-agnostic.

Access-restricted datasets ("Norge digitalt-begrenset") require credentials for the
file download; supply ``username``/``password`` for HTTP Basic auth. Fetching the
Atom feed itself is unauthenticated.
"""

from __future__ import annotations

import sqlite3
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

try:  # requests is installed by the action; guard so the module imports in tests
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

_ATOM_NS = "{http://www.w3.org/2005/Atom}"

# Foretrukket CRS-rekkefølge når en Atom-feed lister flere landsdekkende varianter.
_DEFAULT_CRS_PREFERENCE = ("EPSG:25833", "EPSG:4258")

# SQLite/GeoPackage-kolonnetype -> featurekatalog-attributt-type.
_TYPE_MAP = {
    "INTEGER": "integer",
    "INT": "integer",
    "TINYINT": "integer",
    "SMALLINT": "integer",
    "MEDIUMINT": "integer",
    "BIGINT": "integer",
    "TEXT": "string",
    "VARCHAR": "string",
    "CHARACTER": "string",
    "CHAR": "string",
    "NCHAR": "string",
    "NVARCHAR": "string",
    "CLOB": "string",
    "REAL": "number",
    "FLOAT": "number",
    "DOUBLE": "number",
    "DECIMAL": "number",
    "NUMERIC": "number",
    "BOOLEAN": "boolean",
    "BOOL": "boolean",
    "DATE": "date",
    "DATETIME": "dateTime",
    "TIMESTAMP": "dateTime",
    "BLOB": "binary",
}

# GeoPackage-geometritype -> featurekatalog-geometritype (samme stil som OGC).
_GEOMETRY_MAP = {
    "POINT": "geometry-point",
    "MULTIPOINT": "geometry-point",
    "LINESTRING": "geometry-line",
    "MULTILINESTRING": "geometry-line",
    "CURVE": "geometry-line",
    "MULTICURVE": "geometry-line",
    "COMPOUNDCURVE": "geometry-line",
    "POLYGON": "geometry-polygon",
    "MULTIPOLYGON": "geometry-polygon",
    "SURFACE": "geometry-polygon",
    "MULTISURFACE": "geometry-polygon",
    "CURVEPOLYGON": "geometry-polygon",
    "GEOMETRY": "geometry",
    "GEOMETRYCOLLECTION": "geometry",
}


def _map_column_type(sqlite_type: str | None) -> str:
    base = (sqlite_type or "").strip().upper().split("(", 1)[0].strip()
    return _TYPE_MAP.get(base, "string")


def _map_geometry_type(name: str | None) -> str:
    return _GEOMETRY_MAP.get((name or "").strip().upper(), "geometry")


def _default_http_get(url: str, *, auth: Any = None, stream: bool = False):
    if requests is None:  # pragma: no cover
        raise RuntimeError(
            "The 'requests' package is required to download GeoPackage sources."
        )
    return requests.get(url, auth=auth, stream=stream, timeout=120)


# --------------------------------------------------------------------------- #
# Kilde-oppløsning: lokal fil / direkte .gpkg / Atom-feed
# --------------------------------------------------------------------------- #


def _looks_like_gpkg(value: str) -> bool:
    return value.lower().split("?", 1)[0].endswith(".gpkg")


def _entry_matches_crs(entry: ET.Element, preference: str) -> bool:
    pref = preference.replace(" ", "").upper()
    code = pref.split(":")[-1]
    for category in entry.findall(f"{_ATOM_NS}category"):
        scheme = (category.get("scheme") or "").lower()
        if "crs" not in scheme:
            continue
        term = (category.get("term") or "").replace(" ", "").upper()
        label = (category.get("label") or "").replace(" ", "").upper()
        if pref in (term, label) or (code and (code in term or code in label)):
            return True
    return False


def _entry_download_href(entry: ET.Element) -> str | None:
    # Bruk <link rel="alternate" href> (id-en har et _timestamp-suffiks som ikke
    # kan brukes direkte). Fall tilbake på en hvilken som helst link med href.
    fallback: str | None = None
    for link in entry.findall(f"{_ATOM_NS}link"):
        href = link.get("href")
        if not href:
            continue
        rel = (link.get("rel") or "alternate").lower()
        if rel == "alternate":
            return href
        fallback = fallback or href
    return fallback


def _resolve_atom_feed(
    feed_url: str,
    *,
    crs_preference: Any = None,
    http_get: Any = None,
) -> str:
    getter = http_get or _default_http_get
    response = getter(feed_url)
    status = getattr(response, "status_code", 200)
    if status >= 400:
        raise RuntimeError(
            f"Request to Atom feed '{feed_url}' failed with status code {status}."
        )
    try:
        root = ET.fromstring(response.text)
    except ET.ParseError as error:
        raise RuntimeError(f"Atom feed '{feed_url}' is not valid XML.") from error

    entries = root.findall(f"{_ATOM_NS}entry")
    if not entries:
        raise RuntimeError(f"Atom feed '{feed_url}' contains no entries.")

    # Foretrekk landsdekkende varianter (komplett datamodell).
    nationwide = [
        entry
        for entry in entries
        if "landsdekkende" in (entry.findtext(f"{_ATOM_NS}title") or "").lower()
    ]
    candidates = nationwide or entries

    chosen: ET.Element | None = None
    for preference in list(crs_preference or _DEFAULT_CRS_PREFERENCE):
        for entry in candidates:
            if _entry_matches_crs(entry, preference):
                chosen = entry
                break
        if chosen is not None:
            break
    if chosen is None:
        chosen = candidates[0]

    href = _entry_download_href(chosen)
    if not href:
        raise RuntimeError(
            f"Could not find a download link in the selected Atom entry of '{feed_url}'."
        )
    return href


def _download_gpkg(href: str, *, auth: Any = None, http_get: Any = None) -> Path:
    getter = http_get or _default_http_get
    response = getter(href, auth=auth, stream=True)
    status = getattr(response, "status_code", 200)
    if status >= 400:
        hint = (
            " If the dataset is access-restricted ('Norge digitalt-begrenset'), "
            "supply valid credentials."
            if status in (401, 403)
            else ""
        )
        raise RuntimeError(
            f"Request to download GeoPackage from '{href}' failed with status code {status}.{hint}"
        )

    tmp = tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False)
    try:
        iter_content = getattr(response, "iter_content", None)
        if callable(iter_content):
            for chunk in iter_content(chunk_size=256 * 1024):
                if chunk:
                    tmp.write(chunk)
        else:
            tmp.write(response.content)
    finally:
        tmp.close()
    return Path(tmp.name)


def _resolve_geopackage_source(
    url_or_path: str,
    *,
    auth: Any = None,
    crs_preference: Any = None,
    http_get: Any = None,
) -> tuple[Path, bool]:
    """Return ``(path, is_temp)`` for a concrete local .gpkg file."""
    local = Path(url_or_path)
    if local.exists():
        return local, False

    text = str(url_or_path)
    if _looks_like_gpkg(text):
        return _download_gpkg(text, auth=auth, http_get=http_get), True

    href = _resolve_atom_feed(text, crs_preference=crs_preference, http_get=http_get)
    return _download_gpkg(href, auth=auth, http_get=http_get), True


# --------------------------------------------------------------------------- #
# Skjema-lesing (sqlite3 mot gpkg_*-metatabellene)
# --------------------------------------------------------------------------- #


def _assert_geopackage(path: Path) -> None:
    with open(path, "rb") as handle:
        header = handle.read(16)
    if not header.startswith(b"SQLite format 3"):
        raise RuntimeError(
            f"'{path}' is not a valid SQLite/GeoPackage file "
            "(the download may have returned an error or login page)."
        )


def _srs_to_uri(connection: sqlite3.Connection, srs_id: Any) -> str | None:
    if srs_id is None:
        return None
    row = connection.execute(
        "SELECT organization, organization_coordsys_id "
        "FROM gpkg_spatial_ref_sys WHERE srs_id = ?",
        (srs_id,),
    ).fetchone()
    if row is None:
        return None
    organization = (row["organization"] or "").upper()
    code = row["organization_coordsys_id"]
    if organization == "EPSG" and code:
        return f"http://www.opengis.net/def/crs/EPSG/0/{code}"
    return None


def _build_geometry(connection: sqlite3.Connection, geometry_row: sqlite3.Row) -> dict[str, Any]:
    geom_type = _map_geometry_type(geometry_row["geometry_type_name"])
    geometry: dict[str, Any] = {
        "itemType": "feature",
        "type": geom_type,
        "format": geom_type,
        "ogcRole": "primary-geometry",
    }
    crs_uri = _srs_to_uri(connection, geometry_row["srs_id"])
    if crs_uri:
        geometry["crs"] = [crs_uri]
        geometry["storageCrs"] = crs_uri
    return geometry


def _read_geopackage_schema(gpkg_path: Path) -> list[dict[str, Any]]:
    _assert_geopackage(gpkg_path)
    connection = sqlite3.connect(str(gpkg_path))
    connection.row_factory = sqlite3.Row
    try:
        try:
            contents = connection.execute(
                "SELECT table_name, identifier, description, srs_id "
                "FROM gpkg_contents WHERE data_type = 'features' ORDER BY table_name"
            ).fetchall()
        except sqlite3.DatabaseError as error:
            raise RuntimeError(
                f"'{gpkg_path}' is not a GeoPackage (missing gpkg_contents)."
            ) from error

        geometry_columns: dict[str, sqlite3.Row] = {}
        for row in connection.execute(
            "SELECT table_name, column_name, geometry_type_name, srs_id "
            "FROM gpkg_geometry_columns"
        ):
            geometry_columns[row["table_name"]] = row

        feature_types: list[dict[str, Any]] = []
        for content in contents:
            table = content["table_name"]
            geometry_row = geometry_columns.get(table)
            geometry_column_name = geometry_row["column_name"] if geometry_row else None

            attributes: list[dict[str, Any]] = []
            for column in connection.execute(f'PRAGMA table_info("{table}")'):
                if column["name"] == geometry_column_name:
                    continue
                attribute: dict[str, Any] = {
                    "name": column["name"],
                    "type": _map_column_type(column["type"]),
                    "cardinality": "1" if (column["notnull"] or column["pk"]) else "0..1",
                }
                if column["pk"]:
                    attribute["ogcRole"] = "id"
                attributes.append(attribute)

            feature_type: dict[str, Any] = {
                "name": content["identifier"] or table,
                "description": content["description"] or "",
            }
            if geometry_row is not None:
                feature_type["geometry"] = _build_geometry(connection, geometry_row)
            feature_type["attributes"] = attributes
            feature_types.append(feature_type)

        return feature_types
    finally:
        connection.close()


# --------------------------------------------------------------------------- #
# Offentlig inngang
# --------------------------------------------------------------------------- #


def load_feature_types_from_geopackage(
    url_or_path: str,
    *,
    username: str | None = None,
    password: str | None = None,
    crs_preference: Any = None,
    http_get: Any = None,
) -> list[dict[str, Any]]:
    """Read the data model (feature types) from a GeoPackage source.

    ``url_or_path`` may be a local ``.gpkg`` path, a direct ``.gpkg`` URL, or a
    Geonorge Atom download feed. ``username``/``password`` enable HTTP Basic auth
    for access-restricted file downloads. ``http_get`` is injectable for testing.
    """
    auth = (username, password) if username and password else None
    gpkg_path, is_temp = _resolve_geopackage_source(
        url_or_path, auth=auth, crs_preference=crs_preference, http_get=http_get
    )
    try:
        return _read_geopackage_schema(gpkg_path)
    finally:
        if is_temp:
            try:
                gpkg_path.unlink()
            except OSError:  # pragma: no cover
                pass
