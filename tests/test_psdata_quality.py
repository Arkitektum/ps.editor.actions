"""Tests for the Datakvalitet chapter: deduplication and per-field layout."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from geonorge.psdata import _extract_quality  # noqa: E402
from md.product_specification import _format_data_quality_report  # noqa: E402


class QualityDeduplicationTests(unittest.TestCase):
    """Geonorge records the same measurement in two places.

    ``QualitySpecifications`` holds it structured, and ``QuantitativeResult``
    repeats it as a flat "<description>: <value>" summary. Emitting both put the
    same measure in the chapter twice.
    """

    def test_quantitative_result_repeating_a_spec_is_dropped(self) -> None:
        quality = _extract_quality(
            {
                "QualitySpecifications": [
                    {
                        "Title": "Prosentvis oppfyllelse av FAIR-prinsipper",
                        "Explanation": "Angir fullstendighet mot FAIR-prinsippene",
                        "QuantitativeResult": "90",
                    }
                ],
                "QuantitativeResult": {
                    "FAIR": "Prosentvis oppfyllelse av FAIR-prinsipper: 90%"
                },
            }
        )
        report = quality["report"]
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["nameOfMeasure"], "Prosentvis oppfyllelse av FAIR-prinsipper")
        self.assertEqual(report[0]["result"], "90")

    def test_quantitative_result_with_its_own_measure_is_kept(self) -> None:
        # Only exact repeats are removed; anything genuinely new still comes through.
        quality = _extract_quality(
            {
                "QualitySpecifications": [
                    {"Title": "Helt annen måling", "QuantitativeResult": "1"}
                ],
                "QuantitativeResult": {
                    "Coverage": "Prosentvis dekning i forhold til utstrekning: 100%"
                },
            }
        )
        names = [entry["nameOfMeasure"] for entry in quality["report"]]
        self.assertEqual(names, ["Helt annen måling", "Coverage"])

    def test_matching_ignores_case(self) -> None:
        quality = _extract_quality(
            {
                "QualitySpecifications": [{"Title": "FAIR-Prinsipper", "QuantitativeResult": "9"}],
                "QuantitativeResult": {"FAIR": "fair-prinsipper: 9%"},
            }
        )
        self.assertEqual(len(quality["report"]), 1)

    def test_explanation_is_not_written_to_two_fields(self) -> None:
        # Without a quantitative result the explanation is the outcome, so it goes
        # to descriptiveResult only. Setting both printed the same sentence twice.
        quality = _extract_quality(
            {
                "QualitySpecifications": [
                    {
                        "Title": "Sosi applikasjonsskjema",
                        "Explanation": "GML-filer er i henhold til applikasjonsskjema",
                    }
                ]
            }
        )
        entry = quality["report"][0]
        self.assertEqual(entry["descriptiveResult"], "GML-filer er i henhold til applikasjonsskjema")
        self.assertNotIn("measureDescription", entry)

    def test_measure_description_is_kept_when_there_is_a_result(self) -> None:
        quality = _extract_quality(
            {
                "QualitySpecifications": [
                    {"Title": "M", "Explanation": "Beskrivelse", "QuantitativeResult": "90"}
                ]
            }
        )
        entry = quality["report"][0]
        self.assertEqual(entry["measureDescription"], "Beskrivelse")
        self.assertNotIn("descriptiveResult", entry)


class QualityReportLayoutTests(unittest.TestCase):
    """The measure name leads; every other field gets its own line beneath it."""

    def test_fields_are_rendered_on_separate_lines(self) -> None:
        rendered = _format_data_quality_report(
            [
                {
                    "nameOfMeasure": "Prosentvis oppfyllelse av FAIR-prinsipper",
                    "measureDescription": "Angir fullstendighet",
                    "result": "90",
                }
            ]
        )
        self.assertEqual(
            rendered.splitlines(),
            [
                "**Kvalitetsmål**: Prosentvis oppfyllelse av FAIR-prinsipper",
                "",
                "- **Målebeskrivelse**: Angir fullstendighet",
                "- **Resultat**: 90",
            ],
        )

    def test_descriptive_result_gets_its_own_label(self) -> None:
        rendered = _format_data_quality_report(
            [{"nameOfMeasure": "M", "descriptiveResult": "I henhold til spesifikasjonen"}]
        )
        self.assertIn("- **Beskrivende resultat**: I henhold til spesifikasjonen", rendered)

    def test_entries_are_separated_by_a_blank_line(self) -> None:
        rendered = _format_data_quality_report(
            [{"nameOfMeasure": "A", "result": "1"}, {"nameOfMeasure": "B", "result": "2"}]
        )
        self.assertIn("- **Resultat**: 1\n\n**Kvalitetsmål**: B", rendered)

    def test_unknown_keys_keep_their_source_order(self) -> None:
        rendered = _format_data_quality_report(
            [{"nameOfMeasure": "M", "zzz": "sist", "aaa": "forst"}]
        )
        self.assertLess(rendered.index("zzz"), rendered.index("aaa"))

    def test_single_mapping_is_accepted(self) -> None:
        rendered = _format_data_quality_report({"nameOfMeasure": "M", "result": "1"})
        self.assertIn("**Kvalitetsmål**: M", rendered)

    def test_empty_input_renders_nothing(self) -> None:
        self.assertEqual(_format_data_quality_report(None), "")
        self.assertEqual(_format_data_quality_report([]), "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
