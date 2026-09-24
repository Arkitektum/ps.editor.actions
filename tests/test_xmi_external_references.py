"""Tests for classes that a model references but does not define.

Enterprise Architect writes them as ``<EAStub UMLType="Class"/>``. Without them
an association that crosses a file boundary has nothing to connect to, so it
disappears from the diagram entirely -- which is why Arealplan was missing from
the Reguleringsplan model.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from md.feature_types import render_feature_types_to_markdown  # noqa: E402
from puml.feature_types import render_feature_types_to_puml  # noqa: E402
from shapechange.scxml import SCXML_NS, build_scxml  # noqa: E402
from xmi.feature_catalog import load_feature_types_from_xmi  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "external_references.xmi"


def _load() -> list[dict]:
    return load_feature_types_from_xmi(FIXTURE)


def _by_name(feature_types) -> dict[str, dict]:
    return {entry["name"]: entry for entry in feature_types}


class ExternalReferenceSelectionTests(unittest.TestCase):
    def test_association_partner_is_included(self) -> None:
        entry = _by_name(_load())["Arealplan"]
        self.assertTrue(entry["external"])
        self.assertEqual(entry["attributes"], [])

        associations = entry["relationships"]["associations"]
        self.assertEqual(len(associations), 1)
        self.assertEqual(associations[0]["target"], "Planomraade")
        self.assertEqual(associations[0]["role"], "planomraade")
        self.assertEqual(associations[0]["cardinality"], "0..*")

    def test_generalization_subtype_is_excluded(self) -> None:
        # An external model specialising ours is an inbound realization, not an
        # import this model needs.
        self.assertNotIn("LegacySpesialisering", _by_name(_load()))

    def test_attribute_type_only_stub_is_excluded(self) -> None:
        # Already visible as the type text on the attribute; a box would add
        # nothing but noise.
        names = _by_name(_load())
        self.assertNotIn("Identifikasjon", names)
        attributes = {a["name"]: a for a in names["Planomraade"]["attributes"]}
        self.assertEqual(attributes["identifikasjon"]["type"], "Identifikasjon")

    def test_stub_sharing_a_name_with_a_real_class_is_not_duplicated(self) -> None:
        feature_types = _load()
        self.assertEqual([f["name"] for f in feature_types].count("Grense"), 1)
        self.assertNotIn("external", _by_name(feature_types)["Grense"])

    def test_stub_does_not_shadow_a_datatype_of_the_same_name(self) -> None:
        # A stub has no stereotype, so if it were allowed to win the name lookup
        # the datatype would no longer be recognised and its nested attributes
        # would silently vanish from every attribute referencing it.
        attributes = {a["name"]: a for a in _by_name(_load())["Planomraade"]["attributes"]}
        nested = attributes["adresse"].get("attributes")
        self.assertIsNotNone(nested)
        assert nested is not None
        self.assertEqual([entry["name"] for entry in nested], ["gatenavn"])

    def test_real_feature_types_are_unaffected(self) -> None:
        names = _by_name(_load())
        self.assertFalse(names["Planomraade"].get("external"))
        self.assertFalse(names["Grense"].get("external"))


class ExternalReferenceRenderingTests(unittest.TestCase):
    def test_diagram_draws_the_box_and_the_relationship(self) -> None:
        output = render_feature_types_to_puml(_load(), include_notes=False)
        self.assertIn("class Arealplan <<external>>", output)
        self.assertIn("Arealplan --> Planomraade", output)

    def test_object_catalogue_leaves_them_out(self) -> None:
        # They carry no attributes and no description, so a catalogue entry
        # would be an empty section.
        markdown = render_feature_types_to_markdown(_load())
        self.assertNotIn("Arealplan", markdown)
        self.assertIn("Planomraade", markdown)

    def test_shapechange_leaves_them_out(self) -> None:
        # An empty class would become an empty type in the generated XSD.
        root = build_scxml(
            _load(), schema_name="S", target_namespace="http://example.com/1.0"
        ).getroot()
        names = {
            element.findtext(f"{{{SCXML_NS}}}name")
            for element in root.iter(f"{{{SCXML_NS}}}Class")
        }
        self.assertNotIn("Arealplan", names)
        self.assertIn("Planomraade", names)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
