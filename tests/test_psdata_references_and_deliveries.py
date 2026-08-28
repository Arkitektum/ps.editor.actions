"""Tests for product-reference links (Produktark/Produktside) in the last chapter and
generated output files (GeoPackage/schema) added to the delivery section."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from geonorge.psdata import _collect_links, build_psdata  # noqa: E402
from md.product_specification import build_context  # noqa: E402
from scripts.assemble_product_spec import (  # noqa: E402
    _build_output_file_deliveries,
    _augment_delivery_section,
)


class ProductReferenceTests(unittest.TestCase):
    METADATA = {
        "NorwegianTitle": "Testprodukt",
        "ProductSheetUrl": "https://example.com/ark.pdf",
        "ProductPageUrl": "https://example.com/side",
    }

    def test_collect_links_keeps_produktark_and_produktside(self) -> None:
        titles = [link.get("title") for link in _collect_links(self.METADATA)]
        self.assertIn("Produktark", titles)
        self.assertIn("Produktside", titles)

    def test_build_psdata_exposes_additional_references(self) -> None:
        refs = build_psdata("uuid", self.METADATA)["additionalReferences"]
        self.assertEqual(
            refs,
            [
                {"title": "Produktark", "href": "https://example.com/ark.pdf"},
                {"title": "Produktside", "href": "https://example.com/side"},
            ],
        )

    def test_additional_references_absent_when_no_urls(self) -> None:
        self.assertIsNone(build_psdata("uuid", {"NorwegianTitle": "X"}).get("additionalReferences"))

    def test_build_context_formats_references_as_markdown_links(self) -> None:
        psdata = build_psdata("uuid", self.METADATA)
        formatted = build_context(psdata)["additionalReferences"]
        self.assertIn("**Produktark:** [https://example.com/ark.pdf]", formatted)
        self.assertIn("**Produktside:** [https://example.com/side]", formatted)


class OutputDeliveryTests(unittest.TestCase):
    def _spec_tree(self, root: Path) -> Path:
        spec = root / "produktspesifikasjon" / "slug" / "ny-datakilde"
        spec.mkdir(parents=True, exist_ok=True)
        (spec / "ny-datakilde.gpkg").write_bytes(b"x")
        xsd = spec / "schema" / "xsd" / "INPUT"
        xsd.mkdir(parents=True, exist_ok=True)
        (xsd / "Model.xsd").write_text("x", encoding="utf-8")
        js = spec / "schema" / "jsonschema" / "INPUT"
        js.mkdir(parents=True, exist_ok=True)
        (js / "Model.json").write_text("{}", encoding="utf-8")
        return spec

    def test_scans_gpkg_and_schema_with_raw_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = self._spec_tree(root)
            entries = _build_output_file_deliveries(
                spec, root, "https://raw.githubusercontent.com/o/r/main"
            )
            urls = [
                e["delivery"]["deliveryMedium"]["deliveryService"]["serviceEndpoint"]
                for e in entries
            ]
            formats = [e["delivery"]["deliveryFormat"][0]["formatName"] for e in entries]
            self.assertEqual(len(entries), 3)
            self.assertIn(
                "https://raw.githubusercontent.com/o/r/main/produktspesifikasjon/slug/ny-datakilde/ny-datakilde.gpkg",
                urls,
            )
            self.assertIn("GPKG", formats)
            self.assertIn("XSD", formats)
            self.assertIn("JSON Schema", formats)

    def test_augment_appends_to_existing_delivery_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = self._spec_tree(root)
            psdata_path = spec / "psdata.json"
            existing = {"delivery": {"deliveryMedium": {"deliveryMediumName": "WFS"}}}
            psdata_path.write_text(
                json.dumps({"deliverySection": [existing]}), encoding="utf-8"
            )
            _augment_delivery_section(
                psdata_path, spec, root, "https://raw.githubusercontent.com/o/r/main"
            )
            deliveries = json.loads(psdata_path.read_text(encoding="utf-8"))["deliverySection"]
            # Existing metadata delivery kept + 3 generated files appended.
            self.assertEqual(deliveries[0], existing)
            self.assertEqual(len(deliveries), 4)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
