"""Tests for the vendored SOSI map entries and the SOSI rule definitions."""

from __future__ import annotations

import sys
import unittest
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shapechange.sosi import (  # noqa: E402
    SOSI_JSON_ENCODING_RULE,
    SOSI_JSON_RULES,
    SOSI_XSD_ENCODING_RULE,
    SOSI_XSD_RULES,
    custom_json_rules,
    custom_xsd_rules,
    sosi_map_entries_path,
)

CONFIG_NS = "http://www.interactive-instruments.de/ShapeChange/Configuration/1.1"


class SosiMapEntriesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = sosi_map_entries_path()
        self.root = ET.parse(self.path).getroot()
        self.entries = self.root.findall(f"{{{CONFIG_NS}}}XsdMapEntry")

    def test_file_is_shipped_and_parses(self) -> None:
        self.assertTrue(self.path.exists(), f"{self.path} is missing")
        self.assertEqual(self.root.tag, f"{{{CONFIG_NS}}}xsdMapEntries")
        self.assertTrue(self.entries)

    def test_no_duplicate_types(self) -> None:
        # The upstream file maps "Høyde" twice, to double and to string, which
        # would silently make a height a string. Only the double mapping is kept.
        counts = Counter(entry.get("type") for entry in self.entries)
        duplicates = {name: count for name, count in counts.items() if count > 1}
        self.assertEqual(duplicates, {})

    def test_hoyde_is_a_double(self) -> None:
        entry = next(e for e in self.entries if e.get("type") == "Høyde")
        self.assertEqual(entry.get("xmlType"), "double")

    def test_norwegian_geometry_types_map_to_gml(self) -> None:
        by_type = {entry.get("type"): entry for entry in self.entries}
        self.assertEqual(by_type["Flate"].get("xmlPropertyType"), "gml:SurfacePropertyType")
        self.assertEqual(by_type["Kurve"].get("xmlPropertyType"), "gml:CurvePropertyType")
        self.assertEqual(by_type["Punkt"].get("xmlPropertyType"), "gml:PointPropertyType")
        self.assertEqual(
            by_type["Sverm"].get("xmlPropertyType"), "gml:MultiPointPropertyType"
        )

    def test_every_entry_applies_to_all_encoding_rules(self) -> None:
        for entry in self.entries:
            self.assertEqual(entry.get("xsdEncodingRules"), "*")


class SosiRuleSelectionTests(unittest.TestCase):
    def test_sosi_and_sosi50_both_resolve(self) -> None:
        self.assertEqual(custom_xsd_rules(SOSI_XSD_ENCODING_RULE), SOSI_XSD_RULES)
        self.assertEqual(custom_xsd_rules("sosi50"), SOSI_XSD_RULES)
        self.assertEqual(custom_xsd_rules("SOSI"), SOSI_XSD_RULES)

    def test_built_in_xsd_rules_resolve_to_none(self) -> None:
        for name in ("iso19136_2007", "gml33", "iso19139_2007", "ogcSweCommon2"):
            self.assertIsNone(custom_xsd_rules(name), name)

    def test_json_rule_selection(self) -> None:
        self.assertEqual(custom_json_rules(SOSI_JSON_ENCODING_RULE), SOSI_JSON_RULES)
        self.assertIsNone(custom_json_rules("defaultGeoJson"))
        self.assertIsNone(custom_json_rules("defaultPlainJson"))

    def test_sosi_json_omits_the_anchor_rule(self) -> None:
        self.assertNotIn("rule-json-cls-name-as-anchor", SOSI_JSON_RULES)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
