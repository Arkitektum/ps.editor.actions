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


RESTRICTION = {
    "resourceConstraints": {
        "useLimitations": "Ingen begrensninger på bruk er oppgitt. Se forøvrig lisens."
    },
    "legalConstraints": {
        "accessConstraints": "Åpne data",
        "useConstraints": "Lisens",
        "license": "Creative Commons BY 4.0 (CC BY 4.0)",
        "licenseUrl": "https://creativecommons.org/licenses/by/4.0/",
    },
    "securityConstraints": {"classification": "Ugradert"},
}

FEATURE_TYPES = [
    {"name": "Kommune", "attributes": [{"name": "navn", "type": "string", "cardinality": "1"}]}
]

# A psdata document shaped like the one geonorge.psdata builds, trimmed to the
# parts the contract reads.
PSDATA = {
    "identificationSection": {
        "title": "Administrative enheter kommuner",
        "purpose": {"summary": "Framstille den offisielle kommuneinndelingen."},
        "topicCategory": ["Administrative grenser"],
        "keyword": ["Kommune", "Fylke", "Administrative grenser"],
        "spatialRepresentationType": "Vektor",
        "spatialResolution": {"equivalentScale": "5000"},
        "uniqueId": "https://data.geonorge.no/sosi/administrativeenheter/kommuner",
        "language": "nor",
        "restriction": RESTRICTION,
        "contact": [
            {
                "individualName": "Kartverket",
                "organizationName": "Kartverket",
                "electronicMailAddress": "post@kartverket.no",
                "role": "owner",
            },
            {
                "individualName": "Navn Navnesen",
                "organizationName": "Kartverket",
                "electronicMailAddress": "navn@kartverket.no",
                "role": "pointOfContact",
            },
        ],
    },
    "maintenanceSection": {
        "maintenanceAndUpdateFrequency": "Årlig",
        "maintenanceAndUpdateStatement": "Kontinuerlig oppdatert",
    },
    "portrayal": {
        "name": "Tegneregler",
        "linkage": "https://register.geonorge.no/tegneregler/kommuner",
    },
    "additionalReferences": [
        {"title": "Produktark", "href": "https://register.geonorge.no/produktark/kommuner"}
    ],
    "deliverySection": [
        {
            "delivery": {
                "deliveryMedium": {
                    "deliveryMediumName": "Geonorge nedlastning",
                    "deliveryService": {
                        "serviceEndpoint": "https://nedlasting.geonorge.no/api/capabilities/041f1e6e"
                    },
                },
                "deliveryFormat": [{"formatName": "GML"}, {"formatName": "SOSI"}],
            }
        },
        {
            "delivery": {
                "deliveryMedium": {
                    "deliveryMediumName": "WMS",
                    "deliveryService": {
                        "serviceEndpoint": "https://wms.geonorge.no/skwms1/wms.adm_enheter"
                    },
                }
            }
        },
        {"delivery": {"deliveryMedium": {"deliveryMediumName": "Uten endepunkt"}}},
    ],
    "metadataSection": {
        "metadataIdentifier": {
            "metadataLinkage": "https://kartkatalog.geonorge.no/metadata/041f1e6e"
        }
    },
}


class ContractTermsTests(unittest.TestCase):
    """Licence and constraints are fetched for the product specification already,
    so carrying them into the contract costs no extra lookup."""

    def _doc(self, constraints=RESTRICTION) -> dict:
        psdata = {"identificationSection": {"restriction": constraints}}
        return build_odcs(FEATURE_TYPES, identifier="Adm enheter", psdata=psdata)

    def test_usage_and_limitations_are_described(self) -> None:
        description = self._doc()["description"]
        self.assertEqual(
            description["usage"],
            "Ingen begrensninger på bruk er oppgitt. Se forøvrig lisens.",
        )
        self.assertIn("Åpne data", description["limitations"])
        self.assertIn("Ugradert", description["limitations"])

    def test_access_and_classification_become_tags(self) -> None:
        self.assertEqual(self._doc()["tags"], ["Åpne data", "Ugradert"])

    def test_raw_values_are_kept_in_custom_properties(self) -> None:
        # The prose summaries are lossy; a machine needs the exact licence.
        custom = {entry["property"]: entry["value"] for entry in self._doc()["customProperties"]}
        self.assertEqual(custom["license"], "Creative Commons BY 4.0 (CC BY 4.0)")
        self.assertEqual(custom["licenseUrl"], "https://creativecommons.org/licenses/by/4.0/")

    def test_open_data_is_priced_at_zero(self) -> None:
        self.assertEqual(self._doc()["price"], {"priceAmount": 0, "priceUnit": "dataset"})

    def test_price_is_left_out_when_access_is_unclear(self) -> None:
        # Stating a price is an inference; it is only made when the access
        # constraint says plainly that the data is open.
        restricted = {"legalConstraints": {"accessConstraints": "Begrenset"}}
        self.assertNotIn("price", self._doc(restricted))

    def test_nothing_is_added_without_constraints(self) -> None:
        doc = self._doc(None)
        for key in ("tags", "price", "customProperties", "description"):
            self.assertNotIn(key, doc)

    def test_the_model_uri_is_an_authoritative_definition(self) -> None:
        doc = build_odcs(
            FEATURE_TYPES,
            identifier="Adm enheter",
            psdata={"identificationSection": {"restriction": RESTRICTION}},
            model_uri="https://example.no/modell",
        )
        self.assertIn("usage", doc["description"])
        self.assertEqual(
            doc["authoritativeDefinitions"][0],
            {"url": "https://example.no/modell", "type": "semanticModel", "description": "Datamodell"},
        )


class PsdataMappingTests(unittest.TestCase):
    """The psdata document already holds what the contract needs to describe the
    product; every field below is carried over, never invented."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.doc = build_odcs(FEATURE_TYPES, identifier="Adm enheter", psdata=PSDATA)

    def test_purpose_comes_from_the_identification_section(self) -> None:
        self.assertEqual(
            self.doc["description"]["purpose"],
            "Framstille den offisielle kommuneinndelingen.",
        )

    def test_keywords_and_topic_categories_become_tags(self) -> None:
        tags = self.doc["tags"]
        # Topic categories lead; the constraint tags still come along.
        self.assertEqual(tags[0], "Administrative grenser")
        self.assertIn("Kommune", tags)
        self.assertIn("Åpne data", tags)
        # "Administrative grenser" is both a topic category and a keyword.
        self.assertEqual(len(tags), len(set(tags)))

    def test_deliveries_with_an_endpoint_become_servers(self) -> None:
        servers = self.doc["servers"]
        self.assertEqual([s["server"] for s in servers], ["geonorge-nedlastning", "wms"])
        self.assertEqual(
            servers[0]["location"],
            "https://nedlasting.geonorge.no/api/capabilities/041f1e6e",
        )
        self.assertEqual(servers[0]["type"], "api")
        self.assertEqual(servers[0]["description"], "Geonorge nedlastning - GML, SOSI")

    def test_a_delivery_without_an_endpoint_is_not_a_server(self) -> None:
        self.assertNotIn("uten-endepunkt", [s["server"] for s in self.doc["servers"]])

    def test_repeated_channel_names_are_told_apart_by_format(self) -> None:
        # Geonorge publishes one Atom feed per format, all under the same name.
        psdata = {
            "deliverySection": [
                {
                    "delivery": {
                        "deliveryMedium": {
                            "deliveryMediumName": "Atom Feed",
                            "deliveryService": {"serviceEndpoint": f"https://example.no/{fmt}.xml"},
                        },
                        "deliveryFormat": [{"formatName": fmt}],
                    }
                }
                for fmt in ("GML", "SOSI")
            ]
        }
        servers = build_odcs(FEATURE_TYPES, identifier="T", psdata=psdata)["servers"]
        self.assertEqual([s["server"] for s in servers], ["atom-feed", "atom-feed-sosi"])

    def test_the_links_become_authoritative_definitions(self) -> None:
        by_url = {d["url"]: d for d in self.doc["authoritativeDefinitions"]}
        self.assertIn("https://data.geonorge.no/sosi/administrativeenheter/kommuner", by_url)
        self.assertIn("https://kartkatalog.geonorge.no/metadata/041f1e6e", by_url)
        self.assertEqual(
            by_url["https://register.geonorge.no/produktark/kommuner"]["description"],
            "Produktark",
        )
        self.assertEqual(
            by_url["https://register.geonorge.no/tegneregler/kommuner"]["type"],
            "implementation",
        )

    def test_a_unique_id_that_is_not_a_url_is_left_out(self) -> None:
        # uniqueId falls back to a bare UUID when the metadata has no namespace.
        psdata = {"identificationSection": {"uniqueId": "041f1e6e-bdbc-4091"}}
        self.assertNotIn("authoritativeDefinitions", build_odcs(FEATURE_TYPES, identifier="T", psdata=psdata))

    def test_contacts_become_the_team(self) -> None:
        members = self.doc["team"]["members"]
        self.assertEqual(
            [m["username"] for m in members],
            ["post@kartverket.no", "navn@kartverket.no"],
        )
        self.assertEqual(members[0]["role"], "owner")
        self.assertEqual(members[1]["name"], "Navn Navnesen")
        self.assertEqual(members[1]["description"], "Kartverket")

    def test_one_address_under_several_roles_is_one_member(self) -> None:
        # Kartverket is owner, point of contact and publisher for its own data.
        psdata = {
            "identificationSection": {
                "contact": [
                    {"electronicMailAddress": "post@kartverket.no", "individualName": "Liv", "role": "owner"},
                    {"electronicMailAddress": "post@kartverket.no", "individualName": "Liv", "role": "pointOfContact"},
                    {"electronicMailAddress": "POST@kartverket.no", "organizationName": "Kartverket", "role": "publisher"},
                ]
            }
        }
        members = build_odcs(FEATURE_TYPES, identifier="T", psdata=psdata)["team"]["members"]
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0]["role"], "owner, pointOfContact, publisher")
        self.assertEqual(members[0]["name"], "Liv")

    def test_a_contact_without_an_email_cannot_be_a_member(self) -> None:
        # ODCS requires a username and the e-mail is the only identifier we get.
        psdata = {"identificationSection": {"contact": [{"individualName": "Uten e-post"}]}}
        self.assertNotIn("team", build_odcs(FEATURE_TYPES, identifier="T", psdata=psdata))

    def test_the_first_contact_becomes_a_support_channel(self) -> None:
        self.assertEqual(
            self.doc["support"],
            [
                {
                    "channel": "Kartverket",
                    "tool": "email",
                    "url": "mailto:post@kartverket.no",
                    "scope": "issues",
                    "description": "Kontaktpunkt hos Kartverket",
                }
            ],
        )

    def test_the_update_frequency_becomes_an_sla_property(self) -> None:
        self.assertEqual(
            self.doc["slaProperties"],
            [
                {
                    "property": "frequency",
                    "value": "Årlig",
                    "driver": "operational",
                    "description": "Kontinuerlig oppdatert",
                }
            ],
        )

    def test_values_odcs_has_no_field_for_land_in_custom_properties(self) -> None:
        custom = {entry["property"]: entry["value"] for entry in self.doc["customProperties"]}
        self.assertEqual(custom["equivalentScale"], "5000")
        self.assertEqual(custom["spatialRepresentationType"], "Vektor")
        self.assertEqual(custom["language"], "nor")
        self.assertEqual(custom["maintenanceAndUpdateStatement"], "Kontinuerlig oppdatert")
        # The licence values from the constraints are still there.
        self.assertEqual(custom["license"], "Creative Commons BY 4.0 (CC BY 4.0)")

    def test_explicit_servers_win_over_the_delivery_section(self) -> None:
        doc = build_odcs(
            FEATURE_TYPES,
            identifier="Adm enheter",
            psdata=PSDATA,
            servers=[{"server": "lokal", "type": "local", "path": "/data"}],
        )
        self.assertEqual([s["server"] for s in doc["servers"]], ["lokal"])

    def test_an_empty_psdata_adds_nothing(self) -> None:
        doc = build_odcs(FEATURE_TYPES, identifier="Adm enheter", psdata={})
        self.assertEqual(set(doc), {"apiVersion", "kind", "id", "version", "status", "name", "schema"})


class SchemaValidationTests(unittest.TestCase):
    def test_contract_validates_against_odcs_v3_1_0(self) -> None:
        try:
            import jsonschema  # noqa: F401
        except ImportError:  # pragma: no cover
            self.skipTest("jsonschema is not installed")
        schema_path = Path(__file__).parent / "fixtures" / "odcs-v3.1.0.json"
        if not schema_path.exists():
            self.skipTest("ODCS schema fixture is not vendored")
        import json as _json

        schema = _json.loads(schema_path.read_text(encoding="utf-8"))
        for psdata in (None, {"identificationSection": {"restriction": RESTRICTION}}, PSDATA):
            with self.subTest(psdata="full" if psdata is PSDATA else psdata):
                jsonschema.validate(
                    build_odcs(
                        FEATURE_TYPES,
                        identifier="Adm enheter",
                        psdata=psdata,
                        model_uri="https://example.no/modell",
                    ),
                    schema,
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

