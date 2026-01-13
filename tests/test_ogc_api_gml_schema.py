"""Unit test for parsing GML schema in OGC API Features."""
from __future__ import annotations

import unittest

from ogc_api.feature_types import load_feature_types


class _FakeResponse:
    def __init__(
        self,
        *,
        json_payload=None,
        text=None,
        headers=None,
        status_code=200,
    ) -> None:
        self._json_payload = json_payload
        self.text = text
        self.headers = headers or {}
        self.status_code = status_code

    def json(self):
        if self._json_payload is None:
            raise ValueError("No JSON payload")
        return self._json_payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")


class OgcApiGmlSchemaTests(unittest.TestCase):
    def test_load_feature_types_from_gml_schema(self) -> None:
        gml_schema = """<?xml version="1.0" encoding="UTF-8"?>
<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema"
            xmlns:gml="http://www.opengis.net/gml/3.2"
            targetNamespace="http://example.com/test">
  <xsd:complexType name="TestType">
    <xsd:sequence>
      <xsd:element name="geometry" type="gml:PointPropertyType" minOccurs="1" maxOccurs="1"/>
      <xsd:element name="name" type="xsd:string" minOccurs="0" maxOccurs="1"/>
      <xsd:element name="tags" type="xsd:string" minOccurs="0" maxOccurs="unbounded"/>
    </xsd:sequence>
  </xsd:complexType>
  <xsd:element name="Test" type="TestType" substitutionGroup="gml:AbstractFeature"/>
</xsd:schema>
"""

        collections_payload = {
            "collections": [
                {
                    "id": "test",
                    "links": [
                        {
                            "rel": "http://www.opengis.net/def/rel/ogc/1.0/schema",
                            "href": "https://example.com/schema.xsd",
                        }
                    ],
                }
            ]
        }

        responses = {
            "https://example.com/collections": _FakeResponse(
                json_payload=collections_payload
            ),
            "https://example.com/schema.xsd": _FakeResponse(
                text=gml_schema,
                headers={"Content-Type": "application/xml"},
            ),
        }

        def http_get(url: str):
            return responses[url]

        feature_types = load_feature_types(
            "https://example.com/collections", http_get=http_get
        )

        self.assertEqual(len(feature_types), 1)
        feature_type = feature_types[0]
        self.assertEqual(feature_type["name"], "TestType")

        geometry = feature_type["geometry"]
        self.assertEqual(geometry.get("format"), "gml")
        self.assertEqual(geometry.get("type"), "gml")

        attributes = {attr["name"]: attr for attr in feature_type["attributes"]}
        self.assertIn("name", attributes)
        self.assertIn("tags", attributes)
        self.assertNotIn("geometry", attributes)
        self.assertEqual(attributes["tags"]["cardinality"], "0..*")

    def test_follow_collections_link_from_landing_page(self) -> None:
        landing_payload = {
            "links": [
                {
                    "rel": "http://www.opengis.net/def/rel/ogc/1.0/collections",
                    "href": "https://example.com/collections",
                }
            ]
        }
        collections_payload = {
            "collections": [
                {"id": "buildings", "description": "Buildings", "links": []}
            ]
        }

        responses = {
            "https://example.com/landing": _FakeResponse(json_payload=landing_payload),
            "https://example.com/collections": _FakeResponse(
                json_payload=collections_payload
            ),
        }

        def http_get(url: str):
            return responses[url]

        feature_types = load_feature_types(
            "https://example.com/landing", http_get=http_get
        )

        self.assertEqual(len(feature_types), 1)
        self.assertEqual(feature_types[0]["name"], "buildings")

    def test_follow_collections_link_from_data_rel(self) -> None:
        landing_payload = {
            "links": [
                {
                    "rel": "data",
                    "href": "https://example.com/collections",
                }
            ]
        }
        collections_payload = {
            "collections": [
                {"id": "roads", "description": "Roads", "links": []}
            ]
        }

        responses = {
            "https://example.com/landing": _FakeResponse(json_payload=landing_payload),
            "https://example.com/collections": _FakeResponse(
                json_payload=collections_payload
            ),
        }

        def http_get(url: str):
            return responses[url]

        feature_types = load_feature_types(
            "https://example.com/landing", http_get=http_get
        )

        self.assertEqual(len(feature_types), 1)
        self.assertEqual(feature_types[0]["name"], "roads")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
