"""Tests for the SOSI XMI feature catalogue loader."""
from __future__ import annotations

from pathlib import Path
import unittest

from xmi.feature_catalog import load_feature_types_from_xmi

FIXTURE = Path(__file__).parent / "fixtures" / "simple_feature_catalog.xmi"


class XmiFeatureCatalogTests(unittest.TestCase):
    def test_loads_feature_types_from_fixture(self) -> None:
        feature_types = load_feature_types_from_xmi(FIXTURE)
        self.assertEqual(len(feature_types), 1)

        feature = feature_types[0]
        self.assertEqual(feature["name"], "SampleFeature")
        self.assertEqual(feature["description"], "Fixture description")

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
        self.assertEqual(nested_attributes["BASE_ATTR"]["type"], "Integer")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

