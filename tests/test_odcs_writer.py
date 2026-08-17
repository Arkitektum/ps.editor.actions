"""Tests for the ODCS (Open Data Contract Standard) v3.1.0 emitter."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from odcs.writer import build_odcs, write_odcs  # noqa: E402

FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "shapechange_feature_types.json"


def _fixture() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _props(schema_object: dict) -> dict:
    return {p["name"]: p for p in schema_object.get("properties", [])}


class OdcsContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.doc = build_odcs(
            _fixture(),
            identifier="Bygning",
            model_uri="https://skjema.geonorge.no/x/bygning/1.0/modell",
        )
        self.schema = {s["name"]: s for s in self.doc["schema"]}

    def test_required_top_level_fields(self) -> None:
        self.assertEqual(self.doc["apiVersion"], "v3.1.0")
        self.assertEqual(self.doc["kind"], "DataContract")
        self.assertTrue(self.doc["id"].startswith("urn:odcs:bygning:"))
        self.assertEqual(self.doc["version"], "1.0.0")
        self.assertEqual(self.doc["status"], "active")

    def test_abstract_type_is_not_a_schema_object(self) -> None:
        self.assertIn("Bygning", self.schema)
        self.assertIn("Eiendom", self.schema)
        self.assertNotIn("BaseFeature", self.schema)

    def test_schema_object_shape(self) -> None:
        byg = self.schema["Bygning"]
        self.assertEqual(byg["logicalType"], "object")
        self.assertEqual(byg["physicalType"], "table")
        self.assertEqual(byg["physicalName"], "bygning")
        # semantic-model link (to ModellDCAT-AP-NO).
        self.assertTrue(byg["authoritativeDefinitions"][0]["url"].endswith("#Bygning"))

    def test_inherited_attribute_is_materialised(self) -> None:
        # identifikasjon comes from the abstract BaseFeature supertype.
        self.assertIn("identifikasjon", _props(self.schema["Bygning"]))

    def test_geometry_becomes_object_with_custom_properties(self) -> None:
        geom = _props(self.schema["Bygning"])["geometri"]
        self.assertEqual(geom["logicalType"], "object")
        self.assertTrue(geom["physicalType"].startswith("geometry("))
        cp = {c["property"]: c["value"] for c in geom["customProperties"]}
        self.assertEqual(cp["geometryType"], "Polygon")
        self.assertEqual(cp["crs"], "EPSG:25833")

    def test_enum_codelist_becomes_pattern_and_allowed_values(self) -> None:
        # status has listedValues (planlagt/oppfoert) and cardinality 0..* -> array.
        status = _props(self.schema["Bygning"])["status"]
        self.assertEqual(status["logicalType"], "array")
        items = status["items"]
        self.assertIn("planlagt", items["logicalTypeOptions"]["pattern"])
        allowed = next(c["value"] for c in items["customProperties"] if c["property"] == "allowedValues")
        self.assertEqual({a["value"] for a in allowed}, {"planlagt", "oppfoert"})

    def test_external_codelist_becomes_authoritative_definition(self) -> None:
        bygningstype = _props(self.schema["Bygning"])["bygningstype"]
        self.assertEqual(
            bygningstype["authoritativeDefinitions"][0]["url"],
            "https://register.geonorge.no/sosi-kodelister/bygningstype",
        )

    def test_nested_datatype_becomes_object_property(self) -> None:
        # adresse (1..*) is a nested datatype -> array of object with its own properties.
        adresse = _props(self.schema["Bygning"])["adresse"]
        self.assertEqual(adresse["logicalType"], "array")
        self.assertEqual(adresse["items"]["logicalType"], "object")
        nested = {p["name"] for p in adresse["items"]["properties"]}
        self.assertEqual(nested, {"gatenavn", "husnummer"})

    def test_cardinality_maps_to_required_and_primary_key(self) -> None:
        doc = build_odcs(
            [
                {
                    "name": "T",
                    "attributes": [
                        {"name": "lokalId", "type": "integer", "cardinality": "1", "ogcRole": "id"},
                        {"name": "valgfri", "type": "string", "cardinality": "0..1"},
                    ],
                }
            ],
            identifier="T",
        )
        props = _props(doc["schema"][0])
        self.assertTrue(props["lokalId"]["required"])
        self.assertTrue(props["lokalId"]["primaryKey"])
        self.assertNotIn("required", props["valgfri"])

    def test_write_odcs_writes_yaml_file(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            path = write_odcs(_fixture(), Path(d) / "bygning.odcs.yaml", identifier="Bygning")
            text = path.read_text(encoding="utf-8")
            self.assertIn("apiVersion: v3.1.0", text)
            self.assertIn("kind: DataContract", text)


if __name__ == "__main__":
    unittest.main()
