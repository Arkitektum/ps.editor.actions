"""Render feature type metadata to Markdown."""
from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping, Sequence
from html import escape, unescape
from pathlib import Path
from typing import Any

__all__ = [
    "render_feature_types_to_markdown",
    "_gather_feature_types_from_file",
    "_render_markdown_section",
    "main",
]

_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_HTML_BREAK_RE = re.compile(r"&lt;br\s*/?&gt;", re.IGNORECASE)
_URL_RE = re.compile(r"(https?://[^\s<>()]+)", re.IGNORECASE)
_TRAILING_PUNCTUATION = ".,:;!?)]"


def render_feature_types_to_markdown(
    feature_types: Sequence[Mapping[str, Any]],
    *,
    heading_level: int = 4,
    include_descriptions: bool = True,
) -> str:
    """Convert feature type metadata into Markdown tables."""

    if not isinstance(feature_types, Sequence) or isinstance(feature_types, (str, bytes)):
        raise TypeError("feature_types must be a sequence of mappings")

    heading_level = max(1, int(heading_level))
    heading_prefix = "#" * heading_level

    sections: list[str] = []
    for feature_type in feature_types:
        if not isinstance(feature_type, Mapping):
            raise TypeError("Each feature type entry must be a mapping")

        name = str(feature_type.get("name", "Unnamed feature type")).strip() or "Unnamed feature type"
        if feature_type.get("abstract") is True:
            name = f"{name} (abstrakt)"
        section_lines: list[str] = [f"{heading_prefix} {name}"]

        paragraphs: list[str] = []
        description = feature_type.get("description")
        if include_descriptions and isinstance(description, str):
            normalized = _normalize_text(description)
            if normalized:
                paragraphs.append(normalized)

        geometry_obj = feature_type.get("geometry")
        geometry_description = _format_geometry_metadata(geometry_obj)
        if geometry_description and include_descriptions:
            paragraphs.append(geometry_description)

        if paragraphs:
            section_lines.append("")
            section_lines.append(_linkify_html(paragraphs[0]))
            for paragraph in paragraphs[1:]:
                section_lines.append("")
                section_lines.append(_linkify_html(paragraph))

        attributes_obj = feature_type.get("attributes")
        attributes: Sequence[Mapping[str, Any]] | None = None
        if isinstance(attributes_obj, Sequence) and not isinstance(attributes_obj, (str, bytes)):
            attributes = [
                attribute
                for attribute in attributes_obj
                if isinstance(attribute, Mapping)
            ]

        flattened = _flatten_attributes(attributes)
        flattened = _inject_geometry_rows(
            flattened,
            geometry_obj if isinstance(geometry_obj, Mapping) else None,
            include_descriptions=include_descriptions,
        )

        geometry_attribute = _build_geometry_attribute(
            feature_type.get("geometry"),
        )
        if geometry_attribute and not any(
            entry.get("name") == "geometry" for entry in flattened
        ):
            flattened.insert(0, geometry_attribute)

        section_lines.append("")
        section_lines.extend(_build_table(flattened, include_descriptions=include_descriptions))

        relationship_lines = _build_relationships(feature_type.get("relationships"))
        if relationship_lines:
            section_lines.append("")
            section_lines.append("Relasjoner")
            section_lines.extend(relationship_lines)

        sections.append("\n".join(section_lines))

    return "\n\n".join(sections)


def _normalize_text(value: str) -> str:
    text = unescape(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _BR_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = text.strip()
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    return text.replace("\n", "<br />")


def _format_geometry_metadata(geometry: Any) -> str:
    if not isinstance(geometry, Mapping):
        return ""

    lines: list[str] = ["Geometri:"]

    item_type = str(geometry.get("itemType", "")).strip()
    geometry_type = str(geometry.get("type", "")).strip()

    if item_type:
        lines.append(f"Elementtype: {item_type}")

    if geometry_type and (not item_type or geometry_type != item_type):
        lines.append(f"Type: {geometry_type}")

    storage_crs_values = _normalize_sequence(geometry.get("storageCrs"))
    if storage_crs_values:
        lines.append("Lagrings-CRS:")
        lines.extend(f"• {value}" for value in storage_crs_values)

    crs_values = _normalize_sequence(geometry.get("crs"))
    if crs_values:
        lines.append("Koordinatreferansesystem (crs):")
        lines.extend(f"• {value}" for value in crs_values)

    if len(lines) == 1:
        return ""

    return "<br />".join(lines)


def _normalize_sequence(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []

    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        normalized: list[str] = []
        for item in value:
            item_text = str(item).strip()
            if item_text:
                normalized.append(item_text)
        return normalized

    if value is None:
        return []

    text = str(value).strip()
    return [text] if text else []


def _inject_geometry_rows(
    attributes: list[dict[str, Any]],
    geometry: Mapping[str, Any] | None,
    *,
    include_descriptions: bool,
) -> list[dict[str, Any]]:
    if not attributes:
        attributes = []
    else:
        attributes = [dict(entry) for entry in attributes]

    metadata_description = (
        _format_geometry_metadata(geometry)
        if include_descriptions and geometry is not None
        else ""
    )

    primary_index: int | None = None
    for index, entry in enumerate(attributes):
        type_value = str(entry.get("type", "")).strip()
        if type_value.lower().startswith("geometry"):
            entry["type"] = _normalize_geometry_type(type_value)

            if not include_descriptions:
                entry["description"] = ""
            elif metadata_description:
                existing = entry.get("description")
                if isinstance(existing, str) and existing.strip():
                    entry["description"] = f"{existing}\n\n{metadata_description}"
                else:
                    entry["description"] = metadata_description

            if entry.get("ogcRole") == "primary-geometry":
                entry["name"] = "geometry"
                if metadata_description:
                    entry["description"] = metadata_description
                if primary_index is None:
                    primary_index = index
        elif not include_descriptions:
            entry["description"] = ""

    if primary_index is not None:
        primary_entry = attributes.pop(primary_index)
        attributes.insert(0, primary_entry)

    return attributes


def _normalize_geometry_type(type_value: str) -> str:
    normalized = type_value.lower()
    if normalized.startswith("geometry"):
        return "geometry-any"
    return type_value


def _format_listed_values(value_domain: Any) -> str:
    if not isinstance(value_domain, Mapping):
        return ""

    bullets: list[str] = []

    code_list = value_domain.get("codeList")
    if isinstance(code_list, str):
        code_list = code_list.strip()
        if code_list:
            bullets.append(f"Kodeliste: {code_list}")

    listed_values = value_domain.get("listedValues")
    if not isinstance(listed_values, Sequence) or isinstance(listed_values, (str, bytes)):
        return "<br />".join(f"- {bullet}" for bullet in bullets) if bullets else ""

    for entry in listed_values:
        if not isinstance(entry, Mapping):
            continue
        value = str(entry.get("value", "")).strip()
        label = str(entry.get("label", "")).strip()
        if not value and not label:
            continue
        if value and label and label != value:
            bullets.append(f"{value} – {label}")
        else:
            bullets.append(value or label)

    if not bullets:
        return ""

    return "<br />".join(f"- {bullet}" for bullet in bullets)


def _flatten_attributes(
    attributes: Sequence[Mapping[str, Any]] | None,
    *,
    prefix: str = "",
) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    if not attributes:
        return flattened

    for attribute in attributes:
        if not isinstance(attribute, Mapping):
            continue

        name = str(attribute.get("name", "")).strip()
        full_name = f"{prefix}.{name}" if prefix and name else prefix or name
        full_name = full_name or ""

        entry: dict[str, Any] = {
            "name": full_name,
            "type": str(attribute.get("type", "")).strip(),
            "description": attribute.get("description"),
            "ogcRole": attribute.get("ogcRole"),
            "cardinality": attribute.get("cardinality"),
        }
        value_domain = attribute.get("valueDomain")
        if isinstance(value_domain, Mapping):
            entry["valueDomain"] = value_domain
        flattened.append(entry)

        nested = attribute.get("attributes")
        if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
            flattened.extend(
                _flatten_attributes(
                    [item for item in nested if isinstance(item, Mapping)],
                    prefix=full_name or prefix,
                )
            )

    return flattened


def _build_geometry_attribute(geometry: Any) -> dict[str, Any] | None:
    if not isinstance(geometry, Mapping):
        return None

    type_value = "geometry"

    format_value = geometry.get("format")
    if isinstance(format_value, str) and format_value.strip():
        type_value = format_value.strip()

    geometry_type = geometry.get("type")
    if isinstance(geometry_type, str) and geometry_type.strip():
        candidate = geometry_type.strip()
        if candidate.lower() not in {"feature", "unknown"}:
            type_value = candidate
        elif type_value == "geometry":
            type_value = "geometry"

    types_value = geometry.get("types")
    geometry_types: list[str] = []
    if isinstance(types_value, Sequence) and not isinstance(types_value, (str, bytes)):
        for entry in types_value:
            entry_text = str(entry).strip()
            if entry_text:
                geometry_types.append(entry_text)
    if geometry_types and type_value.lower() in {"feature", "geometry"}:
        type_value = " | ".join(geometry_types)

    description_parts: list[str] = []
    if geometry_types and " | ".join(geometry_types) != type_value:
        description_parts.append(f"Typer: {', '.join(geometry_types)}")

    item_type = geometry.get("itemType")
    if isinstance(item_type, str) and item_type.strip():
        description_parts.append(f"Elementtype: {item_type.strip()}")

    description: str | None = None
    if description_parts:
        description = "<br />".join(description_parts)

    attribute: dict[str, Any] = {
        "name": "geometry",
        "type": type_value,
        "description": description,
    }

    ogc_role = geometry.get("ogcRole")
    if ogc_role is not None:
        attribute["ogcRole"] = ogc_role

    return attribute


def _build_table(
    attributes: list[dict[str, Any]],
    *,
    include_descriptions: bool,
) -> list[str]:
    processed_rows: list[dict[str, str]] = []

    for entry in attributes:
        name = _escape_html(entry.get("name", ""))
        type_ = _escape_html(entry.get("type", ""))

        cardinality_value = entry.get("cardinality")
        if isinstance(cardinality_value, str):
            cardinality_text = cardinality_value.strip()
        elif cardinality_value is None:
            cardinality_text = ""
        else:
            cardinality_text = str(cardinality_value).strip()

        description_value = entry.get("description")
        description_cell = ""
        if include_descriptions and isinstance(description_value, str):
            normalized = _normalize_text(description_value)
            if normalized:
                description_cell = normalized

        value_domain_text = _format_listed_values(entry.get("valueDomain"))

        ogc_role = entry.get("ogcRole")

        processed_rows.append(
            {
                "name": name,
                "type": type_,
                "cardinality": _escape_html(cardinality_text),
                "description": _escape_html(description_cell, preserve_breaks=True),
                "value_domain": _escape_html(value_domain_text, preserve_breaks=True),
                "ogc_role": _escape_html(
                    str(ogc_role).strip() if ogc_role is not None else ""
                ),
            }
        )

    lines = ["Egenskaper"]

    if not processed_rows:
        lines.append("")
        lines.append("(ingen)")
        return lines

    for row in processed_rows:
        lines.append("")
        lines.append('<table class="feature-attribute-table">')
        lines.append("  <colgroup>")
        lines.append('    <col style="width: 35%;" />')
        lines.append('    <col style="width: 65%;" />')
        lines.append("  </colgroup>")
        lines.append("  <tbody>")

        name_value = row["name"]
        strong_value = f"<strong>{name_value}</strong>" if name_value else ""
        lines.append("    <tr>")
        lines.append('      <th scope="row">Navn:</th>')
        lines.append(f"      <td>{strong_value}</td>")
        lines.append("    </tr>")

        field_rows = [
            ("Definisjon:", row["description"]),
            ("Multiplisitet:", row["cardinality"]),
            ("Type:", row["type"]),
            ("Tillatte verdier:", row["value_domain"]),
            ("OGC-rolle:", row["ogc_role"]),
        ]

        for label, value in field_rows:
            if not value:
                continue
            lines.append("    <tr>")
            lines.append(f"      <th scope=\"row\">{label}</th>")
            lines.append(f"      <td>{value}</td>")
            lines.append("    </tr>")

        lines.append("  </tbody>")
        lines.append("</table>")

    return lines


def _build_relationships(relationships: Any) -> list[str]:
    if not isinstance(relationships, Mapping):
        return []

    lines: list[str] = []

    inheritance = relationships.get("inheritance")
    if isinstance(inheritance, Sequence) and not isinstance(inheritance, (str, bytes)):
        inherited = [str(entry).strip() for entry in inheritance if str(entry).strip()]
        if inherited:
            lines.append("")
            lines.append("**Arv**")
            lines.append(", ".join(inherited))

    associations = relationships.get("associations")
    if isinstance(associations, Sequence) and not isinstance(associations, (str, bytes)):
        assoc_lines: list[str] = []
        for assoc in associations:
            if not isinstance(assoc, Mapping):
                continue
            target = str(assoc.get("target", "")).strip()
            role = str(assoc.get("role", "")).strip()
            cardinality = str(assoc.get("cardinality", "")).strip()
            if not target:
                continue
            parts = [target]
            if role:
                parts.append(f"rolle: {role}")
            if cardinality:
                parts.append(f"kardinalitet: {cardinality}")
            assoc_lines.append(" – ".join(parts))
        if assoc_lines:
            lines.append("")
            lines.append("**Assosiasjoner**")
            lines.extend(assoc_lines)

    return lines


def _escape_html(value: Any, *, preserve_breaks: bool = False) -> str:
    text = str(value) if value is not None else ""
    text = text.strip()
    if not text:
        return ""

    escaped = escape(text, quote=False)
    if preserve_breaks:
        escaped = _HTML_BREAK_RE.sub("<br />", escaped)
    return _linkify_html(escaped)


def _linkify_html(text: str) -> str:
    if not text:
        return ""

    def replace(match: re.Match[str]) -> str:
        url = match.group(0)
        suffix = ""
        while url and url[-1] in _TRAILING_PUNCTUATION:
            suffix = url[-1] + suffix
            url = url[:-1]
        if not url:
            return match.group(0)
        return f'<a href="{url}">{url}</a>{suffix}'

    return _URL_RE.sub(replace, text)


def _gather_feature_types_from_file(path: Path) -> list[Mapping[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
        raise TypeError(f"Expected a sequence of feature types in {path}")

    feature_types: list[Mapping[str, Any]] = []
    for entry in data:
        if not isinstance(entry, Mapping):
            raise TypeError(f"Each feature type entry in {path} must be a mapping")
        feature_types.append(entry)

    return feature_types


def _render_markdown_section(
    display_name: str,
    feature_types: Sequence[Mapping[str, Any]],
) -> str:
    section_heading = "### Objekttyper"
    body = render_feature_types_to_markdown(feature_types, heading_level=4)

    if body:
        return f"{section_heading}\n\n{body}"
    return section_heading


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render feature type metadata to Markdown")
    parser.add_argument(
        "inputs",
        nargs="*",
        type=Path,
        help="Input JSON files containing feature types",
    )
    parser.add_argument(
        "-f",
        "--feature-types",
        metavar="PATH",
        nargs="+",
        type=Path,
        help=(
            "Explicit feature type JSON files to include. "
            "When provided, files given positionally are also used."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output Markdown file. Defaults to stdout",
    )
    parser.add_argument(
        "--no-description",
        dest="include_descriptions",
        action="store_false",
        help="Omit descriptions from the rendered Markdown",
    )
    parser.set_defaults(include_descriptions=True)

    args = parser.parse_args(argv)

    explicit_inputs: list[Path] = []
    if args.feature_types:
        explicit_inputs.extend(args.feature_types)
    if args.inputs:
        explicit_inputs.extend(args.inputs)

    input_paths: list[Path]
    if explicit_inputs:
        missing = [str(path) for path in explicit_inputs if not path.exists()]
        if missing:
            raise FileNotFoundError(
                f"Input file(s) not found: {', '.join(missing)}"
            )
        input_paths = [path for path in explicit_inputs if path.exists()]
    else:
        discovered: set[Path] = set()
        for root in {Path.cwd(), Path("src")}:
            if not root.exists():
                continue
            for path in root.rglob("*_feature_catalogue.json"):
                discovered.add(path)
        input_paths = sorted(discovered)

    if not input_paths:
        raise FileNotFoundError("No feature type JSON files found")

    sections: list[str] = []
    for path in input_paths:
        feature_types = _gather_feature_types_from_file(path)
        stem = path.stem
        prefix = stem.split("_feature_catalogue", 1)[0]
        display_name = prefix.replace("_", " ").strip() or stem
        display_name = display_name.upper()
        section = _render_markdown_section(
            display_name,
            feature_types,
        )
        sections.append(section)

    output = "\n\n".join(sections)

    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
