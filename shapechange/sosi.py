"""SOSI-specific ShapeChange configuration, derived from Kartverket's EA add-in.

`kartverket/ShapeChange-Add-In <https://github.com/kartverket/ShapeChange-Add-In>`_
is a Windows-only Enterprise Architect add-in written in C#, so none of it runs on
a Linux runner. It does, however, carry two pieces of plain, portable ShapeChange
configuration that are *not* part of stock ShapeChange, and that are what make its
output look like the application schemas published on ``skjema.geonorge.no``:

* an ``<EncodingRule name="sosi">`` definition (its ``config/StandardRules.xml``);
* a map-entry file for the Norwegian SOSI primitive and geometry type names,
  vendored here as ``data/StandardMapEntries_sosi.xml``.

Every individual ``rule-*`` below is a stock ShapeChange rule -- only the grouping
is Kartverket's -- so no patched ShapeChange is required. All six XML Schema rules
were checked against the ShapeChange 4.0.0 documentation.
"""
from __future__ import annotations

from pathlib import Path

__all__ = [
    "SOSI_XSD_ENCODING_RULE",
    "SOSI_XSD_RULES",
    "SOSI_JSON_ENCODING_RULE",
    "SOSI_JSON_RULES",
    "SOSI_JSON_MAP_ENTRIES",
    "SOSI_TAGGED_VALUES",
    "sosi_map_entries_path",
    "custom_xsd_rules",
    "custom_json_rules",
]

SOSI_XSD_ENCODING_RULE = "sosi"
SOSI_JSON_ENCODING_RULE = "sosiJson"

# Verbatim from Kartverket's config/StandardRules.xml. The repo defines a second,
# byte-identical rule named "sosi50"; both names are accepted here.
#
# Two of these are what close the gap against the published Geonorge schemas:
#   rule-xsd-all-tagged-values     -> <appinfo><sc:taggedValue tag="SOSI_navn">
#                                     (requires representTaggedValues in <input>)
#   rule-xsd-prop-targetCodeListURI -> <appinfo><sc:targetCodeListURI> carrying the
#                                     code list's registry URI, which survives
#                                     regardless of how the code list itself is encoded
SOSI_XSD_RULES: tuple[str, ...] = (
    "rule-xsd-prop-length-size-pattern",
    "rule-xsd-all-tagged-values",
    "rule-xsd-all-notEncoded",
    "rule-xsd-pkg-schematron",
    "rule-xsd-prop-nillable",
    "rule-xsd-prop-targetCodeListURI",
)

# ShapeChange encoding rules can only ADD rules -- "extends" has no counterpart that
# removes one. That matters here: both built-in JSON rules (defaultGeoJson and
# defaultPlainJson) include rule-json-cls-name-as-anchor, which encodes every class
# name as a JSON Schema "$anchor". JSON Schema requires anchors to match
# ^[A-Za-z][-A-Za-z0-9.:_]*$, so a Norwegian name such as "Målemetode" produces a
# document that fails strict metaschema validation.
#
# Kartverket sidesteps this by defining their JSON rule WITHOUT "extends". The list
# below does the same, starting from their three rules and adding back what is
# needed for geometry, identifiers and documentation.
SOSI_JSON_RULES: tuple[str, ...] = (
    # Kartverket's defaultJson
    "rule-json-cls-basictype",
    "rule-json-cls-codelist-uri-format",
    "rule-json-cls-name-as-entityType",
    # Added back: without these the output loses geometry, identity and docs.
    "rule-json-cls-primaryGeometry",
    "rule-json-cls-identifierForTypeWithIdentity",
    "rule-json-cls-union-propertyCount",
    "rule-json-cls-valueTypeOptions",
    "rule-json-all-documentation",
)

# Kartverket maps the SOSI geometry type names to GeoJSON schemas in the JSON target.
# Each entry is (type, targetType); they are written with rule="*" and param="geometry".
SOSI_JSON_MAP_ENTRIES: tuple[tuple[str, str], ...] = (
    ("Punkt", "https://geojson.org/schema/Point.json"),
    ("Kurve", "https://geojson.org/schema/LineString.json"),
    ("Flate", "https://geojson.org/schema/Polygon.json"),
    ("Sverm", "https://geojson.org/schema/MultiPoint.json"),
)

# Kartverket's add-in passes these as addTaggedValues; SOSI_navn is the one that
# shows up as sc:taggedValue appinfo in the published Geonorge schemas.
SOSI_TAGGED_VALUES: tuple[str, ...] = ("SOSI_navn", "SOSI_verdi", "NVDB_ID")

_SOSI_RULE_NAMES = {"sosi", "sosi50"}


def sosi_map_entries_path() -> Path:
    """Absolute path to the vendored SOSI map-entry file."""
    return (Path(__file__).resolve().parent / "data" / "StandardMapEntries_sosi.xml")


def custom_xsd_rules(encoding_rule: str) -> tuple[str, ...] | None:
    """Return the rules to define for ``encoding_rule``, or ``None``.

    ``None`` means the name refers to an encoding rule ShapeChange already knows
    (``iso19136_2007``, ``gml33``, ...), so the configuration must not define it.
    """
    if encoding_rule.strip().lower() in _SOSI_RULE_NAMES:
        return SOSI_XSD_RULES
    return None


def custom_json_rules(encoding_rule: str) -> tuple[str, ...] | None:
    """Return the rules to define for ``encoding_rule``, or ``None``.

    ``defaultGeoJson`` and ``defaultPlainJson`` are pre-configured in ShapeChange
    and must not be redefined.
    """
    if encoding_rule.strip() == SOSI_JSON_ENCODING_RULE:
        return SOSI_JSON_RULES
    return None
