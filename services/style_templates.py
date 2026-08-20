# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Style template engine for Quick Symbology. A template is JSON - line style, optional vertex markers, and a label recipe made of roles, where a role names a slot like "identifier" plus the field aliases that can fill it. Applying binds each role to the first field matching an alias and reports back whatever stayed unbound."""

# one JSON file per template in the QGIS profile so they survive updates, and import/export is just file copying. nothing ships seeded, a fresh profile starts empty

import json
import os
import re

from qgis.PyQt.QtGui import QColor  # type: ignore
from qgis.core import (  # type: ignore
    Qgis, QgsApplication, QgsFillSymbol, QgsLineSymbol,
    QgsMarkerLineSymbolLayer, QgsMarkerSymbol, QgsPalLayerSettings,
    QgsSimpleMarkerSymbolLayer, QgsSingleSymbolRenderer,
    QgsTextBufferSettings, QgsTextFormat, QgsUnitTypes,
    QgsVectorLayerSimpleLabeling, QgsWkbTypes,
)

from ..i18n import tr as _tr

GEOMETRY_HINTS = ("any", "polygon", "line", "point")

# pen style names as QgsFillSymbol/QgsLineSymbol.createSimple understands them
PEN_STYLES = ("solid", "dash", "dot", "dash dot", "dash dot dot")

MARKER_SHAPES = ("square", "circle")

SIZE_UNITS = ("points", "mm")

PLACEMENT_ENUMS = {
    "horizontal": Qgis.LabelPlacement.Horizontal,
    "free": Qgis.LabelPlacement.Free,
    "over_point": Qgis.LabelPlacement.OverPoint,
    "around_point": Qgis.LabelPlacement.AroundPoint,
    "line": Qgis.LabelPlacement.Line,
    "curved": Qgis.LabelPlacement.Curved,
    "perimeter": Qgis.LabelPlacement.PerimeterCurved,
}

_MARKER_SHAPE_ENUMS = {
    "square": Qgis.MarkerShape.Square,
    "circle": Qgis.MarkerShape.Circle,
}

_SIZE_UNIT_ENUMS = {
    "points": QgsUnitTypes.RenderUnit.RenderPoints,
    "mm": QgsUnitTypes.RenderUnit.RenderMillimeters,
}


class TemplateError(Exception):
    """Template missing, malformed, or failing validation."""


# --- storage ---


def templates_dir(directory=None) -> str:
    """The template folder, created on demand. In the QGIS profile rather than the plugin folder, so templates survive updates."""
    if directory is None:
        directory = os.path.join(QgsApplication.qgisSettingsDirPath(),
                                 "vernier", "style_templates")
    os.makedirs(directory, exist_ok=True)
    return directory


def safe_filename(name: str) -> str:
    """Template name cut down to a portable file basename, no extension."""
    return re.sub(r"[^\w \-]", "_", name).strip() or "template"


def _scan(directory) -> dict:
    """name -> file path, unreadable files skipped."""
    names = {}
    for fname in sorted(os.listdir(directory)):
        if not fname.lower().endswith(".json"):
            continue
        path = os.path.join(directory, fname)
        try:
            with open(path, encoding="utf-8") as fp:
                data = json.load(fp)
        except (OSError, ValueError):
            continue
        name = data.get("name") if isinstance(data, dict) else None
        if isinstance(name, str) and name.strip():
            names.setdefault(name.strip(), path)
    return names


def list_templates(directory=None) -> list:
    """Sorted names of every stored template."""
    return sorted(_scan(templates_dir(directory)), key=str.casefold)


def load(name: str, directory=None) -> dict:
    """Load and validate a stored template by name."""
    path = _scan(templates_dir(directory)).get(name)
    if path is None:
        raise TemplateError(_tr('No template named "{0}".').format(name))
    try:
        with open(path, encoding="utf-8") as fp:
            template = json.load(fp)
    except (OSError, ValueError) as e:
        raise TemplateError(
            _tr('Could not read template "{0}": {1}').format(name, e))
    validate(template)
    return template


def save(template: dict, directory=None) -> str:
    """Validate and store a template, overwriting one of the same name. Returns the path written."""
    validate(template)
    directory = templates_dir(directory)
    name = template["name"].strip()
    path = _scan(directory).get(name)
    if path is None:
        base = safe_filename(name)
        path = os.path.join(directory, base + ".json")
        suffix = 1
        # the sanitized filename might already belong to a different template
        while os.path.exists(path):
            suffix += 1
            path = os.path.join(directory, f"{base}_{suffix}.json")
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(template, fp, ensure_ascii=False, indent=2)
    return path


def delete(name: str, directory=None):
    path = _scan(templates_dir(directory)).get(name)
    if path is None:
        raise TemplateError(_tr('No template named "{0}".').format(name))
    os.remove(path)


def rename(old: str, new: str, directory=None):
    """Rename a template in place, same file."""
    new = new.strip()
    if not new:
        raise TemplateError(_tr("The new template name cannot be empty."))
    directory = templates_dir(directory)
    existing = _scan(directory)
    if old not in existing:
        raise TemplateError(_tr('No template named "{0}".').format(old))
    if new != old and new in existing:
        raise TemplateError(
            _tr('A template named "{0}" already exists.').format(new))
    template = load(old, directory)
    template["name"] = new
    with open(existing[old], "w", encoding="utf-8") as fp:
        json.dump(template, fp, ensure_ascii=False, indent=2)


# --- schema ---


def _valid_color(value) -> bool:
    return (isinstance(value, (list, tuple))
            and len(value) in (3, 4)
            and all(isinstance(c, int) and not isinstance(c, bool)
                    and 0 <= c <= 255 for c in value))


def _positive_number(value) -> bool:
    return (isinstance(value, (int, float))
            and not isinstance(value, bool) and value > 0)


def validate(template: dict):
    """Raise TemplateError on the first schema problem. Only name and line are mandatory, everything else validates when present and falls back to defaults downstream, so hand-written templates can stay minimal."""
    if not isinstance(template, dict):
        raise TemplateError(_tr("A template must be a JSON object."))
    name = template.get("name")
    if not isinstance(name, str) or not name.strip():
        raise TemplateError(_tr("A template needs a non-empty name."))
    if template.get("geometry", "any") not in GEOMETRY_HINTS:
        raise TemplateError(_tr("geometry must be one of {0}.").format(
            ", ".join(GEOMETRY_HINTS)))

    line = template.get("line")
    if not isinstance(line, dict):
        raise TemplateError(_tr('A template needs a "line" section.'))
    if not _valid_color(line.get("color")):
        raise TemplateError(
            _tr("line.color must be [r, g, b] or [r, g, b, a]."))
    if not _positive_number(line.get("width")):
        raise TemplateError(_tr("line.width must be a positive number."))
    if line.get("pen_style", "solid") not in PEN_STYLES:
        raise TemplateError(_tr("line.pen_style must be one of {0}.").format(
            ", ".join(PEN_STYLES)))

    marker = template.get("vertex_marker", {})
    if not isinstance(marker, dict):
        raise TemplateError(_tr("vertex_marker must be an object."))
    if not isinstance(marker.get("enabled", False), bool):
        raise TemplateError(
            _tr("vertex_marker.enabled must be true or false."))
    if marker.get("shape", "circle") not in MARKER_SHAPES:
        raise TemplateError(
            _tr("vertex_marker.shape must be one of {0}.").format(
                ", ".join(MARKER_SHAPES)))
    if "color" in marker and not _valid_color(marker["color"]):
        raise TemplateError(
            _tr("vertex_marker.color must be [r, g, b(, a)]."))
    if "size" in marker and not _positive_number(marker["size"]):
        raise TemplateError(
            _tr("vertex_marker.size must be a positive number."))

    labels = template.get("labels", {})
    if not isinstance(labels, dict):
        raise TemplateError(_tr("labels must be an object."))
    if not isinstance(labels.get("enabled", False), bool):
        raise TemplateError(_tr("labels.enabled must be true or false."))
    if "size" in labels and not _positive_number(labels["size"]):
        raise TemplateError(_tr("labels.size must be a positive number."))
    if labels.get("size_unit", "points") not in SIZE_UNITS:
        raise TemplateError(
            _tr("labels.size_unit must be one of {0}.").format(
                ", ".join(SIZE_UNITS)))
    if "color" in labels and not _valid_color(labels["color"]):
        raise TemplateError(_tr("labels.color must be [r, g, b(, a)]."))
    if ("placement" in labels
            and labels["placement"] not in PLACEMENT_ENUMS):
        raise TemplateError(
            _tr("labels.placement must be one of {0}.").format(
                ", ".join(PLACEMENT_ENUMS)))

    buffer_cfg = labels.get("buffer", {})
    if not isinstance(buffer_cfg, dict):
        raise TemplateError(_tr("labels.buffer must be an object."))
    if not isinstance(buffer_cfg.get("enabled", False), bool):
        raise TemplateError(
            _tr("labels.buffer.enabled must be true or false."))
    if "size" in buffer_cfg and not _positive_number(buffer_cfg["size"]):
        raise TemplateError(
            _tr("labels.buffer.size must be a positive number."))
    if "color" in buffer_cfg and not _valid_color(buffer_cfg["color"]):
        raise TemplateError(
            _tr("labels.buffer.color must be [r, g, b(, a)]."))

    roles = labels.get("roles", [])
    if not isinstance(roles, list):
        raise TemplateError(_tr("labels.roles must be a list."))
    seen = set()
    for role in roles:
        if not isinstance(role, dict):
            raise TemplateError(
                _tr("Every label role must be an object."))
        rname = role.get("name")
        if not isinstance(rname, str) or not rname.strip():
            raise TemplateError(
                _tr("Every label role needs a non-empty name."))
        if rname in seen:
            raise TemplateError(
                _tr('Duplicate label role "{0}".').format(rname))
        seen.add(rname)
        aliases = role.get("field_aliases")
        if (not isinstance(aliases, list) or not aliases
                or not all(isinstance(a, str) and a.strip()
                           for a in aliases)):
            raise TemplateError(
                _tr('Role "{0}" needs a non-empty field_aliases '
                    'list.').format(rname))
        for key in ("prefix", "suffix"):
            if not isinstance(role.get(key, ""), str):
                raise TemplateError(
                    _tr('Role "{0}": {1} must be text.').format(rname, key))
        if not isinstance(role.get("skip_empty", True), bool):
            raise TemplateError(
                _tr('Role "{0}": skip_empty must be true or '
                    'false.').format(rname))


# --- binding and label expression ---


def bind_roles(template: dict, layer):
    """Bind each label role to the first field matching one of its aliases, case-insensitively. Returns (binding, unbound_role_names) with the field's real casing."""
    fields_lower = {}
    for field in layer.fields():
        fields_lower.setdefault(field.name().lower(), field.name())
    binding = {}
    unbound = []
    for role in (template.get("labels") or {}).get("roles", []):
        for alias in role.get("field_aliases", []):
            match = fields_lower.get(alias.lower())
            if match is not None:
                binding[role["name"]] = match
                break
        else:
            unbound.append(role["name"])
    return binding, unbound


def _quote_field(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def build_label_expression(roles, binding):
    """Label expression joining the bound roles, one line each. A skip_empty role drops its whole line, prefix and suffix included, when the field is NULL or empty. None when nothing is bound."""
    parts = []
    for role in roles:
        field = binding.get(role.get("name"))
        if not field:
            continue
        quoted = _quote_field(field)
        value = f"to_string({quoted})"
        skip_empty = role.get("skip_empty", True)
        pieces = []
        prefix = role.get("prefix", "")
        suffix = role.get("suffix", "")
        if prefix:
            pieces.append(_quote_literal(prefix))
        pieces.append(value if skip_empty else f"coalesce({value}, '')")
        if suffix:
            pieces.append(_quote_literal(suffix))
        concat = " || ".join(pieces)
        if skip_empty:
            parts.append(f"CASE WHEN {quoted} IS NOT NULL "
                         f"AND {value} <> '' THEN {concat} ELSE '' END")
        else:
            parts.append(concat)
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    # the array wrapper is so skip_empty lines don't leave blank rows behind. array_remove_all drops EVERY '' element, so a skip_empty=false role with an empty value and no prefix/suffix loses its line too and the rows below shift up - give roles that must hold a line a prefix or suffix
    return ("array_to_string(array_remove_all(array("
            + ", ".join(parts) + "), ''), char(10))")


# --- symbology ---


def _color(value, fallback=(0, 0, 0)) -> QColor:
    if not _valid_color(value):
        value = fallback
    return QColor(*value)


def _vertex_marker_layer(marker_cfg: dict, fallback_color: QColor):
    shape = _MARKER_SHAPE_ENUMS.get(
        marker_cfg.get("shape", "circle"), Qgis.MarkerShape.Circle)
    color = (_color(marker_cfg["color"]) if "color" in marker_cfg
             else QColor(fallback_color))
    size = float(marker_cfg.get("size", 2.0))
    marker = QgsSimpleMarkerSymbolLayer(
        shape, size, 0.0, Qgis.ScaleMethod.ScaleDiameter, color, color)
    marker_symbol = QgsMarkerSymbol()
    marker_symbol.changeSymbolLayer(0, marker)
    line = QgsMarkerLineSymbolLayer()
    line.setPlacements(Qgis.MarkerLinePlacement.Vertex)
    line.setSubSymbol(marker_symbol)
    return line


def build_symbol(template: dict, geometry_type):
    """Symbol for a QgsWkbTypes.GeometryType, or None. Polygons get a transparent fill with a colored outline, and both polygons and lines pick up a vertex marker layer when the template asks. Follows the real layer geometry, not the template's hint."""
    line = template["line"]
    color = _color(line.get("color"))
    color_str = (f"{color.red()},{color.green()},"
                 f"{color.blue()},{color.alpha()}")
    width = float(line.get("width", 0.26))
    pen = line.get("pen_style", "solid")
    marker_cfg = template.get("vertex_marker") or {}

    if geometry_type == QgsWkbTypes.GeometryType.PolygonGeometry:
        symbol = QgsFillSymbol.createSimple({
            "color": "0,0,0,0",
            "outline_color": color_str,
            "outline_width": str(width),
            "outline_style": pen,
        })
    elif geometry_type == QgsWkbTypes.GeometryType.LineGeometry:
        symbol = QgsLineSymbol.createSimple({
            "color": color_str,
            "width": str(width),
            "line_style": pen,
        })
    elif geometry_type == QgsWkbTypes.GeometryType.PointGeometry:
        shape = _MARKER_SHAPE_ENUMS.get(
            marker_cfg.get("shape", "circle"), Qgis.MarkerShape.Circle)
        point_color = (_color(marker_cfg["color"])
                       if "color" in marker_cfg else color)
        symbol = QgsMarkerSymbol()
        symbol.changeSymbolLayer(0, QgsSimpleMarkerSymbolLayer(
            shape, float(marker_cfg.get("size", 2.5)), 0.0,
            Qgis.ScaleMethod.ScaleDiameter, point_color, point_color))
        return symbol
    else:
        return None

    if marker_cfg.get("enabled"):
        symbol.appendSymbolLayer(_vertex_marker_layer(marker_cfg, color))
    return symbol


def _apply_labels(template: dict, layer, binding):
    labels = template.get("labels") or {}
    expression = None
    if labels.get("enabled"):
        expression = build_label_expression(labels.get("roles", []), binding)
    if not expression:
        layer.setLabelsEnabled(False)
        return

    settings = QgsPalLayerSettings()
    settings.fieldName = expression
    settings.isExpression = True
    placement = PLACEMENT_ENUMS.get(labels.get("placement", ""))
    if placement is not None:
        settings.placement = placement

    text_format = QgsTextFormat()
    text_format.setSize(float(labels.get("size", 8.0)))
    text_format.setSizeUnit(_SIZE_UNIT_ENUMS[
        labels.get("size_unit", "points")])
    text_format.setColor(_color(labels.get("color")))

    buffer_cfg = labels.get("buffer") or {}
    if buffer_cfg.get("enabled"):
        buf = QgsTextBufferSettings()
        buf.setEnabled(True)
        buf.setSize(float(buffer_cfg.get("size", 0.8)))
        buf.setSizeUnit(QgsUnitTypes.RenderUnit.RenderMillimeters)
        buf.setColor(_color(buffer_cfg.get("color"), (255, 255, 255)))
        text_format.setBuffer(buf)

    settings.setFormat(text_format)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)


def apply_to_layer(template: dict, layer, binding=None):
    """Apply a template's symbology and labels to a layer. binding maps role to field, None auto-binds through bind_roles(), and unbound roles drop out of the expression and come back by name. Swaps in a single-symbol renderer, so it works over categorized or rule-based ones too."""
    validate(template)
    roles = (template.get("labels") or {}).get("roles", [])
    if binding is None:
        binding, unbound = bind_roles(template, layer)
    else:
        unbound = [role["name"] for role in roles
                   if role.get("name") not in binding]

    symbol = build_symbol(template, layer.geometryType())
    if symbol is not None:
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    _apply_labels(template, layer, binding)
    return unbound
