"""Tests for reading feature types back out of a PostGIS DDL script."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from postgis.feature_types import (  # noqa: E402
    feature_types_from_sql,
    load_feature_types_from_postgis,
    split_comment,
)
from postgis.sql import SqlSyntaxError, parse_sql  # noqa: E402
from postgis.writer import build_postgis_ddl, write_postgis_ddl  # noqa: E402
from xmi.feature_catalog import load_feature_types_from_xmi  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
_URL = "https://register.geonorge.no/sosi-kodelister/kommunenummer"


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, indent=1)


def _by_name(feature_types) -> dict:
    return {ft["name"]: ft for ft in feature_types}


def _attributes(feature_type) -> dict:
    return {a["name"]: a for a in feature_type["attributes"]}


def _edge_case_model() -> list[dict]:
    """Everything the flattened table layout loses, in one model."""
    return [
        {
            "name": "Basis",
            "abstract": True,
            "description": "",
            "attributes": [
                {
                    "name": "kvalitet",
                    "type": "Posisjonskvalitet",
                    "cardinality": "0..1",
                    "description": "Kvalitet",
                    "attributes": [
                        {
                            "name": "målemetode",
                            "type": "Målemetode",
                            "cardinality": "1",
                            "valueDomain": {
                                "kind": "codeList",
                                "listedValues": [
                                    {"value": "10", "label": "Terrengmålt"},
                                    {"value": "96", "label": "Uspesifisert"},
                                ],
                            },
                        },
                        {"name": "merknader", "type": "CharacterString", "cardinality": "0..*"},
                    ],
                }
            ],
            "relationships": {
                "inheritance": [],
                "associations": [{"target": "Eiendom", "role": "ligger på", "cardinality": "0..1"}],
            },
        },
        {
            "name": "Bygning",
            "description": "En bygning",
            "attributes": [
                {"name": "omriss", "type": "GM_Surface", "cardinality": "0..1"},
                {"name": "punkt", "type": "GM_Point", "cardinality": "1", "description": ""},
                {
                    "name": "status",
                    "type": "Status",
                    "cardinality": "1",
                    "valueDomain": {
                        "kind": "enumeration",
                        "listedValues": [
                            {"value": "gjeldende", "label": "Gjeldende"},
                            {"value": "utgått"},
                        ],
                    },
                },
                {
                    "name": "kommune",
                    "type": "Kommunenummer",
                    "cardinality": "0..1",
                    "description": "Kommunen",
                    "valueDomain": {"codeList": _URL, "asDictionary": "true"},
                },
                {"name": "nabokommune", "type": "Kommunenummer", "cardinality": "0..*", "valueDomain": {"codeList": _URL}},
                {
                    "name": "etasje",
                    "type": "Etasje",
                    "cardinality": "1..*",
                    "attributes": [
                        {"name": "nummer", "type": "Integer", "cardinality": "1"},
                        {"name": "areal", "type": "Real"},
                    ],
                },
            ],
            "relationships": {"inheritance": ["Basis"], "associations": []},
        },
        {
            "name": "Eiendom",
            "attributes": [
                {
                    "name": "matrikkelnummer",
                    "type": "CharacterString",
                    "cardinality": "1",
                    "taggedValues": {"SOSI_navn": "MNR"},
                }
            ],
            "relationships": {
                "inheritance": [],
                "associations": [
                    {
                        "target": "Bygning",
                        "role": "bygning",
                        "cardinality": "0..*",
                        "sourceRole": "eiendom",
                        "sourceCardinality": "0..*",
                    }
                ],
            },
        },
        {
            "name": "Anlegg",
            "attributes": [{"name": "type", "type": "CharacterString"}],
            "relationships": {"inheritance": ["Eiendom"], "associations": []},
        },
        {
            "name": "Kartlag",
            "description": "OGC-stil",
            "geometry": {
                "itemType": "feature",
                "type": "geometry-polygon",
                "storageCrs": "http://www.opengis.net/def/crs/EPSG/0/25832",
                "crs": ["http://www.opengis.net/def/crs/EPSG/0/25832"],
                "ogcRole": "primary-geometry",
            },
            "attributes": [
                {"name": "id", "type": "integer", "cardinality": "1", "ogcRole": "id"},
                {"name": "navn", "type": "string", "cardinality": "0..1"},
            ],
        },
    ]


class RoundTripTests(unittest.TestCase):
    """A script the writer produced reads back into the model it came from."""

    def assertRoundTrip(self, feature_types, **options) -> None:
        back = feature_types_from_sql(build_postgis_ddl(feature_types, **options), schema=options.get("schema"))
        self.assertEqual(_canonical(back), _canonical(feature_types))

    def test_sample_catalogue(self) -> None:
        self.assertRoundTrip(json.loads((PROJECT_ROOT / "feature_catalogue.json").read_text(encoding="utf-8")))

    def test_sample_catalogue_in_a_schema(self) -> None:
        self.assertRoundTrip(
            json.loads((PROJECT_ROOT / "feature_catalogue.json").read_text(encoding="utf-8")),
            schema="Administrative enheter",
        )

    def test_xmi_models(self) -> None:
        for fixture in ("simple_feature_catalog.xmi", "external_references.xmi"):
            with self.subTest(fixture=fixture):
                self.assertRoundTrip(load_feature_types_from_xmi(FIXTURES / fixture))

    def test_shapechange_fixture(self) -> None:
        self.assertRoundTrip(
            json.loads((FIXTURES / "shapechange_feature_types.json").read_text(encoding="utf-8"))
        )

    def test_everything_the_table_layout_loses(self) -> None:
        self.assertRoundTrip(_edge_case_model())

    def test_code_lists_resolved_from_the_register(self) -> None:
        # The register's values end up in the database, but the model only had
        # the URL; reading back gives the model, not the register.
        self.assertRoundTrip(
            _edge_case_model(),
            codelist_resolver=lambda _url: [
                {"value": "0301", "label": "Oslo"},
                {"value": "1103", "label": "Stavanger's"},
            ],
        )

    def test_through_a_file(self) -> None:
        model = _edge_case_model()
        with tempfile.TemporaryDirectory() as tmp:
            path = write_postgis_ddl(model, Path(tmp) / "model.postgis.sql", schema="bygg")
            self.assertEqual(
                _canonical(load_feature_types_from_postgis(path, schema="bygg")), _canonical(model)
            )


class MetadataTests(unittest.TestCase):
    def test_split_comment(self) -> None:
        self.assertEqual(split_comment('Tekst\n\n@ps {"a":1}'), ("Tekst", {"a": 1}))
        self.assertEqual(split_comment('@ps {"a":1}'), (None, {"a": 1}))
        self.assertEqual(split_comment("Bare tekst"), ("Bare tekst", None))
        self.assertEqual(split_comment("Tekst\n\n@ps ikke json"), ("Tekst\n\n@ps ikke json", None))
        self.assertEqual(split_comment(None), (None, None))

    def test_code_values_are_not_written_twice(self) -> None:
        ddl = build_postgis_ddl(_edge_case_model())
        self.assertIn('"listedValues":"@rows"', ddl)
        # A CHECK cannot hold labels, so an enumeration with labels keeps them.
        self.assertIn('"label":"Gjeldende"', ddl)


class StructureOnlyTests(unittest.TestCase):
    """Without the model metadata, the table structure is read in reverse."""

    def setUp(self) -> None:
        ddl = build_postgis_ddl(_edge_case_model(), model_metadata=False)
        self.types = _by_name(feature_types_from_sql(ddl))

    def test_class_names_come_from_objtype(self) -> None:
        self.assertEqual(set(self.types), {"Bygning", "Eiendom", "Anlegg", "Kartlag"})

    def test_flattened_columns_and_names_stay_as_in_the_database(self) -> None:
        attributes = _attributes(self.types["Bygning"])
        self.assertIn("kvalitet_maalemetode", attributes)
        self.assertEqual(attributes["kvalitet_maalemetode"]["cardinality"], "0..1")

    def test_code_lists_enumerations_and_register_urls(self) -> None:
        attributes = _attributes(self.types["Bygning"])
        self.assertEqual(
            attributes["kvalitet_maalemetode"]["valueDomain"]["listedValues"],
            [{"value": "10", "label": "Terrengmålt"}, {"value": "96", "label": "Uspesifisert"}],
        )
        self.assertEqual(
            attributes["status"]["valueDomain"],
            {"kind": "enumeration", "listedValues": [{"value": "gjeldende"}, {"value": "utgått"}]},
        )
        self.assertEqual(attributes["kommune"]["valueDomain"], {"codeList": _URL})
        self.assertEqual(attributes["kommune"]["description"], "Kommunen")

    def test_several_geometries_become_attributes(self) -> None:
        attributes = _attributes(self.types["Bygning"])
        self.assertEqual(attributes["omriss"]["type"], "GM_MultiSurface")
        self.assertEqual(attributes["punkt"]["type"], "GM_Point")
        self.assertNotIn("geometry", self.types["Bygning"])

    def test_single_geometry_becomes_the_geometry(self) -> None:
        geometry = self.types["Kartlag"]["geometry"]
        self.assertEqual(geometry["type"], "geometry-polygon")
        self.assertEqual(geometry["storageCrs"], "http://www.opengis.net/def/crs/EPSG/0/25832")

    def test_child_tables_become_repeating_attributes(self) -> None:
        attributes = _attributes(self.types["Bygning"])
        self.assertEqual(attributes["etasje"]["cardinality"], "0..*")
        self.assertEqual([a["name"] for a in attributes["etasje"]["attributes"]], ["nummer", "areal"])
        self.assertEqual(attributes["kvalitet_merknader"]["cardinality"], "0..*")
        self.assertEqual(attributes["kvalitet_merknader"]["type"], "string")

    def test_foreign_keys_and_join_tables_become_associations(self) -> None:
        # The association points at Eiendom, which Anlegg specialises, so the writer
        # made one column per table that can hold an Eiendom.
        bygning = self.types["Bygning"]["relationships"]["associations"]
        self.assertEqual(
            bygning,
            [
                {"target": "Eiendom", "role": "ligger_paa_eiendom", "cardinality": "0..1", "sourceCardinality": "0..*"},
                {"target": "Anlegg", "role": "ligger_paa_anlegg", "cardinality": "0..1", "sourceCardinality": "0..*"},
            ],
        )
        eiendom = self.types["Eiendom"]["relationships"]["associations"]
        self.assertEqual(eiendom[0]["target"], "Bygning")
        self.assertEqual(eiendom[0]["cardinality"], "0..*")


_PG_DUMP = r"""
--
-- PostgreSQL database dump
--

\restrict 3fKq9
SET statement_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);

CREATE SCHEMA adm;
ALTER SCHEMA adm OWNER TO eier;

CREATE FUNCTION adm.touch() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  NEW.oppdatert := now(); -- a semicolon inside the body
  RETURN NEW;
END;
$$;

SET default_tablespace = '';

CREATE TABLE adm.kommune (
    objid integer NOT NULL,
    objtype text DEFAULT 'Kommune'::text NOT NULL,
    kommunenummer text NOT NULL,
    status text,
    omraade public.geometry(MultiPolygon,25833) NOT NULL,
    CONSTRAINT kommune_status_check CHECK ((status = ANY (ARRAY['gjeldende'::text, 'utgått'::text])))
);

ALTER TABLE adm.kommune OWNER TO eier;

COMMENT ON TABLE adm.kommune IS 'Kommune i Norge';
COMMENT ON COLUMN adm.kommune.kommunenummer IS 'Nummer; fire siffer, f.eks. ''0301''';

ALTER TABLE adm.kommune ALTER COLUMN objid ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME adm.kommune_objid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE adm.spraakkode (
    identifier text NOT NULL,
    description text
);

CREATE TABLE adm.kommune_navn (
    objid integer NOT NULL,
    kommune_fk integer NOT NULL,
    navn text NOT NULL,
    spraak text
);

COPY adm.spraakkode (identifier, description) FROM stdin;
nob	Norsk bokmål
sme	\N
\.

ALTER TABLE ONLY adm.kommune
    ADD CONSTRAINT kommune_pkey PRIMARY KEY (objid);
ALTER TABLE ONLY adm.spraakkode
    ADD CONSTRAINT spraakkode_pkey PRIMARY KEY (identifier);
ALTER TABLE ONLY adm.kommune_navn
    ADD CONSTRAINT kommune_navn_pkey PRIMARY KEY (objid);
CREATE INDEX kommune_omraade_gist ON adm.kommune USING gist (omraade);
ALTER TABLE ONLY adm.kommune_navn
    ADD CONSTRAINT kommune_navn_kommune_fk_fkey FOREIGN KEY (kommune_fk) REFERENCES adm.kommune(objid) ON DELETE CASCADE;
ALTER TABLE ONLY adm.kommune_navn
    ADD CONSTRAINT kommune_navn_spraak_fkey FOREIGN KEY (spraak) REFERENCES adm.spraakkode(identifier);

\unrestrict 3fKq9
"""

# Shaped like the Gistools .NET generator's output: serial keys, OIDS, one INSERT
# per code value, constraints added afterwards, an untyped geometry column whose
# SRID lives in an enforce_srid CHECK, and an AddGeometryColumn one.
_GISTOOLS = """
CREATE SCHEMA planer AUTHORIZATION eier;
CREATE TABLE planer.arealformaal (
identifier text NOT NULL,
description text)
WITH (OIDS=FALSE);

INSERT INTO planer.arealformaal (identifier, description) VALUES ('1001', 'Boligbebyggelse');
INSERT INTO planer.arealformaal (identifier, description) VALUES ('1002', 'Fritidsbebyggelse');

CREATE TABLE planer.planomraade (
objid serial NOT NULL,
objtype text DEFAULT 'Planområde',
lokalid text UNIQUE,
arealformaal text,
omraade geometry)
WITH (OIDS=FALSE);

CREATE TABLE planer.planpunkt (
objid serial NOT NULL,
objtype text DEFAULT 'Planpunkt')
WITH (OIDS=FALSE);
SELECT AddGeometryColumn('planer', 'planpunkt', 'posisjon', 25832, 'POINT', 2);

ALTER TABLE planer.arealformaal ADD PRIMARY KEY (identifier);
ALTER TABLE planer.planomraade ADD PRIMARY KEY (objid);
ALTER TABLE planer.planpunkt ADD PRIMARY KEY (objid);
ALTER TABLE planer.planomraade ADD FOREIGN KEY (arealformaal) REFERENCES planer.arealformaal(identifier) MATCH SIMPLE ON UPDATE NO ACTION ON DELETE NO ACTION;
ALTER TABLE planer.planomraade ADD CONSTRAINT enforce_dims_omraade CHECK (st_ndims(omraade) = 2);
ALTER TABLE planer.planomraade ADD CONSTRAINT enforce_srid_omraade CHECK (st_srid(omraade) = 25832);
GRANT USAGE ON SCHEMA planer TO leser;
"""


class PgDumpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.types = _by_name(feature_types_from_sql(_PG_DUMP, schema="adm"))

    def test_only_feature_tables_become_types(self) -> None:
        self.assertEqual(list(self.types), ["Kommune"])

    def test_columns(self) -> None:
        kommune = self.types["Kommune"]
        self.assertEqual(kommune["description"], "Kommune i Norge")
        attributes = _attributes(kommune)
        self.assertEqual(
            attributes["kommunenummer"],
            {
                "name": "kommunenummer",
                "type": "string",
                "cardinality": "1",
                "description": "Nummer; fire siffer, f.eks. '0301'",
            },
        )
        self.assertEqual(
            attributes["status"]["valueDomain"]["listedValues"],
            [{"value": "gjeldende"}, {"value": "utgått"}],
        )
        self.assertEqual(kommune["geometry"]["type"], "geometry-polygon")
        self.assertEqual(kommune["geometry"]["storageCrs"], "http://www.opengis.net/def/crs/EPSG/0/25833")

    def test_copy_rows_fill_the_code_list(self) -> None:
        navn = _attributes(self.types["Kommune"])["navn"]
        self.assertEqual(navn["cardinality"], "0..*")
        spraak = {a["name"]: a for a in navn["attributes"]}["spraak"]
        self.assertEqual(
            spraak["valueDomain"]["listedValues"],
            [{"value": "nob", "label": "Norsk bokmål"}, {"value": "sme"}],
        )

    def test_unknown_schema_is_an_error(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "schema 'mangler'"):
            feature_types_from_sql(_PG_DUMP, schema="mangler")


class GistoolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.types = _by_name(feature_types_from_sql(_GISTOOLS))

    def test_names_and_code_list(self) -> None:
        self.assertEqual(list(self.types), ["Planområde", "Planpunkt"])
        attributes = _attributes(self.types["Planområde"])
        self.assertEqual(set(attributes), {"lokalid", "arealformaal"})
        self.assertEqual(
            attributes["arealformaal"]["valueDomain"]["listedValues"],
            [{"value": "1001", "label": "Boligbebyggelse"}, {"value": "1002", "label": "Fritidsbebyggelse"}],
        )

    def test_untyped_geometry_takes_its_srid_from_the_check(self) -> None:
        geometry = self.types["Planområde"]["geometry"]
        self.assertEqual(geometry["type"], "geometry")
        self.assertEqual(geometry["storageCrs"], "http://www.opengis.net/def/crs/EPSG/0/25832")
        self.assertEqual(geometry["name"], "omraade")

    def test_add_geometry_column(self) -> None:
        geometry = self.types["Planpunkt"]["geometry"]
        self.assertEqual(geometry["type"], "geometry-point")
        self.assertEqual(geometry["name"], "posisjon")


class SqlParserTests(unittest.TestCase):
    def test_semicolons_inside_strings_comments_and_bodies(self) -> None:
        schema = parse_sql(
            r'''
            -- CREATE TABLE fake (a int);
            /* CREATE TABLE fake2 (a int); /* nested; */ still comment; */
            CREATE FUNCTION f() RETURNS void AS $body$ SELECT 1; SELECT 2; $body$ LANGUAGE sql;
            CREATE TABLE "Sær ; tabell" (
              "Kolonne ""x""" text DEFAULT 'a;b',
              e text DEFAULT E'linje\nto\'s'
            );
            '''
        )
        self.assertEqual(list(schema.tables), [(None, "Sær ; tabell")])
        table = schema.tables[(None, "Sær ; tabell")]
        self.assertEqual(list(table.columns), ['Kolonne "x"', "e"])
        self.assertEqual(table.columns["e"].default[0].value, "linje\nto's")

    def test_unquoted_names_are_folded_to_lowercase(self) -> None:
        schema = parse_sql("CREATE TABLE Adm.Kommune (Navn TEXT);")
        self.assertIn(("adm", "kommune"), schema.tables)
        self.assertIn("navn", schema.tables[("adm", "kommune")].columns)

    def test_column_types(self) -> None:
        columns = parse_sql(
            "CREATE TABLE t (a double precision, b timestamp with time zone NOT NULL, "
            "c character varying(20)[], d public.geometry(PointZ, 5972));"
        ).tables[(None, "t")].columns
        self.assertEqual(columns["a"].type_name, "double precision")
        self.assertEqual(columns["b"].type_name, "timestamp with time zone")
        self.assertTrue(columns["b"].not_null)
        self.assertEqual((columns["c"].type_name, columns["c"].array), ("character varying", True))
        self.assertEqual((columns["d"].type_name, columns["d"].type_args), ("geometry", ["pointz", "5972"]))

    def test_search_path_qualifies_unqualified_names(self) -> None:
        schema = parse_sql("SET search_path TO adm, public; CREATE TABLE t (a int);")
        self.assertIn(("adm", "t"), schema.tables)

    def test_unbalanced_statement_is_reported(self) -> None:
        with self.assertRaisesRegex(SqlSyntaxError, "(?i)unbalanced.*create table t"):
            parse_sql("CREATE TABLE t (a int;")

    def test_unterminated_string_is_reported(self) -> None:
        with self.assertRaisesRegex(SqlSyntaxError, "line 2"):
            parse_sql("CREATE TABLE t (a int);\nCOMMENT ON TABLE t IS 'never closed")


class LoaderTests(unittest.TestCase):
    def test_url_source(self) -> None:
        class Response:
            status_code = 200
            encoding = "utf-8"
            text = _GISTOOLS

        requested: list[str] = []
        types = load_feature_types_from_postgis(
            "https://example.org/planer.sql", http_get=lambda url: requested.append(url) or Response()
        )
        self.assertEqual(requested, ["https://example.org/planer.sql"])
        self.assertEqual(len(types), 2)

    def test_missing_file(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_feature_types_from_postgis("finnes/ikke.sql")

    def test_script_without_tables(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "no CREATE TABLE"):
            feature_types_from_sql("GRANT USAGE ON SCHEMA x TO y;")


if __name__ == "__main__":
    unittest.main()
