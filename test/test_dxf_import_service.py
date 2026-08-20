# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The geometry-class table behind DXF import. Its uri tokens are magic strings the OGR provider has to recognize - a typo does not raise, it silently drops the filter and hands back a layer typed from whichever entity OGR saw first, so these check every token against a real provider rather than against the spelling in the map."""

import os
import sys
import tempfile
import unittest

_PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _PLUGINS_DIR not in sys.path:
    sys.path.insert(0, _PLUGINS_DIR)

from osgeo import ogr  # noqa: E402  # type: ignore
from qgis.core import (  # noqa: E402  # type: ignore
    QgsApplication, QgsVectorLayer, QgsWkbTypes,
)

from vernier.services.dxf_import_service import (  # noqa: E402
    _GEOM_CLASSES, geom_style,
)

QGS = None
GPKG = None

# one mixed table, like the entities table ogr2ogr writes from a drawing. the point is stored first
# and flat, which is what used to type every imported layer as a flat point layer
_ROWS = [
    ("PCT", "POINT (1 1)"),
    ("PLAN", "LINESTRING (0 0, 10 0)"),
    ("PLANM", "MULTILINESTRING ((0 0, 1 1), (2 2, 3 3))"),
    ("PARCELA", "POLYGON ((0 0, 5 0, 5 5, 0 0))"),
    ("PARCELAM", "MULTIPOLYGON (((0 0, 5 0, 5 5, 0 0)))"),
    ("PCTM", "MULTIPOINT ((7 7))"),
    ("COTE", "LINESTRING Z (0 0 12.5, 10 0 13.25)"),
]


def setUpModule():
    global QGS, GPKG
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QGS = QgsApplication([], False)
    QGS.initQgis()

    GPKG = os.path.join(tempfile.mkdtemp(prefix="va_dxfimp_"), "entities.gpkg")
    ds = ogr.GetDriverByName("GPKG").CreateDataSource(GPKG)
    layer = ds.CreateLayer("entities", geom_type=ogr.wkbUnknown)
    layer.CreateField(ogr.FieldDefn("Layer", ogr.OFTString))
    for cad_layer, wkt in _ROWS:
        feat = ogr.Feature(layer.GetLayerDefn())
        feat.SetField("Layer", cad_layer)
        feat.SetGeometry(ogr.CreateGeometryFromWkt(wkt))
        layer.CreateFeature(feat)
    ds = None


def tearDownModule():
    QGS.exitQgis()


def _layer(token, subset=None):
    uri = f"{GPKG}|layername=entities"
    if token:
        uri += f"|geometrytype={token}"
    lyr = QgsVectorLayer(uri, "probe", "ogr")
    if subset:
        lyr.setSubsetString(subset)
    return lyr


class TestGeometryTokens(unittest.TestCase):

    def test_every_token_is_a_name_the_provider_knows(self):
        for ogr_name, (_qml, token) in _GEOM_CLASSES.items():
            with self.subTest(ogr_name):
                lyr = _layer(token)
                self.assertTrue(lyr.isValid(), f"{token} gave an invalid layer")
                # an unknown name is ignored rather than rejected, so the proof is that it filtered
                self.assertNotEqual(
                    lyr.wkbType(), QgsWkbTypes.Unknown,
                    f"{token} did not resolve to a concrete type")

    def test_each_token_keeps_its_own_geometry_class(self):
        expected = {
            "POINT": QgsWkbTypes.GeometryType.PointGeometry,
            "MULTIPOINT": QgsWkbTypes.GeometryType.PointGeometry,
            "LINESTRING": QgsWkbTypes.GeometryType.LineGeometry,
            "MULTILINESTRING": QgsWkbTypes.GeometryType.LineGeometry,
            "POLYGON": QgsWkbTypes.GeometryType.PolygonGeometry,
            "MULTIPOLYGON": QgsWkbTypes.GeometryType.PolygonGeometry,
        }
        for ogr_name, (_qml, token) in _GEOM_CLASSES.items():
            with self.subTest(ogr_name):
                self.assertEqual(_layer(token).geometryType(),
                                 expected[ogr_name])

    def test_tokens_declare_z_so_exports_keep_elevations(self):
        # CAD geometry is 2.5D, and QgsVectorFileWriter builds an export from the declared type
        for ogr_name, (_qml, token) in _GEOM_CLASSES.items():
            with self.subTest(ogr_name):
                self.assertTrue(QgsWkbTypes.hasZ(_layer(token).wkbType()),
                                f"{token} declared a 2D layer")

    def test_the_token_filters_a_mixed_table(self):
        # without it the linework comes back typed as the point that happens to sit first
        _, _, token = geom_style("LINESTRING", 1, 0)
        plain = _layer("", "\"Layer\" = 'PLAN'")
        filtered = _layer(token, "\"Layer\" = 'PLAN'")
        self.assertEqual(plain.geometryType(), QgsWkbTypes.GeometryType.PointGeometry)
        self.assertEqual(filtered.geometryType(), QgsWkbTypes.GeometryType.LineGeometry)
        self.assertEqual(filtered.featureCount(), 1)

    def test_an_unlisted_class_asks_for_no_filter(self):
        gtype, is_text, token = geom_style("CIRCULARSTRING", 5, 0)
        self.assertEqual(gtype, "lines")
        self.assertFalse(is_text)
        self.assertEqual(token, "")

    def test_a_mostly_text_point_group_still_filters_as_points(self):
        gtype, is_text, token = geom_style("POINT", 10, 8)
        self.assertEqual(gtype, "texts")
        self.assertTrue(is_text)
        self.assertEqual(token, _GEOM_CLASSES["POINT"][1])


if __name__ == "__main__":
    unittest.main()
