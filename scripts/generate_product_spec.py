"""Generate product specification artefacts from Geonorge and OGC API inputs."""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from geonorge.psdata import fetch_psdata  # noqa: E402
from md.feature_types import render_feature_types_to_markdown  # noqa: E402
from md.product_specification import IncludeResource, render_template  # noqa: E402
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


def _build_feature_catalogue_assets(
    feature_types: list[dict[str, Any]],
    *,
    slug: str,
    spec_dir: Path,
    prefix: str = "",
    product_title: str = "",
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

    return {
        "json_path": json_path,
        "markdown_path": markdown_path,
        "markdown_content": markdown_content,
        "uml_path": uml_path,
        "uml_content": uml_content,
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
    render_spec_markdown: bool = True,
) -> dict[str, Path]:
    if not ogc_feature_api and not xmi_model:
        raise ValueError("Either an OGC API endpoint or an XMI model must be provided.")

    psdata = fetch_psdata(metadata_id)
    ogc_feature_types: list[dict[str, Any]] = []
    if ogc_feature_api:
        ogc_feature_types = load_feature_types(ogc_feature_api)

    xmi_feature_types: list[dict[str, Any]] = []
    if xmi_model:
        xmi_feature_types = load_feature_types_from_xmi(
            xmi_model,
            username=xmi_username or "sosi",
            password=xmi_password or "sosi",
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
        "feature_catalogue_json": ogc_assets["json_path"],
        "feature_catalogue_markdown": ogc_assets["markdown_path"],
        "feature_catalogue_uml": ogc_assets["uml_path"],
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
            "URL to the OGC API - Features collections endpoint providing feature type "
            "metadata. Optional when --xmi-model is supplied."
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    template_path = args.template or _default_template_path()
    if not template_path.exists():
        print(f"Template '{template_path}' not found.", file=sys.stderr)
        return 1

    output_dir = args.output_dir
    if not output_dir.is_absolute():
        output_dir = Path.cwd() / output_dir

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
            render_spec_markdown=not args.skip_spec_markdown,
        )
    except Exception as error:  # pragma: no cover - defensive logging
        print(f"Failed to generate product specification: {error}", file=sys.stderr)
        return 1

    print(f"Product specification directory: {paths['directory']}")
    print(f"Wrote psdata JSON: {paths['psdata']}")
    print(f"Wrote feature catalogue JSON: {paths['feature_catalogue_json']}")
    if paths["feature_catalogue_markdown"].exists():
        print(f"Wrote feature catalogue Markdown: {paths['feature_catalogue_markdown']}")
    else:
        print(
            "No feature catalogue Markdown generated "
            f"(reserved path: {paths['feature_catalogue_markdown']})",
        )
    if paths["feature_catalogue_uml"].exists():
        print(f"Wrote feature catalogue PlantUML: {paths['feature_catalogue_uml']}")
    else:
        print(
            "No feature catalogue PlantUML generated "
            f"(reserved path: {paths['feature_catalogue_uml']})",
        )
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
    print(f"[paths] feature_catalogue_json={paths['feature_catalogue_json']}")
    print(f"[paths] feature_catalogue_markdown={paths['feature_catalogue_markdown']}")
    print(f"[paths] feature_catalogue_uml={paths['feature_catalogue_uml']}")
    print(f"[paths] xmi_feature_catalogue_json={paths.get('xmi_feature_catalogue_json') or ''}")
    print(f"[paths] xmi_feature_catalogue_markdown={paths.get('xmi_feature_catalogue_markdown') or ''}")
    print(f"[paths] xmi_feature_catalogue_uml={paths.get('xmi_feature_catalogue_uml') or ''}")
    print(f"[paths] spec_markdown={paths['spec_markdown']}")

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
