"""Shared parts of the pygeoapi configuration emitters.

:mod:`pygeoapi_config.postgis` serves the data model's PostGIS tables with
the ``PostgreSQL`` provider, :mod:`pygeoapi_config.gpkg` its GeoPackage tables with
``SQLiteGPKG``. Everything but the provider is the same and lives here.

The psdata values are read by :mod:`service_metadata`; here they are placed in
pygeoapi's structure:

* tittel, sammendrag, nøkkelord  -> ``metadata.identification``
* tilgangsrestriksjoner          -> ``identification.terms_of_service``
* metadatalenke                  -> ``identification.url``
* lisens                         -> ``metadata.license``
* eier (role ``owner``)          -> ``metadata.provider``
* kontaktpunkt (``pointOfContact``) -> ``metadata.contact``
* geografisk utstrekning         -> ``extents.spatial.bbox`` on every collection

Values only the operator of the service can know -- database connection, file
location, the provider's and contact's web address -- are written as
``<REPLACE_ME>``. So is any psdata value that is missing, rather than inventing
one: the file is a starting point that does not run until those are filled in.
"""
from __future__ import annotations

from typing import Any

import yaml

from service_metadata import (
    DEFAULT_SRID,
    OFFERED_EPSG_CODES,
    PLACEHOLDER,
    contact,
    dataset_bbox,
    feature_keywords,
    keywords,
    resource_name,
    string,
    text,
    unique_key,
)

__all__ = [
    "CRS84",
    "DEFAULT_SRID",
    "OFFERED_EPSG_CODES",
    "PLACEHOLDER",
    "collection",
    "crs_settings",
    "dataset_bbox",
    "document",
    "render_pygeoapi_config",
    "resource_name",
    "unique_key",
]

CRS84 = "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
_EPSG_URI = "http://www.opengis.net/def/crs/EPSG/0/{code}"


def _crs_uri(code: int) -> str:
    return _EPSG_URI.format(code=code)


# --------------------------------------------------------------------------- #
# psdata -> service metadata
# --------------------------------------------------------------------------- #


def _metadata(psdata: Any) -> dict[str, Any]:
    owner = contact(psdata, "owner")
    point_of_contact = contact(psdata, "pointOfContact")
    return {
        "identification": {
            "title": string(psdata, "identificationSection.title"),
            "description": string(psdata, "identificationSection.abstract"),
            "keywords": keywords(psdata) or [PLACEHOLDER],
            "keywords_type": "theme",
            "terms_of_service": string(
                psdata, "identificationSection.restriction.legalConstraints.accessConstraints"
            ),
            "url": string(psdata, "metadataSection.metadataIdentifier.metadataLinkage"),
        },
        "license": {
            "name": string(psdata, "identificationSection.restriction.legalConstraints.license"),
            "url": string(psdata, "identificationSection.restriction.legalConstraints.licenseUrl"),
        },
        "provider": {
            "name": text(owner.get("organizationName")) or PLACEHOLDER,
            "url": PLACEHOLDER,
        },
        "contact": {
            "name": text(point_of_contact.get("organizationName")) or PLACEHOLDER,
            "email": text(point_of_contact.get("electronicMailAddress")) or PLACEHOLDER,
            "url": PLACEHOLDER,
        },
    }


def _server() -> dict[str, Any]:
    return {
        "bind": {"host": "0.0.0.0", "port": 5000},
        "cors": False,
        "url": "${PYGEOAPI_SERVER_URL:-http://localhost:5000}",
        "mimetype": "application/json; charset=UTF-8",
        "encoding": "utf-8",
        "gzip": False,
        "languages": ["nb-NO"],
        "pretty_print": True,
        "admin": "${PYGEOAPI_SERVER_ADMIN:-false}",
        "limits": {"default_items": 20, "max_items": 100},
        "map": {
            "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            "attribution": (
                '&copy; <a href="https://openstreetmap.org/copyright">'
                "OpenStreetMap contributors</a>"
            ),
        },
    }


# --------------------------------------------------------------------------- #
# Feature types -> resources
# --------------------------------------------------------------------------- #


def crs_settings(srid: int) -> dict[str, Any]:
    """The ``crs`` list and ``storage_crs`` of a provider whose data is in ``srid``."""
    offered = [CRS84] + [_crs_uri(code) for code in OFFERED_EPSG_CODES]
    if _crs_uri(srid) not in offered:
        offered.append(_crs_uri(srid))
    return {"crs": offered, "storage_crs": _crs_uri(srid)}


def collection(
    ft: dict[str, Any], provider: dict[str, Any], *, bbox: list[float | str]
) -> dict[str, Any]:
    """A ``resources`` entry for ``ft``, served by ``provider``."""
    title = text(ft.get("title")) or text(ft.get("name"))
    return {
        "type": "collection",
        "title": title,
        "description": text(ft.get("description")) or PLACEHOLDER,
        # pygeoapi requires at least one keyword.
        "keywords": feature_keywords(ft, title),
        "extents": {
            "spatial": {
                "bbox": list(bbox),
                "crs": CRS84,
            }
        },
        "providers": [provider],
    }


def document(psdata: Any, resources: dict[str, Any]) -> dict[str, Any]:
    """The whole configuration around ``resources``."""
    return {
        "server": _server(),
        "logging": {"level": "ERROR"},
        "metadata": _metadata(psdata),
        "resources": resources,
    }


class _Dumper(yaml.SafeDumper):
    """Quotes ``<REPLACE_ME>`` so the values to fill in stand out."""


def _represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = '"' if value == PLACEHOLDER else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _represent_str)


def render_pygeoapi_config(config: dict[str, Any]) -> str:
    body = yaml.dump(
        config,
        Dumper=_Dumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    return (
        "# pygeoapi-konfigurasjon — generert fra datamodellen og metadata i Geonorge.\n"
        f'# Verdier merket "{PLACEHOLDER}" må fylles inn før tjenesten kan startes.\n'
        + body
    )
