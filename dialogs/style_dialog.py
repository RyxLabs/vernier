# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Quick Symbology - a Templates tab over services/style_templates with alias-based field binding, and a Custom tab for direct line, vertex-marker and label styling."""

import copy
import json
import os

from qgis.PyQt.QtCore import Qt  # type: ignore
from qgis.PyQt.QtGui import QColor  # type: ignore
from qgis.PyQt.QtWidgets import (  # type: ignore
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QPushButton, QTabWidget, QVBoxLayout, QWidget,
)
from qgis.core import (  # type: ignore
    Qgis, QgsMapLayerProxyModel, QgsMarkerLineSymbolLayer,
    QgsSimpleMarkerSymbolLayer, QgsSingleSymbolRenderer, QgsUnitTypes,
    QgsVectorLayer, QgsWkbTypes,
)
from qgis.gui import QgsColorButton, QgsMapLayerComboBox  # type: ignore

from ..services import label_memory, style_templates
from .base_dialog import BaseDialog

# (display label, template pen style name), labels get tr()'d at consumption
_LINE_STYLES = (
    ("Solid", "solid"),
    ("Dash", "dash"),
    ("Dot", "dot"),
    ("Dash dot", "dash dot"),
    ("Dash dot dot", "dash dot dot"),
)

_PEN_STYLE_BY_QT = {
    Qt.PenStyle.SolidLine: "solid",
    Qt.PenStyle.DashLine: "dash",
    Qt.PenStyle.DotLine: "dot",
    Qt.PenStyle.DashDotLine: "dash dot",
    Qt.PenStyle.DashDotDotLine: "dash dot dot",
}

_VERTEX_SHAPES = (
    ("Square", "square"),
    ("Circle", "circle"),
)

_SHAPE_BY_ENUM = {
    Qgis.MarkerShape.Square: "square",
    Qgis.MarkerShape.Circle: "circle",
}

# placement choices per geometry, (display label, template placement name)
_POLYGON_PLACEMENTS = (
    ("Horizontal", "horizontal"),
    ("Free", "free"),
    ("Over point", "over_point"),
    ("Around perimeter", "perimeter"),
)
_LINE_PLACEMENTS = (
    ("Along line", "line"),
    ("Curved", "curved"),
    ("Horizontal", "horizontal"),
)
_POINT_PLACEMENTS = (
    ("Over point", "over_point"),
    ("Around point", "around_point"),
    ("Horizontal", "horizontal"),
)

_PLACEMENT_BY_ENUM = {
    enum: name for name, enum in style_templates.PLACEMENT_ENUMS.items()}

_GEOMETRY_HINTS = {
    QgsWkbTypes.GeometryType.PolygonGeometry: "polygon",
    QgsWkbTypes.GeometryType.LineGeometry: "line",
    QgsWkbTypes.GeometryType.PointGeometry: "point",
}

_RENDERER_NAMES = {
    "categorizedSymbol": "categorized",
    "graduatedSymbol": "graduated",
    "RuleRenderer": "rule-based",
    "invertedPolygonRenderer": "inverted-polygon",
    "heatmapRenderer": "heatmap",
    "pointDisplacement": "point-displacement",
    "pointCluster": "point-cluster",
}

_PREVIEW_SCAN_LIMIT = 50

# prefix/suffix memory, shared with the KMZ and DXF export dialogs
_load_label_default = label_memory.load_default
_save_label_default = label_memory.save_default


def _rgba(color: QColor) -> list:
    return [color.red(), color.green(), color.blue(), color.alpha()]


class _RoleBindingDialog(BaseDialog):
    """Prompt for the label roles no layer field matched - bind or skip each."""

    def __init__(self, roles, layer, iface=None, parent=None):
        super().__init__(iface, parent)
        self.setWindowTitle(self.tr("Bind Label Fields"))
        self.setMinimumWidth(360)
        self._combos = {}

        layout = QVBoxLayout(self)
        intro = QLabel(self.tr(
            "No layer field matched these label roles. "
            "Pick a field for each, or skip it."))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        field_names = [field.name() for field in layer.fields()]
        for role in roles:
            combo = QComboBox()
            combo.addItem(self.tr("(skip)"), None)
            for name in field_names:
                combo.addItem(name, name)
            aliases = ", ".join(role.get("field_aliases") or [])
            if aliases:
                combo.setToolTip(
                    self.tr("Aliases tried: {0}").format(aliases))
            form.addRow(role.get("name", ""), combo)
            self._combos[role.get("name", "")] = combo
        layout.addLayout(form)
        button_row, _apply, _cancel = self.create_button_row(
            self.tr("Apply"), self.tr("Cancel"))
        layout.addLayout(button_row)

    def binding(self) -> dict:
        return {name: combo.currentData()
                for name, combo in self._combos.items()
                if combo.currentData()}


class StyleDialog(BaseDialog):

    def __init__(self, iface=None, parent=None):
        super().__init__(iface, parent)
        self.setWindowTitle(self.tr("Quick Symbology"))
        self.setMinimumWidth(480)
        self._setup_ui()
        self._refresh_templates()
        self._on_layer_changed()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        layer_group = QGroupBox(self.tr("Layer"))
        layer_layout = QVBoxLayout()
        self.layer_combo = QgsMapLayerComboBox()
        self.layer_combo.setFilters(QgsMapLayerProxyModel.Filter.PointLayer
                                    | QgsMapLayerProxyModel.Filter.LineLayer
                                    | QgsMapLayerProxyModel.Filter.PolygonLayer)
        layer_layout.addWidget(self.layer_combo)
        layer_group.setLayout(layer_layout)
        layout.addWidget(layer_group)

        # both tabs apply through the same path, so the notice about what Apply overwrites lives outside the tab widget
        self.renderer_notice = QLabel()
        self.renderer_notice.setWordWrap(True)
        self.renderer_notice.setVisible(False)
        layout.addWidget(self.renderer_notice)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_templates_tab(), self.tr("Templates"))
        self.tabs.addTab(self._build_custom_tab(), self.tr("Custom"))
        layout.addWidget(self.tabs)

        button_row, _apply, _close = self.create_button_row(self.tr("Apply"))
        layout.addLayout(button_row)

        self.preselect_active_layer(self.layer_combo)
        self.layer_combo.layerChanged.connect(self._on_layer_changed)

        self.remember("template_vertex_markers", self.template_vertex_chk)
        self.restore_remembered()

    # --- templates tab ---

    def _build_templates_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        self.template_list = QListWidget()
        self.template_list.setToolTip(self.tr(
            "Template files live in {0}").format(
                style_templates.templates_dir()))
        self.template_list.itemDoubleClicked.connect(lambda _: self.accept())
        layout.addWidget(self.template_list)

        # user-level opt-out a template's own vertex_marker.enabled can't override
        self.template_vertex_chk = QCheckBox(
            self.tr("Apply the template's vertex markers"))
        self.template_vertex_chk.setChecked(True)
        self.template_vertex_chk.setToolTip(self.tr(
            "Unchecked, templates apply without their vertex markers.\n"
            "Point layers keep their marker style either way."))
        layout.addWidget(self.template_vertex_chk)

        hint = QLabel(self.tr(
            "Templates are JSON files in your QGIS profile. Plugin updates "
            "do not remove them, and Import/Export moves them between installs."))
        hint.setWordWrap(True)
        hint.setStyleSheet("font-style: italic;")
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        btn_rename = QPushButton(self.tr("Rename..."))
        btn_rename.clicked.connect(self._on_rename_template)
        btn_delete = QPushButton(self.tr("Delete"))
        btn_delete.clicked.connect(self._on_delete_template)
        btn_import = QPushButton(self.tr("Import..."))
        btn_import.clicked.connect(self._on_import_templates)
        btn_export = QPushButton(self.tr("Export..."))
        btn_export.clicked.connect(self._on_export_template)
        buttons.addWidget(btn_rename)
        buttons.addWidget(btn_delete)
        buttons.addStretch()
        buttons.addWidget(btn_import)
        buttons.addWidget(btn_export)
        layout.addLayout(buttons)
        return tab

    def _refresh_templates(self, select=None):
        if select is None:
            current = self.template_list.currentItem()
            select = current.text() if current else None
        self.template_list.clear()
        for name in style_templates.list_templates():
            self.template_list.addItem(name)
            item = self.template_list.item(self.template_list.count() - 1)
            try:
                template = style_templates.load(name)
            except style_templates.TemplateError:
                item.setToolTip(self.tr("This template file is invalid."))
                continue
            roles = (template.get("labels") or {}).get("roles", [])
            role_names = ", ".join(
                role.get("name", "") for role in roles)
            item.setToolTip(self.tr("Geometry: {0}\nLabel roles: {1}").format(
                template.get("geometry", "any"),
                role_names or self.tr("none")))
            if name == select:
                self.template_list.setCurrentItem(item)

    def _selected_template_name(self):
        item = self.template_list.currentItem()
        if item is None:
            self.show_warning(self.tr("Quick Symbology"),
                              self.tr("Select a template first."))
            return None
        return item.text()

    def _on_rename_template(self):
        old = self._selected_template_name()
        if old is None:
            return
        new, ok = QInputDialog.getText(
            self, self.tr("Rename Template"), self.tr("New name:"),
            text=old)
        new = (new or "").strip()
        if not ok or not new or new == old:
            return
        try:
            style_templates.rename(old, new)
        except (OSError, style_templates.TemplateError) as e:
            self.show_warning(self.tr("Rename Template"), str(e))
            return
        self._refresh_templates(select=new)

    def _on_delete_template(self):
        name = self._selected_template_name()
        if name is None:
            return
        if not self.confirm_action(
                self.tr("Delete Template"),
                self.tr('Delete the template "{0}"?').format(name)):
            return
        try:
            style_templates.delete(name)
        except (OSError, style_templates.TemplateError) as e:
            self.show_warning(self.tr("Delete Template"), str(e))
            return
        self._refresh_templates()

    def _on_import_templates(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, self.tr("Import style templates"), "",
            self.tr("JSON files (*.json)"))
        if not paths:
            return
        existing = set(style_templates.list_templates())
        imported = 0
        failed = []
        last_name = None
        for path in paths:
            try:
                with open(path, encoding="utf-8") as fp:
                    template = json.load(fp)
                style_templates.validate(template)
            except (OSError, ValueError, style_templates.TemplateError):
                failed.append(os.path.basename(path))
                continue
            name = template["name"].strip()
            if name in existing and not self.confirm_action(
                    self.tr("Import Style Templates"),
                    self.tr('A template named "{0}" already exists. '
                            "Replace it?").format(name)):
                continue
            try:
                style_templates.save(template)
            except (OSError, style_templates.TemplateError):
                failed.append(os.path.basename(path))
                continue
            existing.add(name)
            imported += 1
            last_name = name
        self._refresh_templates(select=last_name)
        if imported == 1:
            imported_msg = self.tr("Imported 1 template.")
        else:
            imported_msg = self.tr("Imported {0} templates.").format(imported)
        if failed:
            self.show_warning(
                self.tr("Import Style Templates"),
                imported_msg + " " + self.tr("Could not import: {0}")
                .format(", ".join(failed)))
        elif imported:
            self.show_success(imported_msg)

    def _on_export_template(self):
        name = self._selected_template_name()
        if name is None:
            return
        try:
            template = style_templates.load(name)
        except style_templates.TemplateError as e:
            self.show_warning(self.tr("Export Template"), str(e))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Export the template"),
            style_templates.safe_filename(name) + ".json",
            self.tr("JSON files (*.json)"))
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fp:
                json.dump(template, fp, ensure_ascii=False, indent=2)
        except OSError as e:
            self.show_error(self.tr("Export Template"),
                            self.tr("Could not write {0}: {1}")
                            .format(path, e))
            return
        self.show_export_done(
            self.tr('Exported "{0}" to {1}').format(name, path), path)

    # --- custom tab ---

    def _build_custom_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        layout.addWidget(self._build_line_group())
        layout.addWidget(self._build_vertex_group())
        layout.addWidget(self._build_label_group())

        save_row = QHBoxLayout()
        save_row.addStretch()
        btn_save = QPushButton(self.tr("Save as Template..."))
        btn_save.setToolTip(self.tr(
            "Store these settings as a reusable template "
            "on the Templates tab"))
        btn_save.clicked.connect(self._on_save_as_template)
        save_row.addWidget(btn_save)
        layout.addLayout(save_row)

        layout.addStretch()
        return tab

    def _build_line_group(self):
        group = QGroupBox(self.tr("Line"))
        form = QFormLayout()

        self.line_color_btn = QgsColorButton()
        self.line_color_btn.setColor(QColor(0, 0, 0))
        self.line_color_btn.setAllowOpacity(True)
        self.line_color_btn.setColorDialogTitle(self.tr("Line color"))
        form.addRow(self.tr("Color:"), self.line_color_btn)

        self.line_width_spin = QDoubleSpinBox()
        self.line_width_spin.setRange(0.05, 10.0)
        self.line_width_spin.setSingleStep(0.05)
        self.line_width_spin.setDecimals(2)
        self.line_width_spin.setValue(0.26)
        self.line_width_spin.setSuffix(" mm")
        form.addRow(self.tr("Width:"), self.line_width_spin)

        self.line_style_combo = QComboBox()
        for label, key in _LINE_STYLES:
            self.line_style_combo.addItem(self.tr(label), key)
        form.addRow(self.tr("Style:"), self.line_style_combo)

        group.setLayout(form)
        return group

    def _build_vertex_group(self):
        group = QGroupBox(self.tr("Vertex markers"))
        # unchecked means a style with no vertex markers
        group.setCheckable(True)
        group.setChecked(True)
        self.vertex_group = group
        form = QFormLayout()

        self.vertex_shape_combo = QComboBox()
        for label, key in _VERTEX_SHAPES:
            self.vertex_shape_combo.addItem(self.tr(label), key)
        form.addRow(self.tr("Shape:"), self.vertex_shape_combo)

        self.vertex_color_btn = QgsColorButton()
        self.vertex_color_btn.setColor(QColor(30, 100, 255))
        self.vertex_color_btn.setColorDialogTitle(
            self.tr("Vertex marker color"))
        form.addRow(self.tr("Color:"), self.vertex_color_btn)

        group.setLayout(form)
        return group

    def _build_label_group(self):
        group = QGroupBox(self.tr("Labels"))
        layout = QVBoxLayout()

        self.label_enabled_chk = QCheckBox(self.tr("Enable labels"))
        self.label_enabled_chk.setChecked(False)
        self.label_enabled_chk.toggled.connect(self._on_label_toggled)
        layout.addWidget(self.label_enabled_chk)

        form = QFormLayout()

        self.label_field_combo = QComboBox()
        self.label_field_combo.currentTextChanged.connect(
            self._on_field_changed)
        form.addRow(self.tr("Field:"), self.label_field_combo)

        self.label_prefix_edit = QLineEdit()
        self.label_prefix_edit.setPlaceholderText(self.tr("e.g. No."))
        self.label_prefix_edit.textChanged.connect(self._update_label_preview)
        form.addRow(self.tr("Prefix:"), self.label_prefix_edit)

        self.label_suffix_edit = QLineEdit()
        self.label_suffix_edit.setPlaceholderText(self.tr("e.g. m²"))
        self.label_suffix_edit.textChanged.connect(self._update_label_preview)
        form.addRow(self.tr("Suffix:"), self.label_suffix_edit)

        self.label_preview = QLabel("")
        self.label_preview.setStyleSheet(
            "font-style: italic; padding: 2px 0;")
        form.addRow(self.tr("Preview:"), self.label_preview)

        self.label_size_spin = QDoubleSpinBox()
        self.label_size_spin.setRange(0.5, 50.0)
        self.label_size_spin.setSingleStep(0.5)
        self.label_size_spin.setDecimals(1)
        self.label_size_spin.setValue(2.0)
        self.label_size_spin.setSuffix(" mm")
        form.addRow(self.tr("Size:"), self.label_size_spin)

        self.label_color_btn = QgsColorButton()
        self.label_color_btn.setColor(QColor(0, 0, 0))
        self.label_color_btn.setColorDialogTitle(self.tr("Label color"))
        form.addRow(self.tr("Color:"), self.label_color_btn)

        self.label_placement_combo = QComboBox()
        form.addRow(self.tr("Placement:"), self.label_placement_combo)

        buffer_row = QHBoxLayout()
        self.buffer_enabled_chk = QCheckBox()
        self.buffer_enabled_chk.setChecked(False)
        self.buffer_enabled_chk.toggled.connect(
            self._set_buffer_fields_enabled)
        buffer_row.addWidget(self.buffer_enabled_chk)

        self.buffer_size_spin = QDoubleSpinBox()
        self.buffer_size_spin.setRange(0.1, 10.0)
        self.buffer_size_spin.setSingleStep(0.1)
        self.buffer_size_spin.setDecimals(1)
        self.buffer_size_spin.setValue(0.5)
        self.buffer_size_spin.setSuffix(" mm")
        buffer_row.addWidget(self.buffer_size_spin)

        self.buffer_color_btn = QgsColorButton()
        self.buffer_color_btn.setColor(QColor(255, 255, 255))
        self.buffer_color_btn.setColorDialogTitle(
            self.tr("Label buffer color"))
        buffer_row.addWidget(self.buffer_color_btn)

        form.addRow(self.tr("Buffer:"), buffer_row)

        layout.addLayout(form)
        group.setLayout(layout)

        self._set_label_fields_enabled(False)
        self._set_buffer_fields_enabled(False)
        return group

    def _on_label_toggled(self, checked):
        self._set_label_fields_enabled(checked)
        if not checked:
            self.buffer_enabled_chk.setChecked(False)

    def _set_label_fields_enabled(self, enabled):
        self.label_field_combo.setEnabled(enabled)
        self.label_prefix_edit.setEnabled(enabled)
        self.label_suffix_edit.setEnabled(enabled)
        self.label_size_spin.setEnabled(enabled)
        self.label_color_btn.setEnabled(enabled)
        self.label_placement_combo.setEnabled(enabled)
        self.buffer_enabled_chk.setEnabled(enabled)
        if not enabled:
            self._set_buffer_fields_enabled(False)

    def _set_buffer_fields_enabled(self, enabled):
        self.buffer_size_spin.setEnabled(enabled)
        self.buffer_color_btn.setEnabled(enabled)

    def _on_field_changed(self, field_name):
        if field_name:
            prefix, suffix = _load_label_default(field_name)
            self.label_prefix_edit.setText(prefix)
            self.label_suffix_edit.setText(suffix)
        self._update_label_preview()

    def _update_label_preview(self):
        field = self.label_field_combo.currentText()
        prefix = self.label_prefix_edit.text()
        suffix = self.label_suffix_edit.text()
        if not field:
            self.label_preview.setText("")
            return

        # first real value from the layer, capped so an all-NULL column on a huge layer doesn't stall the dialog
        sample = ""
        layer = self.layer_combo.currentLayer()
        if isinstance(layer, QgsVectorLayer):
            idx = layer.fields().indexOf(field)
            if idx >= 0:
                for i, feature in enumerate(layer.getFeatures()):
                    if i >= _PREVIEW_SCAN_LIMIT:
                        break
                    value = feature.attribute(idx)
                    if value is not None and str(value).strip():
                        sample = str(value).strip()
                        break

        if sample:
            self.label_preview.setText(f"{prefix}{sample}{suffix}")
        else:
            self.label_preview.setText(f"{prefix}[{field}]{suffix}")

    # --- layer change / read-back ---

    def _on_layer_changed(self, layer=None):
        if layer is None:
            layer = self.layer_combo.currentLayer()
        self._populate_field_combo(layer)
        self._populate_placement_combo(layer)
        self._load_current_style()
        self._update_label_preview()

    def _populate_field_combo(self, layer):
        previous = self.label_field_combo.currentText()
        self.label_field_combo.blockSignals(True)
        self.label_field_combo.clear()
        if isinstance(layer, QgsVectorLayer):
            for field in layer.fields():
                self.label_field_combo.addItem(field.name())
        index = self.label_field_combo.findText(previous)
        if index >= 0:
            self.label_field_combo.setCurrentIndex(index)
        self.label_field_combo.blockSignals(False)
        self._on_field_changed(self.label_field_combo.currentText())

    def _placement_options(self, layer):
        if isinstance(layer, QgsVectorLayer):
            geometry_type = layer.geometryType()
            if geometry_type == QgsWkbTypes.GeometryType.LineGeometry:
                return _LINE_PLACEMENTS
            if geometry_type == QgsWkbTypes.GeometryType.PointGeometry:
                return _POINT_PLACEMENTS
        return _POLYGON_PLACEMENTS

    def _populate_placement_combo(self, layer):
        self.label_placement_combo.clear()
        for label, key in self._placement_options(layer):
            self.label_placement_combo.addItem(self.tr(label), key)

    def _load_current_style(self):
        """Read the layer's symbology and labels back into the Custom tab. Only single-symbol renderers fit these widgets, anything else gets a visible notice."""
        layer = self.layer_combo.currentLayer()
        if not isinstance(layer, QgsVectorLayer):
            self.renderer_notice.setVisible(False)
            return

        renderer = layer.renderer()
        if isinstance(renderer, QgsSingleSymbolRenderer):
            symbol = renderer.symbol()
            if symbol is not None:
                self._load_line_style(symbol, layer.geometryType())
                self._load_vertex_style(symbol)

        self._update_apply_notice(layer, renderer)
        self._load_label_settings(layer)

    def _update_apply_notice(self, layer, renderer):
        """Say what Apply overwrites. Both tabs go through apply_to_layer, which swaps in a single-symbol renderer and simple labeling, so a richer setup gets flattened rather than edited."""
        parts = []
        if renderer is not None and not isinstance(
                renderer, QgsSingleSymbolRenderer):
            kind = _RENDERER_NAMES.get(renderer.type(), renderer.type())
            parts.append(self.tr(
                "This layer uses {0} rendering, so its current symbology "
                "cannot be shown here.").format(kind))
        labeling = layer.labeling()
        if labeling is not None and labeling.type() != "simple":
            parts.append(self.tr(
                "This layer uses {0} labeling, which cannot be shown here.")
                .format(labeling.type()))
        flattened = bool(parts)
        parts.append(self.tr(
            "Applying replaces this layer's renderer and its labeling; a "
            "style with labels turned off clears the existing labels."))
        self.renderer_notice.setText(" ".join(parts))
        self.renderer_notice.setStyleSheet(
            "color: #d9822b; font-weight: bold; padding: 4px;" if flattened
            else "font-style: italic; padding: 4px;")
        self.renderer_notice.setVisible(True)

    def _load_line_style(self, symbol, geometry_type):
        sym_layer = symbol.symbolLayer(0)
        if sym_layer is None:
            return
        try:
            if geometry_type == QgsWkbTypes.GeometryType.PolygonGeometry:
                color = sym_layer.strokeColor()
                width = sym_layer.strokeWidth()
            elif geometry_type == QgsWkbTypes.GeometryType.LineGeometry:
                color = sym_layer.color()
                width = sym_layer.width()
            elif geometry_type == QgsWkbTypes.GeometryType.PointGeometry:
                color = sym_layer.color()
                width = sym_layer.size()
            else:
                return
            self.line_color_btn.setColor(color)
            self.line_width_spin.setValue(width)
            pen_style = self._detect_pen_style(sym_layer, geometry_type)
            if pen_style is not None:
                index = self.line_style_combo.findData(
                    _PEN_STYLE_BY_QT.get(pen_style))
                if index >= 0:
                    self.line_style_combo.setCurrentIndex(index)
        except (AttributeError, RuntimeError):
            pass  # symbol layer type without these properties

    @staticmethod
    def _detect_pen_style(sym_layer, geometry_type):
        try:
            if geometry_type == QgsWkbTypes.GeometryType.PolygonGeometry:
                return sym_layer.strokeStyle()
            if geometry_type == QgsWkbTypes.GeometryType.LineGeometry:
                return sym_layer.penStyle()
        except AttributeError:
            pass
        return None

    def _load_vertex_style(self, symbol):
        for i in range(symbol.symbolLayerCount()):
            sym_layer = symbol.symbolLayer(i)
            if not isinstance(sym_layer, QgsMarkerLineSymbolLayer):
                continue
            sub = sym_layer.subSymbol()
            if not sub or sub.symbolLayerCount() == 0:
                continue
            marker = sub.symbolLayer(0)
            if not isinstance(marker, QgsSimpleMarkerSymbolLayer):
                continue
            self.vertex_group.setChecked(True)
            index = self.vertex_shape_combo.findData(
                _SHAPE_BY_ENUM.get(marker.shape()))
            if index >= 0:
                self.vertex_shape_combo.setCurrentIndex(index)
            self.vertex_color_btn.setColor(marker.color())
            return
        self.vertex_group.setChecked(False)

    def _load_label_settings(self, layer):
        labeling = layer.labeling()
        enabled = layer.labelsEnabled() and labeling is not None
        self.label_enabled_chk.setChecked(enabled)
        if not enabled:
            return
        try:
            settings = labeling.settings()
        except (AttributeError, RuntimeError):
            return

        # an expression made by a template matches no field name, the combo just keeps its selection then
        if not settings.isExpression:
            index = self.label_field_combo.findText(settings.fieldName)
            if index >= 0:
                self.label_field_combo.setCurrentIndex(index)

        text_format = settings.format()
        size = text_format.size()
        # templates label in points, the spin box is millimeters, so convert on read-back
        if text_format.sizeUnit() == QgsUnitTypes.RenderUnit.RenderPoints:
            size *= 0.352778
        self.label_size_spin.setValue(size)
        self.label_color_btn.setColor(text_format.color())

        index = self.label_placement_combo.findData(
            _PLACEMENT_BY_ENUM.get(settings.placement))
        if index >= 0:
            self.label_placement_combo.setCurrentIndex(index)

        buf = text_format.buffer()
        self.buffer_enabled_chk.setChecked(buf.enabled())
        if buf.enabled():
            self.buffer_size_spin.setValue(buf.size())
            self.buffer_color_btn.setColor(buf.color())

        self._update_label_preview()

    # --- apply ---

    def accept(self):
        """Apply the current tab to the chosen layer, and stay open so the style can be tuned and re-applied. Close or Escape dismisses."""
        layer = self.layer_combo.currentLayer()
        if not self.validate_layer(layer, self.tr("layer")):
            return
        try:
            if self.tabs.currentIndex() == 0:
                applied = self._apply_selected_template(layer)
            else:
                applied = self._apply_custom(layer)
        except style_templates.TemplateError as e:
            self.show_error(self.tr("Quick Symbology"), str(e))
            return
        except Exception as e:
            self.show_error(self.tr("Quick Symbology"),
                            self.tr("Applying the style failed: {0}")
                            .format(e))
            return
        if not applied:
            return
        self.save_remembered()
        layer.triggerRepaint()
        if self.iface:
            self.iface.layerTreeView().refreshLayerSymbology(layer.id())
        self._load_current_style()

    def _apply_selected_template(self, layer) -> bool:
        name = self._selected_template_name()
        if name is None:
            return False
        template = style_templates.load(name)
        if not self.template_vertex_chk.isChecked():
            # strip on a copy - the loaded dict is what save()/rename() write back to disk
            template = copy.deepcopy(template)
            if template.get("vertex_marker"):
                template["vertex_marker"]["enabled"] = False
        binding, unbound = style_templates.bind_roles(template, layer)

        labels = template.get("labels") or {}
        if unbound and labels.get("enabled"):
            roles = [role for role in labels.get("roles", [])
                     if role.get("name") in unbound]
            prompt = _RoleBindingDialog(roles, layer, iface=self.iface,
                                        parent=self)
            if prompt.exec() != QDialog.DialogCode.Accepted:
                return False
            binding.update(prompt.binding())

        skipped = style_templates.apply_to_layer(template, layer, binding)
        message = self.tr('Applied template "{0}" to "{1}".').format(
            name, layer.name())
        if skipped and labels.get("enabled"):
            message += " " + self.tr(
                "Label roles without a field were skipped: {0}.").format(
                    ", ".join(skipped))
        self.show_success(message)
        return True

    def _apply_custom(self, layer) -> bool:
        template = self._capture_template(layer)
        binding = {}
        field = self.label_field_combo.currentText()
        if self.label_enabled_chk.isChecked() and field:
            # the captured role is named after the field so it binds 1:1
            binding = {field: field}
            _save_label_default(field, self.label_prefix_edit.text(),
                                self.label_suffix_edit.text())
        style_templates.apply_to_layer(template, layer, binding)
        self.show_success(
            self.tr('Style applied to "{0}".').format(layer.name()))
        return True

    def _capture_template(self, layer) -> dict:
        """Template dict from the Custom tab's current widget values."""
        geometry = "any"
        if isinstance(layer, QgsVectorLayer):
            geometry = _GEOMETRY_HINTS.get(layer.geometryType(), "any")

        field = self.label_field_combo.currentText()
        roles = []
        if field:
            roles.append({
                "name": field,
                "field_aliases": [field.lower()],
                "prefix": self.label_prefix_edit.text(),
                "suffix": self.label_suffix_edit.text(),
                "skip_empty": True,
            })

        labels = {
            "enabled": self.label_enabled_chk.isChecked(),
            "size": self.label_size_spin.value(),
            "size_unit": "mm",
            "color": _rgba(self.label_color_btn.color()),
            "buffer": {
                "enabled": self.buffer_enabled_chk.isChecked(),
                "size": self.buffer_size_spin.value(),
                "color": _rgba(self.buffer_color_btn.color()),
            },
            "roles": roles,
        }
        placement = self.label_placement_combo.currentData()
        if placement:
            labels["placement"] = placement

        return {
            "name": "Custom",
            "geometry": geometry,
            "line": {
                "color": _rgba(self.line_color_btn.color()),
                "width": self.line_width_spin.value(),
                "pen_style": self.line_style_combo.currentData() or "solid",
            },
            "vertex_marker": {
                "enabled": self.vertex_group.isChecked(),
                "shape": self.vertex_shape_combo.currentData() or "circle",
                "color": _rgba(self.vertex_color_btn.color()),
                "size": 2.0,
            },
            "labels": labels,
        }

    # --- save as template ---

    def _on_save_as_template(self):
        name, ok = QInputDialog.getText(
            self, self.tr("Save Template"), self.tr("Template name:"))
        name = (name or "").strip()
        if not ok or not name:
            return
        if name in style_templates.list_templates():
            if not self.confirm_action(
                    self.tr("Save Template"),
                    self.tr('A template named "{0}" already exists. '
                            "Replace it?").format(name)):
                return
        template = self._capture_template(self.layer_combo.currentLayer())
        template["name"] = name
        try:
            style_templates.save(template)
        except (OSError, style_templates.TemplateError) as e:
            self.show_error(self.tr("Save Template"), str(e))
            return
        self._refresh_templates(select=name)
        self.show_success(self.tr('Template "{0}" saved.').format(name))
