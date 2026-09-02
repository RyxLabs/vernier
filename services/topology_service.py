# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Topology checks - validity, duplicates, overlaps, gaps, vertex quality. Each one is a pure QgsVectorLayer -> list[TopologyError] with tolerances in layer units and no UI or config reads, and the errors are self-contained so callers can hold onto them after the source layer moves on."""

# set algebra rather than layer pipelines: gaps dissolve the valid footprints with unaryUnion and read the interior rings, duplicates cluster on exact bounding box before comparing with GEOS, overlaps stream through an incrementally built spatial index so every unordered pair comes up once

import math
from typing import Callable, List, Optional

from qgis.analysis import (  # type: ignore
    QgsGeometrySnapper, QgsInternalGeometrySnapper,
)
from qgis.core import (  # type: ignore
    QgsCurvePolygon, QgsFeature, QgsGeometry, QgsPointXY, QgsPolygon,
    QgsSpatialIndex, QgsVectorLayer,
)

from ..i18n import tr as _tr
from ..qt_compat import VALIDATOR_GEOS

# error kinds
KIND_INVALID = "invalid"
KIND_DUPLICATE = "duplicate"
KIND_OVERLAP = "overlap"
KIND_GAP = "gap"
KIND_VERTEX = "vertex"

# vertex error subtypes
VERTEX_DUPLICATE_POINT = "duplicate_point"
VERTEX_CLOSE_VERTICES = "close_vertices"
VERTEX_SHORT_SEGMENT = "short_segment"

# a segment is "short" under this many times the vertex tolerance, and the panel tooltip promises the user 10x
SHORT_SEGMENT_FACTOR = 10

ProgressCallback = Optional[Callable[[float], None]]


class TopologyError:
    """One finding. conflict is the geometry to highlight, feature_geometries are copies of the features involved for secondary highlighting, value carries the measured quantity where there is one, and location is an optional point marking the exact defect."""

    def __init__(self, kind: str, conflict: QgsGeometry, feature_ids,
                 description: str, value: float = 0.0, subtype: str = "",
                 feature_geometries=None, location=None):
        self.kind = kind
        self.conflict = conflict
        self.feature_ids = list(feature_ids)
        self.description = description
        self.value = value
        self.subtype = subtype
        self.feature_geometries = list(feature_geometries or [])
        self.location = location


def _per_feature_progress(progress: ProgressCallback, layer: QgsVectorLayer):
    """A callable that takes the number of features handled so far."""
    if progress is None:
        return lambda handled: None
    span = max(layer.featureCount(), 1)
    # the callback's own return value flows back out: duplicate_groups lets a caller stop the scan by returning False, which is the tool's Cancel button
    return lambda handled: progress(handled * 100.0 / span)


def _geos_finding(geometry: QgsGeometry):
    """First GEOS complaint as (message, finding), (None, None) when the geometry is fine. The finding comes back too because it carries where() - the one piece of an invalid finding that is not already visible on the map."""
    findings = geometry.validateGeometry(VALIDATOR_GEOS)
    if not findings:
        return None, None
    return findings[0].what() or _tr("invalid geometry"), findings[0]


def invalid_location(geometry: QgsGeometry, finding) -> QgsGeometry:
    """A point marking where the geometry breaks. GEOS usually reports one, and where it does not the fallback is pointOnSurface - guaranteed to sit inside the feature, unlike a centroid, which for a U or a ring lands in empty space and would send the user to the wrong place."""
    if finding is not None and finding.hasWhere():
        return QgsGeometry.fromPointXY(QgsPointXY(finding.where()))
    surface = geometry.pointOnSurface()
    if surface is not None and not surface.isNull():
        return surface
    return QgsGeometry(geometry.centroid())


def check_validity(layer: QgsVectorLayer,
                   progress: ProgressCallback = None) -> List[TopologyError]:
    """Flag features whose geometry doesn't pass GEOS validation."""
    report = _per_feature_progress(progress, layer)
    errors = []
    for handled, feature in enumerate(layer.getFeatures(), start=1):
        geom = feature.geometry()
        if not geom.isNull():
            complaint, finding = _geos_finding(geom)
            if complaint is not None:
                errors.append(TopologyError(
                    KIND_INVALID, QgsGeometry(geom), [feature.id()],
                    _tr("feature {0}: {1}").format(feature.id(), complaint),
                    subtype=complaint,
                    feature_geometries=[QgsGeometry(geom)],
                    location=invalid_location(geom, finding)))
        report(handled)
    return errors


def duplicate_groups(layer: QgsVectorLayer,
                     progress: ProgressCallback = None) -> List[List[tuple]]:
    """Groups of two or more features sharing one geometry, each as [(feature id, geometry), ...]. The single definition of "duplicate" in the plugin: the topology check and the review-layer tool both build on this, so they can never report different counts for one layer.

    Topological equality, not matching vertex lists - a re-digitised parcel carrying a redundant collinear vertex covers identical ground and belongs in the same group. Clustering on the exact bounding box first keeps the GEOS comparisons to pairs that could actually be equal, since equal geometries cover the same points and their extremes match bit for bit. A progress callback returning False stops the scan, and whatever was grouped so far comes back."""
    report = _per_feature_progress(progress, layer)
    clusters = {}
    for handled, feature in enumerate(layer.getFeatures(), start=1):
        geom = feature.geometry()
        if not geom.isNull() and not geom.isEmpty():
            box = geom.boundingBox()
            key = (box.xMinimum(), box.yMinimum(),
                   box.xMaximum(), box.yMaximum())
            clusters.setdefault(key, []).append((feature.id(), geom))
        if report(handled) is False:
            break

    found = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        # split the cluster into GEOS-equal groups, each represented by its first member
        groups = []
        for fid, geom in members:
            for group in groups:
                if geom.isGeosEqual(group[0][1]):
                    group.append((fid, geom))
                    break
            else:
                groups.append([(fid, geom)])
        found.extend(group for group in groups if len(group) > 1)
    return found


def check_duplicates(layer: QgsVectorLayer,
                     progress: ProgressCallback = None) -> List[TopologyError]:
    """Features whose geometry is topologically equal to another's - a group of n gives n-1 errors, every one of them naming the group's first feature as the keeper."""
    errors = []
    for group in duplicate_groups(layer, progress=progress):
        first_id, first_geom = group[0]
        for other_id, other_geom in group[1:]:
            errors.append(TopologyError(
                KIND_DUPLICATE, QgsGeometry(first_geom),
                [first_id, other_id],
                _tr("features {0} and {1} are copies of the same "
                    "geometry").format(first_id, other_id),
                feature_geometries=[QgsGeometry(first_geom),
                                    QgsGeometry(other_geom)]))
    if progress:
        progress(100.0)
    return errors


def duplicate_id_groups(errors) -> List[List[int]]:
    """Feature ids per duplicate group, keeper first, in the order the groups were reported. check_duplicates emits a group of n as n-1 pairs that all name the same first feature, so grouping on feature_ids[0] rebuilds the group. Non-duplicate kinds are ignored, so a mixed run can be handed over whole."""
    members = {}
    order = []
    for error in errors:
        if error.kind != KIND_DUPLICATE or len(error.feature_ids) < 2:
            continue
        keeper = error.feature_ids[0]
        if keeper not in members:
            members[keeper] = []
            order.append(keeper)
        members[keeper].append(error.feature_ids[1])
    return [[keeper] + members[keeper] for keeper in order]


def redundant_duplicate_ids(errors) -> List[int]:
    """The features to delete so exactly one member of every duplicate group survives - every id but the keeper's, across all groups. Says nothing about whether deleting them is safe; see split_duplicate_groups."""
    return [fid for group in duplicate_id_groups(errors) for fid in group[1:]]


def split_duplicate_groups(errors, attributes_by_id):
    """(deletable ids, groups left alone), given {feature id: attribute values}.

    Identical geometry does not make two features interchangeable. Copy-paste duplicates carry identical attributes and deleting one loses nothing, but two records claiming the same ground with different attributes is a data conflict - keeping whichever happened to be digitised first would silently discard the one that may be correct. Those groups come back untouched for a human to settle.

    An id missing from the mapping counts as a conflict: failing to read a feature is not evidence that it is a copy."""
    deletable = []
    conflicted = []
    for group in duplicate_id_groups(errors):
        values = [attributes_by_id.get(fid) for fid in group]
        if any(value is None for value in values):
            conflicted.append(group)
        elif any(value != values[0] for value in values[1:]):
            conflicted.append(group)
        else:
            deletable.extend(group[1:])
    return deletable, conflicted


def check_overlaps(layer: QgsVectorLayer,
                   progress: ProgressCallback = None) -> List[TopologyError]:
    """Pairs of polygons whose interiors share area - partial overlap and full containment both count, touching doesn't. Identical pairs are left to check_duplicates so one mistake isn't reported twice, and invalid geometries to check_validity."""
    report = _per_feature_progress(progress, layer)
    index = QgsSpatialIndex()
    indexed_geoms = {}
    errors = []
    for handled, feature in enumerate(layer.getFeatures(), start=1):
        geom = feature.geometry()
        if geom.isNull() or not geom.isGeosValid():
            report(handled)
            continue
        for prior_id in index.intersects(geom.boundingBox()):
            prior = indexed_geoms[prior_id]
            # DE-9IM overlaps() is false for containment, but a polygon swallowed whole is still a conflict
            if not (geom.overlaps(prior) or geom.contains(prior)
                    or geom.within(prior)):
                continue
            if geom.isGeosEqual(prior):
                continue
            shared = geom.intersection(prior)
            errors.append(TopologyError(
                KIND_OVERLAP, shared, [prior_id, feature.id()],
                _tr("features {0} and {1} overlap by {2}").format(
                    prior_id, feature.id(), f"{shared.area():.4f}"),
                value=shared.area(),
                feature_geometries=[QgsGeometry(prior), QgsGeometry(geom)]))
        index.addFeature(feature)
        indexed_geoms[feature.id()] = QgsGeometry(geom)
        report(handled)
    return errors


def _enclosed_pockets(coverage: QgsGeometry) -> List[QgsGeometry]:
    """Every interior ring of the coverage, as a standalone polygon."""
    pockets = []
    for part in coverage.constParts():
        if not isinstance(part, QgsCurvePolygon):
            continue
        for i in range(part.numInteriorRings()):
            shell = QgsPolygon()
            shell.setExteriorRing(part.interiorRing(i).clone())
            pockets.append(QgsGeometry(shell))
    return pockets


def check_gaps(layer: QgsVectorLayer, snap_tolerance: float = 0.005,
               gap_min_area: float = 0.01, gap_buffer: float = 0.0005,
               progress: ProgressCallback = None) -> List[TopologyError]:
    """Enclosed areas the layer's coverage misses. The valid footprints dissolve into one geometry whose interior rings are exactly the uncovered pockets - invalid geometries stay out of the union on purpose, their footprint should read as uncovered. Tolerances in layer units: snap_tolerance closes hairline cracks first, gap_min_area drops small pockets, gap_buffer writes off slivers thinner than about twice its size as snapping residue."""
    def report(pct: float):
        if progress:
            progress(pct)

    footprints = []
    for feature in layer.getFeatures():
        geom = feature.geometry()
        if geom.isNull() or geom.isEmpty() or not geom.isGeosValid():
            continue
        footprints.append(QgsGeometry(geom))
    report(15)

    if snap_tolerance > 0 and len(footprints) > 1:
        snapper = QgsInternalGeometrySnapper(
            snap_tolerance, QgsGeometrySnapper.SnapMode.PreferNodes)
        aligned = []
        # snapping is one of the two slow phases, report per feature - on a big layer the bar keeps moving instead of sitting still long enough to read as a hang
        total = len(footprints)
        for handled, geom in enumerate(footprints, start=1):
            carrier = QgsFeature()
            carrier.setGeometry(geom)
            snapped = snapper.snapFeature(carrier)
            if not snapped.isGeosValid():
                snapped = snapped.makeValid()
            aligned.append(snapped)
            report(15 + handled * 25.0 / total)
        footprints = aligned
    else:
        report(40)

    coverage = QgsGeometry.unaryUnion(footprints) if footprints else None
    report(65)

    pockets = [] if coverage is None else _enclosed_pockets(coverage)
    report(85)

    errors = []
    for pocket in pockets:
        area = pocket.area()
        if area < gap_min_area:
            continue
        if gap_buffer > 0 and pocket.buffer(-gap_buffer, 5).isEmpty():
            continue
        errors.append(TopologyError(
            KIND_GAP, pocket, [],
            _tr("uncovered pocket of {0}").format(f"{area:.4f}"),
            value=area))
    report(100)
    return errors


def _boundary_chains(geometry: QgsGeometry):
    """Each polygon ring, as its closed vertex sequence."""
    for part in geometry.constParts():
        if not isinstance(part, QgsCurvePolygon):
            continue
        rings = [part.exteriorRing()]
        rings.extend(part.interiorRing(i)
                     for i in range(part.numInteriorRings()))
        for ring in rings:
            if ring is not None:
                yield ring.curveToLine().points()


def check_vertex_errors(layer: QgsVectorLayer, tolerance: float = 0.005,
                        progress: ProgressCallback = None
                        ) -> List[TopologyError]:
    """Grade every ring segment by length - zero means a doubled vertex, under tolerance means two suspiciously close vertices, under SHORT_SEGMENT_FACTOR x tolerance means a suspiciously short segment. Rings are walked closed, so the closing segment gets graded like the rest."""
    report = _per_feature_progress(progress, layer)
    errors = []
    for handled, feature in enumerate(layer.getFeatures(), start=1):
        geom = feature.geometry()
        if geom.isNull() or geom.isEmpty():
            report(handled)
            continue
        for chain in _boundary_chains(geom):
            for i, (start, end) in enumerate(zip(chain, chain[1:])):
                length = math.hypot(end.x() - start.x(), end.y() - start.y())
                if length == 0.0:
                    errors.append(TopologyError(
                        KIND_VERTEX,
                        QgsGeometry.fromPointXY(QgsPointXY(start)),
                        [feature.id()],
                        _tr("feature {0}: vertex {1} is doubled").format(
                            feature.id(), i),
                        subtype=VERTEX_DUPLICATE_POINT))
                elif length < tolerance:
                    errors.append(TopologyError(
                        KIND_VERTEX,
                        QgsGeometry.fromPointXY(QgsPointXY(start)),
                        [feature.id()],
                        _tr("feature {0}: vertices {1} and {2} are only "
                            "{3} apart").format(feature.id(), i, i + 1,
                                                f"{length:.8f}"),
                        value=length, subtype=VERTEX_CLOSE_VERTICES))
                elif length < tolerance * SHORT_SEGMENT_FACTOR:
                    midpoint = QgsPointXY((start.x() + end.x()) / 2,
                                          (start.y() + end.y()) / 2)
                    errors.append(TopologyError(
                        KIND_VERTEX, QgsGeometry.fromPointXY(midpoint),
                        [feature.id()],
                        _tr("feature {0}: segment {1}-{2} is {3} "
                            "long").format(feature.id(), i, i + 1,
                                           f"{length:.8f}"),
                        value=length, subtype=VERTEX_SHORT_SEGMENT))
        report(handled)
    return errors
