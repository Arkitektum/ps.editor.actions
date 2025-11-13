"""Render product specification metadata to Markdown using a Handlebars-style template."""

from __future__ import annotations

import argparse
import json
import os
import re
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "IncludeResource",
    "render_product_specification",
    "build_context",
    "render_template",
    "main",
    "_read_include_directories",
]


_PLACEHOLDER_RE = re.compile(r"{{\s*([^}]+?)\s*}}")

_LABEL_TRANSLATIONS: dict[str, str] = {
    "title": "tittel",
    "href": "lenke",
    "name": "navn",
    "format": "format",
    "rel": "relasjon",
    "type": "type",
    "code": "kode",
    "codeSpace": "koderom",
    "version": "versjon",
    "organization": "organisasjon",
    "email": "epost",
    "phone": "telefon",
    "voice": "telefonnummer",
    "role": "rolle",
    "address": "adresse",
    "deliveryPoint": "leveringspunkt",
    "city": "by",
    "postalCode": "postnummer",
    "country": "land",
    "hoursOfService": "åpningstider",
    "contactInstructions": "kontaktinstruks",
    "access": "tilgang",
    "protocol": "protokoll",
    "language": "språk",
    "metadata": "metadata",
    "scope": "omfang",
    "level": "nivå",
    "extent": "utstrekning",
    "spatial": "romlig",
    "spatialScope": "romlig omfang",
    "boundingBox": "avgrensning",
    "bbox": "bbox",
    "west": "vest",
    "south": "sør",
    "east": "øst",
    "north": "nord",
    "crs": "crs",
    "temporal": "tidsmessig",
    "interval": "intervall",
    "legalConstraints": "juridiske begrensninger",
    "accessConstraints": "tilgangsbegrensninger",
    "useConstraints": "bruksbegrensninger",
    "license": "lisens",
    "licenseUrl": "lisenslenke",
    "securityConstraints": "sikkerhetsbegrensninger",
    "result": "resultat",
    "identification": "identifikasjon",
    "abstract": "sammendrag",
    "purpose": "formål",
    "keywords": "stikkord",
    "topicCategories": "temakategorier",
    "dates": "datoer",
    "creation": "opprettet",
    "publication": "publisering",
    "revision": "revisjon",
    "responsibleParties": "ansvarlige parter",
    "supplementalInformation": "tilleggsinformasjon",
    "dataContent": "datainnhold",
    "usage": "bruk",
    "referenceSystems": "referansesystemer",
    "spatialReferenceSystems": "romlige referansesystemer",
    "spatialRepresentationType": "romlig representasjonstype",
    "dataQuality": "datakvalitet",
    "qualityElements": "kvalitetselementer",
    "measure": "måleparameter",
    "lineage": "historikk",
    "statement": "beskrivelse",
    "maintenance": "vedlikehold",
    "maintenanceFrequency": "vedlikeholdsfrekvens",
    "maintenanceNote": "vedlikeholdsnotat",
    "status": "status",
    "delivery": "leveranse",
    "distributions": "distribusjoner",
    "notes": "notater",
    "standard": "standard",
    "standardVersion": "standardversjon",
    "metadataDate": "metadatadato",
    "pointOfContact": "kontaktpunkt",
    "identifiers": "identifikatorer",
    "authority": "myndighet",
    "metadataUrl": "metadatalenke",
    "links": "lenker",
    "useLimitation": "bruksbegrensninger",
    "legendDescriptionUrl": "Tegnforklaring",
    
}


def _translate_label(label: str) -> str:
    """Translate known metadata keys from English to Norwegian."""

    return _LABEL_TRANSLATIONS.get(label, label)


@dataclass
class IncludeResource:
    """Represent a static resource that should be injected into the template."""

    placeholder: str
    content: str


def build_context(psdata: Mapping[str, Any], *, updated: str | None = None) -> dict[str, Any]:
    """Build the template context from the psdata JSON payload.

    Parameters
    ----------
    psdata:
        The psdata style mapping containing identification, quality and other
        product specification metadata.
    updated:
        Optional explicit value to use for the ``updated`` front matter field.
        When omitted the function attempts to use
        ``psdata['metadata']['metadataDate']`` if present.
    """

    if not isinstance(psdata, Mapping):
        raise TypeError("psdata must be a mapping")

    context = dict(psdata)

    metadata = psdata.get("metadata")
    if updated:
        context["updated"] = updated
    elif isinstance(metadata, Mapping):
        metadata_date = metadata.get("metadataDate")
        if isinstance(metadata_date, str):
            context["updated"] = metadata_date

    return context


def render_product_specification(
    template_text: str,
    context: Mapping[str, Any],
    *,
    resources: Sequence[IncludeResource] | None = None,
) -> str:
    """Render the product specification Markdown from ``template_text``.

    The function performs a minimal Handlebars/Mustache style replacement
    supporting dotted paths and integer list indexes (``{{foo.bar.[0].baz}}``).
    Any placeholders that cannot be resolved are replaced with an empty string.
    """

    if not isinstance(template_text, str):
        raise TypeError("template_text must be a string")
    if not isinstance(context, Mapping):
        raise TypeError("context must be a mapping")

    render_context = dict(context)
    if resources:
        for resource in resources:
            render_context[resource.placeholder] = resource.content

    def substitute(match: re.Match[str]) -> str:
        expression = match.group(1).strip()
        value = _resolve_expression(render_context, expression)
        return _stringify(value)

    return _PLACEHOLDER_RE.sub(substitute, template_text)


def render_template(
    template_path: Path,
    psdata_path: Path,
    *,
    includes: Sequence[IncludeResource] | None = None,
    updated: str | None = None,
) -> str:
    """Render a template from paths to the template and psdata JSON file."""

    template_text = template_path.read_text(encoding="utf-8")
    psdata = json.loads(psdata_path.read_text(encoding="utf-8"))
    context = build_context(psdata, updated=updated)
    return render_product_specification(template_text, context, resources=includes)


def _resolve_expression(context: Mapping[str, Any], expression: str) -> Any:
    current: Any = context
    for token in _tokenize(expression):
        if isinstance(token, int):
            if isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
                if 0 <= token < len(current):
                    current = current[token]
                else:
                    return None
            else:
                return None
        else:
            if isinstance(current, Mapping):
                if token in current:
                    current = current[token]
                else:
                    return None
            else:
                return None
    return current


def _tokenize(expression: str) -> list[int | str]:
    tokens: list[int | str] = []
    buffer: list[str] = []
    i = 0
    while i < len(expression):
        char = expression[i]
        if char == ".":
            if buffer:
                tokens.append("".join(buffer))
                buffer.clear()
            i += 1
        elif char == "[":
            if buffer:
                tokens.append("".join(buffer))
                buffer.clear()
            end = expression.find("]", i)
            if end == -1:
                # treat the rest as raw text
                buffer.append(expression[i:])
                break
            index_str = expression[i + 1 : end].strip()
            if index_str.isdigit():
                tokens.append(int(index_str))
            else:
                # non-integer indexes fall back to literal handling
                tokens.append(index_str)
            i = end + 1
            if i < len(expression) and expression[i] == ".":
                i += 1
        else:
            buffer.append(char)
            i += 1
    if buffer:
        tokens.append("".join(buffer))
    return tokens


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Mapping):
        lines: list[str] = []
        for key, inner in value.items():
            label = _translate_label(key)
            formatted = _stringify(inner).strip()
            if formatted:
                if "\n" in formatted:
                    indented = textwrap.indent(formatted, "  ")
                    lines.append(f"- **{label}**:\n{indented}")
                else:
                    lines.append(f"- **{label}**: {formatted}")
            else:
                lines.append(f"- **{label}**:")
        return "\n".join(lines)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        primitive = [item for item in value if not isinstance(item, (Mapping, Sequence)) or isinstance(item, (str, bytes))]
        if len(primitive) == len(value):
            return ", ".join(_stringify(item) for item in value if item is not None)
        lines = []
        for item in value:
            formatted = _stringify(item).strip()
            if not formatted:
                lines.append("-")
                continue
            parts = formatted.splitlines()
            first_line = parts[0]
            if first_line.startswith("- "):
                first_line = first_line[2:]
            if len(parts) > 1:
                remainder = "\n".join(parts[1:])
                indented = textwrap.indent(remainder, "  ")
                lines.append(f"- {first_line}\n{indented}")
            else:
                lines.append(f"- {first_line}")
        return "\n".join(lines)
    return str(value)


def _read_include_resources(
    entries: Sequence[str],
    *,
    is_image: bool,
    output_path: Path | None,
) -> list[IncludeResource]:
    resources: list[IncludeResource] = []
    for entry in entries:
        name, path_str = _split_mapping(entry)
        path = Path(path_str)
        if is_image:
            resources.append(
                IncludeResource(
                    placeholder=name,
                    content=_format_image_markdown(path, output_path),
                )
            )
        else:
            text = path.read_text(encoding="utf-8").rstrip()
            resources.append(IncludeResource(placeholder=name, content=text))
    return resources


def _read_include_directories(directories: Sequence[Path]) -> list[IncludeResource]:
    """Load Markdown include resources from the given directories.

    Each ``.md`` file is mapped to a placeholder using the convention that a file
    named ``section.md`` populates the ``{{incl_section}}`` slot in the template.
    Nested directories are processed recursively to make it easy to organise the
    include snippets.
    """

    resources: list[IncludeResource] = []
    for directory in directories:
        if not directory.exists():
            raise FileNotFoundError(f"Include directory '{directory}' does not exist")
        if not directory.is_dir():
            raise NotADirectoryError(f"Include directory '{directory}' is not a directory")

        for path in sorted(directory.rglob("*.md")):
            placeholder = f"incl_{path.stem}"
            text = path.read_text(encoding="utf-8").rstrip()
            resources.append(IncludeResource(placeholder=placeholder, content=text))

    return resources


def _split_mapping(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            f"Expected NAME=PATH syntax for include arguments, got '{value}'",
        )
    name, path = value.split("=", 1)
    name = name.strip()
    path = path.strip()
    if not name:
        raise argparse.ArgumentTypeError("Include placeholder name cannot be empty")
    if not path:
        raise argparse.ArgumentTypeError("Include path cannot be empty")
    return name, path


def _format_image_markdown(path: Path, output_path: Path | None) -> str:
    if output_path is not None:
        try:
            relative = Path(os.path.relpath(path, output_path.parent))
        except ValueError:
            relative = path
    else:
        relative = path

    alt_text = path.stem.replace("_", " ").strip().capitalize() or path.stem
    posix_path = relative.as_posix()
    return f"![{alt_text}]({posix_path})"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render a product specification Markdown document",
    )
    parser.add_argument(
        "psdata",
        type=Path,
        help="Path to the psdata JSON file",
    )
    parser.add_argument(
        "-t",
        "--template",
        type=Path,
        default=Path("data/template/ps.md.hbs"),
        help="Path to the Handlebars Markdown template",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Optional output path for the rendered Markdown",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="Inject the contents of PATH into the template placeholder NAME",
    )
    parser.add_argument(
        "--include-dir",
        action="append",
        default=[],
        type=Path,
        metavar="PATH",
        help=(
            "Load every Markdown file in PATH as an include. A file named "
            "'section.md' is mapped to the placeholder 'incl_section'."
        ),
    )
    parser.add_argument(
        "--image",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help=(
            "Inject a Markdown image referencing PATH into placeholder NAME. "
            "Paths are resolved relative to the output directory when provided."
        ),
    )
    parser.add_argument(
        "--updated",
        help="Optional override for the 'updated' front matter field",
    )

    args = parser.parse_args(argv)

    try:
        directory_includes = _read_include_directories(args.include_dir)
    except OSError as exc:
        parser.error(str(exc))

    includes = _read_include_resources(
        args.include,
        is_image=False,
        output_path=args.output,
    )
    images = _read_include_resources(
        args.image,
        is_image=True,
        output_path=args.output,
    )

    all_resources = _merge_include_resources(directory_includes, includes, images)

    rendered = render_template(
        args.template,
        args.psdata,
        includes=all_resources,
        updated=args.updated,
    )

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())


def _merge_include_resources(
    *groups: Sequence[IncludeResource] | None,
) -> list[IncludeResource]:
    merged: dict[str, IncludeResource] = {}
    for group in groups:
        if not group:
            continue
        for resource in group:
            merged[resource.placeholder] = resource
    return list(merged.values())
