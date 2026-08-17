"""Apply user-authored overrides onto a freshly fetched feature catalogue.

The feature catalogue is re-fetched from source (OGC / XMI / GeoPackage) and
overwritten on every run, so edits made in the web app are stored in a separate,
committed overrides file and merged here. The source stays authoritative for the
model *structure*; the overrides supply *text* (v1: descriptions) that would
otherwise be lost on the next generation.

Overrides file shape (v1)::

    {
      "version": 1,
      "featureTypes": {
        "Bygning": {
          "description": "...",
          "attributes": { "bygningsnavn": { "description": "..." } }
        }
      }
    }

Only non-empty overrides are applied; unknown feature-type/attribute names and
blank values are ignored, so the source value is kept.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

__all__ = ["load_overrides", "apply_overrides"]


def load_overrides(path: str | Path) -> dict[str, Any]:
    """Read an overrides JSON file.

    Returns ``{}`` when the file is missing or unreadable -- a bad overrides file
    must never fail the generation, only leave the catalogue untouched.
    """
    file = Path(path)
    if not file.exists():
        return {}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _clean(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _apply_attribute_overrides(
    attributes: Any, attr_overrides: Mapping[str, Any]
) -> Any:
    if not isinstance(attributes, list):
        return attributes
    result: list[Any] = []
    for attribute in attributes:
        if isinstance(attribute, dict):
            name = attribute.get("name")
            override = attr_overrides.get(name) if isinstance(name, str) else None
            if isinstance(override, Mapping):
                description = _clean(override.get("description"))
                if description is not None:
                    attribute = {**attribute, "description": description}
        result.append(attribute)
    return result


def apply_overrides(
    feature_types: list[dict[str, Any]], overrides: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Return a copy of ``feature_types`` with override text applied.

    v1 overrides only descriptions, keyed by feature-type name -> attribute name.
    The input is not mutated.
    """
    ft_overrides = overrides.get("featureTypes") if isinstance(overrides, Mapping) else None
    if not isinstance(ft_overrides, Mapping) or not ft_overrides:
        return feature_types

    result: list[dict[str, Any]] = []
    for feature_type in feature_types:
        if not isinstance(feature_type, dict):
            result.append(feature_type)
            continue
        name = feature_type.get("name")
        override = ft_overrides.get(name) if isinstance(name, str) else None
        if not isinstance(override, Mapping):
            result.append(feature_type)
            continue

        merged = dict(feature_type)
        description = _clean(override.get("description"))
        if description is not None:
            merged["description"] = description

        attribute_overrides = override.get("attributes")
        if isinstance(attribute_overrides, Mapping) and attribute_overrides:
            merged["attributes"] = _apply_attribute_overrides(
                merged.get("attributes"), attribute_overrides
            )
        result.append(merged)
    return result
