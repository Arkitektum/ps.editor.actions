"""Shared parts of the GeoServer setup script emitters.

:mod:`geoserver_config.postgis` publishes the data model's PostGIS tables from a
``postgis`` data store, :mod:`geoserver_config.gpkg` its GeoPackage tables from a
``geopkg`` one. Everything but the data store is the same and lives here.

GeoServer is not configured from one file: a workspace, its settings, a data
store and one feature type per table are created through the REST API. The
output is therefore a bash script that makes those calls with ``curl``, and
that can be run again -- whatever exists already is updated.

GeoServer serves OGC API - Features per workspace
(``/geoserver/<workspace>/ogc/features/v1``) and takes the service metadata
there from the workspace's WFS settings and its contact from the workspace
settings. The psdata values (:mod:`service_metadata`) go to:

* tittel, sammendrag, nøkkelord     -> WFS ``title``, ``abstrct``, ``keywords``
* tilgangsrestriksjoner             -> WFS ``accessConstraints``
* lisens                            -> WFS ``fees``
* metadatalenke                     -> WFS ``onlineResource``
* eier / kontaktpunkt               -> ``contactOrganization`` / ``contactEmail``
* geografisk utstrekning            -> every feature type's ``latLonBoundingBox``

A missing psdata value is left out rather than published as a placeholder:
unlike a pygeoapi file the script changes a running server as soon as it is run.
Values only the operator knows -- the GeoServer address and login, the database
connection -- are read from environment variables when the script runs.
"""
from __future__ import annotations

import json
import re
from typing import Any

from service_metadata import (
    OFFERED_EPSG_CODES,
    contact,
    dataset_bbox,
    feature_keywords,
    keywords,
    resource_name,
    string,
    text,
)

# Used when psdata has no usable extent: GeoServer needs a bounding box, and one
# computed from a freshly created, empty table is meaningless.
WORLD_BBOX: tuple[float, float, float, float] = (-180.0, -90.0, 180.0, 90.0)

# WFS settings GeoServer needs besides the metadata: without ``serviceLevel``
# a workspace's GetCapabilities fails. The values are GeoServer's own defaults.
_WFS_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "name": "WFS",
    "versions": {
        "org.geotools.util.Version": [
            {"version": "1.0.0"},
            {"version": "1.1.0"},
            {"version": "2.0.0"},
        ]
    },
    "citeCompliant": False,
    "schemaBaseURL": "http://schemas.opengis.net",
    "verbose": False,
    "gml": {
        "entry": [
            {"version": "V_10", "gml": {"srsNameStyle": "XML", "overrideGMLAttributes": True}},
            {"version": "V_20", "gml": {"srsNameStyle": "URN2", "overrideGMLAttributes": False}},
            {"version": "V_11", "gml": {"srsNameStyle": "URN", "overrideGMLAttributes": False}},
        ]
    },
    "serviceLevel": "COMPLETE",
    "maxFeatures": 1000000,
    "featureBounding": False,
    "canonicalSchemaLocation": False,
    "encodeFeatureMember": False,
    "hitsIgnoreMaxFeatures": False,
}


def workspace_name(name: str) -> str:
    """Workspace for ``name``: kebab-case like the layers, never starting with a
    digit -- the workspace name is also the namespace prefix in GML output."""
    workspace = resource_name(name)
    return f"ps-{workspace}" if workspace[:1].isdigit() else workspace


def dataset_extent(psdata: Any) -> list[float]:
    """The dataset's extent from psdata, or the whole world when it has none."""
    bbox = dataset_bbox(psdata)
    if all(isinstance(value, float) for value in bbox):
        return [float(value) for value in bbox]
    return list(WORLD_BBOX)


def _response_srs(srid: int) -> list[str]:
    codes = list(OFFERED_EPSG_CODES)
    if srid not in codes:
        codes.append(srid)
    return [str(code) for code in codes]


# --------------------------------------------------------------------------- #
# psdata -> workspace settings
# --------------------------------------------------------------------------- #


def _service_settings(psdata: Any, workspace: str) -> dict[str, Any]:
    """The workspace's WFS settings, which OGC API - Features shares."""
    wfs: dict[str, Any] = {
        "title": string(psdata, "identificationSection.title", missing=workspace),
    }
    abstract = string(psdata, "identificationSection.abstract", missing="")
    if abstract:
        wfs["abstrct"] = abstract  # sic: GeoServer's field name
    words = keywords(psdata)
    if words:
        wfs["keywords"] = {"string": words}
    wfs["accessConstraints"] = string(
        psdata,
        "identificationSection.restriction.legalConstraints.accessConstraints",
        missing="NONE",
    )
    license_name = string(
        psdata, "identificationSection.restriction.legalConstraints.license", missing=""
    )
    license_url = string(
        psdata, "identificationSection.restriction.legalConstraints.licenseUrl", missing=""
    )
    if license_name and license_url:
        wfs["fees"] = f"{license_name} ({license_url})"
    else:
        wfs["fees"] = license_name or license_url or "NONE"
    metadata_link = string(psdata, "metadataSection.metadataIdentifier.metadataLinkage", missing="")
    if metadata_link:
        wfs["onlineResource"] = metadata_link
    return {"wfs": {**wfs, **_WFS_DEFAULTS}}


def _workspace_settings(psdata: Any) -> dict[str, Any]:
    """The workspace's own settings: the contact shown in the capabilities."""
    owner = contact(psdata, "owner")
    point_of_contact = contact(psdata, "pointOfContact")
    details = {
        "contactOrganization": text(owner.get("organizationName"))
        or text(point_of_contact.get("organizationName")),
        "contactEmail": text(point_of_contact.get("electronicMailAddress")),
    }
    return {
        "settings": {
            "contact": {key: value for key, value in details.items() if value},
            "charset": "UTF-8",
            "numDecimals": 8,
            "verbose": False,
            "verboseExceptions": False,
            "localWorkspaceIncludesPrefix": False,
        }
    }


# --------------------------------------------------------------------------- #
# Feature types -> layers
# --------------------------------------------------------------------------- #


def feature_type(
    ft: dict[str, Any],
    *,
    name: str,
    native_name: str,
    srid: int,
    bbox: list[float],
) -> dict[str, Any]:
    """The REST body publishing table ``native_name`` as layer ``name``.

    Only the lat/lon box is given: GeoServer derives the native one from it.
    ``overridingServiceSRS`` makes the layer's own CRS list the one OGC API -
    Features offers, after CRS84.
    """
    title = text(ft.get("title")) or text(ft.get("name")) or native_name
    body: dict[str, Any] = {"name": name, "nativeName": native_name, "title": title}
    description = text(ft.get("description"))
    if description:
        body["abstract"] = description
    west, south, east, north = bbox
    body.update(
        {
            "keywords": {"string": feature_keywords(ft, title)},
            "srs": f"EPSG:{srid}",
            "projectionPolicy": "FORCE_DECLARED",
            "latLonBoundingBox": {
                "minx": west,
                "miny": south,
                "maxx": east,
                "maxy": north,
                "crs": "EPSG:4326",
            },
            "responseSRS": {"string": _response_srs(srid)},
            "overridingServiceSRS": True,
            "enabled": True,
        }
    )
    return {"featureType": body}


def document(
    psdata: Any,
    *,
    workspace: str | None,
    store: dict[str, Any],
    layers: list[dict[str, Any]],
) -> dict[str, Any]:
    """Everything the script sets up. ``workspace`` is ``None`` when the user
    has to name it (``GEOSERVER_WORKSPACE``)."""
    return {
        "workspace": workspace,
        "settings": _workspace_settings(psdata),
        "service": _service_settings(psdata, workspace or ""),
        "store": store,
        "layers": layers,
    }


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

_HEADER = """\
#!/usr/bin/env bash
# GeoServer-oppsett — generert fra datamodellen og metadata i Geonorge.
# {purpose}
# gjennom GeoServers REST-API, som OGC API - Features under
# <GEOSERVER_URL>/<arbeidsområde>/ogc/features/v1.
# Skriptet kan kjøres flere ganger: det som finnes fra før, blir oppdatert.
#
# Miljøvariabler (de uten standardverdi må settes):
{variables}
set -euo pipefail
"""

_FUNCTIONS = r"""
REST="${GEOSERVER_URL%/}/rest"
WS="$GEOSERVER_WORKSPACE"
response="$(mktemp)"
trap 'rm -f "$response"' EXIT

# request METHOD PATH [CURL-ARGS...]: one REST call; stops with GeoServer's answer on an error.
request() {
  local method=$1 path=$2 status
  shift 2
  status=$(curl -sS -o "$response" -w '%{http_code}' \
    -u "$GEOSERVER_USER:$GEOSERVER_PASSWORD" -X "$method" "$@" "$REST$path")
  case $status in
    2??) ;;
    *)
      echo "$method $path: HTTP $status" >&2
      cat "$response" >&2
      echo >&2
      exit 1
      ;;
  esac
}

# exists PATH: whether GeoServer has the resource at PATH.
exists() {
  [ "$(curl -sS -o /dev/null -w '%{http_code}' \
    -u "$GEOSERVER_USER:$GEOSERVER_PASSWORD" "$REST$1.json")" = 200 ]
}

# send_json METHOD PATH JSON
send_json() {
  request "$1" "$2" -H 'Content-Type: application/json' --data-binary "$3"
}

# upsert COLLECTION NAME JSON: updates COLLECTION/NAME if it exists, else creates it.
upsert() {
  if exists "$1/$2"; then
    send_json PUT "$1/$2" "$3"
  else
    send_json POST "$1" "$3"
  fi
}

# json_string VALUE: VALUE as a JSON string.
json_string() {
  local value=$1
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  value=${value//$'\n'/\\n}
  value=${value//$'\r'/\\r}
  value=${value//$'\t'/\\t}
  printf '"%s"' "$value"
}

# parameter KEY VALUE: one data store connection parameter.
parameter() {
  printf '{"@key":%s,"$":%s}' "$(json_string "$1")" "$(json_string "$2")"
}
"""

_WORKSPACE = """
echo "Arbeidsområde $WS"
if ! exists "/workspaces/$WS"; then
  send_json POST /workspaces "{\\"workspace\\":{\\"name\\":$(json_string "$WS")}}"
fi
if [ -n "${GEOSERVER_NAMESPACE_URI:-}" ]; then
  send_json PUT "/namespaces/$WS" \\
    "{\\"namespace\\":{\\"prefix\\":$(json_string "$WS"),\\"uri\\":$(json_string "$GEOSERVER_NAMESPACE_URI")}}"
fi
send_json PUT "/workspaces/$WS/settings" "$(cat <<'JSON'
{settings}
JSON
)"
send_json PUT "/services/wfs/workspaces/$WS/settings" "$(cat <<'JSON'
{service}
JSON
)"
"""

_LAYER = """
echo "Lag {name}"
upsert "/workspaces/$WS/datastores/$STORE/featuretypes" {name} "$(cat <<'JSON'
{body}
JSON
)"
"""

_FOOTER = """
echo "Ferdig: ${GEOSERVER_URL%/}/$WS/ogc/features/v1"
"""

def double_quoted(value: str) -> str:
    """``value`` escaped for a double-quoted bash string."""
    return re.sub(r'([\\"$`])', r"\\\1", value)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def render_geoserver_script(
    config: dict[str, Any],
    *,
    purpose: str,
    variables: list[tuple[str, str | None, str]],
    store_section: str,
) -> str:
    """The setup script for ``config``.

    ``variables`` are ``(name, default, description)`` for the backend's own
    environment variables; a ``None`` default makes the variable required.
    ``store_section`` is the bash that creates the data store ``$STORE``.
    """
    workspace = config.get("workspace")
    common: list[tuple[str, str | None, str]] = [
        ("GEOSERVER_URL", "http://localhost:8080/geoserver", "GeoServer-adressen"),
        ("GEOSERVER_USER", None, "administrator i GeoServer"),
        ("GEOSERVER_PASSWORD", None, "administratorens passord"),
        ("GEOSERVER_WORKSPACE", workspace or None, "arbeidsområdet lagene publiseres i"),
        ("GEOSERVER_NAMESPACE_URI", "", "navnerom-URI for arbeidsområdet (valgfri)"),
    ]
    everything = common + variables
    width = max(len(name) for name, _, _ in everything)

    lines = []
    for name, default, description in everything:
        note = f" (standard: {default})" if default else ""
        lines.append(f"#   {name.ljust(width)}  {description}{note}")

    script = [_HEADER.format(purpose=purpose, variables="\n".join(lines)), ""]
    for name, default, _ in everything:
        if default is not None:
            script.append(f'{name}="${{{name}:-{double_quoted(default)}}}"')
    required = [name for name, default, _ in everything if default is None]
    script.append(
        f"""
missing=()
for name in {" ".join(required)}; do
  [ -n "${{!name:-}}" ] || missing+=("$name")
done
if [ "${{#missing[@]}}" -gt 0 ]; then
  echo "Mangler miljøvariabler: ${{missing[*]}}" >&2
  exit 1
fi"""
    )
    script.append(_FUNCTIONS)
    script.append(
        _WORKSPACE.replace("{settings}", _json(config["settings"])).replace(
            "{service}", _json(config["service"])
        )
    )
    script.append(store_section)
    for layer in config["layers"]:
        # Layer names are resource_name()s: lowercase ASCII, digits and "-".
        name = layer["featureType"]["name"]
        script.append(_LAYER.replace("{name}", name).replace("{body}", _json(layer)))
    script.append(_FOOTER)
    return "\n".join(part.rstrip("\n") for part in script) + "\n"
