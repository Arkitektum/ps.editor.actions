"""Assemble a product specification Markdown document from prepared artefacts."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from md.product_specification import IncludeResource, render_template  # noqa: E402


def _default_template_path() -> Path:
    return PROJECT_ROOT / "data" / "template" / "ps.md.hbs"


def _format_json_block(data: Any) -> str:
    serialized = json.dumps(data, indent=2, ensure_ascii=False)
    return f"```json\n{serialized}\n```"


def _format_image_markdown(image_path: Path, output_path: Path) -> str:
    try:
        relative = Path(os.path.relpath(image_path, output_path.parent))
    except ValueError:
        relative = image_path

    alt_text = image_path.stem.replace("_", " ").strip().capitalize() or image_path.stem
    return f"![{alt_text}]({relative.as_posix()})"


def _read_text(path: Path) -> str:
    if not path or not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def assemble_product_specification(
    psdata_path: Path,
    *,
    template_path: Path,
    output_path: Path,
    feature_catalogue_markdown: Path | None,
    feature_catalogue_uml: Path | None,
    feature_catalogue_png: Path | None,
    updated: str | None,
) -> Path:
    psdata = json.loads(psdata_path.read_text(encoding="utf-8"))

    includes: list[IncludeResource] = [
        IncludeResource("incl_psdata_json", _format_json_block(psdata)),
    ]

    table_content = _read_text(feature_catalogue_markdown) if feature_catalogue_markdown else ""
    if table_content:
        includes.append(
            IncludeResource("incl_featuretypes_table", table_content),
        )

    diagram_content = ""
    png_exists = False
    if feature_catalogue_png and feature_catalogue_png.exists():
        png_exists = True
        diagram_content = _format_image_markdown(feature_catalogue_png, output_path)

    if not png_exists:
        if feature_catalogue_uml and feature_catalogue_uml.exists():
            uml_text = _read_text(feature_catalogue_uml)
            if uml_text:
                diagram_content = f"```plantuml\n{uml_text}\n```"
        elif feature_catalogue_png:
            # PNG path was provided but does not exist; raise to flag missing artefact.
            raise FileNotFoundError(f"Feature catalogue PNG '{feature_catalogue_png}' was not found.")

    if diagram_content:
        includes.append(
            IncludeResource("incl_featuretypes_uml", diagram_content),
        )

    rendered = render_template(
        template_path,
        psdata_path,
        includes=includes,
        updated=updated,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    return output_path


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Assemble a product specification Markdown document from prepared artefacts.",
    )
    parser.add_argument(
        "psdata",
        type=Path,
        help="Path to the psdata JSON file produced by the preparation step.",
    )
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        type=Path,
        help="Output path for the rendered product specification Markdown file.",
    )
    parser.add_argument(
        "-t",
        "--template",
        type=Path,
        help="Optional override for the Handlebars-style template.",
    )
    parser.add_argument(
        "--feature-catalogue-markdown",
        type=Path,
        help="Path to the feature catalogue Markdown table.",
    )
    parser.add_argument(
        "--feature-catalogue-uml",
        type=Path,
        help="Path to the feature catalogue PlantUML source (optional, not embedded).",
    )
    parser.add_argument(
        "--feature-catalogue-png",
        type=Path,
        help="Optional path to the rendered PlantUML PNG diagram.",
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

    try:
        output = assemble_product_specification(
            args.psdata,
            template_path=template_path,
            output_path=args.output,
            feature_catalogue_markdown=args.feature_catalogue_markdown,
            feature_catalogue_uml=args.feature_catalogue_uml,
            feature_catalogue_png=args.feature_catalogue_png,
            updated=args.updated,
        )
    except Exception as error:  # pragma: no cover - defensive logging
        print(f"Failed to assemble product specification: {error}", file=sys.stderr)
        return 1

    print(f"Assembled product specification: {output}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
