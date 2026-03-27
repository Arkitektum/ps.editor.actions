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
_URL_RE = re.compile(r"(https?://[^\s<>()]+)", re.IGNORECASE)
_TRAILING_PUNCTUATION = ".,:;!?)]"

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
    "level": "Nivå",
    "extent": "Utstrekning",
    "west": "vest",
    "south": "sør",
    "east": "øst",
    "north": "nord",
    "crs": "crs",
    "result": "Resultat",
    "creation": "Opprettet",
    "publication": "Publisering",
    "revision": "Revisjon",
    "supplementalInformation": "Tilleggsinformasjon",
    "notes": "Notater",
    "authority": "Utsteder",
    # New / renamed keys
    "contact": "Kontakt",
    "individualName": "Kontaktperson",
    "organizationName": "Organisasjon",
    "electronicMailAddress": "Epost",
    "logo": "Logo",
    "uniqueId": "Unik identifikator",
    "keyword": "Stikkord",
    "topicCategory": "Temakategori",
    "abstract": "Sammendrag",
    "purpose": "Formål",
    "summary": "Sammendrag",
    "useCase": "Bruksområde",
    "date": "Dato",
    "identificationSection": "Identifikasjon",
    "restriction": "Begrensninger",
    "resourceConstraints": "Ressursbegrensninger",
    "useLimitations": "Bruksbegrensninger",
    "legalConstraints": "Juridiske begrensninger",
    "accessConstraints": "Tilgangsbegrensninger",
    "useConstraints": "Bruksbegrensninger",
    "license": "Lisens",
    "licenseUrl": "Lisenslenke",
    "otherConstraints": "Andre begrensninger",
    "reference": "Lovhenvisning",
    "securityConstraints": "Sikkerhetsbegrensninger",
    "classification": "Klassifisering",
    "spatialRepresentationType": "Romlig representasjonstype",
    "spatialResolution": "Romlig oppløsning",
    "distance": "Avstand",
    "uom": "Måleenhet",
    "value": "Verdi",
    "equivalentScale": "Ekvivalent målestokk",
    "geographicElement": "Geografisk utstrekning",
    "westBoundLongitude": "Vest",
    "eastBoundLongitude": "Øst",
    "southBoundLatitude": "Sør",
    "northBoundLatitude": "Nord",
    "temporalElement": "Tidsmessig utstrekning",
    "timePeriod": "Tidsperiode",
    "beginPosition": "Fra",
    "endPosition": "Til",
    "maintenance": "Vedlikehold",
    "scopeSection": "Spesifikasjonsomfang",
    "specificationScope": "Omfang",
    "scopeIdentification": "Identifikasjon",
    "levelName": "Nivånavn",
    "levelDescription": "Nivåbeskrivelse",
    "description": "Beskrivelse",
    "dataContentAndStructureSection": "Innhold og struktur",
    "narrativeDescription": "Beskrivelse",
    "referenceSystemSection": "Referansesystem",
    "spatialReferenceSystem": "Romlige referansesystemer",
    "dataQualitySection": "Datakvalitet",
    "scope": "Omfang",
    "report": "Kvalitetselementer",
    "nameOfMeasure": "Kvalitetsmål",
    "measureDescription": "Målebeskrivelse",
    "descriptiveResult": "Beskrivende resultat",
    "resourceLineage": "Historikk",
    "statement": "Beskrivelse",
    "maintenanceSection": "Vedlikehold",
    "maintenanceAndUpdateFrequency": "Vedlikeholdsfrekvens",
    "maintenanceAndUpdateStatement": "Status",
    "dataCaptureAndProductionSection": "Datafangst",
    "DataAcquisitionAndProcessing": "Datainnsamling og prosessering",
    "processStep": "Prosesstrinn",
    "portrayal": "Presentasjon",
    "linkage": "Lenke",
    "deliverySection": "Leveranse",
    "delivery": "Leveranse",
    "deliveryMedium": "Leveransemedium",
    "deliveryMediumName": "Medienavn",
    "deliveryService": "Leveransetjeneste",
    "serviceEndpoint": "Tjenesteendepunkt",
    "serviceProperty": "Tjenesteegenskap",
    "deliveryFormat": "Leveranseformat",
    "formatName": "Formatnavn",
    "deliveryScope": "Leveranseomfang",
    "unitsOfDelivery": "Leveranseenheter",
    "metadataSection": "Metadata",
    "metadataStandard": "Metadatastandard",
    "metadataStandardVersion": "Metadatastandardversjon",
    "metadataDate": "Metadatadato",
    "metadataIdentifier": "Metadataidentifikator",
    "metadataLinkage": "Metadatalenke",
    "links": "Lenker",
    "specificationUrl": "Denne versjonen av produktspesifikasjonen",
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

    metadata = psdata.get("metadataSection")
    if updated:
        context["updated"] = updated
    elif isinstance(metadata, Mapping):
        metadata_date = metadata.get("metadataDate")
        if isinstance(metadata_date, str):
            context["updated"] = metadata_date

    ref_section = context.get("referenceSystemSection")
    if isinstance(ref_section, Mapping):
        formatted = _format_reference_system_table(ref_section)
        if formatted:
            context["referenceSystemSection"] = formatted

    scope_section = context.get("scopeSection")
    if isinstance(scope_section, (list, Sequence)) and not isinstance(scope_section, (str, bytes)):
        formatted = _format_scope_section(scope_section)
        if formatted:
            context["scopeSection"] = formatted

    delivery_section = context.get("deliverySection")
    if isinstance(delivery_section, (list, Sequence)) and not isinstance(delivery_section, (str, bytes)):
        formatted = _format_delivery_section(delivery_section)
        if formatted:
            context["deliverySection"] = formatted

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

    rendered = _PLACEHOLDER_RE.sub(substitute, template_text)
    rendered = _propagate_blockquote_prefix(rendered)
    return _linkify_markdown(rendered)


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


def _stringify(value: Any, *, level: int = 0, suppress_bullet: bool = False) -> str:
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
        has_block = False
        for key, inner in value.items():
            label = _translate_label(key)
            formatted = _stringify(inner, level=level + 1).strip()
            bullet = level > 0 and not suppress_bullet
            prefix = "- " if bullet else ""
            force_block = _should_force_block(inner, formatted, level)
            if force_block:
                has_block = True
            if formatted:
                if "\n" in formatted or force_block:
                    if bullet:
                        indented = (
                            textwrap.indent(formatted, "  ")
                            if "\n" in formatted
                            else textwrap.indent(formatted, "  ")
                        )
                        lines.append(f"{prefix}**{label}**:\n{indented}")
                    else:
                        separator = "\n\n" if formatted.lstrip().startswith(("- ", "* ")) else "\n"
                        lines.append(f"**{label}**:{separator}{formatted}")
                else:
                    lines.append(f"{prefix}**{label}**: {formatted}")
            else:
                lines.append(f"{prefix}**{label}**:")
        joiner = "\n\n" if level == 0 or has_block else "\n"
        return joiner.join(line for line in lines if line)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        primitive = [item for item in value if not isinstance(item, (Mapping, Sequence)) or isinstance(item, (str, bytes))]
        if len(primitive) == len(value):
            return ", ".join(_stringify(item, level=level + 1) for item in value if item is not None)
        lines = []
        for item in value:
            formatted = _stringify(item, level=level + 1, suppress_bullet=True).strip()
            if not formatted:
                continue
            parts = formatted.splitlines()
            while parts and not parts[0].strip():
                parts.pop(0)
            if not parts:
                continue
            first_line = parts[0]
            if first_line.startswith("- "):
                first_line = first_line[2:]
            remainder = "\n".join(parts[1:])
            entry = f"- {first_line}"
            if remainder.strip():
                entry += "\n" + textwrap.indent(remainder, "  ")
            lines.append(entry)
        return "\n\n".join(lines)
    return str(value)


def _should_force_block(raw_value: Any, rendered: str, level: int) -> bool:
    if isinstance(raw_value, Mapping):
        return True

    if isinstance(raw_value, Sequence) and not isinstance(raw_value, (str, bytes)):
        has_complex = any(
            isinstance(item, (Mapping, Sequence)) and not isinstance(item, (str, bytes))
            for item in raw_value
        )
        if has_complex or level == 0:
            return True

    stripped = rendered.lstrip()
    if stripped.startswith(("http://", "https://")) and level == 0:
        return True

    return False


def _format_scope_section(scope_section: Sequence[Any]) -> str:
    """Format the scope section as structured Markdown.

    Each scope entry gets its own ``###`` heading (visible in the navigation
    menu) followed by metadata fields rendered on separate lines.
    """
    blocks: list[str] = []
    for entry in scope_section:
        if not isinstance(entry, Mapping):
            continue
        spec_scope = entry.get("specificationScope")
        if not isinstance(spec_scope, Mapping):
            continue

        lines: list[str] = []

        scope_id = spec_scope.get("scopeIdentification")
        scope_id_text = scope_id.strip() if isinstance(scope_id, str) else ""

        if scope_id_text:
            lines.append(f"### {scope_id_text}")
            lines.append("")

        level = spec_scope.get("level")
        if isinstance(level, str) and level.strip():
            lines.append(f"**Nivå**: {level.strip()}")
            lines.append("")

        level_name = spec_scope.get("levelName")
        if isinstance(level_name, str) and level_name.strip():
            lines.append(f"**Nivånavn**: {level_name.strip()}")
            lines.append("")

        extent = spec_scope.get("extent")
        if isinstance(extent, Mapping):
            desc = extent.get("description")
            if isinstance(desc, str) and desc.strip():
                lines.append(f"**Utstrekning**: {desc.strip()}")
                lines.append("")

        level_desc = spec_scope.get("levelDescription")
        if isinstance(level_desc, str) and level_desc.strip():
            lines.append(f"**Nivåbeskrivelse**: {level_desc.strip()}")

        if lines:
            blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def _format_delivery_section(delivery_section: Sequence[Any]) -> str:
    """Format the delivery section as a compact Markdown table.

    Entries sharing the same service endpoint are merged so that their
    formats are combined and the most complete metadata is kept.
    """
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for entry in delivery_section:
        if not isinstance(entry, Mapping):
            continue
        delivery = entry.get("delivery")
        if not isinstance(delivery, Mapping):
            continue

        medium = delivery.get("deliveryMedium")
        if not isinstance(medium, Mapping):
            continue

        name = str(medium.get("deliveryMediumName", "")).strip()
        units = str(medium.get("unitsOfDelivery", "")).strip()

        endpoint = ""
        svc_type = ""
        service = medium.get("deliveryService")
        if isinstance(service, Mapping):
            endpoint = str(service.get("serviceEndpoint", "")).strip()
            prop = service.get("serviceProperty")
            if isinstance(prop, Mapping):
                svc_type = str(prop.get("value", "")).strip()

        formats: list[str] = []
        fmt_list = delivery.get("deliveryFormat")
        if isinstance(fmt_list, Sequence) and not isinstance(fmt_list, (str, bytes)):
            for fmt in fmt_list:
                if isinstance(fmt, Mapping):
                    fn = str(fmt.get("formatName", "")).strip()
                    if fn:
                        formats.append(fn)

        key = endpoint or name
        if key in merged:
            existing = merged[key]
            for f in formats:
                if f not in existing["formats"]:
                    existing["formats"].append(f)
            if not existing["name"] and name:
                existing["name"] = name
            if not existing["units"] and units:
                existing["units"] = units
            if not existing["type"] and svc_type:
                existing["type"] = svc_type
        else:
            merged[key] = {
                "name": name,
                "endpoint": endpoint,
                "type": svc_type,
                "formats": formats,
                "units": units,
            }
            order.append(key)

    if not merged:
        return ""

    lines = [
        "| Tjeneste | Endepunkt | Type | Format | Leveranseenheter |",
        "| --- | --- | --- | --- | --- |",
    ]
    for key in order:
        r = merged[key]
        endpoint = r["endpoint"]
        if endpoint:
            endpoint = f"[Lenke]({endpoint})"
        lines.append(
            f"| {r['name']} | {endpoint} | {r['type']} | {', '.join(r['formats'])} | {r['units']} |"
        )
    return "\n".join(lines)


def _format_reference_system_table(ref_section: Mapping[str, Any]) -> str:
    """Format the reference system section as a Markdown table.

    Deduplicates entries by EPSG code and renders a table with columns
    for code and name, similar to the AsciiDoc mockup.
    """
    spatial = ref_section.get("spatialReferenceSystem")
    if not isinstance(spatial, Sequence) or isinstance(spatial, (str, bytes)):
        return ""

    seen: set[str] = set()
    rows: list[tuple[str, str]] = []
    for entry in spatial:
        if not isinstance(entry, Mapping):
            continue
        code = str(entry.get("code", "")).strip()
        name = str(entry.get("name", "")).strip()
        if not code:
            continue
        if code in seen:
            continue
        seen.add(code)
        epsg_number = code.split(":")[-1] if ":" in code else code
        code_link = f"[{code}](https://epsg.io/{epsg_number})"
        name_link = f"[{name}](https://register.geonorge.no/epsg-koder)" if name else ""
        rows.append((code_link, name_link or code))

    if not rows:
        return ""

    lines = [
        "| EPSG-kode | Navn på referansesystem |",
        "| --- | --- |",
    ]
    for code_cell, name_cell in rows:
        lines.append(f"| {code_cell} | {name_cell} |")

    return "\n".join(lines)


def _propagate_blockquote_prefix(text: str) -> str:
    """Ensure multi-line content inside blockquotes keeps the ``>`` prefix.

    When a placeholder on a ``> `` line expands to multiple lines, only the
    first line retains the blockquote prefix.  This function detects such
    situations and prepends ``> `` to continuation lines so the entire block
    stays inside the blockquote.
    """
    if ">" not in text:
        return text

    lines = text.split("\n")
    result: list[str] = []
    in_blockquote = False

    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("> ") or stripped == ">":
            in_blockquote = True
            result.append(line)
        elif in_blockquote:
            if stripped.startswith(("#",)):
                in_blockquote = False
                result.append(line)
            elif not stripped:
                # Look ahead: if next non-empty line is not a heading or
                # already a blockquote, keep inside the blockquote.
                next_content = ""
                for j in range(i + 1, len(lines)):
                    if lines[j].strip():
                        next_content = lines[j].strip()
                        break
                if next_content and not next_content.startswith(("#", "> ")):
                    result.append(">")
                else:
                    in_blockquote = False
                    result.append(line)
            else:
                result.append(f"> {line}")
        else:
            result.append(line)

    return "\n".join(result)


def _linkify_markdown(text: str) -> str:
    if not text:
        return ""

    front_matter = ""
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            front_matter = text[: end + 4]
            body = text[end + 4 :]

    return front_matter + _linkify_markdown_body(body)


def _linkify_markdown_body(text: str) -> str:
    if not text:
        return ""

    lines = text.splitlines(keepends=True)
    in_fence = False
    linked_lines: list[str] = []

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            linked_lines.append(line)
            continue
        if in_fence:
            linked_lines.append(line)
            continue
        if stripped.startswith("<"):
            linked_lines.append(line)
            continue
        if "`" in line:
            segments = line.split("`")
            for index in range(0, len(segments), 2):
                segments[index] = _linkify_plain_text(segments[index])
            linked_lines.append("`".join(segments))
        else:
            linked_lines.append(_linkify_plain_text(line))

    return "".join(linked_lines)


def _linkify_plain_text(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        start = match.start()
        if start > 0:
            prev = text[start - 1]
            if prev in {"(", "[", "<"}:
                return match.group(0)
        prefix = text[max(0, start - 6) : start].lower()
        if "href=" in prefix or "src=" in prefix:
            return match.group(0)

        url = match.group(0)
        suffix = ""
        while url and url[-1] in _TRAILING_PUNCTUATION:
            suffix = url[-1] + suffix
            url = url[:-1]
        if not url:
            return match.group(0)
        return f"<{url}>{suffix}"

    return _URL_RE.sub(replace, text)


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
