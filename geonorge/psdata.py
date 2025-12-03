"""Convert Geonorge dataset metadata into psdata-style JSON structures."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

try:  # pragma: no cover - optional dependency when running tests
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore[assignment]

API_URL = "https://kartkatalog.geonorge.no/api/getdata/{metadata_id}"
HTTPGet = Callable[[str], Any]

KEYWORD_FIELDS: tuple[str, ...] = (
    "KeywordsTheme",
    "KeywordsPlace",
    "KeywordsInspire",
    "KeywordsInspirePriorityDataset",
    "KeywordsHighValueDataset",
    "KeywordsNationalInitiative",
    "KeywordsNationalTheme",
    "KeywordsOther",
    "KeywordsConcept",
    "KeywordsAdministrativeUnits",
)

CONTACT_FIELDS: tuple[str, ...] = (
    "ContactOwner",
    "ContactMetadata",
    "ContactPublisher",
    "ContactDistributor",
)

DISTRIBUTION_GROUPS: tuple[str, ...] = (
    "SelfDistribution",
    "RelatedDataset",
    "RelatedSerieDatasets",
    "RelatedDatasetSerie",
    "RelatedApplications",
    "RelatedServices",
    "RelatedServiceLayer",
    "RelatedViewServices",
    "RelatedDownloadServices",
)

__all__ = [
    "fetch_metadata",
    "build_psdata",
    "fetch_psdata",
    "main",
]


def _default_http_get(url: str) -> Any:
    """Fetch ``url`` using :mod:`requests` with a sensible timeout."""

    if requests is None:  # pragma: no cover - defensive, requests should be available
        raise RuntimeError("The 'requests' library is required to fetch data.")
    return requests.get(url, timeout=30)


def fetch_metadata(metadata_id: str, http_get: HTTPGet | None = None) -> Mapping[str, Any]:
    """Fetch raw metadata for ``metadata_id`` from Geonorge."""

    getter = http_get or _default_http_get
    url = API_URL.format(metadata_id=metadata_id)

    try:
        response = getter(url)
    except Exception as exc:  # pragma: no cover - simple network error conversion
        raise RuntimeError(f"Failed to fetch metadata for '{metadata_id}'.") from exc

    status_code = getattr(response, "status_code", None)
    if status_code is not None and int(status_code) >= 400:
        raise RuntimeError(
            f"Request for metadata '{metadata_id}' failed with status code {status_code}."
        )

    if hasattr(response, "raise_for_status"):
        try:
            response.raise_for_status()
        except Exception as exc:  # pragma: no cover - handled above in most cases
            raise RuntimeError(
                f"Request for metadata '{metadata_id}' failed: {exc}."
            ) from exc

    try:
        payload = response.json()
    except Exception as exc:  # pragma: no cover - invalid JSON
        raise ValueError("Metadata response did not contain valid JSON.") from exc

    if isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], Mapping):
        payload = payload[0]

    if not isinstance(payload, Mapping):
        snippet = ""
        try:
            text = getattr(response, "text", "")
            if text:
                snippet = f" Response snippet: {text[:300]!r}"
        except Exception:
            snippet = ""
        raise ValueError(f"Metadata response must be a JSON object; got {type(payload).__name__}.{snippet}")

    return payload


def fetch_psdata(metadata_id: str, *, http_get: HTTPGet | None = None) -> dict[str, Any]:
    """Fetch Geonorge metadata and convert it to the psdata-like structure."""

    metadata = fetch_metadata(metadata_id, http_get=http_get)
    return build_psdata(metadata_id, metadata)


def build_psdata(metadata_id: str, metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Convert Geonorge metadata ``metadata`` into a psdata-like mapping."""

    spatial_reference_systems, primary_crs = _extract_reference_systems(metadata)
    spatial_extent = _extract_spatial_extent(metadata, default_crs=primary_crs)

    result = _compact_mapping(
        {
            "identification": _build_identification(metadata_id, metadata),
            "scope": _compact_mapping(
                {
                    "level": _normalize_string(
                        metadata.get("HierarchyLevel") or metadata.get("Type")
                    ),
                    "extent": _compact_mapping(
                        {
                            "spatial": spatial_extent,
                            "temporal": _build_temporal_extent(metadata),
                        }
                    ),
                    "legalConstraints": _extract_legal_constraints(metadata),
                }
            ),
            "dataContent": _build_data_content(metadata),
            "referenceSystems": _compact_mapping(
                {
                    "spatialReferenceSystems": spatial_reference_systems,
                    "spatialRepresentationType": _normalize_string(
                        metadata.get("SpatialRepresentation")
                    ),
                }
            ),
            "dataQuality": _extract_quality(metadata),
            "maintenance": _compact_mapping(
                {
                    "maintenanceFrequency": _normalize_string(
                        metadata.get("MaintenanceFrequency")
                    ),
                    "maintenanceNote": _normalize_string(metadata.get("SpecificUsage")),
                    "status": _normalize_string(metadata.get("Status")),
                }
            ),
            "portrayal": _extract_portrayal(metadata),
            "delivery": _compact_mapping(
                {
                    "distributions": _extract_distributions(metadata),
                }
            ),
            "metadata": _build_metadata_section(metadata_id, metadata),
            "links": _collect_links(metadata),
        }
    )

    return result


def _build_identification(metadata_id: str, metadata: Mapping[str, Any]) -> dict[str, Any]:
    keywords = _collect_keywords(metadata)
    topic_categories = _collect_topic_categories(metadata)

    dates = _compact_mapping(
        {
            "creation": _parse_date(metadata.get("DatePublished")),
            "publication": _parse_date(metadata.get("DatePublished")),
            "revision": _parse_date(metadata.get("DateUpdated")),
            "metadata": _parse_date(metadata.get("DateMetadataUpdated")),
        }
    )

    identification = _compact_mapping(
        {
            "id": metadata.get("Uuid") or metadata_id,
            "title": _select_first_string(
                metadata.get("NorwegianTitle"), metadata.get("EnglishTitle"), metadata.get("Title")
            ),
            "abstract": _normalize_string(metadata.get("Abstract")),
            "purpose": _normalize_string(metadata.get("Purpose")),
            "language": _normalize_string(metadata.get("DatasetLanguage")),
            "keywords": keywords,
            "topicCategories": topic_categories,
            "dates": dates,
            "responsibleParties": _collect_contacts(metadata),
            "organizationLogoUrl": _normalize_string(metadata.get("OrganizationLogoUrl")),
        }
    )

    supplemental = _normalize_string(metadata.get("SupplementalDescription"))
    if supplemental:
        identification.setdefault("supplementalInformation", supplemental)

    return identification


def _build_temporal_extent(metadata: Mapping[str, Any]) -> Mapping[str, Any] | None:
    temporal_start = _parse_date(metadata.get("DatePublished"))
    temporal_end = _parse_date(metadata.get("DateUpdated"))

    if not temporal_start and not temporal_end:
        return None

    interval = [
        temporal_start or temporal_end,
        temporal_end or temporal_start,
    ]

    if interval[0] == interval[1]:
        interval = [interval[0], interval[1]] if interval[0] else []

    return _compact_mapping({"interval": [interval] if interval else None})


def _build_data_content(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    feature_catalogue = None
    if isinstance(metadata.get("OperatesOn"), Sequence) and not isinstance(
        metadata.get("OperatesOn"), (str, bytes)
    ):
        feature_catalogue = [
            _compact_mapping({
                "title": _normalize_string(item.get("Title")),
                "identifier": _normalize_string(item.get("Uuid")),
            })
            for item in metadata["OperatesOn"]
            if isinstance(item, Mapping)
        ]
        feature_catalogue = [item for item in feature_catalogue if item]

    if not feature_catalogue and metadata.get("OperatesOn") and isinstance(
        metadata.get("OperatesOn"), Mapping
    ):
        item = metadata["OperatesOn"]
        feature_catalogue = [
            _compact_mapping(
                {
                    "title": _normalize_string(item.get("Title")),
                    "identifier": _normalize_string(item.get("Uuid")),
                }
            )
        ]
        feature_catalogue = [entry for entry in feature_catalogue if entry]

    specific_usage = _normalize_string(metadata.get("SpecificUsage"))

    if not feature_catalogue and not specific_usage:
        return None

    return _compact_mapping(
        {
            "featureCatalogue": feature_catalogue,
            "usage": specific_usage,
        }
    )


def _build_metadata_section(metadata_id: str, metadata: Mapping[str, Any]) -> dict[str, Any]:
    point_of_contact = None
    metadata_contact = metadata.get("ContactMetadata")
    if isinstance(metadata_contact, Mapping):
        point_of_contact = _compact_mapping(
            {
                "organization": _normalize_string(
                    metadata_contact.get("Organization")
                    or metadata_contact.get("OrganizationEnglish")
                ),
                "email": _normalize_string(metadata_contact.get("Email")),
                "role": _normalize_string(metadata_contact.get("Role")),
            }
        )

    identifiers = [
        _compact_mapping({
            "authority": "geonorge",
            "code": metadata.get("Uuid") or metadata_id,
        })
    ]

    metadata_section = _compact_mapping(
        {
            "standard": _normalize_string(metadata.get("MetadataStandard")),
            "standardVersion": _normalize_string(metadata.get("MetadataStandardVersion")),
            "metadataDate": _parse_date(metadata.get("DateMetadataUpdated")),
            "language": _normalize_string(metadata.get("MetadataLanguage")),
            "pointOfContact": point_of_contact,
            "identifiers": [identifier for identifier in identifiers if identifier],
        }
    )

    metadata_xml = _normalize_string(metadata.get("MetadataXmlUrl"))
    if metadata_xml:
        metadata_section.setdefault("metadataUrl", metadata_xml)

    landing_page = _normalize_string(
        metadata.get("LandingPage")
        or metadata.get("LandingPageUrl")
        or metadata.get("Landingpage")
    )
    if landing_page:
        metadata_section.setdefault("landingPage", landing_page)

    return metadata_section


def _collect_keywords(metadata: Mapping[str, Any]) -> list[str]:
    keywords: list[str] = []
    seen: set[str] = set()

    for field in KEYWORD_FIELDS:
        value = metadata.get(field)
        for keyword in _iter_keyword_values(value):
            lowered = keyword.casefold()
            if lowered not in seen:
                seen.add(lowered)
                keywords.append(keyword)

    return keywords


def _iter_keyword_values(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        extracted = _select_first_string(
            value.get("KeywordValue"),
            value.get("EnglishKeyword"),
            value.get("Keyword"),
            value.get("Title"),
            value.get("Name"),
            value.get("Value"),
        )
        if extracted:
            yield extracted
        return

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            yield from _iter_keyword_values(item)
        return

    if isinstance(value, str):
        for part in value.replace(";", ",").split(","):
            part = part.strip()
            if part:
                yield part
        return

    if value:
        text = str(value).strip()
        if text:
            yield text


def _collect_topic_categories(metadata: Mapping[str, Any]) -> list[str]:
    categories: list[str] = []
    seen: set[str] = set()

    for field in ("TopicCategories", "TopicCategory"):
        value = metadata.get(field)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for item in value:
                text = _normalize_string(item)
                if text and text.casefold() not in seen:
                    seen.add(text.casefold())
                    categories.append(text)
        else:
            text = _normalize_string(value)
            if text and text.casefold() not in seen:
                seen.add(text.casefold())
                categories.append(text)

    return categories


def _collect_contacts(metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
    contacts: list[dict[str, Any]] = []

    for field in CONTACT_FIELDS:
        value = metadata.get(field)
        if not isinstance(value, Mapping):
            continue

        entry = _compact_mapping(
            {
                "name": _normalize_string(value.get("Name")),
                "organization": _select_first_string(
                    value.get("Organization"), value.get("OrganizationEnglish")
                ),
                "email": _normalize_string(value.get("Email")),
                "role": _normalize_string(value.get("Role")),
            }
        )
        if entry:
            contacts.append(entry)

    return contacts


def _extract_spatial_extent(
    metadata: Mapping[str, Any], *, default_crs: str | None
) -> dict[str, Any] | None:
    extent: dict[str, Any] = {}

    scope_description = _normalize_string(metadata.get("SpatialScope"))
    if scope_description:
        extent["spatialScope"] = scope_description

    bbox = metadata.get("BoundingBox")
    if isinstance(bbox, Mapping):
        try:
            west = float(bbox.get("WestBoundLongitude"))
            south = float(bbox.get("SouthBoundLatitude"))
            east = float(bbox.get("EastBoundLongitude"))
            north = float(bbox.get("NorthBoundLatitude"))
        except (TypeError, ValueError):  # pragma: no cover - invalid bounding box
            west = south = east = north = None
        else:
            extent["bbox"] = [west, south, east, north]
            bounding_box: dict[str, Any] = {
                "west": west,
                "south": south,
                "east": east,
                "north": north,
            }

            crs = default_crs
            if not crs:
                reference_system = metadata.get("ReferenceSystem")
                if isinstance(reference_system, Mapping):
                    crs = _extract_epsg_code(reference_system.get("CoordinateSystemUrl")) or _normalize_string(
                        reference_system.get("CoordinateSystem")
                    )
                else:
                    crs = _normalize_string(reference_system)

            if isinstance(crs, str) and crs:
                bounding_box["crs"] = crs

            extent["boundingBox"] = bounding_box

    return extent or None


def _extract_portrayal(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    portrayal = _compact_mapping(
        {
            "styleReferences": _normalize_sequence(metadata.get("StyleReferences")),
            "defaultPortrayalNote": _normalize_string(metadata.get("DefaultPortrayal")),
            "legendDescriptionUrl": _normalize_string(metadata.get("LegendDescriptionUrl")),
        }
    )
    return portrayal or None


def _extract_reference_systems(
    metadata: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], str | None]:
    systems: list[dict[str, Any]] = []
    primary_code: str | None = None

    candidates = []
    reference_systems = metadata.get("ReferenceSystems")
    if isinstance(reference_systems, Sequence) and not isinstance(
        reference_systems, (str, bytes)
    ):
        candidates.extend(reference_systems)

    reference_system = metadata.get("ReferenceSystem")
    if isinstance(reference_system, Mapping):
        candidates.append(reference_system)

    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        code = _extract_epsg_code(candidate.get("CoordinateSystemUrl"))
        name = _normalize_string(candidate.get("CoordinateSystem"))
        entry = _compact_mapping({"code": code, "name": name})
        if entry:
            systems.append(entry)
            if primary_code is None and code:
                primary_code = code

    return systems, primary_code


def _extract_epsg_code(url: Any) -> str | None:
    text = _normalize_string(url)
    if not text:
        return None

    parts = text.rstrip("/").split("/")
    if not parts:
        return None

    candidate = parts[-1]
    if candidate.isdigit():
        return f"EPSG:{candidate}"

    return text


def _extract_legal_constraints(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    constraints = metadata.get("Constraints")
    if not isinstance(constraints, Mapping):
        return None

    return _compact_mapping(
        {
            "useLimitation": _normalize_string(constraints.get("UseLimitations")),
            "accessConstraints": _normalize_string(constraints.get("AccessConstraints")),
            "useConstraints": _normalize_string(constraints.get("UseConstraints")),
            "license": _select_first_string(
                constraints.get("OtherConstraintsLinkText"),
                constraints.get("OtherConstraintsAccess"),
            ),
            "licenseUrl": _normalize_string(constraints.get("OtherConstraintsLink")),
            "securityConstraints": _normalize_string(
                constraints.get("SecurityConstraints")
            ),
        }
    )


def _extract_quality(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    elements: list[dict[str, Any]] = []

    quality_specs = metadata.get("QualitySpecifications")
    if isinstance(quality_specs, Sequence) and not isinstance(quality_specs, (str, bytes)):
        for spec in quality_specs:
            if not isinstance(spec, Mapping):
                continue
            entry = _compact_mapping(
                {
                    "name": _normalize_string(spec.get("Title")),
                    "measure": _normalize_string(spec.get("Explanation")),
                    "result": _normalize_string(spec.get("QuantitativeResult")),
                }
            )
            if entry:
                elements.append(entry)

    quantitative = metadata.get("QuantitativeResult")
    if isinstance(quantitative, Mapping):
        for key, value in quantitative.items():
            entry = _compact_mapping(
                {
                    "name": _normalize_string(key),
                    "result": _normalize_string(value),
                }
            )
            if entry:
                elements.append(entry)

    lineage_statement = _normalize_string(metadata.get("SupplementalDescription"))

    result = _compact_mapping(
        {
            "scope": _compact_mapping(
                {
                    "level": _normalize_string(
                        metadata.get("HierarchyLevel") or metadata.get("Type")
                    )
                }
            ),
            "qualityElements": elements if elements else None,
            "lineage": _compact_mapping({"statement": lineage_statement}),
        }
    )

    return result if result else None


def _extract_distributions(metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
    distributions: list[dict[str, Any]] = []

    protocol = _normalize_string(metadata.get("DistributionProtocol"))
    distribution_url = _normalize_string(metadata.get("DistributionUrl"))
    download_url = _normalize_string(metadata.get("DownloadUrl"))

    if protocol or distribution_url or download_url:
        distributions.append(
            _compact_mapping(
                {
                    "format": _compact_mapping({"format": protocol}),
                    "access": _compact_mapping(
                        {
                            "href": distribution_url or download_url,
                            "protocol": protocol,
                        }
                    ),
                }
            )
        )

    details = metadata.get("DistributionDetails")
    if isinstance(details, Mapping):
        distributions.append(
            _compact_mapping(
                {
                    "title": _normalize_string(details.get("ProtocolName")),
                    "format": _compact_mapping(
                        {"format": _normalize_string(details.get("ProtocolName"))}
                    ),
                    "access": _compact_mapping(
                        {
                            "href": _normalize_string(details.get("URL")),
                            "protocol": _normalize_string(details.get("Protocol")),
                        }
                    ),
                }
            )
        )

    nested = metadata.get("Distributions")
    if isinstance(nested, Mapping):
        for group in DISTRIBUTION_GROUPS:
            items = nested.get(group)
            if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
                continue
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                format_name = _extract_distribution_format(item.get("DistributionFormats"))
                access_href = _normalize_string(
                    item.get("DistributionUrl") or item.get("MapUrl")
                )
                entry = _compact_mapping(
                    {
                        "title": _normalize_string(item.get("Title")),
                        "format": _compact_mapping({"format": format_name}),
                        "access": _compact_mapping(
                            {
                                "href": access_href,
                                "protocol": _normalize_string(item.get("Protocol")),
                                "license": _normalize_string(item.get("DataAccess")),
                            }
                        ),
                        "notes": _normalize_string(item.get("TypeTranslated")),
                    }
                )
                if entry:
                    distributions.append(entry)

    return [entry for entry in distributions if entry]


def _extract_distribution_format(value: Any) -> str | None:
    if isinstance(value, Mapping):
        return _select_first_string(value.get("Name"), value.get("Format"))

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            extracted = _extract_distribution_format(item)
            if extracted:
                return extracted

    return _normalize_string(value)


def _collect_links(metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_link(href: Any, *, rel: str | None, link_type: str | None, title: str | None) -> None:
        url = _normalize_string(href)
        if not url:
            return
        if url in seen:
            return
        seen.add(url)
        link = _compact_mapping({
            "href": url,
            "rel": rel,
            "type": link_type,
            "title": title,
        })
        if link:
            links.append(link)

    add_link(metadata.get("MetadataXmlUrl"), rel="describedby", link_type="application/xml", title="Metadata (ISO 19139)")
    add_link(metadata.get("ProductPageUrl"), rel="about", link_type="text/html", title="Produktside")
    add_link(metadata.get("DownloadUrl"), rel="enclosure", link_type="text/html", title="Nedlasting")
    add_link(metadata.get("DistributionUrl"), rel="enclosure", link_type="text/html", title="Distribusjon")
    add_link(metadata.get("MapLink"), rel="alternate", link_type="text/html", title="Kartvisning")
    add_link(metadata.get("ServiceLink"), rel="service", link_type="text/html", title="Tjeneste")
    add_link(
        metadata.get("ServiceDistributionUrlForDataset"),
        rel="service",
        link_type="application/xml",
        title="Tjeneste-distribusjon",
    )

    details = metadata.get("DistributionDetails")
    if isinstance(details, Mapping):
        add_link(
            details.get("URL"),
            rel="enclosure",
            link_type="text/html",
            title=_normalize_string(details.get("ProtocolName")) or "Distribusjon",
        )

    nested = metadata.get("Distributions")
    if isinstance(nested, Mapping):
        for group in DISTRIBUTION_GROUPS:
            items = nested.get(group)
            if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
                continue
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                add_link(
                    item.get("DistributionUrl") or item.get("MapUrl"),
                    rel="alternate",
                    link_type=_normalize_string(item.get("Protocol")) or "text/html",
                    title=_normalize_string(item.get("Title")) or _normalize_string(item.get("TypeTranslated")),
                )

    return links


def _select_first_string(*values: Any) -> str:
    for value in values:
        text = _normalize_string(value)
        if text:
            return text
    return ""


def _normalize_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _normalize_sequence(value: Any) -> list[str] | None:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        items = [_normalize_string(item) for item in value if _normalize_string(item)]
        return items or None

    text = _normalize_string(value)
    if not text:
        return None

    if "," in text or ";" in text:
        parts = text.replace(";", ",").split(",")
        items = [_normalize_string(part) for part in parts if _normalize_string(part)]
        return items or None

    return [text]


def _parse_date(value: Any) -> str | None:
    text = _normalize_string(value)
    if not text:
        return None

    sanitized = text.replace("Z", "")
    try:
        dt = datetime.fromisoformat(sanitized)
    except ValueError:
        if len(text) >= 10:
            candidate = text[:10]
            try:
                datetime.strptime(candidate, "%Y-%m-%d")
                return candidate
            except ValueError:
                return text
        return text
    return dt.date().isoformat()


def _compact_mapping(mapping: Mapping[str, Any] | None) -> dict[str, Any]:
    if mapping is None:
        return {}

    compacted: dict[str, Any] = {}
    for key, value in mapping.items():
        cleaned = _compact_value(value)
        if _has_value(cleaned):
            compacted[key] = cleaned
    return compacted


def _compact_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _compact_mapping(value)

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        cleaned_sequence = [_compact_value(item) for item in value]
        return [item for item in cleaned_sequence if _has_value(item)]

    return value


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return True
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return bool(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_has_value(item) for item in value)
    return True


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point."""

    parser = argparse.ArgumentParser(
        description="Fetch dataset metadata from Geonorge and convert it to psdata-style JSON.",
    )
    parser.add_argument("metadata_id", help="Metadata UUID to fetch.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Optional path to the output JSON file. Defaults to stdout if omitted.",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="Number of spaces used for JSON indentation (default: 2).",
    )

    args = parser.parse_args(list(argv) if argv is not None else None)

    psdata = fetch_psdata(args.metadata_id)
    text = json.dumps(psdata, indent=args.indent, ensure_ascii=False)

    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        sys.stdout.write(text)
        sys.stdout.write("\n")

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
