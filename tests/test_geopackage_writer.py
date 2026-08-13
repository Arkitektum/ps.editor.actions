"""Tests for the GeoPackage writer (data model -> empty .gpkg)."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from geopackage.feature_types import load_feature_types_from_geopackage  # noqa: E402
from geopackage.writer import write_geopackage  # noqa: E402


def _sample_feature_types() -> list[dict]:
    return [
        {
            "name": "Dyrkbar jord",
            "description": "Områder med dyrkbar jord",
            "geometry": {
                "itemType": "feature",
                "type": "geometry-polygon",
                "storageCrs": "http://www.opengis.net/def/crs/EPSG/0/25833",
                "crs": ["http://www.opengis.net/def/crs/EPSG/0/25833"],
                "ogcRole": "primary-geometry",
            },
            "attributes": [
                {"name": "lokalId", "type": "integer", "cardinality": "1", "ogcRole": "id"},
                {
                    "name": "arealformaal",
                    "type": "string",
                    "cardinality": "0..1",
                    "description": "Arealformål",
                    "valueDomain": {
                        "listedValues": [
                            {"value": "1001", "label": "Boligbebyggelse"},
                            {"value": "1002", "label": "Fritidsbebyggelse"},
                        ],
                        "definition": "Arealformål-koder",
                    },
                },
                {
                    "name": "kommunenummer",
                    "type": "string",
                    "cardinality": "0..1",
                    "valueDomain": {
                        "asDictionary": "true",
                        "codeList": "https://register.geonorge.no/sosi-kodelister/kommunenummer",
                    },
                },
                {"name": "informasjon", "type": "string", "cardinality": "0..1"},
            ],
            "relationships": {
                "inheritance": [],
                "associations": [
                    {"target": "Grense", "role": "avgrensesAv", "cardinality": "0..*"}
                ],
            },
        },
        {
            "name": "Grense",
            "description": "Avgrensningslinje",
            "geometry": {
                "type": "geometry-line",
                "storageCrs": "http://www.opengis.net/def/crs/EPSG/0/25833",
                "ogcRole": "primary-geometry",
            },
            "attributes": [
                {"name": "lokalId", "type": "integer", "cardinality": "1", "ogcRole": "id"}
            ],
        },
    ]


class GeoPackageWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "model.gpkg"
        write_geopackage(_sample_feature_types(), self.path)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def _scalar(self, sql, params=()):
        return self.conn.execute(sql, params).fetchone()[0]

    def test_is_valid_geopackage_header(self) -> None:
        with open(self.path, "rb") as handle:
            self.assertTrue(handle.read(16).startswith(b"SQLite format 3"))
        self.assertEqual(self._scalar("PRAGMA application_id"), 1196444487)
        self.assertEqual(self._scalar("PRAGMA user_version"), 10400)

    def test_required_srs_rows(self) -> None:
        ids = {r[0] for r in self.conn.execute("SELECT srs_id FROM gpkg_spatial_ref_sys")}
        self.assertTrue({-1, 0, 4326, 25833}.issubset(ids))

    def test_feature_tables_registered(self) -> None:
        contents = {
            r["table_name"]: r["data_type"]
            for r in self.conn.execute("SELECT table_name, data_type FROM gpkg_contents")
        }
        self.assertEqual(contents.get("Dyrkbar jord"), "features")
        self.assertEqual(contents.get("Grense"), "features")
        geom = {
            r["table_name"]: r["geometry_type_name"]
            for r in self.conn.execute(
                "SELECT table_name, geometry_type_name FROM gpkg_geometry_columns"
            )
        }
        self.assertEqual(geom.get("Dyrkbar jord"), "MULTIPOLYGON")
        self.assertEqual(geom.get("Grense"), "LINESTRING")

    def test_schema_extension_codelist(self) -> None:
        # The enum code list is materialised as constraint rows.
        rows = self.conn.execute(
            "SELECT value, description FROM gpkg_data_column_constraints "
            "WHERE constraint_type='enum' ORDER BY value"
        ).fetchall()
        values = {r["value"]: r["description"] for r in rows}
        self.assertEqual(values, {"1001": "Boligbebyggelse", "1002": "Fritidsbebyggelse"})
        # The column links to the constraint.
        constraint_name = self._scalar(
            "SELECT constraint_name FROM gpkg_data_columns "
            "WHERE table_name='Dyrkbar jord' AND column_name='arealformaal'"
        )
        self.assertTrue(constraint_name)
        # External code list -> URL kept in the column description.
        desc = self._scalar(
            "SELECT description FROM gpkg_data_columns "
            "WHERE table_name='Dyrkbar jord' AND column_name='kommunenummer'"
        )
        self.assertIn("register.geonorge.no", desc)
        # Schema extension registered.
        ext = {r[0] for r in self.conn.execute("SELECT extension_name FROM gpkg_extensions")}
        self.assertIn("gpkg_schema", ext)

    def test_related_tables_extension(self) -> None:
        rel = self.conn.execute(
            "SELECT base_table_name, related_table_name, relation_name, mapping_table_name "
            "FROM gpkgext_relations"
        ).fetchone()
        self.assertIsNotNone(rel)
        self.assertEqual(rel["base_table_name"], "Dyrkbar jord")
        self.assertEqual(rel["related_table_name"], "Grense")
        self.assertEqual(rel["relation_name"], "features")
        # Mapping table exists with the required columns.
        cols = {
            c["name"]
            for c in self.conn.execute(
                f'PRAGMA table_info("{rel["mapping_table_name"]}")'
            )
        }
        self.assertEqual(cols, {"base_id", "related_id"})
        ext = {r[0] for r in self.conn.execute("SELECT extension_name FROM gpkg_extensions")}
        self.assertIn("related_tables", ext)

    def test_round_trip_with_reader(self) -> None:
        # Read the written GeoPackage back with the #2 reader.
        feature_types = load_feature_types_from_geopackage(str(self.path))
        by_name = {ft["name"]: ft for ft in feature_types}
        self.assertEqual(set(by_name), {"Dyrkbar jord", "Grense"})

        dj = by_name["Dyrkbar jord"]
        self.assertEqual(dj["geometry"]["type"], "geometry-polygon")
        self.assertEqual(
            dj["geometry"]["storageCrs"], "http://www.opengis.net/def/crs/EPSG/0/25833"
        )
        attrs = {a["name"]: a for a in dj["attributes"]}
        self.assertNotIn("geom", attrs)
        self.assertEqual(attrs["lokalId"]["ogcRole"], "id")
        self.assertEqual(attrs["lokalId"]["cardinality"], "1")
        self.assertIn("arealformaal", attrs)
        self.assertIn("kommunenummer", attrs)
        self.assertIn("informasjon", attrs)


if __name__ == "__main__":
    unittest.main()
