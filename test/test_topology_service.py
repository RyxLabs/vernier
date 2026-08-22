# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""All five topology checks on synthetic fixtures. Every check gets its clean case next to its dirty one, the gap check carries a CRS regression test, and TestCleanLayerEverywhere runs the whole battery over an error-free layer as a smoke test."""

import os
import sys
import unittest

from qgis.core import (  # type: ignore
    QgsApplication, QgsFeature, QgsGeometry, QgsVectorLayer,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from vernier.services import topology_service  # noqa: E402

SQUARE = "POLYGON((0 0, 2 0, 2 2, 0 2, 0 0))"
# same square, ring starting at a different vertex - GEOS-equal
SQUARE_ROTATED = "POLYGON((2 0, 2 2, 0 2, 0 0, 2 0))"
SQUARE_SHIFTED = "POLYGON((1 0, 3 0, 3 2, 1 2, 1 0))"  # overlaps SQUARE by 2
SQUARE_INSIDE = "POLYGON((0.5 0.5, 1.5 0.5, 1.5 1.5, 0.5 1.5, 0.5 0.5))"
SQUARE_FAR = "POLYGON((10 10, 11 10, 11 11, 10 11, 10 10))"

# two clean unit squares sharing the edge x=1 - no errors of any kind
ADJACENT_A = "POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"
ADJACENT_B = "POLYGON((1 0, 2 0, 2 1, 1 1, 1 0))"

# U plus a cap overlapping its arms, which encloses a 6 x 0.5 sliver gap between y=2 and y=2.5
U_SHAPE = "POLYGON((0 0, 10 0, 10 10, 8 10, 8 2, 2 2, 2 10, 0 10, 0 0))"
CAP = "POLYGON((0 2.5, 10 2.5, 10 10, 0 10, 0 2.5))"

BOWTIE = "POLYGON((0 0, 2 2, 2 0, 0 2, 0 0))"  # self-intersecting

# vertex triage fixtures (default tolerance 0.005, short segment < 0.05)
DUP_VERTEX = "POLYGON((0 0, 1 0, 1 0, 1 1, 0 1, 0 0))"
CLOSE_VERTICES = "POLYGON((0 0, 1 0, 1 1, 1.002 1, 0 1, 0 0))"
SHORT_SEGMENT = "POLYGON((0 0, 1 0, 1 1, 1.02 1, 0 1, 0 0))"

QGS = None


def setUpModule():
    global QGS
    QGS = QgsApplication([], False)
    QGS.initQgis()


def tearDownModule():
    QGS.exitQgis()


def _layer(wkts, uri="Polygon"):
    layer = QgsVectorLayer(uri, "fixture", "memory")
    features = []
    for wkt in wkts:
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromWkt(wkt))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    return layer


class TestValidity(unittest.TestCase):

    def test_bowtie_flagged(self):
        layer = _layer([SQUARE, BOWTIE])
        errors = topology_service.check_validity(layer)
        self.assertEqual(len(errors), 1)
        error = errors[0]
        self.assertEqual(error.kind, topology_service.KIND_INVALID)
        bowtie_id = [f.id() for f in layer.getFeatures()][1]
        self.assertEqual(error.feature_ids, [bowtie_id])

    def test_valid_layer_clean(self):
        layer = _layer([SQUARE, SQUARE_FAR])
        self.assertEqual(topology_service.check_validity(layer), [])


class TestDuplicates(unittest.TestCase):

    def test_identical_pair_found(self):
        layer = _layer([SQUARE, SQUARE, SQUARE_FAR])
        errors = topology_service.check_duplicates(layer)
        self.assertEqual(len(errors), 1)
        error = errors[0]
        self.assertEqual(error.kind, topology_service.KIND_DUPLICATE)
        ids = [f.id() for f in layer.getFeatures()]
        self.assertEqual(sorted(error.feature_ids), sorted(ids[:2]))

    def test_rotated_ring_is_still_a_duplicate(self):
        layer = _layer([SQUARE, SQUARE_ROTATED])
        errors = topology_service.check_duplicates(layer)
        self.assertEqual(len(errors), 1)

    def test_distinct_layer_clean(self):
        layer = _layer([SQUARE, SQUARE_FAR])
        self.assertEqual(topology_service.check_duplicates(layer), [])

    def test_progress_reported_per_feature(self):
        layer = _layer([SQUARE, SQUARE_FAR])
        seen = []
        topology_service.check_duplicates(layer, progress=seen.append)
        self.assertEqual(seen, sorted(seen))
        self.assertAlmostEqual(seen[-1], 100.0)
        # ticks flow while the layer streams by, not one jump at the end
        self.assertLess(seen[0], 100.0)


class TestRedundantDuplicateIds(unittest.TestCase):
    """The delete set for the topology panel: every duplicate except one keeper per group."""

    def test_pair_yields_one_id(self):
        layer = _layer([SQUARE, SQUARE])
        ids = [f.id() for f in layer.getFeatures()]
        errors = topology_service.check_duplicates(layer)
        self.assertEqual(topology_service.redundant_duplicate_ids(errors),
                         [ids[1]])

    def test_group_of_three_keeps_exactly_one(self):
        layer = _layer([SQUARE, SQUARE, SQUARE])
        ids = [f.id() for f in layer.getFeatures()]
        errors = topology_service.check_duplicates(layer)
        redundant = topology_service.redundant_duplicate_ids(errors)
        self.assertEqual(len(redundant), 2)
        self.assertNotIn(ids[0], redundant)  # the keeper survives
        self.assertEqual(sorted(redundant), sorted(ids[1:]))

    def test_two_groups_each_keep_one(self):
        layer = _layer([SQUARE, SQUARE, SQUARE_FAR, SQUARE_FAR])
        ids = [f.id() for f in layer.getFeatures()]
        errors = topology_service.check_duplicates(layer)
        redundant = topology_service.redundant_duplicate_ids(errors)
        self.assertEqual(sorted(redundant), sorted([ids[1], ids[3]]))

    def test_clean_layer_yields_nothing(self):
        layer = _layer([SQUARE, SQUARE_FAR])
        errors = topology_service.check_duplicates(layer)
        self.assertEqual(topology_service.redundant_duplicate_ids(errors), [])

    def test_other_error_kinds_ignored(self):
        # a mixed run hands over every kind at once, and only duplicates may be deleted
        layer = _layer([SQUARE, BOWTIE])
        errors = (topology_service.check_validity(layer)
                  + topology_service.check_overlaps(layer))
        self.assertEqual(topology_service.redundant_duplicate_ids(errors), [])

    def test_no_id_repeats_when_a_group_spans_several_errors(self):
        # a group of n reports n-1 pairs all sharing the keeper, so a naive flatten of feature_ids would delete the keeper and double-count
        layer = _layer([SQUARE, SQUARE, SQUARE, SQUARE])
        errors = topology_service.check_duplicates(layer)
        redundant = topology_service.redundant_duplicate_ids(errors)
        self.assertEqual(len(redundant), len(set(redundant)))
        self.assertEqual(len(redundant), 3)


class TestOverlaps(unittest.TestCase):

    def test_overlap_found_with_area(self):
        layer = _layer([SQUARE, SQUARE_SHIFTED, SQUARE_FAR])
        errors = topology_service.check_overlaps(layer)
        self.assertEqual(len(errors), 1)
        error = errors[0]
        self.assertEqual(error.kind, topology_service.KIND_OVERLAP)
        ids = [f.id() for f in layer.getFeatures()]
        self.assertEqual(sorted(error.feature_ids), sorted(ids[:2]))
        self.assertAlmostEqual(error.value, 2.0, places=6)
        self.assertAlmostEqual(error.conflict.area(), 2.0, places=6)

    def test_touching_is_not_overlapping(self):
        layer = _layer([ADJACENT_A, ADJACENT_B])
        self.assertEqual(topology_service.check_overlaps(layer), [])

    def test_contained_polygon_counts_as_overlap(self):
        # DE-9IM overlaps() is false for containment, but a polygon swallowed whole still has to be flagged
        layer = _layer([SQUARE, SQUARE_INSIDE])
        errors = topology_service.check_overlaps(layer)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].kind, topology_service.KIND_OVERLAP)
        self.assertAlmostEqual(errors[0].value, 1.0)

    def test_exact_duplicate_left_to_the_duplicates_check(self):
        # identical geometries contain each other, but reporting them here would double up with check_duplicates
        layer = _layer([SQUARE, SQUARE])
        self.assertEqual(topology_service.check_overlaps(layer), [])

    def test_progress_reported(self):
        layer = _layer([SQUARE, SQUARE_SHIFTED])
        seen = []
        topology_service.check_overlaps(layer, progress=seen.append)
        self.assertTrue(seen)
        self.assertEqual(seen, sorted(seen))
        self.assertAlmostEqual(seen[-1], 100.0)


class TestGaps(unittest.TestCase):

    def test_sliver_gap_found(self):
        layer = _layer([U_SHAPE, CAP])
        errors = topology_service.check_gaps(layer)
        self.assertEqual(len(errors), 1)
        error = errors[0]
        self.assertEqual(error.kind, topology_service.KIND_GAP)
        self.assertAlmostEqual(error.value, 3.0, delta=0.05)
        center = error.conflict.centroid().asPoint()
        self.assertAlmostEqual(center.x(), 5.0, delta=0.1)
        self.assertAlmostEqual(center.y(), 2.25, delta=0.1)

    def test_min_area_filters_the_gap(self):
        layer = _layer([U_SHAPE, CAP])
        errors = topology_service.check_gaps(layer, gap_min_area=10.0)
        self.assertEqual(errors, [])

    def test_overlay_uses_the_layer_crs(self):
        # the coverage union is built from the layer's own geometries, so a projected CRS has to give the same pocket as an unset one
        layer = _layer([U_SHAPE, CAP], uri="Polygon?crs=EPSG:32635")
        errors = topology_service.check_gaps(layer)
        self.assertEqual(len(errors), 1)
        self.assertAlmostEqual(errors[0].value, 3.0, delta=0.05)

    def test_progress_called_between_steps(self):
        layer = _layer([U_SHAPE, CAP])
        seen = []
        topology_service.check_gaps(layer, progress=seen.append)
        self.assertTrue(seen)
        self.assertEqual(seen, sorted(seen))
        self.assertAlmostEqual(seen[-1], 100.0)

    def test_full_coverage_clean(self):
        layer = _layer([ADJACENT_A, ADJACENT_B])
        self.assertEqual(topology_service.check_gaps(layer), [])


class TestVertexErrors(unittest.TestCase):

    def _single(self, wkt):
        errors = topology_service.check_vertex_errors(_layer([wkt]))
        self.assertEqual(len(errors), 1)
        return errors[0]

    def test_duplicate_point(self):
        error = self._single(DUP_VERTEX)
        self.assertEqual(error.kind, topology_service.KIND_VERTEX)
        self.assertEqual(error.subtype,
                         topology_service.VERTEX_DUPLICATE_POINT)

    def test_close_vertices(self):
        error = self._single(CLOSE_VERTICES)
        self.assertEqual(error.subtype,
                         topology_service.VERTEX_CLOSE_VERTICES)
        self.assertAlmostEqual(error.value, 0.002, places=9)

    def test_short_segment(self):
        error = self._single(SHORT_SEGMENT)
        self.assertEqual(error.subtype,
                         topology_service.VERTEX_SHORT_SEGMENT)
        self.assertAlmostEqual(error.value, 0.02, places=9)

    def test_clean_layer(self):
        layer = _layer([ADJACENT_A, ADJACENT_B])
        self.assertEqual(topology_service.check_vertex_errors(layer), [])


class TestCleanLayerEverywhere(unittest.TestCase):

    def test_all_five_checks_return_nothing(self):
        layer = _layer([ADJACENT_A, ADJACENT_B])
        self.assertEqual(topology_service.check_validity(layer), [])
        self.assertEqual(topology_service.check_duplicates(layer), [])
        self.assertEqual(topology_service.check_overlaps(layer), [])
        self.assertEqual(topology_service.check_gaps(layer), [])
        self.assertEqual(topology_service.check_vertex_errors(layer), [])


if __name__ == "__main__":
    unittest.main()
