"""Tests for repeating scope sections when scopes are provided."""
from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from md.product_specification import build_context
import scripts.generate_product_spec as product_spec


class ScopeRenderingTests(unittest.TestCase):
    def test_scopes_repeat_sections(self) -> None:
        scopes = [
            {
                "name": "datafangst",
                "url": "https://example.invalid/xmi",
                "generator": "xmi",
                "description": "Datamodell for datafangst.",
            },
            {
                "name": "innsynstjeneste",
                "url": "https://example.invalid/ogc",
                "generator": "ogc_feature_api",
                "description": "Tjeneste for innsyn i planomrader.",
            },
        ]
        psdata = {"identification": {"title": "Test spesifikasjon"}}
        context = build_context(psdata, updated="2025-12-03")

        def fake_assets(
            _feature_types: object,
            **_kwargs: object,
        ) -> dict[str, Path | str]:
            spec_dir = _kwargs.get("spec_dir")
            spec_name = ""
            if isinstance(spec_dir, Path):
                spec_name = spec_dir.name.lower()
            if "datafangst" in spec_name:
                return {
                    "json_path": Path("xmi.json"),
                    "markdown_path": Path("xmi.md"),
                    "markdown_content": "XMI_TABLE",
                    "uml_path": Path("xmi.puml"),
                    "uml_content": "XMI_UML",
                }
            return {
                "json_path": Path("ogc.json"),
                "markdown_path": Path("ogc.md"),
                "markdown_content": "OGC_TABLE",
                "uml_path": Path("ogc.puml"),
                "uml_content": "OGC_UML",
            }

        written: dict[Path, str] = {}

        def fake_write(path: Path, content: str) -> None:
            written[path] = content

        with patch.object(product_spec, "load_feature_types", return_value=[]), patch.object(
            product_spec, "load_feature_types_from_xmi", return_value=[]
        ), patch.object(
            product_spec, "_build_feature_catalogue_assets", side_effect=fake_assets
        ), patch.object(product_spec, "_write_text_file", side_effect=fake_write):
            scopes_text = product_spec._build_scope_catalogues(
                context=context,
                scopes=scopes,
                spec_dir=Path("output"),
                product_title="Test spesifikasjon",
                feature_type_filter=None,
                xmi_username="sosi",
                xmi_password="sosi",
            )

        self.assertIn("Objektkataloger:", scopes_text)
        self.assertIn("- [Objektkatalog - datafangst](datafangst/objektkatalog.md)", scopes_text)
        self.assertIn(
            "- [Objektkatalog - innsynstjeneste](innsynstjeneste/objektkatalog.md)",
            scopes_text,
        )

        xmi_path = Path("output") / "datafangst" / "objektkatalog.md"
        ogc_path = Path("output") / "innsynstjeneste" / "objektkatalog.md"
        self.assertIn(xmi_path, written)
        self.assertIn(ogc_path, written)
        self.assertIn("### Datamodell", written[xmi_path])
        self.assertIn("XMI_TABLE", written[xmi_path])
        self.assertIn("OGC_TABLE", written[ogc_path])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
