# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""KML/KMZ generation for the export dialog. Hand-rolled rather than OGR's because the output is tuned for Google Maps and Earth on phones - polygons get a companion invisible-icon point carrying the label, since Google Maps never renders polygon names, and every placemark carries its <Style> inline, since phone viewers ignore shared styles referenced through styleUrl. Styles come off the layer's QGIS renderer feature by feature, so categorized, rule-based and data-defined colors all survive, unless the caller forces one flat color for the whole layer."""

import re
import zipfile

from qgis.PyQt.QtCore import Qt, QVariant  # type: ignore
from qgis.PyQt.QtGui import QColor  # type: ignore
from qgis.core import (  # type: ignore
    Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsExpressionContext, QgsExpressionContextUtils, QgsGeometry,
    QgsMessageLog, QgsProject, QgsRenderContext, QgsSymbolLayer, QgsWkbTypes,
)

PLUGIN_NAME = "Vernier"

# scoped enum access for PyQt6, original member names for the 3.28 floor
_PROP_STROKE_COLOR = QgsSymbolLayer.Property.PropertyStrokeColor
_PROP_FILL_COLOR = QgsSymbolLayer.Property.PropertyFillColor

# opaque blue, in KML's aabbggrr byte order
KML_FALLBACK_COLOR_ABGR = "ffff0000"

# QGIS symbol widths are millimeters, KML widths are screen pixels
_MM_TO_PX = 96 / 25.4

# XML 1.0 allows no control characters past tab/LF/CR, and one NUL carried in from a legacy CAD or DBF attribute makes the whole doc.kml unparseable
_XML_ILLEGAL = re.compile(
    "[^\t\n\r\u0020-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


def xml_escape(text) -> str:
    if text is None:
        return ""
    return (_XML_ILLEGAL.sub("", str(text)).replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def name_tag(text) -> str:
    return "<name>" + xml_escape(text) + "</name>"


def xml_escape_attr(text) -> str:
    """xml_escape plus quote escaping, for text placed inside a double-quoted XML attribute."""
    return xml_escape(text).replace('"', "&quot;")


def color_to_kml_abgr(color: QColor) -> str:
    """KML colors are aabbggrr, not the usual aarrggbb."""
    if not color.isValid():
        return KML_FALLBACK_COLOR_ABGR
    return (f"{color.alpha():02x}{color.blue():02x}"
            f"{color.green():02x}{color.red():02x}")


def _opaque_abgr(abgr: str) -> str:
    return "ff" + abgr[2:]


def _mm_to_px(mm, fallback: str) -> str:
    try:
        mm = float(mm)
    except (TypeError, ValueError):
        return fallback
    if mm <= 0:
        return fallback
    return f"{max(1.0, mm * _MM_TO_PX):.1f}"


def _with_opacity(color: QColor, opacity: float) -> QColor:
    if opacity >= 1.0:
        return color
    faded = QColor(color)
    faded.setAlpha(max(0, min(255, round(color.alpha() * opacity))))
    return faded


def _dd_color(symbol_layer, prop_key, expr_context, fallback: QColor) -> QColor:
    """Data-defined color evaluated for the current feature. Vernier's own DXF import drives per-entity CAD colors this way, and the static symbol color is just the group's most common one - reading only the static color exports a multi-color drawing flat."""
    if expr_context is None:
        return fallback
    try:
        prop = symbol_layer.dataDefinedProperties().property(prop_key)
        if prop is None or not prop.isActive():
            return fallback
        value = prop.valueAsColor(expr_context, fallback)
        color = value[0] if isinstance(value, tuple) else value
        if isinstance(color, QColor) and color.isValid():
            return color
    except (AttributeError, RuntimeError, TypeError):
        pass
    return fallback


def _symbol_paint(symbol, geometry_type, opacity: float,
                  expr_context=None) -> dict:
    """Colors, flags and width off one renderer symbol, every lookup guarded - a gradient or SVG symbol layer lacks half these accessors and falls back piecewise. The symbol belongs to the renderer and is only valid inside the render bracket, so everything is copied out here."""
    base = QColor(0, 0, 255)
    try:
        color = symbol.color()
        if color.isValid():
            base = QColor(color)
    except (AttributeError, RuntimeError):
        pass

    # outline defaults to the fill color made opaque - inheriting a fill's alpha leaves KML outlines barely visible
    line = QColor(base)
    line.setAlpha(255)
    paint = {"fill_flag": "1", "outline_flag": "1", "width": None}

    layer0 = None
    try:
        layer0 = symbol.symbolLayer(0)
    except (AttributeError, RuntimeError):
        pass

    if geometry_type == QgsWkbTypes.GeometryType.PolygonGeometry and layer0 is not None:
        if hasattr(layer0, "strokeColor"):
            stroke = layer0.strokeColor()
            if stroke.isValid():
                line = QColor(stroke)
        line = _dd_color(layer0, _PROP_STROKE_COLOR, expr_context, line)
        base = _dd_color(layer0, _PROP_FILL_COLOR, expr_context, base)
        if (hasattr(layer0, "brushStyle")
                and layer0.brushStyle() == Qt.BrushStyle.NoBrush):
            paint["fill_flag"] = "0"
        if (hasattr(layer0, "strokeStyle")
                and layer0.strokeStyle() == Qt.PenStyle.NoPen):
            paint["outline_flag"] = "0"
        if hasattr(layer0, "strokeWidth"):
            paint["width"] = layer0.strokeWidth()
    elif geometry_type == QgsWkbTypes.GeometryType.LineGeometry:
        if hasattr(symbol, "width"):
            paint["width"] = symbol.width()
        if layer0 is not None:
            line = _dd_color(layer0, _PROP_STROKE_COLOR, expr_context, line)
    elif geometry_type == QgsWkbTypes.GeometryType.PointGeometry:
        if layer0 is not None:
            base = _dd_color(layer0, _PROP_FILL_COLOR, expr_context, base)

    paint["line"] = color_to_kml_abgr(_with_opacity(line, opacity))
    paint["fill"] = color_to_kml_abgr(_with_opacity(base, opacity))
    return paint


_DEFAULT_PAINT = {
    "line": KML_FALLBACK_COLOR_ABGR,
    "fill": "80" + KML_FALLBACK_COLOR_ABGR[2:],
    "fill_flag": "1",
    "outline_flag": "1",
    "width": None,
}


def _paint_style(paint: dict, geometry_type, has_labels: bool, sid: str,
                 label_color=None) -> str:
    """One <Style> for one paint. The polygon label scale is 0 - its name goes on the companion point, and viewers that do label polygons would show it twice. label_color overrides the label text color; by default line and point labels take their own symbol color."""
    sc = "1.0" if has_labels else "0"

    if geometry_type == QgsWkbTypes.GeometryType.PolygonGeometry:
        width = _mm_to_px(paint["width"], "1.5")
        return (f'<Style id="{sid}"><LineStyle><color>{paint["line"]}'
                f'</color><width>{width}</width></LineStyle>'
                f'<PolyStyle><color>{paint["fill"]}</color>'
                f'<fill>{paint["fill_flag"]}</fill>'
                f'<outline>{paint["outline_flag"]}</outline>'
                f'</PolyStyle>'
                f'<LabelStyle><scale>0</scale></LabelStyle></Style>')

    if geometry_type == QgsWkbTypes.GeometryType.LineGeometry:
        width = _mm_to_px(paint["width"], "2")
        col = label_color or _opaque_abgr(paint["line"])
        return (f'<Style id="{sid}"><LineStyle><color>{paint["line"]}'
                f'</color><width>{width}</width></LineStyle>'
                f'<LabelStyle><color>{col}'
                f'</color><scale>{sc}</scale></LabelStyle></Style>')

    col = label_color or _opaque_abgr(paint["fill"])
    return (f'<Style id="{sid}"><IconStyle><color>{paint["fill"]}'
            f'</color><scale>1.0</scale>'
            f'<Icon><href>http://maps.google.com/mapfiles/kml/shapes/'
            f'placemark_circle.png</href></Icon></IconStyle>'
            f'<LabelStyle><color>{col}</color>'
            f'<scale>{sc}</scale></LabelStyle></Style>')


def _label_point_style(lid: str, label_color=None) -> str:
    """Invisible icon, visible text - the companion point that carries a polygon's label. Without a color the viewer default applies (white with a dark outline), which stays legible over imagery; a label in the outline color is hard to tell apart from its polygon."""
    color = f"<color>{label_color}</color>" if label_color else ""
    return (f'<Style id="{lid}"><IconStyle><scale>0</scale></IconStyle>'
            f'<LabelStyle>{color}<scale>1.0</scale>'
            f'</LabelStyle></Style>')


def _coords_to_str(points) -> str:
    return " ".join(f"{p.x():.8f},{p.y():.8f},0" for p in points)


def _point_kml(point) -> str:
    return (f"<Point><coordinates>"
            f"{point.x():.8f},{point.y():.8f},0"
            f"</coordinates></Point>")


def _poly_to_kml(poly) -> str:
    kml = "<Polygon><outerBoundaryIs><LinearRing><coordinates>"
    kml += _coords_to_str(poly.exteriorRing())
    kml += "</coordinates></LinearRing></outerBoundaryIs>"
    for i in range(poly.numInteriorRings()):
        kml += "<innerBoundaryIs><LinearRing><coordinates>"
        kml += _coords_to_str(poly.interiorRing(i))
        kml += "</coordinates></LinearRing></innerBoundaryIs>"
    kml += "</Polygon>"
    return kml


def _line_kml(points) -> str:
    return (f"<LineString><coordinates>"
            f"{_coords_to_str(points)}"
            f"</coordinates></LineString>")


def _multi(parts) -> str:
    if len(parts) == 1:
        return parts[0]
    return "<MultiGeometry>" + "".join(parts) + "</MultiGeometry>"


def geometry_to_kml(geometry: QgsGeometry, geometry_type):
    """KML for one geometry, None for empty or unsupported input. Curved geometries get segmentized first, KML has no arcs."""
    if geometry is None or geometry.isNull() or geometry.isEmpty():
        return None

    if QgsWkbTypes.isCurvedType(geometry.wkbType()):
        geometry = QgsGeometry(geometry)
        geometry.convertToStraightSegment()

    g = geometry.constGet()
    if g is None:
        return None

    if geometry_type == QgsWkbTypes.GeometryType.PolygonGeometry:
        if geometry.isMultipart():
            return _multi([_poly_to_kml(p) for p in g])
        return _poly_to_kml(g)

    if geometry_type == QgsWkbTypes.GeometryType.LineGeometry:
        if geometry.isMultipart():
            return _multi([_line_kml(p) for p in g])
        return _line_kml(g)

    if geometry_type == QgsWkbTypes.GeometryType.PointGeometry:
        if geometry.isMultipart():
            return _multi([_point_kml(p) for p in g])
        return _point_kml(g)

    return None


def build_label(feature, field_configs) -> str:
    """Label text from [{"idx", "prefix", "suffix"}, ...] - one prefix+value+suffix line per field, NULLs skipped."""
    parts = []
    for cfg in field_configs:
        val = feature.attribute(cfg["idx"])
        # check the type, not the text. a value that literally reads "NULL" is data and has to survive
        if val is None or (isinstance(val, QVariant) and val.isNull()):
            continue
        text = str(val).replace("\n", " ")
        if not text:
            continue
        parts.append(cfg["prefix"] + text + cfg["suffix"])
    return "\n".join(parts)


def extended_data(feature, field_configs) -> str:
    """<ExtendedData> rows from [{"idx", "name"}, ...], Google Earth's balloon table. Empty when no field has a value."""
    rows = []
    for cfg in field_configs:
        val = feature.attribute(cfg["idx"])
        # same rule as build_label: judge NULL by type, a literal "NULL" string is data
        if val is None or (isinstance(val, QVariant) and val.isNull()):
            continue
        text = str(val)
        if not text:
            continue
        rows.append(f'<Data name="{xml_escape_attr(cfg["name"])}">'
                    f"<value>{xml_escape(text)}</value></Data>")
    if not rows:
        return ""
    return "<ExtendedData>" + "".join(rows) + "</ExtendedData>"


def layer_to_kml(layer, label_fields, transform_context,
                 color_abgr=None, style_index: int = 0, data_fields=None,
                 qgis_labels: bool = False):
    """Placemarks for one layer, reprojected to WGS84, as (kml_fragment, feature_count).

    Styles follow the layer's renderer feature by feature - categorized, graduated, rule-based and data-defined colors all come through - and every placemark carries its <Style> inline. Passing color_abgr instead forces one flat outline style for the whole layer. data_fields names the attribute columns carried as balloon ExtendedData. qgis_labels labels the placemarks with the layer's own QGIS labeling - text and color - instead of label_fields; it needs simple labeling turned on, anything else falls back to no labels. style_index keeps the inline ids unique across the document."""
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    tr = QgsCoordinateTransform(layer.crs(), wgs84, transform_context)
    gt = layer.geometryType()

    label_expr = None
    label_expr_ctx = None
    label_color = None
    if qgis_labels:
        try:
            labeling = layer.labeling()
            if labeling is not None and layer.labelsEnabled():
                # rule-based labeling has no settings() and drops to the except
                settings = labeling.settings()
                label_expr = settings.getLabelExpression()
                label_expr_ctx = QgsExpressionContext()
                label_expr_ctx.appendScope(
                    QgsExpressionContextUtils.globalScope())
                label_expr_ctx.appendScope(
                    QgsExpressionContextUtils.projectScope(
                        QgsProject.instance()))
                label_expr_ctx.appendScope(
                    QgsExpressionContextUtils.layerScope(layer))
                label_expr_ctx.setFields(layer.fields())
                label_expr.prepare(label_expr_ctx)
                # KML has no text halo and Google Earth outlines every label dark, so a buffered label - one that needs its halo to read - keeps the viewer's default white instead of going dark-on-dark; only a bare color is carried over
                fmt = settings.format()
                text_color = fmt.color()
                if not fmt.buffer().enabled() and text_color.isValid():
                    opaque = QColor(text_color)
                    opaque.setAlpha(255)
                    label_color = color_to_kml_abgr(opaque)
        except (AttributeError, RuntimeError):
            label_expr = None
            label_expr_ctx = None

    field_configs = []
    if label_expr is None:
        for cfg in (label_fields or []):
            idx = layer.fields().indexOf(cfg["field"])
            if idx >= 0:
                field_configs.append({
                    "idx": idx,
                    "prefix": cfg.get("prefix", ""),
                    "suffix": cfg.get("suffix", ""),
                })
    has_labels = bool(field_configs) or label_expr is not None

    data_configs = []
    for name in (data_fields or []):
        idx = layer.fields().indexOf(name)
        if idx >= 0:
            data_configs.append({"idx": idx, "name": name})

    uniform = color_abgr is not None
    if uniform:
        # the flat override becomes a paint like any other; polygons get no fill, so the forced color draws only the outline
        uniform_paint = {
            "line": color_abgr, "fill": color_abgr,
            "fill_flag": "0" if gt == QgsWkbTypes.GeometryType.PolygonGeometry
            else "1",
            "outline_flag": "1", "width": None,
        }

    renderer = None
    ctx = None
    layer_opacity = 1.0
    if not uniform:
        renderer = layer.renderer()
        try:
            layer_opacity = float(layer.opacity())
        except (AttributeError, TypeError):
            pass
        if renderer is not None:
            ctx = QgsRenderContext()
            expr = QgsExpressionContext()
            expr.appendScope(QgsExpressionContextUtils.globalScope())
            expr.appendScope(
                QgsExpressionContextUtils.projectScope(QgsProject.instance()))
            expr.appendScope(QgsExpressionContextUtils.layerScope(layer))
            ctx.setExpressionContext(expr)
            # categorized and rule-based renderers build their per-feature lookup in startRender - symbolForFeature answers None without the bracket
            renderer.startRender(ctx, layer.fields())

    placemarks = []
    count = 0
    skipped = 0
    try:
        for f in layer.getFeatures():
            # one bad row shouldn't abort the export, the whole KMZ is written in a single pass and an exception here loses every other layer too
            try:
                geom = f.geometry()
                if not geom or geom.isEmpty():
                    skipped += 1
                    continue
                geom = QgsGeometry(geom)
                geom.transform(tr)

                if label_expr is not None and label_expr_ctx is not None:
                    label_expr_ctx.setFeature(f)
                    value = label_expr.evaluate(label_expr_ctx)
                    label = "" if value is None else str(value)
                elif has_labels:
                    label = build_label(f, field_configs)
                else:
                    label = ""
                data = extended_data(f, data_configs) if data_configs else ""

                # go by the feature's own type, not the layer's - a provider can declare one thing and hand back another, DXF imports especially, and the wrong branch reaches for parts that aren't there
                fgt = QgsWkbTypes.geometryType(geom.wkbType())
                gkml = geometry_to_kml(geom, fgt)
                if not gkml:
                    skipped += 1
                    continue

                if uniform:
                    paint = uniform_paint
                else:
                    paint = _DEFAULT_PAINT
                    if renderer is not None and ctx is not None:
                        ctx.expressionContext().setFeature(f)
                        symbol = renderer.symbolForFeature(f, ctx)
                        if symbol is not None:
                            opacity = layer_opacity
                            try:
                                opacity *= float(symbol.opacity())
                            except (AttributeError, TypeError):
                                pass
                            paint = _symbol_paint(
                                symbol, fgt, opacity,
                                ctx.expressionContext())

                # the style is pasted into every placemark rather than shared and referenced through styleUrl - Google Maps on phones, the audience this export is tuned for, ignores styleUrl references, and the repetition compresses to nearly nothing inside the KMZ
                style = _paint_style(paint, fgt, has_labels,
                                     f"s{style_index}_{count}", label_color)

                placemarks.append("  <Placemark>")
                placemarks.append("    " + name_tag(label))
                placemarks.append("    " + style)
                if data:
                    placemarks.append("    " + data)
                placemarks.append("    " + gkml)
                placemarks.append("  </Placemark>")

                # the polygon's own label scale is 0, the name goes on a companion point - and an off-type row must not get one
                if (gt == QgsWkbTypes.GeometryType.PolygonGeometry
                        and fgt == gt and label):
                    interior_pt = geom.pointOnSurface()
                    if interior_pt and not interior_pt.isEmpty():
                        # built before the first append, so a failure here can't leave half a Placemark behind
                        pt_kml = _point_kml(interior_pt.constGet())
                        placemarks.append("  <Placemark>")
                        placemarks.append("    " + name_tag(label))
                        placemarks.append("    " + _label_point_style(
                            f"lbl{style_index}_{count}", label_color))
                        if data:
                            placemarks.append("    " + data)
                        placemarks.append("    " + pt_kml)
                        placemarks.append("  </Placemark>")

                count += 1
            except Exception as e:
                skipped += 1
                QgsMessageLog.logMessage(
                    f"KMZ export - {layer.name()} feature {f.id()}: {e}",
                    PLUGIN_NAME, Qgis.MessageLevel.Warning)
    finally:
        if renderer is not None and ctx is not None:
            renderer.stopRender(ctx)

    if skipped:
        QgsMessageLog.logMessage(
            f"KMZ export - {layer.name()}: {skipped} features skipped "
            f"(no geometry, failed reprojection or unsupported type)",
            PLUGIN_NAME, Qgis.MessageLevel.Warning)

    return "\n".join(placemarks), count


def build_kml_document(folders) -> str:
    """Full KML document from [(folder_name, placemark_markup), ...], one Folder per layer so Google Earth shows them as separate toggleable groups."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2">',
        "<Document>",
    ]
    for folder_name, content in folders:
        lines.append("  <Folder>")
        lines.append(f"    {name_tag(folder_name)}")
        lines.append(content)
        lines.append("  </Folder>")
    lines.append("</Document>")
    lines.append("</kml>")
    return "\n".join(lines)


def write_kmz(output_path: str, kml_text: str):
    """Write kml_text as doc.kml inside a KMZ. The zip metadata is shaped to match what QGIS and Google Earth produce, because WhatsApp only recognizes a KMZ when it does."""
    info = zipfile.ZipInfo("doc.kml")
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_version = 0
    info.create_system = 0
    info.extract_version = 20
    info.internal_attr = 1  # text file

    with zipfile.ZipFile(output_path, "w") as kmz:
        kmz.writestr(info, kml_text.encode("utf-8"))

    _patch_external_attrs(output_path)


def _patch_external_attrs(path: str):
    """Zero external_attr on every central-directory entry. Python 3.13+ stamps 0o600 << 16 in there when it's 0, native KMZs carry 0, and WhatsApp keys on it. Walked from the end-of-central-directory record, since scanning raw bytes for the signature can false-positive inside deflated data."""
    with open(path, "r+b") as fp:
        data = fp.read()
        eocd = data.rfind(b"PK\x05\x06")
        if eocd < 0:
            return
        entries = int.from_bytes(data[eocd + 10:eocd + 12], "little")
        offset = int.from_bytes(data[eocd + 16:eocd + 20], "little")
        for _ in range(entries):
            if data[offset:offset + 4] != b"PK\x01\x02":
                break
            fp.seek(offset + 38)  # external_attr field of this entry
            fp.write(b"\x00\x00\x00\x00")
            name_len = int.from_bytes(data[offset + 28:offset + 30], "little")
            extra_len = int.from_bytes(data[offset + 30:offset + 32], "little")
            comment_len = int.from_bytes(
                data[offset + 32:offset + 34], "little")
            offset += 46 + name_len + extra_len + comment_len
