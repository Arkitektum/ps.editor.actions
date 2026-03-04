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

    if payload is None:
        payload = {}

    if isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], Mapping):
        payload = payload[0]

    snippet = ""
    try:
        text = getattr(response, "text", "")
        if text:
            snippet = f" Response snippet: {text[:300]!r}"
    except Exception:
        snippet = ""

    if isinstance(payload, Mapping) and not payload:
        raise ValueError(
            f"Metadata response for '{metadata_id}' was empty; verify the metadata ID or access.{snippet}"
        )

    if not isinstance(payload, Mapping):
        raise ValueError(
            f"Metadata response must be a JSON object; got {type(payload).__name__}.{snippet}"
        )

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
            "title": _select_first_string(
                metadata.get("NorwegianTitle"), metadata.get("EnglishTitle"), metadata.get("Title")
            ),
            "date": _compact_mapping(
                {
                    "creation": _parse_date(metadata.get("DatePublished")),
                    "revision": _parse_date(metadata.get("DateUpdated")),
                }
            ),
            "language": _normalize_string(metadata.get("DatasetLanguage")),
            "contact": _build_top_level_contact(metadata),
            "identificationSection": _build_identification(
                metadata_id, metadata, spatial_extent=spatial_extent,
            ),
            "scopeSection": _build_scope_section(metadata),
            "dataContentAndStructureSection": _build_data_content_section(metadata),
            "referenceSystemSection": _compact_mapping(
                {
                    "spatialReferenceSystem": spatial_reference_systems,
                }
            ),
            "dataQualitySection": _extract_quality(metadata),
            "maintenanceSection": _compact_mapping(
                {
                    "maintenanceAndUpdateFrequency": _normalize_string(
                        metadata.get("MaintenanceFrequency")
                    ),
                    "maintenanceAndUpdateStatement": _normalize_string(metadata.get("Status")),
                }
            ),
            "dataCaptureAndProductionSection": _build_data_capture_section(metadata),
            "portrayal": _extract_portrayal(metadata),
            "deliverySection": _extract_deliveries(metadata),
            "metadataSection": _build_metadata_section(metadata_id, metadata),
        }
    )

    return result


# ---------------------------------------------------------------------------
# Top-level contact (specification owner)
# ---------------------------------------------------------------------------

def _build_top_level_contact(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    owner = metadata.get("ContactOwner")
    if not isinstance(owner, Mapping):
        return None

    contact = _compact_mapping(
        {
            "individualName": _normalize_string(owner.get("Name")),
            "organizationName": _select_first_string(
                owner.get("Organization"), owner.get("OrganizationEnglish")
            ),
            "logo": _normalize_string(metadata.get("OrganizationLogoUrl")),
            "electronicMailAddress": _normalize_string(owner.get("Email")),
            "role": _normalize_string(owner.get("Role")),
        }
    )
    return contact or None


# ---------------------------------------------------------------------------
# identificationSection
# ---------------------------------------------------------------------------

def _build_identification(
    metadata_id: str,
    metadata: Mapping[str, Any],
    *,
    spatial_extent: dict[str, Any] | None,
) -> dict[str, Any]:
    keywords = _collect_keywords(metadata)
    topic_categories = _collect_topic_categories(metadata)

    date = _compact_mapping(
        {
            "creation": _parse_date(metadata.get("DatePublished")),
            "publication": _parse_date(metadata.get("DatePublished")),
            "revision": _parse_date(metadata.get("DateUpdated")),
        }
    )

    purpose_text = _normalize_string(metadata.get("Purpose"))
    specific_usage = _normalize_string(metadata.get("SpecificUsage"))
    purpose = _compact_mapping(
        {
            "summary": purpose_text,
            "useCase": _compact_mapping(
                {
                    "name": "Bruksområde" if specific_usage else "",
                    "summary": specific_usage,
                }
            ),
        }
    )

    restriction = _build_restriction(metadata)
    extent = _build_identification_extent(spatial_extent, metadata)

    identification = _compact_mapping(
        {
            "title": _select_first_string(
                metadata.get("NorwegianTitle"), metadata.get("EnglishTitle"), metadata.get("Title")
            ),
            "abstract": _normalize_string(metadata.get("Abstract")),
            "purpose": purpose,
            "topicCategory": topic_categories,
            "spatialRepresentationType": _normalize_string(
                metadata.get("SpatialRepresentation")
            ),
            "spatialResolution": _build_spatial_resolution(metadata),
            "supplementalInformation": _normalize_string(metadata.get("SupplementalDescription")),
            "uniqueId": metadata.get("Uuid") or metadata_id,
            "keyword": keywords,
            "restriction": restriction,
            "contact": _collect_contacts(metadata),
            "maintenance": _normalize_string(metadata.get("MaintenanceFrequency")),
            "extent": extent,
            "date": date,
            "language": _normalize_string(metadata.get("DatasetLanguage")),
        }
    )

    return identification


def _build_restriction(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    constraints = metadata.get("Constraints")
    if not isinstance(constraints, Mapping):
        return None

    use_limitations = _normalize_string(constraints.get("UseLimitations"))
    access_constraints = _normalize_string(constraints.get("AccessConstraints"))
    use_constraints = _normalize_string(constraints.get("UseConstraints"))
    license_text = _select_first_string(
        constraints.get("OtherConstraintsLinkText"),
        constraints.get("OtherConstraintsAccess"),
    )
    license_url = _normalize_string(constraints.get("OtherConstraintsLink"))
    other_constraints = _normalize_string(constraints.get("OtherConstraints"))
    security_constraints_value = _normalize_string(constraints.get("SecurityConstraints"))

    # Reference from securityConstraints/userNote in metadata
    reference = _normalize_string(constraints.get("SecurityConstraintsNote"))

    restriction = _compact_mapping(
        {
            "resourceConstraints": _compact_mapping(
                {
                    "useLimitations": use_limitations,
                }
            ),
            "legalConstraints": _compact_mapping(
                {
                    "accessConstraints": access_constraints,
                    "useConstraints": use_constraints,
                    "license": license_text,
                    "licenseUrl": license_url,
                    "otherConstraints": other_constraints,
                    "reference": reference,
                }
            ),
            "securityConstraints": _compact_mapping(
                {
                    "classification": security_constraints_value,
                }
            ),
        }
    )
    return restriction or None


def _build_spatial_resolution(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    resolution = metadata.get("ResolutionScale")
    distance_value = metadata.get("ResolutionDistance")

    if not resolution and not distance_value:
        return None

    distance = None
    if distance_value:
        distance = _compact_mapping(
            {
                "uom": "meter",
                "value": distance_value,
            }
        )

    result = _compact_mapping(
        {
            "distance": distance,
            "equivalentScale": _normalize_string(resolution) if resolution else None,
        }
    )
    return result or None


def _build_identification_extent(
    spatial_extent: dict[str, Any] | None,
    metadata: Mapping[str, Any],
) -> dict[str, Any] | None:
    geographic = None
    if spatial_extent:
        bbox = spatial_extent.get("boundingBox")
        if isinstance(bbox, Mapping):
            geographic = _compact_mapping(
                {
                    "westBoundLongitude": str(bbox.get("west", "")) if bbox.get("west") is not None else "",
                    "eastBoundLongitude": str(bbox.get("east", "")) if bbox.get("east") is not None else "",
                    "southBoundLatitude": str(bbox.get("south", "")) if bbox.get("south") is not None else "",
                    "northBoundLatitude": str(bbox.get("north", "")) if bbox.get("north") is not None else "",
                }
            )

    temporal = None
    temporal_start = _parse_date(metadata.get("DatePublished"))
    temporal_end = _parse_date(metadata.get("DateUpdated"))
    if temporal_start or temporal_end:
        temporal = _compact_mapping(
            {
                "timePeriod": _compact_mapping(
                    {
                        "beginPosition": temporal_start or temporal_end,
                        "endPosition": temporal_end or temporal_start,
                    }
                ),
            }
        )

    extent = _compact_mapping(
        {
            "geographicElement": geographic,
            "temporalElement": temporal,
        }
    )
    return extent or None


# ---------------------------------------------------------------------------
# scopeSection
# ---------------------------------------------------------------------------

def _build_scope_section(metadata: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    level = _normalize_string(metadata.get("HierarchyLevel") or metadata.get("Type"))
    spatial_scope = _normalize_string(metadata.get("SpatialScope"))

    if not level:
        return None

    scope_entry = _compact_mapping(
        {
            "specificationScope": _compact_mapping(
                {
                    "scopeIdentification": "hele datasettet",
                    "level": level,
                    "levelName": "",
                    "levelDescription": "",
                    "extent": _compact_mapping(
                        {
                            "description": spatial_scope,
                        }
                    ),
                }
            ),
        }
    )

    return [scope_entry] if scope_entry else None


# ---------------------------------------------------------------------------
# dataContentAndStructureSection
# ---------------------------------------------------------------------------

def _build_data_content_section(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    specific_usage = _normalize_string(metadata.get("SpecificUsage"))

    if not specific_usage:
        return None

    return _compact_mapping(
        {
            "narrativeDescription": specific_usage,
        }
    )


# ---------------------------------------------------------------------------
# dataQualitySection
# ---------------------------------------------------------------------------

def _extract_quality(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    elements: list[dict[str, Any]] = []

    quality_specs = metadata.get("QualitySpecifications")
    if isinstance(quality_specs, Sequence) and not isinstance(quality_specs, (str, bytes)):
        for spec in quality_specs:
            if not isinstance(spec, Mapping):
                continue
            explanation = _normalize_string(spec.get("Explanation"))
            quantitative_result = _normalize_string(spec.get("QuantitativeResult"))
            entry = _compact_mapping(
                {
                    "nameOfMeasure": _normalize_string(spec.get("Title")),
                    "measureDescription": explanation,
                    "descriptiveResult": explanation if not quantitative_result else None,
                    "result": quantitative_result,
                }
            )
            if entry:
                elements.append(entry)

    quantitative = metadata.get("QuantitativeResult")
    if isinstance(quantitative, Mapping):
        for key, value in quantitative.items():
            entry = _compact_mapping(
                {
                    "nameOfMeasure": _normalize_string(key),
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
            "report": elements if elements else None,
            "resourceLineage": _compact_mapping({"statement": lineage_statement}),
        }
    )

    return result if result else None


# ---------------------------------------------------------------------------
# dataCaptureAndProductionSection
# ---------------------------------------------------------------------------

def _build_data_capture_section(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    process_steps = _build_process_steps(metadata)
    if not process_steps:
        return None

    return _compact_mapping(
        {
            "DataAcquisitionAndProcessing": _compact_mapping(
                {
                    "processStep": process_steps,
                }
            ),
        }
    )


# ---------------------------------------------------------------------------
# portrayal
# ---------------------------------------------------------------------------

def _extract_portrayal(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    legend_url = _normalize_string(metadata.get("LegendDescriptionUrl"))

    portrayal = _compact_mapping(
        {
            "name": "Tegneregler" if legend_url else "",
            "linkage": legend_url,
        }
    )
    return portrayal or None


# ---------------------------------------------------------------------------
# deliverySection
# ---------------------------------------------------------------------------

def _extract_deliveries(metadata: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    deliveries: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_delivery(entry: dict[str, Any] | None) -> None:
        if not entry:
            return
        key = json.dumps(entry, sort_keys=True, ensure_ascii=False)
        if key in seen:
            return
        seen.add(key)
        deliveries.append(entry)

    units_of_distribution = _normalize_string(metadata.get("UnitsOfDistribution"))

    # Prefer DistributionsFormats (rich, grouped by protocol+format)
    dist_formats = metadata.get("DistributionsFormats")
    if isinstance(dist_formats, Sequence) and not isinstance(dist_formats, (str, bytes)) and dist_formats:
        _build_deliveries_from_distributions_formats(dist_formats, units_of_distribution, add_delivery)
    else:
        # Fallback to top-level distribution fields
        _build_deliveries_from_top_level(metadata, units_of_distribution, add_delivery)

    # Nested Distributions (services like WMS, WFS etc.)
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
                format_version = _extract_distribution_format_version(item.get("DistributionFormats"))
                access_href = _normalize_string(
                    item.get("DistributionUrl") or item.get("MapUrl")
                )
                item_protocol = _normalize_string(item.get("Protocol"))
                item_title = _normalize_string(item.get("Title"))

                delivery_format = None
                if format_name:
                    delivery_format = [
                        _compact_mapping(
                            {
                                "formatName": format_name,
                                "version": format_version or "",
                            }
                        )
                    ]

                delivery = _compact_mapping(
                    {
                        "delivery": _compact_mapping(
                            {
                                "deliveryMedium": _compact_mapping(
                                    {
                                        "deliveryMediumName": item_title,
                                        "deliveryService": _compact_mapping(
                                            {
                                                "serviceEndpoint": access_href,
                                                "serviceProperty": _compact_mapping(
                                                    {
                                                        "type": item_title,
                                                        "value": item_protocol,
                                                    }
                                                ),
                                            }
                                        ),
                                    }
                                ),
                                "deliveryFormat": delivery_format,
                                "deliveryScope": _normalize_string(item.get("TypeTranslated")),
                            }
                        ),
                    }
                )
                add_delivery(delivery)

    return deliveries if deliveries else None


def _build_deliveries_from_distributions_formats(
    dist_formats: Sequence[Any],
    units_of_distribution: str,
    add_delivery: Callable[[dict[str, Any] | None], None],
) -> None:
    """Build delivery entries from the rich DistributionsFormats array.

    Groups entries by (ProtocolName, URL, Protocol) so each unique service
    endpoint becomes one delivery with multiple formats.
    """
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    group_meta: dict[tuple[str, str, str], dict[str, str]] = {}

    for item in dist_formats:
        if not isinstance(item, Mapping):
            continue
        protocol_name = _normalize_string(item.get("ProtocolName"))
        url = _normalize_string(item.get("URL"))
        protocol = _normalize_string(item.get("Protocol"))
        format_name = _normalize_string(item.get("FormatName"))
        version = _normalize_string(item.get("Version"))
        units = _normalize_string(item.get("UnitsOfDistribution")) or units_of_distribution

        key = (protocol_name, url, protocol)
        if key not in groups:
            groups[key] = []
            group_meta[key] = {"units": units}

        if format_name:
            groups[key].append(
                _compact_mapping({"formatName": format_name, "version": version})
            )

    for key, formats in groups.items():
        protocol_name, url, protocol = key
        units = group_meta[key].get("units", "")

        delivery = _compact_mapping(
            {
                "delivery": _compact_mapping(
                    {
                        "deliveryMedium": _compact_mapping(
                            {
                                "unitsOfDelivery": units,
                                "deliveryMediumName": protocol_name,
                                "deliveryService": _compact_mapping(
                                    {
                                        "serviceEndpoint": url,
                                        "serviceProperty": _compact_mapping(
                                            {
                                                "type": protocol_name,
                                                "value": protocol,
                                            }
                                        ),
                                    }
                                ),
                            }
                        ),
                        "deliveryFormat": formats if formats else None,
                    }
                ),
            }
        )
        add_delivery(delivery)


def _build_deliveries_from_top_level(
    metadata: Mapping[str, Any],
    units_of_distribution: str,
    add_delivery: Callable[[dict[str, Any] | None], None],
) -> None:
    """Fallback: build delivery entries from top-level distribution fields."""
    protocol = _normalize_string(metadata.get("DistributionProtocol"))
    distribution_url = _normalize_string(metadata.get("DistributionUrl"))
    download_url = _normalize_string(metadata.get("DownloadUrl"))

    # Collect top-level DistributionFormats for format list
    top_formats: list[dict[str, Any]] = []
    raw_formats = metadata.get("DistributionFormats")
    if isinstance(raw_formats, Sequence) and not isinstance(raw_formats, (str, bytes)):
        seen_names: set[str] = set()
        for fmt in raw_formats:
            if isinstance(fmt, Mapping):
                name = _normalize_string(fmt.get("Name"))
                if name and name not in seen_names:
                    seen_names.add(name)
                    top_formats.append(
                        _compact_mapping({"formatName": name, "version": _normalize_string(fmt.get("Version"))})
                    )

    if protocol or distribution_url or download_url:
        details = metadata.get("DistributionDetails")
        detail_name = ""
        if isinstance(details, Mapping):
            detail_name = _normalize_string(details.get("ProtocolName"))

        delivery = _compact_mapping(
            {
                "delivery": _compact_mapping(
                    {
                        "deliveryMedium": _compact_mapping(
                            {
                                "unitsOfDelivery": units_of_distribution,
                                "deliveryMediumName": detail_name or protocol or "",
                                "deliveryService": _compact_mapping(
                                    {
                                        "serviceEndpoint": distribution_url or download_url,
                                        "serviceProperty": _compact_mapping(
                                            {
                                                "type": detail_name or protocol,
                                                "value": protocol,
                                            }
                                        ),
                                    }
                                ),
                            }
                        ),
                        "deliveryFormat": top_formats if top_formats else None,
                    }
                ),
            }
        )
        add_delivery(delivery)


def _extract_distribution_format_version(value: Any) -> str | None:
    if isinstance(value, Mapping):
        return _normalize_string(value.get("Version")) or None

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            extracted = _extract_distribution_format_version(item)
            if extracted:
                return extracted

    return None


# ---------------------------------------------------------------------------
# metadataSection
# ---------------------------------------------------------------------------

def _build_metadata_section(metadata_id: str, metadata: Mapping[str, Any]) -> dict[str, Any]:
    contact = None
    metadata_contact = metadata.get("ContactMetadata")
    if isinstance(metadata_contact, Mapping):
        contact = _compact_mapping(
            {
                "organizationName": _normalize_string(
                    metadata_contact.get("Organization")
                    or metadata_contact.get("OrganizationEnglish")
                ),
                "individualName": _normalize_string(metadata_contact.get("Name")),
                "logo": _normalize_string(metadata.get("OrganizationLogoUrl")),
                "electronicMailAddress": _normalize_string(metadata_contact.get("Email")),
                "role": _normalize_string(metadata_contact.get("Role")),
            }
        )

    uuid = metadata.get("Uuid") or metadata_id
    metadata_xml = _normalize_string(metadata.get("MetadataXmlUrl"))
    landing_page = _normalize_string(
        metadata.get("LandingPage")
        or metadata.get("LandingPageUrl")
        or metadata.get("Landingpage")
    )

    metadata_identifier = _compact_mapping(
        {
            "authority": "Geonorge",
            "code": uuid,
            "codeSpace": "https://kartkatalog.geonorge.no/metadata/",
            "metadataLinkage": landing_page or f"https://kartkatalog.geonorge.no/metadata/{uuid}",
        }
    )

    links = _collect_links(metadata)

    metadata_section = _compact_mapping(
        {
            "metadataStandard": _normalize_string(metadata.get("MetadataStandard")),
            "metadataStandardVersion": _normalize_string(metadata.get("MetadataStandardVersion")),
            "metadataDate": _parse_date(metadata.get("DateMetadataUpdated")),
            "language": _normalize_string(metadata.get("MetadataLanguage")),
            "contact": contact,
            "metadataIdentifier": metadata_identifier,
            "links": links if links else None,
        }
    )

    return metadata_section


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _build_process_steps(metadata: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    raw = metadata.get("ProcessHistory")
    if raw is None:
        return None

    steps: list[dict[str, Any]] = []

    def add_step(description: str, date_value: Any = None) -> None:
        desc = _normalize_string(description)
        if not desc:
            return
        step = _compact_mapping(
            {
                "description": desc,
                "date": _parse_date(date_value),
            }
        )
        if step:
            steps.append(step)

    if isinstance(raw, str):
        add_step(raw)
        return steps or None

    if isinstance(raw, Mapping):
        description = _select_first_string(
            raw.get("Description"),
            raw.get("ProcessStep"),
            raw.get("ProcessDescription"),
            raw.get("Text"),
            raw.get("Value"),
        )
        add_step(description, raw.get("Date") or raw.get("ProcessDate"))
        return steps or None

    if isinstance(raw, Sequence):
        for item in raw:
            if isinstance(item, str):
                add_step(item)
                continue
            if isinstance(item, Mapping):
                description = _select_first_string(
                    item.get("Description"),
                    item.get("ProcessStep"),
                    item.get("ProcessDescription"),
                    item.get("Text"),
                    item.get("Value"),
                )
                add_step(description, item.get("Date") or item.get("ProcessDate"))
        return steps or None

    return None


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
            west = _parse_coordinate(bbox.get("WestBoundLongitude"))
            south = _parse_coordinate(bbox.get("SouthBoundLatitude"))
            east = _parse_coordinate(bbox.get("EastBoundLongitude"))
            north = _parse_coordinate(bbox.get("NorthBoundLatitude"))
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
                "individualName": _normalize_string(value.get("Name")),
                "organizationName": _select_first_string(
                    value.get("Organization"), value.get("OrganizationEnglish")
                ),
                "electronicMailAddress": _normalize_string(value.get("Email")),
                "role": _normalize_string(value.get("Role")),
            }
        )
        if entry:
            contacts.append(entry)

    return contacts


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


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _parse_coordinate(value: Any) -> float:
    """Parse a coordinate value, handling both dot and comma decimal separators."""
    text = _normalize_string(value)
    if not text:
        raise ValueError("Empty coordinate value")
    return float(text.replace(",", "."))


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
