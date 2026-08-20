# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The pure KML/KMZ helpers - geometry serialization, ABGR color packing, the label-on-companion-point trick for polygons, and the KMZ round-trip, including proof the external_attr patch reaches every central-directory entry, which is what keeps WhatsApp accepting the files."""

import os
import re
import sys
import tempfile
import unittest
import zipfile

from qgis.PyQt.QtGui import QColor  # type: ignore
from qgis.core import (  # type: ignore
    QgsApplication, QgsFeature, QgsField, QgsFields, QgsGeometry,
    QgsProject, QgsVectorLayer, QgsWkbTypes,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from vernier.qt_compat import FIELD_STRING  # noqa: E402
from vernier.services import kml_writer  # noqa: E402

QGS = None


def setUpModule():
    global QGS
    QGS = QgsApplication([], False)
    QGS.initQgis()


def tearDownModule():
    QGS.exitQgis()


def _feature(values):
    """QgsFeature with string fields a, b, ... holding the given values."""
    fields = QgsFields()
    for i in range(len(values)):
        fields.append(QgsField(chr(ord("a") + i), FIELD_STRING))
    feature = QgsFeature(fields)
    feature.setAttributes(list(values))
    return feature


class TestGeometryToKml(unittest.TestCase):

    def test_polygon_with_hole_writes_both_rings(self):
        geom = QgsGeometry.fromWkt(
            "POLYGON((0 0, 4 0, 4 4, 0 4, 0 0),"
            "(1 1, 2 1, 2 2, 1 2, 1 1))")
        kml = kml_writer.geometry_to_kml(geom, QgsWkbTypes.PolygonGeometry)
        self.assertEqual(kml.count("<outerBoundaryIs>"), 1)
        self.assertEqual(kml.count("<innerBoundaryIs>"), 1)
        self.assertIn("4.00000000,4.00000000,0", kml)
        self.assertIn("2.00000000,2.00000000,0", kml)
        # both rings closed: 5 coordinate triplets each
        triplets = re.findall(r"-?\d+\.\d{8},-?\d+\.\d{8},0(?=[ <])", kml)
        self.assertEqual(len(triplets), 10)

    def test_multipolygon_becomes_multigeometry(self):
        geom = QgsGeometry.fromWkt(
            "MULTIPOLYGON(((0 0, 1 0, 1 1, 0 1, 0 0)),"
            "((5 5, 6 5, 6 6, 5 6, 5 5)))")
        kml = kml_writer.geometry_to_kml(geom, QgsWkbTypes.PolygonGeometry)
        self.assertEqual(kml.count("<Polygon>"), 2)
        self.assertIn("<MultiGeometry>", kml)

    def test_multipoint_exports_every_point(self):
        geom = QgsGeometry.fromWkt("MULTIPOINT((0 0), (1 1), (2 2))")
        kml = kml_writer.geometry_to_kml(geom, QgsWkbTypes.PointGeometry)
        self.assertEqual(kml.count("<Point>"), 3)
        self.assertIn("2.00000000,2.00000000,0", kml)

    def test_single_point(self):
        geom = QgsGeometry.fromWkt("POINT(3 4)")
        kml = kml_writer.geometry_to_kml(geom, QgsWkbTypes.PointGeometry)
        self.assertEqual(kml.count("<Point>"), 1)
        self.assertNotIn("<MultiGeometry>", kml)

    def test_circular_string_is_segmentized(self):
        geom = QgsGeometry.fromWkt("CIRCULARSTRING(0 0, 1 1, 2 0)")
        kml = kml_writer.geometry_to_kml(geom, QgsWkbTypes.LineGeometry)
        self.assertIsNotNone(kml)
        self.assertIn("<LineString>", kml)
        # an arc segmentizes into more vertices than its 3 control points
        self.assertGreater(kml.count(",0 "), 3)

    def test_curve_polygon_is_segmentized(self):
        geom = QgsGeometry.fromWkt(
            "CURVEPOLYGON(CIRCULARSTRING(0 0, 2 0, 2 2, 0 2, 0 0))")
        kml = kml_writer.geometry_to_kml(geom, QgsWkbTypes.PolygonGeometry)
        self.assertIsNotNone(kml)
        self.assertIn("<Polygon>", kml)

    def test_empty_geometry_returns_none(self):
        self.assertIsNone(kml_writer.geometry_to_kml(
            QgsGeometry(), QgsWkbTypes.PolygonGeometry))


class TestBuildLabel(unittest.TestCase):

    def test_prefix_and_suffix_per_field(self):
        feature = _feature(["12A", "350"])
        configs = [
            {"idx": 0, "prefix": "No. ", "suffix": ""},
            {"idx": 1, "prefix": "", "suffix": " sqm"},
        ]
        self.assertEqual(kml_writer.build_label(feature, configs),
                         "No. 12A\n350 sqm")

    def test_null_value_skipped(self):
        feature = _feature(["12A", None])
        configs = [
            {"idx": 0, "prefix": "", "suffix": ""},
            {"idx": 1, "prefix": "", "suffix": " sqm"},
        ]
        self.assertEqual(kml_writer.build_label(feature, configs), "12A")

    def test_newlines_in_value_flattened(self):
        feature = _feature(["line1\nline2"])
        configs = [{"idx": 0, "prefix": "", "suffix": ""}]
        self.assertEqual(kml_writer.build_label(feature, configs),
                         "line1 line2")

    def test_no_configs_gives_empty_label(self):
        self.assertEqual(kml_writer.build_label(_feature(["x"]), []), "")


class TestColorConversion(unittest.TestCase):

    def test_red_is_abgr(self):
        self.assertEqual(
            kml_writer.color_to_kml_abgr(QColor(255, 0, 0)), "ff0000ff")

    def test_blue_is_abgr(self):
        self.assertEqual(
            kml_writer.color_to_kml_abgr(QColor(0, 0, 255)), "ffff0000")

    def test_alpha_leads(self):
        self.assertEqual(
            kml_writer.color_to_kml_abgr(QColor(0x10, 0x20, 0x40, 0x80)),
            "80402010")

    def test_invalid_color_falls_back_to_blue(self):
        self.assertEqual(kml_writer.color_to_kml_abgr(QColor()),
                         kml_writer.KML_FALLBACK_COLOR_ABGR)


class TestLayerToKml(unittest.TestCase):

    def _polygon_layer(self):
        layer = QgsVectorLayer(
            "Polygon?crs=EPSG:4326&field=nr:string", "fixture", "memory")
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromWkt(
            "POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"))
        feature.setAttributes(["7"])
        layer.dataProvider().addFeatures([feature])
        return layer

    def test_polygon_label_rides_on_companion_point(self):
        layer = self._polygon_layer()
        kml, count = kml_writer.layer_to_kml(
            layer, [{"field": "nr", "prefix": "No. ", "suffix": ""}],
            QgsProject.instance().transformContext(), "ffff0000")
        self.assertEqual(count, 1)
        # label appears on the polygon placemark and on the label point
        self.assertEqual(kml.count("<name>No. 7</name>"), 2)
        self.assertIn("#lbl", kml)
        self.assertIn("<fill>0</fill>", kml)
        self.assertIn("ffff0000", kml)

    def test_no_labels_no_companion_point(self):
        layer = self._polygon_layer()
        kml, count = kml_writer.layer_to_kml(
            layer, [], QgsProject.instance().transformContext())
        self.assertEqual(count, 1)
        self.assertNotIn("#lbl", kml)
        self.assertEqual(kml.count("<Placemark>"), 1)


class TestKmlDocument(unittest.TestCase):

    def test_folder_per_layer_with_escaped_names(self):
        doc = kml_writer.build_kml_document(
            [("A & B", "<Placemark/>"), ("C <x>", "<Placemark/>")])
        self.assertEqual(doc.count("<Folder>"), 2)
        self.assertIn("<name>A &amp; B</name>", doc)
        self.assertIn("<name>C &lt;x&gt;</name>", doc)
        self.assertTrue(doc.startswith('<?xml version="1.0"'))
        self.assertTrue(doc.endswith("</kml>"))


class TestWriteKmz(unittest.TestCase):

    def test_roundtrip_and_zero_external_attr(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.kmz")
            kml_writer.write_kmz(path, "<kml>test</kml>")
            with zipfile.ZipFile(path) as kmz:
                self.assertIsNone(kmz.testzip())
                info = kmz.getinfo("doc.kml")
                self.assertEqual(info.external_attr, 0)
                self.assertEqual(info.create_system, 0)
                self.assertEqual(kmz.read("doc.kml"),
                                 b"<kml>test</kml>")

    def test_patch_covers_every_central_directory_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "multi.zip")
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("first.kml", "one")
                zf.writestr("second.kml", "two")
            kml_writer._patch_external_attrs(path)
            with zipfile.ZipFile(path) as zf:
                self.assertIsNone(zf.testzip())
                for info in zf.infolist():
                    self.assertEqual(info.external_attr, 0)
                self.assertEqual(zf.read("second.kml"), b"two")


if __name__ == "__main__":
    unittest.main()
