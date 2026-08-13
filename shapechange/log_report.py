"""Read ShapeChange's XML log and turn it into a pass/fail verdict.

ShapeChange only returns a non-zero exit code for ``ShapeChangeAbortException``.
Ordinary target and model errors are written to the log and the process still
exits 0, so the log has to be inspected to decide whether a run succeeded.

The log uses the namespace
``http://www.interactive-instruments.de/ShapeChange/Result`` with one element
per message (``Error``, ``FatalError``, ``Warning``, ``Info``, ``Debug`` and the
``ProcessFlow*`` variants) carrying a ``message`` attribute, plus ``Result``
elements describing the files that were written.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

__all__ = [
    "LogMessage",
    "LogResult",
    "LogReport",
    "read_log_report",
    "format_github_annotations",
]

RESULT_NS = "http://www.interactive-instruments.de/ShapeChange/Result"

_ERROR_TAGS = {"Error", "FatalError", "ProcessFlowError", "ProcessFlowFatalError"}
_WARNING_TAGS = {"Warning", "ProcessFlowWarning"}


@dataclass(frozen=True)
class LogMessage:
    level: str
    message: str
    details: tuple[str, ...] = ()

    def format(self) -> str:
        if not self.details:
            return self.message
        return f"{self.message} ({'; '.join(self.details)})"


@dataclass(frozen=True)
class LogResult:
    target: str
    scope: str
    href: str
    label: str


@dataclass
class LogReport:
    errors: list[LogMessage] = field(default_factory=list)
    warnings: list[LogMessage] = field(default_factory=list)
    results: list[LogResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _read_message(element: ET.Element) -> LogMessage:
    details = tuple(
        str(child.get("message", "")).strip()
        for child in element
        if str(child.get("message", "")).strip()
    )
    return LogMessage(
        level=_local_name(element.tag),
        message=str(element.get("message", "")).strip(),
        details=details,
    )


def read_log_report(log_path: str | Path) -> LogReport:
    """Parse a ShapeChange log file.

    A missing or unparseable log is itself an error: it means ShapeChange did
    not run to the point of writing its log, which must not be reported as a
    successful run.
    """
    path = Path(log_path)
    report = LogReport()

    if not path.exists():
        report.errors.append(
            LogMessage(
                level="FatalError",
                message=f"ShapeChange log '{path}' was not found; the run did not produce a log.",
            )
        )
        return report

    try:
        tree = ET.parse(path)
    except ET.ParseError as error:
        report.errors.append(
            LogMessage(
                level="FatalError",
                message=f"ShapeChange log '{path}' could not be parsed: {error}",
            )
        )
        return report

    for element in tree.getroot().iter():
        name = _local_name(element.tag)
        if name in _ERROR_TAGS:
            report.errors.append(_read_message(element))
        elif name in _WARNING_TAGS:
            report.warnings.append(_read_message(element))
        elif name == "Result":
            report.results.append(
                LogResult(
                    target=str(element.get("target", "")).strip(),
                    scope=str(element.get("scope", "")).strip(),
                    href=str(element.get("href", "")).strip(),
                    label=(element.text or "").strip(),
                )
            )

    return report


def format_github_annotations(report: LogReport) -> list[str]:
    """Render the report as GitHub Actions workflow commands."""
    lines: list[str] = []
    for warning in report.warnings:
        lines.append(f"::warning::ShapeChange: {warning.format()}")
    for error in report.errors:
        lines.append(f"::error::ShapeChange: {error.format()}")
    return lines
