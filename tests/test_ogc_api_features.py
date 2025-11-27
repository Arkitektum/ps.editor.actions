"""Integration test for OGC API Features collections on kos-pygeoapi."""
from __future__ import annotations

import json
from pathlib import Path
import unittest

from ogc_api.feature_types import load_feature_types


class OgcApiFeaturesIntegrationTests(unittest.TestCase):
    COLLECTIONS_URL = "https://kos-pygeoapi.atkv3-dev.kartverket.cloud/collections"

    def test_load_feature_types_from_ogc_api(self) -> None:
        try:
            feature_types = load_feature_types(self.COLLECTIONS_URL)
        except RuntimeError as exc:
            self.skipTest(f"Network error fetching collections: {exc}")
        except Exception:
            raise

        out_path = Path(__file__).resolve().parent.parent / "feature_catalogue_ogc.json"
        out_path.write_text(json.dumps(feature_types, indent=2, ensure_ascii=False))

        self.assertIsInstance(feature_types, list)
        self.assertGreater(len(feature_types), 0)
        first = feature_types[0]
        self.assertIn("name", first)
        self.assertIn("attributes", first)
        self.assertIsInstance(first["attributes"], list)
        self.assertIn("geometry", first)
        self.assertIsInstance(first["geometry"], dict)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
