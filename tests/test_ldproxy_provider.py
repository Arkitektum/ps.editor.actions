"""Tests for rewriting a ShapeChange ldproxy provider to the GeoPackage dialect."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shapechange.ldproxy_provider import (  # noqa: E402
    apply_geopackage_provider,
    find_provider_files,
)

_PGIS_PROVIDER = """---
id: ny_datakilde
providerType: FEATURE
providerSubType: SQL
nativeCrs:
  code: 25833
nativeTimeZone: Europe/Oslo
connectionInfo:
  database: FIXME
  host: FIXME
  user: FIXME
  password: FIXME-base64-encoded
  schemas:
  - public
sourcePathDefaults:
  primaryKey: objid
  sortKey: objid
types:
  dataavgrensning:
    sourcePath: /dataavgrensning
    type: OBJECT
"""


class LdproxyProviderTests(unittest.TestCase):
    def _write_provider(self, root: Path, input_id: str = "INPUT") -> Path:
        provider = (
            root
            / input_id
            / "data"
            / "entities"
            / "instances"
            / "providers"
            / "ny_datakilde.yml"
        )
        provider.parent.mkdir(parents=True, exist_ok=True)
        provider.write_text(_PGIS_PROVIDER, encoding="utf-8")
        return provider

    def test_finds_provider_under_input_subdir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_provider(root, "LDPROXY_FLAT")
            found = find_provider_files(root)
            self.assertEqual(len(found), 1)

    def test_rewrites_connection_info_to_gpkg(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_provider(root)
            changed = apply_geopackage_provider(root, "ny-datakilde.gpkg")
            self.assertEqual(len(changed), 1)

            data = yaml.safe_load(changed[0].read_text(encoding="utf-8"))
            self.assertEqual(
                data["connectionInfo"], {"dialect": "GPKG", "database": "ny-datakilde.gpkg"}
            )
            # PostGIS-only keys are dropped.
            self.assertNotIn("host", data["connectionInfo"])
            self.assertNotIn("schemas", data["connectionInfo"])
            # Everything else (objid PK, types, CRS) is preserved.
            self.assertEqual(data["sourcePathDefaults"]["primaryKey"], "objid")
            self.assertIn("dataavgrensning", data["types"])
            self.assertEqual(data["nativeCrs"]["code"], 25833)

    def test_missing_directory_yields_no_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            changed = apply_geopackage_provider(Path(directory) / "nope", "x.gpkg")
            self.assertEqual(changed, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
