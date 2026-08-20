# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Key handling for the two join dialogs. Join keys turn up as int, float, string, bool, NULL QVariant or NaN depending on the provider, so TestNormalizeKey pins the equivalences and what counts as missing."""

import os
import sys
import unittest

from qgis.PyQt.QtCore import QVariant  # type: ignore
from qgis.core import (  # type: ignore
    QgsApplication, QgsFeature, QgsField, QgsVectorLayer,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from vernier.qt_compat import FIELD_DOUBLE, FIELD_INT, FIELD_STRING  # noqa: E402
from vernier.services.join_service import (  # noqa: E402
    build_key_map, count_matches, dedupe_preserve_order,
    is_missing, key_set, normalize_key,
)

QGS = None


def setUpModule():
    global QGS
    QGS = QgsApplication([], False)
    QGS.initQgis()


def tearDownModule():
    QGS.exitQgis()


def _table_layer(key_type, rows):
    """Geometry-less layer with a code field of key_type and a string name, built from [(code, name), ...] where None stays NULL."""
    layer = QgsVectorLayer("NoGeometry", "fixture", "memory")
    layer.dataProvider().addAttributes([
        QgsField("code", key_type), QgsField("name", FIELD_STRING)])
    layer.updateFields()
    features = []
    for code, name in rows:
        feature = QgsFeature(layer.fields())
        feature.setAttributes([code, name])
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    return layer


class TestNormalizeKey(unittest.TestCase):

    def test_int(self):
        self.assertEqual(normalize_key(1), "1")

    def test_integral_float_matches_int(self):
        self.assertEqual(normalize_key(1.0), normalize_key(1))

    def test_integral_float_matches_string(self):
        self.assertEqual(normalize_key(1.0), normalize_key("1"))

    def test_fractional_float(self):
        self.assertEqual(normalize_key(1.5), "1.5")

    def test_string_stripped(self):
        self.assertEqual(normalize_key("  A17 "), "A17")

    def test_empty_string_is_missing(self):
        self.assertIsNone(normalize_key(""))

    def test_whitespace_only_is_missing(self):
        self.assertIsNone(normalize_key("   "))

    def test_none_is_missing(self):
        self.assertIsNone(normalize_key(None))

    def test_null_qvariant_is_missing(self):
        self.assertIsNone(normalize_key(QVariant()))

    def test_wrapped_qvariant_unwrapped(self):
        self.assertEqual(normalize_key(QVariant(7)), "7")

    def test_nan_is_missing(self):
        self.assertIsNone(normalize_key(float("nan")))

    def test_bool_normalizes_as_int(self):
        self.assertEqual(normalize_key(True), "1")


class TestIsMissing(unittest.TestCase):

    def test_none(self):
        self.assertTrue(is_missing(None))

    def test_null_qvariant(self):
        self.assertTrue(is_missing(QVariant()))

    def test_zero_is_present(self):
        self.assertFalse(is_missing(0))

    def test_empty_string_is_present(self):
        # blank-string policy belongs to normalize_key, not is_missing
        self.assertFalse(is_missing(""))


class TestKeyHelpers(unittest.TestCase):

    def test_build_key_map_normalizes_double_keys(self):
        source = _table_layer(FIELD_DOUBLE,
                              [(1.0, "a"), (2.0, "b"), (None, "c")])
        data = build_key_map(source, "code", ["name"])
        self.assertEqual(set(data), {"1", "2"})
        self.assertEqual(data["1"]["name"], "a")

    def test_build_key_map_later_feature_wins(self):
        source = _table_layer(FIELD_INT, [(1, "first"), (1, "second")])
        data = build_key_map(source, "code", ["name"])
        self.assertEqual(data["1"]["name"], "second")

    def test_key_set_excludes_missing(self):
        source = _table_layer(FIELD_STRING,
                              [("A", "x"), ("", "y"), (None, "z")])
        self.assertEqual(key_set(source, "code"), {"A"})

    def test_count_matches_across_types(self):
        # float source keys against string target keys, the DBF numeric-field silent-miss scenario
        source = _table_layer(FIELD_DOUBLE, [(1.0, "a"), (3.0, "b")])
        target = _table_layer(FIELD_STRING,
                              [("1", "t1"), ("2", "t2"), ("3", "t3")])
        keys = key_set(source, "code")
        self.assertEqual(count_matches(target, "code", keys), 2)

    def test_count_matches_skips_missing_target_keys(self):
        source = _table_layer(FIELD_STRING, [("A", "a")])
        target = _table_layer(FIELD_STRING, [(None, "x"), ("A", "y")])
        keys = key_set(source, "code")
        self.assertEqual(count_matches(target, "code", keys), 1)


class TestDedupe(unittest.TestCase):

    def test_order_preserved(self):
        self.assertEqual(dedupe_preserve_order(["b", "a", "b", "c", "a"]),
                         ["b", "a", "c"])

    def test_empty(self):
        self.assertEqual(dedupe_preserve_order([]), [])


if __name__ == "__main__":
    unittest.main()
