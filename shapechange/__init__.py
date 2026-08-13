"""ShapeChange integration: feature catalogue JSON to SCXML and configuration."""

from shapechange.config import build_config
from shapechange.log_report import LogReport, read_log_report
from shapechange.scxml import build_scxml, write_scxml

__all__ = [
    "build_config",
    "build_scxml",
    "write_scxml",
    "LogReport",
    "read_log_report",
]
