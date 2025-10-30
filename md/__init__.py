"""Markdown utilities for feature type metadata and product specifications."""

from .feature_types import main as feature_types_main
from .feature_types import render_feature_types_to_markdown
from .product_specification import (
    IncludeResource,
    build_context,
    main as product_specification_main,
    render_product_specification,
    render_template,
)

__all__ = [
    "render_feature_types_to_markdown",
    "feature_types_main",
    "IncludeResource",
    "build_context",
    "render_product_specification",
    "render_template",
    "product_specification_main",
]
