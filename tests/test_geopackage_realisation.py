"""Tests for the multi-geometry realisation rule.

A GeoPackage feature table has one geometry column, so a class modelled with
several geometry properties becomes one feature type per geometry. The realised
model deviates from the UML model on purpose, and is what a product specification
document should describe.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from geopackage.realisation import (  # noqa: E402
    geometry_suffix,
    split_multi_geometry_types,
)


def _names(feature_types) -> list[str]:
    return [entry["name"] for entry in feature_types]


def _by_name(feature_types) -> dict[str, dict]:
    return {entry["name"]: entry for entry in feature_types}


MULTI = [
    {
        "name": "Vrak",
        "description": "Et vrak.",
        "attributes": [
            {"name": "vraktype", "type": "Vraktype", "cardinality": "1"},
            {"name": "område", "type": "GM_Surface", "cardinality": "1"},
            {"name": "posisjon", "type": "GM_Point", "cardinality": "0..1"},
        ],
        "relationships": {"inheritance": ["Fellesegenskaper"], "associations": []},
    }
]


class GeometrySuffixTests(unittest.TestCase):
    def test_iso_and_sosi_names_both_resolve(self) -> None:
        self.assertEqual(geometry_suffix("GM_Surface"), "flate")
        self.assertEqual(geometry_suffix("Flate"), "flate")
        self.assertEqual(geometry_suffix("GM_Curve"), "linje")
        self.assertEqual(geometry_suffix("Kurve"), "linje")
        self.assertEqual(geometry_suffix("GM_Point"), "punkt")
        self.assertEqual(geometry_suffix("Punkt"), "punkt")

    def test_unspecific_geometry_gets_a_generic_suffix(self) -> None:
        # GM_Primitive carries no usable shape name of its own.
        self.assertEqual(geometry_suffix("GM_Primitive"), "geometri")

    def test_non_geometry_types_have_no_suffix(self) -> None:
        self.assertIsNone(geometry_suffix("CharacterString"))
        self.assertIsNone(geometry_suffix(""))
        self.assertIsNone(geometry_suffix(None))


class SplitTests(unittest.TestCase):
    def test_one_type_per_geometry(self) -> None:
        self.assertEqual(_names(split_multi_geometry_types(MULTI)), ["Vrak_flate", "Vrak_punkt"])

    def test_each_part_keeps_only_its_own_geometry(self) -> None:
        parts = _by_name(split_multi_geometry_types(MULTI))
        for name, own, other in (
            ("Vrak_flate", "område", "posisjon"),
            ("Vrak_punkt", "posisjon", "område"),
        ):
            attributes = {a["name"] for a in parts[name]["attributes"]}
            self.assertIn(own, attributes)
            self.assertNotIn(other, attributes)
            # Non-geometry properties belong to every part.
            self.assertIn("vraktype", attributes)

    def test_parts_record_the_class_they_came_from(self) -> None:
        # So a post-process can tell a realised type from a modelled one.
        for part in split_multi_geometry_types(MULTI):
            self.assertEqual(part["realisedFrom"], "Vrak")

    def test_other_keys_are_carried_over(self) -> None:
        part = split_multi_geometry_types(MULTI)[0]
        self.assertEqual(part["description"], "Et vrak.")
        self.assertEqual(part["relationships"]["inheritance"], ["Fellesegenskaper"])

    def test_single_geometry_is_untouched(self) -> None:
        source = [
            {
                "name": "Kai",
                "attributes": [{"name": "område", "type": "GM_Surface", "cardinality": "1"}],
            }
        ]
        result = split_multi_geometry_types(source)
        self.assertEqual(_names(result), ["Kai"])
        self.assertNotIn("realisedFrom", result[0])

    def test_type_without_geometry_is_untouched(self) -> None:
        source = [{"name": "Register", "attributes": [{"name": "navn", "type": "string"}]}]
        self.assertEqual(_names(split_multi_geometry_types(source)), ["Register"])

    def test_geometries_sharing_a_suffix_fall_back_to_the_property_name(self) -> None:
        # A point and a multipoint would otherwise both become "_punkt".
        source = [
            {
                "name": "Maaling",
                "attributes": [
                    {"name": "start", "type": "GM_Point", "cardinality": "1"},
                    {"name": "sverm", "type": "GM_MultiPoint", "cardinality": "1"},
                ],
            }
        ]
        self.assertEqual(
            _names(split_multi_geometry_types(source)), ["Maaling_start", "Maaling_sverm"]
        )

    def test_source_is_not_mutated(self) -> None:
        before = len(MULTI[0]["attributes"])
        split_multi_geometry_types(MULTI)
        self.assertEqual(len(MULTI[0]["attributes"]), before)

    def test_non_mapping_entries_are_skipped(self) -> None:
        self.assertEqual(split_multi_geometry_types(["nope", None]), [])

    def test_sequence_is_required(self) -> None:
        with self.assertRaises(TypeError):
            split_multi_geometry_types("Vrak")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
