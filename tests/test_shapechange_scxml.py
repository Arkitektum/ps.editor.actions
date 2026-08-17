"""Tests for the feature catalogue to ShapeChange SCXML conversion."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shapechange.scxml import SCXML_NS, build_scxml, write_scxml  # noqa: E402

FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "shapechange_feature_types.json"


def _q(tag: str) -> str:
    return f"{{{SCXML_NS}}}{tag}"


def _load_fixture() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _build(feature_types: list[dict] | None = None) -> ET.Element:
    tree = build_scxml(
        feature_types if feature_types is not None else _load_fixture(),
        schema_name="Test Schema",
        target_namespace="http://example.com/test/1.0",
        xmlns_prefix="t",
    )
    return tree.getroot()


def _classes(root: ET.Element) -> dict[str, ET.Element]:
    classes: dict[str, ET.Element] = {}
    for element in root.iter(_q("Class")):
        name = element.findtext(_q("name")) or ""
        classes[name] = element
    return classes


def _stereotypes(element: ET.Element) -> list[str]:
    container = element.find(_q("stereotypes"))
    if container is None:
        return []
    return [entry.text or "" for entry in container.findall(_q("Stereotype"))]


def _tagged_values(element: ET.Element) -> dict[str, str]:
    container = element.find(_q("taggedValues"))
    if container is None:
        return {}
    values: dict[str, str] = {}
    for tagged_value in container.findall(_q("TaggedValue")):
        name = tagged_value.findtext(_q("name")) or ""
        value_container = tagged_value.find(_q("values"))
        text = ""
        if value_container is not None:
            first = value_container.find(_q("Value"))
            text = (first.text or "") if first is not None else ""
        values[name] = text
    return values


def _properties(element: ET.Element) -> dict[str, ET.Element]:
    container = element.find(_q("properties"))
    if container is None:
        return {}
    result: dict[str, ET.Element] = {}
    for prop in container.findall(_q("Property")):
        result[prop.findtext(_q("name")) or ""] = prop
    return result


class ScxmlStructureTests(unittest.TestCase):
    def test_model_root_carries_required_attributes(self) -> None:
        root = _build()
        self.assertEqual(root.tag, _q("Model"))
        # 'encoding' is required by ShapeChangeExportedModel.xsd.
        self.assertEqual(root.get("encoding"), "UTF-8")
        self.assertTrue(root.get("scxmlProducer"))

    def test_application_schema_package_carries_tagged_values(self) -> None:
        root = _build()
        package = root.find(f"{_q('packages')}/{_q('Package')}")
        self.assertIsNotNone(package)
        assert package is not None

        self.assertEqual(package.findtext(_q("name")), "Test Schema")
        self.assertIn("application schema", _stereotypes(package))

        tags = _tagged_values(package)
        # ShapeChange decides what is a schema from targetNamespace, not from
        # the stereotype, so this tagged value is what makes the run work.
        self.assertEqual(tags["targetNamespace"], "http://example.com/test/1.0")
        self.assertEqual(tags["xmlns"], "t")
        self.assertEqual(tags["version"], "1.0")
        self.assertEqual(tags["xsdDocument"], "TestSchema.xsd")
        self.assertEqual(tags["jsonDocument"], "TestSchema.json")

    def test_explicit_document_names_are_used_verbatim(self) -> None:
        # The per-scope workflow passes {scope}.xsd / {scope}.json (hyphens kept)
        # so the deliverables line up with {scope}.gpkg.
        tree = build_scxml(
            _load_fixture(),
            schema_name="Test Schema",
            target_namespace="http://example.com/test/1.0",
            xsd_document="bydeler-bodo-kommune.xsd",
            json_document="bydeler-bodo-kommune.json",
        )
        package = tree.getroot().find(f"{_q('packages')}/{_q('Package')}")
        assert package is not None
        tags = _tagged_values(package)
        self.assertEqual(tags["xsdDocument"], "bydeler-bodo-kommune.xsd")
        self.assertEqual(tags["jsonDocument"], "bydeler-bodo-kommune.json")

    def test_classes_get_expected_stereotypes(self) -> None:
        classes = _classes(_build())

        self.assertEqual(_stereotypes(classes["Bygning"]), ["featuretype"])
        self.assertEqual(_stereotypes(classes["Eiendom"]), ["featuretype"])
        self.assertEqual(_stereotypes(classes["Adresse"]), ["datatype"])
        # An external codeList URI makes it a code list...
        self.assertEqual(_stereotypes(classes["Bygningstype"]), ["codelist"])
        # ...while listed values alone make it an enumeration.
        self.assertEqual(_stereotypes(classes["Bygningsstatus"]), ["enumeration"])

    def test_abstract_feature_type_is_marked_abstract(self) -> None:
        classes = _classes(_build())
        self.assertEqual(classes["BaseFeature"].findtext(_q("isAbstract")), "true")
        self.assertIsNone(classes["Bygning"].find(_q("isAbstract")))

    def test_codelist_uri_becomes_a_tagged_value(self) -> None:
        classes = _classes(_build())
        tags = _tagged_values(classes["Bygningstype"])
        self.assertEqual(
            tags["codeList"],
            "https://register.geonorge.no/sosi-kodelister/bygningstype",
        )
        self.assertEqual(tags["asDictionary"], "true")

    def test_enumeration_values_become_properties_without_a_type(self) -> None:
        classes = _classes(_build())
        properties = _properties(classes["Bygningsstatus"])
        self.assertEqual(sorted(properties), ["oppfoert", "planlagt"])
        # Codes and enums have no value type.
        for prop in properties.values():
            self.assertIsNone(prop.find(_q("typeName")))
            self.assertIsNone(prop.find(_q("typeId")))

    def test_geometry_is_normalised_to_a_gm_type(self) -> None:
        classes = _classes(_build())

        # GeoPackage reports "geometry-polygon"...
        bygning_geometry = _properties(classes["Bygning"])["geometry"]
        self.assertEqual(bygning_geometry.findtext(_q("typeName")), "GM_Surface")

        # ...and OGC API already reports a GM_ type.
        eiendom_geometry = _properties(classes["Eiendom"])["geometry"]
        self.assertEqual(eiendom_geometry.findtext(_q("typeName")), "GM_Surface")

    def test_geometry_falls_back_to_gm_object_when_unrecognised(self) -> None:
        root = _build(
            [
                {
                    "name": "Ukjent",
                    "geometry": {"itemType": "feature", "type": "Unknown"},
                    "attributes": [],
                }
            ]
        )
        geometry = _properties(_classes(root)["Ukjent"])["geometry"]
        self.assertEqual(geometry.findtext(_q("typeName")), "GM_Object")

    def test_scalar_types_are_normalised_and_have_no_type_id(self) -> None:
        classes = _classes(_build())
        name = _properties(classes["Bygning"])["bygningsnavn"]
        self.assertEqual(name.findtext(_q("typeName")), "CharacterString")
        # typeId must reference a Class/id, so basic ISO types get typeName only.
        self.assertIsNone(name.find(_q("typeId")))

    def test_inheritance_becomes_a_supertype_reference(self) -> None:
        classes = _classes(_build())
        base_id = classes["BaseFeature"].findtext(_q("id"))
        supertypes = classes["Bygning"].find(_q("supertypes"))
        self.assertIsNotNone(supertypes)
        assert supertypes is not None
        self.assertEqual(
            [entry.text for entry in supertypes.findall(_q("SupertypeId"))],
            [base_id],
        )

    def test_association_creates_a_navigable_role_and_an_association(self) -> None:
        root = _build()
        classes = _classes(root)

        role = _properties(classes["Bygning"])["liggerPaa"]
        self.assertEqual(role.findtext(_q("isAttribute")), "false")
        self.assertEqual(role.findtext(_q("typeName")), "Eiendom")
        self.assertEqual(role.findtext(_q("cardinality")), "1..*")
        association_id = role.findtext(_q("associationId"))
        self.assertTrue(association_id)

        associations = root.find(_q("associations"))
        self.assertIsNotNone(associations)
        assert associations is not None
        entries = associations.findall(_q("Association"))
        self.assertEqual(len(entries), 1)

        association = entries[0]
        self.assertEqual(association.findtext(_q("id")), association_id)
        end1 = association.find(_q("end1"))
        assert end1 is not None
        self.assertEqual(end1.get("ref"), role.findtext(_q("id")))

        # The opposite end is encoded inline, must not be navigable and must
        # carry inClassId -- all three are schema assertions.
        end2_property = association.find(f"{_q('end2')}/{_q('Property')}")
        assert end2_property is not None
        self.assertEqual(end2_property.findtext(_q("isNavigable")), "false")
        self.assertEqual(
            end2_property.findtext(_q("inClassId")),
            classes["Eiendom"].findtext(_q("id")),
        )

    def test_association_to_an_unknown_target_is_dropped(self) -> None:
        root = _build()
        # 'FinnesIkke' is not a generated class, so keeping the role would
        # produce a dangling typeId reference.
        self.assertNotIn("ugyldig", _properties(_classes(root)["Bygning"]))
        self.assertNotIn("FinnesIkke", _classes(root))

    def test_class_name_starting_with_a_digit_is_skipped(self) -> None:
        # A code value or numeric type can leak in as a data type / code list
        # name ("0"). ShapeChange aborts the whole run on such a name, so the
        # class is dropped instead; the feature type still survives.
        root = _build(
            [
                {
                    "name": "DyrkbarJord",
                    "attributes": [
                        {
                            "name": "verdi",
                            "type": "0",
                            "cardinality": "1",
                            "valueDomain": {
                                "listedValues": [
                                    {"value": "0", "label": "Nei"},
                                    {"value": "1", "label": "Ja"},
                                ]
                            },
                        }
                    ],
                }
            ]
        )
        classes = _classes(root)
        self.assertIn("DyrkbarJord", classes)
        self.assertNotIn("0", classes)

    def test_codelist_stereotype_follows_the_source_model(self) -> None:
        # The XMI loader records the stereotype as 'kind'. Without it the writer
        # would have to guess from the presence of a codeList URI, which makes a
        # register-less SOSI code list look like an enumeration.
        root = _build(
            [
                {
                    "name": "X",
                    "attributes": [
                        {
                            "name": "a",
                            "type": "LukketKodeliste",
                            "cardinality": "1",
                            "valueDomain": {
                                "kind": "codelist",
                                "listedValues": [{"value": "1", "label": "En"}],
                            },
                        },
                        {
                            "name": "b",
                            "type": "EkteEnum",
                            "cardinality": "1",
                            "valueDomain": {
                                "kind": "enumeration",
                                "listedValues": [{"value": "2", "label": "To"}],
                            },
                        },
                    ],
                }
            ]
        )
        classes = _classes(root)
        self.assertEqual(_stereotypes(classes["LukketKodeliste"]), ["codelist"])
        self.assertEqual(_stereotypes(classes["EkteEnum"]), ["enumeration"])

    def test_as_dictionary_can_be_forced_off(self) -> None:
        # 'false' makes ShapeChange emit the union-with-'other:' form that the
        # published Geonorge schemas use.
        feature_types = [
            {
                "name": "X",
                "attributes": [
                    {
                        "name": "a",
                        "type": "Kodeliste",
                        "cardinality": "1",
                        "valueDomain": {
                            "kind": "codelist",
                            "asDictionary": "true",
                            "codeList": "https://register.example.no/kodeliste",
                        },
                    }
                ],
            }
        ]

        def _tags(as_dictionary: str) -> dict[str, str]:
            tree = build_scxml(
                feature_types,
                schema_name="S",
                target_namespace="http://example.com/1.0",
                as_dictionary=as_dictionary,
            )
            return _tagged_values(_classes(tree.getroot())["Kodeliste"])

        self.assertEqual(_tags("model")["asDictionary"], "true")
        self.assertEqual(_tags("false")["asDictionary"], "false")
        self.assertEqual(_tags("true")["asDictionary"], "true")
        # The registry URI is kept either way; rule-xsd-prop-targetCodeListURI
        # carries it into the XSD regardless of the encoding.
        self.assertEqual(
            _tags("false")["codeList"], "https://register.example.no/kodeliste"
        )

    def test_source_tagged_values_are_written(self) -> None:
        root = _build(
            [
                {
                    "name": "X",
                    "taggedValues": {"SOSI_navn": "XOBJ"},
                    "attributes": [
                        {
                            "name": "a",
                            "type": "string",
                            "cardinality": "1",
                            "taggedValues": {"SOSI_navn": "AAA"},
                        }
                    ],
                }
            ]
        )
        classes = _classes(root)
        self.assertEqual(_tagged_values(classes["X"])["SOSI_navn"], "XOBJ")

        prop = _properties(classes["X"])["a"]
        container = prop.find(_q("taggedValues"))
        self.assertIsNotNone(container)
        assert container is not None
        tagged_value = container.find(_q("TaggedValue"))
        assert tagged_value is not None
        self.assertEqual(tagged_value.findtext(_q("name")), "SOSI_navn")

    def test_target_namespace_is_required(self) -> None:
        with self.assertRaises(ValueError):
            build_scxml([], schema_name="X", target_namespace="")

    def test_fixture_without_packages_puts_everything_in_the_schema_package(self) -> None:
        root = _build()
        packages = list(root.iter(_q("Package")))
        self.assertEqual([p.findtext(_q("name")) for p in packages], ["Test Schema"])

    def test_package_names_become_sub_packages(self) -> None:
        root = _build(
            [
                {
                    "name": "A",
                    "package": "Pakke1",
                    "attributes": [
                        {
                            "name": "y",
                            "type": "Dt",
                            "cardinality": "1",
                            "attributes": [
                                {"name": "z", "type": "integer", "cardinality": "1"}
                            ],
                        }
                    ],
                },
                {"name": "B", "package": "Pakke2", "attributes": []},
            ]
        )

        contents = {
            package.findtext(_q("name")): [
                entry.findtext(_q("name"))
                for entry in package.findall(f"{_q('classes')}/{_q('Class')}")
            ]
            for package in root.iter(_q("Package"))
        }

        # Sub-packages inherit targetNamespace from the schema package, so only
        # the schema package is selected by ShapeChange. Datatypes stay in it
        # because they may be referenced from any sub-package.
        self.assertEqual(contents["Test Schema"], ["Dt"])
        self.assertEqual(contents["Pakke1"], ["A"])
        self.assertEqual(contents["Pakke2"], ["B"])

        sub_packages = [
            package
            for package in root.iter(_q("Package"))
            if package.findtext(_q("name")) != "Test Schema"
        ]
        for package in sub_packages:
            self.assertEqual(_tagged_values(package), {})


class ScxmlInvariantTests(unittest.TestCase):
    """The referential rules that ShapeChangeExportedModel.xsd enforces."""

    def test_all_ids_are_unique(self) -> None:
        root = _build()
        ids = [element.text for element in root.iter(_q("id"))]
        self.assertEqual(len(ids), len(set(ids)), "sc:id values must be unique")

    def test_every_type_id_references_a_class(self) -> None:
        root = _build()
        class_ids = {
            element.findtext(_q("id")) for element in root.iter(_q("Class"))
        }
        for prop in root.iter(_q("Property")):
            type_id = prop.findtext(_q("typeId"))
            if type_id is not None:
                self.assertIn(type_id, class_ids)

    def test_every_association_id_references_an_association(self) -> None:
        root = _build()
        associations = root.find(_q("associations"))
        association_ids = set()
        if associations is not None:
            association_ids = {
                entry.findtext(_q("id")) for entry in associations.findall(_q("Association"))
            }
        for prop in root.iter(_q("Property")):
            association_id = prop.findtext(_q("associationId"))
            if association_id is not None:
                self.assertIn(association_id, association_ids)

    def test_sequence_numbers_are_unique_per_class(self) -> None:
        root = _build()
        for element in root.iter(_q("Class")):
            container = element.find(_q("properties"))
            if container is None:
                continue
            numbers = [
                prop.findtext(_q("sequenceNumber"))
                for prop in container.findall(_q("Property"))
            ]
            self.assertTrue(all(numbers))
            self.assertEqual(len(numbers), len(set(numbers)))

    def test_cardinalities_match_the_schema_pattern(self) -> None:
        import re

        pattern = re.compile(r"^([1-9]\d*|\*|((0|[1-9]\d*)\.\.([1-9]\d*|\*)))$")
        root = _build()
        for prop in root.iter(_q("Property")):
            cardinality = prop.findtext(_q("cardinality"))
            if cardinality is not None:
                self.assertRegex(cardinality, pattern)

    def test_zero_cardinality_is_normalised_to_optional(self) -> None:
        # A bare "0" is not expressible in the SCXML multiplicity pattern.
        root = _build(
            [{"name": "X", "attributes": [{"name": "a", "type": "string", "cardinality": "0"}]}]
        )
        prop = _properties(_classes(root)["X"])["a"]
        self.assertEqual(prop.findtext(_q("cardinality")), "0..1")


class ScxmlWriteTests(unittest.TestCase):
    def test_write_scxml_creates_a_parseable_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "model.scxml"
            result = write_scxml(
                _load_fixture(),
                output,
                schema_name="Test Schema",
                target_namespace="http://example.com/test/1.0",
            )
            self.assertEqual(result, output)
            self.assertTrue(output.exists())
            parsed = ET.parse(output)
            self.assertEqual(parsed.getroot().tag, _q("Model"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
