# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""detach_by_areas geometry partitioning. The fixtures walk up in difficulty from an axis-aligned rectangle to a true multipart input, millimeter mode proves vertices land on the mm grid while areas still hit their targets, and TestConservation checks the pieces sum back to the original polygon."""

import os
import sys
import unittest

from qgis.core import QgsApplication, QgsGeometry, QgsPointXY  # type: ignore

_PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, _PLUGINS_DIR)

from vernier.services.detach_service import (  # noqa: E402
    DetachError, detach_by_areas,
)

# 100 x 50 rectangle, area 5000
RECT = "POLYGON((0 0, 100 0, 100 50, 0 50, 0 0))"

# U opening upward, a 30x30 block minus a 10x20 notch, area 700. the arms sit at x in [0,10] and [20,30], y in [10,30]
U_SHAPE = ("POLYGON((0 0, 30 0, 30 30, 20 30, 20 10, 10 10, 10 30, "
           "0 30, 0 0))")

# 40x40 square with a centered 20x20 hole, area 1200
DONUT = ("POLYGON((0 0, 40 0, 40 40, 0 40, 0 0),"
         "(10 10, 30 10, 30 30, 10 30, 10 10))")

# two 10x10 squares 10 apart, total area 200
MULTI = ("MULTIPOLYGON(((0 0, 10 0, 10 10, 0 10, 0 0)),"
         "((20 0, 30 0, 30 10, 20 10, 20 0)))")

# same 100x50 rectangle at realistic projected-CRS coordinate magnitudes
BIG_RECT = ("POLYGON((500000 300000, 500100 300000, 500100 300050, "
            "500000 300050, 500000 300000))")

# irregular convex hexagon around (500000, 300000), area 13950
BIG_HEX = ("POLYGON((500000 300000, 500080 299990, 500150 300030, "
           "500140 300090, 500060 300110, 499990 300060, 500000 300000))")

# self-intersecting bowtie: invalid, makeValid gives two triangles, area 50
BOWTIE = "POLYGON((0 0, 10 10, 10 0, 0 10, 0 0))"

QGS = None


def setUpModule():
    global QGS
    QGS = QgsApplication([], False)
    QGS.initQgis()


def tearDownModule():
    QGS.exitQgis()


def _geom(wkt):
    geometry = QgsGeometry.fromWkt(wkt)
    assert not geometry.isNull(), f"fixture WKT did not parse: {wkt}"
    return geometry


def _line(x0, y0, x1, y1):
    return (QgsPointXY(x0, y0), QgsPointXY(x1, y1))


class TestRectangle(unittest.TestCase):

    def test_axis_aligned_two_targets_plus_remainder(self):
        result = detach_by_areas(_geom(RECT), _line(0, 0, 0, 50),
                                 [1000.0, 2000.0], tolerance=1e-6)
        self.assertEqual(len(result.pieces), 3)
        self.assertAlmostEqual(result.total_area, 5000.0, places=6)
        self.assertFalse(result.fixed_input)

        first, second, remainder = result.pieces
        self.assertEqual(first.target, 1000.0)
        self.assertEqual(first.index, 0)
        self.assertLessEqual(abs(first.area - 1000.0), 1e-6)
        self.assertEqual(second.target, 2000.0)
        self.assertEqual(second.index, 1)
        self.assertLessEqual(abs(second.area - 2000.0), 1e-6)
        self.assertIsNone(remainder.target)
        self.assertEqual(remainder.index, 2)
        self.assertLessEqual(abs(remainder.area - 2000.0), 5e-6)

    def test_diagonal_direction_line(self):
        # drawn diagonally across the rectangle, so the cuts aren't axis-aligned but the targets still have to be met
        result = detach_by_areas(_geom(RECT), _line(0, 0, 30, 40),
                                 [1000.0, 2000.0], tolerance=1e-6)
        self.assertEqual(len(result.pieces), 3)
        self.assertLessEqual(abs(result.pieces[0].area - 1000.0), 1e-6)
        self.assertLessEqual(abs(result.pieces[1].area - 2000.0), 1e-6)
        self.assertLessEqual(abs(result.pieces[2].area - 2000.0), 5e-6)


class TestConcaveAndHoles(unittest.TestCase):

    # drawn above the U, the near side, so the piece comes off the top and crosses both arms. drawing direction doesn't matter
    U_DIRECTION = _line(0, 40, -1, 40)

    def test_cut_across_both_arms_gives_multipart_piece(self):
        result = detach_by_areas(_geom(U_SHAPE), self.U_DIRECTION,
                                 [200.0], tolerance=1e-6)
        self.assertEqual(len(result.pieces), 2)
        piece = result.pieces[0]
        self.assertTrue(piece.geometry.isMultipart())
        self.assertEqual(len(piece.geometry.asGeometryCollection()), 2)
        self.assertLessEqual(abs(piece.area - 200.0), 1e-6)
        self.assertLessEqual(abs(result.pieces[1].area - 500.0), 5e-6)

    def test_split_fragments_expands_multipart_piece(self):
        result = detach_by_areas(_geom(U_SHAPE), self.U_DIRECTION,
                                 [200.0], tolerance=1e-6,
                                 split_fragments=True)
        target_rows = [p for p in result.pieces if p.index == 0]
        self.assertEqual(len(target_rows), 2)
        for row in target_rows:
            self.assertEqual(row.target, 200.0)
            self.assertLessEqual(abs(row.area - 100.0), 1e-6)
        remainder_rows = [p for p in result.pieces if p.index == 1]
        self.assertEqual(len(remainder_rows), 1)
        self.assertIsNone(remainder_rows[0].target)

    def test_hole_crossed_by_cut(self):
        # drawn right of the donut, so the piece comes off the right and 500 m2 puts the cut at x = 25, straight through the hole
        result = detach_by_areas(_geom(DONUT), _line(50, 0, 50, 1),
                                 [500.0], tolerance=1e-6)
        self.assertEqual(len(result.pieces), 2)
        piece, remainder = result.pieces
        self.assertLessEqual(abs(piece.area - 500.0), 1e-6)
        self.assertLessEqual(abs(remainder.area - 700.0), 5e-6)
        self.assertLessEqual(
            abs(piece.area + remainder.area - result.total_area), 5e-6)
        self.assertAlmostEqual(
            piece.geometry.boundingBox().xMinimum(), 25.0, places=5)


class TestMultipartInput(unittest.TestCase):

    def test_targets_span_the_parts(self):
        # drawn along x=0, nearest the left square, so we get all of it plus half the right one as a two-part piece of 150
        result = detach_by_areas(_geom(MULTI), _line(0, 0, 0, 1),
                                 [150.0], tolerance=1e-6)
        self.assertEqual(len(result.pieces), 2)
        piece = result.pieces[0]
        self.assertTrue(piece.geometry.isMultipart())
        self.assertLessEqual(abs(piece.area - 150.0), 1e-6)
        self.assertLessEqual(abs(result.pieces[1].area - 50.0), 5e-6)


class TestMillimeterMode(unittest.TestCase):

    def test_vertices_on_mm_grid_and_areas_hit(self):
        result = detach_by_areas(
            _geom(BIG_RECT), _line(500000, 300000, 500000, 300050),
            [1234.0], tolerance=0.01, coord_decimals=3)
        self.assertEqual(len(result.pieces), 2)
        for piece in result.pieces:
            for v in piece.geometry.vertices():
                for c in (v.x(), v.y()):
                    self.assertLessEqual(
                        abs(c * 1000.0 - round(c * 1000.0)), 1e-6,
                        f"vertex coordinate {c!r} is off the mm grid")
        self.assertLessEqual(abs(result.pieces[0].area - 1234.0), 0.01)
        self.assertLessEqual(
            abs(result.pieces[0].area + result.pieces[1].area
                - result.total_area), 0.02)


class TestGuards(unittest.TestCase):

    def test_overallocation_raises_with_both_numbers(self):
        with self.assertRaises(DetachError) as ctx:
            detach_by_areas(_geom(RECT), _line(0, 0, 0, 50),
                            [3000.0, 3000.0])
        message = str(ctx.exception)
        self.assertIn("6000", message)
        self.assertIn("5000", message)

    def test_zero_length_direction_raises(self):
        with self.assertRaises(DetachError):
            detach_by_areas(_geom(RECT), _line(5, 5, 5, 5), [100.0])

    def test_bad_targets_raise(self):
        for bad in ([0.0], [-5.0], [float("nan")], []):
            with self.assertRaises(DetachError):
                detach_by_areas(_geom(RECT), _line(0, 0, 0, 50), bad)

    def test_empty_polygon_raises(self):
        with self.assertRaises(DetachError):
            detach_by_areas(QgsGeometry(), _line(0, 0, 0, 50), [100.0])

    def test_invalid_input_is_repaired_and_flagged(self):
        result = detach_by_areas(_geom(BOWTIE), _line(0, 0, 0, 10),
                                 [25.0], tolerance=1e-6)
        self.assertTrue(result.fixed_input)
        self.assertAlmostEqual(result.total_area, 50.0, places=6)
        self.assertLessEqual(abs(result.pieces[0].area - 25.0), 1e-6)


class TestConservation(unittest.TestCase):

    def test_three_targets_partition_the_polygon(self):
        polygon = _geom(BIG_HEX)
        tolerance = 0.001
        result = detach_by_areas(
            polygon, _line(500000, 300000, 500050, 300080),
            [3000.0, 4000.0, 3500.0], tolerance=tolerance)
        self.assertEqual(len(result.pieces), 4)

        total = sum(p.area for p in result.pieces)
        self.assertLessEqual(abs(total - result.total_area), 5 * tolerance)

        geoms = [p.geometry for p in result.pieces]
        for i in range(len(geoms)):
            for j in range(i + 1, len(geoms)):
                overlap = geoms[i].intersection(geoms[j])
                self.assertLess(overlap.area(), polygon.area() * 1e-9,
                                f"pieces {i} and {j} overlap")

        union = QgsGeometry.unaryUnion(geoms)
        leftover = union.symDifference(polygon)
        self.assertLess(leftover.area(), polygon.area() * 1e-6)


class TestRegressions(unittest.TestCase):
    """Regression tests: exact-sum allocation, sliver handling, remainder rows."""

    def test_exact_allocation_covers_the_polygon(self):
        # targets summing exactly to the total have to partition the input - no dropped sliver, no phantom remainder row
        polygon = _geom(BIG_HEX)
        total = polygon.area()
        targets = [total * 0.3, total * 0.45]
        targets.append(total - targets[0] - targets[1])
        result = detach_by_areas(
            polygon, _line(499980, 299980, 499985, 300100), targets)
        self.assertEqual(len(result.pieces), 3)
        union = QgsGeometry(result.pieces[0].geometry)
        for piece in result.pieces[1:]:
            union = union.combine(piece.geometry)
        self.assertLess(union.symDifference(polygon).area(), 1e-6)

    def test_piece_comes_from_the_side_nearest_the_line(self):
        # the line can be drawn on either side of the piece and it must not matter
        rect = _geom(RECT)  # x in [0, 100]
        left = detach_by_areas(rect, _line(-10, 0, -10, 50), [1000.0])
        self.assertLess(
            left.pieces[0].geometry.centroid().asPoint().x(), 50.0)
        right = detach_by_areas(rect, _line(110, 0, 110, 50), [1000.0])
        self.assertGreater(
            right.pieces[0].geometry.centroid().asPoint().x(), 50.0)

    def test_bool_target_rejected(self):
        with self.assertRaises(DetachError):
            detach_by_areas(_geom(RECT), _line(0, 0, 0, 50), [True])


if __name__ == "__main__":
    unittest.main()
