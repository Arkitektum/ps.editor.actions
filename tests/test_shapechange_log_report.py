"""Tests for reading ShapeChange's XML log."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shapechange.log_report import (  # noqa: E402
    format_github_annotations,
    read_log_report,
)

_LOG_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<ShapeChangeResult xmlns="http://www.interactive-instruments.de/ShapeChange/Result">
{body}
</ShapeChangeResult>
"""


def _write_log(directory: str, body: str) -> Path:
    path = Path(directory) / "log.xml"
    path.write_text(_LOG_TEMPLATE.format(body=body), encoding="utf-8")
    return path


class LogReportTests(unittest.TestCase):
    def test_clean_log_is_ok(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _write_log(
                directory,
                '  <Info message="Everything is fine"/>\n'
                '  <Result target="XML Schema" scope="Class" href="Test.xsd">Test.xsd</Result>',
            )
            report = read_log_report(path)

        self.assertTrue(report.ok)
        self.assertEqual(report.errors, [])
        self.assertEqual(len(report.results), 1)
        self.assertEqual(report.results[0].target, "XML Schema")
        self.assertEqual(report.results[0].label, "Test.xsd")

    def test_errors_are_collected_and_fail_the_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _write_log(
                directory,
                '  <Error message="Type not found"/>\n'
                '  <ProcessFlowFatalError message="Target aborted"/>\n'
                '  <Warning message="Using a default name"/>',
            )
            report = read_log_report(path)

        self.assertFalse(report.ok)
        self.assertEqual(
            [error.message for error in report.errors],
            ["Type not found", "Target aborted"],
        )
        self.assertEqual([w.message for w in report.warnings], ["Using a default name"])

    def test_nested_detail_messages_are_kept(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _write_log(
                directory,
                '  <Error message="Encoding failed">\n'
                '    <Detail message="class Bygning"/>\n'
                "  </Error>",
            )
            report = read_log_report(path)

        self.assertEqual(report.errors[0].details, ("class Bygning",))
        self.assertEqual(report.errors[0].format(), "Encoding failed (class Bygning)")

    def test_missing_log_is_an_error(self) -> None:
        # ShapeChange exits 0 on many failures, so "no log" must not read as success.
        with tempfile.TemporaryDirectory() as directory:
            report = read_log_report(Path(directory) / "log.xml")

        self.assertFalse(report.ok)
        self.assertIn("was not found", report.errors[0].message)

    def test_unparseable_log_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "log.xml"
            path.write_text("<ShapeChangeResult><broken>", encoding="utf-8")
            report = read_log_report(path)

        self.assertFalse(report.ok)
        self.assertIn("could not be parsed", report.errors[0].message)

    def test_annotations_are_rendered_as_workflow_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _write_log(
                directory,
                '  <Error message="Boom"/>\n  <Warning message="Careful"/>',
            )
            report = read_log_report(path)

        self.assertEqual(
            format_github_annotations(report),
            ["::warning::ShapeChange: Careful", "::error::ShapeChange: Boom"],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
