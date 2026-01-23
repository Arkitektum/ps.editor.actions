"""Generate product specification artefacts from Geonorge and OGC API inputs."""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from geonorge.psdata import fetch_psdata  # noqa: E402
from md.feature_types import render_feature_types_to_markdown  # noqa: E402
from md.product_specification import (  # noqa: E402
    IncludeResource,
    build_context,
    render_product_specification,
    render_template,
)
from ogc_api.feature_types import load_feature_types  # noqa: E402
from puml.feature_types import render_feature_types_to_puml  # noqa: E402
from xmi.feature_catalog import load_feature_types_from_xmi  # noqa: E402


def _normalize_slug(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug


def _derive_slug(metadata_id: str, psdata: dict[str, Any], override: str | None) -> str:
    if override:
        slug = _normalize_slug(override)
        if slug:
            return slug

    identification = psdata.get("identification")
    if isinstance(identification, dict):
        title = identification.get("title")
        if isinstance(title, str):
            slug = _normalize_slug(title)
            if slug:
                return slug

    fallback = _normalize_slug(metadata_id)
    if fallback:
        return fallback

    return "produktspesifikasjon"


def _write_text_file(path: Path, content: str) -> None:
    if not content.endswith("\n"):
        content = f"{content}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _default_template_path() -> Path:
    return PROJECT_ROOT / "data" / "template" / "ps.md.hbs"


def _format_json_block(data: Any) -> str:
    serialized = json.dumps(data, indent=2, ensure_ascii=False)
    return f"```json\n{serialized}\n```"


_SCOPE_CATALOG_TEMPLATE = """### Datamodell
{{incl_datamodell}}
{{incl_featuretypes_xmi_uml}}

{{incl_featuretypes_xmi_table}}

{{incl_featuretypes_uml}}

{{incl_featuretypes_table}}
"""

_PNG_PLACEHOLDER = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJ"
    "TYQAAAAASUVORK5CYII="
)


def _write_placeholder_png(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(_PNG_PLACEHOLDER))


def _format_scope_level(scopes: Sequence[Mapping[str, Any]]) -> str:
    lines: list[str] = []
    for index, scope in enumerate(scopes, start=1):
        name = scope.get("name")
        scope_name = name.strip() if isinstance(name, str) and name.strip() else f"Scope {index}"
        description = scope.get("description")
        lines.append(f"#### {scope_name}")
        if isinstance(description, str) and description.strip():
            lines.append(description.strip())
        lines.append("")
    return "\n".join(line for line in lines if line is not None).strip()


def _parse_feature_type_filter(values: Sequence[str] | None) -> list[str]:
    if not values:
        return []
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        parts = [part.strip() for part in value.split(",")]
        normalized.extend(part for part in parts if part)
    return normalized


def _filter_feature_types(
    feature_types: list[dict[str, Any]],
    allowed_names: Sequence[str] | None,
) -> list[dict[str, Any]]:
    if not allowed_names:
        return feature_types
    allowed = {name.strip().lower() for name in allowed_names if name.strip()}
    if not allowed:
        return feature_types
    filtered: list[dict[str, Any]] = []
    for feature_type in feature_types:
        name = feature_type.get("name")
        if isinstance(name, str) and name.strip().lower() in allowed:
            filtered.append(feature_type)
    return filtered


def _parse_scopes(scopes_value: str | None) -> list[dict[str, Any]]:
    if not scopes_value:
        return []

    data: Any = None
    path = Path(scopes_value)
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    else:
        data = yaml.safe_load(scopes_value)

    if isinstance(data, Mapping) and "scopes" in data:
        data = data["scopes"]

    if isinstance(data, (str, bytes)) or not isinstance(data, Sequence):
        raise ValueError("Scopes must be a list of mappings or a mapping containing a scopes list.")

    scopes: list[dict[str, Any]] = []
    for entry in data:
        if isinstance(entry, Mapping):
            scopes.append(dict(entry))
    return scopes


def _normalize_scope_generator(value: str | None) -> str:
    if not value:
        return ""
    normalized = value.strip().lower().replace("-", "_")
    if normalized in {"xmi", "xmi_model"}:
        return "xmi"
    if normalized in {"ogc", "ogc_api", "ogc_feature_api"}:
        return "ogc"
    return ""


def _build_scope_catalogues(
    *,
    context: Mapping[str, Any],
    scopes: Sequence[Mapping[str, Any]],
    spec_dir: Path,
    product_title: str,
    feature_type_filter: Sequence[str] | None,
    xmi_username: str | None,
    xmi_password: str | None,
) -> str:
    if not scopes:
        return ""

    sections: list[str] = []
    for index, scope in enumerate(scopes, start=1):
        name = scope.get("name")
        scope_name = name.strip() if isinstance(name, str) and name.strip() else f"Scope {index}"
        description = scope.get("description")
        url = scope.get("url")
        generator = _normalize_scope_generator(scope.get("generator"))
        if not isinstance(url, str) or not url.strip():
            raise ValueError(f"Scope '{scope_name}' is missing a valid url.")
        if not generator:
            raise ValueError(
                f"Scope '{scope_name}' has unsupported generator '{scope.get('generator')}'."
            )

        if generator == "xmi":
            feature_types = load_feature_types_from_xmi(
                url,
                username=xmi_username or "sosi",
                password=xmi_password or "sosi",
            )
        else:
            feature_types = load_feature_types(url)

        feature_types = _filter_feature_types(feature_types, feature_type_filter)
        scope_slug = _normalize_slug(scope_name) or f"scope_{index}"
        scope_dir = spec_dir / scope_slug
        scope_title = f"{product_title} - {scope_name}".strip(" -")
        assets = _build_feature_catalogue_assets(
            feature_types,
            slug=scope_slug,
            spec_dir=scope_dir,
            product_title=scope_title,
            create_png=True,
        )

        scope_includes: list[IncludeResource] = []
        if generator == "xmi":
            if assets["markdown_content"].strip():
                scope_includes.append(
                    IncludeResource("incl_featuretypes_xmi_table", assets["markdown_content"].strip()),
                )
            if assets["uml_content"].strip():
                scope_includes.append(
                    IncludeResource(
                        "incl_featuretypes_xmi_uml",
                        f"```plantuml\n{assets['uml_content'].strip()}\n```",
                    ),
                )
        else:
            if assets["markdown_content"].strip():
                scope_includes.append(
                    IncludeResource("incl_featuretypes_table", assets["markdown_content"].strip()),
                )
            if assets["uml_content"].strip():
                scope_includes.append(
                    IncludeResource(
                        "incl_featuretypes_uml",
                        f"```plantuml\n{assets['uml_content'].strip()}\n```",
                    ),
                )

        scope_context = dict(context)
        if isinstance(description, str) and description.strip():
            scope_context["scope"] = description.strip()

        scope_markdown = render_product_specification(
            _SCOPE_CATALOG_TEMPLATE,
            scope_context,
            resources=scope_includes,
        ).rstrip()
        if scope_markdown:
            scope_path = scope_dir / "objektkatalog.md"
            _write_text_file(scope_path, scope_markdown)
            relative = Path(scope_slug) / "objektkatalog.md"
            sections.append(f"### Datamodell - {scope_name}")
            sections.append("")
            sections.append(f"- [Objektkatalog - {scope_name}]({relative.as_posix()})")

    if not sections:
        return ""
    return "\n\n".join(section for section in sections if section is not None).rstrip()


def _build_feature_catalogue_assets(
    feature_types: list[dict[str, Any]],
    *,
    slug: str,
    spec_dir: Path,
    prefix: str = "",
    product_title: str = "",
    create_png: bool = False,
) -> dict[str, Any]:
    suffix = f"{prefix}_" if prefix else ""
    base_name = f"{slug}_{suffix}feature_catalogue"

    json_path = spec_dir / f"{base_name}.json"
    _write_text_file(json_path, json.dumps(feature_types, indent=2, ensure_ascii=False))

    markdown_path = spec_dir / f"{base_name}.md"
    if feature_types:
        markdown_content = render_feature_types_to_markdown(feature_types)
        _write_text_file(markdown_path, markdown_content)
    else:
        markdown_content = ""
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.touch(exist_ok=True)

    uml_path = spec_dir / f"{base_name}.puml"
    if feature_types:
        title = f"{product_title} - Objekttyper" if product_title else None
        uml_content = render_feature_types_to_puml(
            feature_types,
            title=title,
            package="Objekttyper",
            include_notes=False,
            include_descriptions=False,
        )
        _write_text_file(uml_path, uml_content)
    else:
        uml_content = ""
        uml_path.parent.mkdir(parents=True, exist_ok=True)
        uml_path.touch(exist_ok=True)

    png_path = spec_dir / f"{base_name}.png"
    if create_png:
        _write_placeholder_png(png_path)

    return {
        "json_path": json_path,
        "markdown_path": markdown_path,
        "markdown_content": markdown_content,
        "uml_path": uml_path,
        "uml_content": uml_content,
        "png_path": png_path if create_png else None,
    }


def generate_product_specification(
    metadata_id: str,
    ogc_feature_api: str | None,
    *,
    output_dir: Path,
    slug_override: str | None,
    template_path: Path,
    updated: str | None,
    xmi_model: str | Path | None = None,
    xmi_username: str | None = None,
    xmi_password: str | None = None,
    feature_type_filter: Sequence[str] | None = None,
    scopes: Sequence[Mapping[str, Any]] | None = None,
    render_spec_markdown: bool = True,
) -> dict[str, Path | None]:
    psdata = fetch_psdata(metadata_id)
    if scopes:
        scope_level = _format_scope_level(scopes)
        if scope_level:
            scope_value = psdata.get("scope")
            if isinstance(scope_value, Mapping):
                scope_value = dict(scope_value)
            else:
                scope_value = {}
            scope_value["level"] = scope_level
            psdata["scope"] = scope_value
    ogc_feature_types: list[dict[str, Any]] = []
    if ogc_feature_api:
        ogc_feature_types = load_feature_types(ogc_feature_api)
        ogc_feature_types = _filter_feature_types(
            ogc_feature_types, feature_type_filter
        )

    xmi_feature_types: list[dict[str, Any]] = []
    if xmi_model:
        xmi_feature_types = load_feature_types_from_xmi(
            xmi_model,
            username=xmi_username or "sosi",
            password=xmi_password or "sosi",
        )
        xmi_feature_types = _filter_feature_types(
            xmi_feature_types, feature_type_filter
        )

    slug = _derive_slug(metadata_id, psdata, slug_override)
    spec_dir = output_dir / slug

    psdata_filename = f"psdata_{slug}.json"
    psdata_path = spec_dir / psdata_filename
    _write_text_file(psdata_path, json.dumps(psdata, indent=2, ensure_ascii=False))

    identification = psdata.get("identification")
    product_title = ""
    if isinstance(identification, dict):
        title_value = identification.get("title")
        if isinstance(title_value, str):
            product_title = title_value.strip()

    ogc_assets = None
    if ogc_feature_api:
        ogc_assets = _build_feature_catalogue_assets(
            ogc_feature_types,
            slug=slug,
            spec_dir=spec_dir,
            prefix="",
            product_title=product_title,
        )

    xmi_assets = None
    if xmi_model:
        xmi_assets = _build_feature_catalogue_assets(
            xmi_feature_types,
            slug=slug,
            spec_dir=spec_dir,
            prefix="xmi",
            product_title=product_title,
        )

    includes: list[IncludeResource] = [
        IncludeResource("incl_psdata_json", _format_json_block(psdata)),
    ]
    if ogc_assets:
        ogc_markdown = ogc_assets["markdown_content"]
        if ogc_markdown.strip():
            includes.append(
                IncludeResource("incl_featuretypes_table", ogc_markdown.strip()),
            )
        ogc_uml_content = ogc_assets["uml_content"]
        if ogc_uml_content.strip():
            includes.append(
                IncludeResource(
                    "incl_featuretypes_uml",
                    f"```plantuml\n{ogc_uml_content.strip()}\n```",
                ),
            )

    if xmi_assets:
        xmi_markdown = xmi_assets["markdown_content"]
        if xmi_markdown.strip():
            includes.append(
                IncludeResource("incl_featuretypes_xmi_table", xmi_markdown.strip()),
            )
        xmi_uml_content = xmi_assets["uml_content"]
        if xmi_uml_content.strip():
            includes.append(
                IncludeResource(
                    "incl_featuretypes_xmi_uml",
                    f"```plantuml\n{xmi_uml_content.strip()}\n```",
                ),
            )

    context = build_context(psdata, updated=updated)
    scope_links = _build_scope_catalogues(
        context=context,
        scopes=scopes or [],
        spec_dir=spec_dir,
        product_title=product_title,
        feature_type_filter=feature_type_filter,
        xmi_username=xmi_username,
        xmi_password=xmi_password,
    )
    if scope_links:
        scope_links_path = spec_dir / "scope_catalogues.md"
        _write_text_file(scope_links_path, scope_links)
        includes.append(IncludeResource("incl_scope_catalogues", scope_links))

    spec_markdown_path = spec_dir / "index.md"
    if render_spec_markdown:
        spec_markdown = render_template(
            template_path,
            psdata_path,
            includes=includes,
            updated=updated,
        )
        _write_text_file(spec_markdown_path, spec_markdown)

    result: dict[str, Path | None] = {
        "directory": spec_dir,
        "psdata": psdata_path,
        "feature_catalogue_json": ogc_assets["json_path"] if ogc_assets else None,
        "feature_catalogue_markdown": ogc_assets["markdown_path"] if ogc_assets else None,
        "feature_catalogue_uml": ogc_assets["uml_path"] if ogc_assets else None,
        "spec_markdown": spec_markdown_path,
        "xmi_feature_catalogue_json": xmi_assets["json_path"] if xmi_assets else None,
        "xmi_feature_catalogue_markdown": xmi_assets["markdown_path"] if xmi_assets else None,
        "xmi_feature_catalogue_uml": xmi_assets["uml_path"] if xmi_assets else None,
    }

    return result


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate product specification artefacts from Geonorge and OGC API sources.",
    )
    parser.add_argument("metadata_id", help="Metadata UUID registered in Geonorge.")
    parser.add_argument(
        "ogc_feature_api",
        nargs="?",
        help=(
            "Optional URL to the OGC API - Features collections endpoint providing feature "
            "type metadata. Omit to skip OGC feature catalogue generation or when using "
            "--xmi-model instead."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("produktspesifikasjon"),
        help="Directory where the product specification folder should be created.",
    )
    parser.add_argument(
        "--slug",
        dest="slug_override",
        help="Optional explicit slug to use for the product specification directory.",
    )
    parser.add_argument(
        "--template",
        type=Path,
        help="Path to the Handlebars-style template used for rendering the specification.",
    )
    parser.add_argument(
        "--updated",
        help="Optional override for the 'updated' metadata field in the rendered specification.",
    )
    parser.add_argument(
        "--xmi-model",
        help="Optional path or URL to a SOSI UML XMI feature catalogue. When provided the OGC API input is ignored.",
    )
    parser.add_argument(
        "--xmi-username",
        default="sosi",
        help="Optional username used when downloading the XMI file (default: sosi).",
    )
    parser.add_argument(
        "--xmi-password",
        default="sosi",
        help="Optional password used when downloading the XMI file (default: sosi).",
    )
    parser.add_argument(
        "--skip-spec-markdown",
        action="store_true",
        help="Skip rendering the final product specification Markdown document.",
    )
    parser.add_argument(
        "--feature-type-filter",
        action="append",
        help=(
            "Optional feature type name filter (exact match, case-insensitive). "
            "Use multiple times or provide a comma-separated list."
        ),
    )
    parser.add_argument(
        "--scopes",
        help=(
            "Optional YAML/JSON list of scope definitions or a path to a file containing "
            "a scopes list."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    feature_type_filter = _parse_feature_type_filter(args.feature_type_filter)
    scopes = _parse_scopes(args.scopes)

    template_path = args.template or _default_template_path()
    if not template_path.exists():
        print(f"Template '{template_path}' not found.", file=sys.stderr)
        return 1

    output_dir = args.output_dir
    if not output_dir.is_absolute():
        output_dir = Path.cwd() / output_dir

    if not args.ogc_feature_api and not args.xmi_model:
        print(
            "No OGC API or XMI source supplied; generating psdata and empty feature catalogue artefacts.",
        )

    try:
        paths = generate_product_specification(
            args.metadata_id,
            args.ogc_feature_api,
            output_dir=output_dir,
            slug_override=args.slug_override,
            template_path=template_path,
            updated=args.updated,
            xmi_model=args.xmi_model,
            xmi_username=args.xmi_username,
            xmi_password=args.xmi_password,
            feature_type_filter=feature_type_filter,
            scopes=scopes,
            render_spec_markdown=not args.skip_spec_markdown,
        )
    except Exception as error:  # pragma: no cover - defensive logging
        print(f"Failed to generate product specification: {error}", file=sys.stderr)
        return 1

    print(f"Product specification directory: {paths['directory']}")
    print(f"Wrote psdata JSON: {paths['psdata']}")
    if paths.get("feature_catalogue_json"):
        print(f"Wrote feature catalogue JSON: {paths['feature_catalogue_json']}")
        if paths["feature_catalogue_markdown"] and paths["feature_catalogue_markdown"].exists():
            print(f"Wrote feature catalogue Markdown: {paths['feature_catalogue_markdown']}")
        elif paths["feature_catalogue_markdown"]:
            print(
                "No feature catalogue Markdown generated "
                f"(reserved path: {paths['feature_catalogue_markdown']})",
            )
        if paths["feature_catalogue_uml"] and paths["feature_catalogue_uml"].exists():
            print(f"Wrote feature catalogue PlantUML: {paths['feature_catalogue_uml']}")
        elif paths["feature_catalogue_uml"]:
            print(
                "No feature catalogue PlantUML generated "
                f"(reserved path: {paths['feature_catalogue_uml']})",
            )
    else:
        print("Skipped OGC feature catalogue artefacts (no OGC API provided).")
    if args.skip_spec_markdown:
        print(
            "Skipped rendering product specification Markdown "
            f"(reserved path: {paths['spec_markdown']})",
        )
    else:
        print(f"Rendered product specification: {paths['spec_markdown']}")

    xmi_json_path = paths.get("xmi_feature_catalogue_json")
    if xmi_json_path:
        print(f"Wrote XMI feature catalogue JSON: {xmi_json_path}")
        xmi_markdown_path = paths.get("xmi_feature_catalogue_markdown")
        if xmi_markdown_path and xmi_markdown_path.exists():
            print(f"Wrote XMI feature catalogue Markdown: {xmi_markdown_path}")
        if xmi_markdown_path and not xmi_markdown_path.exists():
            print(f"No XMI feature catalogue Markdown generated (reserved path: {xmi_markdown_path})")
        xmi_uml_path = paths.get("xmi_feature_catalogue_uml")
        if xmi_uml_path and xmi_uml_path.exists():
            print(f"Wrote XMI feature catalogue PlantUML: {xmi_uml_path}")
        if xmi_uml_path and not xmi_uml_path.exists():
            print(f"No XMI feature catalogue PlantUML generated (reserved path: {xmi_uml_path})")

    print(f"[paths] directory={paths['directory']}")
    print(f"[paths] psdata={paths['psdata']}")
    print(f"[paths] feature_catalogue_json={paths.get('feature_catalogue_json') or ''}")
    print(f"[paths] feature_catalogue_markdown={paths.get('feature_catalogue_markdown') or ''}")
    print(f"[paths] feature_catalogue_uml={paths.get('feature_catalogue_uml') or ''}")
    print(f"[paths] xmi_feature_catalogue_json={paths.get('xmi_feature_catalogue_json') or ''}")
    print(f"[paths] xmi_feature_catalogue_markdown={paths.get('xmi_feature_catalogue_markdown') or ''}")
    print(f"[paths] xmi_feature_catalogue_uml={paths.get('xmi_feature_catalogue_uml') or ''}")
    print(f"[paths] spec_markdown={paths['spec_markdown']}")

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
