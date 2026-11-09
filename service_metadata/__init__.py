"""What the OGC API - Features configuration emitters share: service metadata read
from psdata, collection names and the CRSs every collection offers."""

from .psdata import (
    DEFAULT_SRID,
    OFFERED_EPSG_CODES,
    PLACEHOLDER,
    contact,
    dataset_bbox,
    feature_keywords,
    keywords,
    resource_name,
    string,
    text,
    unique_key,
)

__all__ = [
    "DEFAULT_SRID",
    "OFFERED_EPSG_CODES",
    "PLACEHOLDER",
    "contact",
    "dataset_bbox",
    "feature_keywords",
    "keywords",
    "resource_name",
    "string",
    "text",
    "unique_key",
]
