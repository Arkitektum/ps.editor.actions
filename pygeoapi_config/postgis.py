"""Write a pygeoapi configuration (``.yml``) that publishes the data model's
PostGIS tables as OGC API - Features collections.

The configuration is the counterpart of the PostGIS DDL script: every realisable
feature type -- the concrete ones, which get a feature table in
:mod:`postgis.writer` -- becomes a ``resources`` entry served by pygeoapi's
``PostgreSQL`` provider. Table, id column, geometry column and storage CRS are
read from the same table model the DDL script is rendered from, so the
configuration points at exactly the tables and columns that script creates.

Database host, port, user and password are left as ``<REPLACE_ME>``. The service
metadata and collection descriptions come from :mod:`pygeoapi_config.common`.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from postgis.writer import PRIMARY_KEY_COLUMN, build_postgis_model, pg_name
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


def _postgresql_provider(
    table: Any, *, dbname: str, schema: str, default_srid: int
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
    return provider


def build_pygeoapi_postgis_config(
    feature_types: list[dict[str, Any]],
    *,
    psdata: Any = None,
    dbname: str | None = None,
    schema: str | None = None,
    default_srid: int = DEFAULT_SRID,
    allow_z: bool = False,
) -> dict[str, Any]:
    """Build the pygeoapi configuration for the PostGIS tables as a dict.

    ``schema`` and ``default_srid``/``allow_z`` must match what the PostGIS DDL
    script was written with, so table and column types line up. ``dbname`` is
    the database the script is run in (sanitised like the table names);
    without one it is left for the user to fill in.
    """
    features = [ft for ft in feature_types if isinstance(ft, dict)]
    model = build_postgis_model(
        features,
        default_srid=default_srid,
        allow_z=allow_z,
        model_metadata=False,
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
