"""Write a pygeoapi configuration (``.yml``) that publishes the data model's
GeoPackage tables as OGC API - Features collections.

The GeoPackage counterpart of :mod:`pygeoapi_config.postgis`: the same service
metadata and collection descriptions, but every collection is served by
pygeoapi's ``SQLiteGPKG`` provider from the GeoPackage file instead of from
PostgreSQL.

The collections are the feature tables :func:`geopackage.writer.write_geopackage`
creates, taken from the same rules (:func:`geopackage.writer.realised_feature_tables`):

* abstract types get no table;
* a class with several geometries is split into one table per geometry
  (``Bygning_flate``, ``Bygning_punkt``), so each becomes its own collection;
* table names are the feature type names as they are, and the id column is
  ``objid``.

SQLiteGPKG finds the geometry column itself in ``gpkg_geometry_columns``, so no
``geom_field`` is written. It also refuses a table without one, so a type
without geometry -- an aspatial attributes table in the GeoPackage -- gets no
collection.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from geopackage.writer import PRIMARY_KEY_COLUMN, realised_feature_tables
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


def build_pygeoapi_gpkg_config(
    feature_types: list[dict[str, Any]],
    *,
    psdata: Any = None,
    data: str | None = None,
    default_crs: int = DEFAULT_SRID,
    split_multi_geometry: bool = True,
) -> dict[str, Any]:
    """Build the pygeoapi configuration for the GeoPackage as a dict.

    ``data`` is the path to the GeoPackage as pygeoapi should open it (relative
    paths are resolved from pygeoapi's working directory); without one it is
    left for the user to fill in. ``default_crs`` and ``split_multi_geometry``
    must match what the GeoPackage was written with.
    """
    path = data.strip() if isinstance(data, str) and data.strip() else PLACEHOLDER
    extent = dataset_bbox(psdata)

    resources: dict[str, Any] = {}
    for table in realised_feature_tables(
        [ft for ft in feature_types if isinstance(ft, dict)],
        default_crs=default_crs,
        split_multi_geometry=split_multi_geometry,
    ):
        if table.geometry_column is None:
            continue  # SQLiteGPKG raises on a table without a geometry column
        provider: dict[str, Any] = {
            "type": "feature",
            "name": "SQLiteGPKG",
            "data": path,
            "id_field": PRIMARY_KEY_COLUMN,
            "table": table.name,
            **crs_settings(table.srs_id or default_crs),
        }
        resources[unique_key(table.name, resources)] = collection(
            table.feature_type, provider, bbox=extent
        )

    return document(psdata, resources)


def write_pygeoapi_gpkg_config(
    feature_types: list[dict[str, Any]],
    path: str | Path,
    **options: Any,
) -> Path:
    """Write :func:`build_pygeoapi_gpkg_config`'s configuration to ``path``."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_pygeoapi_config(build_pygeoapi_gpkg_config(feature_types, **options)),
        encoding="utf-8",
    )
    return out
