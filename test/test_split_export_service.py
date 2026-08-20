# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Filename sanitizing and template rendering, field discovery across layers, split-layer building including CRS survival, and a full per-group DXF export into a temp folder. The export tests count DXF outputs, so ezdxf has to be importable."""

import datetime
import os
import sys
import tempfile
import unittest

from qgis.core import (  # type: ignore
    QgsApplication, QgsCoordinateReferenceSystem, QgsFeature, QgsGeometry,
    QgsVectorLayer,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from vernier.services import split_export_service as svc  # noqa: E402

QGS = None


def setUpModule():
    global QGS
    QGS = QgsApplication([], False)
    QGS.initQgis()


def tearDownModule():
    QGS.exitQgis()


def _polygon_layer(name, sectors, crs="EPSG:32635"):
    """Memory polygon layer with a "sector" field, one square per value."""
    layer = QgsVectorLayer("Polygon?field=sector:string", name, "memory")
    layer.setCrs(QgsCoordinateReferenceSystem(crs))
    feats = []
    for i, sector in enumerate(sectors):
        f = QgsFeature(layer.fields())
        x = i * 20
        f.setGeometry(QgsGeometry.fromWkt(
            f"POLYGON(({x} 0, {x + 10} 0, {x + 10} 10, {x} 10, {x} 0))"))
        f.setAttributes([sector])
        feats.append(f)
    layer.dataProvider().addFeatures(feats)
    return layer


def _config(layer):
    return {
        "layer": layer,
        "stroke_color": (255, 0, 0),
        "stroke_width": 0.3,
        "labels_enabled": False,
        "label_color": (0, 0, 255),
        "label_size_pt": 4.0,
        "adaptive_text": False,
        "fixed_text_size": 1.5,
        "label_fields": [],
        "label_separator": ",",
        "label_newline": False,
        "label_font": "Arial",
    }


class TestSafeFilename(unittest.TestCase):

    def test_illegal_characters_replaced(self):
        self.assertEqual(svc.safe_filename('a<b>:c/d\\e|f?g*h'),
                         "a_b__c_d_e_f_g_h")

    def test_trailing_dots_and_spaces_trimmed(self):
        self.assertEqual(svc.safe_filename("name. "), "name")

    def test_spaces_and_accents_preserved(self):
        self.assertEqual(svc.safe_filename("Grüne Zone (été)"),
                         "Grüne Zone (été)")

    def test_none_and_empty(self):
        self.assertEqual(svc.safe_filename(None), "NULL")
        self.assertEqual(svc.safe_filename(""), "_")

    def test_windows_reserved_names_prefixed(self):
        for bad in ("CON", "nul", "Aux", "COM1", "lpt9", "PRN"):
            self.assertEqual(svc.safe_filename(bad), "_" + bad, bad)
        # only whole-stem matches: these are legal names
        for ok in ("CONTOUR", "AUX1", "COM0", "COM10", "NULL"):
            self.assertEqual(svc.safe_filename(ok), ok, ok)


class TestRenderFilename(unittest.TestCase):

    def test_default_template_is_the_bare_value(self):
        self.assertEqual(svc.render_filename("", "23"), "23")
        self.assertEqual(svc.render_filename(None, "05B"), "05B")

    def test_all_builtin_placeholders(self):
        result = svc.render_filename(
            "{layer}_{field}_{value}_{date}", "7", "parcels", "sector")
        today = datetime.date.today().isoformat()
        self.assertEqual(result, f"parcels_sector_7_{today}")

    def test_none_value_renders_as_null(self):
        self.assertEqual(svc.render_filename("{value}", None), "NULL")

    def test_unknown_placeholder_braces_stripped(self):
        self.assertEqual(svc.render_filename("{value}_{foo}", "1"), "1_foo")

    def test_attribute_placeholder_from_sample_feature(self):
        layer = _polygon_layer("l", ["A"])
        feat = next(layer.getFeatures())
        self.assertEqual(
            svc.render_filename("S_{sector}", "x", sample_feature=feat),
            "S_A")

    def test_illegal_value_characters_sanitized(self):
        self.assertEqual(svc.render_filename("{value}", "a/b"), "a_b")


class TestFieldDiscovery(unittest.TestCase):

    def test_common_fields_preserve_first_layer_order(self):
        a = QgsVectorLayer(
            "Polygon?field=x:string&field=y:string&field=z:string",
            "a", "memory")
        b = QgsVectorLayer(
            "Polygon?field=z:string&field=y:string", "b", "memory")
        self.assertEqual(svc.get_common_fields([a, b]), ["y", "z"])

    def test_no_layers_no_fields(self):
        self.assertEqual(svc.get_common_fields([]), [])

    def test_unique_values_counted_across_layers(self):
        a = _polygon_layer("a", ["1", "1", "2"])
        b = _polygon_layer("b", ["2", "3"])
        counts = svc.get_unique_values([a, b], "sector")
        self.assertEqual(dict(counts), {"1": 2, "2": 2, "3": 1})
        # sorted by value
        self.assertEqual(list(counts.keys()), ["1", "2", "3"])

    def test_layer_without_the_field_is_skipped(self):
        a = _polygon_layer("a", ["1"])
        b = QgsVectorLayer("Polygon?field=other:string", "b", "memory")
        counts = svc.get_unique_values([a, b], "sector")
        self.assertEqual(dict(counts), {"1": 1})


class TestBuildSplitLayer(unittest.TestCase):

    def test_filters_by_value(self):
        source = _polygon_layer("parcels", ["1", "1", "2"])
        split = svc.build_split_layer(source, "sector", "1")
        self.assertEqual(split.featureCount(), 2)
        for f in split.getFeatures():
            self.assertEqual(f["sector"], "1")

    def test_mirrors_fields_and_name(self):
        source = _polygon_layer("parcels", ["1"])
        split = svc.build_split_layer(source, "sector", "1")
        self.assertEqual(split.name(), "parcels")
        self.assertEqual(
            [f.name() for f in split.fields()],
            [f.name() for f in source.fields()])

    def test_custom_crs_survives(self):
        # a custom CRS has an empty authid - a ?crs= URI would lose it
        source = _polygon_layer("parcels", ["1"])
        custom = QgsCoordinateReferenceSystem.fromProj(
            "+proj=tmerc +lat_0=0 +lon_0=25 +k=0.9998 +x_0=500000 "
            "+y_0=0 +ellps=WGS84 +units=m +no_defs")
        self.assertTrue(custom.isValid())
        self.assertEqual(custom.authid(), "")
        source.setCrs(custom)
        split = svc.build_split_layer(source, "sector", "1")
        self.assertTrue(split.crs().isValid())
        self.assertEqual(split.crs(), source.crs())

    def test_missing_field_returns_none(self):
        source = _polygon_layer("parcels", ["1"])
        self.assertIsNone(svc.build_split_layer(source, "nope", "1"))


class _CountingLayer:
    """Delegates to a real layer and counts how many times its features are walked."""

    def __init__(self, layer):
        self._layer = layer
        self.scans = 0

    def getFeatures(self, *args):
        self.scans += 1
        return self._layer.getFeatures(*args)

    def getSelectedFeatures(self, *args):
        self.scans += 1
        return self._layer.getSelectedFeatures(*args)

    def __getattr__(self, name):
        return getattr(self._layer, name)


class TestBucketByField(unittest.TestCase):

    def test_groups_every_feature_by_value(self):
        source = _polygon_layer("parcels", ["1", "1", "2"])
        buckets = svc.bucket_by_field(source, "sector")
        self.assertEqual(sorted(buckets), ["1", "2"])
        self.assertEqual(len(buckets["1"]), 2)
        self.assertEqual(len(buckets["2"]), 1)

    def test_missing_field_gives_nothing(self):
        source = _polygon_layer("parcels", ["1"])
        self.assertEqual(svc.bucket_by_field(source, "nope"), {})

    def test_one_pass_over_the_layer(self):
        source = _CountingLayer(_polygon_layer("parcels", ["1", "2", "3"]))
        svc.bucket_by_field(source, "sector")
        self.assertEqual(source.scans, 1)

    def test_matched_shortcut_agrees_with_a_full_scan(self):
        source = _polygon_layer("parcels", ["1", "1", "2"])
        scanned = svc.build_split_layer(source, "sector", "1")
        bucketed = svc.build_split_layer(
            source, "sector", "1",
            matched=svc.bucket_by_field(source, "sector")["1"])
        self.assertEqual(scanned.featureCount(), bucketed.featureCount())
        self.assertEqual(
            [f.attributes() for f in scanned.getFeatures()],
            [f.attributes() for f in bucketed.getFeatures()])


class TestSplitScalesWithLayerNotGroups(unittest.TestCase):
    """Both callers walk every value, so a per-value scan makes the cost groups x features."""

    def test_filename_preview_reads_each_layer_once(self):
        values = [str(i) for i in range(20)]
        source = _CountingLayer(_polygon_layer("parcels", values))
        names = svc.predict_split_filenames(
            [_config(source)], "sector", values, "{value}")
        self.assertEqual(len(names), 20)
        self.assertEqual(source.scans, 1)

    def test_export_reads_each_layer_once(self):
        values = ["1", "2", "3", "4", "5"]
        source = _CountingLayer(_polygon_layer("parcels", values))
        tmp = tempfile.mkdtemp(prefix="va_split_scan_")
        result = svc.export_split_groups(
            [_config(source)], "sector", values, tmp, "{value}")
        self.assertEqual(result.files_written, 5)
        self.assertEqual(source.scans, 1)


class TestExportSplitGroups(unittest.TestCase):

    def test_one_dxf_per_value(self):
        source = _polygon_layer("parcels", ["1", "1", "2"])
        tmp = tempfile.mkdtemp(prefix="va_split_test_")
        result = svc.export_split_groups(
            [_config(source)], "sector", ["1", "2"], tmp,
            template="{value}")

        self.assertEqual(result.total_groups, 2)
        self.assertEqual(result.files_written, 2)
        self.assertEqual(result.total_errors, 0)
        for name in ("1.dxf", "2.dxf"):
            self.assertTrue(
                os.path.isfile(os.path.join(tmp, name)), name)

    def test_several_layers_share_one_file_per_group(self):
        """Every checked layer goes into the same DXF for a group, as its own DXF layer - one file, not one per source."""
        a = _polygon_layer("parcels", ["1"])
        b = _polygon_layer("buildings", ["1"])
        tmp = tempfile.mkdtemp(prefix="va_split_test_")
        result = svc.export_split_groups(
            [_config(a), _config(b)], "sector", ["1"], tmp,
            template="{value}")
        self.assertEqual(result.files_written, 1)
        self.assertEqual(sorted(os.listdir(tmp)), ["1.dxf"])
        self.assertEqual(result.total_success, 2)

    def test_value_without_features_reports_no_paths(self):
        source = _polygon_layer("parcels", ["1"])
        tmp = tempfile.mkdtemp(prefix="va_split_test_")
        result = svc.export_split_groups(
            [_config(source)], "sector", ["9"], tmp,
            template="{value}")
        self.assertEqual(result.files_written, 0)
        self.assertEqual(result.per_group[0]["paths"], [])
        self.assertIsNotNone(result.per_group[0]["error_message"])

    def test_predicted_filenames_match_written_files(self):
        """The dialog's overwrite check has to see the names the export writes, placeholders included, and those depend on which layer and feature the export picks per group."""
        a = _polygon_layer("parcels", ["1", "2"])
        b = _polygon_layer("buildings", ["2", "3"])
        config = [_config(a), _config(b)]
        template = "{layer}_{sector}_{value}"
        values = ["1", "2", "3", "9"]  # 9 has no features anywhere

        predicted = svc.predict_split_filenames(
            config, "sector", values, template)
        # group 1: only parcels; group 2: parcels first; group 3: buildings
        self.assertEqual(sorted(predicted), sorted([
            "parcels_1_1.dxf",
            "parcels_2_2.dxf",
            "buildings_3_3.dxf",
        ]))

        tmp = tempfile.mkdtemp(prefix="va_split_test_")
        result = svc.export_split_groups(
            config, "sector", values, tmp, template=template)
        written = sorted(
            os.path.basename(p) for g in result.per_group for p in g["paths"])
        self.assertEqual(written, sorted(predicted))

    def test_progress_callback_fires(self):
        source = _polygon_layer("parcels", ["1", "2"])
        tmp = tempfile.mkdtemp(prefix="va_split_test_")
        calls = []
        svc.export_split_groups(
            [_config(source)], "sector", ["1", "2"], tmp,
            template="{value}",
            progress_callback=lambda *a: calls.append(a))
        self.assertTrue(calls)
        self.assertEqual({c[0] for c in calls}, {0, 1})


if __name__ == "__main__":
    unittest.main()
