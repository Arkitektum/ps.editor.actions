"""Smoke tests for rendering Markdown and PlantUML from feature_catalogue.json."""
from __future__ import annotations

import json
from pathlib import Path
import unittest

from md.feature_types import render_feature_types_to_markdown
from puml.feature_types import render_feature_types_to_puml


CATALOG_PATH = Path(__file__).resolve().parent.parent / "feature_catalogue.json"
MD_OUTPUT_PATH = Path(__file__).resolve().parent.parent / "feature_catalogue.md"
PUML_OUTPUT_PATH = Path(__file__).resolve().parent.parent / "feature_catalogue.puml"
PUML_OUTPUT_NONOTES_PATH = Path(__file__).resolve().parent.parent / "feature_catalogue_nonotes.puml"


def _load_catalogue() -> list[dict]:
    if not CATALOG_PATH.exists():
        raise FileNotFoundError("feature_catalogue.json is missing; run integration tests first.")
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("feature_catalogue.json did not contain a non-empty list.")
    return data


class RenderFeatureCatalogueOutputsTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.feature_types = _load_catalogue()
        except (FileNotFoundError, ValueError) as exc:
            self.skipTest(str(exc))

    def test_renders_markdown(self) -> None:
        output = render_feature_types_to_markdown(self.feature_types, include_descriptions=True)
        self.assertIsInstance(output, str)
        self.assertTrue(output.strip())
        first_name = str(self.feature_types[0].get("name", "")).strip()
        if first_name:
            self.assertIn(first_name, output)
        MD_OUTPUT_PATH.write_text(output, encoding="utf-8")
        self.assertTrue(MD_OUTPUT_PATH.exists())
        self.assertGreater(len(MD_OUTPUT_PATH.read_text(encoding="utf-8").strip()), 0)

    def test_renders_puml(self) -> None:
        output = render_feature_types_to_puml(self.feature_types, title="Feature catalogue")
        self.assertIsInstance(output, str)
        self.assertTrue(output.strip())
        self.assertIn("@startuml", output)
        self.assertIn("@enduml", output)
        first_name = str(self.feature_types[0].get("name", "")).strip()
        if first_name:
            self.assertIn(first_name, output)
        PUML_OUTPUT_PATH.write_text(output, encoding="utf-8")
        self.assertTrue(PUML_OUTPUT_PATH.exists())
        self.assertGreater(len(PUML_OUTPUT_PATH.read_text(encoding="utf-8").strip()), 0)

        output_nonotes = render_feature_types_to_puml(
            self.feature_types,
            title="Feature catalogue",
            include_notes=False,
            include_descriptions=False,
        )
        self.assertIsInstance(output_nonotes, str)
        self.assertTrue(output_nonotes.strip())
        self.assertNotIn("note right of", output_nonotes)
        self.assertNotIn("'", output_nonotes)
        PUML_OUTPUT_NONOTES_PATH.write_text(output_nonotes, encoding="utf-8")
        self.assertTrue(PUML_OUTPUT_NONOTES_PATH.exists())
        self.assertGreater(len(PUML_OUTPUT_NONOTES_PATH.read_text(encoding="utf-8").strip()), 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
