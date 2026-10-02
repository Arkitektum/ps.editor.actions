"""Tests for repeating scope sections when scopes are provided."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from md.product_specification import build_context
import scripts.generate_product_spec as product_spec


class ScopeRenderingTests(unittest.TestCase):
    def test_scopes_repeat_sections(self) -> None:
        scopes = [
            {
                "name": "datafangst",
                "source": "https://example.invalid/xmi",
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

        self.assertIn("### Datamodell - datafangst", scopes_text)
        self.assertIn(
            'Se full datamodell for omfang "datafangst"',
            scopes_text,
        )
        self.assertIn("(datafangst/objektkatalog.html)", scopes_text)
        self.assertIn("datafangst/datafangst_feature_catalogue.png", scopes_text)
        self.assertIn("### Datamodell - innsynstjeneste", scopes_text)
        self.assertIn(
            'Se full datamodell for omfang "innsynstjeneste"',
            scopes_text,
        )
        self.assertIn("(innsynstjeneste/objektkatalog.html)", scopes_text)
        self.assertIn("innsynstjeneste/innsynstjeneste_feature_catalogue.png", scopes_text)

        xmi_path = Path("output") / "datafangst" / "objektkatalog.md"
        ogc_path = Path("output") / "innsynstjeneste" / "objektkatalog.md"
        self.assertIn(xmi_path, written)
        self.assertIn(ogc_path, written)
        self.assertIn("### Datamodell", written[xmi_path])
        self.assertIn("XMI_TABLE", written[xmi_path])
        self.assertIn("OGC_TABLE", written[ogc_path])

    def test_postgis_scope_reads_the_script_with_its_schema(self) -> None:
        scopes = [
            {
                "name": "database",
                "source": "produktspesifikasjon/adm.postgis.sql",
                "generator": "PostGIS",
                "schema": " adm ",
            }
        ]
        context = build_context({"identification": {"title": "Test"}}, updated="2025-12-03")
        assets = {
            "json_path": Path("db.json"),
            "markdown_path": Path("db.md"),
            "markdown_content": "DB_TABLE",
            "uml_path": Path("db.puml"),
            "uml_content": "DB_UML",
        }
        with patch.object(
            product_spec, "load_feature_types_from_postgis", return_value=[]
        ) as loader, patch.object(
            product_spec, "_build_feature_catalogue_assets", return_value=assets
        ), patch.object(product_spec, "_write_text_file"):
            product_spec._build_scope_catalogues(
                context=context,
                scopes=scopes,
                spec_dir=Path("output"),
                product_title="Test",
                feature_type_filter=None,
                xmi_username=None,
                xmi_password=None,
            )
        loader.assert_called_once_with("produktspesifikasjon/adm.postgis.sql", schema="adm")

    def _run_postgis_scope(self, scope: dict, *, product_slug: str | None, env: dict):
        context = build_context({"identification": {"title": "Test"}}, updated="2025-12-03")
        assets = {
            "json_path": Path("db.json"),
            "markdown_path": Path("db.md"),
            "markdown_content": "DB_TABLE",
            "uml_path": Path("db.puml"),
            "uml_content": "DB_UML",
        }
        written: dict[Path, str] = {}
        with patch.dict("os.environ", env, clear=False), patch.object(
            product_spec, "load_feature_types_from_postgis", return_value=[]
        ) as loader, patch.object(
            product_spec, "_build_feature_catalogue_assets", return_value=assets
        ), patch.object(
            product_spec, "_write_text_file", side_effect=lambda p, c: written.__setitem__(p, c)
        ):
            product_spec._build_scope_catalogues(
                context=context,
                scopes=[scope],
                spec_dir=Path("output"),
                product_title="Test",
                feature_type_filter=None,
                xmi_username=None,
                xmi_password=None,
                product_slug=product_slug,
            )
        return loader, written

    def test_postgis_scope_without_source_reads_the_schema_from_the_repository(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            schema_path = Path(workspace) / "inputs" / "arealplan" / "postgis.schema.sql"
            schema_path.parent.mkdir(parents=True)
            schema_path.write_text("CREATE TABLE a (objid integer);", encoding="utf-8")
            loader, written = self._run_postgis_scope(
                {"name": "database", "generator": "postgis"},
                product_slug="arealplan",
                env={
                    "GITHUB_WORKSPACE": workspace,
                    "GITHUB_REPOSITORY": "o/r",
                    "GITHUB_SERVER_URL": "https://github.com",
                    "GITHUB_SHA": "abc123",
                },
            )
        loader.assert_called_once_with(schema_path, schema=None)
        scope_markdown = written[Path("output") / "database" / "objektkatalog.md"]
        self.assertIn(
            "[PostGIS-skjema (SQL)](https://github.com/o/r/blob/abc123/"
            "inputs/arealplan/postgis.schema.sql)",
            scope_markdown,
        )

    def test_postgis_scope_without_source_fails_when_the_repository_has_no_schema(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaisesRegex(FileNotFoundError, "inputs/arealplan/postgis.schema.sql"):
                self._run_postgis_scope(
                    {"name": "database", "generator": "postgis"},
                    product_slug="arealplan",
                    env={"GITHUB_WORKSPACE": workspace},
                )

    def test_scope_without_source_still_fails_for_other_generators(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing a source"):
            self._run_postgis_scope(
                {"name": "database", "generator": "xmi"}, product_slug="arealplan", env={}
            )

    def test_scope_source_prefers_source_and_falls_back_to_url(self) -> None:
        self.assertEqual(
            product_spec._scope_source({"source": " a.sql ", "url": "b.sql"}), "a.sql"
        )
        self.assertEqual(product_spec._scope_source({"url": "b.sql"}), "b.sql")
        self.assertEqual(product_spec._scope_source({"source": " ", "url": "b.sql"}), "b.sql")
        self.assertEqual(product_spec._scope_source({}), "")

    def test_postgis_source_label(self) -> None:
        self.assertEqual(
            product_spec._format_source_reference("https://example.invalid/adm.xml", "postgis"),
            "**Kilde:** [PostGIS-skjema (SQL)](https://example.invalid/adm.xml)",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
