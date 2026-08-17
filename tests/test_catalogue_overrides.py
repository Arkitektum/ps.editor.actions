"""Tests for the feature-catalogue overrides overlay."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from catalogue_overrides import apply_overrides, load_overrides  # noqa: E402


def _sample() -> list[dict]:
    return [
        {
            "name": "Bygning",
            "description": "",
            "attributes": [
                {"name": "bygningsnavn", "type": "string", "cardinality": "0..1"},
                {"name": "bygningstype", "type": "Bygningstype", "cardinality": "1",
                 "description": "original"},
            ],
            "relationships": {"inheritance": [], "associations": []},
        },
        {"name": "Eiendom", "description": "En matrikkelenhet.", "attributes": []},
    ]


class ApplyOverridesTests(unittest.TestCase):
    def test_sets_feature_type_and_attribute_descriptions(self) -> None:
        overrides = {
            "featureTypes": {
                "Bygning": {
                    "description": "En bygning.",
                    "attributes": {"bygningsnavn": {"description": "Navn på bygningen."}},
                }
            }
        }
        result = apply_overrides(_sample(), overrides)
        bygning = result[0]
        self.assertEqual(bygning["description"], "En bygning.")
        attrs = {a["name"]: a for a in bygning["attributes"]}
        self.assertEqual(attrs["bygningsnavn"]["description"], "Navn på bygningen.")
        # Non-overridden attribute keeps its original description...
        self.assertEqual(attrs["bygningstype"]["description"], "original")
        # ...and structure/other fields are untouched.
        self.assertEqual(attrs["bygningstype"]["type"], "Bygningstype")
        self.assertEqual(bygning["relationships"], {"inheritance": [], "associations": []})
        # Feature type without an override is unchanged.
        self.assertEqual(result[1]["description"], "En matrikkelenhet.")

    def test_ignores_unknown_names_and_blank_values(self) -> None:
        overrides = {
            "featureTypes": {
                "Bygning": {
                    "description": "   ",  # blank -> ignored
                    "attributes": {
                        "finnesIkke": {"description": "x"},  # unknown attr -> ignored
                        "bygningstype": {"description": ""},  # blank -> keep original
                    },
                },
                "Ukjent": {"description": "y"},  # unknown feature type -> ignored
            }
        }
        result = apply_overrides(_sample(), overrides)
        bygning = result[0]
        self.assertEqual(bygning["description"], "")  # blank override did not win
        attrs = {a["name"]: a for a in bygning["attributes"]}
        self.assertEqual(attrs["bygningstype"]["description"], "original")
        self.assertNotIn("finnesIkke", attrs)

    def test_empty_overrides_returns_input_unchanged(self) -> None:
        sample = _sample()
        self.assertIs(apply_overrides(sample, {}), sample)
        self.assertIs(apply_overrides(sample, {"featureTypes": {}}), sample)

    def test_does_not_mutate_input(self) -> None:
        sample = _sample()
        snapshot = json.dumps(sample, ensure_ascii=False)
        apply_overrides(
            sample, {"featureTypes": {"Bygning": {"description": "ny"}}}
        )
        self.assertEqual(json.dumps(sample, ensure_ascii=False), snapshot)


class LoadOverridesTests(unittest.TestCase):
    def test_missing_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(load_overrides(Path(d) / "nope.json"), {})

    def test_invalid_json_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.json"
            p.write_text("{not json", encoding="utf-8")
            self.assertEqual(load_overrides(p), {})

    def test_valid_file_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ok.json"
            p.write_text('{"featureTypes": {"A": {"description": "x"}}}', encoding="utf-8")
            self.assertEqual(load_overrides(p)["featureTypes"]["A"]["description"], "x")


if __name__ == "__main__":
    unittest.main()
