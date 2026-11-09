"""GeoServer setup script emitters: the data model's PostGIS tables or GeoPackage
tables as OGC API - Features collections, set up through GeoServer's REST API."""

from .gpkg import (
    build_geoserver_gpkg_config,
    render_geoserver_gpkg_script,
    write_geoserver_gpkg_config,
)
from .postgis import (
    build_geoserver_postgis_config,
    render_geoserver_postgis_script,
    write_geoserver_postgis_config,
)

__all__ = [
    "build_geoserver_gpkg_config",
    "build_geoserver_postgis_config",
    "render_geoserver_gpkg_script",
    "render_geoserver_postgis_script",
    "write_geoserver_gpkg_config",
    "write_geoserver_postgis_config",
]
