"""Integration test for OGC API Features collections on kos-pygeoapi."""
from __future__ import annotations

import json
from pathlib import Path
import unittest

from ogc_api.feature_types import (
    _AttributeNode,
    _collections_url_candidates,
    _is_inline_enumeration,
    _node_to_attribute,
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
            [base, f"{base}/collections", f"{base}/collections?f=json"],
        )

    def test_candidates_keep_existing_collections(self) -> None:
        url = "https://example.com/collections"
        self.assertEqual(
            _collections_url_candidates(url),
            [url, f"{url}?f=json"],
        )

    def test_candidates_single_collection_derives_list(self) -> None:
        url = "https://example.com/collections/surveys"
        self.assertEqual(
            _collections_url_candidates(url),
            [
                url,
                "https://example.com/collections",
                "https://example.com/collections?f=json",
            ],
        )

    def test_candidates_items_derives_list(self) -> None:
        url = "https://example.com/collections/surveys/items"
        self.assertEqual(
            _collections_url_candidates(url),
            [
                url,
                "https://example.com/collections",
                "https://example.com/collections?f=json",
            ],
        )

    def test_candidates_base_with_query_derives_collections(self) -> None:
        base = "https://example.com/OGCFeatureServer?f=json"
        self.assertEqual(
            _collections_url_candidates(base),
            [
                base,
                "https://example.com/OGCFeatureServer/collections",
                "https://example.com/OGCFeatureServer/collections?f=json",
            ],
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

    def test_single_collection_url_lifted_to_list(self) -> None:
        """A URL pointing at one collection (``.../collections/{id}``) must resolve
        to the collections list, not be mistaken for the list itself."""
        single = "https://example.com/collections/surveys"
        collections = "https://example.com/collections"
        single_payload = {"id": "surveys", "title": "Survey coverages", "links": []}
        collections_payload = {
            "collections": [{"id": "surveys", "title": "Survey coverages"}],
        }

        def fake_get(url: str) -> _FakeResponse:
            if url == single:
                return _FakeResponse(json_payload=single_payload)
            if url == collections:
                return _FakeResponse(json_payload=collections_payload)
            return _FakeResponse(status_code=404)

        feature_types = load_feature_types(single, fake_get)
        self.assertEqual([ft["name"] for ft in feature_types], ["surveys"])

    def test_items_url_lifted_to_list(self) -> None:
        """A URL pointing at a collection's items must resolve to the list."""
        items = "https://example.com/collections/surveys/items"
        collections = "https://example.com/collections"
        items_payload = {"type": "FeatureCollection", "features": [], "links": []}
        collections_payload = {
            "collections": [{"id": "surveys", "title": "Survey coverages"}],
        }

        def fake_get(url: str) -> _FakeResponse:
            if url == items:
                return _FakeResponse(json_payload=items_payload)
            if url == collections:
                return _FakeResponse(json_payload=collections_payload)
            return _FakeResponse(status_code=404)

        feature_types = load_feature_types(items, fake_get)
        self.assertEqual([ft["name"] for ft in feature_types], ["surveys"])

    def test_landing_page_prefers_collections_candidate(self) -> None:
        """A service landing page returns valid JSON without a 'collections' array;
        the loader must keep looking and use the '/collections' candidate rather
        than giving up on the landing document."""
        landing = "https://example.com/felles/jordressurser"
        collections = "https://example.com/felles/jordressurser/collections"
        landing_payload = {
            "title": "Jordressurser",
            "links": [{"rel": "data", "href": collections}],
        }
        collections_payload = {
            "collections": [{"id": "jordressurser", "title": "Jordressurser"}],
        }

        def fake_get(url: str) -> _FakeResponse:
            if url == landing:
                return _FakeResponse(json_payload=landing_payload)
            if url == collections:
                return _FakeResponse(json_payload=collections_payload)
            return _FakeResponse(status_code=404)

        feature_types = load_feature_types(landing, fake_get)
        self.assertEqual([ft["name"] for ft in feature_types], ["jordressurser"])

    def test_geoserver_ignoring_accept_uses_f_json(self) -> None:
        """Some GeoServer deployments ignore the Accept header and return HTML
        unless ``?f=json`` is present; the loader must try the f=json candidate."""
        collections = "https://example.com/collections"
        f_json = "https://example.com/collections?f=json"
        collections_payload = {
            "collections": [{"id": "0", "title": "Test"}],
        }

        def fake_get(url: str) -> _FakeResponse:
            if url == collections:
                return _FakeResponse(text="<html></html>")
            if url == f_json:
                return _FakeResponse(json_payload=collections_payload)
            return _FakeResponse(status_code=404)

        feature_types = load_feature_types(collections, fake_get)
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


class InlineEnumerationNamingTests(unittest.TestCase):
    """Anonymous inline enums (type 'string' + enum) must be named after the
    property, not the JSON primitive -- otherwise every one of them collapses into
    a single 'string' codelist downstream (_collect_codelists keys on `type`)."""

    def _enum_node(self, name: str, *, attr_type: str = "string", details=None) -> _AttributeNode:
        return _AttributeNode(
            name=name,
            path=(name,),
            type=attr_type,
            details=details,
            value_domain={
                "type": "enumeration",
                "listedValues": [{"value": "1", "label": "en"}],
            },
        )

    def test_inline_enum_is_named_after_the_property(self) -> None:
        attr = _node_to_attribute(self._enum_node("losmassetype"))
        self.assertEqual(attr["type"], "Losmassetype")

    def test_distinct_properties_get_distinct_type_names(self) -> None:
        a = _node_to_attribute(self._enum_node("temakvalitet"))["type"]
        b = _node_to_attribute(self._enum_node("losmassetype"))["type"]
        self.assertEqual({a, b}, {"Temakvalitet", "Losmassetype"})
        self.assertNotIn("string", {a, b})

    def test_norwegian_letters_are_preserved(self) -> None:
        attr = _node_to_attribute(self._enum_node("målemetode"))
        self.assertEqual(attr["type"], "Målemetode")

    def test_ref_name_wins_over_property_name(self) -> None:
        node = self._enum_node(
            "kvalitet", details={"$ref": "#/definitions/Datafangstmetode"}
        )
        self.assertEqual(_node_to_attribute(node)["type"], "Datafangstmetode")

    def test_plain_scalar_without_enum_keeps_primitive_type(self) -> None:
        node = _AttributeNode(name="navn", path=("navn",), type="string")
        self.assertEqual(_node_to_attribute(node)["type"], "string")

    def test_external_codelist_reference_is_left_untouched(self) -> None:
        node = self._enum_node("kommune")
        node.value_domain = {"codeList": "https://register.example/kommune"}
        self.assertFalse(_is_inline_enumeration(node.value_domain))
        self.assertEqual(_node_to_attribute(node)["type"], "string")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
