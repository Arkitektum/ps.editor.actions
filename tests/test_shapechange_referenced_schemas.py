"""Tests for schemas a model references but does not define.

A SOSI model such as Reguleringsplan borrows types from Planregister and the
general SOSI packages. Without declaring those schemas, ShapeChange has no way to
know the types belong elsewhere, so it copies them into this schema's namespace
instead of importing them.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_shapechange import _parse_referenced_schemas  # noqa: E402
from shapechange.config import CONFIG_NS, build_config  # noqa: E402
from shapechange.scxml import SCXML_NS, build_scxml  # noqa: E402


def _q(tag: str) -> str:
    return f"{{{SCXML_NS}}}{tag}"


def _c(tag: str) -> str:
    return f"{{{CONFIG_NS}}}{tag}"


FEATURE_TYPES = [
    {
        "name": "Planomraade",
        "attributes": [
            {
                "name": "identifikasjon",
                "type": "Identifikasjon",
                "cardinality": "1",
                # The referencing model inlines the borrowed datatype, which is
                # exactly what would otherwise be copied into our namespace.
                "attributes": [
                    {"name": "lokalId", "type": "string", "cardinality": "1"}
                ],
            },
            {
                "name": "eget",
                "type": "EgenDatatype",
                "cardinality": "1",
                "attributes": [
                    {"name": "verdi", "type": "string", "cardinality": "1"}
                ],
            },
        ],
    }
]

PLANREGISTER = {
    "name": "Planregister",
    "targetNamespace": "https://example.no/Planregister/2026",
    "xmlnsPrefix": "pr",
    "version": "2026",
    "xsdDocument": "Planregister.xsd",
    "location": "https://example.no/Planregister/2026/Planregister.xsd",
    "types": {"Identifikasjon", "Arealplan"},
}


def _packages(root: ET.Element) -> dict[str, ET.Element]:
    return {
        package.findtext(_q("name")): package
        for package in root.iter(_q("Package"))
    }


def _tagged_values(element: ET.Element) -> dict[str, str]:
    container = element.find(_q("taggedValues"))
    if container is None:
        return {}
    values = {}
    for tagged_value in container.findall(_q("TaggedValue")):
        first = tagged_value.find(f"{_q('values')}/{_q('Value')}")
        values[tagged_value.findtext(_q("name"))] = first.text if first is not None else ""
    return values


def _classes_in(package: ET.Element) -> list[str]:
    return [
        entry.findtext(_q("name"))
        for entry in package.findall(f"{_q('classes')}/{_q('Class')}")
    ]


class ScxmlPlacementTests(unittest.TestCase):
    def _build(self, referenced=(PLANREGISTER,)) -> ET.Element:
        return build_scxml(
            FEATURE_TYPES,
            schema_name="Reguleringsplan",
            target_namespace="https://example.no/Reguleringsplan/2026",
            xmlns_prefix="app",
            referenced_schemas=referenced,
        ).getroot()

    def test_borrowed_type_lands_in_the_owning_schema(self) -> None:
        packages = _packages(self._build())
        self.assertIn("Identifikasjon", _classes_in(packages["Planregister"]))
        self.assertNotIn("Identifikasjon", _classes_in(packages["Reguleringsplan"]))

    def test_own_types_stay_put(self) -> None:
        packages = _packages(self._build())
        own = _classes_in(packages["Reguleringsplan"])
        self.assertIn("Planomraade", own)
        self.assertIn("EgenDatatype", own)

    def test_referenced_package_carries_its_own_namespace(self) -> None:
        # This is what makes ShapeChange emit an import rather than a local copy.
        tags = _tagged_values(_packages(self._build())["Planregister"])
        self.assertEqual(tags["targetNamespace"], "https://example.no/Planregister/2026")
        self.assertEqual(tags["xmlns"], "pr")
        self.assertEqual(tags["xsdDocument"], "Planregister.xsd")

    def test_unused_referenced_schema_is_not_declared(self) -> None:
        # Declaring it would add an import nothing refers to.
        unused = dict(PLANREGISTER, name="Ubrukt", types={"FinnesIkkeHer"})
        self.assertNotIn("Ubrukt", _packages(self._build(referenced=(unused,))))

    def test_without_declaration_the_type_stays_local(self) -> None:
        packages = _packages(self._build(referenced=()))
        self.assertIn("Identifikasjon", _classes_in(packages["Reguleringsplan"]))


class ConfigNamespaceTests(unittest.TestCase):
    def _target(self, referenced=(PLANREGISTER,)) -> ET.Element:
        root = build_config(
            model_path=Path("/out/model.scxml"),
            log_path=Path("/out/log.xml"),
            xsd_directory=Path("/out/xsd"),
            json_directory=Path("/out/json"),
            app_schema_name="Reguleringsplan",
            targets=["xsd"],
            referenced_schemas=referenced,
        ).getroot()
        target = root.find(f"{_c('targets')}/{_c('TargetXmlSchema')}")
        assert target is not None
        return target

    def test_namespace_and_location_are_declared(self) -> None:
        # ShapeChange derives the import itself, but not where the schema lives.
        entry = self._target().find(f"{_c('xmlNamespaces')}/{_c('XmlNamespace')}")
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.get("nsabr"), "pr")
        self.assertEqual(entry.get("ns"), "https://example.no/Planregister/2026")
        self.assertEqual(
            entry.get("location"), "https://example.no/Planregister/2026/Planregister.xsd"
        )

    def test_nothing_is_emitted_without_referenced_schemas(self) -> None:
        self.assertIsNone(self._target(referenced=()).find(_c("xmlNamespaces")))

    def test_only_the_main_schema_is_selected(self) -> None:
        # The referenced schemas must not be generated, only imported.
        root = build_config(
            model_path=Path("/out/model.scxml"),
            log_path=Path("/out/log.xml"),
            xsd_directory=Path("/out/xsd"),
            json_directory=Path("/out/json"),
            app_schema_name="Reguleringsplan",
            targets=["xsd"],
            referenced_schemas=(PLANREGISTER,),
        ).getroot()
        input_element = root.find(_c("input"))
        assert input_element is not None
        regex = {
            entry.get("name"): entry.get("value")
            for entry in input_element.findall(_c("parameter"))
        }["appSchemaNameRegex"]
        self.assertEqual(regex, "^Reguleringsplan$")


class ParsingTests(unittest.TestCase):
    def test_defaults_are_derived_from_the_name(self) -> None:
        [schema] = _parse_referenced_schemas(
            '[{"name": "Planregister", "target-namespace": "https://example.no/pr",'
            ' "xmi-model": "pr.xml"}]'
        )
        self.assertEqual(schema["xsdDocument"], "Planregister.xsd")
        self.assertEqual(schema["location"], "Planregister.xsd")
        self.assertEqual(schema["xmlnsPrefix"], "planre")
        self.assertEqual(schema["version"], "1.0")

    def test_explicit_values_win(self) -> None:
        [schema] = _parse_referenced_schemas(
            '[{"name": "Planregister", "target-namespace": "https://example.no/pr",'
            ' "xmi-model": "pr.xml", "xmlns-prefix": "pr", "version": "2026",'
            ' "location": "https://example.no/pr/Planregister.xsd"}]'
        )
        self.assertEqual(schema["xmlnsPrefix"], "pr")
        self.assertEqual(schema["version"], "2026")
        self.assertEqual(schema["location"], "https://example.no/pr/Planregister.xsd")

    def test_yaml_is_accepted(self) -> None:
        schemas = _parse_referenced_schemas(
            "- name: Planregister\n"
            "  target-namespace: https://example.no/pr\n"
            "  xmi-model: pr.xml\n"
        )
        self.assertEqual(len(schemas), 1)

    def test_missing_namespace_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _parse_referenced_schemas('[{"name": "Planregister"}]')

    def test_empty_input_is_accepted(self) -> None:
        self.assertEqual(_parse_referenced_schemas(None), [])
        self.assertEqual(_parse_referenced_schemas("  "), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
