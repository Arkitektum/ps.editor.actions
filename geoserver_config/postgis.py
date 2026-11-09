"""Write a GeoServer setup script (``.sh``) that publishes the data model's PostGIS
tables as OGC API - Features collections.

The GeoServer counterpart of :mod:`pygeoapi_config.postgis`: every realisable
feature type -- the concrete ones, which get a feature table in
:mod:`postgis.writer` -- becomes a layer in a ``postgis`` data store. Table and
storage CRS are read from the same table model the DDL script is rendered from,
so the layers point at exactly the tables that script creates.

GeoServer publishes every column of a table, so a class with several geometries
keeps them all; the first, the one the feature type declares, is the default
geometry. The primary key ``objid`` is exposed as a property as well.

The database connection is read from ``POSTGIS_*`` environment variables when the
script runs; the database and schema default to the ones the DDL script uses.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from geoserver_config.common import (
    dataset_extent,
    document,
    feature_type,
    render_geoserver_script,
    workspace_name,
)
from postgis.writer import build_postgis_model, pg_name
from service_metadata import DEFAULT_SRID, unique_key

# ``geometry(Polygon, 25833)`` / ``geometry(PointZ, 4258)`` -> the SRID.
_GEOMETRY_SRID = re.compile(r"geometry\(\s*\w+\s*,\s*(\d+)\s*\)", re.IGNORECASE)

STORE_NAME = "postgis"

_STORE_SECTION = r"""
STORE=postgis
echo "Datalager $STORE"
upsert "/workspaces/$WS/datastores" "$STORE" "{\"dataStore\":{\"name\":$(json_string "$STORE"),\"connectionParameters\":{\"entry\":[
$(parameter dbtype postgis),
$(parameter host "$POSTGIS_HOST"),
$(parameter port "$POSTGIS_PORT"),
$(parameter database "$POSTGIS_DATABASE"),
$(parameter schema "$POSTGIS_SCHEMA"),
$(parameter user "$POSTGIS_USER"),
$(parameter passwd "$POSTGIS_PASSWORD"),
$(parameter 'Expose primary keys' true)
]}}}"
"""


def _filled(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _srid(table: Any, default_srid: int) -> int:
    geometry = next((column for column in table.columns if column.geometry), None)
    match = _GEOMETRY_SRID.search(geometry.sql_type) if geometry is not None else None
    return int(match.group(1)) if match else default_srid


def build_geoserver_postgis_config(
    feature_types: list[dict[str, Any]],
    *,
    psdata: Any = None,
    workspace: str | None = None,
    dbname: str | None = None,
    schema: str | None = None,
    default_srid: int = DEFAULT_SRID,
    allow_z: bool = False,
) -> dict[str, Any]:
    """Build what the setup script for the PostGIS tables creates, as a dict.

    ``schema`` and ``default_srid``/``allow_z`` must match what the PostGIS DDL
    script was written with. ``workspace`` and ``dbname`` become the defaults of
    ``GEOSERVER_WORKSPACE`` and ``POSTGIS_DATABASE``; without them the user has
    to set those variables.
    """
    features = [ft for ft in feature_types if isinstance(ft, dict)]
    model = build_postgis_model(
        features,
        default_srid=default_srid,
        allow_z=allow_z,
        model_metadata=False,
    )
    tables = {table.name: table for table in model.tables if table.kind == "feature"}
    extent = dataset_extent(psdata)

    layers: list[dict[str, Any]] = []
    taken: dict[str, Any] = {}
    for ft in features:
        table = tables.get(model.table_for_type.get(ft.get("name"), ""))
        if table is None:  # abstract, external or nameless: no table to serve
            continue
        name = unique_key(ft["name"], taken)
        taken[name] = table
        layers.append(
            feature_type(
                ft,
                name=name,
                native_name=table.name,
                srid=_srid(table, default_srid),
                bbox=extent,
            )
        )

    store = {
        "name": STORE_NAME,
        "database": pg_name(dbname) if _filled(dbname) else None,
        "schema": pg_name(schema) if _filled(schema) else "public",
    }
    return document(
        psdata,
        workspace=workspace_name(workspace) if _filled(workspace) else None,
        store=store,
        layers=layers,
    )


def render_geoserver_postgis_script(config: dict[str, Any]) -> str:
    store = config["store"]
    return render_geoserver_script(
        config,
        purpose="Publiserer datamodellens PostGIS-tabeller fra databasen",
        variables=[
            ("POSTGIS_HOST", None, "databaseserveren, slik GeoServer når den"),
            ("POSTGIS_PORT", "5432", "databaseserverens port"),
            ("POSTGIS_DATABASE", store["database"], "databasen"),
            ("POSTGIS_SCHEMA", store["schema"], "skjemaet tabellene ligger i"),
            ("POSTGIS_USER", None, "databasebrukeren GeoServer leser med"),
            ("POSTGIS_PASSWORD", None, "databasebrukerens passord"),
        ],
        store_section=_STORE_SECTION,
    )


def write_geoserver_postgis_config(
    feature_types: list[dict[str, Any]],
    path: str | Path,
    **options: Any,
) -> Path:
    """Write the setup script for :func:`build_geoserver_postgis_config` to ``path``."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_geoserver_postgis_script(build_geoserver_postgis_config(feature_types, **options)),
        encoding="utf-8",
    )
    os.chmod(out, 0o755)
    return out
