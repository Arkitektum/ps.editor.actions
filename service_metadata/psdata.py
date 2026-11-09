"""psdata and feature types -> the parts of a service configuration that do not
depend on the server software.

Used by :mod:`pygeoapi_config` and :mod:`geoserver_config`. The psdata document
describes the service around the collections; each emitter maps these values
onto its own configuration:

* tittel, sammendrag, nøkkelord     -> service title, description, keywords
* tilgangsrestriksjoner             -> terms of service / access constraints
* metadatalenke                     -> service URL
* lisens                            -> licence
* eier (role ``owner``)             -> provider
* kontaktpunkt (``pointOfContact``) -> contact
* geografisk utstrekning            -> every collection's extent
"""
from __future__ import annotations

import re
from typing import Any

from postgis.writer import pg_name

# Marks a value the end user has to fill in before the configuration is usable.
PLACEHOLDER = "<REPLACE_ME>"

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


def text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def resource_name(name: str) -> str:
    """Collection id for a feature type: latinised, lowercase kebab-case.

    ``"Dyrkbar jord"`` -> ``dyrkbar-jord``, ``"AdministrativEnhet"`` ->
    ``administrativ-enhet``, ``"ÅpenGrunn"`` -> ``aapen-grunn``. Word boundaries
    inside a CamelCase name are kept; the transliteration is the one the PostGIS
    table names use (``æ``→``ae``, ``ø``→``oe``, ``å``→``aa``).
    """
    value = str(name or "")
    # "administrativEnhet" -> "administrativ Enhet", "FKBBygning" -> "FKB Bygning".
    value = re.sub(r"(?<=[a-zæøå0-9])(?=[A-ZÆØÅ])", " ", value)
    value = re.sub(r"(?<=[A-ZÆØÅ])(?=[A-ZÆØÅ][a-zæøå])", " ", value)
    return pg_name(value).replace("_", "-")


def unique_key(name: str, taken: dict[str, Any]) -> str:
    """The collection id for ``name``, suffixed ``-2``, ``-3``, … when taken."""
    base = resource_name(name)
    key, suffix = base, 2
    while key in taken:
        key, suffix = f"{base}-{suffix}", suffix + 1
    return key


# --------------------------------------------------------------------------- #
# psdata
# --------------------------------------------------------------------------- #


def path(psdata: Any, dotted: str) -> Any:
    """The value at ``dotted`` (``"identificationSection.title"``), or ``None``."""
    value: Any = psdata
    for key in dotted.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def string(psdata: Any, dotted: str, missing: str = PLACEHOLDER) -> str:
    """The text at ``dotted``, or ``missing`` when there is none."""
    return text(path(psdata, dotted)) or missing


def contact(psdata: Any, role: str) -> dict[str, Any]:
    """The first ``identificationSection.contact`` entry with ``role``."""
    contacts = path(psdata, "identificationSection.contact")
    for entry in contacts if isinstance(contacts, list) else []:
        if isinstance(entry, dict) and text(entry.get("role")).casefold() == role.casefold():
            return entry
    return {}


def keywords(psdata: Any) -> list[str]:
    """The dataset's keywords; empty when psdata has none."""
    value = path(psdata, "identificationSection.keyword")
    items = value if isinstance(value, list) else [value]
    return [word for item in items if (word := text(item))]


def dataset_bbox(psdata: Any) -> list[float | str]:
    """The dataset's geographic extent as ``[west, south, east, north]``.

    psdata holds the coordinates as strings; the configurations want numbers. A
    missing or unreadable coordinate is left for the user to fill in.
    """
    element = mapping(path(psdata, "identificationSection.extent.geographicElement"))
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


# --------------------------------------------------------------------------- #
# Feature types
# --------------------------------------------------------------------------- #


def feature_keywords(ft: dict[str, Any], title: str) -> list[str]:
    """The feature type's own keywords, else its title."""
    value = ft.get("keywords")
    items = value if isinstance(value, list) else [value]
    return [word for item in items if (word := text(item))] or [title]
