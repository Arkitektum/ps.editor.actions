"""Tests for the lineage statement in the data capture section.

Geonorge's ``ProcessHistory`` is ISO 19115 ``LI_Lineage/statement``: free text
describing how the data was captured. It used to be mapped to ``processStep``,
which dressed a narrative up as a list of discrete steps with dates.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from geonorge.psdata import _build_data_capture_statement, build_psdata  # noqa: E402
from md.product_specification import (  # noqa: E402
    build_context,
    render_product_specification,
)

STATEMENT = "Datafangsten er basert på at dataeiere selv leverer inn data."


class StatementTests(unittest.TestCase):
    def test_plain_text_is_kept_as_is(self) -> None:
        self.assertEqual(
            _build_data_capture_statement({"ProcessHistory": STATEMENT}), STATEMENT
        )

    def test_blank_and_missing_give_nothing(self) -> None:
        self.assertIsNone(_build_data_capture_statement({}))
        self.assertIsNone(_build_data_capture_statement({"ProcessHistory": ""}))
        self.assertIsNone(_build_data_capture_statement({"ProcessHistory": "   "}))

    def test_a_list_is_joined_into_one_statement(self) -> None:
        # Every dataset checked returns a plain string, but the shape is not
        # guaranteed by the API.
        self.assertEqual(
            _build_data_capture_statement({"ProcessHistory": ["Først.", "Så."]}),
            "Først.\n\nSå.",
        )

    def test_mapping_entries_are_read(self) -> None:
        self.assertEqual(
            _build_data_capture_statement(
                {"ProcessHistory": {"Description": STATEMENT, "Date": "2026-01-01"}}
            ),
            STATEMENT,
        )

    def test_duplicates_are_not_repeated(self) -> None:
        self.assertEqual(
            _build_data_capture_statement({"ProcessHistory": [STATEMENT, STATEMENT]}),
            STATEMENT,
        )


class PsdataShapeTests(unittest.TestCase):
    def _section(self, metadata) -> dict:
        psdata = build_psdata("test-uuid", metadata)
        return psdata.get("dataCaptureAndProductionSection") or {}

    def test_statement_lands_under_data_capture_statement(self) -> None:
        section = self._section({"ProcessHistory": STATEMENT})
        self.assertEqual(
            section["DataAcquisitionAndProcessing"]["dataCaptureStatement"], STATEMENT
        )

    def test_process_step_is_gone(self) -> None:
        section = self._section({"ProcessHistory": STATEMENT})
        self.assertNotIn("processStep", section["DataAcquisitionAndProcessing"])

    def test_section_is_omitted_without_a_statement(self) -> None:
        self.assertEqual(self._section({"NorwegianTitle": "Uten historikk"}), {})


class RenderingTests(unittest.TestCase):
    def _render(self, psdata) -> str:
        return render_product_specification(
            "{{dataCaptureAndProductionSection}}", build_context(psdata)
        )

    def test_the_statement_is_plain_prose(self) -> None:
        # One free-text paragraph needs no heading and no bullet around it.
        rendered = self._render(build_psdata("test-uuid", {"ProcessHistory": STATEMENT}))
        self.assertEqual(rendered.strip(), STATEMENT)

    def test_no_labels_are_left_over(self) -> None:
        rendered = self._render(build_psdata("test-uuid", {"ProcessHistory": STATEMENT}))
        for label in ("Prosesstrinn", "Datafangstbeskrivelse", "Datainnsamling"):
            self.assertNotIn(label, rendered)

    def test_other_content_still_renders(self) -> None:
        # Flattening the statement must not swallow anything else the section
        # might carry.
        psdata = build_psdata("test-uuid", {"ProcessHistory": STATEMENT})
        psdata["dataCaptureAndProductionSection"]["DataAcquisitionAndProcessing"][
            "annet"
        ] = "beholdes"
        rendered = self._render(psdata)
        self.assertIn(STATEMENT, rendered)
        self.assertIn("beholdes", rendered)

    def test_psdata_itself_keeps_the_structure(self) -> None:
        # The flattening is presentation only; the JSON stays as mapped.
        psdata = build_psdata("test-uuid", {"ProcessHistory": STATEMENT})
        self.assertEqual(
            psdata["dataCaptureAndProductionSection"]["DataAcquisitionAndProcessing"][
                "dataCaptureStatement"
            ],
            STATEMENT,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
