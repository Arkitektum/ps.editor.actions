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
        self.assertEqual(parameters["defaultEncodingRule"], "iso19136_2007")

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


class ConfigIncludeTests(unittest.TestCase):
    def _hrefs(self, root: ET.Element) -> list[str]:
        return [
            element.get("href", "")
            for element in root.iter(f"{{{XI_NS}}}include")
        ]

    def test_remote_includes_are_used_by_default(self) -> None:
        hrefs = self._hrefs(_build())
        self.assertTrue(all(href.startswith("https://shapechange.net/") for href in hrefs))
        self.assertIn(
            "https://shapechange.net/resources/config/StandardAliases.xml", hrefs
        )
        self.assertIn(
            "https://shapechange.net/resources/config/StandardMapEntries_JSON.xml", hrefs
        )

    def test_bundled_includes_use_distribution_relative_paths(self) -> None:
        hrefs = self._hrefs(_build(bundled_includes=True))
        self.assertTrue(all(href.startswith("config/") for href in hrefs))
        self.assertIn("config/StandardRules.xml", hrefs)


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
