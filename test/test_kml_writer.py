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
    QgsApplication, QgsCategorizedSymbolRenderer, QgsFeature, QgsField,
    QgsFields, QgsFillSymbol, QgsGeometry, QgsLineSymbol,
    QgsPalLayerSettings, QgsProject, QgsProperty, QgsRendererCategory,
    QgsSingleSymbolRenderer, QgsTextBufferSettings, QgsTextFormat,
    QgsVectorLayer, QgsVectorLayerSimpleLabeling, QgsWkbTypes,
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
        kml = kml_writer.geometry_to_kml(geom, QgsWkbTypes.GeometryType.PolygonGeometry)
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
        kml = kml_writer.geometry_to_kml(geom, QgsWkbTypes.GeometryType.PolygonGeometry)
        self.assertEqual(kml.count("<Polygon>"), 2)
        self.assertIn("<MultiGeometry>", kml)

    def test_multipoint_exports_every_point(self):
        geom = QgsGeometry.fromWkt("MULTIPOINT((0 0), (1 1), (2 2))")
        kml = kml_writer.geometry_to_kml(geom, QgsWkbTypes.GeometryType.PointGeometry)
        self.assertEqual(kml.count("<Point>"), 3)
        self.assertIn("2.00000000,2.00000000,0", kml)

    def test_single_point(self):
        geom = QgsGeometry.fromWkt("POINT(3 4)")
        kml = kml_writer.geometry_to_kml(geom, QgsWkbTypes.GeometryType.PointGeometry)
        self.assertEqual(kml.count("<Point>"), 1)
        self.assertNotIn("<MultiGeometry>", kml)

    def test_circular_string_is_segmentized(self):
        geom = QgsGeometry.fromWkt("CIRCULARSTRING(0 0, 1 1, 2 0)")
        kml = kml_writer.geometry_to_kml(geom, QgsWkbTypes.GeometryType.LineGeometry)
        self.assertIsNotNone(kml)
        self.assertIn("<LineString>", kml)
        # an arc segmentizes into more vertices than its 3 control points
        self.assertGreater(kml.count(",0 "), 3)

    def test_curve_polygon_is_segmentized(self):
        geom = QgsGeometry.fromWkt(
            "CURVEPOLYGON(CIRCULARSTRING(0 0, 2 0, 2 2, 0 2, 0 0))")
        kml = kml_writer.geometry_to_kml(geom, QgsWkbTypes.GeometryType.PolygonGeometry)
        self.assertIsNotNone(kml)
        self.assertIn("<Polygon>", kml)

    def test_empty_geometry_returns_none(self):
        self.assertIsNone(kml_writer.geometry_to_kml(
            QgsGeometry(), QgsWkbTypes.GeometryType.PolygonGeometry))


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
        # invisible-icon style on the companion point, label in default white
        self.assertIn("<IconStyle><scale>0</scale></IconStyle>", kml)
        self.assertIn("<LabelStyle><scale>1.0</scale>", kml)
        self.assertIn("<fill>0</fill>", kml)
        self.assertIn("ffff0000", kml)

    def test_styles_are_inline_not_referenced(self):
        # phone viewers ignore styleUrl references to shared styles, so every placemark carries its own <Style>
        layer = self._polygon_layer()
        kml, _ = kml_writer.layer_to_kml(
            layer, [{"field": "nr", "prefix": "", "suffix": ""}],
            QgsProject.instance().transformContext(), "ffff0000")
        self.assertNotIn("styleUrl", kml)
        self.assertEqual(kml.count("<Placemark>"), kml.count("<Style id="))

    def test_no_labels_no_companion_point(self):
        layer = self._polygon_layer()
        kml, count = kml_writer.layer_to_kml(
            layer, [], QgsProject.instance().transformContext())
        self.assertEqual(count, 1)
        self.assertNotIn("lbl", kml)
        self.assertEqual(kml.count("<Placemark>"), 1)


def _polygon_fixture(count=1, fields="field=nr:string"):
    layer = QgsVectorLayer(
        f"Polygon?crs=EPSG:4326&{fields}", "fixture", "memory")
    feats = []
    for i in range(count):
        f = QgsFeature(layer.fields())
        x = i * 10
        f.setGeometry(QgsGeometry.fromWkt(
            f"POLYGON(({x} 0, {x + 1} 0, {x + 1} 1, {x} 1, {x} 0))"))
        f.setAttributes([str(i)] + [None] * (len(layer.fields()) - 1))
        feats.append(f)
    layer.dataProvider().addFeatures(feats)
    return layer


class TestSymbologyStyles(unittest.TestCase):
    """color_abgr=None derives the KML styles from the layer's renderer, feature by feature."""

    def test_fill_and_stroke_follow_the_symbol(self):
        layer = _polygon_fixture()
        layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({
            "color": "255,0,0,128",
            "outline_color": "0,255,0,255",
            "outline_width": "0.5",
        })))
        kml, count = kml_writer.layer_to_kml(
            layer, [], QgsProject.instance().transformContext())
        self.assertEqual(count, 1)
        # fill keeps its alpha, the stroke is its own color, 0.5 mm -> 1.9 px
        self.assertIn("<PolyStyle><color>800000ff</color><fill>1</fill>", kml)
        self.assertIn(
            "<LineStyle><color>ff00ff00</color><width>1.9</width>", kml)

    def test_no_brush_fill_turns_fill_off(self):
        layer = _polygon_fixture()
        layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({
            "color": "255,0,0,255",
            "style": "no",
            "outline_color": "0,0,0,255",
        })))
        kml, _ = kml_writer.layer_to_kml(
            layer, [], QgsProject.instance().transformContext())
        self.assertIn("<fill>0</fill>", kml)

    def test_categorized_layer_keeps_per_feature_colors(self):
        layer = _polygon_fixture(count=3)

        def category(value, color):
            return QgsRendererCategory(value, QgsFillSymbol.createSimple({
                "color": color, "outline_color": color}), value)

        layer.setRenderer(QgsCategorizedSymbolRenderer("nr", [
            category("0", "255,0,0,255"),
            category("1", "0,0,255,255"),
            category("2", "255,0,0,255"),
        ]))
        kml, count = kml_writer.layer_to_kml(
            layer, [], QgsProject.instance().transformContext())
        self.assertEqual(count, 3)
        # one inline style per placemark, each carrying its category's color
        self.assertEqual(kml.count("<Style id="), 3)
        self.assertEqual(kml.count("<Placemark>"), 3)
        self.assertEqual(kml.count("<LineStyle><color>ff0000ff</color>"), 2)
        self.assertEqual(kml.count("<LineStyle><color>ffff0000</color>"), 1)

    def test_flat_color_override_wins_over_the_renderer(self):
        layer = _polygon_fixture()
        layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({
            "color": "0,255,0,255", "outline_color": "0,255,0,255"})))
        kml, _ = kml_writer.layer_to_kml(
            layer, [], QgsProject.instance().transformContext(), "ffff0000")
        self.assertIn("ffff0000", kml)
        self.assertNotIn("ff00ff00", kml)


class TestDataDefinedColors(unittest.TestCase):
    """One symbol, per-feature color expression - how the DXF import styles multi-color CAD layers. The export must follow the expression, not the static base color."""

    def test_line_stroke_expression_exports_per_feature(self):
        layer = QgsVectorLayer(
            "LineString?crs=EPSG:4326&field=culoare:string", "cad", "memory")
        feats = []
        for i, color in enumerate(["#ff0000", "#0000ff"]):
            f = QgsFeature(layer.fields())
            f.setGeometry(QgsGeometry.fromWkt(
                f"LINESTRING(0 {i}, 1 {i})"))
            f.setAttributes([color])
            feats.append(f)
        layer.dataProvider().addFeatures(feats)

        symbol = QgsLineSymbol.createSimple(
            {"color": "255,0,0,255", "width": "0.25"})
        symbol.symbolLayer(0).setDataDefinedProperty(
            kml_writer._PROP_STROKE_COLOR,
            QgsProperty.fromExpression('"culoare"'))
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))

        kml, count = kml_writer.layer_to_kml(
            layer, [], QgsProject.instance().transformContext())
        self.assertEqual(count, 2)
        self.assertEqual(kml.count("<Style id="), 2)
        self.assertIn("ff0000ff", kml)
        self.assertIn("ffff0000", kml)

    def test_polygon_outline_expression_exports_per_feature(self):
        layer = _polygon_fixture(count=2, fields="field=nr:string")
        symbol = QgsFillSymbol.createSimple({
            "color": "0,0,0,30", "outline_color": "255,0,0,255"})
        symbol.symbolLayer(0).setDataDefinedProperty(
            kml_writer._PROP_STROKE_COLOR,
            QgsProperty.fromExpression(
                "CASE WHEN \"nr\" = '0' THEN '#00ff00' ELSE '#0000ff' END"))
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))

        kml, count = kml_writer.layer_to_kml(
            layer, [], QgsProject.instance().transformContext())
        self.assertEqual(count, 2)
        self.assertIn("<LineStyle><color>ff00ff00</color>", kml)
        self.assertIn("<LineStyle><color>ffff0000</color>", kml)


class TestQgisLabels(unittest.TestCase):
    """qgis_labels=True labels the export with the layer's own labeling - text and color."""

    def _labeled_layer(self, expression, is_expression, buffered=False):
        layer = _polygon_fixture()
        settings = QgsPalLayerSettings()
        settings.fieldName = expression
        settings.isExpression = is_expression
        fmt = QgsTextFormat()
        fmt.setColor(QColor(255, 0, 255))
        if buffered:
            buf = QgsTextBufferSettings()
            buf.setEnabled(True)
            buf.setColor(QColor(255, 255, 255))
            fmt.setBuffer(buf)
        settings.setFormat(fmt)
        layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
        return layer

    def test_plain_field_labeling(self):
        layer = self._labeled_layer("nr", False)
        kml, count = kml_writer.layer_to_kml(
            layer, [], QgsProject.instance().transformContext(),
            qgis_labels=True)
        self.assertEqual(count, 1)
        # polygon placemark plus the companion label point
        self.assertEqual(kml.count("<name>0</name>"), 2)

    def test_expression_labeling_evaluated(self):
        layer = self._labeled_layer("'Nr. ' || \"nr\"", True)
        kml, _ = kml_writer.layer_to_kml(
            layer, [], QgsProject.instance().transformContext(),
            qgis_labels=True)
        self.assertEqual(kml.count("<name>Nr. 0</name>"), 2)

    def test_label_color_follows_the_text_format(self):
        layer = self._labeled_layer("nr", False)
        kml, _ = kml_writer.layer_to_kml(
            layer, [], QgsProject.instance().transformContext(),
            qgis_labels=True)
        # magenta 255,0,255 in abgr, on the companion label style
        self.assertIn("<LabelStyle><color>ffff00ff</color>", kml)

    def test_buffered_label_keeps_the_default_white(self):
        # KML has no text halo - a label that needs its buffer to read stays viewer-default instead of going dark-on-dark
        layer = self._labeled_layer("nr", False, buffered=True)
        kml, _ = kml_writer.layer_to_kml(
            layer, [], QgsProject.instance().transformContext(),
            qgis_labels=True)
        self.assertEqual(kml.count("<name>0</name>"), 2)
        self.assertIn("<LabelStyle><scale>1.0</scale>", kml)
        self.assertNotIn("ffff00ff", kml)

    def test_unlabeled_layer_falls_back_to_no_labels(self):
        layer = _polygon_fixture()
        kml, count = kml_writer.layer_to_kml(
            layer, [], QgsProject.instance().transformContext(),
            qgis_labels=True)
        self.assertEqual(count, 1)
        self.assertEqual(kml.count("<Placemark>"), 1)
        self.assertNotIn("<name>0</name>", kml)


class TestExtendedData(unittest.TestCase):

    def test_data_fields_become_balloon_rows(self):
        layer = _polygon_fixture(fields="field=nr:string&field=owner:string")
        feat = next(layer.getFeatures())
        layer.dataProvider().changeAttributeValues(
            {feat.id(): {1: "Ion & fiii <SRL>"}})
        kml, _ = kml_writer.layer_to_kml(
            layer, [], QgsProject.instance().transformContext(),
            data_fields=["nr", "owner"])
        self.assertIn('<Data name="nr"><value>0</value></Data>', kml)
        self.assertIn("<value>Ion &amp; fiii &lt;SRL&gt;</value>", kml)

    def test_null_and_unknown_fields_skipped(self):
        layer = _polygon_fixture(fields="field=nr:string&field=owner:string")
        kml, _ = kml_writer.layer_to_kml(
            layer, [], QgsProject.instance().transformContext(),
            data_fields=["nr", "owner", "missing"])
        self.assertIn('<Data name="nr">', kml)
        self.assertNotIn('<Data name="owner">', kml)
        self.assertNotIn("missing", kml)

    def test_no_data_fields_no_extended_data(self):
        layer = _polygon_fixture()
        kml, _ = kml_writer.layer_to_kml(
            layer, [], QgsProject.instance().transformContext())
        self.assertNotIn("<ExtendedData>", kml)

    def test_companion_label_point_carries_the_data_too(self):
        layer = _polygon_fixture()
        kml, _ = kml_writer.layer_to_kml(
            layer, [{"field": "nr", "prefix": "No. ", "suffix": ""}],
            QgsProject.instance().transformContext(),
            data_fields=["nr"])
        self.assertEqual(kml.count("<ExtendedData>"), 2)

    def test_attribute_escaping_covers_quotes(self):
        self.assertEqual(kml_writer.xml_escape_attr('a"b<c>'),
                         "a&quot;b&lt;c&gt;")


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
