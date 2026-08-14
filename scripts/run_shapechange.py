"""Prepare ShapeChange input and evaluate its output.

ShapeChange itself is a 112 MB Java distribution, so it is executed by the
calling workflow -- the same split this repository already uses for PlantUML.
This script owns the two lightweight Python halves:

``--mode generate``
    Turn a feature catalogue (JSON, or an XMI file read through
    ``xmi.feature_catalog``) into a ShapeChange SCXML model plus a matching
    configuration document.

``--mode check``
    Read the XML log ShapeChange wrote and fail the job when it contains
    errors. ShapeChange only exits non-zero on a fatal abort, so the log is the
    authoritative result.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from shapechange.config import (  # noqa: E402
    JSON_SCHEMA_TARGET_CLASS,
    XML_SCHEMA_TARGET_CLASS,
    write_config,
)
from shapechange.log_report import (  # noqa: E402
    format_github_annotations,
    read_log_report,
)
from shapechange.scxml import write_scxml  # noqa: E402
from xmi.feature_catalog import load_feature_types_from_xmi  # noqa: E402

MODEL_FILENAME = "model.scxml"
CONFIG_FILENAME = "shapechange-config.xml"
LOG_FILENAME = "log.xml"
XSD_DIRNAME = "xsd"
JSON_DIRNAME = "jsonschema"


def _paths_for(output_dir: Path) -> dict[str, Path]:
    return {
        "output_directory": output_dir,
        "scxml_model": output_dir / MODEL_FILENAME,
        "shapechange_config": output_dir / CONFIG_FILENAME,
        "shapechange_log": output_dir / LOG_FILENAME,
        "xsd_directory": output_dir / XSD_DIRNAME,
        "json_schema_directory": output_dir / JSON_DIRNAME,
    }


def _print_paths(paths: dict[str, Path]) -> None:
    for key in (
        "output_directory",
        "scxml_model",
        "shapechange_config",
        "shapechange_log",
        "xsd_directory",
        "json_schema_directory",
    ):
        print(f"[paths] {key}={paths[key]}")


def _load_feature_types(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.feature_catalogue:
        path = Path(args.feature_catalogue)
        if not path.exists():
            raise FileNotFoundError(f"Feature catalogue '{path}' does not exist.")
        # Feature catalogues written on Windows may not be UTF-8, so fall back
        # the same way the XMI loader does rather than failing the whole run.
        raw = path.read_bytes()
        for encoding in ("utf-8", "cp1252", "latin-1"):
            try:
                data = json.loads(raw.decode(encoding))
                break
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        else:
            raise ValueError(f"Feature catalogue '{path}' is not readable JSON.")
    elif args.xmi_model:
        data = load_feature_types_from_xmi(
            args.xmi_model,
            username=args.xmi_username or "sosi",
            password=args.xmi_password or "sosi",
        )
    else:
        raise ValueError(
            "Provide either --feature-catalogue or --xmi-model to build a model."
        )

    if not isinstance(data, list):
        raise ValueError("The feature catalogue must be a JSON list of feature types.")
    return [entry for entry in data if isinstance(entry, dict)]


def _derive_schema_name(args: argparse.Namespace) -> str:
    if args.schema_name and args.schema_name.strip():
        return args.schema_name.strip()
    source = args.feature_catalogue or args.xmi_model or ""
    stem = Path(str(source)).stem.strip()
    return stem or "Applikasjonsskjema"


def _parse_targets(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _generate(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve()
    paths = _paths_for(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    feature_types = _load_feature_types(args)
    if not feature_types:
        print(
            "No feature types found in the input; ShapeChange would produce an "
            "empty schema.",
            file=sys.stderr,
        )
        return 1

    schema_name = _derive_schema_name(args)
    targets = _parse_targets(args.targets)

    write_scxml(
        feature_types,
        paths["scxml_model"],
        schema_name=schema_name,
        target_namespace=args.target_namespace,
        xmlns_prefix=args.xmlns_prefix,
        schema_version=args.schema_version,
        xsd_document=args.xsd_document,
    )

    write_config(
        paths["shapechange_config"],
        model_path=paths["scxml_model"],
        log_path=paths["shapechange_log"],
        xsd_directory=paths["xsd_directory"],
        json_directory=paths["json_schema_directory"],
        app_schema_name=schema_name,
        targets=targets,
        xsd_encoding_rule=args.xsd_encoding_rule,
        json_schema_version=args.json_schema_version,
        json_base_uri=args.json_base_uri or "",
        json_encoding_rule=args.json_encoding_rule,
        entity_type_name=args.entity_type_name,
        xml_schema_target_class=args.xml_schema_target_class,
        json_schema_target_class=args.json_schema_target_class,
        bundled_includes=args.bundled_includes,
    )

    print(f"Application schema: {schema_name}")
    print(f"Target namespace: {args.target_namespace}")
    print(f"Feature types: {len(feature_types)}")
    print(f"Targets: {', '.join(targets)}")
    print(f"Wrote ShapeChange model: {paths['scxml_model']}")
    print(f"Wrote ShapeChange configuration: {paths['shapechange_config']}")
    _print_paths(paths)
    return 0


def _write_github_output(values: dict[str, str]) -> None:
    """Append step outputs so the calling workflow can gate on the verdict."""
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        return
    with open(target, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def _write_job_summary(report: LogReport) -> None:
    """Render errors and warnings into the run's Summary page.

    Inline ``::error::``/``::warning::`` annotations scroll away with the log;
    the job summary keeps the full ShapeChange verdict -- both errors and
    warnings -- on one persistent page.
    """
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return

    lines: list[str] = ["## ShapeChange", ""]
    lines.append(
        f"**{len(report.errors)} feil, {len(report.warnings)} advarsler.**"
    )
    lines.append("")

    if report.results:
        lines.append("### Produserte skjemaer")
        for result in report.results:
            label = result.label or result.href
            lines.append(f"- `{result.target}` {label}")
        lines.append("")

    if report.errors:
        lines.append("### Feil")
        for error in report.errors:
            lines.append(f"- {error.format()}")
        lines.append("")

    if report.warnings:
        lines.append("### Advarsler")
        for warning in report.warnings:
            lines.append(f"- {warning.format()}")
        lines.append("")

    with open(target, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _check(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve()
    paths = _paths_for(output_dir)
    report = read_log_report(paths["shapechange_log"])

    for line in format_github_annotations(report):
        print(line)

    for result in report.results:
        label = result.label or result.href
        print(f"ShapeChange produced [{result.target}] {label}")

    print(
        f"ShapeChange finished with {len(report.errors)} error(s) and "
        f"{len(report.warnings)} warning(s)."
    )
    _print_paths(paths)

    _write_job_summary(report)
    _write_github_output(
        {
            "error-count": str(len(report.errors)),
            "warning-count": str(len(report.warnings)),
            "has-errors": "true" if report.errors else "false",
        }
    )

    if not report.ok:
        print("ShapeChange reported errors; see the log for details.", file=sys.stderr)
        if str(args.fail_on_error).lower() != "false":
            return 1
        # fail-on-error disabled: surface the errors but let the workflow keep
        # going so the schemas ShapeChange did produce are still committed.
        print(
            "Continuing despite ShapeChange errors (fail-on-error is disabled).",
            file=sys.stderr,
        )
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a ShapeChange SCXML model and configuration, or evaluate a "
            "ShapeChange run's log."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=("generate", "check"),
        default="generate",
        help="'generate' writes the model and configuration; 'check' evaluates the log.",
    )
    parser.add_argument(
        "--output-dir",
        default="shapechange",
        help="Directory holding the model, configuration, log and generated schemas.",
    )
    parser.add_argument(
        "--fail-on-error",
        default="true",
        choices=("true", "false"),
        help=(
            "In 'check' mode, exit non-zero when ShapeChange reported errors "
            "(default). Set 'false' to surface errors/warnings but let the "
            "workflow keep the schemas ShapeChange produced."
        ),
    )
    parser.add_argument(
        "--feature-catalogue",
        help="Path to a feature catalogue JSON file (as produced by the prepare action).",
    )
    parser.add_argument(
        "--xmi-model",
        help=(
            "Path or URL to a SOSI UML XMI feature catalogue. It is read by this "
            "repository's XMI loader; ShapeChange cannot read XMI 1.1 itself."
        ),
    )
    parser.add_argument("--xmi-username", help="Username used when downloading the XMI file.")
    parser.add_argument("--xmi-password", help="Password used when downloading the XMI file.")
    parser.add_argument(
        "--target-namespace",
        default="",
        help="Target namespace of the application schema. Required in 'generate' mode.",
    )
    parser.add_argument(
        "--xmlns-prefix",
        default="app",
        help="Namespace prefix (the 'xmlns' tagged value) of the application schema.",
    )
    parser.add_argument("--schema-version", default="1.0", help="Application schema version.")
    parser.add_argument(
        "--schema-name",
        help="Name of the application schema package. Defaults to the input file stem.",
    )
    parser.add_argument(
        "--xsd-document",
        help="File name of the generated XML Schema document. Defaults to <SchemaName>.xsd.",
    )
    parser.add_argument(
        "--targets",
        default="xsd,json",
        help="Comma-separated list of ShapeChange targets to enable: xsd, json.",
    )
    parser.add_argument(
        "--xsd-encoding-rule",
        default="iso19136_2007",
        help="ShapeChange XML Schema encoding rule (default: iso19136_2007).",
    )
    parser.add_argument(
        "--json-schema-version",
        default="2019-09",
        choices=("2020-12", "2019-09", "draft-07", "OpenApi30"),
        help="JSON Schema version produced by the JSON Schema target.",
    )
    parser.add_argument(
        "--json-base-uri",
        help="Base URI used when constructing '$id' values in the JSON Schema output.",
    )
    parser.add_argument(
        "--json-encoding-rule",
        default="defaultGeoJson",
        help="ShapeChange JSON Schema encoding rule (default: defaultGeoJson).",
    )
    parser.add_argument(
        "--entity-type-name",
        default="@type",
        help="Name of the entity type member in the JSON Schema output.",
    )
    parser.add_argument(
        "--xml-schema-target-class",
        default=XML_SCHEMA_TARGET_CLASS,
        help="Java class of the XML Schema target (changed in ShapeChange 4.0.0).",
    )
    parser.add_argument(
        "--json-schema-target-class",
        default=JSON_SCHEMA_TARGET_CLASS,
        help="Java class of the JSON Schema target (changed in ShapeChange 4.0.0).",
    )
    parser.add_argument(
        "--bundled-includes",
        action="store_true",
        help=(
            "Reference the standard rules and map entries bundled with the "
            "ShapeChange distribution instead of the copies on shapechange.net. "
            "Requires running the jar from the distribution root."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.mode == "check":
        return _check(args)

    if not args.target_namespace or not args.target_namespace.strip():
        print(
            "--target-namespace is required: no feature catalogue source carries one, "
            "and ShapeChange selects application schemas by that tagged value.",
            file=sys.stderr,
        )
        return 1

    try:
        return _generate(args)
    except Exception as error:  # pragma: no cover - defensive logging
        print(f"Failed to prepare ShapeChange input: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
