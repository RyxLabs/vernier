# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The pure DXF-to-QGIS style mapping - ACI lookups, lineweight conversion, linetype-to-dash families, and the generated QML with its scale thresholds and label caps. No file I/O and no ezdxf, everything asserts on the XML."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from vernier.services import dxf_symbology  # noqa: E402


class TestAciRgb(unittest.TestCase):

    def test_primary_colors(self):
        self.assertEqual(dxf_symbology.aci_rgb(1), (255, 0, 0))
        self.assertEqual(dxf_symbology.aci_rgb(3), (0, 255, 0))
        self.assertEqual(dxf_symbology.aci_rgb(5), (0, 0, 255))

    def test_negative_aci_means_layer_off_but_same_color(self):
        # ezdxf reports a layer switched off as a negative color index
        self.assertEqual(dxf_symbology.aci_rgb(-3), (0, 255, 0))

    def test_out_of_table_falls_back_to_grey(self):
        self.assertEqual(dxf_symbology.aci_rgb(999), (128, 128, 128))

    def test_full_palette_is_present(self):
        self.assertEqual(len(dxf_symbology.ACI), 256)
        for rgb in dxf_symbology.ACI.values():
            self.assertEqual(len(rgb), 3)
            for c in rgb:
                self.assertTrue(0 <= c <= 255)


class TestLwMm(unittest.TestCase):

    def test_known_values(self):
        self.assertEqual(dxf_symbology.lw_mm(25), 0.25)
        self.assertEqual(dxf_symbology.lw_mm(211), 2.11)
        self.assertEqual(dxf_symbology.lw_mm(0), 0.05)

    def test_default_lineweights_map_to_qgis_default(self):
        # -3 = BYLAYER default in the DXF layer table
        self.assertEqual(dxf_symbology.lw_mm(-3), 0.25)

    def test_unknown_value_falls_back(self):
        self.assertEqual(dxf_symbology.lw_mm(999), 0.25)


class TestLtDash(unittest.TestCase):

    def test_continuous_is_solid(self):
        self.assertEqual(dxf_symbology.lt_dash("Continuous"), "")
        self.assertEqual(dxf_symbology.lt_dash("ByLayer"), "")

    def test_dashed_families(self):
        self.assertEqual(dxf_symbology.lt_dash("DASHED"), "4;2")
        self.assertEqual(dxf_symbology.lt_dash("HIDDEN"), "4;2")
        self.assertEqual(dxf_symbology.lt_dash("DASHED2"), "2;1")
        self.assertEqual(dxf_symbology.lt_dash("hidden2"), "2;1")

    def test_dot_and_dashdot(self):
        self.assertEqual(dxf_symbology.lt_dash("DOT"), "1;3")
        self.assertEqual(dxf_symbology.lt_dash("DASHDOT2"), "4;1;1;1")
        self.assertEqual(dxf_symbology.lt_dash("DASHDOT"), "8;2;2;2")

    def test_center_and_phantom(self):
        self.assertEqual(dxf_symbology.lt_dash("CENTER"), "24;3;6;3")
        self.assertEqual(dxf_symbology.lt_dash("PHANTOM2"), "8;2;2;2;2;2")


class TestMakeQml(unittest.TestCase):

    def test_line_qml_carries_color_and_width(self):
        qml = dxf_symbology.make_qml("lines", 255, 0, 0, width_mm=0.5)
        self.assertIn('value="255,0,0,255"', qml)
        self.assertIn('value="0.500"', qml)
        self.assertIn('value="solid"', qml)
        self.assertIn('value="0"', qml)  # use_custom_dash off

    def test_line_qml_with_dash(self):
        qml = dxf_symbology.make_qml("lines", 0, 0, 0, dash="4;2")
        self.assertIn('value="customdash"', qml)
        self.assertIn('name="customdash" type="QString" value="4;2"', qml)

    def test_polygon_qml_has_transparent_fill(self):
        qml = dxf_symbology.make_qml("polygons", 10, 20, 30)
        self.assertIn('value="10,20,30,30"', qml)    # fill, alpha 30
        self.assertIn('value="10,20,30,255"', qml)   # outline, opaque

    def test_point_scale_thresholds(self):
        qml = dxf_symbology.make_qml("points", 0, 0, 0, feature_count=100)
        self.assertIn('minScale="10000"', qml)
        qml_dense = dxf_symbology.make_qml(
            "points", 0, 0, 0, feature_count=20000)
        self.assertIn('minScale="5000"', qml_dense)

    def test_text_scale_thresholds_and_label_caps(self):
        qml = dxf_symbology.make_qml("texts", 0, 0, 0, feature_count=100)
        self.assertIn('minScale="5000"', qml)
        self.assertIn('maxNumLabels="2000"', qml)
        self.assertIn('displayAll="0"', qml)
        self.assertIn('fieldName="Text"', qml)
        qml_dense = dxf_symbology.make_qml(
            "texts", 0, 0, 0, feature_count=20000)
        self.assertIn('minScale="3000"', qml_dense)

    def test_text_qml_uses_dxf_height_and_rotation(self):
        qml = dxf_symbology.make_qml("texts", 0, 0, 0)
        self.assertIn("text_height", qml)
        self.assertIn("rotation", qml)


if __name__ == "__main__":
    unittest.main()
