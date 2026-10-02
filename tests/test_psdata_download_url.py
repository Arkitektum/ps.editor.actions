"""Tests for completing the Geonorge download API URL.

The metadata gives the download service as a bare capabilities endpoint. Without
the dataset UUID it redirects to an error page, so the link in the delivery table
leads nowhere.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from geonorge.psdata import _complete_download_url, build_psdata  # noqa: E402

UUID = "041f1e6e-bdbc-4091-b48f-8a5990f3cc5b"


class CompleteDownloadUrlTests(unittest.TestCase):
    def test_bare_endpoint_gets_the_uuid(self) -> None:
        self.assertEqual(
            _complete_download_url("https://nedlasting.geonorge.no/api/capabilities/", UUID),
            f"https://nedlasting.geonorge.no/api/capabilities/{UUID}",
        )

    def test_other_hosts_are_handled_too(self) -> None:
        # The download service is deployed by several agencies; the path is what
        # identifies it, not the host.
        for base in (
            "https://nedlasting.ngu.no",
            "https://nap.ft.dibk.no/services/nedlasting",
        ):
            self.assertEqual(
                _complete_download_url(f"{base}/api/capabilities/", UUID),
                f"{base}/api/capabilities/{UUID}",
            )

    def test_a_url_that_already_names_a_dataset_is_untouched(self) -> None:
        url = f"https://nedlasting.geonorge.no/api/capabilities/{UUID}"
        self.assertEqual(_complete_download_url(url, UUID), url)

    def test_unrelated_urls_are_untouched(self) -> None:
        for url in (
            "https://example.no/services/download/",
            "http://nedlasting.geonorge.no/geonorge/ATOM-feeds/Noe_AtomFeedGML.xml",
            "https://nedlasting.geonorge.no/api/codelists/",
        ):
            self.assertEqual(_complete_download_url(url, UUID), url)

    def test_missing_uuid_leaves_the_url_alone(self) -> None:
        url = "https://nedlasting.geonorge.no/api/capabilities/"
        self.assertEqual(_complete_download_url(url, ""), url)

    def test_empty_input_is_handled(self) -> None:
        self.assertEqual(_complete_download_url("", UUID), "")


class DeliverySectionTests(unittest.TestCase):
    def _urls(self, metadata) -> set[str]:
        psdata = build_psdata(UUID, metadata)
        blob = json.dumps(psdata.get("deliverySection") or [], ensure_ascii=False)
        return {part for part in blob.split('"') if part.startswith("http")}

    def test_top_level_download_url_is_completed(self) -> None:
        urls = self._urls(
            {
                "Uuid": UUID,
                "DistributionProtocol": "GEONORGE:DOWNLOAD",
                "DownloadUrl": "https://nedlasting.geonorge.no/api/capabilities/",
            }
        )
        self.assertIn(f"https://nedlasting.geonorge.no/api/capabilities/{UUID}", urls)

    def test_distribution_formats_url_is_completed(self) -> None:
        # This is the path the real metadata takes, and the one that first
        # surfaced the broken link.
        urls = self._urls(
            {
                "Uuid": UUID,
                "DistributionsFormats": [
                    {
                        "ProtocolName": "Geonorge nedlastning",
                        "Protocol": "GEONORGE:DOWNLOAD",
                        "URL": "https://nedlasting.geonorge.no/api/capabilities/",
                        "Name": "GML",
                    }
                ],
            }
        )
        self.assertIn(f"https://nedlasting.geonorge.no/api/capabilities/{UUID}", urls)

    def test_atom_feeds_are_left_alone(self) -> None:
        feed = "http://nedlasting.geonorge.no/geonorge/ATOM-feeds/Noe_AtomFeedGML.xml"
        urls = self._urls(
            {
                "Uuid": UUID,
                "DistributionsFormats": [
                    {"ProtocolName": "Atom Feed", "Protocol": "W3C:AtomFeed", "URL": feed}
                ],
            }
        )
        self.assertIn(feed, urls)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
