"""Tests for a feature type whose supertype is defined in another model file.

SOSI models keep shared classes such as Veglenke and Fellesegenskaper in their
own files. The model that specialises them only holds an ``EAStub`` for the
supertype, so without special handling the inheritance was dropped and the XSD
derived the type from ``gml:AbstractFeatureType`` instead (issue #41).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shapechange.scxml import SCXML_NS, build_scxml  # noqa: E402
from xmi.feature_catalog import (  # noqa: E402
    load_defined_class_names,
    load_feature_types_from_xmi,
)

FIXTURES = Path(__file__).parent / "fixtures"
MODEL = FIXTURES / "supertype_in_other_model.xmi"
SUPERTYPE_MODEL = FIXTURES / "supertype_model.xmi"


def _q(tag: str) -> str:
    return f"{{{SCXML_NS}}}{tag}"


def _referenced_schemas() -> list[dict]:
    return [
        {
            "name": "Felles",
            "targetNamespace": "http://example.com/felles/1.0",
            "xmlnsPrefix": "fel",
            "types": load_defined_class_names(SUPERTYPE_MODEL),
        }
    ]


class ExternalSupertypeCatalogueTests(unittest.TestCase):
    def test_supertype_stub_is_included_as_an_external_reference(self) -> None:
        by_name = {entry["name"]: entry for entry in load_feature_types_from_xmi(MODEL)}
        self.assertEqual(by_name["Trafikkmengde"]["relationships"]["inheritance"], ["Veglenke"])
        self.assertIn("Veglenke", by_name)
        self.assertTrue(by_name["Veglenke"]["external"])


class ExternalSupertypeShapeChangeTests(unittest.TestCase):
    def test_unresolved_supertype_fails_with_the_class_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "Trafikkmengde -> Veglenke"):
            build_scxml(
                load_feature_types_from_xmi(MODEL),
                schema_name="Trafikkmengde",
                target_namespace="http://example.com/trafikkmengde/1.0",
            )

    def test_supertype_from_a_referenced_schema_is_kept(self) -> None:
        root = build_scxml(
            load_feature_types_from_xmi(MODEL),
            schema_name="Trafikkmengde",
            target_namespace="http://example.com/trafikkmengde/1.0",
            referenced_schemas=_referenced_schemas(),
        ).getroot()

        packages = {
            package.findtext(_q("name")): package for package in root.iter(_q("Package"))
        }
        classes = {
            element.findtext(_q("name")): element for element in root.iter(_q("Class"))
        }
        felles_classes = {
            element.findtext(_q("name"))
            for element in packages["Felles"].iter(_q("Class"))
        }
        self.assertIn("Veglenke", felles_classes)
        supertype_ids = [
            entry.text for entry in classes["Trafikkmengde"].iter(_q("SupertypeId"))
        ]
        self.assertEqual(supertype_ids, [classes["Veglenke"].findtext(_q("id"))])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
