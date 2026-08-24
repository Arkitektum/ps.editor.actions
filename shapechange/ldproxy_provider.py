"""Post-process a ShapeChange-generated ldproxy provider for the GeoPackage dialect.

ShapeChange's ``Ldproxy2Target`` only emits a PostgreSQL (``PGIS``) feature provider.
ldproxy itself also supports GeoPackage (``dialect: GPKG``). Because the GeoPackage
writer and the ldproxy target now share conventions -- an ``objid`` surrogate primary
key, flattened single-valued datatype columns, and a geometry column named after the
model property -- the *same* provider structure fits both dialects; only
``connectionInfo`` differs. This module rewrites ``connectionInfo`` so the provider
reads the sibling ``.gpkg`` directly, with no PostGIS deployment required.
"""
from __future__ import annotations

from pathlib import Path

import yaml

# ShapeChange writes the provider under <input-id>/data/entities/instances/providers/.
_PROVIDER_GLOB = "*/data/entities/instances/providers/*.yml"


def find_provider_files(ldproxy_dir: Path) -> list[Path]:
    """Return every ldproxy feature-provider config under ``ldproxy_dir``."""
    return sorted(Path(ldproxy_dir).glob(_PROVIDER_GLOB))


def discover_gpkg_name(ldproxy_dir: Path) -> str | None:
    """Best-effort lookup of the scope's GeoPackage filename. The ldproxy output sits at
    ``<scope>/schema/ldproxy``; the ``.gpkg`` is written in the scope directory (two
    levels up). Search is confined to the scope directory so sibling scopes' GeoPackages
    are never picked up. Returns the filename when exactly one is found, else None."""
    ldproxy_dir = Path(ldproxy_dir)
    # ldproxy_dir=<scope>/schema/ldproxy -> parent=<scope>/schema, parent.parent=<scope>
    for ancestor in (ldproxy_dir.parent, ldproxy_dir.parent.parent):
        matches = sorted(ancestor.glob("*.gpkg"))
        if len(matches) == 1:
            return matches[0].name
        if len(matches) > 1:
            return None
    return None


def _geopackage_connection_info(gpkg_name: str) -> dict:
    # ldproxy resolves `database` against the store's feature resources; the deployer
    # places the generated GeoPackage there. host/user/password/schemas are not relevant
    # for the GPKG dialect and are dropped.
    return {"dialect": "GPKG", "database": gpkg_name}


def apply_geopackage_provider(ldproxy_dir: Path, gpkg_name: str) -> list[Path]:
    """Rewrite every provider under ``ldproxy_dir`` to the GeoPackage (GPKG) dialect,
    pointing ``connectionInfo.database`` at ``gpkg_name``. Returns the files changed."""
    changed: list[Path] = []
    for provider in find_provider_files(ldproxy_dir):
        data = yaml.safe_load(provider.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        data["connectionInfo"] = _geopackage_connection_info(gpkg_name)
        provider.write_text(
            "---\n" + yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        changed.append(provider)
    return changed
