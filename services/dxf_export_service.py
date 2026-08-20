# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Export vector layers to DXF with style, true colors and labels. ezdxf engine writing LWPOLYLINE / POINT / TEXT, lineweights snapped to the legal values, labels at the pole of inaccessibility with PCA rotation for elongated shapes, and text height that fits inside the polygon."""

import math
import re

from qgis.PyQt.QtCore import QVariant  # type: ignore
from qgis.core import (  # type: ignore
    Qgis, QgsGeometry, QgsMessageLog, QgsRenderContext, QgsWkbTypes,
)

PLUGIN_NAME = "Vernier"

# INSUNITS 6 is meters. R2007+ DXF is UTF-8 internally but AutoCAD still reads DWGCODEPAGE when transcoding, and ANSI_1250 keeps accented layer names and labels out of mojibake. DXF_ENCODING has to stay in step - ezdxf rewrites $DWGCODEPAGE from doc.encoding at save time, so the header value alone never survives
DXF_INSUNITS = 6
DXF_CODEPAGE = "ANSI_1250"
DXF_ENCODING = "cp1250"


# --- color utilities ---

ACI_MAP = [
    (255, 0, 0, 1), (255, 255, 0, 2), (0, 255, 0, 3),
    (0, 255, 255, 4), (0, 0, 255, 5), (255, 0, 255, 6),
    (255, 255, 255, 7), (0, 0, 0, 7),
    (128, 128, 128, 8), (192, 192, 192, 9),
]


def rgb_to_aci(r, g, b):
    """Nearest AutoCAD Color Index, 1-9, for an RGB color."""
    best, bd = 7, float("inf")
    for cr, cg, cb, aci in ACI_MAP:
        d = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
        if d < bd:
            bd, best = d, aci
    return best


def rgb_to_int(r, g, b):
    """Pack RGB into the 24-bit integer DXF's true_color wants."""
    return (r << 16) | (g << 8) | b


# lineweight isn't a free integer, only these 24 values are legal (mm * 100) and AutoCAD/BricsCAD quietly ignore or randomize anything else
_DXF_VALID_LINEWEIGHTS = (
    0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60,
    70, 80, 90, 100, 106, 120, 140, 158, 200, 211,
)


def snap_lineweight(stroke_w_mm):
    """Snap a stroke width in mm to the nearest legal DXF lineweight."""
    raw = int(round(stroke_w_mm * 100))
    if raw <= 0:
        return 0
    return min(_DXF_VALID_LINEWEIGHTS, key=lambda v: abs(v - raw))


# DXF symbol-table names reject these outright and ezdxf raises, which kills the whole export since doc.layers.new() runs outside the per-feature try. names like "Parcele: 2024" hit it on completely ordinary data, so replace rather than fail. this is AutoCAD's set, wider than ezdxf's own, because the output has to open there too
_DXF_INVALID_NAME_CHARS = re.compile(r'[<>/\\":;?*|,=`\x00-\x1f]')


def safe_dxf_layer_name(qgis_name, existing):
    """Sanitize to a DXF-legal name, cut it to the 255-char limit and make it unique against existing."""
    cleaned = _DXF_INVALID_NAME_CHARS.sub("_", str(qgis_name)).strip()
    base = cleaned[:255] or "layer"
    # the DXF layer table is case-insensitive, "Parcele" and "PARCELE" are one layer to AutoCAD, so exact-case comparison would let two QGIS layers collapse into one
    taken = {str(name).lower() for name in existing}
    if base.lower() not in taken:
        return base
    # append _N until it's unique, leaving room for the suffix inside the limit
    n = 1
    while True:
        suffix = f"_{n}"
        candidate = base[:255 - len(suffix)] + suffix
        if candidate.lower() not in taken:
            return candidate
        n += 1


# --- label text assembly ---

def build_label_text(feature, label_fields, separator, use_newline):
    """Build the label from [{"field", "prefix", "suffix"}, ...]. use_newline=True ignores the separator, and an all-empty set gives ""."""
    parts = []
    for cfg in label_fields:
        val = feature.attribute(cfg["field"])
        # check the type, not the text. a value that literally reads "NULL" is data and has to survive
        if val is None or (isinstance(val, QVariant) and val.isNull()):
            continue
        if str(val).strip():
            text = f'{cfg["prefix"]}{str(val).strip()}{cfg["suffix"]}'
            parts.append(text)
    if not parts:
        return ""
    if use_newline:
        join = "\n"
    elif separator != separator.strip():
        join = separator  # they already put spaces in, leave it alone
    else:
        # auto-pad, "," becomes ", " and "/" becomes " / "
        s = separator.strip()
        join = f"{s} " if s == "," else f" {s} " if s else " "
    return join.join(parts)


# --- geometry helpers: label placement, PCA, adaptive text ---

def _dominant_part(geom):
    """The largest part of a multipolygon, or the geometry unchanged. Label position, rotation and text height all have to come off the same part, or a multipolygon led by a sliver gets its label placed on one part and sized from another."""
    try:
        if not geom.isMultipart():
            return geom
        biggest = max(geom.constGet(), key=lambda part: part.area())
        return QgsGeometry(biggest.clone())
    except Exception:
        return geom


def _label_point(geom):
    """(x, y, clearance) for the label - the pole of inaccessibility, the interior point furthest from the boundary, and the radius of the largest circle that fits there. Interior rings count, so the label never lands in the hole of a ring polygon. clearance is 0.0 when it had to fall back."""
    part = _dominant_part(geom)
    box = part.boundingBox()
    width, height = box.width(), box.height()
    # precision is a tolerance in map units, derived from the extent so the answer holds up in metres and degrees alike - a fixed value would either never converge or stop on the first iteration depending on the CRS
    span = min(width, height) or max(width, height)
    if span > 0:
        try:
            point, distance = part.poleOfInaccessibility(span / 100.0)
            clearance = float(distance) if distance is not None else 0.0
            # QGIS seeds the distance out-parameter with DBL_MAX and only overwrites it once the algorithm runs, so a sentinel value means a degenerate polygon and the point that came with it is worth no more than the number. the inscribed radius can never exceed half the short side, so one span is a generous ceiling
            usable = (point is not None and not point.isEmpty()
                      and math.isfinite(clearance) and 0.0 < clearance <= span)
            if usable:
                p = point.asPoint()
                return p.x(), p.y(), clearance
        except Exception:  # nosec B110
            pass  # degenerate ring, fall through to the cheap interior points
    pt = part.pointOnSurface()
    if not pt or pt.isEmpty():
        pt = part.centroid()
    if not pt or pt.isEmpty():
        return box.center().x(), box.center().y(), 0.0
    p = pt.asPoint()
    return p.x(), p.y(), 0.0


def _pca_angle(ring):
    """PCA angle of the polygon's long axis, -90 to +90 degrees."""
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    xx = sum((x - cx) ** 2 for x in xs) / len(xs)
    yy = sum((y - cy) ** 2 for y in ys) / len(ys)
    xy = sum((x - cx) * (y - cy) for x, y in zip(xs, ys)) / len(xs)
    # atan2 handles xx == yy natively - equal variances with xy != 0 is a real +-45 degree axis, and atan2(0, 0) is 0 for a point blob
    angle = 0.5 * math.atan2(2 * xy, xx - yy)
    deg = math.degrees(angle)
    if deg < -90:
        deg += 180
    if deg > 90:
        deg -= 180
    return deg


def _inner_dims(ring, angle_deg):
    """Polygon dimensions along the PCA axis."""
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    rot = [(p[0] * ca + p[1] * sa, -p[0] * sa + p[1] * ca) for p in ring]
    xs = [p[0] for p in rot]
    ys = [p[1] for p in rot]
    return max(xs) - min(xs), max(ys) - min(ys)


def _adaptive_height(text, ring, clearance=0.0):
    """Text height that fits inside the polygon. The clamps are fractions of its own short side, because a clamp in fixed map units means nothing outside metre-scale data - the same number is tens of kilometres in a geographic CRS and invisible on a city block. clearance is the largest circle that fits (see _label_point) and caps the height at its diameter, since the ring dimensions describe the outline and overstate the usable space on a ring or a deeply concave parcel."""
    lines = text.split("\n")
    max_chars = max(len(line) for line in lines) if lines else 1
    n_lines = len(lines)
    angle = _pca_angle(ring)
    lun, lat = _inner_dims(ring, angle)
    short = min(lun, lat)
    max_h = short * 0.25
    if clearance > 0:
        max_h = min(max_h, clearance * 2.0)
    min_h = short * 0.02
    h_w = (lun * 0.80) / max(max_chars * 0.55, 1)
    h_h = (lat * 0.75) / max(n_lines * 1.30, 1)
    return max(min(h_w, h_h, max_h), min_h)


def _get_ring(geom):
    """Exterior ring of the dominant part as [(x, y), ...], or None. Outline only; interior rings are handled in _label_point."""
    try:
        # the part has to stay bound: constGet() borrows a pointer into it, and chaining off a temporary QgsGeometry frees it mid-expression, after which exteriorRing() returns None
        part = _dominant_part(geom)
        ring = part.constGet().exteriorRing()
        return [
            (ring.pointN(i).x(), ring.pointN(i).y())
            for i in range(ring.numPoints())
        ]
    except Exception:
        return None


# --- QGIS style reader ---

def read_layer_style(layer):
    """Read the visual style off a layer's live renderer and labeling - stroke_color, stroke_width in mm, labels_enabled, label_color, label_size_pt, label_font and label_field."""
    result = {
        "stroke_color": (100, 100, 100),
        "stroke_width": 0.26,
        "labels_enabled": False,
        "label_color": (234, 245, 29),
        "label_size_pt": 4.0,
        "label_font": "Open Sans",
        "label_field": None,
    }

    # stroke and fill off the renderer. SingleSymbol, Categorized or Graduated, just take the first symbol
    try:
        rend = layer.renderer()
        if rend:
            sym = None
            if hasattr(rend, "symbol") and rend.symbol():
                sym = rend.symbol()
            elif hasattr(rend, "symbols"):
                # symbols() needs a real context, sip rejects None, and a default-constructed one is enough to read colors off
                syms = rend.symbols(QgsRenderContext())
                if syms:
                    sym = syms[0]
            if sym:
                for sl in sym.symbolLayers():
                    if hasattr(sl, "strokeColor"):
                        c = sl.strokeColor()
                        result["stroke_color"] = (c.red(), c.green(), c.blue())
                    if hasattr(sl, "strokeWidth"):
                        result["stroke_width"] = sl.strokeWidth()
    except Exception:  # nosec B110
        pass  # best-effort read of the layer's symbology, result already holds the defaults

    if layer.labelsEnabled():
        result["labels_enabled"] = True
        try:
            lab = layer.labeling()
            if lab and hasattr(lab, "settings"):
                ls = lab.settings()
                fmt = ls.format()
                c = fmt.color()
                result["label_color"] = (c.red(), c.green(), c.blue())
                result["label_size_pt"] = fmt.size()
                font_family = fmt.font().family()
                if font_family:
                    result["label_font"] = font_family
                result["label_field"] = ls.fieldName or None
        except Exception:  # nosec B110
            pass  # best-effort read of the label settings, result already holds the defaults

    return result


# --- export engine ---

def export_layers_to_dxf(layers_config, output_path, progress_callback=None):
    """Export several layers into one DXF, as (success, skipped, errors). Every layers_config entry carries layer, stroke_color, stroke_width, labels_enabled, label_color, label_size_pt, adaptive_text, fixed_text_size, label_fields, label_separator and label_newline."""
    try:
        import ezdxf
    except ImportError:
        QgsMessageLog.logMessage(
            "ezdxf is not installed (pip install ezdxf)",
            PLUGIN_NAME, Qgis.MessageLevel.Critical)
        return (0, 0, 0)

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = DXF_INSUNITS
    doc.header["$DWGCODEPAGE"] = DXF_CODEPAGE
    doc.encoding = DXF_ENCODING
    doc.header["$PDMODE"] = 35    # points draw as circle + cross
    doc.header["$PDSIZE"] = 1.0
    msp = doc.modelspace()

    total_features = 0
    for cfg in layers_config:
        total_features += cfg["layer"].featureCount()

    success = 0
    skip = 0
    errors = 0
    current = 0

    for cfg in layers_config:
        layer = cfg["layer"]
        # DXF allows 255-char layer names, not 31. a shorter cap would quietly merge any two layers sharing a prefix and pool their geometry
        existing_names = {lyr.dxf.name for lyr in doc.layers}
        dxf_layer_name = safe_dxf_layer_name(layer.name(), existing_names)
        stroke_rgb = tuple(cfg["stroke_color"])
        stroke_w = cfg["stroke_width"]
        lw_dxf = snap_lineweight(stroke_w)

        # ACI color plus true color. rgb is the ezdxf property behind true_color - setting .true_color on the Layer wrapper is a silent no-op
        if dxf_layer_name not in doc.layers:
            lo = doc.layers.new(name=dxf_layer_name)
            lo.color = rgb_to_aci(*stroke_rgb)
            lo.rgb = stroke_rgb

        layer_geom_type = layer.geometryType()

        for feat in layer.getFeatures():
            current += 1
            if progress_callback:
                progress_callback(current, total_features)

            geom = feat.geometry()
            if not geom or geom.isEmpty():
                skip += 1
                continue

            # go by the feature's own geometry, not the layer's declared type - a mixed table would otherwise call pointN() on a point and count every row as an error
            geom_type = QgsWkbTypes.geometryType(geom.wkbType())
            if geom_type == QgsWkbTypes.GeometryType.UnknownGeometry:
                geom_type = layer_geom_type
            is_polygon = geom_type == QgsWkbTypes.GeometryType.PolygonGeometry
            is_line = geom_type == QgsWkbTypes.GeometryType.LineGeometry

            try:
                _write_geometry(
                    msp, geom, dxf_layer_name, stroke_rgb, lw_dxf,
                    is_polygon, is_line,
                )

                if cfg["labels_enabled"] and cfg.get("label_fields"):
                    label_text = build_label_text(
                        feat, cfg["label_fields"],
                        cfg.get("label_separator", ","),
                        cfg.get("label_newline", False),
                    )
                    if label_text:
                        _write_label(
                            doc, msp,
                            geom, label_text, dxf_layer_name,
                            cfg["label_color"], cfg.get("label_size_pt", 4.0),
                            cfg.get("adaptive_text", True),
                            cfg.get("fixed_text_size", 1.5),
                            is_polygon,
                            font=cfg.get("label_font", "Open Sans"),
                        )

                success += 1
            except Exception as e:
                errors += 1
                QgsMessageLog.logMessage(
                    f"Feature {feat.id()}: {e}", PLUGIN_NAME, Qgis.MessageLevel.Warning)

    _set_viewport(doc, msp)
    doc.saveas(output_path)
    return (success, skip, errors)


# --- entity writers ---

def _write_geometry(msp, geom, layer_name, stroke_rgb, lw_dxf,
                    is_polygon, is_line):
    """Write a geometry as LWPOLYLINE / POINT with true color. Anything carrying Z goes out as 3D POLYLINE / POINT instead, since LWPOLYLINE is flat by definition and would drop survey elevations to 0 without a word."""
    tc = rgb_to_int(*stroke_rgb)
    has_z = QgsWkbTypes.hasZ(geom.wkbType())

    def attribs():
        a = {"layer": layer_name, "color": 256}
        if lw_dxf > 0:
            a["lineweight"] = lw_dxf
        return a

    def coords_of(line):
        pts = (line.pointN(j) for j in range(line.numPoints()))
        if has_z:
            return [(p.x(), p.y(), p.z()) for p in pts]
        return [(p.x(), p.y()) for p in pts]

    def add_poly(coords, close=False):
        if len(coords) < 2:
            return
        if has_z:
            e = msp.add_polyline3d(
                coords, close=close, dxfattribs=attribs())
        else:
            e = msp.add_lwpolyline(coords, close=close, dxfattribs=attribs())
        e.dxf.true_color = tc

    abstract = geom.constGet()
    parts = list(abstract) if geom.isMultipart() else [abstract]

    for part in parts:
        if is_polygon:
            add_poly(coords_of(part.exteriorRing()), close=True)
            for k in range(part.numInteriorRings()):
                add_poly(coords_of(part.interiorRing(k)), close=True)
        elif is_line:
            add_poly(coords_of(part), close=False)
        else:
            location = ((part.x(), part.y(), part.z()) if has_z
                        else (part.x(), part.y()))
            e = msp.add_point(location, dxfattribs=attribs())
            e.dxf.true_color = tc


def _write_label(doc, msp, geom, text, layer_name,
                 label_rgb, size_pt, adaptive, fixed_size, is_polygon,
                 font="Open Sans"):
    """Write the TEXT entities for a label, one per line."""
    from ezdxf.enums import TextEntityAlignment

    r, g, b = label_rgb[:3]
    tc = rgb_to_int(r, g, b)
    ring = _get_ring(geom) if is_polygon else None

    # register the font as a text style, once per font. a family name isn't a symbol-table name so it needs the same scrubbing a layer name does, and one called "Standard" would collide with the style every document already has
    style_name = _DXF_INVALID_NAME_CHARS.sub("_", font.replace(" ", "_"))
    style_name = style_name.strip()[:255] or "Standard"
    if style_name not in doc.styles:
        doc.styles.new(style_name, dxfattribs={"font": font})

    clearance = 0.0
    if is_polygon:
        cx, cy, clearance = _label_point(geom)
    else:
        pt = geom.pointOnSurface()
        if not pt or pt.isEmpty():
            pt = geom.centroid()
        p = pt.asPoint()
        cx, cy = p.x(), p.y()

    # only elongated polygons get rotated text
    text_angle = 0.0
    if ring and len(ring) >= 3:
        angle = _pca_angle(ring)
        lun, lat = _inner_dims(ring, angle)
        if lat > 0 and lun / lat > 2.0:
            text_angle = angle

    h = 0.0
    if adaptive and ring and len(ring) >= 3:
        h = _adaptive_height(text, ring, clearance)
    # a zero-width sliver sizes the text to nothing, fall back to the fixed-mode height
    if h <= 0:
        h = max(fixed_size, 0.1)

    # stack the TEXT entities vertically for multiline
    lines = text.split("\n")
    line_spacing = h * 1.6
    # offset from the middle so the lines distribute evenly
    total_h = (len(lines) - 1) * line_spacing
    rad = math.radians(text_angle)
    cos_a, sin_a = math.cos(rad), math.sin(rad)

    for i, line in enumerate(lines):
        # offset perpendicular to the text direction, up in the rotated frame
        dy = total_h / 2 - i * line_spacing
        lx = cx + dy * (-sin_a)
        ly = cy + dy * cos_a

        e = msp.add_text(
            line,
            height=h,
            dxfattribs={
                "layer": layer_name,
                "color": 256,
                "style": style_name,
            },
        )
        e.set_placement((lx, ly), align=TextEntityAlignment.MIDDLE_CENTER)
        if abs(text_angle) > 1.0:
            e.dxf.rotation = text_angle
        e.dxf.true_color = tc


def _set_viewport(doc, msp):
    """Set the viewport extents from everything that got written."""
    all_x, all_y = [], []
    for e in msp.query("LWPOLYLINE POLYLINE POINT TEXT"):
        kind = e.dxftype()
        if kind == "LWPOLYLINE":
            pts = [(p[0], p[1]) for p in e.get_points()]
        elif kind == "POLYLINE":
            pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
        elif kind == "POINT":
            pts = [(e.dxf.location.x, e.dxf.location.y)]
        else:
            # for TEXT, set_placement copies the alignment point into insert
            pts = [(e.dxf.insert.x, e.dxf.insert.y)]
        for x, y in pts:
            all_x.append(x)
            all_y.append(y)
    if not all_x:
        return
    doc.header["$EXTMIN"] = (min(all_x), min(all_y), 0)
    doc.header["$EXTMAX"] = (max(all_x), max(all_y), 0)
    try:
        vport = doc.viewports.get("*Active")
        if vport:
            vport[0].dxf.center = (
                (min(all_x) + max(all_x)) / 2,
                (min(all_y) + max(all_y)) / 2,
            )
            span = max(
                max(all_y) - min(all_y),
                max(all_x) - min(all_x),
            )
            vport[0].dxf.height = span * 1.05 if span > 0 else 1.0
    except Exception:  # nosec B110
        pass  # the viewport is a convenience for whoever opens the drawing, never a reason to fail an export that already wrote its entities
