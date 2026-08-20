# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""KML/KMZ generation for the export dialog. Hand-rolled rather than OGR's because the output is tuned for Google Maps and Earth on phones - polygons get an outline-only style plus a companion invisible-icon point carrying the label, since Google Maps never renders polygon names."""

import re
import zipfile

from qgis.PyQt.QtCore import QVariant  # type: ignore
from qgis.PyQt.QtGui import QColor  # type: ignore
from qgis.core import (  # type: ignore
    Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry,
    QgsMessageLog, QgsWkbTypes,
)

PLUGIN_NAME = "Vernier"

# opaque blue, in KML's aabbggrr byte order
KML_FALLBACK_COLOR_ABGR = "ffff0000"

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


def color_to_kml_abgr(color: QColor) -> str:
    """KML colors are aabbggrr, not the usual aarrggbb."""
    if not color.isValid():
        return KML_FALLBACK_COLOR_ABGR
    return (f"{color.alpha():02x}{color.blue():02x}"
            f"{color.green():02x}{color.red():02x}")


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

    if geometry_type == QgsWkbTypes.PolygonGeometry:
        if geometry.isMultipart():
            return _multi([_poly_to_kml(p) for p in g])
        return _poly_to_kml(g)

    if geometry_type == QgsWkbTypes.LineGeometry:
        if geometry.isMultipart():
            return _multi([_line_kml(p) for p in g])
        return _line_kml(g)

    if geometry_type == QgsWkbTypes.PointGeometry:
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


def layer_to_kml(layer, label_fields, transform_context,
                 color_abgr: str = KML_FALLBACK_COLOR_ABGR,
                 style_index: int = 0):
    """Placemarks for one layer, reprojected to WGS84, as (kml_fragment, feature_count). style_index keeps the Style ids unique - every fragment ends up in one <Document>, where duplicate ids all resolve to the first and every layer would come out in the first layer's color."""
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    tr = QgsCoordinateTransform(layer.crs(), wgs84, transform_context)
    gt = layer.geometryType()

    field_configs = []
    for cfg in (label_fields or []):
        idx = layer.fields().indexOf(cfg["field"])
        if idx >= 0:
            field_configs.append({
                "idx": idx,
                "prefix": cfg.get("prefix", ""),
                "suffix": cfg.get("suffix", ""),
            })
    has_labels = len(field_configs) > 0

    col = color_abgr
    sid = f"s{style_index}"
    lid = f"lbl{style_index}"

    # no label on the polygon itself, it rides on the companion point placemark further down
    if gt == QgsWkbTypes.PolygonGeometry:
        style = (
            f'<Style id="{sid}"><LineStyle><color>{col}</color>'
            f'<width>1.5</width>'
            f'</LineStyle><PolyStyle><fill>0</fill><outline>1</outline>'
            f'</PolyStyle></Style>'
        )
    elif gt == QgsWkbTypes.LineGeometry:
        sc = "1.0" if has_labels else "0"
        style = (
            f'<Style id="{sid}"><LineStyle><color>{col}</color>'
            f'<width>2</width>'
            f'</LineStyle><LabelStyle><color>{col}</color><scale>{sc}'
            f'</scale></LabelStyle></Style>'
        )
    else:
        sc = "1.0" if has_labels else "0"
        style = (
            f'<Style id="{sid}"><IconStyle><color>{col}</color>'
            f'<scale>1.0</scale>'
            f'<Icon><href>http://maps.google.com/mapfiles/kml/shapes/'
            f'placemark_circle.png</href></Icon></IconStyle>'
            f'<LabelStyle><color>{col}</color><scale>{sc}</scale>'
            f'</LabelStyle></Style>'
        )

    lines = [style]

    # label-point style for polygons, invisible icon and visible text
    if gt == QgsWkbTypes.PolygonGeometry and has_labels:
        lines.append(
            f'<Style id="{lid}"><IconStyle><scale>0</scale></IconStyle>'
            f'<LabelStyle><color>{col}</color><scale>1.0</scale>'
            f'</LabelStyle></Style>'
        )

    count = 0
    skipped = 0
    for f in layer.getFeatures():
        # one bad row shouldn't abort the export, the whole KMZ is written in a single pass and an exception here loses every other layer too
        try:
            geom = f.geometry()
            if not geom or geom.isEmpty():
                skipped += 1
                continue
            geom = QgsGeometry(geom)
            geom.transform(tr)

            label = build_label(f, field_configs) if has_labels else ""

            # go by the feature's own type, not the layer's - a provider can declare one thing and hand back another, DXF imports especially, and the wrong branch reaches for parts that aren't there
            fgt = QgsWkbTypes.geometryType(geom.wkbType())
            gkml = geometry_to_kml(geom, fgt)
            if not gkml:
                skipped += 1
                continue

            lines.append("  <Placemark>")
            lines.append("    " + name_tag(label))
            lines.append(f'    <styleUrl>#{sid}</styleUrl>')
            lines.append("    " + gkml)
            lines.append("  </Placemark>")

            # the label-point style only gets emitted for polygon layers, so an off-type row must not link to it
            if gt == QgsWkbTypes.PolygonGeometry and fgt == gt and label:
                interior_pt = geom.pointOnSurface()
                if interior_pt and not interior_pt.isEmpty():
                    # built before the first append, so a failure here can't leave half a Placemark behind
                    pt_kml = _point_kml(interior_pt.constGet())
                    lines.append("  <Placemark>")
                    lines.append("    " + name_tag(label))
                    lines.append(f'    <styleUrl>#{lid}</styleUrl>')
                    lines.append("    " + pt_kml)
                    lines.append("  </Placemark>")

            count += 1
        except Exception as e:
            skipped += 1
            QgsMessageLog.logMessage(
                f"KMZ export - {layer.name()} feature {f.id()}: {e}",
                PLUGIN_NAME, Qgis.Warning)

    if skipped:
        QgsMessageLog.logMessage(
            f"KMZ export - {layer.name()}: {skipped} features skipped "
            f"(no geometry, failed reprojection or unsupported type)",
            PLUGIN_NAME, Qgis.Warning)

    return "\n".join(lines), count


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
