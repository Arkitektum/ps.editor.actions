"""Tests for the pygeoapi configuration writer for PostGIS (PostgreSQLProvider)."""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from postgis.writer import build_postgis_ddl  # noqa: E402
from pygeoapi_config.common import (  # noqa: E402
    CRS84,
    OFFERED_EPSG_CODES,
    PLACEHOLDER,
    render_pygeoapi_config,
    resource_name,
)
from pygeoapi_config.postgis import (  # noqa: E402
    build_pygeoapi_postgis_config,
    write_pygeoapi_postgis_config,
)

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
    """A synthetic psdata document with every value the writer maps."""
    return {
        "identificationSection": {
            "title": "Testdatasett",
            "abstract": "Et syntetisk datasett for tester.",
            "keyword": ["arealressurs", "jordbruk"],
            "restriction": {
                "legalConstraints": {
                    "accessConstraints": "Åpne data",
                    "license": "Creative Commons BY 4.0",
                    "licenseUrl": "https://creativecommons.org/licenses/by/4.0/",
                }
            },
            "contact": [
                {
                    "organizationName": "Eksempeletaten",
                    "electronicMailAddress": "eier@example.com",
                    "role": "owner",
                },
                {
                    "organizationName": "Kontaktkontoret",
                    "electronicMailAddress": "kontakt@example.com",
                    "role": "pointOfContact",
                },
            ],
            "extent": {
                "geographicElement": {
                    "westBoundLongitude": "4.5",
                    "eastBoundLongitude": "31,25",
                    "southBoundLatitude": "57.9",
                    "northBoundLatitude": "71.2",
                }
            },
        },
        "metadataSection": {
            "metadataIdentifier": {
                "metadataLinkage": "https://kartkatalog.geonorge.no/metadata/00000000-0000-0000-0000-000000000000"
            }
        },
    }


def _provider(config: dict, resource: str) -> dict:
    return config["resources"][resource]["providers"][0]


class ResourceNameTests(unittest.TestCase):
    def test_latinised_lowercase_kebab_case(self) -> None:
        self.assertEqual(resource_name("Bygning"), "bygning")
        self.assertEqual(resource_name("Dyrkbar jord"), "dyrkbar-jord")
        self.assertEqual(resource_name("AdministrativEnhet"), "administrativ-enhet")
        self.assertEqual(resource_name("ÅpenGrunn"), "aapen-grunn")
        self.assertEqual(resource_name("HøydeKurve"), "hoeyde-kurve")
        self.assertEqual(resource_name("FKBBygning"), "fkb-bygning")
        self.assertEqual(resource_name("Kode_liste.v2"), "kode-liste-v2")


class ResourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = build_pygeoapi_postgis_config(
            _fixture(), psdata=_psdata(), dbname="Test-data", schema="Bygg"
        )

    def test_one_resource_per_realisable_feature_type(self) -> None:
        # BaseFeature is abstract, so it has no table and no collection.
        self.assertEqual(list(self.config["resources"]), ["bygning", "eiendom"])

    def test_resource_shape(self) -> None:
        resource = self.config["resources"]["bygning"]
        self.assertEqual(resource["type"], "collection")
        self.assertEqual(resource["title"], "Bygning")
        self.assertEqual(resource["description"], "En bygning.")
        self.assertEqual(resource["keywords"], ["Bygning"])
        self.assertEqual(resource["extents"]["spatial"]["crs"], CRS84)
        self.assertEqual(resource["extents"]["spatial"]["bbox"], [4.5, 57.9, 31.25, 71.2])

    def test_provider_points_at_the_postgis_table(self) -> None:
        provider = _provider(self.config, "bygning")
        self.assertEqual(provider["type"], "feature")
        self.assertEqual(provider["name"], "PostgreSQLProvider")
        self.assertEqual(provider["table"], "bygning")
        self.assertEqual(provider["id_field"], "objid")
        self.assertEqual(provider["geom_field"], "geometry")
        self.assertEqual(provider["storage_crs"], _EPSG.format(25833))

    def test_connection_values_are_left_to_the_user(self) -> None:
        data = _provider(self.config, "bygning")["data"]
        for key in ("host", "port", "user", "password"):
            self.assertEqual(data[key], PLACEHOLDER, key)
        self.assertEqual(data["dbname"], "test_data")
        self.assertEqual(data["search_path"], ["bygg"])

    def test_offered_crs_list(self) -> None:
        crs = _provider(self.config, "bygning")["crs"]
        self.assertEqual(crs[0], CRS84)
        self.assertEqual(crs[1:], [_EPSG.format(code) for code in OFFERED_EPSG_CODES])

    def test_tables_and_columns_exist_in_the_ddl_script(self) -> None:
        ddl = build_postgis_ddl(_fixture(), schema="Bygg")
        for key in self.config["resources"]:
            provider = _provider(self.config, key)
            match = re.search(
                rf'CREATE TABLE "bygg"\."{provider["table"]}" \((.*?)\n\);', ddl, re.DOTALL
            )
            self.assertIsNotNone(match, provider["table"])
            body = match.group(1)
            self.assertIn(f'"{provider["id_field"]}" integer', body)
            self.assertIn(f'"{provider["geom_field"]}" geometry(', body)


class FeatureTypeVariantTests(unittest.TestCase):
    def test_storage_crs_follows_the_geometry_and_is_offered(self) -> None:
        config = build_pygeoapi_postgis_config(
            [
                _feature("Sone", geometry={"name": "område", "type": "GM_Surface", "storageCrs": "EPSG:32633"}),
                _feature("Punkt", geometry={"name": "posisjon", "type": "GM_Point", "storageCrs": _EPSG.format(25832)}),
            ]
        )
        sone = _provider(config, "sone")
        self.assertEqual(sone["storage_crs"], _EPSG.format(32633))
        self.assertEqual(sone["crs"][-1], _EPSG.format(32633))
        self.assertEqual(sone["geom_field"], "omraade")

        punkt = _provider(config, "punkt")
        self.assertEqual(punkt["storage_crs"], _EPSG.format(25832))
        self.assertEqual(len(punkt["crs"]), 1 + len(OFFERED_EPSG_CODES))

    def test_default_srid_applies_without_storage_crs(self) -> None:
        config = build_pygeoapi_postgis_config(
            [_feature("Sone", geometry={"name": "område", "type": "GM_Surface"})],
            default_srid=25835,
        )
        self.assertEqual(_provider(config, "sone")["storage_crs"], _EPSG.format(25835))

    def test_declared_geometry_wins_over_geometry_attributes(self) -> None:
        config = build_pygeoapi_postgis_config(
            [
                _feature(
                    "Bygning",
                    _attr("representasjonspunkt", "GM_Point", "1"),
                    geometry={"name": "område", "type": "GM_Surface"},
                )
            ]
        )
        self.assertEqual(_provider(config, "bygning")["geom_field"], "omraade")

    def test_geometry_attribute_without_declared_geometry(self) -> None:
        config = build_pygeoapi_postgis_config([_feature("Mast", _attr("posisjon", "GM_Point", "1"))])
        self.assertEqual(_provider(config, "mast")["geom_field"], "posisjon")

    def test_feature_type_without_geometry_has_no_geometry_settings(self) -> None:
        config = build_pygeoapi_postgis_config([_feature("Register", _attr("navn"))])
        provider = _provider(config, "register")
        self.assertEqual(provider["table"], "register")
        for key in ("geom_field", "crs", "storage_crs"):
            self.assertNotIn(key, provider)

    def test_abstract_and_external_types_are_skipped(self) -> None:
        config = build_pygeoapi_postgis_config(
            [
                _feature("Felles", _attr("navn"), abstract=True),
                _feature("Ekstern", _attr("navn"), external=True),
                _feature("Konkret", _attr("navn"), relationships={"inheritance": ["Felles"]}),
            ]
        )
        self.assertEqual(list(config["resources"]), ["konkret"])

    def test_colliding_resource_names_stay_distinct(self) -> None:
        config = build_pygeoapi_postgis_config([_feature("Dyrkbar jord"), _feature("DyrkbarJord")])
        self.assertEqual(list(config["resources"]), ["dyrkbar-jord", "dyrkbar-jord-2"])
        tables = [_provider(config, key)["table"] for key in config["resources"]]
        self.assertEqual(tables, ["dyrkbar_jord", "dyrkbarjord"])

    def test_title_keywords_and_missing_description(self) -> None:
        config = build_pygeoapi_postgis_config(
            [_feature("Veg", title="Veglenke", keywords=["samferdsel", " ", "veg"])]
        )
        resource = config["resources"]["veg"]
        self.assertEqual(resource["title"], "Veglenke")
        self.assertEqual(resource["keywords"], ["samferdsel", "veg"])
        self.assertEqual(resource["description"], PLACEHOLDER)

    def test_defaults_without_schema_and_dbname(self) -> None:
        data = _provider(build_pygeoapi_postgis_config([_feature("Veg")]), "veg")["data"]
        self.assertEqual(data["search_path"], ["public"])
        self.assertEqual(data["dbname"], PLACEHOLDER)


class MetadataTests(unittest.TestCase):
    def test_psdata_values(self) -> None:
        metadata = build_pygeoapi_postgis_config([], psdata=_psdata())["metadata"]
        self.assertEqual(
            metadata["identification"],
            {
                "title": "Testdatasett",
                "description": "Et syntetisk datasett for tester.",
                "keywords": ["arealressurs", "jordbruk"],
                "keywords_type": "theme",
                "terms_of_service": "Åpne data",
                "url": "https://kartkatalog.geonorge.no/metadata/00000000-0000-0000-0000-000000000000",
            },
        )
        self.assertEqual(
            metadata["license"],
            {
                "name": "Creative Commons BY 4.0",
                "url": "https://creativecommons.org/licenses/by/4.0/",
            },
        )
        self.assertEqual(metadata["provider"], {"name": "Eksempeletaten", "url": PLACEHOLDER})
        self.assertEqual(
            metadata["contact"],
            {"name": "Kontaktkontoret", "email": "kontakt@example.com", "url": PLACEHOLDER},
        )

    def test_missing_psdata_values_become_placeholders(self) -> None:
        config = build_pygeoapi_postgis_config([_feature("Veg", geometry={"type": "GM_Curve"})])
        metadata = config["metadata"]
        self.assertEqual(metadata["identification"]["title"], PLACEHOLDER)
        self.assertEqual(metadata["identification"]["keywords"], [PLACEHOLDER])
        self.assertEqual(metadata["license"]["name"], PLACEHOLDER)
        self.assertEqual(metadata["provider"]["name"], PLACEHOLDER)
        self.assertEqual(metadata["contact"]["email"], PLACEHOLDER)
        self.assertEqual(
            config["resources"]["veg"]["extents"]["spatial"]["bbox"], [PLACEHOLDER] * 4
        )

    def test_contact_without_matching_role(self) -> None:
        psdata = _psdata()
        psdata["identificationSection"]["contact"] = [
            {"organizationName": "Utgiveren", "role": "publisher"}
        ]
        metadata = build_pygeoapi_postgis_config([], psdata=psdata)["metadata"]
        self.assertEqual(metadata["provider"]["name"], PLACEHOLDER)
        self.assertEqual(metadata["contact"]["name"], PLACEHOLDER)

    def test_server_section_matches_template(self) -> None:
        config = build_pygeoapi_postgis_config([])
        self.assertEqual(config["server"]["bind"], {"host": "0.0.0.0", "port": 5000})
        self.assertEqual(config["server"]["url"], "${PYGEOAPI_SERVER_URL:-http://localhost:5000}")
        self.assertEqual(config["server"]["languages"], ["nb-NO"])
        self.assertEqual(config["logging"], {"level": "ERROR"})


class RenderTests(unittest.TestCase):
    def test_round_trips_and_quotes_placeholders(self) -> None:
        config = build_pygeoapi_postgis_config(_fixture(), psdata=_psdata(), dbname="test")
        text = render_pygeoapi_config(config)
        self.assertTrue(text.startswith("# pygeoapi-konfigurasjon"))
        self.assertIn('host: "<REPLACE_ME>"', text)
        self.assertIn("terms_of_service: Åpne data", text)
        self.assertEqual(yaml.safe_load(text), config)

    def test_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_pygeoapi_postgis_config(
                _fixture(), Path(directory) / "sub" / "x.pygeoapi-postgis.yml", psdata=_psdata()
            )
            self.assertTrue(path.exists())
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertEqual(list(loaded["resources"]), ["bygning", "eiendom"])

    def test_validates_against_pygeoapi_schema(self) -> None:
        try:
            import jsonschema
        except ImportError:  # pragma: no cover - optional dependency
            self.skipTest("jsonschema is not installed")
        text = render_pygeoapi_config(
            build_pygeoapi_postgis_config(_fixture(), psdata=_psdata(), dbname="test")
        )
        # pygeoapi expands ${VAR:-default} in the raw text before parsing and
        # validating it, so "admin" is a boolean by then.
        filled = yaml.safe_load(re.sub(r"\$\{[^}:]+:-([^}]*)\}", r"\1", text))
        # The placeholders are the user's to fill in; with them filled in, the
        # configuration must be one pygeoapi accepts.
        for key in filled["resources"]:
            filled["resources"][key]["providers"][0]["data"].update(
                host="db", port=5432, user="reader", password="${POSTGRES_PASSWORD}"
            )
        filled["metadata"]["provider"]["url"] = "https://example.com"
        filled["metadata"]["contact"]["url"] = "https://example.com/kontakt"
        schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
        jsonschema.validate(filled, schema)


class GenerateProductSpecTests(unittest.TestCase):
    def test_flag_writes_config_next_to_the_other_artefacts(self) -> None:
        import scripts.generate_product_spec as product_spec

        self.assertTrue(product_spec._parse_args(["uuid", "--pygeoapi-postgis-output"]).pygeoapi_postgis_output)
        self.assertFalse(product_spec._parse_args(["uuid"]).pygeoapi_postgis_output)

        with tempfile.TemporaryDirectory() as directory:
            assets = product_spec._build_feature_catalogue_assets(
                _fixture(),
                slug="dyrkbar-jord",
                spec_dir=Path(directory),
                write_pygeoapi_postgis=True,
                postgis_schema="jord",
                psdata=_psdata(),
            )
            path = assets["pygeoapi_postgis_path"]
            self.assertEqual(path, Path(directory) / "dyrkbar-jord.pygeoapi-postgis.yml")
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            data = loaded["resources"]["bygning"]["providers"][0]["data"]
            self.assertEqual(data["dbname"], "dyrkbar_jord")
            self.assertEqual(data["search_path"], ["jord"])

            assets = product_spec._build_feature_catalogue_assets(
                _fixture(), slug="x", spec_dir=Path(directory)
            )
            self.assertIsNone(assets["pygeoapi_postgis_path"])


if __name__ == "__main__":
    unittest.main()
