"""GeoPackage as a feature catalogue source and as an output format."""

from geopackage.feature_types import load_feature_types_from_geopackage
from geopackage.realisation import geometry_suffix, split_multi_geometry_types
from geopackage.writer import write_geopackage

__all__ = [
    "load_feature_types_from_geopackage",
    "write_geopackage",
    # Exposed so a post-process can describe the realised model in the product
    # specification, rather than the modelled one.
    "split_multi_geometry_types",
    "geometry_suffix",
]
