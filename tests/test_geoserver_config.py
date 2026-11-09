"""Tests for the GeoServer setup script writers (PostGIS and GeoPackage data stores)."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from geopackage.writer import write_geopackage  # noqa: E402
from geoserver_config.common import WORLD_BBOX, workspace_name  # noqa: E402
from geoserver_config.gpkg import (  # noqa: E402
    build_geoserver_gpkg_config,
    render_geoserver_gpkg_script,
    write_geoserver_gpkg_config,
)
from geoserver_config.postgis import (  # noqa: E402
    build_geoserver_postgis_config,
    render_geoserver_postgis_script,
    write_geoserver_postgis_config,
)
from postgis.writer import build_postgis_ddl  # noqa: E402
from pygeoapi_config.postgis import build_pygeoapi_postgis_config  # noqa: E402
from service_metadata import OFFERED_EPSG_CODES  # noqa: E402

FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "shapechange_feature_types.json"

BASH = shutil.which("bash")

# Stands in for curl: records each call and answers like GeoServer would for a
# resource that is missing (GET -> FAKE_GET_STATUS, default 404) or created.
_FAKE_CURL = r"""#!/usr/bin/env bash
method=GET data= url=
while [ $# -gt 0 ]; do
  case $1 in
    -X) method=$2; shift 2 ;;
    --data-binary) data=$2; shift 2 ;;
    -o|-w|-u|-H) shift 2 ;;
    -*) shift ;;
    *) url=$1; shift ;;
  esac
done
count=$(($(wc -l < "$CURL_LOG") + 1))
printf '%s %s\n' "$method" "$url" >> "$CURL_LOG"
printf '%s' "$data" > "$CURL_LOG.$count"
if [ "$method" = GET ]; then printf '%s' "${FAKE_GET_STATUS:-404}"; else printf 201; fi
"""


def _fixture() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _feature(name: str, *attributes: dict, **extra) -> dict:
    return {"name": name, "attributes": list(attributes), **extra}


def _attr(name: str, type_: str = "CharacterString", cardinality: str = "0..1", **extra) -> dict:
    return {"name": name, "type": type_, "cardinality": cardinality, **extra}


def _psdata() -> dict:
    """A synthetic psdata document."""
    return {
        "identificationSection": {
            "title": "Testdatasett",
            "abstract": "Et syntetisk datasett for tester.",
            "keyword": ["arealressurs"],
            "contact": [
                {"organizationName": "Eksempeletaten", "role": "owner"},
                {
                    "organizationName": "Kontaktkontoret",
                    "electronicMailAddress": "kontakt@example.com",
                    "role": "pointOfContact",
                },
            ],
            "restriction": {
                "legalConstraints": {
                    "accessConstraints": "Ingen",
                    "license": "CC BY 4.0",
                    "licenseUrl": "https://creativecommons.org/licenses/by/4.0/",
                }
            },
            "extent": {
                "geographicElement": {
                    "westBoundLongitude": "4.5",
                    "eastBoundLongitude": "31.2",
                    "southBoundLatitude": "57.9",
                    "northBoundLatitude": "71.2",
                }
            },
        },
        "metadataSection": {
            "metadataIdentifier": {"metadataLinkage": "https://example.com/metadata/1"}
        },
    }


def _layers(config: dict) -> dict[str, dict]:
    return {layer["featureType"]["name"]: layer["featureType"] for layer in config["layers"]}


class _Run:
    """The result of running a setup script against the fake curl."""

    def __init__(self, result: subprocess.CompletedProcess, log: Path) -> None:
        self.returncode = result.returncode
        self.stdout = result.stdout
        self.stderr = result.stderr
        lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        self.calls = [tuple(line.split(" ", 1)) for line in lines]
        self.bodies = [
            Path(f"{log}.{index}").read_text(encoding="utf-8") for index in range(1, len(lines) + 1)
        ]

    def body(self, method: str, suffix: str) -> str:
        for (call_method, url), body in zip(self.calls, self.bodies):
            if call_method == method and url.endswith(suffix):
                return body
        raise AssertionError(f"no {method} …{suffix} in {self.calls}")


def _run_script(script: str, directory: Path, env: dict[str, str]) -> _Run:
    bin_dir = directory / "bin"
    bin_dir.mkdir(exist_ok=True)
    curl = bin_dir / "curl"
    curl.write_text(_FAKE_CURL, encoding="utf-8")
    curl.chmod(0o755)
    path = directory / "setup.sh"
    path.write_text(script, encoding="utf-8")
    log = directory / "curl.log"
    log.write_text("", encoding="utf-8")
    result = subprocess.run(
        [BASH, str(path)],
        capture_output=True,
        text=True,
        env={
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "CURL_LOG": str(log),
            "GEOSERVER_URL": "http://geoserver.example/geoserver",
            "GEOSERVER_USER": "admin",
            "GEOSERVER_PASSWORD": "hemmelig",
            **env,
        },
        check=False,
    )
    return _Run(result, log)


_POSTGIS_ENV = {"POSTGIS_HOST": "db", "POSTGIS_USER": "geoserver", "POSTGIS_PASSWORD": "pw"}


class WorkspaceNameTests(unittest.TestCase):
    def test_kebab_case_like_the_layers(self) -> None:
        self.assertEqual(workspace_name("dyrkbar-jord_xmi"), "dyrkbar-jord-xmi")
        self.assertEqual(workspace_name("ÅpenGrunn"), "aapen-grunn")

    def test_never_starts_with_a_digit(self) -> None:
        # The workspace is also the namespace prefix, which must be an XML name.
        self.assertEqual(workspace_name("2024-plan"), "ps-2024-plan")


class PostgisConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = build_geoserver_postgis_config(
            _fixture(), psdata=_psdata(), workspace="bygning", dbname="Bygning", schema="Data"
        )

    def test_one_layer_per_feature_table(self) -> None:
        # BaseFeature is abstract, so it has no table and no layer.
        self.assertEqual(list(_layers(self.config)), ["bygning", "eiendom"])

    def test_layer_names_match_the_pygeoapi_collections(self) -> None:
        pygeoapi = build_pygeoapi_postgis_config(_fixture(), psdata=_psdata(), dbname="x")
        self.assertEqual(list(_layers(self.config)), list(pygeoapi["resources"]))

    def test_layer_shape(self) -> None:
        layer = _layers(self.config)["bygning"]
        self.assertEqual(layer["nativeName"], "bygning")
        self.assertEqual(layer["title"], "Bygning")
        self.assertEqual(layer["abstract"], "En bygning.")
        self.assertEqual(layer["keywords"], {"string": ["Bygning"]})
        self.assertEqual(layer["srs"], "EPSG:25833")
        self.assertEqual(layer["projectionPolicy"], "FORCE_DECLARED")
        self.assertEqual(
            layer["latLonBoundingBox"],
            {"minx": 4.5, "miny": 57.9, "maxx": 31.2, "maxy": 71.2, "crs": "EPSG:4326"},
        )
        # GeoServer derives the native box from the lat/lon one.
        self.assertNotIn("nativeBoundingBox", layer)
        self.assertEqual(
            layer["responseSRS"]["string"], [str(code) for code in OFFERED_EPSG_CODES]
        )
        self.assertTrue(layer["overridingServiceSRS"])

    def test_tables_exist_in_the_ddl_script(self) -> None:
        ddl = build_postgis_ddl(_fixture(), schema="data")
        for layer in _layers(self.config).values():
            self.assertIn(f'CREATE TABLE "data"."{layer["nativeName"]}"', ddl)

    def test_storage_crs_follows_the_geometry_and_is_offered(self) -> None:
        config = build_geoserver_postgis_config(
            [_feature("Sone", geometry={"name": "område", "type": "GM_Surface", "storageCrs": "EPSG:32633"})]
        )
        layer = _layers(config)["sone"]
        self.assertEqual(layer["srs"], "EPSG:32633")
        self.assertEqual(layer["responseSRS"]["string"][-1], "32633")

    def test_feature_type_without_geometry_is_published(self) -> None:
        config = build_geoserver_postgis_config([_feature("Register", _attr("navn"))])
        self.assertEqual(_layers(config)["register"]["srs"], "EPSG:25833")

    def test_abstract_and_external_types_are_skipped(self) -> None:
        config = build_geoserver_postgis_config(
            [
                _feature("Felles", _attr("navn"), abstract=True),
                _feature("Ekstern", _attr("navn"), external=True),
                _feature("Konkret", _attr("navn"), relationships={"inheritance": ["Felles"]}),
            ]
        )
        self.assertEqual(list(_layers(config)), ["konkret"])

    def test_colliding_layer_names_stay_distinct(self) -> None:
        config = build_geoserver_postgis_config([_feature("Dyrkbar jord"), _feature("DyrkbarJord")])
        self.assertEqual(list(_layers(config)), ["dyrkbar-jord", "dyrkbar-jord-2"])

    def test_store_defaults(self) -> None:
        self.assertEqual(
            self.config["store"], {"name": "postgis", "database": "bygning", "schema": "data"}
        )
        config = build_geoserver_postgis_config([_feature("Veg")])
        self.assertEqual(config["store"], {"name": "postgis", "database": None, "schema": "public"})
        self.assertIsNone(config["workspace"])


class ServiceMetadataTests(unittest.TestCase):
    def test_psdata_values(self) -> None:
        config = build_geoserver_postgis_config(_fixture(), psdata=_psdata(), workspace="bygning")
        wfs = config["service"]["wfs"]
        self.assertEqual(wfs["title"], "Testdatasett")
        self.assertEqual(wfs["abstrct"], "Et syntetisk datasett for tester.")
        self.assertEqual(wfs["keywords"], {"string": ["arealressurs"]})
        self.assertEqual(wfs["accessConstraints"], "Ingen")
        self.assertEqual(wfs["fees"], "CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)")
        self.assertEqual(wfs["onlineResource"], "https://example.com/metadata/1")
        # Without a service level GeoServer's GetCapabilities fails.
        self.assertEqual(wfs["serviceLevel"], "COMPLETE")
        self.assertEqual(
            config["settings"]["settings"]["contact"],
            {"contactOrganization": "Eksempeletaten", "contactEmail": "kontakt@example.com"},
        )

    def test_missing_psdata_values_are_left_out(self) -> None:
        config = build_geoserver_postgis_config(
            [_feature("Veg", geometry={"type": "GM_Curve"})], workspace="veg"
        )
        wfs = config["service"]["wfs"]
        self.assertEqual(wfs["title"], "veg")
        for key in ("abstrct", "keywords", "onlineResource"):
            self.assertNotIn(key, wfs)
        self.assertEqual(wfs["accessConstraints"], "NONE")
        self.assertEqual(wfs["fees"], "NONE")
        self.assertEqual(config["settings"]["settings"]["contact"], {})
        self.assertNotIn("abstract", _layers(config)["veg"])
        bbox = _layers(config)["veg"]["latLonBoundingBox"]
        self.assertEqual(
            [bbox["minx"], bbox["miny"], bbox["maxx"], bbox["maxy"]], list(WORLD_BBOX)
        )

    def test_contact_falls_back_to_the_point_of_contact(self) -> None:
        psdata = _psdata()
        psdata["identificationSection"]["contact"] = psdata["identificationSection"]["contact"][1:]
        config = build_geoserver_postgis_config([_feature("Veg")], psdata=psdata)
        self.assertEqual(
            config["settings"]["settings"]["contact"]["contactOrganization"], "Kontaktkontoret"
        )


@unittest.skipIf(BASH is None, "bash is not installed")
class PostgisScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = render_geoserver_postgis_script(
            build_geoserver_postgis_config(
                _fixture(), psdata=_psdata(), workspace="bygning", dbname="bygning"
            )
        )

    def test_syntax(self) -> None:
        subprocess.run([BASH, "-n"], input=self.script, text=True, check=True)

    def test_required_variables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = _run_script(self.script, Path(directory), {})
        self.assertEqual(run.returncode, 1)
        self.assertIn("POSTGIS_HOST POSTGIS_USER POSTGIS_PASSWORD", run.stderr)
        self.assertEqual(run.calls, [])

    def test_creates_workspace_store_and_layers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = _run_script(self.script, Path(directory), _POSTGIS_ENV)
        self.assertEqual(run.returncode, 0, run.stderr)
        rest = "http://geoserver.example/geoserver/rest"
        self.assertEqual(
            [call for call in run.calls if call[0] != "GET"],
            [
                ("POST", f"{rest}/workspaces"),
                ("PUT", f"{rest}/workspaces/bygning/settings"),
                ("PUT", f"{rest}/services/wfs/workspaces/bygning/settings"),
                ("POST", f"{rest}/workspaces/bygning/datastores"),
                ("POST", f"{rest}/workspaces/bygning/datastores/postgis/featuretypes"),
                ("POST", f"{rest}/workspaces/bygning/datastores/postgis/featuretypes"),
            ],
        )
        self.assertEqual(
            json.loads(run.body("POST", "/workspaces")), {"workspace": {"name": "bygning"}}
        )
        layer = json.loads(run.bodies[-1])["featureType"]
        self.assertEqual(layer["name"], "eiendom")
        self.assertIn("ogc/features/v1", run.stdout)

    def test_connection_comes_from_the_environment(self) -> None:
        env = {**_POSTGIS_ENV, "POSTGIS_PASSWORD": 'p"w\\$x`y\'z', "POSTGIS_PORT": "6543"}
        with tempfile.TemporaryDirectory() as directory:
            run = _run_script(self.script, Path(directory), env)
        self.assertEqual(run.returncode, 0, run.stderr)
        store = json.loads(run.body("POST", "/datastores"))["dataStore"]
        parameters = {entry["@key"]: entry["$"] for entry in store["connectionParameters"]["entry"]}
        self.assertEqual(
            parameters,
            {
                "dbtype": "postgis",
                "host": "db",
                "port": "6543",
                "database": "bygning",
                "schema": "public",
                "user": "geoserver",
                "passwd": 'p"w\\$x`y\'z',
                "Expose primary keys": "true",
            },
        )

    def test_existing_resources_are_updated(self) -> None:
        env = {**_POSTGIS_ENV, "FAKE_GET_STATUS": "200", "GEOSERVER_WORKSPACE": "annet"}
        with tempfile.TemporaryDirectory() as directory:
            run = _run_script(self.script, Path(directory), env)
        self.assertEqual(run.returncode, 0, run.stderr)
        writes = [call for call in run.calls if call[0] != "GET"]
        self.assertNotIn("POST", {method for method, _ in writes})
        self.assertTrue(writes[-1][1].endswith("/workspaces/annet/datastores/postgis/featuretypes/eiendom"))

    def test_namespace_uri_is_optional(self) -> None:
        env = {**_POSTGIS_ENV, "GEOSERVER_NAMESPACE_URI": "https://example.com/bygning"}
        with tempfile.TemporaryDirectory() as directory:
            run = _run_script(self.script, Path(directory), env)
        self.assertEqual(
            json.loads(run.body("PUT", "/namespaces/bygning")),
            {"namespace": {"prefix": "bygning", "uri": "https://example.com/bygning"}},
        )

    def test_without_defaults_workspace_and_database_are_required(self) -> None:
        script = render_geoserver_postgis_script(build_geoserver_postgis_config([_feature("Veg")]))
        with tempfile.TemporaryDirectory() as directory:
            run = _run_script(script, Path(directory), _POSTGIS_ENV)
        self.assertEqual(run.returncode, 1)
        self.assertIn("GEOSERVER_WORKSPACE POSTGIS_DATABASE", run.stderr)

    def test_metadata_with_shell_characters_is_sent_unchanged(self) -> None:
        psdata = _psdata()
        title = "Data $HOME `id` \"sitert\" 'apostrof' (parentes)"
        psdata["identificationSection"]["title"] = title
        script = render_geoserver_postgis_script(
            build_geoserver_postgis_config([_feature("Veg")], psdata=psdata, workspace="veg", dbname="veg")
        )
        with tempfile.TemporaryDirectory() as directory:
            run = _run_script(script, Path(directory), _POSTGIS_ENV)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.body("PUT", "/wfs/workspaces/veg/settings"))["wfs"]["title"], title)

    def test_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_geoserver_postgis_config(
                _fixture(), Path(directory) / "x.geoserver-postgis.sh", workspace="x"
            )
            self.assertTrue(path.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash\n"))
            self.assertTrue(os.access(path, os.X_OK))


class GpkgConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = build_geoserver_gpkg_config(
            _fixture(), psdata=_psdata(), workspace="bygning", geopackage="bygning.gpkg"
        )

    def test_one_layer_per_feature_table(self) -> None:
        layers = _layers(self.config)
        self.assertEqual(list(layers), ["bygning", "eiendom"])
        # GeoPackage tables keep the feature type's name as it is.
        self.assertEqual(layers["bygning"]["nativeName"], "Bygning")

    def test_everything_but_the_store_matches_the_postgis_config(self) -> None:
        postgis = build_geoserver_postgis_config(_fixture(), psdata=_psdata(), workspace="bygning")
        self.assertEqual(self.config["service"], postgis["service"])
        self.assertEqual(self.config["settings"], postgis["settings"])
        for name, layer in _layers(self.config).items():
            other = dict(_layers(postgis)[name])
            mine = dict(layer)
            other.pop("nativeName")
            mine.pop("nativeName")
            self.assertEqual(mine, other)

    def test_tables_exist_in_the_written_geopackage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            gpkg = write_geopackage(_fixture(), Path(directory) / "x.gpkg")
            connection = sqlite3.connect(gpkg)
            try:
                geometry_tables = dict(
                    connection.execute("SELECT table_name, srs_id FROM gpkg_geometry_columns")
                )
            finally:
                connection.close()
        for layer in _layers(self.config).values():
            self.assertIn(layer["nativeName"], geometry_tables)
            self.assertEqual(layer["srs"], f"EPSG:{geometry_tables[layer['nativeName']]}")

    def test_multi_geometry_class_is_split_like_the_geopackage(self) -> None:
        config = build_geoserver_gpkg_config(
            [
                _feature(
                    "Bygning",
                    _attr("navn"),
                    _attr("område", "GM_Surface", "1"),
                    _attr("posisjon", "GM_Point", "1"),
                )
            ]
        )
        layers = _layers(config)
        self.assertEqual(list(layers), ["bygning-flate", "bygning-punkt"])
        self.assertEqual(
            [layer["nativeName"] for layer in layers.values()], ["Bygning_flate", "Bygning_punkt"]
        )

    def test_feature_type_without_geometry_gets_no_layer(self) -> None:
        config = build_geoserver_gpkg_config(
            [_feature("Register", _attr("navn")), _feature("Mast", _attr("posisjon", "GM_Point", "1"))]
        )
        self.assertEqual(list(_layers(config)), ["mast"])


@unittest.skipIf(BASH is None, "bash is not installed")
class GpkgScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = render_geoserver_gpkg_script(
            build_geoserver_gpkg_config(
                _fixture(), psdata=_psdata(), workspace="bygning", geopackage="bygning.gpkg"
            )
        )

    def test_syntax(self) -> None:
        subprocess.run([BASH, "-n"], input=self.script, text=True, check=True)

    def test_uploads_the_geopackage_next_to_the_script(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "bygning.gpkg").write_bytes(b"gpkg")
            run = _run_script(self.script, Path(directory), {})
        self.assertEqual(run.returncode, 0, run.stderr)
        rest = "http://geoserver.example/geoserver/rest"
        self.assertIn(("PUT", f"{rest}/resource/data/bygning/bygning.gpkg"), run.calls)
        self.assertEqual(run.body("PUT", "/resource/data/bygning/bygning.gpkg"), f"@{directory}/bygning.gpkg")
        store = json.loads(run.body("POST", "/datastores"))["dataStore"]
        parameters = {entry["@key"]: entry["$"] for entry in store["connectionParameters"]["entry"]}
        self.assertEqual(
            parameters,
            {
                "dbtype": "geopkg",
                "database": "file:data/bygning/bygning.gpkg",
                "Expose primary keys": "true",
            },
        )

    def test_file_on_the_server_is_not_uploaded(self) -> None:
        env = {"GEOPACKAGE_SERVER_PATH": "/srv/data/bygning.gpkg"}
        with tempfile.TemporaryDirectory() as directory:
            run = _run_script(self.script, Path(directory), env)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse(any("/resource/" in url for _, url in run.calls))
        store = json.loads(run.body("POST", "/datastores"))["dataStore"]
        self.assertIn(
            {"@key": "database", "$": "/srv/data/bygning.gpkg"},
            store["connectionParameters"]["entry"],
        )

    def test_missing_geopackage_stops_before_the_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = _run_script(self.script, Path(directory), {})
        self.assertEqual(run.returncode, 1)
        self.assertIn("GEOPACKAGE_FILE", run.stderr)
        self.assertFalse(any("/datastores" in url for _, url in run.calls))

    def test_without_a_file_name_one_must_be_given(self) -> None:
        script = render_geoserver_gpkg_script(build_geoserver_gpkg_config(_fixture(), workspace="x"))
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "data.gpkg").write_bytes(b"gpkg")
            run = _run_script(script, Path(directory), {})
            self.assertEqual(run.returncode, 1)
            run = _run_script(
                script, Path(directory), {"GEOPACKAGE_FILE": str(Path(directory) / "data.gpkg")}
            )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(any(url.endswith("/resource/data/x/data.gpkg") for _, url in run.calls))

    def test_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_geoserver_gpkg_config(
                _fixture(), Path(directory) / "x.geoserver-gpkg.sh", workspace="x", geopackage="x.gpkg"
            )
            self.assertIn('GEOPACKAGE_FILE="$(dirname "$0")/x.gpkg"', path.read_text(encoding="utf-8"))
            self.assertTrue(os.access(path, os.X_OK))


class GenerateProductSpecTests(unittest.TestCase):
    def test_flags_write_scripts_next_to_the_other_artefacts(self) -> None:
        import scripts.generate_product_spec as product_spec

        args = product_spec._parse_args(
            ["uuid", "--geoserver-postgis-output", "--geoserver-gpkg-output"]
        )
        self.assertTrue(args.geoserver_postgis_output)
        self.assertTrue(args.geoserver_gpkg_output)
        with tempfile.TemporaryDirectory() as directory:
            assets = product_spec._build_feature_catalogue_assets(
                _fixture(),
                slug="dyrkbar-jord",
                spec_dir=Path(directory),
                prefix="xmi",
                write_geoserver_postgis=True,
                write_geoserver_gpkg=True,
                postgis_schema="data",
                psdata=_psdata(),
            )
            self.assertEqual(
                assets["geoserver_postgis_path"],
                Path(directory) / "dyrkbar-jord_xmi.geoserver-postgis.sh",
            )
            postgis = assets["geoserver_postgis_path"].read_text(encoding="utf-8")
            self.assertIn('GEOSERVER_WORKSPACE="${GEOSERVER_WORKSPACE:-dyrkbar-jord-xmi}"', postgis)
            self.assertIn('POSTGIS_DATABASE="${POSTGIS_DATABASE:-dyrkbar_jord}"', postgis)
            self.assertIn('POSTGIS_SCHEMA="${POSTGIS_SCHEMA:-data}"', postgis)
            gpkg = assets["geoserver_gpkg_path"].read_text(encoding="utf-8")
            # The same name the GeoPackage writer gives the .gpkg next to it.
            self.assertIn('GEOPACKAGE_FILE="$(dirname "$0")/dyrkbar-jord_xmi.gpkg"', gpkg)
            self.assertIsNone(assets["pygeoapi_gpkg_path"])


if __name__ == "__main__":
    unittest.main()
