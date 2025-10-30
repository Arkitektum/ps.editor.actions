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


def generate_product_specification(
    metadata_id: str,
    ogc_feature_api: str,
    *,
    output_dir: Path,
    slug_override: str | None,
    template_path: Path,
    updated: str | None,
) -> dict[str, Path]:
    psdata = fetch_psdata(metadata_id)
    feature_types = load_feature_types(ogc_feature_api)

    slug = _derive_slug(metadata_id, psdata, slug_override)
    spec_dir = output_dir / slug

    psdata_filename = f"psdata_{slug}.json"
    psdata_path = spec_dir / psdata_filename
    _write_text_file(psdata_path, json.dumps(psdata, indent=2, ensure_ascii=False))

    feature_types_filename = f"{slug}_feature_types.json"
    feature_types_path = spec_dir / feature_types_filename
    _write_text_file(feature_types_path, json.dumps(feature_types, indent=2, ensure_ascii=False))

    feature_types_markdown_path = spec_dir / f"{slug}_feature_types.md"
    if feature_types:
        feature_types_markdown = render_feature_types_to_markdown(feature_types)
        _write_text_file(feature_types_markdown_path, feature_types_markdown)
    else:
        feature_types_markdown = ""
        feature_types_markdown_path.parent.mkdir(parents=True, exist_ok=True)
        feature_types_markdown_path.touch(exist_ok=True)

    uml_path = spec_dir / f"{slug}_feature_types.puml"
    if feature_types:
        identification = psdata.get("identification")
        title = ""
        if isinstance(identification, dict):
            title_value = identification.get("title")
            if isinstance(title_value, str):
                title = title_value.strip()
        uml_content = render_feature_types_to_puml(
            feature_types,
            title=f"{title} - Objekttyper" if title else None,
            package="Objekttyper",
            include_notes=False,
            include_descriptions=False,
        )
        _write_text_file(uml_path, uml_content)
    else:
        uml_content = ""
        uml_path.parent.mkdir(parents=True, exist_ok=True)
        uml_path.touch(exist_ok=True)

    includes: list[IncludeResource] = [
        IncludeResource("incl_psdata_json", _format_json_block(psdata)),
    ]
    if feature_types_markdown.strip():
        includes.append(
            IncludeResource("incl_featuretypes_table", feature_types_markdown.strip()),
        )
    if uml_content.strip():
        includes.append(
            IncludeResource(
                "incl_featuretypes_uml",
                f"```plantuml\n{uml_content.strip()}\n```",
            ),
        )

    spec_markdown = render_template(
        template_path,
        psdata_path,
        includes=includes,
        updated=updated,
    )

    spec_markdown_path = spec_dir / f"{slug}.md"
    _write_text_file(spec_markdown_path, spec_markdown)

    return {
        "directory": spec_dir,
        "psdata": psdata_path,
        "feature_catalogue_json": feature_types_path,
        "feature_catalogue_markdown": feature_types_markdown_path,
        "feature_catalogue_uml": uml_path,
        "spec_markdown": spec_markdown_path,
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate product specification artefacts from Geonorge and OGC API sources.",
    )
    parser.add_argument("metadata_id", help="Metadata UUID registered in Geonorge.")
    parser.add_argument(
        "ogc_feature_api",
        help="URL to the OGC API - Features collections endpoint providing feature type metadata.",
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
    print(f"Rendered product specification: {paths['spec_markdown']}")

    print(f"[paths] directory={paths['directory']}")
    print(f"[paths] psdata={paths['psdata']}")
    print(f"[paths] feature_catalogue_json={paths['feature_catalogue_json']}")
    print(f"[paths] feature_catalogue_markdown={paths['feature_catalogue_markdown']}")
    print(f"[paths] feature_catalogue_uml={paths['feature_catalogue_uml']}")
    print(f"[paths] spec_markdown={paths['spec_markdown']}")

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
