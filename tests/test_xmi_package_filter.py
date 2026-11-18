"""Per-scope package filter: several SOSI packages can share one XMI file."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from xmi.feature_catalog import load_feature_types_from_xmi


def _feature(class_id: str, name: str) -> str:
    return (
        f'<UML:Class name="{name}" xmi.id="{class_id}" isAbstract="false">'
        '<UML:ModelElement.stereotype><UML:Stereotype name="FeatureType"/>'
        "</UML:ModelElement.stereotype></UML:Class>"
    )


def _package(pkg_id: str, name: str, inner: str) -> str:
    return (
        f'<UML:Package name="{name}" xmi.id="{pkg_id}"><UML:Namespace.ownedElement>'
        f"{inner}</UML:Namespace.ownedElement></UML:Package>"
    )


# Synthetic file: two sibling packages; A has a nested sub-package.
XMI = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<XMI xmlns:UML="omg.org/UML1.3" xmi.version="1.1"><XMI.content><UML:Model>'
    '<UML:Namespace.ownedElement>'
    + _package(
        "EAPK_A",
        "Pakke A",
        _feature("EAID_A1", "Alfa")
        + _package("EAPK_A_SUB", "Under A", _feature("EAID_A2", "AlfaUnder")),
    )
    + _package("EAPK_B", "Pakke B", _feature("EAID_B1", "Beta"))
    + "</UML:Namespace.ownedElement></UML:Model></XMI.content></XMI>"
)


class XmiPackageFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.path = Path(self._tmp.name) / "model.xml"
        self.path.write_text(XMI, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _names(self, **kwargs) -> list[str]:
        return sorted(f["name"] for f in load_feature_types_from_xmi(self.path, **kwargs))

    def test_without_package_returns_all_feature_types(self) -> None:
        self.assertEqual(self._names(), ["Alfa", "AlfaUnder", "Beta"])

    def test_package_id_keeps_package_and_sub_packages(self) -> None:
        self.assertEqual(self._names(package="EAPK_A"), ["Alfa", "AlfaUnder"])
        self.assertEqual(self._names(package="EAPK_B"), ["Beta"])

    def test_falls_back_to_package_name_when_id_is_unknown(self) -> None:
        self.assertEqual(
            self._names(package="EAPK_UNKNOWN", package_name="pakke b"), ["Beta"]
        )

    def test_missing_package_raises_instead_of_empty_catalogue(self) -> None:
        with self.assertRaises(ValueError):
            load_feature_types_from_xmi(self.path, package="EAPK_NOPE", package_name="Nope")


if __name__ == "__main__":
    unittest.main()
