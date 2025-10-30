"""Render feature type metadata to PlantUML diagrams."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from html import unescape
from pathlib import Path
from typing import Any

__all__ = ["render_feature_types_to_puml", "main"]


_TYPE_MAPPING: dict[str, str] = {
    "string": "CharacterString",
    "integer": "Integer",
    "number": "Real",
    "boolean": "Boolean",
    "array": "Sequence",
    "object": "Object",
    "unknown": "Any",
}

_GEOMETRY_MAPPING: dict[str, str] = {
    "point": "GM_Point",
    "linestring": "GM_Curve",
    "curve": "GM_Curve",
    "line": "GM_Curve",
    "polygon": "GM_Surface",
    "surface": "GM_Surface",
    "multipoint": "GM_MultiPoint",
    "multilinestring": "GM_MultiCurve",
    "multicurve": "GM_MultiCurve",
    "multipolygon": "GM_MultiSurface",
    "multisurface": "GM_MultiSurface",
    "geometrycollection": "GM_Object",
}

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def render_feature_types_to_puml(
    feature_types: Sequence[Mapping[str, Any]],
    *,
    title: str | None = None,
    package: str | None = None,
    include_notes: bool = True,
    include_descriptions: bool = True,
) -> str:
    """Convert feature type metadata into a PlantUML class diagram."""

    if not isinstance(feature_types, Sequence) or isinstance(feature_types, (str, bytes)):
        raise TypeError("feature_types must be a sequence of mappings")

    lines: list[str] = []
    lines.append("@startuml")
    if title:
        lines.append(f"title {title}")
        lines.append("")

    lines.extend(
        [
            "skinparam class {",
            "  AttributeIconSize 0",
            "}",
            "",
        ]
    )

    indent = ""
    if package:
        lines.append(f'package "{package}" {{')
        lines.append("")
        indent = "  "

    for index, feature_type in enumerate(feature_types):
        if not isinstance(feature_type, Mapping):
            raise TypeError("Each feature type entry must be a mapping")
        if index:
            lines.append("")

        _append_feature_type(
            lines,
            feature_type,
            indent,
            include_notes=include_notes,
            include_descriptions=include_descriptions,
        )

    if package:
        lines.append("}")

    lines.append("")
    lines.append("@enduml")

    return "\n".join(lines)


def _append_feature_type(
    lines: list[str],
    feature_type: Mapping[str, Any],
    indent: str,
    *,
    include_notes: bool,
    include_descriptions: bool,
) -> None:
    name = str(feature_type.get("name", "UnnamedFeature"))
    class_header = _build_class_header(name)
    class_alias = class_header.split(" as ")[-1] if " as " in class_header else name

    lines.append(f"{indent}class {class_header} <<featureType>> {{")

    attributes_obj = feature_type.get("attributes")
    attribute_entries = _collect_attribute_entries(attributes_obj)

    nested_object_attributes = _append_attributes(
        lines,
        attribute_entries,
        indent,
        include_descriptions=include_descriptions,
        prefix="",
    )

    lines.append(f"{indent}}}")

    if not include_notes:
        return

    note_lines = _build_note_lines(feature_type)
    if note_lines:
        lines.append(f"{indent}note right of {class_alias}")
        for note_line in note_lines:
            lines.append(f"{indent}  {note_line}")
        lines.append(f"{indent}end note")

    if not nested_object_attributes:
        return

    nested_class_blocks: list[list[str]] = []
    association_lines: list[str] = []

    for attribute, attribute_prefix in nested_object_attributes:
        blocks, relations = _build_nested_object_classes(
            attribute,
            class_alias,
            indent,
            include_descriptions=include_descriptions,
            prefix=attribute_prefix,
        )
        nested_class_blocks.extend(blocks)
        association_lines.extend(relations)

    if association_lines or nested_class_blocks:
        lines.append("")

    for relation in association_lines:
        lines.append(relation)

    if association_lines and nested_class_blocks:
        lines.append("")

    for index, block in enumerate(nested_class_blocks):
        lines.extend(block)
        if index != len(nested_class_blocks) - 1:
            lines.append("")


def _append_attributes(
    lines: list[str],
    attributes: Sequence[Mapping[str, Any]] | None,
    indent: str,
    *,
    include_descriptions: bool,
    prefix: str = "",
) -> list[tuple[Mapping[str, Any], str]]:
    regular_lines: list[str] = []
    geometry_lines: list[str] = []
    nested_object_attributes: list[tuple[Mapping[str, Any], str]] = []

    if attributes:
        for attribute in attributes:
            if not isinstance(attribute, Mapping):
                continue

            attribute_name = str(attribute.get("name", ""))
            attribute_prefix = _combine_attribute_prefix(prefix, attribute_name)
            raw_type = str(attribute.get("type", "unknown"))
            target = geometry_lines if raw_type.lower().startswith("geometry-") else regular_lines
            target.append(
                _render_attribute_line(
                    attribute,
                    indent,
                    include_descriptions=include_descriptions,
                    prefix=prefix,
                )
            )

            if _is_object_with_attributes(attribute):
                nested_object_attributes.append((attribute, attribute_prefix))

    if not regular_lines and not geometry_lines:
        lines.append(f"{indent}  ' Ingen attributter")
        return []

    for entry in regular_lines:
        lines.append(entry)

    if geometry_lines:
        if regular_lines:
            lines.append("")
        lines.append(f"{indent}  ..Geometri..")
        for entry in geometry_lines:
            lines.append(entry)

    return nested_object_attributes


def _render_attribute_line(
    attribute: Mapping[str, Any],
    indent: str,
    *,
    include_descriptions: bool,
    prefix: str = "",
) -> str:
    raw_name = str(attribute.get("name", ""))
    name = _combine_attribute_prefix(prefix, raw_name)
    raw_type = str(attribute.get("type", "unknown"))
    uml_type = _map_type(raw_type)
    cardinality = _format_cardinality(attribute)
    description = attribute.get("description")

    suffix = ""
    if include_descriptions and isinstance(description, str):
        desc_text = _clean_inline_text(description)
        if desc_text:
            suffix = f"  ' {desc_text}"

    level_indent = indent + "  "
    cardinality_segment = f" [{cardinality}]" if cardinality else ""
    return f"{level_indent}+ {name}{cardinality_segment} : {uml_type}{suffix}"


def _collect_attribute_entries(attributes_obj: Any) -> list[Mapping[str, Any]]:
    if isinstance(attributes_obj, Sequence) and not isinstance(attributes_obj, (str, bytes)):
        return [
            attribute
            for attribute in attributes_obj
            if isinstance(attribute, Mapping)
        ]
    return []


def _combine_attribute_prefix(prefix: str, name: str) -> str:
    name = name.strip()
    if prefix and name:
        return f"{prefix}.{name}"
    return prefix or name


def _is_object_with_attributes(attribute: Mapping[str, Any]) -> bool:
    raw_type = str(attribute.get("type", "")).lower()
    if raw_type != "object":
        return False
    child_attributes = _collect_attribute_entries(attribute.get("attributes"))
    return bool(child_attributes)


def _derive_nested_class_name(parent_alias: str, attribute_name: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_]", "_", attribute_name) or "Attribute"
    if sanitized[0].isdigit():
        sanitized = f"_{sanitized}"
    return f"{parent_alias}_{sanitized}"


def _build_nested_object_classes(
    attribute: Mapping[str, Any],
    parent_alias: str,
    indent: str,
    *,
    include_descriptions: bool,
    prefix: str | None = None,
) -> tuple[list[list[str]], list[str]]:
    attribute_name = str(attribute.get("name", "attribute"))
    class_name = _derive_nested_class_name(parent_alias, attribute_name)
    class_header = _build_class_header(class_name)
    child_alias = class_header.split(" as ")[-1] if " as " in class_header else class_name

    child_attributes = _collect_attribute_entries(attribute.get("attributes"))
    attribute_prefix = prefix if prefix is not None else _combine_attribute_prefix("", attribute_name)

    class_lines: list[str] = [f"{indent}class {class_header} {{"]
    child_nested_attributes = _append_attributes(
        class_lines,
        child_attributes,
        indent,
        include_descriptions=include_descriptions,
        prefix=attribute_prefix,
    )
    class_lines.append(f"{indent}}}")

    class_blocks: list[list[str]] = [class_lines]
    relation_label = attribute_name
    cardinality = _format_cardinality(attribute)
    if cardinality:
        relation_label = f"{relation_label} [{cardinality}]"
    relations = [f"{indent}{parent_alias} *-- {child_alias} : {relation_label}"]

    for nested_attribute, nested_prefix in child_nested_attributes:
        nested_blocks, nested_relations = _build_nested_object_classes(
            nested_attribute,
            child_alias,
            indent,
            include_descriptions=include_descriptions,
            prefix=nested_prefix,
        )
        class_blocks.extend(nested_blocks)
        relations.extend(nested_relations)

    return class_blocks, relations


def _build_class_header(name: str) -> str:
    if _IDENTIFIER_RE.match(name):
        return name

    alias = re.sub(r"[^A-Za-z0-9_]", "_", name) or "FeatureType"
    return f'"{name}" as {alias}'


def _map_type(raw_type: str) -> str:
    key = raw_type.strip().lower()

    if key.startswith("date-time"):
        return "DateTime"
    if key.startswith("date"):
        return "Date"

    if key.startswith("geometry-"):
        geometry_key = key.split("-", 1)[1]
        return _GEOMETRY_MAPPING.get(geometry_key, "GM_Object")

    return _TYPE_MAPPING.get(key, "Any")


def _format_cardinality(attribute: Mapping[str, Any]) -> str:
    value = attribute.get("cardinality")
    if value is None:
        return ""

    if isinstance(value, str):
        text = value.strip()
        return text

    return str(value).strip()


def _build_note_lines(feature_type: Mapping[str, Any]) -> list[str]:
    description = feature_type.get("description")
    geometry = feature_type.get("geometry")

    lines: list[str] = []
    if isinstance(description, str):
        description_lines = _clean_multiline_text(description)
        lines.extend(description_lines)

    geometry_lines = _build_geometry_note_lines(geometry)
    if geometry_lines:
        if lines:
            lines.append("")
        lines.extend(geometry_lines)

    return lines


def _build_geometry_note_lines(geometry: Any) -> list[str]:
    if not isinstance(geometry, Mapping):
        return []

    lines: list[str] = []
    geom_type = geometry.get("type")
    if isinstance(geom_type, str) and geom_type and geom_type.lower() != "feature":
        lines.append(f"Type: {geom_type}")

    storage_crs = geometry.get("storageCrs")
    if isinstance(storage_crs, str) and storage_crs:
        lines.append(f"Storage CRS: {storage_crs}")

    crs = geometry.get("crs")
    if isinstance(crs, Sequence) and not isinstance(crs, (str, bytes)):
        crs_values = [str(value) for value in crs if isinstance(value, str) and value.strip()]
        if crs_values:
            lines.append(f"CRS: {', '.join(crs_values)}")

    return lines


def _clean_inline_text(text: str) -> str:
    cleaned = " ".join(_clean_multiline_text(text))
    return cleaned.replace("'", "’")


def _clean_multiline_text(text: str) -> list[str]:
    text = unescape(text)
    text = text.replace("<br />", "\n").replace("<br/>", "\n").replace("<br>", "\n")
    text = re.sub(r"<[^>]+>", "", text)
    lines = [segment.strip() for segment in text.splitlines()]
    return [line for line in lines if line]


def main(argv: Sequence[str] | None = None) -> int:
    """Entrypoint for the ``python -m puml.feature_types`` CLI."""

    parser = argparse.ArgumentParser(
        description="Render PlantUML diagrams from feature_types.json files.",
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Path to the feature_types.json file to render.",
    )
    parser.add_argument(
        "--title",
        help="Optional PlantUML title to include at the top of the diagram.",
    )
    parser.add_argument(
        "--package",
        help="Optional package name used to wrap the generated feature types.",
    )
    parser.set_defaults(include_notes=True, include_descriptions=True)
    parser.add_argument(
        "--no-notes",
        dest="include_notes",
        action="store_false",
        help="Disable inclusion of feature type notes in the output.",
    )
    parser.add_argument(
        "--no-description",
        dest="include_descriptions",
        action="store_false",
        help="Disable attribute descriptions in the generated output.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write the generated PlantUML to this file instead of stdout.",
    )

    args = parser.parse_args(argv)

    try:
        feature_types = json.loads(args.input.read_text(encoding="utf-8"))
    except FileNotFoundError:  # pragma: no cover - passthrough to CLI behaviour
        parser.error(f"Input file '{args.input}' was not found.")
    except json.JSONDecodeError as exc:  # pragma: no cover - passthrough to CLI behaviour
        parser.error(f"Input file '{args.input}' did not contain valid JSON: {exc}.")

    output = render_feature_types_to_puml(
        feature_types,
        title=args.title,
        package=args.package,
        include_notes=args.include_notes,
        include_descriptions=args.include_descriptions,
    )

    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
        if not output.endswith("\n"):
            sys.stdout.write("\n")

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    sys.exit(main())
