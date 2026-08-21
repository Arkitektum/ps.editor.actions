"""Build a ShapeChange configuration document.

Modelled on the configuration that the ShapeChange distribution itself uses to
smoke-test a release (``test/scxml/testSCXML.xml``), so it is known to work with
ShapeChange 4.x.

Two defaults in ShapeChange are actively dangerous and are always overridden
here: ``inputModelType`` defaults to the deprecated ``XMI10``, and ``inputFile``
defaults to a demo model hosted on shapechange.net -- a configuration that omits
both "succeeds" against someone else's schema.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from xml.etree import ElementTree as ET

from shapechange.sosi import (
    SOSI_JSON_ENCODING_RULE,
    SOSI_JSON_MAP_ENTRIES,
    SOSI_TAGGED_VALUES,
    SOSI_XSD_ENCODING_RULE,
    custom_json_rules,
    custom_xsd_rules,
    sosi_map_entries_path,
)

__all__ = [
    "build_config",
    "write_config",
    "XML_SCHEMA_TARGET_CLASS",
    "JSON_SCHEMA_TARGET_CLASS",
    "LDPROXY_TARGET_CLASS",
]

CONFIG_NS = "http://www.interactive-instruments.de/ShapeChange/Configuration/1.1"
XI_NS = "http://www.w3.org/2001/XInclude"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
CONFIG_SCHEMA_LOCATION = (
    "http://www.interactive-instruments.de/ShapeChange/Configuration/1.1 "
    "https://shapechange.net/resources/schema/ShapeChangeConfiguration.xsd"
)

# Class names changed in ShapeChange 4.0.0:
#   de.interactive_instruments.ShapeChange.Target.XmlSchema.XmlSchema
#     -> de.interactive_instruments.shapechange.core.target.xmlschema.XmlSchema
# Configurations written for 2.x/3.x fail with ClassNotFoundException on 4.x,
# so both are exposed as overridable inputs.
XML_SCHEMA_TARGET_CLASS = (
    "de.interactive_instruments.shapechange.core.target.xmlschema.XmlSchema"
)
JSON_SCHEMA_TARGET_CLASS = (
    "de.interactive_instruments.shapechange.core.target.json.JsonSchemaTarget"
)
# ldproxy configuration target (ShapeChange 4.x). Emits an ldproxy 3.x entities/store
# (SQL/PostGIS feature provider + OGC API service + codelist configs) -- a starting
# template for a PostGIS deployment (no GeoPackage provider support in ldproxy today).
LDPROXY_TARGET_CLASS = (
    "de.interactive_instruments.shapechange.core.target.ldproxy2.Ldproxy2Target"
)
# Key ldproxy2 encoding rules (extends the universal '*' rule set).
_LDPROXY_RULES = ("rule-ldp2-all-associativeTablesWithSeparatePkField",)

_REMOTE_CONFIG_BASE = "https://shapechange.net/resources/config"
_BUNDLED_CONFIG_BASE = "config"


# Regex metacharacters that must be escaped for the pattern to match literally.
# Python's re.escape also escapes spaces, which is legal but noisy in a Java
# regular expression, so the set is spelled out here.
_REGEX_METACHARACTERS = set(r".^$*+?()[]{}|\\")


def _quote_regex(value: str) -> str:
    return "".join(
        f"\\{char}" if char in _REGEX_METACHARACTERS else char for char in value
    )


def _sub(parent: ET.Element, tag: str) -> ET.Element:
    return ET.SubElement(parent, f"{{{CONFIG_NS}}}{tag}")


def _parameter(parent: ET.Element, name: str, value: str) -> None:
    element = _sub(parent, "parameter")
    element.set("name", name)
    element.set("value", value)


def _target_parameter(parent: ET.Element, name: str, value: str) -> None:
    element = _sub(parent, "targetParameter")
    element.set("name", name)
    element.set("value", value)


def _include(parent: ET.Element, base: str, filename: str) -> None:
    element = ET.SubElement(parent, f"{{{XI_NS}}}include")
    element.set("href", f"{base}/{filename}")


def _include_path(parent: ET.Element, path: Path) -> None:
    element = ET.SubElement(parent, f"{{{XI_NS}}}include")
    element.set("href", path.as_uri())


def _json_map_entries(
    parent: ET.Element, entries: Sequence[tuple[str, str]]
) -> None:
    """Map the SOSI geometry type names onto GeoJSON schemas."""
    container = _sub(parent, "mapEntries")
    for type_name, target_type in entries:
        entry = _sub(container, "MapEntry")
        entry.set("type", type_name)
        entry.set("rule", "*")
        entry.set("targetType", target_type)
        entry.set("param", "geometry")


def _encoding_rules(
    parent: ET.Element, name: str, rules: Sequence[str], extends: str | None = None
) -> None:
    """Define an encoding rule inside a target element.

    Only written for rule names ShapeChange does not already know -- redefining a
    built-in such as ``iso19136_2007`` or ``defaultGeoJson`` is an error.
    """
    container = _sub(parent, "rules")
    encoding_rule = _sub(container, "EncodingRule")
    encoding_rule.set("name", name)
    if extends:
        encoding_rule.set("extends", extends)
    for rule_name in rules:
        rule = _sub(encoding_rule, "rule")
        rule.set("name", rule_name)


def build_config(
    *,
    model_path: Path,
    log_path: Path,
    xsd_directory: Path,
    json_directory: Path,
    app_schema_name: str,
    ldproxy_directory: Path | None = None,
    ldproxy_srid: int = 25833,
    ldproxy_native_timezone: str = "Europe/Oslo",
    ldproxy_primary_key_column: str = "objid",
    ldproxy_target_class: str = LDPROXY_TARGET_CLASS,
    targets: Sequence[str] = ("xsd", "json"),
    xsd_encoding_rule: str = SOSI_XSD_ENCODING_RULE,
    json_schema_version: str = "2019-09",
    json_base_uri: str = "",
    json_encoding_rule: str = SOSI_JSON_ENCODING_RULE,
    entity_type_name: str = "@type",
    xml_schema_target_class: str = XML_SCHEMA_TARGET_CLASS,
    json_schema_target_class: str = JSON_SCHEMA_TARGET_CLASS,
    bundled_includes: bool = False,
    represent_tagged_values: Sequence[str] = SOSI_TAGGED_VALUES,
    report_level: str = "INFO",
) -> ET.ElementTree:
    """Build the ShapeChange configuration for an SCXML input model.

    All file references are written as absolute paths. ShapeChange resolves
    relative paths against the JVM working directory -- not against the location
    of the configuration file -- and the caller runs the jar from the unpacked
    distribution directory, so relative paths would break.
    """
    selected = {target.strip().lower() for target in targets if target and target.strip()}
    unknown = selected - {"xsd", "json", "ldproxy"}
    if unknown:
        raise ValueError(f"Unknown ShapeChange target(s): {', '.join(sorted(unknown))}.")
    if not selected:
        raise ValueError("At least one ShapeChange target must be selected.")

    include_base = _BUNDLED_CONFIG_BASE if bundled_includes else _REMOTE_CONFIG_BASE

    # The configuration namespace must be the *default* namespace: ShapeChange
    # looks the elements up by tag name without a prefix, and a prefixed
    # document makes it abort with a NullPointerException on a null <input>.
    ET.register_namespace("", CONFIG_NS)
    ET.register_namespace("xi", XI_NS)
    ET.register_namespace("xsi", XSI_NS)

    root = ET.Element(f"{{{CONFIG_NS}}}ShapeChangeConfiguration")
    root.set(f"{{{XSI_NS}}}schemaLocation", CONFIG_SCHEMA_LOCATION)

    input_element = _sub(root, "input")
    input_element.set("id", "INPUT")
    _parameter(input_element, "inputModelType", "SCXML")
    _parameter(input_element, "inputFile", str(model_path))
    _parameter(input_element, "appSchemaNameRegex", f"^{_quote_regex(app_schema_name)}$")
    _parameter(input_element, "publicOnly", "true")
    _parameter(input_element, "checkingConstraints", "disabled")
    _parameter(input_element, "sortedSchemaOutput", "true")
    _parameter(input_element, "addTaggedValues", "*")
    # rule-xsd-all-tagged-values only emits the tags listed here as sc:taggedValue
    # appinfo, which is how SOSI_navn reaches the XSD.
    tags = [tag.strip() for tag in represent_tagged_values if tag and tag.strip()]
    if tags:
        _parameter(input_element, "representTaggedValues", ",".join(tags))
    _include(input_element, include_base, "StandardAliases.xml")

    log_element = _sub(root, "log")
    _parameter(log_element, "reportLevel", report_level)
    _parameter(log_element, "logFile", str(log_path))

    targets_element = _sub(root, "targets")

    if "xsd" in selected:
        # The XML Schema target uses its own element name because it carries
        # <xsdMapEntries>; the JSON target uses the generic <Target>.
        xsd_target = _sub(targets_element, "TargetXmlSchema")
        xsd_target.set("class", xml_schema_target_class)
        xsd_target.set("mode", "enabled")
        xsd_target.set("inputs", "INPUT")
        _target_parameter(xsd_target, "outputDirectory", str(xsd_directory))
        _target_parameter(xsd_target, "sortedOutput", "true")
        _target_parameter(xsd_target, "defaultEncodingRule", xsd_encoding_rule)
        # Bruk 'documentation'-deskriptoren (som SCXML-eksporten skriver) i XSD-annotasjoner.
        # ShapeChange sin default er '[[definition]]', som er tom i vår modell -> ingen definisjoner.
        _target_parameter(xsd_target, "documentationTemplate", "[[documentation]]")
        _target_parameter(xsd_target, "documentationNoValue", "")
        xsd_rules = custom_xsd_rules(xsd_encoding_rule)
        if xsd_rules:
            _encoding_rules(
                xsd_target, xsd_encoding_rule, xsd_rules, extends="iso19136_2007"
            )
        _include(xsd_target, include_base, "StandardRules.xml")
        _include(xsd_target, include_base, "StandardNamespaces.xml")
        _include(xsd_target, include_base, "StandardMapEntries.xml")
        if xsd_rules:
            # Last include wins, so the SOSI type names override the standard
            # ISO map entries where they overlap.
            _include_path(xsd_target, sosi_map_entries_path())

    if "json" in selected:
        json_target = _sub(targets_element, "Target")
        json_target.set("class", json_schema_target_class)
        json_target.set("mode", "enabled")
        json_target.set("inputs", "INPUT")
        _target_parameter(json_target, "outputDirectory", str(json_directory))
        _target_parameter(json_target, "sortedOutput", "true")
        _target_parameter(json_target, "jsonSchemaVersion", json_schema_version)
        if json_base_uri:
            _target_parameter(json_target, "jsonBaseUri", json_base_uri)
        _target_parameter(json_target, "entityTypeName", entity_type_name)
        _target_parameter(json_target, "defaultEncodingRule", json_encoding_rule)
        # Samme som XSD: hent definisjonene fra 'documentation'-deskriptoren.
        _target_parameter(json_target, "documentationTemplate", "[[documentation]]")
        _target_parameter(json_target, "documentationNoValue", "")
        json_rules = custom_json_rules(json_encoding_rule)
        if json_rules:
            # Deliberately no "extends": both built-in JSON rules pull in
            # rule-json-cls-name-as-anchor, which emits $anchor values that are
            # invalid for non-ASCII class names.
            _encoding_rules(json_target, json_encoding_rule, json_rules)
            _json_map_entries(json_target, SOSI_JSON_MAP_ENTRIES)
        _include(json_target, include_base, "StandardMapEntries_JSON.xml")

    if "ldproxy" in selected and ldproxy_directory is not None:
        # ldproxy 3.x entities/store: SQL/PostGIS feature provider + OGC API service +
        # codelist configs. Utkast/startmal — provider-mappingen antar et PostGIS-skjema
        # (synteserer tabell-/kolonnenavn fra modellen via navnekonvensjoner). ldproxy har
        # ingen GeoPackage-provider i dag.
        ldp_target = _sub(targets_element, "Target")
        ldp_target.set("class", ldproxy_target_class)
        ldp_target.set("mode", "enabled")
        ldp_target.set("inputs", "INPUT")
        _target_parameter(ldp_target, "outputDirectory", str(ldproxy_directory))
        _target_parameter(ldp_target, "srid", str(ldproxy_srid))
        # Gistools/PostGIS-etablering bruker «objid» som primærnøkkel (ShapeChange-default
        # er «id»). Jf. https://arkitektum.atlassian.net/wiki/spaces/gistools/pages/524373
        if ldproxy_primary_key_column:
            _target_parameter(ldp_target, "primaryKeyColumn", ldproxy_primary_key_column)
        if ldproxy_native_timezone:
            _target_parameter(ldp_target, "nativeTimeZone", ldproxy_native_timezone)
        _target_parameter(ldp_target, "defaultEncodingRule", "ldproxy2")
        _target_parameter(ldp_target, "documentationTemplate", "[[documentation]]")
        _target_parameter(ldp_target, "documentationNoValue", "")
        _encoding_rules(ldp_target, "ldproxy2", _LDPROXY_RULES, extends="*")
        # Kun ldproxy-map-entriene: de mapper til ldproxy sitt verditype-domene
        # (STRING/GEOMETRY/…). SQL-DDL-map-entriene (MULTIPOINT/POINT/…) er IKKE gyldige
        # for Ldproxy2Target og får den semantiske valideringen til å stoppe kjøringen.
        _include(ldp_target, include_base, "StandardMapEntries_Ldproxy2.xml")

    tree = ET.ElementTree(root)
    ET.indent(tree, space=" ")
    return tree


def write_config(output_path: str | Path, **kwargs: object) -> Path:
    """Build the configuration and write it to ``output_path``."""
    tree = build_config(**kwargs)  # type: ignore[arg-type]
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(path, encoding="UTF-8", xml_declaration=True)
    return path
