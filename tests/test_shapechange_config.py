"""Tests for the generated ShapeChange configuration document."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shapechange.config import (  # noqa: E402
    CONFIG_NS,
    JSON_SCHEMA_TARGET_CLASS,
    XI_NS,
    XML_SCHEMA_TARGET_CLASS,
    build_config,
    write_config,
)


def _q(tag: str) -> str:
    return f"{{{CONFIG_NS}}}{tag}"


def _build(**overrides: object) -> ET.Element:
    kwargs: dict[str, object] = {
        "model_path": Path("/out/model.scxml"),
        "log_path": Path("/out/log.xml"),
        "xsd_directory": Path("/out/xsd"),
        "json_directory": Path("/out/jsonschema"),
        "app_schema_name": "Test Schema",
    }
    kwargs.update(overrides)
    return build_config(**kwargs).getroot()  # type: ignore[arg-type]


def _parameters(element: ET.Element, tag: str = "parameter") -> dict[str, str]:
    return {
        entry.get("name", ""): entry.get("value", "")
        for entry in element.findall(_q(tag))
    }


class ConfigStructureTests(unittest.TestCase):
    def test_root_uses_the_configuration_namespace(self) -> None:
        root = _build()
        self.assertEqual(root.tag, _q("ShapeChangeConfiguration"))

    def test_configuration_namespace_is_serialised_as_the_default(self) -> None:
        # ShapeChange looks the elements up without a prefix; a prefixed
        # document makes it abort on a null <input>.
        xml = ET.tostring(_build(), encoding="unicode")
        self.assertIn(f'xmlns="{CONFIG_NS}"', xml)
        self.assertIn("<input ", xml)
        self.assertNotIn("<sc:input", xml)

    def test_input_always_pins_model_type_and_file(self) -> None:
        root = _build()
        input_element = root.find(_q("input"))
        assert input_element is not None
        parameters = _parameters(input_element)

        # Both have dangerous defaults: XMI10, and a demo model on shapechange.net.
        self.assertEqual(parameters["inputModelType"], "SCXML")
        self.assertEqual(parameters["inputFile"], str(Path("/out/model.scxml")))
        self.assertEqual(parameters["appSchemaNameRegex"], "^Test Schema$")

    def test_app_schema_regex_escapes_metacharacters(self) -> None:
        root = _build(app_schema_name="Plan (2.0) [utkast]")
        input_element = root.find(_q("input"))
        assert input_element is not None
        self.assertEqual(
            _parameters(input_element)["appSchemaNameRegex"],
            r"^Plan \(2\.0\) \[utkast\]$",
        )

    def test_log_file_is_configured(self) -> None:
        root = _build()
        log_element = root.find(_q("log"))
        assert log_element is not None
        self.assertEqual(_parameters(log_element)["logFile"], str(Path("/out/log.xml")))

    def test_xml_schema_target_uses_its_own_element_name(self) -> None:
        root = _build()
        targets = root.find(_q("targets"))
        assert targets is not None

        xsd_target = targets.find(_q("TargetXmlSchema"))
        self.assertIsNotNone(xsd_target)
        assert xsd_target is not None
        self.assertEqual(xsd_target.get("class"), XML_SCHEMA_TARGET_CLASS)

        parameters = _parameters(xsd_target, "targetParameter")
        self.assertEqual(parameters["outputDirectory"], str(Path("/out/xsd")))
        self.assertEqual(parameters["defaultEncodingRule"], "sosi")

    def test_json_schema_target_uses_the_generic_element_name(self) -> None:
        root = _build(json_base_uri="https://example.com/base")
        targets = root.find(_q("targets"))
        assert targets is not None

        json_target = targets.find(_q("Target"))
        self.assertIsNotNone(json_target)
        assert json_target is not None
        self.assertEqual(json_target.get("class"), JSON_SCHEMA_TARGET_CLASS)

        parameters = _parameters(json_target, "targetParameter")
        self.assertEqual(parameters["outputDirectory"], str(Path("/out/jsonschema")))
        self.assertEqual(parameters["jsonSchemaVersion"], "2019-09")
        self.assertEqual(parameters["jsonBaseUri"], "https://example.com/base")

    def test_json_base_uri_is_omitted_when_empty(self) -> None:
        json_target = _build().find(f"{_q('targets')}/{_q('Target')}")
        assert json_target is not None
        self.assertNotIn("jsonBaseUri", _parameters(json_target, "targetParameter"))

    def test_targets_can_be_selected(self) -> None:
        targets = _build(targets=["xsd"]).find(_q("targets"))
        assert targets is not None
        self.assertIsNotNone(targets.find(_q("TargetXmlSchema")))
        self.assertIsNone(targets.find(_q("Target")))

        targets = _build(targets=["json"]).find(_q("targets"))
        assert targets is not None
        self.assertIsNone(targets.find(_q("TargetXmlSchema")))
        self.assertIsNotNone(targets.find(_q("Target")))

    def test_unknown_or_empty_targets_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _build(targets=["sqlddl"])
        with self.assertRaises(ValueError):
            _build(targets=[])

    def test_target_classes_can_be_overridden_for_older_shapechange(self) -> None:
        root = _build(
            xml_schema_target_class="de.interactive_instruments.ShapeChange.Target.XmlSchema.XmlSchema",
            json_schema_target_class="de.interactive_instruments.ShapeChange.Target.JSON.JsonSchemaTarget",
        )
        targets = root.find(_q("targets"))
        assert targets is not None
        xsd_target = targets.find(_q("TargetXmlSchema"))
        assert xsd_target is not None
        self.assertEqual(
            xsd_target.get("class"),
            "de.interactive_instruments.ShapeChange.Target.XmlSchema.XmlSchema",
        )


class SosiProfileTests(unittest.TestCase):
    """The SOSI profile lifted from Kartverket's Enterprise Architect add-in."""

    def _encoding_rule(self, target: ET.Element) -> ET.Element | None:
        return target.find(f"{_q('rules')}/{_q('EncodingRule')}")

    def _rule_names(self, encoding_rule: ET.Element) -> list[str]:
        return [rule.get("name", "") for rule in encoding_rule.findall(_q("rule"))]

    def test_sosi_xsd_rule_is_defined_and_extends_iso19136(self) -> None:
        xsd_target = _build().find(f"{_q('targets')}/{_q('TargetXmlSchema')}")
        assert xsd_target is not None
        encoding_rule = self._encoding_rule(xsd_target)
        self.assertIsNotNone(encoding_rule)
        assert encoding_rule is not None

        self.assertEqual(encoding_rule.get("name"), "sosi")
        self.assertEqual(encoding_rule.get("extends"), "iso19136_2007")

        rules = self._rule_names(encoding_rule)
        # These two are what close the gap against the published Geonorge schemas.
        self.assertIn("rule-xsd-all-tagged-values", rules)
        self.assertIn("rule-xsd-prop-targetCodeListURI", rules)
        self.assertEqual(len(rules), 6)

    def test_built_in_xsd_rule_is_not_redefined(self) -> None:
        # Redefining a rule ShapeChange already knows is an error.
        xsd_target = _build(xsd_encoding_rule="iso19136_2007").find(
            f"{_q('targets')}/{_q('TargetXmlSchema')}"
        )
        assert xsd_target is not None
        self.assertIsNone(self._encoding_rule(xsd_target))

    def test_sosi_json_rule_does_not_extend_a_built_in(self) -> None:
        json_target = _build().find(f"{_q('targets')}/{_q('Target')}")
        assert json_target is not None
        encoding_rule = self._encoding_rule(json_target)
        self.assertIsNotNone(encoding_rule)
        assert encoding_rule is not None

        self.assertEqual(encoding_rule.get("name"), "sosiJson")
        # Both built-in JSON rules pull in rule-json-cls-name-as-anchor, which
        # emits $anchor values that are invalid for non-ASCII class names. Rules
        # can only be added, never removed, so the rule must not extend one.
        self.assertIsNone(encoding_rule.get("extends"))
        self.assertNotIn("rule-json-cls-name-as-anchor", self._rule_names(encoding_rule))

    def test_built_in_json_rule_is_not_redefined(self) -> None:
        json_target = _build(json_encoding_rule="defaultGeoJson").find(
            f"{_q('targets')}/{_q('Target')}"
        )
        assert json_target is not None
        self.assertIsNone(self._encoding_rule(json_target))

    def test_sosi_json_maps_norwegian_geometry_to_geojson(self) -> None:
        json_target = _build().find(f"{_q('targets')}/{_q('Target')}")
        assert json_target is not None
        entries = {
            entry.get("type"): entry.get("targetType")
            for entry in json_target.findall(f"{_q('mapEntries')}/{_q('MapEntry')}")
        }
        self.assertEqual(entries["Flate"], "https://geojson.org/schema/Polygon.json")
        self.assertEqual(entries["Kurve"], "https://geojson.org/schema/LineString.json")

    def test_represent_tagged_values_defaults_to_the_sosi_tags(self) -> None:
        input_element = _build().find(_q("input"))
        assert input_element is not None
        self.assertEqual(
            _parameters(input_element)["representTaggedValues"],
            "SOSI_navn,SOSI_verdi,NVDB_ID",
        )

    def test_represent_tagged_values_is_omitted_when_empty(self) -> None:
        input_element = _build(represent_tagged_values=[]).find(_q("input"))
        assert input_element is not None
        self.assertNotIn("representTaggedValues", _parameters(input_element))


class ConfigIncludeTests(unittest.TestCase):
    def _hrefs(self, root: ET.Element) -> list[str]:
        return [
            element.get("href", "")
            for element in root.iter(f"{{{XI_NS}}}include")
        ]

    def test_remote_includes_are_used_by_default(self) -> None:
        hrefs = self._hrefs(_build())
        remote = [href for href in hrefs if not href.startswith("file:")]
        self.assertTrue(all(href.startswith("https://shapechange.net/") for href in remote))
        self.assertIn(
            "https://shapechange.net/resources/config/StandardAliases.xml", hrefs
        )
        self.assertIn(
            "https://shapechange.net/resources/config/StandardMapEntries_JSON.xml", hrefs
        )

    def test_bundled_includes_use_distribution_relative_paths(self) -> None:
        hrefs = self._hrefs(_build(bundled_includes=True))
        bundled = [href for href in hrefs if not href.startswith("file:")]
        self.assertTrue(all(href.startswith("config/") for href in bundled))
        self.assertIn("config/StandardRules.xml", hrefs)

    def test_sosi_map_entries_are_included_as_a_vendored_file(self) -> None:
        # Vendored rather than fetched: the upstream copy is served over plain
        # HTTP from a version-pinned path.
        hrefs = self._hrefs(_build())
        vendored = [href for href in hrefs if href.startswith("file:")]
        self.assertEqual(len(vendored), 1)
        self.assertTrue(vendored[0].endswith("StandardMapEntries_sosi.xml"))

    def test_sosi_map_entries_are_omitted_for_built_in_encoding_rules(self) -> None:
        hrefs = self._hrefs(_build(xsd_encoding_rule="iso19136_2007"))
        self.assertEqual([href for href in hrefs if href.startswith("file:")], [])


class ConfigWriteTests(unittest.TestCase):
    def test_write_config_creates_a_parseable_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "shapechange-config.xml"
            result = write_config(
                output,
                model_path=Path("/out/model.scxml"),
                log_path=Path("/out/log.xml"),
                xsd_directory=Path("/out/xsd"),
                json_directory=Path("/out/jsonschema"),
                app_schema_name="Test Schema",
            )
            self.assertEqual(result, output)
            parsed = ET.parse(output)
            self.assertEqual(parsed.getroot().tag, _q("ShapeChangeConfiguration"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
