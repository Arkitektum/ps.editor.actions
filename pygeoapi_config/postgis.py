"""Write a pygeoapi configuration (``.yml``) that publishes the data model's
PostGIS tables as OGC API - Features collections.

The configuration is the counterpart of the PostGIS DDL script: every realisable
feature type -- the concrete ones, which get a feature table in
:mod:`postgis.writer` -- becomes a ``resources`` entry served by pygeoapi's
``PostgreSQL`` provider. Table, id column, geometry column and storage CRS are
read from the same table model the DDL script is rendered from, so the
configuration points at exactly the tables and columns that script creates.

Every provider also gets a ``field_metadata`` section for our own PostgreSQL
provider: per column that holds a model attribute, in the model's order, the
attribute's name, description and -- for enumerations and code lists -- the codes
it allows::

    field_metadata:
      identifikasjon_lokalid:
        name: lokalId
        description: Lokal identifikator av et objekt
      kommunenummer:
        name: kommunenummer
        values:
        - '4020'
        - '4023'

The name is the attribute's own, unless another column of the table has the same
one; then it is the path through the flattened data types
(``kvalitet.datafangstdato``). Repeating attributes have a table of their own and
are not served, so they are not listed.

Database host, port, user and password are left as ``<REPLACE_ME>``. The service
metadata and collection descriptions come from :mod:`pygeoapi_config.common`.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from geopackage.writer import CodeListResolver
from postgis.writer import PRIMARY_KEY_COLUMN, Table, build_postgis_model, pg_name
from pygeoapi_config.common import (
    DEFAULT_SRID,
    PLACEHOLDER,
    collection,
    crs_settings,
    dataset_bbox,
    document,
    render_pygeoapi_config,
    unique_key,
)

# ``geometry(Polygon, 25833)`` / ``geometry(PointZ, 4258)`` -> the SRID.
_GEOMETRY_SRID = re.compile(r"geometry\(\s*\w+\s*,\s*(\d+)\s*\)", re.IGNORECASE)


def _filled(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _field_metadata(table: Table) -> dict[str, Any]:
    """Name, description and allowed codes of every column holding a model attribute."""
    columns = [column for column in table.columns if column.attribute is not None]
    leaf_names = Counter(column.attribute_path[-1] for column in columns)
    metadata: dict[str, Any] = {}
    for column in columns:
        path = column.attribute_path
        entry: dict[str, Any] = {
            "name": path[-1] if leaf_names[path[-1]] == 1 else ".".join(path)
        }
        description = (column.attribute or {}).get("description")
        if isinstance(description, str) and description.strip():
            entry["description"] = description.strip()
        if column.values:
            entry["values"] = list(column.values)
        metadata[column.name] = entry
    return metadata


def _postgresql_provider(
    table: Table, *, dbname: str, schema: str, default_srid: int
) -> dict[str, Any]:
    provider: dict[str, Any] = {
        "type": "feature",
        "name": "PostgreSQL",
        "data": {
            "host": PLACEHOLDER,
            "port": PLACEHOLDER,
            "dbname": dbname,
            "user": PLACEHOLDER,
            "password": PLACEHOLDER,
            "search_path": [schema],
        },
        "id_field": PRIMARY_KEY_COLUMN,
        "table": table.name,
    }

    # The PostgreSQL provider takes one geometry column. A class with several
    # geometries keeps them all in its table; the first is the one the feature
    # type declares as its geometry, so that is the one published.
    geometry = next((column for column in table.columns if column.geometry), None)
    if geometry is not None:
        match = _GEOMETRY_SRID.search(geometry.sql_type)
        provider["geom_field"] = geometry.name
        provider.update(crs_settings(int(match.group(1)) if match else default_srid))
    # Without a geometry column the collection is served without geometries;
    # pygeoapi's default "geom" field would point at a column that is not there.
    field_metadata = _field_metadata(table)
    if field_metadata:
        provider["field_metadata"] = field_metadata
    return provider


def build_pygeoapi_postgis_config(
    feature_types: list[dict[str, Any]],
    *,
    psdata: Any = None,
    dbname: str | None = None,
    schema: str | None = None,
    default_srid: int = DEFAULT_SRID,
    allow_z: bool = False,
    codelist_resolver: CodeListResolver | None = None,
) -> dict[str, Any]:
    """Build the pygeoapi configuration for the PostGIS tables as a dict.

    ``schema``, ``default_srid``/``allow_z`` and ``codelist_resolver`` must match
    what the PostGIS DDL script was written with, so table and column types --
    and the codes listed in ``field_metadata`` -- line up. ``dbname`` is the
    database the script is run in (sanitised like the table names); without one
    it is left for the user to fill in.
    """
    features = [ft for ft in feature_types if isinstance(ft, dict)]
    model = build_postgis_model(
        features,
        default_srid=default_srid,
        allow_z=allow_z,
        model_metadata=False,
        codelist_resolver=codelist_resolver,
    )
    tables = {table.name: table for table in model.tables if table.kind == "feature"}

    extent = dataset_bbox(psdata)
    database = pg_name(dbname) if _filled(dbname) else PLACEHOLDER
    search_path = pg_name(schema) if _filled(schema) else "public"

    resources: dict[str, Any] = {}
    for ft in features:
        table = tables.get(model.table_for_type.get(ft.get("name"), ""))
        if table is None:  # abstract, external or nameless: no table to serve
            continue
        provider = _postgresql_provider(
            table, dbname=database, schema=search_path, default_srid=default_srid
        )
        resources[unique_key(ft["name"], resources)] = collection(ft, provider, bbox=extent)

    return document(psdata, resources)


def write_pygeoapi_postgis_config(
    feature_types: list[dict[str, Any]],
    path: str | Path,
    **options: Any,
) -> Path:
    """Write :func:`build_pygeoapi_postgis_config`'s configuration to ``path``."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_pygeoapi_config(build_pygeoapi_postgis_config(feature_types, **options)),
        encoding="utf-8",
    )
    return out
