# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The topology panel's pure row builders. _duplicate_rows turns the pairs check_duplicates reports into what the map layer should carry, which is a different shape from the findings tree."""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import (  # type: ignore
    QgsApplication, QgsFeature, QgsGeometry, QgsVectorLayer, QgsWkbTypes,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from vernier import topology_panel  # noqa: E402
from vernier.services import topology_service  # noqa: E402

SQUARE = "POLYGON((0 0, 2 0, 2 2, 0 2, 0 0))"
SQUARE_ROTATED = "POLYGON((2 0, 2 2, 0 2, 0 0, 2 0))"
SQUARE_FAR = "POLYGON((10 10, 11 10, 11 11, 10 11, 10 10))"
SQUARE_OTHER = "POLYGON((5 5, 6 5, 6 6, 5 6, 5 5))"

QGS = None


def setUpModule():
    global QGS
    QGS = QgsApplication([], True)
    QGS.initQgis()


def tearDownModule():
    QGS.exitQgis()


def _layer(wkts, uri="Polygon?crs=EPSG:3844"):
    layer = QgsVectorLayer(uri, "fixture", "memory")
    features = []
    for wkt in wkts:
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromWkt(wkt))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    return layer


class TestDuplicateRows(unittest.TestCase):

    def test_one_row_per_group_not_per_reported_pair(self):
        # a trio and a pair: 3 errors, but only 2 places on the map
        layer = _layer([SQUARE, SQUARE_ROTATED, SQUARE,
                        SQUARE_FAR, SQUARE_FAR, SQUARE_OTHER])
        errors = topology_service.check_duplicates(layer)
        self.assertEqual(len(errors), 3)
        rows = topology_panel._duplicate_rows(errors)
        self.assertEqual(len(rows), 2)

    def test_row_counts_every_copy_in_the_group(self):
        layer = _layer([SQUARE, SQUARE_ROTATED, SQUARE, SQUARE_FAR, SQUARE_FAR])
        errors = topology_service.check_duplicates(layer)
        rows = topology_panel._duplicate_rows(errors)
        copies = sorted(attributes[2] for _geometry, attributes in rows)
        self.assertEqual(copies, [2, 3])

    def test_identical_geometries_are_not_stacked(self):
        # every copy shares one footprint, so a row per copy would overprint
        # the same translucent fill and shade the trio darker than the pair
        layer = _layer([SQUARE, SQUARE, SQUARE])
        rows = topology_panel._duplicate_rows(
            topology_service.check_duplicates(layer))
        self.assertEqual(len(rows), 1)

    def test_geometry_is_promoted_to_multi_part(self):
        layer = _layer([SQUARE, SQUARE])
        rows = topology_panel._duplicate_rows(
            topology_service.check_duplicates(layer))
        self.assertTrue(QgsWkbTypes.isMultiType(rows[0][0].wkbType()))

    def test_row_carries_the_duplicate_kind(self):
        layer = _layer([SQUARE, SQUARE])
        rows = topology_panel._duplicate_rows(
            topology_service.check_duplicates(layer))
        self.assertEqual(rows[0][1][0], topology_service.KIND_DUPLICATE)

    def test_clean_layer_yields_no_rows(self):
        layer = _layer([SQUARE, SQUARE_FAR])
        self.assertEqual(topology_panel._duplicate_rows(
            topology_service.check_duplicates(layer)), [])


if __name__ == "__main__":
    unittest.main()
