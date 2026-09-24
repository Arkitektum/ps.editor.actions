"""Rule-driven format realisation for GeoPackage.

A GeoPackage feature table has exactly one geometry column, but a SOSI UML class
may carry several geometry properties (a surface and a point, say). The model and
the format therefore cannot match one to one, and a choice has to be made.

This module implements the chosen rule: one feature type per geometry, named with
a Norwegian suffix for the geometry kind. ``Bygning`` with a surface and a point
becomes ``Bygning_flate`` and ``Bygning_punkt``. That is a deliberate deviation
from the UML model, and the realised model returned here is what a product
specification document should describe.

Associations between the realised types are out of scope; ``relationships`` is
copied unchanged onto every part.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "GEOMETRY_SUFFIXES",
    "geometry_suffix",
    "split_multi_geometry_types",
]

# GeoPackage geometry type -> the suffix that names the realised feature type.
# "geometri" covers the unspecific types (GM_Object, GM_Primitive), which carry no
# usable shape name of their own.
GEOMETRY_SUFFIXES: dict[str, str] = {
    "POINT": "punkt",
    "MULTIPOINT": "punkt",
    "LINESTRING": "linje",
    "MULTILINESTRING": "linje",
    "POLYGON": "flate",
    "MULTIPOLYGON": "flate",
    "GEOMETRY": "geometri",
    "GEOMETRYCOLLECTION": "geometri",
}


def _geometry_kind(type_name: Any) -> str | None:
    """GeoPackage geometry type for a model type name, or ``None``."""
    # Imported lazily: the writer imports this module, so a module-level import
    # would be circular.
    from geopackage.writer import _GM_GEOM, _GPKG_GEOM, _SOSI_GEOM

    raw = str(type_name or "").strip().lower()
    if not raw:
        return None
    return _GM_GEOM.get(raw) or _SOSI_GEOM.get(raw) or _GPKG_GEOM.get(raw)


def geometry_suffix(type_name: Any) -> str | None:
    """Suffix for a geometry type name, e.g. ``GM_Surface`` -> ``flate``."""
    kind = _geometry_kind(type_name)
    return GEOMETRY_SUFFIXES.get(kind) if kind else None


def _geometry_attributes(feature_type: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    attributes = feature_type.get("attributes")
    if not isinstance(attributes, Sequence) or isinstance(attributes, (str, bytes)):
        return []
    return [
        attribute
        for attribute in attributes
        if isinstance(attribute, Mapping) and _geometry_kind(attribute.get("type"))
    ]


def split_multi_geometry_types(
    feature_types: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Split every feature type that carries more than one geometry.

    Each part keeps one geometry property and all of the non-geometry ones. A
    feature type with a single geometry, or none, is returned unchanged.

    The suffix comes from the geometry kind. When two geometries would produce
    the same suffix -- a point and a multipoint, say -- the property name is used
    instead, so the names stay unique and predictable.
    """
    if not isinstance(feature_types, Sequence) or isinstance(feature_types, (str, bytes)):
        raise TypeError("feature_types must be a sequence of mappings")

    realised: list[dict[str, Any]] = []
    for feature_type in feature_types:
        if not isinstance(feature_type, Mapping):
            continue

        geometries = _geometry_attributes(feature_type)
        if len(geometries) < 2:
            realised.append(dict(feature_type))
            continue

        suffixes = [geometry_suffix(entry.get("type")) or "geometri" for entry in geometries]
        duplicated = {suffix for suffix in suffixes if suffixes.count(suffix) > 1}

        base_name = str(feature_type.get("name") or "").strip()
        geometry_ids = {id(entry) for entry in geometries}
        others = [
            attribute
            for attribute in feature_type.get("attributes") or []
            if id(attribute) not in geometry_ids
        ]

        for geometry, suffix in zip(geometries, suffixes):
            if suffix in duplicated:
                suffix = str(geometry.get("name") or suffix).strip() or suffix
            part = dict(feature_type)
            part["name"] = f"{base_name}_{suffix}" if base_name else suffix
            part["attributes"] = [*others, geometry]
            # Recorded so a post-process can tell a realised type from a modelled
            # one, and trace it back to the class it came from.
            part["realisedFrom"] = base_name
            realised.append(part)

    return realised
