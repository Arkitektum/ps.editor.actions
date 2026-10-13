"""Tests for the pygeoapi configuration writer for GeoPackage (SQLiteGPKG provider)."""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from geopackage.writer import write_geopackage  # noqa: E402
from pygeoapi_config.common import CRS84, OFFERED_EPSG_CODES, PLACEHOLDER  # noqa: E402
from pygeoapi_config.gpkg import (  # noqa: E402
    build_pygeoapi_gpkg_config,
    write_pygeoapi_gpkg_config,
)
from pygeoapi_config.postgis import build_pygeoapi_postgis_config  # noqa: E402

FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "shapechange_feature_types.json"
SCHEMA = PROJECT_ROOT / "tests" / "fixtures" / "pygeoapi-config-0.x.yml"

_EPSG = "http://www.opengis.net/def/crs/EPSG/0/{}"


def _fixture() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _feature(name: str, *attributes: dict, **extra) -> dict:
    return {"name": name, "attributes": list(attributes), **extra}


def _attr(name: str, type_: str = "CharacterString", cardinality: str = "0..1", **extra) -> dict:
    return {"name": name, "type": type_, "cardinality": cardinality, **extra}


def _psdata() -> dict:
    """A synthetic psdata document."""
    return {
        "identificationSection": {
            "title": "Testdatasett",
            "abstract": "Et syntetisk datasett for tester.",
            "keyword": ["arealressurs"],
            "contact": [
                {"organizationName": "Eksempeletaten", "role": "owner"},
                {
                    "organizationName": "Kontaktkontoret",
                    "electronicMailAddress": "kontakt@example.com",
                    "role": "pointOfContact",
                },
            ],
            "restriction": {"legalConstraints": {"license": "CC BY 4.0"}},
            "extent": {
                "geographicElement": {
                    "westBoundLongitude": "4.5",
                    "eastBoundLongitude": "31.2",
                    "southBoundLatitude": "57.9",
                    "northBoundLatitude": "71.2",
                }
            },
        },
    }


def _provider(config: dict, resource: str) -> dict:
    return config["resources"][resource]["providers"][0]


class GpkgConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = build_pygeoapi_gpkg_config(
            _fixture(), psdata=_psdata(), data="./bygning.gpkg"
        )

    def test_one_resource_per_feature_table(self) -> None:
        # BaseFeature is abstract, so it has no table and no collection.
        self.assertEqual(list(self.config["resources"]), ["bygning", "eiendom"])

    def test_provider_shape(self) -> None:
        provider = _provider(self.config, "bygning")
        self.assertEqual(provider["type"], "feature")
        self.assertEqual(provider["name"], "SQLiteGPKG")
        self.assertEqual(provider["data"], "./bygning.gpkg")
        self.assertEqual(provider["id_field"], "objid")
        # GeoPackage tables keep the feature type's name as it is.
        self.assertEqual(provider["table"], "Bygning")
        # SQLiteGPKG reads the geometry column from gpkg_geometry_columns.
        self.assertNotIn("geom_field", provider)
        self.assertEqual(provider["storage_crs"], _EPSG.format(25833))
        self.assertEqual(provider["crs"][0], CRS84)
        self.assertEqual(provider["crs"][1:], [_EPSG.format(code) for code in OFFERED_EPSG_CODES])

    def test_everything_but_the_provider_matches_the_postgresql_config(self) -> None:
        postgresql = build_pygeoapi_postgis_config(_fixture(), psdata=_psdata(), dbname="x")
        self.assertEqual(self.config["server"], postgresql["server"])
        self.assertEqual(self.config["metadata"], postgresql["metadata"])
        for key, resource in self.config["resources"].items():
            other = dict(postgresql["resources"][key])
            mine = dict(resource)
            other.pop("providers")
            mine.pop("providers")
            self.assertEqual(mine, other)

    def test_tables_exist_in_the_written_geopackage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            gpkg = write_geopackage(_fixture(), Path(directory) / "x.gpkg")
            connection = sqlite3.connect(gpkg)
            try:
                geometry_tables = dict(
                    connection.execute(
                        "SELECT table_name, srs_id FROM gpkg_geometry_columns"
                    ).fetchall()
                )
                for key in self.config["resources"]:
                    provider = _provider(self.config, key)
                    table = provider["table"]
                    self.assertIn(table, geometry_tables)
                    self.assertEqual(
                        provider["storage_crs"], _EPSG.format(geometry_tables[table])
                    )
                    columns = [
                        row[1]
                        for row in connection.execute(f'PRAGMA table_info("{table}")')
                    ]
                    self.assertIn(provider["id_field"], columns)
            finally:
                connection.close()

    def test_validates_against_pygeoapi_schema(self) -> None:
        try:
            import jsonschema
        except ImportError:  # pragma: no cover - optional dependency
            self.skipTest("jsonschema is not installed")
        from pygeoapi_config.common import render_pygeoapi_config

        text = render_pygeoapi_config(self.config)
        filled = yaml.safe_load(re.sub(r"\$\{[^}:]+:-([^}]*)\}", r"\1", text))
        filled["metadata"]["provider"]["url"] = "https://example.com"
        filled["metadata"]["contact"]["url"] = "https://example.com/kontakt"
        filled["metadata"]["license"]["url"] = "https://example.com/lisens"
        jsonschema.validate(filled, yaml.safe_load(SCHEMA.read_text(encoding="utf-8")))


class GpkgFeatureTypeVariantTests(unittest.TestCase):
    def test_multi_geometry_class_is_split_like_the_geopackage(self) -> None:
        feature_types = [
            _feature(
                "Bygning",
                _attr("navn"),
                _attr("område", "GM_Surface", "1"),
                _attr("posisjon", "GM_Point", "1"),
            )
        ]
        config = build_pygeoapi_gpkg_config(feature_types)
        self.assertEqual(list(config["resources"]), ["bygning-flate", "bygning-punkt"])
        self.assertEqual(
            [_provider(config, key)["table"] for key in config["resources"]],
            ["Bygning_flate", "Bygning_punkt"],
        )
        with tempfile.TemporaryDirectory() as directory:
            gpkg = write_geopackage(feature_types, Path(directory) / "x.gpkg")
            connection = sqlite3.connect(gpkg)
            try:
                tables = {
                    row[0]
                    for row in connection.execute("SELECT table_name FROM gpkg_geometry_columns")
                }
            finally:
                connection.close()
        self.assertEqual(tables, {"Bygning_flate", "Bygning_punkt"})

    def test_without_split_the_first_geometry_is_kept(self) -> None:
        config = build_pygeoapi_gpkg_config(
            [
                _feature(
                    "Bygning",
                    _attr("område", "GM_Surface", "1"),
                    _attr("posisjon", "GM_Point", "1"),
                )
            ],
            split_multi_geometry=False,
        )
        self.assertEqual(list(config["resources"]), ["bygning"])

    def test_feature_type_without_geometry_gets_no_collection(self) -> None:
        config = build_pygeoapi_gpkg_config(
            [_feature("Register", _attr("navn")), _feature("Mast", _attr("posisjon", "GM_Point", "1"))]
        )
        self.assertEqual(list(config["resources"]), ["mast"])

    def test_storage_crs_follows_the_geometry(self) -> None:
        config = build_pygeoapi_gpkg_config(
            [
                _feature("Sone", geometry={"name": "område", "type": "GM_Surface", "storageCrs": "EPSG:32633"}),
                _feature("Mast", _attr("posisjon", "GM_Point", "1")),
            ],
            default_crs=25835,
        )
        sone = _provider(config, "sone")
        self.assertEqual(sone["storage_crs"], _EPSG.format(32633))
        self.assertEqual(sone["crs"][-1], _EPSG.format(32633))
        self.assertEqual(_provider(config, "mast")["storage_crs"], _EPSG.format(25835))

    def test_abstract_types_are_skipped(self) -> None:
        config = build_pygeoapi_gpkg_config(
            [
                _feature("Felles", _attr("posisjon", "GM_Point", "1"), abstract=True),
                _feature("Konkret", relationships={"inheritance": ["Felles"]}),
            ]
        )
        # Konkret inherits its geometry from the abstract supertype.
        self.assertEqual(list(config["resources"]), ["konkret"])

    def test_missing_data_path_is_left_to_the_user(self) -> None:
        config = build_pygeoapi_gpkg_config([_feature("Mast", _attr("posisjon", "GM_Point", "1"))])
        self.assertEqual(_provider(config, "mast")["data"], PLACEHOLDER)

    def test_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_pygeoapi_gpkg_config(
                _fixture(), Path(directory) / "x.pygeoapi-gpkg.yml", data="./x.gpkg"
            )
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("# pygeoapi-konfigurasjon"))
            self.assertEqual(yaml.safe_load(text)["resources"]["bygning"]["providers"][0]["data"], "./x.gpkg")


class GenerateProductSpecTests(unittest.TestCase):
    def test_flag_writes_config_pointing_at_the_geopackage(self) -> None:
        import scripts.generate_product_spec as product_spec

        self.assertTrue(
            product_spec._parse_args(["uuid", "--pygeoapi-gpkg-output"]).pygeoapi_gpkg_output
        )
        with tempfile.TemporaryDirectory() as directory:
            assets = product_spec._build_feature_catalogue_assets(
                _fixture(),
                slug="dyrkbar-jord",
                spec_dir=Path(directory),
                prefix="xmi",
                write_pygeoapi_gpkg=True,
                psdata=_psdata(),
            )
            path = assets["pygeoapi_gpkg_path"]
            self.assertEqual(path, Path(directory) / "dyrkbar-jord_xmi.pygeoapi-gpkg.yml")
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            # The same name the GeoPackage writer gives the .gpkg next to it.
            self.assertEqual(
                loaded["resources"]["bygning"]["providers"][0]["data"], "./dyrkbar-jord_xmi.gpkg"
            )
            self.assertIsNone(assets["pygeoapi_postgis_path"])


if __name__ == "__main__":
    unittest.main()
