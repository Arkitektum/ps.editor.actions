"""Tests for the SOSI XMI feature catalogue loader."""
from __future__ import annotations

import json
from pathlib import Path
import unittest
from tempfile import TemporaryDirectory

from xmi.feature_catalog import load_feature_types_from_xmi

FIXTURE = Path(__file__).parent / "fixtures" / "simple_feature_catalog.xmi"


class XmiFeatureCatalogTests(unittest.TestCase):
    def test_loads_feature_types_from_fixture(self) -> None:
        feature_types = load_feature_types_from_xmi(FIXTURE)
        self.assertEqual(len(feature_types), 1)

        feature = feature_types[0]
        self.assertEqual(feature["name"], "SampleFeature")
        self.assertEqual(feature["description"], "Fixture description")
        self.assertFalse(feature["abstract"])

        geometry = feature["geometry"]
        self.assertEqual(geometry["type"], "GM_Surface")
        self.assertEqual(geometry["name"], "GEOM")

        attributes = {entry["name"]: entry for entry in feature["attributes"]}
        self.assertIn("STATUS", attributes)
        self.assertIn("DETAILS", attributes)

        status = attributes["STATUS"]
        self.assertEqual(status["cardinality"], "0..*")
        listed_values = status["valueDomain"]["listedValues"]
        self.assertEqual(
            listed_values,
            [
                {"value": "active", "label": "Active state"},
                {"value": "retired", "label": "Retired state"},
            ],
        )

        details = attributes["DETAILS"]
        self.assertIn("attributes", details)
        nested_attributes = {entry["name"]: entry for entry in details["attributes"]}
        self.assertEqual(nested_attributes["CHILD_NAME"]["type"], "CharacterString")
        self.assertNotIn("BASE_ATTR", nested_attributes)
        relationships = feature["relationships"]
        self.assertEqual(relationships["inheritance"], [])
        self.assertEqual(relationships["associations"], [])

    def test_downloads_remote_catalog_and_saves_json(self) -> None:
        from xmi import feature_catalog as fc

        url = (
            "https://sosi.geonorge.no/svn/SOSI/SOSI%20Del%203/Statens%20kartverk/"
            "AdministrativeEnheter_FylkerOgKommuner-20240101.xml"
        )

        if fc.requests is None:
            self.skipTest("requests is required for integration test")

        out_path = Path(__file__).resolve().parent.parent / "feature_catalogue.json"
        feature_types = load_feature_types_from_xmi(url)
        out_path.write_text(json.dumps(feature_types, indent=2, ensure_ascii=False))
        saved = json.loads(out_path.read_text())

        self.assertIsInstance(saved, list)
        self.assertGreater(len(saved), 0)
        self.assertIn("name", saved[0])
        self.assertIn("attributes", saved[0])
        self.assertIn("relationships", saved[0])
        self.assertIn("associations", saved[0]["relationships"])
        self.assertIsInstance(saved[0]["relationships"]["associations"], list)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
