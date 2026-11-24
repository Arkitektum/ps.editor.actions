"""Tests for psdata built without a Geonorge metadata record (dataset not registered yet)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from geonorge.psdata import build_placeholder_psdata  # noqa: E402


class PlaceholderPsdataTests(unittest.TestCase):
    def test_fills_title_abstract_and_contact(self) -> None:
        psdata = build_placeholder_psdata(
            title="Eksempeldatasett",
            abstract="Kort beskrivelse.",
            organization="Eksempeletaten",
        )
        ident = psdata["identificationSection"]
        self.assertEqual(ident["title"], "Eksempeldatasett")
        self.assertEqual(ident["abstract"], "Kort beskrivelse.")
        self.assertEqual(ident["contact"][0]["organizationName"], "Eksempeletaten")
        self.assertEqual(psdata["contact"]["organizationName"], "Eksempeletaten")

    def test_marks_unlinked_and_has_no_geonorge_identifiers(self) -> None:
        psdata = build_placeholder_psdata(title="Eksempeldatasett")
        self.assertIs(psdata["metadataLinked"], False)
        self.assertNotIn("metadataSection", psdata)
        self.assertNotIn("uniqueId", psdata["identificationSection"])
        self.assertNotIn("kartkatalog.geonorge.no", str(psdata))

    def test_only_title_is_enough(self) -> None:
        psdata = build_placeholder_psdata(title="Eksempeldatasett")
        self.assertNotIn("contact", psdata)
        self.assertNotIn("abstract", psdata["identificationSection"])


class GeneratorWithoutMetadataIdTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            import generate_product_spec  # noqa: F401
        except ImportError as exc:  # pragma: no cover - optional deps (yaml, requests)
            self.skipTest(f"generator dependencies missing: {exc}")
        self.gen = sys.modules["generate_product_spec"]

    def test_empty_metadata_id_does_not_call_geonorge(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            self.gen, "fetch_psdata", side_effect=AssertionError("must not fetch")
        ):
            paths = self.gen.generate_product_specification(
                "",
                None,
                output_dir=Path(tmp),
                slug_override="eksempel",
                template_path=self.gen._default_template_path(),
                updated="2026-01-01",
                render_spec_markdown=False,
                dataset_title="Eksempeldatasett",
            )
            import json

            psdata = json.loads(Path(paths["psdata"]).read_text(encoding="utf-8"))
            self.assertEqual(Path(paths["directory"]).name, "eksempel")
            self.assertEqual(psdata["identificationSection"]["title"], "Eksempeldatasett")
            self.assertIs(psdata["metadataLinked"], False)

    def test_empty_metadata_id_without_title_or_slug_fails(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            self.gen.generate_product_specification(
                "",
                None,
                output_dir=Path(tmp),
                slug_override=None,
                template_path=self.gen._default_template_path(),
                updated=None,
                render_spec_markdown=False,
            )


if __name__ == "__main__":
    unittest.main()
