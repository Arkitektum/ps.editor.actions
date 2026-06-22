"""Integration test for OGC API Features collections on kos-pygeoapi."""
from __future__ import annotations

import json
from pathlib import Path
import unittest

from ogc_api.feature_types import (
    _collections_url_candidates,
    load_feature_types,
)


class _FakeResponse:
    def __init__(self, *, json_payload=None, text=None, status_code=200) -> None:
        self._json_payload = json_payload
        self.text = text
        self.headers = {}
        self.status_code = status_code

    def json(self):
        if self._json_payload is None:
            raise ValueError("No JSON payload")
        return self._json_payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")


class OgcApiCollectionsUrlFallbackTests(unittest.TestCase):
    def test_candidates_append_collections(self) -> None:
        base = "https://example.com/arcgis/.../OGCFeatureServer"
        self.assertEqual(
            _collections_url_candidates(base),
            [base, f"{base}/collections"],
        )

    def test_candidates_keep_existing_collections(self) -> None:
        url = "https://example.com/collections"
        self.assertEqual(_collections_url_candidates(url), [url])

    def test_candidates_preserve_query_string(self) -> None:
        base = "https://example.com/OGCFeatureServer?f=json"
        self.assertEqual(
            _collections_url_candidates(base),
            [base, "https://example.com/OGCFeatureServer/collections?f=json"],
        )

    def test_falls_back_to_collections_when_base_returns_html(self) -> None:
        base = "https://example.com/OGCFeatureServer"
        collections_payload = {
            "collections": [{"id": "0", "title": "Test"}],
        }

        def fake_get(url: str) -> _FakeResponse:
            if url == base:
                # Base service URL responds with HTML (not valid JSON).
                return _FakeResponse(text="<html></html>")
            if url == f"{base}/collections":
                return _FakeResponse(json_payload=collections_payload)
            return _FakeResponse(status_code=404)

        feature_types = load_feature_types(base, fake_get)
        self.assertEqual([ft["name"] for ft in feature_types], ["0"])


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
