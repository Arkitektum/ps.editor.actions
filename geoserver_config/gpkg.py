"""Write a GeoServer setup script (``.sh``) that publishes the data model's
GeoPackage tables as OGC API - Features collections.

The GeoPackage counterpart of :mod:`geoserver_config.postgis`: the same workspace
settings and layer metadata, but the layers come from a ``geopkg`` data store.
They are the feature tables :func:`geopackage.writer.write_geopackage` creates,
taken from the same rules (:func:`geopackage.writer.realised_feature_tables`) as
:mod:`pygeoapi_config.gpkg`:

* abstract types get no table;
* a class with several geometries is split into one table per geometry
  (``Bygning_flate``, ``Bygning_punkt``), so each becomes its own layer;
* a type without geometry -- an aspatial attributes table -- gets no layer:
  GeoServer's GeoPackage store only lists the feature tables.

By default the script uploads the GeoPackage next to it into GeoServer's data
directory (``data/<workspace>/``) through the REST resource API, so the server's
file system need not be reachable. ``GEOPACKAGE_SERVER_PATH`` points the store
at a file already on the server instead.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from geopackage.writer import realised_feature_tables
from geoserver_config.common import (
    dataset_extent,
    document,
    double_quoted,
    feature_type,
    render_geoserver_script,
    workspace_name,
)
from service_metadata import DEFAULT_SRID, unique_key

STORE_NAME = "geopackage"

# Without a known file name GEOPACKAGE_FILE has no default.
_DEFAULT_FILE_LINE = '  [ -n "$GEOPACKAGE_FILE" ] || GEOPACKAGE_FILE="$(dirname "$0")/__GPKG_NAME__"\n'

_STORE_SECTION = r"""
STORE=geopackage
if [ -n "$GEOPACKAGE_SERVER_PATH" ]; then
  database="$GEOPACKAGE_SERVER_PATH"
else
  [ -n "$GEOPACKAGE_FILE" ] || GEOPACKAGE_FILE="$(dirname "$0")/__GPKG_NAME__"
  if [ ! -f "$GEOPACKAGE_FILE" ]; then
    echo "Finner ikke GeoPackage-fila '$GEOPACKAGE_FILE'" \
      "(sett GEOPACKAGE_FILE eller GEOPACKAGE_SERVER_PATH)" >&2
    exit 1
  fi
  gpkg_name="$(basename "$GEOPACKAGE_FILE")"
  echo "Laster opp $GEOPACKAGE_FILE"
  request PUT "/resource/data/$WS/$gpkg_name" \
    -H 'Content-Type: application/octet-stream' --data-binary "@$GEOPACKAGE_FILE"
  database="file:data/$WS/$gpkg_name"
fi
echo "Datalager $STORE"
upsert "/workspaces/$WS/datastores" "$STORE" "{\"dataStore\":{\"name\":$(json_string "$STORE"),\"connectionParameters\":{\"entry\":[
$(parameter dbtype geopkg),
$(parameter database "$database"),
$(parameter 'Expose primary keys' true)
]}}}"
"""


def build_geoserver_gpkg_config(
    feature_types: list[dict[str, Any]],
    *,
    psdata: Any = None,
    workspace: str | None = None,
    geopackage: str | None = None,
    default_crs: int = DEFAULT_SRID,
    split_multi_geometry: bool = True,
) -> dict[str, Any]:
    """Build what the setup script for the GeoPackage creates, as a dict.

    ``geopackage`` is the GeoPackage's file name, looked for next to the script
    (``GEOPACKAGE_FILE`` overrides it). ``default_crs`` and
    ``split_multi_geometry`` must match what the GeoPackage was written with.
    """
    extent = dataset_extent(psdata)
    layers: list[dict[str, Any]] = []
    taken: dict[str, Any] = {}
    for table in realised_feature_tables(
        [ft for ft in feature_types if isinstance(ft, dict)],
        default_crs=default_crs,
        split_multi_geometry=split_multi_geometry,
    ):
        if table.geometry_column is None:
            continue  # an attributes table: not offered by the geopkg store
        name = unique_key(table.name, taken)
        taken[name] = table
        layers.append(
            feature_type(
                table.feature_type,
                name=name,
                native_name=table.name,
                srid=table.srs_id or default_crs,
                bbox=extent,
            )
        )

    filled = isinstance(geopackage, str) and geopackage.strip()
    store = {"name": STORE_NAME, "file": Path(geopackage).name if filled else None}
    return document(
        psdata,
        workspace=workspace_name(workspace) if workspace and workspace.strip() else None,
        store=store,
        layers=layers,
    )


def render_geoserver_gpkg_script(config: dict[str, Any]) -> str:
    file_name = config["store"]["file"]
    if file_name:
        local = f"GeoPackage-fila som lastes opp (standard: {file_name} ved siden av skriptet)"
        section = _STORE_SECTION.replace("__GPKG_NAME__", double_quoted(file_name))
    else:
        local = "GeoPackage-fila som lastes opp"
        section = _STORE_SECTION.replace(_DEFAULT_FILE_LINE, "")
    return render_geoserver_script(
        config,
        purpose="Publiserer datamodellens GeoPackage-tabeller",
        variables=[
            ("GEOPACKAGE_FILE", "", local),
            (
                "GEOPACKAGE_SERVER_PATH",
                "",
                "fila slik GeoServer ser den, i stedet for å laste opp (valgfri)",
            ),
        ],
        store_section=section,
    )


def write_geoserver_gpkg_config(
    feature_types: list[dict[str, Any]],
    path: str | Path,
    **options: Any,
) -> Path:
    """Write the setup script for :func:`build_geoserver_gpkg_config` to ``path``."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_geoserver_gpkg_script(build_geoserver_gpkg_config(feature_types, **options)),
        encoding="utf-8",
    )
    os.chmod(out, 0o755)
    return out
