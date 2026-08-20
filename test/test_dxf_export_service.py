# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The pure helpers plus an ezdxf round-trip on memory layers - write a DXF, read it back, check entities, colors, lineweights and label text. Label placement goes through QgsGeometry, the rotation and height math through bare coordinate rings, and the round-trip classes import ezdxf per test so a QGIS python without it only fails those."""

import os
import sys
import tempfile
import unittest

from qgis.core import (  # type: ignore
    QgsApplication, QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem, QgsFeature, QgsField, QgsFields,
    QgsFillSymbol, QgsGeometry, QgsRendererCategory, QgsVectorLayer,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from vernier.qt_compat import FIELD_STRING  # noqa: E402
from vernier.services import dxf_export_service as svc  # noqa: E402

QGS = None


def setUpModule():
    global QGS
    QGS = QgsApplication([], False)
    QGS.initQgis()


def tearDownModule():
    QGS.exitQgis()


def _feature(values):
    fields = QgsFields()
    for i in range(len(values)):
        fields.append(QgsField(chr(ord("a") + i), FIELD_STRING))
    feature = QgsFeature(fields)
    feature.setAttributes(list(values))
    return feature


def _memory_layer(geom_str, name, wkts, attr="7"):
    layer = QgsVectorLayer(f"{geom_str}?field=nr:string", name, "memory")
    layer.setCrs(QgsCoordinateReferenceSystem("EPSG:32635"))
    feats = []
    for wkt in wkts:
        f = QgsFeature(layer.fields())
        f.setGeometry(QgsGeometry.fromWkt(wkt))
        f.setAttributes([attr])
        feats.append(f)
    layer.dataProvider().addFeatures(feats)
    return layer


def _config(layer, **overrides):
    cfg = {
        "layer": layer,
        "stroke_color": (255, 0, 0),
        "stroke_width": 0.5,
        "labels_enabled": False,
        "label_color": (0, 0, 255),
        "label_size_pt": 4.0,
        "adaptive_text": False,
        "fixed_text_size": 2.0,
        "label_fields": [],
        "label_separator": ",",
        "label_newline": False,
        "label_font": "Arial",
    }
    cfg.update(overrides)
    return cfg


class TestSnapLineweight(unittest.TestCase):

    def test_exact_values_pass_through(self):
        self.assertEqual(svc.snap_lineweight(0.25), 25)
        self.assertEqual(svc.snap_lineweight(2.11), 211)

    def test_in_between_snaps_to_nearest(self):
        self.assertEqual(svc.snap_lineweight(0.26), 25)
        self.assertEqual(svc.snap_lineweight(0.28), 30)

    def test_above_the_table_caps_at_max(self):
        self.assertEqual(svc.snap_lineweight(3.0), 211)

    def test_zero_and_negative_mean_default(self):
        self.assertEqual(svc.snap_lineweight(0.0), 0)
        self.assertEqual(svc.snap_lineweight(-1.0), 0)


class TestRgbToAci(unittest.TestCase):

    def test_primaries(self):
        self.assertEqual(svc.rgb_to_aci(255, 0, 0), 1)
        self.assertEqual(svc.rgb_to_aci(0, 0, 255), 5)

    def test_black_and_white_are_seven(self):
        self.assertEqual(svc.rgb_to_aci(0, 0, 0), 7)
        self.assertEqual(svc.rgb_to_aci(255, 255, 255), 7)

    def test_rgb_to_int_packs(self):
        self.assertEqual(svc.rgb_to_int(255, 0, 0), 0xFF0000)
        self.assertEqual(svc.rgb_to_int(1, 2, 3), 0x010203)


class TestSafeDxfLayerName(unittest.TestCase):

    def test_free_name_unchanged(self):
        self.assertEqual(svc.safe_dxf_layer_name("parcels", set()), "parcels")

    def test_collision_gets_suffix(self):
        self.assertEqual(
            svc.safe_dxf_layer_name("parcels", {"parcels"}), "parcels_1")
        self.assertEqual(
            svc.safe_dxf_layer_name("parcels", {"parcels", "parcels_1"}),
            "parcels_2")

    def test_long_name_truncated_within_limit(self):
        long_name = "x" * 300
        result = svc.safe_dxf_layer_name(long_name, set())
        self.assertEqual(len(result), 255)
        collided = svc.safe_dxf_layer_name(long_name, {result})
        self.assertLessEqual(len(collided), 255)
        self.assertTrue(collided.endswith("_1"))

    def test_empty_name_falls_back(self):
        self.assertEqual(svc.safe_dxf_layer_name("", set()), "layer")


class TestBuildLabelText(unittest.TestCase):

    def test_comma_separator_auto_padded(self):
        feat = _feature(["12A", "350"])
        fields = [
            {"field": "a", "prefix": "No. ", "suffix": ""},
            {"field": "b", "prefix": "", "suffix": " sqm"},
        ]
        self.assertEqual(
            svc.build_label_text(feat, fields, ",", False),
            "No. 12A, 350 sqm")

    def test_newline_mode_ignores_separator(self):
        feat = _feature(["12A", "350"])
        fields = [
            {"field": "a", "prefix": "", "suffix": ""},
            {"field": "b", "prefix": "", "suffix": ""},
        ]
        self.assertEqual(
            svc.build_label_text(feat, fields, ",", True), "12A\n350")

    def test_separator_with_spaces_respected(self):
        feat = _feature(["1", "2"])
        fields = [
            {"field": "a", "prefix": "", "suffix": ""},
            {"field": "b", "prefix": "", "suffix": ""},
        ]
        self.assertEqual(
            svc.build_label_text(feat, fields, " | ", False), "1 | 2")

    def test_empty_values_skipped(self):
        feat = _feature(["12A", None])
        fields = [
            {"field": "a", "prefix": "", "suffix": ""},
            {"field": "b", "prefix": "", "suffix": ""},
        ]
        self.assertEqual(
            svc.build_label_text(feat, fields, ",", False), "12A")

    def test_all_empty_gives_empty_string(self):
        feat = _feature([None, ""])
        fields = [
            {"field": "a", "prefix": "x", "suffix": ""},
            {"field": "b", "prefix": "", "suffix": "y"},
        ]
        self.assertEqual(svc.build_label_text(feat, fields, ",", False), "")


class TestGeometryHelpers(unittest.TestCase):

    def test_label_point_centers_a_square(self):
        geom = QgsGeometry.fromWkt("POLYGON((0 0, 10 0, 10 10, 0 10, 0 0))")
        x, y, clearance = svc._label_point(geom)
        self.assertAlmostEqual(x, 5.0, delta=1.5)
        self.assertAlmostEqual(y, 5.0, delta=1.5)
        self.assertAlmostEqual(clearance, 5.0, delta=0.5)

    def test_label_point_stays_out_of_the_hole(self):
        """The centroid of a ring parcel sits in the hole, so the label point has to come from something that accounts for interior rings."""
        donut = QgsGeometry.fromWkt(
            "POLYGON((0 0, 100 0, 100 100, 0 100, 0 0),"
            "(20 20, 80 20, 80 80, 20 80, 20 20))")
        self.assertFalse(donut.contains(donut.centroid()))
        x, y, clearance = svc._label_point(donut)
        self.assertTrue(
            donut.contains(QgsGeometry.fromWkt(f"POINT({x} {y})")),
            f"label at ({x}, {y}) is not inside the ring")
        # the annulus is 20 wide and widens at the corners, so the circle that
        # fits has r ~11.7 - the point is that it is annulus-scale and not the
        # r=50 the same outline would give if the hole were ignored
        self.assertLess(clearance, 15.0)

    def test_label_point_falls_back_on_a_degenerate_polygon(self):
        """QGIS seeds poleOfInaccessibility's distance with DBL_MAX and leaves it there when the algorithm cannot run, so an unsanitised clearance would reach the text-height math."""
        flat = QgsGeometry.fromWkt("POLYGON((0 0, 10 0, 10 0, 0 0, 0 0))")
        x, y, clearance = svc._label_point(flat)
        self.assertTrue(all(v == v for v in (x, y)))  # not NaN
        self.assertEqual(clearance, 0.0)

    def test_label_point_uses_the_dominant_part(self):
        geom = QgsGeometry.fromWkt(
            "MULTIPOLYGON(((0 0, 1 0, 1 1, 0 1, 0 0)),"
            "((100 100, 200 100, 200 200, 100 200, 100 100)))")
        x, y, _clearance = svc._label_point(geom)
        self.assertGreater(x, 99.0)
        self.assertGreater(y, 99.0)

    def test_adaptive_height_capped_by_the_fitting_circle(self):
        """The ring dimensions describe the outline, so on a ring parcel they size the text as if the hole were solid. Only the clearance term accounts for it."""
        outline = [(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)]
        without = svc._adaptive_height("AB12", outline)
        with_hole = svc._adaptive_height("AB12", outline, clearance=10.0)
        self.assertLess(with_hole, without)
        self.assertLessEqual(with_hole, 20.0)

    def test_adaptive_height_unchanged_on_an_ordinary_parcel(self):
        # a convex polygon's fitting circle is generous, so the cap must not bite
        outline = [(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)]
        _x, _y, clearance = svc._label_point(
            QgsGeometry.fromWkt("POLYGON((0 0, 100 0, 100 100, 0 100, 0 0))"))
        self.assertEqual(svc._adaptive_height("AB12", outline),
                         svc._adaptive_height("AB12", outline, clearance))

    def test_pca_angle_follows_the_long_axis(self):
        horizontal = [(0, 0), (10, 0), (10, 1), (0, 1), (0, 0)]
        self.assertAlmostEqual(svc._pca_angle(horizontal), 0.0, delta=2.0)
        vertical = [(0, 0), (1, 0), (1, 10), (0, 10), (0, 0)]
        self.assertAlmostEqual(abs(svc._pca_angle(vertical)), 90.0, delta=2.0)

    def test_pca_angle_diagonal_with_equal_variances(self):
        # rectangle along y=x with a point set symmetric under the x/y swap, so xx == yy exactly and the angle has to come from xy alone rather than collapse to 0
        diagonal = [(-1, 1), (1, -1), (11, 9), (9, 11)]
        self.assertAlmostEqual(svc._pca_angle(diagonal), 45.0, delta=0.1)

    def test_adaptive_height_fits_the_polygon(self):
        ring = [(0, 0), (10, 0), (10, 4), (0, 4), (0, 0)]
        h = svc._adaptive_height("AB", ring)
        self.assertGreaterEqual(h, 0.3)
        self.assertLessEqual(h, 2.5)

    def test_get_ring_multipart_prefers_largest_part(self):
        # sliver part listed first, the label ring has to come from the dominant part and not whichever one leads the collection
        geom = QgsGeometry.fromWkt(
            "MULTIPOLYGON(((100 100, 100.1 100, 100.1 100.1,"
            " 100 100.1, 100 100)),"
            "((0 0, 10 0, 10 10, 0 10, 0 0)))")
        ring = svc._get_ring(geom)
        self.assertIsNotNone(ring)
        self.assertLessEqual(max(x for x, _ in ring), 10.0)


class TestReadLayerStyle(unittest.TestCase):

    def test_categorized_renderer_supplies_stroke_color(self):
        # categorized and graduated renderers have no .symbol(), so the style reader has to fall back to symbols(context) instead of the gray default
        layer = _memory_layer(
            "Polygon", "cat", ["POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"])
        symbol = QgsFillSymbol.createSimple(
            {"outline_color": "255,140,0", "outline_width": "0.66"})
        renderer = QgsCategorizedSymbolRenderer(
            "nr", [QgsRendererCategory("7", symbol, "seven")])
        layer.setRenderer(renderer)
        style = svc.read_layer_style(layer)
        self.assertEqual(style["stroke_color"], (255, 140, 0))
        self.assertAlmostEqual(style["stroke_width"], 0.66)


class TestExportRoundTrip(unittest.TestCase):
    """Write a DXF with ezdxf, read it back with ezdxf."""

    def _export(self, configs):
        tmp = tempfile.mkdtemp(prefix="va_dxf_test_")
        path = os.path.join(tmp, "out.dxf")
        result = svc.export_layers_to_dxf(configs, path)
        return path, result

    def test_polygon_with_hole_labels_and_colors(self):
        import ezdxf
        layer = _memory_layer(
            "Polygon", "parcels",
            ["POLYGON((0 0, 20 0, 20 20, 0 20, 0 0),"
             "(5 5, 8 5, 8 8, 5 8, 5 5))"])
        cfg = _config(
            layer,
            labels_enabled=True,
            label_fields=[{"field": "nr", "prefix": "No. ", "suffix": ""}],
        )
        path, (ok, skip, err) = self._export([cfg])
        self.assertEqual((ok, skip, err), (1, 0, 0))

        doc = ezdxf.readfile(path)
        self.assertEqual(doc.header["$INSUNITS"], svc.DXF_INSUNITS)
        self.assertEqual(doc.header["$DWGCODEPAGE"], svc.DXF_CODEPAGE)

        lo = doc.layers.get("parcels")
        self.assertEqual(tuple(lo.rgb), (255, 0, 0))
        self.assertEqual(lo.color, 1)  # nearest ACI for red

        msp = doc.modelspace()
        polys = msp.query("LWPOLYLINE")
        self.assertEqual(len(polys), 2)  # exterior + hole
        for e in polys:
            self.assertEqual(e.dxf.layer, "parcels")
            self.assertTrue(e.closed)
            self.assertEqual(e.dxf.true_color, 0xFF0000)
            self.assertEqual(e.dxf.lineweight, 50)  # 0.5 mm

        texts = msp.query("TEXT")
        self.assertEqual(len(texts), 1)
        text = texts[0]
        self.assertEqual(text.dxf.text, "No. 7")
        self.assertAlmostEqual(text.dxf.height, 2.0)
        self.assertEqual(text.dxf.true_color, 0x0000FF)
        # label lands inside the polygon
        x, y = text.dxf.insert.x, text.dxf.insert.y
        self.assertTrue(0 < x < 20 and 0 < y < 20)

    def test_line_and_point_entities(self):
        import ezdxf
        line_layer = _memory_layer(
            "LineString", "roads", ["LINESTRING(0 0, 10 0, 10 5)"])
        point_layer = _memory_layer(
            "Point", "poles", ["POINT(3 4)"])
        path, (ok, skip, err) = self._export([
            _config(line_layer, stroke_color=(0, 255, 0)),
            _config(point_layer, stroke_color=(0, 0, 255)),
        ])
        self.assertEqual((ok, skip, err), (2, 0, 0))

        doc = ezdxf.readfile(path)
        msp = doc.modelspace()
        lines = msp.query("LWPOLYLINE")
        self.assertEqual(len(lines), 1)
        self.assertFalse(lines[0].closed)
        self.assertEqual(lines[0].dxf.true_color, 0x00FF00)
        self.assertEqual(len(lines[0]), 3)

        points = msp.query("POINT")
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0].dxf.layer, "poles")
        self.assertAlmostEqual(points[0].dxf.location.x, 3.0)
        self.assertAlmostEqual(points[0].dxf.location.y, 4.0)

    def test_multiline_label_stacks_text_entities(self):
        import ezdxf
        layer = QgsVectorLayer(
            "Polygon?field=nr:string&field=area:string", "plots", "memory")
        layer.setCrs(QgsCoordinateReferenceSystem("EPSG:32635"))
        f = QgsFeature(layer.fields())
        f.setGeometry(QgsGeometry.fromWkt(
            "POLYGON((0 0, 30 0, 30 30, 0 30, 0 0))"))
        f.setAttributes(["12", "450"])
        layer.dataProvider().addFeatures([f])

        cfg = _config(
            layer,
            labels_enabled=True,
            label_newline=True,
            label_fields=[
                {"field": "nr", "prefix": "", "suffix": ""},
                {"field": "area", "prefix": "", "suffix": " sqm"},
            ],
        )
        path, (ok, skip, err) = self._export([cfg])
        self.assertEqual(err, 0)

        doc = ezdxf.readfile(path)
        texts = doc.modelspace().query("TEXT")
        self.assertEqual(len(texts), 2)
        contents = {t.dxf.text for t in texts}
        self.assertEqual(contents, {"12", "450 sqm"})
        # stacked vertically: same x, different y
        ys = sorted(t.dxf.insert.y for t in texts)
        self.assertGreater(ys[1] - ys[0], 0.1)

    def test_elongated_polygon_rotates_its_label(self):
        import ezdxf
        layer = _memory_layer(
            "Polygon", "strips",
            ["POLYGON((0 0, 3 0, 3 40, 0 40, 0 0))"])
        cfg = _config(
            layer,
            labels_enabled=True,
            label_fields=[{"field": "nr", "prefix": "", "suffix": ""}],
        )
        path, _counts = self._export([cfg])
        doc = ezdxf.readfile(path)
        texts = doc.modelspace().query("TEXT")
        self.assertEqual(len(texts), 1)
        self.assertAlmostEqual(abs(texts[0].dxf.rotation), 90.0, delta=3.0)

    def test_empty_geometry_is_skipped(self):
        layer = QgsVectorLayer("Polygon?field=nr:string", "empty", "memory")
        layer.setCrs(QgsCoordinateReferenceSystem("EPSG:32635"))
        f = QgsFeature(layer.fields())
        f.setAttributes(["1"])  # no geometry
        layer.dataProvider().addFeatures([f])
        path, (ok, skip, err) = self._export([_config(layer)])
        self.assertEqual((ok, skip, err), (0, 1, 0))

    def test_duplicate_layer_names_get_separate_dxf_layers(self):
        import ezdxf
        a = _memory_layer("Polygon", "same",
                          ["POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"])
        b = _memory_layer("Polygon", "same",
                          ["POLYGON((5 5, 6 5, 6 6, 5 6, 5 5))"])
        path, (ok, skip, err) = self._export([
            _config(a), _config(b, stroke_color=(0, 255, 0))])
        self.assertEqual((ok, skip, err), (2, 0, 0))
        doc = ezdxf.readfile(path)
        names = {lyr.dxf.name for lyr in doc.layers}
        self.assertIn("same", names)
        self.assertIn("same_1", names)


if __name__ == "__main__":
    unittest.main()
