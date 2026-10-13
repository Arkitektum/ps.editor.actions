"""Shared parts of the pygeoapi configuration emitters.

:mod:`pygeoapi_config.postgis` serves the data model's PostGIS tables with
``PostgreSQLProvider``, :mod:`pygeoapi_config.gpkg` its GeoPackage tables with
``SQLiteGPKG``. Everything but the provider is the same and lives here.

The psdata document describes the service around the collections:

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

import re
from typing import Any

import yaml

from postgis.writer import pg_name

# Marks a value the end user has to fill in before the configuration is usable.
PLACEHOLDER = "<REPLACE_ME>"

CRS84 = "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
_EPSG_URI = "http://www.opengis.net/def/crs/EPSG/0/{code}"

# The CRSs every collection offers, after CRS84: the UTM zones and NN2000 heights
# used in Norway, web mapping, the Arctic polar projection and the European ones.
OFFERED_EPSG_CODES: tuple[int, ...] = (
    25832,
    25833,
    25835,
    5972,
    5973,
    5975,
    3857,
    3575,
    4326,
    3035,
    4258,
    5942,
)

DEFAULT_SRID = 25833


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _crs_uri(code: int) -> str:
    return _EPSG_URI.format(code=code)


def resource_name(name: str) -> str:
    """Collection id for a feature type: latinised, lowercase kebab-case.

    ``"Dyrkbar jord"`` -> ``dyrkbar-jord``, ``"AdministrativEnhet"`` ->
    ``administrativ-enhet``, ``"ÅpenGrunn"`` -> ``aapen-grunn``. Word boundaries
    inside a CamelCase name are kept; the transliteration is the one the PostGIS
    table names use (``æ``→``ae``, ``ø``→``oe``, ``å``→``aa``).
    """
    text = str(name or "")
    # "administrativEnhet" -> "administrativ Enhet", "FKBBygning" -> "FKB Bygning".
    text = re.sub(r"(?<=[a-zæøå0-9])(?=[A-ZÆØÅ])", " ", text)
    text = re.sub(r"(?<=[A-ZÆØÅ])(?=[A-ZÆØÅ][a-zæøå])", " ", text)
    return pg_name(text).replace("_", "-")


# --------------------------------------------------------------------------- #
# psdata -> service metadata
# --------------------------------------------------------------------------- #


def _path(psdata: dict[str, Any], dotted: str) -> Any:
    """The value at ``dotted`` (``"identificationSection.title"``), or ``None``."""
    value: Any = psdata
    for key in dotted.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _string(psdata: dict[str, Any], dotted: str) -> str:
    return _text(_path(psdata, dotted)) or PLACEHOLDER


def _contact(psdata: dict[str, Any], role: str) -> dict[str, Any]:
    """The first ``identificationSection.contact`` entry with ``role``."""
    contacts = _path(psdata, "identificationSection.contact")
    for contact in contacts if isinstance(contacts, list) else []:
        if isinstance(contact, dict) and _text(contact.get("role")).casefold() == role.casefold():
            return contact
    return {}


def _keywords(psdata: dict[str, Any]) -> list[str]:
    value = _path(psdata, "identificationSection.keyword")
    items = value if isinstance(value, list) else [value]
    keywords = [text for item in items if (text := _text(item))]
    return keywords or [PLACEHOLDER]


def _bbox(psdata: dict[str, Any]) -> list[float | str]:
    """The dataset's geographic extent as ``[west, south, east, north]``.

    psdata holds the coordinates as strings; pygeoapi wants numbers. A missing
    or unreadable coordinate is left for the user to fill in.
    """
    element = _mapping(_path(psdata, "identificationSection.extent.geographicElement"))
    bbox: list[float | str] = []
    for key in (
        "westBoundLongitude",
        "southBoundLatitude",
        "eastBoundLongitude",
        "northBoundLatitude",
    ):
        try:
            bbox.append(float(str(element.get(key)).strip().replace(",", ".")))
        except (TypeError, ValueError):
            bbox.append(PLACEHOLDER)
    return bbox


def _metadata(psdata: dict[str, Any]) -> dict[str, Any]:
    owner = _contact(psdata, "owner")
    point_of_contact = _contact(psdata, "pointOfContact")
    return {
        "identification": {
            "title": _string(psdata, "identificationSection.title"),
            "description": _string(psdata, "identificationSection.abstract"),
            "keywords": _keywords(psdata),
            "keywords_type": "theme",
            "terms_of_service": _string(
                psdata, "identificationSection.restriction.legalConstraints.accessConstraints"
            ),
            "url": _string(psdata, "metadataSection.metadataIdentifier.metadataLinkage"),
        },
        "license": {
            "name": _string(psdata, "identificationSection.restriction.legalConstraints.license"),
            "url": _string(psdata, "identificationSection.restriction.legalConstraints.licenseUrl"),
        },
        "provider": {
            "name": _text(owner.get("organizationName")) or PLACEHOLDER,
            "url": PLACEHOLDER,
        },
        "contact": {
            "name": _text(point_of_contact.get("organizationName")) or PLACEHOLDER,
            "email": _text(point_of_contact.get("electronicMailAddress")) or PLACEHOLDER,
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


def _resource_keywords(ft: dict[str, Any], title: str) -> list[str]:
    """The feature type's own keywords, else its title: pygeoapi requires one."""
    value = ft.get("keywords")
    items = value if isinstance(value, list) else [value]
    keywords = [text for item in items if (text := _text(item))]
    return keywords or [title]


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
    title = _text(ft.get("title")) or _text(ft.get("name"))
    return {
        "type": "collection",
        "title": title,
        "description": _text(ft.get("description")) or PLACEHOLDER,
        "keywords": _resource_keywords(ft, title),
        "extents": {
            "spatial": {
                "bbox": list(bbox),
                "crs": CRS84,
            }
        },
        "providers": [provider],
    }


def unique_key(name: str, taken: dict[str, Any]) -> str:
    """The collection id for ``name``, suffixed ``-2``, ``-3``, … when taken."""
    base = resource_name(name)
    key, suffix = base, 2
    while key in taken:
        key, suffix = f"{base}-{suffix}", suffix + 1
    return key


def document(psdata: Any, resources: dict[str, Any]) -> dict[str, Any]:
    """The whole configuration around ``resources``."""
    return {
        "server": _server(),
        "logging": {"level": "ERROR"},
        "metadata": _metadata(_mapping(psdata)),
        "resources": resources,
    }


def dataset_bbox(psdata: Any) -> list[float | str]:
    """The dataset's extent from psdata, shared by every collection."""
    return _bbox(_mapping(psdata))


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
