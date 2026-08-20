# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Voronoi-based medial axis for polygons - densify the boundary, take the Voronoi edges that fall inside, pull the longest path out of that graph or prune the spurs, merge into one line, trim the corner spurs off the ends, straighten out the sampling zigzag, optionally extend to the boundary, then smooth with Chaikin. extract_centerlines() runs it inline, CenterlineTask off the UI thread."""

# shapely is the only external dependency and ships with most QGIS installs, but callers still have to check HAS_SHAPELY first

import heapq
import traceback
from collections import defaultdict

from qgis.core import (  # type: ignore
    Qgis, QgsFeature, QgsFeatureRequest, QgsField, QgsFields, QgsGeometry,
    QgsMessageLog, QgsTask, QgsVectorLayer, QgsVectorLayerFeatureSource,
    QgsWkbTypes,
)

from ..qt_compat import FIELD_DOUBLE, FIELD_LONGLONG

_LOG_TAG = "Vernier"

# cap on boundary samples per polygon, the Voronoi and graph passes are superlinear in it
_MAX_BOUNDARY_POINTS = 20000

try:
    from shapely import wkt as shapely_wkt
    from shapely.geometry import (
        LineString, MultiLineString, MultiPoint, MultiPolygon, Point, Polygon,
    )
    from shapely.ops import linemerge, voronoi_diagram
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False


def _log(message, level=Qgis.MessageLevel.Warning):
    QgsMessageLog.logMessage(message, _LOG_TAG, level)


def _c2d(coord):
    return (float(coord[0]), float(coord[1]))


def _coords2d(coords):
    return [_c2d(c) for c in coords]


def _densify_boundary(polygon, distance):
    """Sample the boundary, exterior and holes, at regular intervals."""
    points = []
    try:
        boundary = polygon.exterior
        length = boundary.length
        if length == 0:
            return points

        # a long boundary at a fine step feeds hundreds of thousands of points into the Voronoi pass and freezes the UI, so widen the step - lose resolution rather than responsiveness
        total = length + sum(ring.length for ring in polygon.interiors)
        distance = max(distance, total / _MAX_BOUNDARY_POINTS)

        num_points = max(int(length / distance), 4)
        step = length / num_points
        for i in range(num_points):
            pt = boundary.interpolate(i * step)
            points.append((pt.x, pt.y))

        for interior in polygon.interiors:
            int_length = interior.length
            if int_length == 0:
                continue
            int_num = max(int(int_length / distance), 4)
            int_step = int_length / int_num
            for i in range(int_num):
                pt = interior.interpolate(i * int_step)
                points.append((pt.x, pt.y))
    except Exception as e:
        _log(f"densify_boundary: {e}")
    return points


def _iter_lines(geom):
    """Every LineString inside a geometry, however nested."""
    if geom is None or geom.is_empty:
        return
    if geom.geom_type == "LineString":
        yield geom
    elif hasattr(geom, "geoms"):
        for part in geom.geoms:
            yield from _iter_lines(part)


def _voronoi_edges_inside(polygon, densify_dist):
    """The Voronoi edges of the densified boundary that fall inside the polygon."""
    points = _densify_boundary(polygon, densify_dist)
    # coincident points degenerate the diagram, and dict keeps the order
    points = list(dict.fromkeys(points))
    if len(points) < 4:
        return []

    try:
        diagram = voronoi_diagram(MultiPoint(points), edges=True)
    except Exception as e:
        _log(f"Voronoi computation: {e}")
        return []

    # tiny outward buffer so edges that only touch the boundary still count as inside. GEOS clips the diagram's outer edges to a big envelope and those fail the test, which is what we want
    try:
        poly_test = polygon.buffer(densify_dist * 0.01)
    except Exception as e:
        _log(f"voronoi_edges_inside buffer fallback: {e}")
        poly_test = polygon

    lines = []
    for line in _iter_lines(diagram):
        try:
            coords = list(line.coords)
            c1 = _c2d(coords[0])
            c2 = _c2d(coords[-1])
            if (polygon.contains(Point(c1)) and polygon.contains(Point(c2))
                    and poly_test.contains(line)):
                lines.append(LineString(_coords2d(coords)))
        except Exception as e:
            _log(f"Voronoi edge filter: {e}")
            continue
    return lines


def _build_graph(lines):
    """Adjacency graph from the segments, coords rounded, edges weighted by length."""
    PREC = 6

    def sn(c):
        return (round(float(c[0]), PREC), round(float(c[1]), PREC))

    graph = defaultdict(list)
    for i, line in enumerate(lines):
        coords = list(line.coords)
        s = sn(coords[0])
        e = sn(coords[-1])
        if s == e:
            continue
        w = float(line.length)
        graph[s].append((e, w, i))
        graph[e].append((s, w, i))
    return graph


def _extract_trunk(lines):
    """Pull the longest path out with a double Dijkstra over the terminals."""
    if not lines or len(lines) <= 1:
        return lines

    try:
        graph = _build_graph(lines)
        if not graph:
            return lines

        terminals = [n for n, edges in graph.items() if len(edges) == 1]
        if len(terminals) < 2:
            terminals = list(graph.keys())
        if len(terminals) < 2:
            return lines

        def dijkstra(start):
            dist = {start: 0.0}
            parent = {start: (None, None)}
            counter = 0
            pq = [(0.0, counter, start)]
            while pq:
                d, _, node = heapq.heappop(pq)
                if d > dist.get(node, float("inf")):
                    continue
                for neighbor, weight, line_idx in graph[node]:
                    nd = d + weight
                    if nd < dist.get(neighbor, float("inf")):
                        dist[neighbor] = nd
                        parent[neighbor] = (node, line_idx)
                        counter += 1
                        heapq.heappush(pq, (nd, counter, neighbor))
            return dist, parent

        # farthest terminal from an arbitrary start, then the farthest from that one. standard tree-diameter double sweep
        dist1, _ = dijkstra(terminals[0])
        far_a = max(terminals, key=lambda n: dist1.get(n, 0.0))
        dist2, parent2 = dijkstra(far_a)
        far_b = max(terminals, key=lambda n: dist2.get(n, 0.0))

        trunk_idx = set()
        cur = far_b
        for _ in range(len(lines) + 10):
            p = parent2.get(cur)
            if p is None or p[0] is None:
                break
            if p[1] is not None:
                trunk_idx.add(p[1])
            cur = p[0]

        if trunk_idx:
            return [line for i, line in enumerate(lines) if i in trunk_idx]

    except Exception as e:
        _log(f"extract_trunk: {e}\n{traceback.format_exc()}")

    return lines


def _prune_branches(lines, min_length):
    """Iteratively drop leaf chains shorter than min_length. A chain runs from a degree-1 node through degree-2 nodes to the first junction - the raw axis is hundreds of short segments in a row, so measuring per segment instead of per chain would eat the whole network tip-first."""
    if not lines:
        return lines

    PREC = 6

    def sn(c):
        return (round(float(c[0]), PREC), round(float(c[1]), PREC))

    current = list(lines)
    for _ in range(50):
        if not current:
            break

        adj = defaultdict(list)
        for i, line in enumerate(current):
            coords = list(line.coords)
            s = sn(coords[0])
            e = sn(coords[-1])
            # a degenerate segment whose ends round to one node would double-append there, inflating the degree and faking a junction
            if s == e:
                continue
            adj[s].append((e, i))
            adj[e].append((s, i))

        remove = set()
        for node, edges in adj.items():
            if len(edges) != 1:
                continue
            nxt, idx = edges[0]
            chain = {idx}
            length = current[idx].length
            spur = False
            while length < min_length:
                nbrs = adj[nxt]
                if len(nbrs) == 1:
                    # a bare path down to another leaf - removing it would erase the whole component
                    break
                if len(nbrs) > 2:
                    spur = True
                    break
                (n1, i1), (n2, i2) = nbrs
                nxt, idx = (n1, i1) if i1 != idx else (n2, i2)
                if idx in chain:
                    break
                chain.add(idx)
                length += current[idx].length
            if spur and length < min_length:
                remove.update(chain)

        if not remove:
            break
        current = [seg for i, seg in enumerate(current) if i not in remove]

    return current


def _snap_and_merge(lines, tolerance=1e-4):
    """Snap nearby endpoints together, then linemerge into one continuous line."""
    if not lines:
        return None
    if len(lines) == 1:
        return lines[0]

    try:
        clean = []
        for line in lines:
            c = _coords2d(line.coords)
            if len(c) >= 2:
                clean.append(LineString(c))
        if not clean:
            return None
        if len(clean) == 1:
            return clean[0]

        eps = []
        for line in clean:
            c = list(line.coords)
            eps.append(c[0])
            eps.append(c[-1])

        # group nearby endpoints onto canonical representatives, bucketed on a grid of cell size tolerance so a match can only be in the 3x3 cells around the endpoint. scanning every canon is quadratic in the edge count
        tol_sq = tolerance * tolerance
        cell = tolerance if tolerance > 0 else 1.0
        buckets = {}
        cmap = {}
        for ep in eps:
            cx, cy = int(ep[0] // cell), int(ep[1] // cell)
            matched = None
            for ix in (cx - 1, cx, cx + 1):
                for iy in (cy - 1, cy, cy + 1):
                    for cn in buckets.get((ix, iy), ()):
                        dx = ep[0] - cn[0]
                        dy = ep[1] - cn[1]
                        if (dx * dx + dy * dy) < tol_sq:
                            matched = cn
                            break
                    if matched is not None:
                        break
                if matched is not None:
                    break
            if matched is not None:
                cmap[ep] = matched
            else:
                buckets.setdefault((cx, cy), []).append(ep)
                cmap[ep] = ep

        snapped = []
        for line in clean:
            new_c = [cmap.get(c, c) for c in line.coords]
            # drop consecutive duplicates the snapping may have made
            dd = [new_c[0]]
            for c in new_c[1:]:
                if abs(c[0] - dd[-1][0]) > 1e-12 or abs(c[1] - dd[-1][1]) > 1e-12:
                    dd.append(c)
            if len(dd) >= 2:
                snapped.append(LineString(dd))

        if not snapped:
            return None
        return linemerge(MultiLineString(snapped))

    except Exception as e:
        # the caller retries linemerge on the raw segments and treats a second failure as a logged skip - better a visible skip than one arbitrary fragment shipped as a centerline
        _log(f"snap_and_merge: {e}")
        return None


def _trim_corner_tails(line, polygon, densify_dist):
    """Cut the corner spurs off the trunk ends. The longest-path trunk always turns into a corner branch at each end of the polygon, because the branch adds length - so the raw axis runs corner to corner instead of mid-end to mid-end. Along a corner branch the clearance to the boundary climbs from near zero up to the local half-width where the branch joins the true axis, so walk inward from each end while the clearance still climbs and drop the climb. An end that is already central has a flat profile and is left alone."""
    if not isinstance(line, LineString):
        return line

    try:
        coords = _coords2d(line.coords)
        if len(coords) < 3:
            return line

        boundary = polygon.boundary
        # the clearance ratchet: each accepted step must climb by eps over the last accepted vertex, so plateau noise self-arrests instead of crawling down the axis
        eps = densify_dist * 0.1
        max_walk = line.length * 0.4

        def climb(pts):
            clear = boundary.distance(Point(pts[0]))
            start = clear
            cut = 0
            walked = 0.0
            i = 0
            limit = len(pts) - 2  # always leave two vertices
            while i < limit:
                dx = pts[i + 1][0] - pts[i][0]
                dy = pts[i + 1][1] - pts[i][1]
                walked += (dx * dx + dy * dy) ** 0.5
                if walked > max_walk:
                    break
                nxt = boundary.distance(Point(pts[i + 1]))
                if nxt > clear + eps:
                    clear = nxt
                    i += 1
                    cut = i
                    continue
                # one flat or dipping vertex is allowed if the climb resumes right after
                if i + 2 <= limit:
                    nxt2 = boundary.distance(Point(pts[i + 2]))
                    if nxt2 > clear + 2 * eps:
                        clear = nxt2
                        i += 2
                        cut = i
                        continue
                break
            # only a real spur climbs substantially
            if clear > start * 1.3 + eps:
                return cut
            return 0

        front = climb(coords)
        if front:
            coords = coords[front:]
        if len(coords) >= 3:
            back = climb(coords[::-1])
            if back:
                coords = coords[:-back]

        if len(coords) < 2:
            return line
        return LineString(coords)

    except Exception as e:
        _log(f"trim_corner_tails: {e}")
        return line


def _straighten_geometry(geom, tolerance):
    """Douglas-Peucker the sampling zigzag away. The Voronoi axis wobbles at the densify scale even inside a perfectly straight polygon; smoothing only rounds that wobble, simplification removes it."""
    if geom is None or geom.is_empty or tolerance <= 0:
        return geom
    try:
        # endpoints survive simplification, so junctions between the parts of a network stay connected
        simplified = geom.simplify(tolerance, preserve_topology=False)
        if simplified is None or simplified.is_empty:
            return geom
        return simplified
    except Exception as e:
        _log(f"straighten_geometry: {e}")
        return geom


def _extend_to_boundary(centerline, polygon, max_extension=None):
    """Push the centerline endpoints out to the polygon boundary. Each endpoint gets re-centered on the perpendicular cross-section first, then projected outward, so the extension stays on the axis instead of drifting to one side."""
    if centerline is None or centerline.is_empty:
        return centerline

    try:
        if isinstance(centerline, MultiLineString):
            parts = []
            for line in centerline.geoms:
                ext = _extend_to_boundary(line, polygon, max_extension)
                if ext is not None:
                    parts.append(ext)
            if not parts:
                return centerline
            return MultiLineString(parts) if len(parts) > 1 else parts[0]

        if not isinstance(centerline, LineString):
            return centerline

        coords = _coords2d(centerline.coords)
        if len(coords) < 2:
            return centerline

        if max_extension is None:
            # relative to the polygon, not absolute layer units - 1.0 would be a whole degree in a geographic CRS
            max_extension = max(centerline.length * 0.5,
                                polygon.length * 0.01)

        boundary = polygon.boundary

        def _get_points_from_intersection(inter):
            pts = []
            if inter is None or inter.is_empty:
                return pts
            gt = inter.geom_type
            if gt == "Point":
                pts.append((inter.x, inter.y))
            elif gt == "MultiPoint":
                for p in inter.geoms:
                    pts.append((p.x, p.y))
            elif gt == "LineString":
                c = list(inter.coords)
                if len(c) >= 2:
                    pts.append(_c2d(c[0]))
                    pts.append(_c2d(c[-1]))
                elif len(c) == 1:
                    pts.append(_c2d(c[0]))
            elif hasattr(inter, "geoms"):
                for g in inter.geoms:
                    pts.extend(_get_points_from_intersection(g))
            return pts

        def _extend_centered(endpoint, prev_pt, ext_dist):
            try:
                ex, ey = endpoint
                dx = ex - prev_pt[0]
                dy = ey - prev_pt[1]
                seg_len = (dx * dx + dy * dy) ** 0.5
                if seg_len < 1e-12:
                    return None
                dx /= seg_len
                dy /= seg_len

                # perpendicular, 90 degree rotation
                px, py = -dy, dx

                # estimate the polygon width at the endpoint with a long perpendicular cut
                half_w = ext_dist * 3
                perp_line = LineString([
                    (ex - px * half_w, ey - py * half_w),
                    (ex + px * half_w, ey + py * half_w),
                ])
                perp_pts = _get_points_from_intersection(
                    perp_line.intersection(boundary))

                if len(perp_pts) >= 2:
                    def proj_on_perp(pt):
                        return (pt[0] - ex) * px + (pt[1] - ey) * py

                    perp_pts.sort(key=proj_on_perp)
                    # scaled to the cut rather than absolute layer units, it only has to absorb float noise when a crossing lands exactly on the endpoint
                    tol = half_w * 1e-6
                    neg_pts = [p for p in perp_pts if proj_on_perp(p) <= tol]
                    pos_pts = [p for p in perp_pts if proj_on_perp(p) >= -tol]
                    if neg_pts and pos_pts:
                        left = max(neg_pts, key=proj_on_perp)
                        right = min(pos_pts, key=proj_on_perp)
                        mid_x = (left[0] + right[0]) / 2.0
                        mid_y = (left[1] + right[1]) / 2.0
                    else:
                        mid_x = (perp_pts[0][0] + perp_pts[-1][0]) / 2.0
                        mid_y = (perp_pts[0][1] + perp_pts[-1][1]) / 2.0
                else:
                    mid_x, mid_y = ex, ey

                # from the cross-section center, out to the boundary
                ray = LineString([
                    (mid_x, mid_y),
                    (mid_x + dx * ext_dist, mid_y + dy * ext_dist),
                ])
                ray_pts = _get_points_from_intersection(
                    ray.intersection(boundary))
                if ray_pts:
                    hit = min(
                        ray_pts,
                        key=lambda c: (c[0] - mid_x) ** 2 + (c[1] - mid_y) ** 2,
                    )
                    if boundary.distance(Point(hit)) < ext_dist * 0.05:
                        return hit

                # fallback: the cross-section center, if it is inside
                if polygon.contains(Point(mid_x, mid_y)):
                    return (mid_x, mid_y)
                return None

            except Exception as e:
                _log(f"extend_centered: {e}")
                return None

        new_coords = list(coords)
        ns = _extend_centered(coords[0], coords[1], max_extension)
        if ns is not None:
            new_coords[0] = ns
        ne = _extend_centered(coords[-1], coords[-2], max_extension)
        if ne is not None:
            new_coords[-1] = ne
        return LineString(new_coords)

    except Exception as e:
        _log(f"extend_to_boundary: {e}")
        return centerline


def _chaikin_smooth(coords, iterations=3):
    """Chaikin corner-cutting smoothing."""
    if len(coords) < 3:
        return coords

    current = list(coords)
    for _ in range(iterations):
        new = [current[0]]
        for i in range(len(current) - 1):
            p1, p2 = current[i], current[i + 1]
            new.append((0.75 * p1[0] + 0.25 * p2[0],
                        0.75 * p1[1] + 0.25 * p2[1]))
            new.append((0.25 * p1[0] + 0.75 * p2[0],
                        0.25 * p1[1] + 0.75 * p2[1]))
        new.append(current[-1])
        current = new
    return current


def _smooth_geometry(geom, iterations=3):
    """Apply Chaikin smoothing to a LineString / MultiLineString."""
    if geom is None or geom.is_empty:
        return geom
    try:
        if isinstance(geom, LineString):
            return LineString(
                _chaikin_smooth(_coords2d(geom.coords), iterations))
        if isinstance(geom, MultiLineString):
            return MultiLineString([
                LineString(_chaikin_smooth(_coords2d(line.coords), iterations))
                for line in geom.geoms
            ])
    except Exception as e:
        _log(f"smooth_geometry: {e}")
    return geom


def _extract_centerline(polygon, *, densify_distance, straighten,
                        straighten_tolerance, smooth, smooth_iterations,
                        prune, prune_min_length, trunk_only, extend_to_ends):
    """The whole pipeline for one shapely polygon. No defaults on purpose - extract_centerlines and CenterlineTask supply every option, so the public signatures stay the owners of the defaults."""

    if polygon is None or polygon.is_empty:
        return None

    try:
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
            if polygon is None or polygon.is_empty:
                return None
    except Exception as e:
        _log(f"extract_centerline validity fix failed: {e}")
        return None

    # a MultiPolygon gets done part by part, then merged
    if isinstance(polygon, MultiPolygon):
        all_lines = []
        for poly in polygon.geoms:
            try:
                r = _extract_centerline(
                    poly, densify_distance=densify_distance,
                    straighten=straighten,
                    straighten_tolerance=straighten_tolerance,
                    smooth=smooth, smooth_iterations=smooth_iterations,
                    prune=prune, prune_min_length=prune_min_length,
                    trunk_only=trunk_only, extend_to_ends=extend_to_ends)
                if r and not r.is_empty:
                    if isinstance(r, MultiLineString):
                        all_lines.extend(r.geoms)
                    else:
                        all_lines.append(r)
            except Exception as e:
                _log(f"extract_centerline MultiPolygon part: {e}")
                continue
        if not all_lines:
            return None
        try:
            return linemerge(MultiLineString(all_lines))
        except Exception as e:
            _log(f"extract_centerline MultiPolygon merge fallback: {e}")
            return MultiLineString(all_lines)

    if not isinstance(polygon, Polygon):
        return None

    # too small to carry a meaningful axis at this sampling interval
    if polygon.area < densify_distance * densify_distance:
        return None

    try:
        edges = _voronoi_edges_inside(polygon, densify_distance)
        if not edges:
            return None

        if trunk_only:
            edges = _extract_trunk(edges)
        elif prune:
            ml = prune_min_length if prune_min_length else densify_distance * 3.0
            edges = _prune_branches(edges, ml)
        if not edges:
            return None

        merged = _snap_and_merge(edges, tolerance=densify_distance * 0.1)
        if merged is None or merged.is_empty:
            merged = linemerge(MultiLineString(edges))
        if merged is None or merged.is_empty:
            return None

        if straighten:
            # trim before simplifying - simplification never drops endpoints, so it cannot remove a corner spur on its own. both run before the extension so the endpoint direction comes from the clean axis
            merged = _trim_corner_tails(merged, polygon, densify_distance)
            tol = (straighten_tolerance if straighten_tolerance is not None
                   else densify_distance * 0.8)
            merged = _straighten_geometry(merged, tol)

        if extend_to_ends:
            merged = _extend_to_boundary(merged, polygon)

        if smooth and smooth_iterations > 0:
            merged = _smooth_geometry(merged, smooth_iterations)

        return merged

    except Exception as e:
        _log(f"extract_centerline: {e}\n{traceback.format_exc()}")
        return None


def _qgs_to_shapely(qgs_geom):
    # shapely can't parse the CURVEPOLYGON/CIRCULARSTRING WKT QGIS emits for arcs, and GEOS aborts the process instead of raising, so no try/except upstream would catch it. segment first
    if QgsWkbTypes.isCurvedType(qgs_geom.wkbType()):
        qgs_geom = QgsGeometry(qgs_geom)
        qgs_geom.convertToStraightSegment()
    return shapely_wkt.loads(qgs_geom.asWkt())


def _shapely_to_qgs(shapely_geom):
    if shapely_geom is None or shapely_geom.is_empty:
        return None
    return QgsGeometry.fromWkt(shapely_geom.wkt)


def centerline_fields(source_fields):
    """Output fields - the source layer's, plus cl_length and cl_source_id. The appended names get deduped: a source that already carries a cl_length would make addAttributes reject the duplicate and then quietly shift every appended value one column left."""
    fields = QgsFields()
    taken = set()
    for field in source_fields:
        fields.append(QgsField(field))
        taken.add(field.name().lower())

    def _unique(name):
        candidate, suffix = name, 1
        while candidate.lower() in taken:
            suffix += 1
            candidate = f"{name}_{suffix}"
        taken.add(candidate.lower())
        return candidate

    fields.append(QgsField(_unique("cl_length"), FIELD_DOUBLE))
    fields.append(QgsField(_unique("cl_source_id"), FIELD_LONGLONG))
    return fields


def build_output_layer(name, crs, out_fields, out_features):
    """Memory layer holding the extraction result. Main thread only - creating a QgsVectorLayer off it is not safe."""
    # MultiLineString so single- and multi-part results share one layer, and setCrs rather than a ?crs= URI because custom CRS have an empty authid
    layer = QgsVectorLayer("MultiLineString", name, "memory")
    layer.setCrs(crs)
    provider = layer.dataProvider()
    provider.addAttributes(list(out_fields))
    layer.updateFields()
    if out_features:
        provider.addFeatures(out_features)
        layer.updateExtents()
    return layer


def _process_features(features, out_fields, *, total, densify_distance,
                      straighten, straighten_tolerance, smooth,
                      smooth_iterations, trunk_only, prune, prune_min_length,
                      extend_to_ends, progress_callback=None,
                      should_cancel=None):
    """The per-feature loop, as (out_features, ok, skipped, errors). Touches nothing but value types and shapely, so it is safe on a worker thread. No defaults on purpose - the callers own them, same as _extract_centerline."""
    success_count = 0
    error_count = 0
    skip_count = 0
    features_out = []

    for i, feature in enumerate(features):
        if should_cancel is not None and should_cancel():
            break

        if progress_callback is not None:
            status = (
                f"Feature {i + 1}/{total} "
                f"(ok: {success_count}, skipped: {skip_count}, "
                f"errors: {error_count})")
            try:
                progress_callback(i + 1, total, status)
            except Exception as e:
                _log(f"progress_callback error: {e}")

        geom = feature.geometry()
        if geom is None or geom.isEmpty():
            skip_count += 1
            continue

        try:
            shapely_geom = _qgs_to_shapely(geom)
            if shapely_geom is None or shapely_geom.is_empty:
                skip_count += 1
                continue

            centerline = _extract_centerline(
                shapely_geom, densify_distance=densify_distance,
                straighten=straighten,
                straighten_tolerance=straighten_tolerance,
                smooth=smooth, smooth_iterations=smooth_iterations,
                prune=prune, prune_min_length=prune_min_length,
                trunk_only=trunk_only, extend_to_ends=extend_to_ends)
            if centerline is None or centerline.is_empty:
                skip_count += 1
                continue

            qgs_centerline = _shapely_to_qgs(centerline)
            if qgs_centerline is None or qgs_centerline.isEmpty():
                skip_count += 1
                continue
            qgs_centerline.convertToMultiType()

            # output fields are the source fields plus the two cl_ ones at the end, so attributes() + [...] lines up positionally
            out_feat = QgsFeature(out_fields)
            out_feat.setGeometry(qgs_centerline)
            out_feat.setAttributes(
                feature.attributes()
                + [round(centerline.length, 3), feature.id()])
            features_out.append(out_feat)
            success_count += 1

        except Exception as e:
            error_count += 1
            _log(f"Feature {feature.id()}: {e}\n{traceback.format_exc()}")

    return features_out, success_count, skip_count, error_count


def extract_centerlines(
    layer,
    densify_distance=1.0,
    straighten=True,
    straighten_tolerance=None,
    smooth=False,
    smooth_iterations=3,
    trunk_only=True,
    prune=False,
    prune_min_length=None,
    extend_to_ends=True,
    output_name="Centerline",
    selected_only=False,
    progress_callback=None,
):
    """Extract centerlines from a layer's polygons, as (output_layer, success, skipped, errors). The output is a MultiLineString memory layer in the input CRS, None when nothing came out, and each feature copies the source attributes plus cl_length and cl_source_id. Blocking; CenterlineTask is the same work off the UI thread."""
    if layer is None:
        return (None, 0, 0, 0)

    if layer.geometryType() != QgsWkbTypes.PolygonGeometry:
        _log("extract_centerlines: layer is not polygon type")
        return (None, 0, 0, 0)

    if selected_only:
        # take the flag literally - an empty selection means nothing to do, not "do the whole layer". the dialog's checkbox can stay ticked after the canvas selection is cleared
        features = list(layer.selectedFeatures())
    else:
        features = list(layer.getFeatures())

    feature_count = len(features)
    if feature_count == 0:
        return (None, 0, 0, 0)

    out_fields = centerline_fields(layer.fields())
    features_out, success_count, skip_count, error_count = _process_features(
        features, out_fields, total=feature_count,
        densify_distance=densify_distance, straighten=straighten,
        straighten_tolerance=straighten_tolerance, smooth=smooth,
        smooth_iterations=smooth_iterations, trunk_only=trunk_only,
        prune=prune, prune_min_length=prune_min_length,
        extend_to_ends=extend_to_ends, progress_callback=progress_callback)

    _log(
        f"Centerline extraction done: {success_count} ok, "
        f"{skip_count} skipped, {error_count} errors "
        f"(total {feature_count})",
        Qgis.MessageLevel.Info)

    output_layer = (
        build_output_layer(output_name, layer.crs(), out_fields, features_out)
        if features_out else None)
    return (output_layer, success_count, skip_count, error_count)


class CenterlineTask(QgsTask):
    """Centerline extraction off the UI thread. Everything that reaches into the layer - the feature source, the selection, the fields, the CRS - is captured in __init__ on the main thread, so run() sees nothing but value types and shapely. The result comes back as plain features rather than a layer, because build_layer() has to happen on the main thread too."""

    def __init__(self, layer,
                 densify_distance=1.0,
                 straighten=True,
                 straighten_tolerance=None,
                 smooth=False,
                 smooth_iterations=3,
                 trunk_only=True,
                 prune=False,
                 prune_min_length=None,
                 extend_to_ends=True,
                 output_name="Centerline",
                 selected_only=False,
                 finished_cb=None):
        super().__init__(f"Centerline: {layer.name()}", QgsTask.CanCancel)
        # QgsVectorLayerFeatureSource exists for exactly this - a snapshot of the layer's data taken on the main thread that a worker can then iterate safely
        self._source = QgsVectorLayerFeatureSource(layer)
        self._fids = (list(layer.selectedFeatureIds()) if selected_only
                      else None)
        self._crs = layer.crs()
        self._out_fields = centerline_fields(layer.fields())
        self._output_name = output_name
        self._total = (len(self._fids) if self._fids is not None
                       else layer.featureCount())
        self._options = {
            "densify_distance": densify_distance,
            "straighten": straighten,
            "straighten_tolerance": straighten_tolerance,
            "smooth": smooth,
            "smooth_iterations": smooth_iterations,
            "trunk_only": trunk_only,
            "prune": prune,
            "prune_min_length": prune_min_length,
            "extend_to_ends": extend_to_ends,
        }
        self.finished_cb = finished_cb
        self.features = []
        self.ok = 0
        self.skipped = 0
        self.errors = 0
        self.error_msg = None

    def run(self):
        try:
            request = QgsFeatureRequest()
            if self._fids is not None:
                request.setFilterFids(self._fids)
            (self.features, self.ok, self.skipped,
             self.errors) = _process_features(
                self._source.getFeatures(request), self._out_fields,
                total=max(self._total, 1), progress_callback=self._report,
                should_cancel=self.isCanceled, **self._options)
        except Exception as e:
            _log(f"Centerline task: {e}\n{traceback.format_exc()}",
                 Qgis.MessageLevel.Critical)
            self.error_msg = str(e) or e.__class__.__name__
            return False

        _log(
            f"Centerline extraction done: {self.ok} ok, "
            f"{self.skipped} skipped, {self.errors} errors "
            f"(total {self._total})",
            Qgis.MessageLevel.Info)
        return not self.isCanceled()

    def _report(self, current, total, _status):
        self.setProgress(100.0 * current / max(total, 1))

    def build_layer(self):
        """Whatever run() produced, as a memory layer. None when nothing came out."""
        if not self.features:
            return None
        return build_output_layer(
            self._output_name, self._crs, self._out_fields, self.features)

    def finished(self, result):
        """Back on the main thread once run() returns."""
        if self.finished_cb is not None:
            self.finished_cb(result, self)
