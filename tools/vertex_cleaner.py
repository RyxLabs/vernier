# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Close-vertex removal that knows about topology - a close pair gets resolved by ranking both members and keeping the better one. UI-free, the dialog owns the widgets, markers and the optional snap pass."""

# rank is a lexicographic tuple: how many OTHER features have a vertex at exactly those coordinates, then perpendicular distance from the chord through the pair's outer neighbors, then position within the pair so ties resolve the same way every run

# a vertex the neighbors also reference is common boundary material and dropping it opens a gap, so when BOTH members of a pair are shared the pair is left alone - every neighbor resolves its own copy, and two features that disagree about the survivor stop meeting along that boundary

# polygons go ring by ring on every part: opened, cleaned circularly so the seam vertex is treated like any other, then re-closed, never under 3 unique points. lines have no wrap-around and their endpoints always survive, since they carry the connectivity of the network

import math

from qgis.core import (  # type: ignore
    QgsExpression, QgsExpressionContext, QgsExpressionContextUtils,
    QgsFeature, QgsGeometry, QgsRectangle, QgsSpatialIndex, QgsVectorLayer,
    QgsWkbTypes,
)

from ..i18n import tr as _tr

# bbox half-width for the spatial-index lookups, the match itself is on exact coordinates so this only has to be inclusive
_EPS = 1e-9


class CleanResult:
    """What clean_layer produced - the new layer plus the review data."""

    def __init__(self, layer, removed_points, removed_count, skipped_count):
        self.layer = layer
        self.removed_points = removed_points  # QgsPointXY, in layer CRS
        self.removed_count = removed_count
        self.skipped_count = skipped_count


def _chord_offset(point, anchor_start, anchor_end):
    """Perpendicular distance from the point to the line through the anchors. Coincident anchors give 0.0 and push the decision down to the positional rank."""
    ux = anchor_end.x() - anchor_start.x()
    uy = anchor_end.y() - anchor_start.y()
    length = math.hypot(ux, uy)
    if length == 0.0:
        return 0.0
    return abs(ux * (point.y() - anchor_start.y())
               - uy * (point.x() - anchor_start.x())) / length


def _rank(point, anchors, shared):
    """How much a close-pair member is worth keeping, higher wins - (neighbor share count, offset from the anchors' chord). The header explains the ordering."""
    return (shared, _chord_offset(point, anchors[0], anchors[1]))


def _survivor(pair, anchors, shares):
    """Which member of the close pair stays, 0 or 1. Top rank survives and a full tie keeps the second, so the same pair resolves the same way every run."""
    rank_a = _rank(pair[0], anchors, shares[0])
    rank_b = _rank(pair[1], anchors, shares[1])
    return 1 if rank_b >= rank_a else 0


def _clean_open_ring(points, tolerance, count_shared):
    """Remove close vertices from an open ring, treated circularly so (last, first) is a candidate like any other. Never shrinks below 3 points. Returns (new_points, removed_points)."""
    n = len(points)
    if n <= 3:
        return list(points), []
    marked = {}  # index -> rank of the vertex marked for deletion
    for i in range(n):
        j = (i + 1) % n
        a, b = points[i], points[j]
        if a.distance(b) > tolerance:
            continue
        shares = (count_shared(a), count_shared(b))
        # both sit on a common boundary, and the neighbors resolve their own copy of this pair, so either deletion can tear the shared edge
        if shares[0] > 0 and shares[1] > 0:
            continue
        anchors = (points[(i - 1) % n], points[(i + 2) % n])
        keep = _survivor((a, b), anchors, shares)
        index = j if keep == 0 else i
        rank = _rank(points[index], anchors, shares[1 - keep])
        # a vertex can lose in both its pairs, and the floor below has to respect the better rank
        marked[index] = max(rank, marked.get(index, rank))
    limit = n - 3
    if len(marked) > limit:
        # more marked than the floor allows, so give up the lowest-ranked ones rather than whichever sit last in the ring
        doomed = set(sorted(marked, key=lambda k: (marked[k], k))[:limit])
    else:
        doomed = set(marked)
    if not doomed:
        return list(points), []
    new_points = [pt for k, pt in enumerate(points) if k not in doomed]
    removed = [points[k] for k in doomed]
    return new_points, removed


def _clean_line(points, tolerance, count_shared):
    """Remove close vertices from an open line part. Endpoints always survive, and when an endpoint's close partner is shared with another feature neither goes - dropping the shared vertex opens a gap, dropping the endpoint moves the line end."""
    n = len(points)
    if n < 3:
        return list(points), []
    last = n - 1
    to_delete = set()
    for i in range(n - 1):
        j = i + 1
        a, b = points[i], points[j]
        if a.distance(b) > tolerance:
            continue
        shares = (count_shared(a), count_shared(b))
        if i == 0:
            # the endpoint stays either way, so the partner only goes if no other feature has a node on it
            if shares[1] > 0:
                continue
            to_delete.add(j)
        elif j == last:
            if shares[0] > 0:
                continue
            to_delete.add(i)
        else:
            # see _clean_open_ring for pairs shared at both ends
            if shares[0] > 0 and shares[1] > 0:
                continue
            keep = _survivor((a, b), (points[i - 1], points[j + 1]),
                             shares)
            to_delete.add(j if keep == 0 else i)
    if not to_delete:
        return list(points), []
    new_points = [pt for k, pt in enumerate(points) if k not in to_delete]
    removed = [points[k] for k in to_delete]
    return new_points, removed


def clean_geometry(geometry, tolerance, count_shared):
    """Cleaned copy of one polygon or line, as (QgsGeometry, removed_points). count_shared(point) has to say how many OTHER features have a vertex at exactly those coordinates, and anything that isn't a polygon or line comes back untouched."""
    removed = []
    wkb = geometry.wkbType()
    gtype = QgsWkbTypes.geometryType(wkb)
    is_multi = QgsWkbTypes.isMultiType(wkb)

    if gtype == QgsWkbTypes.GeometryType.PolygonGeometry:
        parts = geometry.asMultiPolygon() if is_multi \
            else [geometry.asPolygon()]
        new_parts = []
        for part in parts:
            new_rings = []
            for ring in part:
                # asPolygon rings carry the closing duplicate, work open
                pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] \
                    else list(ring)
                if len(pts) < 3:
                    new_rings.append(list(ring))
                    continue
                new_pts, ring_removed = _clean_open_ring(
                    pts, tolerance, count_shared)
                removed.extend(ring_removed)
                new_rings.append(new_pts + [new_pts[0]])
            new_parts.append(new_rings)
        if is_multi:
            return QgsGeometry.fromMultiPolygonXY(new_parts), removed
        return QgsGeometry.fromPolygonXY(new_parts[0]), removed

    if gtype == QgsWkbTypes.GeometryType.LineGeometry:
        parts = geometry.asMultiPolyline() if is_multi \
            else [geometry.asPolyline()]
        new_parts = []
        for part in parts:
            new_pts, part_removed = _clean_line(
                list(part), tolerance, count_shared)
            removed.extend(part_removed)
            new_parts.append(new_pts)
        if is_multi:
            return QgsGeometry.fromMultiPolylineXY(new_parts), removed
        return QgsGeometry.fromPolylineXY(new_parts[0]), removed

    return QgsGeometry(geometry), removed


def _vertex_count(geometry) -> int:
    base = geometry.constGet()
    return base.nCoordinates() if base is not None else 0


def clean_layer(layer, segment_tolerance, dup_tolerance=0.0,
                skip_expression="", only_fids=None, progress=None):
    """Clean a whole polygon or line layer into a new memory layer, same CRS and fields, one feature per input feature, not added to the project. The analysis is planar so Z/M is dropped and the output is always 2D. skip_expression copies matching features through unchanged and raises ValueError up front if it won't parse, only_fids narrows the work, and progress gets called with 0-100 once per feature."""
    expression = None
    context = None
    if skip_expression and skip_expression.strip():
        expression = QgsExpression(skip_expression)
        if expression.hasParserError():
            raise ValueError(expression.parserErrorString())
        context = QgsExpressionContext(
            QgsExpressionContextUtils.globalProjectLayerScopes(layer))
        expression.prepare(context)
        if expression.hasEvalError():
            raise ValueError(expression.evalErrorString())

    features = {f.id(): f for f in layer.getFeatures()}
    index = QgsSpatialIndex(layer.getFeatures())

    # exact-coordinate vertex sets per feature, built lazily. a vertex is "shared" when another feature stores the very same coordinates, which is how snapped common boundaries actually look in the data
    vertex_sets = {}

    def _vertices_of(fid):
        cached = vertex_sets.get(fid)
        if cached is None:
            cached = {(v.x(), v.y())
                      for v in features[fid].geometry().vertices()}
            vertex_sets[fid] = cached
        return cached

    def _make_counter(fid):
        def count_shared(point):
            rect = QgsRectangle(point.x() - _EPS, point.y() - _EPS,
                                point.x() + _EPS, point.y() + _EPS)
            key = (point.x(), point.y())
            return sum(1 for nid in index.intersects(rect)
                       if nid != fid and key in _vertices_of(nid))
        return count_shared

    # flat type, because the XY rebuild in clean_geometry can't keep Z/M and a 2D geometry inside a Z-typed layer would be inconsistent
    result_layer = QgsVectorLayer(
        QgsWkbTypes.displayString(QgsWkbTypes.flatType(layer.wkbType())),
        f"{layer.name()}_cleaned", "memory")
    if not result_layer.isValid():
        raise RuntimeError(_tr("Could not create the result layer."))
    result_layer.setCrs(layer.crs())
    provider = result_layer.dataProvider()
    provider.addAttributes(list(layer.fields()))
    result_layer.updateFields()

    removed_points = []
    removed_count = 0
    skipped_count = 0
    copies = []
    total = len(features) or 1
    done = 0
    for fid, feature in features.items():
        copy = QgsFeature(result_layer.fields())
        copy.setAttributes(feature.attributes())

        process = feature.hasGeometry()
        if process and only_fids is not None and fid not in only_fids:
            process = False
        if process and expression is not None:
            context.setFeature(feature)
            value = expression.evaluate(context)
            if expression.hasEvalError():
                skip = True
            else:
                try:
                    skip = bool(value)
                except (TypeError, ValueError):
                    skip = False
            if skip:
                process = False
                skipped_count += 1

        if process:
            cleaned, removed = clean_geometry(
                feature.geometry(), segment_tolerance, _make_counter(fid))
            removed_points.extend(removed)
            removed_count += len(removed)
            if dup_tolerance > 0:
                before = _vertex_count(cleaned)
                cleaned.removeDuplicateNodes(dup_tolerance)
                removed_count += before - _vertex_count(cleaned)
            copy.setGeometry(cleaned)
        elif feature.hasGeometry():
            passthrough = QgsGeometry(feature.geometry())
            base = passthrough.get()
            if base is not None:
                base.dropZValue()
                base.dropMValue()
            copy.setGeometry(passthrough)

        copies.append(copy)
        done += 1
        if progress is not None:
            progress(int(done * 100 / total))

    provider.addFeatures(copies)
    result_layer.updateExtents()
    return CleanResult(result_layer, removed_points,
                       removed_count, skipped_count)
