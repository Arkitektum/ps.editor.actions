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
from geopackage.writer import (  # noqa: E402
    _geonorge_api_url,
    _parse_geonorge_codelist,
    write_geopackage,
)


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
        # Mapping table exists with base_id/related_id + en heltalls-PK (FID) så
        # QGIS/GDAL laster den refererende laget som gyldig.
        info = list(
            self.conn.execute(f'PRAGMA table_info("{rel["mapping_table_name"]}")')
        )
        cols = {c["name"] for c in info}
        self.assertTrue({"base_id", "related_id"}.issubset(cols))
        self.assertEqual(len([c for c in info if c["pk"]]), 1)
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
        # objid is the synthetic surrogate PK (Gistools/ldproxy convention), not a
        # model attribute; lokalId is now an ordinary NOT NULL column.
        self.assertNotIn("objid", attrs)
        self.assertNotIn("ogcRole", attrs["lokalId"])
        self.assertEqual(attrs["lokalId"]["cardinality"], "1")
        self.assertIn("arealformaal", attrs)
        self.assertIn("kommunenummer", attrs)
        self.assertIn("informasjon", attrs)


class GeoPackageInheritanceTests(unittest.TestCase):
    def test_inherited_attributes_are_materialised(self) -> None:
        feature_types = [
            {
                "name": "Fellesegenskaper",
                "abstract": True,
                "attributes": [
                    {"name": "lokalId", "type": "integer", "cardinality": "1", "ogcRole": "id"},
                    {"name": "oppdateringsdato", "type": "dateTime", "cardinality": "0..1"},
                ],
            },
            {
                "name": "Vei",
                "attributes": [{"name": "navn", "type": "string", "cardinality": "0..1"}],
                "geometry": {
                    "type": "geometry-line",
                    "storageCrs": "http://www.opengis.net/def/crs/EPSG/0/25833",
                },
                "relationships": {"inheritance": ["Fellesegenskaper"], "associations": []},
            },
        ]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "m.gpkg"
        write_geopackage(feature_types, path)

        conn = sqlite3.connect(str(path))
        try:
            cols = {r[1] for r in conn.execute('PRAGMA table_info("Vei")')}
            tables = {r[0] for r in conn.execute("SELECT table_name FROM gpkg_contents")}
        finally:
            conn.close()

        # Egne OG arvede felt skal være med.
        self.assertIn("navn", cols)
        self.assertIn("lokalId", cols)
        self.assertIn("oppdateringsdato", cols)
        # Abstrakt supertype får ingen egen tabell.
        self.assertNotIn("Fellesegenskaper", tables)
        self.assertIn("Vei", tables)


class ExternalCodeListTests(unittest.TestCase):
    """External code lists (valueDomain.codeList URL) resolved to enum constraints."""

    _URL = "https://register.geonorge.no/sosi-kodelister/kommunenummer"

    def _write(self, resolver):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = Path(self._tmp.name) / "model.gpkg"
        write_geopackage(_sample_feature_types(), path, codelist_resolver=resolver)
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        return conn

    def test_resolver_materialises_enum(self) -> None:
        calls = []

        def resolver(url):
            calls.append(url)
            return [{"value": "3401", "label": "Kongsvinger"}, {"value": "3403", "label": "Hamar"}]

        conn = self._write(resolver)
        # The kommunenummer column now links to a constraint...
        cname = conn.execute(
            "SELECT constraint_name FROM gpkg_data_columns "
            "WHERE table_name='Dyrkbar jord' AND column_name='kommunenummer'"
        ).fetchone()[0]
        self.assertTrue(cname)
        # ...whose enum rows are the resolved codes.
        rows = {
            r["value"]: r["description"]
            for r in conn.execute(
                "SELECT value, description FROM gpkg_data_column_constraints "
                "WHERE constraint_name=? AND constraint_type='enum'",
                (cname,),
            )
        }
        self.assertEqual(rows, {"3401": "Kongsvinger", "3403": "Hamar"})
        # The source URL is still kept in the description for provenance.
        desc = conn.execute(
            "SELECT description FROM gpkg_data_columns "
            "WHERE table_name='Dyrkbar jord' AND column_name='kommunenummer'"
        ).fetchone()[0]
        self.assertIn("register.geonorge.no", desc)
        self.assertIn(self._URL, calls)

    def test_resolver_failure_falls_back_to_description(self) -> None:
        # Resolver returns None (e.g. network failure) -> no enum, URL in description.
        conn = self._write(lambda url: None)
        cname = conn.execute(
            "SELECT constraint_name FROM gpkg_data_columns "
            "WHERE table_name='Dyrkbar jord' AND column_name='kommunenummer'"
        ).fetchone()[0]
        self.assertIsNone(cname)
        desc = conn.execute(
            "SELECT description FROM gpkg_data_columns "
            "WHERE table_name='Dyrkbar jord' AND column_name='kommunenummer'"
        ).fetchone()[0]
        self.assertIn("register.geonorge.no", desc)


class SosiGeometryTests(unittest.TestCase):
    """SOSI UML models use Punkt/Kurve/Flate instead of GM_* (Havnedata, NRL)."""

    def _write(self, feature_types):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "sosi.gpkg"
        write_geopackage(feature_types, path)
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        return conn

    def test_sosi_geometry_types_become_feature_tables(self) -> None:
        conn = self._write(
            [
                {"name": "Punktobjekt", "attributes": [{"name": "posisjon", "type": "Punkt", "cardinality": "1"}]},
                {"name": "Linjeobjekt", "attributes": [{"name": "grense", "type": "Kurve", "cardinality": "1"}]},
                {"name": "Flateobjekt", "attributes": [{"name": "område", "type": "Flate", "cardinality": "1"}]},
            ]
        )
        contents = {r["table_name"]: r["data_type"] for r in conn.execute("SELECT table_name, data_type FROM gpkg_contents")}
        self.assertEqual(contents, {"Punktobjekt": "features", "Linjeobjekt": "features", "Flateobjekt": "features"})
        geom = {r["table_name"]: r["geometry_type_name"] for r in conn.execute("SELECT table_name, geometry_type_name FROM gpkg_geometry_columns")}
        self.assertEqual(geom, {"Punktobjekt": "POINT", "Linjeobjekt": "LINESTRING", "Flateobjekt": "MULTIPOLYGON"})

    def test_multiple_geometry_attributes_keep_one_geometry_column(self) -> None:
        # A SOSI type often has both a Flate and a Punkt representation.
        conn = self._write(
            [
                {
                    "name": "Havneanlegg",
                    "attributes": [
                        {"name": "navn", "type": "string", "cardinality": "0..1"},
                        {"name": "område", "type": "Flate", "cardinality": "1"},
                        {"name": "posisjon", "type": "Punkt", "cardinality": "1"},
                    ],
                }
            ]
        )
        self.assertEqual(
            self._scalar_conn(conn, "SELECT data_type FROM gpkg_contents WHERE table_name='Havneanlegg'"),
            "features",
        )
        # One geometry column (the first: område/Flate); the other geometry attr is not a column.
        cols = {r["name"] for r in conn.execute('PRAGMA table_info("Havneanlegg")')}
        self.assertIn("område", cols)
        self.assertNotIn("posisjon", cols)
        self.assertIn("navn", cols)

    @staticmethod
    def _scalar_conn(conn, sql):
        return conn.execute(sql).fetchone()[0]


class GeonorgeResolverUnitTests(unittest.TestCase):
    def test_api_url_inserts_api_segment(self) -> None:
        self.assertEqual(
            _geonorge_api_url("https://register.geonorge.no/sosi-kodelister/bygningstype"),
            "https://register.geonorge.no/api/sosi-kodelister/bygningstype",
        )
        # Already an API url -> unchanged; non-Geonorge -> None.
        self.assertEqual(
            _geonorge_api_url("https://register.geonorge.no/api/sosi-kodelister/x"),
            "https://register.geonorge.no/api/sosi-kodelister/x",
        )
        self.assertIsNone(_geonorge_api_url("https://example.com/codes"))

    def test_parse_codelist_extracts_valid_codes(self) -> None:
        data = {
            "containeditems": [
                {"codevalue": "60", "label": "Genererte data", "status": "Gyldig"},
                {"codevalue": "69", "label": "Beregnet", "status": "Gyldig"},
                {"codevalue": "99", "label": "Gammel", "status": "Utgått"},  # retired
                {"codevalue": "60", "label": "Duplikat", "status": "Gyldig"},  # dup
                {"label": "Uten kode", "status": "Gyldig"},  # no codevalue
            ]
        }
        self.assertEqual(
            _parse_geonorge_codelist(data),
            [{"value": "60", "label": "Genererte data"}, {"value": "69", "label": "Beregnet"}],
        )

    def test_parse_codelist_handles_bad_payload(self) -> None:
        self.assertEqual(_parse_geonorge_codelist({}), [])
        self.assertEqual(_parse_geonorge_codelist("nope"), [])


if __name__ == "__main__":
    unittest.main()
