# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cut fixed-area pieces off a polygon with straight cuts parallel to a drawn direction line. Geometry in, DetachResult out - no widgets, no iface, no project access."""

# each cut offset is found by bisection: the piece at offset d is the part on the -n side of the cut, and its area grows monotonically with d, so a plain interval search converges. cuts run sequentially on what the previous one left, and the clipping itself is GEOS against a huge half-plane rectangle

# coord_decimals turns on mm mode, where the search converges on the area a piece will have AFTER rounding and the output is rounded the same way. achievable areas are quantized by the coordinate grid there, so either the tolerance clears the grid granularity or the target has to be attainable on it

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from qgis.core import QgsGeometry, QgsPointXY, QgsWkbTypes  # type: ignore

from ..i18n import tr as _tr

_MAX_ITERATIONS = 120
# bisection gives up once the bracket shrinks to this fraction of its initial width without meeting the area tolerance
_BRACKET_EPS = 1e-12
# leftover under this is numeric dust, not a remainder piece
_DUST = 1e-9
# in mm mode, when bisection exhausts the bracket, the nearest achievable piece is accepted up to this deviation. past it the piece isn't usable and it raises
_MM_MAX_DEVIATION = 1.0


class DetachError(Exception):
    """A detach request that can't be satisfied."""


@dataclass
class DetachPiece:
    """One output polygon, either a requested division or the remainder."""
    target: Optional[float]  # requested area, None for the remainder
    geometry: QgsGeometry    # polygon or multipolygon
    area: float              # actual planar area of it
    index: int               # position in targets, len(targets) means remainder


@dataclass
class DetachResult:
    pieces: List[DetachPiece]  # in cut order, remainder last if there is one
    total_area: float          # measured area of the input, after any repair
    fixed_input: bool = False  # input was invalid and went through makeValid


def _unit_and_normal(
    direction: Tuple[QgsPointXY, QgsPointXY],
) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """Unit vector along the direction line and its left normal."""
    x0, y0 = direction[0].x(), direction[0].y()
    x1, y1 = direction[1].x(), direction[1].y()
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if not math.isfinite(length) or length <= 0.0:
        raise DetachError(_tr(
            "The direction line has zero length - draw two distinct points."))
    u = (dx / length, dy / length)
    return u, (-u[1], u[0])


def _polygonal(geometry: QgsGeometry) -> QgsGeometry:
    """Just the polygon parts - overlay ops can hand back collections with line or point crumbs where boundaries touch."""
    if geometry is None or geometry.isNull() or geometry.isEmpty():
        return QgsGeometry()
    if (QgsWkbTypes.geometryType(geometry.wkbType())
            == QgsWkbTypes.GeometryType.PolygonGeometry):
        return geometry
    parts = [part for part in geometry.asGeometryCollection()
             if (QgsWkbTypes.geometryType(part.wkbType())
                 == QgsWkbTypes.GeometryType.PolygonGeometry)]
    if not parts:
        return QgsGeometry()
    if len(parts) == 1:
        return parts[0]
    return QgsGeometry.collectGeometry(parts)


def _rounded(geometry: QgsGeometry, decimals: int) -> QgsGeometry:
    """Copy with every vertex rounded to decimals, repaired if that broke it."""
    if geometry.isEmpty():
        return QgsGeometry(geometry)
    multi = geometry.isMultipart()
    parts = geometry.asMultiPolygon() if multi else [geometry.asPolygon()]
    new_parts = []
    for part in parts:
        new_parts.append([
            [QgsPointXY(round(p.x(), decimals), round(p.y(), decimals))
             for p in ring]
            for ring in part])
    if multi:
        out = QgsGeometry.fromMultiPolygonXY(new_parts)
    else:
        out = QgsGeometry.fromPolygonXY(new_parts[0])
    if not out.isGeosValid():
        out = _polygonal(out.makeValid())
    return out


def _measure(geometry: QgsGeometry, coord_decimals: Optional[int]) -> float:
    """Area, or the area it will have once the vertices are rounded."""
    if geometry is None or geometry.isEmpty():
        return 0.0
    if coord_decimals is None:
        return geometry.area()
    return _rounded(geometry, coord_decimals).area()


def _finalize(geometry: QgsGeometry,
              coord_decimals: Optional[int]) -> QgsGeometry:
    if coord_decimals is None or geometry.isEmpty():
        return geometry
    return _rounded(geometry, coord_decimals)


def _oriented_normal(polygon: QgsGeometry, origin: QgsPointXY,
                     u: Tuple[float, float],
                     n: Tuple[float, float]) -> Tuple[float, float]:
    """Flip n so the first piece comes off the side nearest the drawn line, whichever way the line was drawn. When it crosses the polygon the side with the nearer extreme wins, which is at least deterministic."""
    d_values, _s = _projections(polygon, origin, u, n)
    if d_values and abs(max(d_values)) < abs(min(d_values)):
        return (-n[0], -n[1])
    return n


def _projections(geometry: QgsGeometry, origin: QgsPointXY,
                 u: Tuple[float, float],
                 n: Tuple[float, float]) -> Tuple[List[float], List[float]]:
    """Every vertex projected onto the normal and direction axes."""
    d_values, s_values = [], []
    for v in geometry.vertices():
        rx, ry = v.x() - origin.x(), v.y() - origin.y()
        d_values.append(n[0] * rx + n[1] * ry)
        s_values.append(u[0] * rx + u[1] * ry)
    return d_values, s_values


def _cut_rect(origin: QgsPointXY, u: Tuple[float, float],
              n: Tuple[float, float], top: float, bottom: float,
              s_lo: float, s_hi: float) -> QgsGeometry:
    """Rectangle covering the half-plane below offset top. Its top edge sits on the cut line and the other three are far outside the polygon, so intersecting with it is a half-plane clip."""
    def corner(d, s):
        return QgsPointXY(origin.x() + n[0] * d + u[0] * s,
                          origin.y() + n[1] * d + u[1] * s)
    ring = [corner(top, s_lo), corner(top, s_hi),
            corner(bottom, s_hi), corner(bottom, s_lo)]
    ring.append(QgsPointXY(ring[0]))
    return QgsGeometry.fromPolygonXY([ring])


def _frame(geometry: QgsGeometry, origin: QgsPointXY, u, n):
    """Per-round bounds - the offset bracket and the rect extents."""
    d_values, s_values = _projections(geometry, origin, u, n)
    if not d_values:
        raise DetachError(_tr(
            "Nothing is left of the polygon for the next target area."))
    bbox = geometry.boundingBox()
    margin = 10.0 * math.hypot(bbox.width(), bbox.height())
    if margin <= 0.0:
        raise DetachError(_tr(
            "The polygon is degenerate - it has no extent to cut."))
    return (min(d_values), max(d_values),
            min(s_values) - margin, max(s_values) + margin, margin)


def _bisect_cut(current: QgsGeometry, origin: QgsPointXY, u, n,
                target: float, tolerance: float,
                coord_decimals: Optional[int]
                ) -> Tuple[QgsGeometry, QgsGeometry]:
    """Find the cut whose piece measures target, as (piece, rect). The rect comes back so the caller subtracts exactly what it intersected with - piece and remainder then share boundary coordinates and nothing leaks between them."""
    lo, hi, s_lo, s_hi, margin = _frame(current, origin, u, n)
    extent = hi - lo
    if extent <= 0.0:
        raise DetachError(_tr(
            "The polygon is degenerate along the cut direction."))
    bottom = lo - margin

    area = 0.0
    best_piece, best_rect, best_dev = None, None, math.inf
    best_area = 0.0
    for _ in range(_MAX_ITERATIONS):
        mid = 0.5 * (lo + hi)
        rect = _cut_rect(origin, u, n, mid, bottom, s_lo, s_hi)
        piece = _polygonal(current.intersection(rect))
        area = _measure(piece, coord_decimals)
        deviation = abs(area - target)
        if deviation <= tolerance:
            return piece, rect
        if deviation < best_dev:
            best_piece, best_rect, best_dev = piece, rect, deviation
            best_area = area
        if hi - lo <= _BRACKET_EPS * extent:
            break
        if area < target:
            lo = mid
        else:
            hi = mid
    # in mm mode the coordinate grid can put the exact target out of reach, so take the nearest achievable piece within the ceiling
    if (coord_decimals is not None and best_piece is not None
            and best_dev <= max(tolerance, _MM_MAX_DEVIATION)):
        return best_piece, best_rect
    # the last probed offset isn't the nearest one, report what the search actually kept as its best candidate
    closest = best_area if best_piece is not None else area
    raise DetachError(_tr(
        "Could not reach the target area {0:.3f} within tolerance "
        "{1:g} - closest achievable was {2:.3f}. In millimeter mode "
        "the target must be attainable on the coordinate grid.").format(
            target, tolerance, closest))


def _append_piece(pieces: List[DetachPiece], geometry: QgsGeometry,
                  target: Optional[float], index: int,
                  split_fragments: bool) -> None:
    if split_fragments and not geometry.isEmpty():
        parts = [part for part in geometry.asGeometryCollection()
                 if not part.isEmpty()]
        if len(parts) > 1:
            for part in parts:
                pieces.append(DetachPiece(target, part, part.area(), index))
            return
    pieces.append(DetachPiece(target, geometry, geometry.area(), index))


def detach_by_areas(polygon: QgsGeometry, direction, targets: Sequence[float],
                    *, tolerance: float = 0.001,
                    coord_decimals: Optional[int] = None,
                    split_fragments: bool = False) -> DetachResult:
    """Cut the areas in targets off polygon, parallel to direction, taking pieces from the side nearest the drawn line and each cut working on what the last one left. Leftover comes back as a final piece with target None, or gets absorbed by the last target when the whole polygon is allocated - the pieces always partition the input, no geometry is ever dropped. Raises DetachError on empty input, a zero-length direction, bad targets, over-allocation or an unreachable area."""
    if polygon is None or polygon.isNull() or polygon.isEmpty():
        raise DetachError(_tr(
            "No polygon to detach from - the input geometry is empty."))
    if (QgsWkbTypes.geometryType(polygon.wkbType())
            != QgsWkbTypes.GeometryType.PolygonGeometry):
        raise DetachError(_tr("The input geometry must be a polygon."))
    u, n = _unit_and_normal(direction)
    if not targets:
        raise DetachError(_tr("No target areas were given."))
    checked = []
    for i, t in enumerate(targets):
        # bool is an int subclass, don't let True quietly become target 1.0
        if (isinstance(t, bool) or not isinstance(t, (int, float))
                or not math.isfinite(t) or t <= 0.0):
            raise DetachError(_tr(
                "Target area #{0} must be a positive number, got {1!r}."
            ).format(i + 1, t))
        checked.append(float(t))
    targets = checked
    if not math.isfinite(tolerance) or tolerance <= 0.0:
        raise DetachError(_tr("The tolerance must be a positive number."))

    working = QgsGeometry(polygon)
    fixed_input = False
    if not working.isGeosValid():
        working = _polygonal(working.makeValid())
        if working.isEmpty():
            raise DetachError(_tr(
                "The input polygon is invalid and could not be repaired."))
        fixed_input = True

    total = _measure(working, coord_decimals)
    allocated = math.fsum(targets)
    if allocated > total + tolerance:
        raise DetachError(_tr(
            "The target areas sum to {0:.3f} but the polygon measures "
            "only {1:.3f}.").format(allocated, total))

    origin = QgsPointXY(direction[0])
    # orientation is decided once on the whole input, so a shape change mid-sequence can't flip which side later cuts come from
    n = _oriented_normal(working, origin, u, n)
    pieces: List[DetachPiece] = []
    current = working
    last = len(targets) - 1
    for index, target in enumerate(targets):
        if index == last:
            # if what's left already matches the final target within the drift the earlier cuts accumulated, hand it over whole rather than cut it and risk dropping a hairline sliver
            drift_band = tolerance * len(targets)
            if abs(_measure(current, coord_decimals) - target) <= drift_band:
                _append_piece(pieces, _finalize(current, coord_decimals),
                              target, index, split_fragments)
                current = QgsGeometry()
                break
        piece, rect = _bisect_cut(current, origin, u, n, target,
                                  tolerance, coord_decimals)
        current = _polygonal(current.difference(rect))
        if (index == last and not current.isEmpty()
                and current.area() <= max(tolerance, _DUST)):
            # never throw geometry away - a sub-tolerance strip along the last cut merges back into the last piece so the output still covers the input
            piece = _polygonal(piece.combine(current))
            current = QgsGeometry()
        _append_piece(pieces, _finalize(piece, coord_decimals),
                      target, index, split_fragments)

    if not current.isEmpty():
        remainder = _finalize(current, coord_decimals)
        if remainder.area() > max(tolerance, _DUST):
            _append_piece(pieces, remainder, None, len(targets),
                          split_fragments)
        elif pieces and not remainder.isEmpty():
            # rounding shrank the leftover under the threshold, fold it into the last piece rather than lose it
            tail = pieces[-1]
            merged = _polygonal(tail.geometry.combine(remainder))
            pieces[-1] = DetachPiece(tail.target, merged, merged.area(),
                                     tail.index)

    return DetachResult(pieces=pieces, total_area=total,
                        fixed_input=fixed_input)
