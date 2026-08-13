"""Tests for the GeoPackage feature-type loader."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from geopackage.feature_types import (  # noqa: E402
    _map_column_type,
    _map_geometry_type,
    _resolve_atom_feed,
    load_feature_types_from_geopackage,
)


def _build_geopackage(path: Path) -> None:
    """Create a minimal but valid GeoPackage with one feature table."""
    connection = sqlite3.connect(str(path))
    try:
        connection.executescript(
            """
            CREATE TABLE gpkg_spatial_ref_sys (
                srs_name TEXT, srs_id INTEGER PRIMARY KEY, organization TEXT,
                organization_coordsys_id INTEGER, definition TEXT, description TEXT
            );
            CREATE TABLE gpkg_contents (
                table_name TEXT PRIMARY KEY, data_type TEXT, identifier TEXT,
                description TEXT, last_change TEXT, min_x REAL, min_y REAL,
                max_x REAL, max_y REAL, srs_id INTEGER
            );
            CREATE TABLE gpkg_geometry_columns (
                table_name TEXT, column_name TEXT, geometry_type_name TEXT,
                srs_id INTEGER, z INTEGER, m INTEGER
            );
            CREATE TABLE dyrkbar_jord (
                fid INTEGER PRIMARY KEY AUTOINCREMENT,
                geom BLOB,
                lokalId INTEGER,
                dyrkbarJord INTEGER,
                informasjon TEXT NOT NULL
            );
            CREATE TABLE metadata_only (
                id INTEGER PRIMARY KEY, note TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
            ("ETRS89 / UTM 33N", 25833, "EPSG", 25833, "GEOGCS[...]", ""),
        )
        connection.execute(
            "INSERT INTO gpkg_contents (table_name, data_type, identifier, description, srs_id) "
            "VALUES (?,?,?,?,?)",
            ("dyrkbar_jord", "features", "Dyrkbar jord", "Områder med dyrkbar jord", 25833),
        )
        # Non-feature table should be ignored.
        connection.execute(
            "INSERT INTO gpkg_contents (table_name, data_type, identifier, srs_id) VALUES (?,?,?,?)",
            ("metadata_only", "attributes", "Metadata", 25833),
        )
        connection.execute(
            "INSERT INTO gpkg_geometry_columns VALUES (?,?,?,?,?,?)",
            ("dyrkbar_jord", "geom", "MULTIPOLYGON", 25833, 0, 0),
        )
        connection.commit()
    finally:
        connection.close()


_ATOM_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Dyrkbar Jord</title>
  <entry>
    <title>GeoPackage-format, Landsdekkende</title>
    <category term="EPSG:25833" scheme="http://www.opengis.net/def/crs/" label="EPSG/0/25833"/>
    <link rel="alternate" href="https://example.test/download/25833.file" type="application/gml+xml"/>
  </entry>
  <entry>
    <title>GeoPackage-format, Landsdekkende</title>
    <category term="EPSG:4258" scheme="http://www.opengis.net/def/crs/" label="EPSG/0/4258"/>
    <link rel="alternate" href="https://example.test/download/4258.file" type="application/gml+xml"/>
  </entry>
  <entry>
    <title>GeoPackage-format, 55 Troms</title>
    <category term="EPSG:25833" scheme="http://www.opengis.net/def/crs/" label="EPSG/0/25833"/>
    <link rel="alternate" href="https://example.test/download/troms.file"/>
  </entry>
</feed>
"""


class _FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200):
        self.text = text
        self.status_code = status_code


class GeoPackageSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.gpkg_path = Path(self._tmp.name) / "sample.gpkg"
        _build_geopackage(self.gpkg_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_reads_feature_type_from_local_gpkg(self) -> None:
        feature_types = load_feature_types_from_geopackage(str(self.gpkg_path))

        # Only the 'features' table becomes a feature type.
        self.assertEqual(len(feature_types), 1)
        ft = feature_types[0]
        self.assertEqual(ft["name"], "Dyrkbar jord")
        self.assertEqual(ft["description"], "Områder med dyrkbar jord")

        geometry = ft["geometry"]
        self.assertEqual(geometry["type"], "geometry-polygon")
        self.assertEqual(geometry["ogcRole"], "primary-geometry")
        self.assertEqual(
            geometry["storageCrs"], "http://www.opengis.net/def/crs/EPSG/0/25833"
        )
        self.assertEqual(
            geometry["crs"], ["http://www.opengis.net/def/crs/EPSG/0/25833"]
        )

        attributes = {a["name"]: a for a in ft["attributes"]}
        # Geometry column is excluded from attributes.
        self.assertNotIn("geom", attributes)
        # Primary key -> mandatory + id role.
        self.assertEqual(attributes["fid"]["cardinality"], "1")
        self.assertEqual(attributes["fid"]["ogcRole"], "id")
        # NOT NULL -> mandatory.
        self.assertEqual(attributes["informasjon"]["cardinality"], "1")
        self.assertEqual(attributes["informasjon"]["type"], "string")
        # Nullable -> optional.
        self.assertEqual(attributes["lokalId"]["cardinality"], "0..1")
        self.assertEqual(attributes["lokalId"]["type"], "integer")

    def test_type_and_geometry_mapping(self) -> None:
        self.assertEqual(_map_column_type("INTEGER"), "integer")
        self.assertEqual(_map_column_type("VARCHAR(40)"), "string")
        self.assertEqual(_map_column_type("DOUBLE"), "number")
        self.assertEqual(_map_column_type("DATETIME"), "dateTime")
        self.assertEqual(_map_column_type("SOMETHING_ODD"), "string")
        self.assertEqual(_map_geometry_type("MULTIPOLYGON"), "geometry-polygon")
        self.assertEqual(_map_geometry_type("POINT"), "geometry-point")
        self.assertEqual(_map_geometry_type("LineString"), "geometry-line")


class AtomFeedResolveTests(unittest.TestCase):
    def _getter(self, url, *, auth=None, stream=False):
        return _FakeResponse(text=_ATOM_FEED)

    def test_picks_nationwide_entry_for_preferred_crs(self) -> None:
        href = _resolve_atom_feed(
            "https://example.test/feed.xml",
            crs_preference=("EPSG:25833",),
            http_get=self._getter,
        )
        self.assertEqual(href, "https://example.test/download/25833.file")

    def test_respects_crs_preference_order(self) -> None:
        href = _resolve_atom_feed(
            "https://example.test/feed.xml",
            crs_preference=("EPSG:4258", "EPSG:25833"),
            http_get=self._getter,
        )
        self.assertEqual(href, "https://example.test/download/4258.file")

    def test_prefers_nationwide_over_fylke(self) -> None:
        # Default preference (25833 first) must still choose a *Landsdekkende*
        # entry, never the "55 Troms" fylke entry that shares the same CRS.
        href = _resolve_atom_feed(
            "https://example.test/feed.xml",
            http_get=self._getter,
        )
        self.assertNotIn("troms", href)
        self.assertEqual(href, "https://example.test/download/25833.file")


if __name__ == "__main__":
    unittest.main()
