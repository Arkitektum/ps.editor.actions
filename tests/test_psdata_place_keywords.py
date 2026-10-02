"""Tests for routing place keywords to the geographic extent.

Geonorge keeps place names among the keywords. They say where the data applies,
not what it is about, so they belong in the extent as a geographic description
(ISO 19115 EX_GeographicDescription) rather than in the subject keyword list.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from geonorge.psdata import (  # noqa: E402
    _collect_keywords,
    _collect_place_keywords,
    build_psdata,
)

METADATA = {
    "NorwegianTitle": "Testprodukt",
    "KeywordsTheme": [
        {"KeywordValue": "Reguleringsplan", "Type": "theme"},
        {"KeywordValue": "Arealbruk", "Type": "theme"},
    ],
    "KeywordsPlace": [
        {"KeywordValue": "Norges fastland", "Type": "place"},
        {"KeywordValue": "Kommune", "Type": "place"},
    ],
}


class PlaceKeywordSelectionTests(unittest.TestCase):
    def test_place_keywords_are_collected(self) -> None:
        self.assertEqual(
            _collect_place_keywords(METADATA), ["Norges fastland", "Kommune"]
        )

    def test_place_keywords_leave_the_subject_list(self) -> None:
        keywords = _collect_keywords(METADATA)
        self.assertEqual(keywords, ["Reguleringsplan", "Arealbruk"])

    def test_the_type_decides_not_the_word(self) -> None:
        # "Kommune" is a place in one dataset and a subject in another; only the
        # keyword type can tell them apart.
        metadata = {
            "KeywordsTheme": [{"KeywordValue": "Kommune", "Type": "theme"}],
            "KeywordsPlace": [],
        }
        self.assertEqual(_collect_keywords(metadata), ["Kommune"])
        self.assertEqual(_collect_place_keywords(metadata), [])

    def test_place_type_is_honoured_outside_the_place_field(self) -> None:
        # The field name and the type code are two signals for the same thing;
        # either one is enough.
        metadata = {"KeywordsOther": [{"KeywordValue": "Svalbard", "Type": "place"}]}
        self.assertEqual(_collect_place_keywords(metadata), ["Svalbard"])
        self.assertEqual(_collect_keywords(metadata), [])

    def test_duplicates_are_removed_case_insensitively(self) -> None:
        metadata = {
            "KeywordsPlace": [
                {"KeywordValue": "Norge", "Type": "place"},
                {"KeywordValue": "norge", "Type": "place"},
            ]
        }
        self.assertEqual(_collect_place_keywords(metadata), ["Norge"])

    def test_plain_string_keywords_still_work(self) -> None:
        # Not every entry is a mapping; a bare string carries no type.
        metadata = {"KeywordsTheme": "Plan, Arealbruk"}
        self.assertEqual(_collect_keywords(metadata), ["Plan", "Arealbruk"])
        self.assertEqual(_collect_place_keywords(metadata), [])

    def test_missing_keywords_are_handled(self) -> None:
        self.assertEqual(_collect_place_keywords({}), [])
        self.assertEqual(_collect_keywords({}), [])


class ExtentTests(unittest.TestCase):
    def _extent(self, metadata) -> dict:
        psdata = build_psdata("test-uuid", metadata)
        return psdata["identificationSection"].get("extent") or {}

    def test_places_land_in_the_extent(self) -> None:
        self.assertEqual(
            self._extent(METADATA)["geographicDescription"],
            ["Norges fastland", "Kommune"],
        )

    def test_bounding_box_is_unaffected(self) -> None:
        metadata = dict(METADATA, BoundingBox={"WestBoundLongitude": "2", "EastBoundLongitude": "33",
                                               "SouthBoundLatitude": "57", "NorthBoundLatitude": "72"})
        extent = self._extent(metadata)
        self.assertIn("geographicDescription", extent)
        self.assertIn("geographicElement", extent)

    def test_no_place_keywords_leaves_the_key_out(self) -> None:
        extent = self._extent({"NorwegianTitle": "Uten sted"})
        self.assertNotIn("geographicDescription", extent)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
