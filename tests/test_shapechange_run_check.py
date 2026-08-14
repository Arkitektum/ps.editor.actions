"""Tests for the ShapeChange 'check' verdict, its outputs and job summary."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_shapechange  # noqa: E402

_LOG = """<?xml version="1.0" encoding="UTF-8"?>
<ShapeChangeResult xmlns="http://www.interactive-instruments.de/ShapeChange/Result">
  <Warning message="Type 0 unknown; using anyType"/>
  <Error message="Name of 'class' '0' includes invalid characters."/>
  <Result target="XmlSchema" scope="Schema" href="DyrkbarJord.xsd">DyrkbarJord.xsd</Result>
</ShapeChangeResult>
"""


class RunShapeChangeCheckTests(unittest.TestCase):
    def _run(self, tmp: Path, fail_on_error: str) -> tuple[int, str, str]:
        (tmp / "log.xml").write_text(_LOG, encoding="utf-8")
        output = tmp / "gh_output.txt"
        summary = tmp / "gh_summary.md"
        env = {
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
        }
        old = {key: os.environ.get(key) for key in env}
        os.environ.update(env)
        try:
            code = run_shapechange.main(
                [
                    "--mode",
                    "check",
                    "--output-dir",
                    str(tmp),
                    "--fail-on-error",
                    fail_on_error,
                ]
            )
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        return code, output.read_text(encoding="utf-8"), summary.read_text(encoding="utf-8")

    def test_fail_on_error_false_keeps_going_and_records_the_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            code, output, summary = self._run(Path(directory), "false")
        self.assertEqual(code, 0)
        self.assertIn("error-count=1", output)
        self.assertIn("warning-count=1", output)
        self.assertIn("has-errors=true", output)
        # Both errors and warnings land in the persistent job summary.
        self.assertIn("Name of 'class' '0'", summary)
        self.assertIn("Type 0 unknown", summary)

    def test_fail_on_error_true_fails_the_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            code, _output, _summary = self._run(Path(directory), "true")
        self.assertEqual(code, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
