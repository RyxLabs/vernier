# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""KMZ export dialog - one KML Folder per layer, labels built from any number of fields with a remembered prefix/suffix each."""

# when sending through WhatsApp, the receiver gets output.kmz.zip. that's WhatsApp renaming it, the file is a valid KMZ and opens fine in Google Earth

from qgis.PyQt.QtCore import Qt  # type: ignore
from qgis.PyQt.QtGui import QBrush, QColor, QFont  # type: ignore
from qgis.PyQt.QtWidgets import (  # type: ignore
    QApplication, QColorDialog, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QPushButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout,
)
from qgis.core import (  # type: ignore
    Qgis, QgsMapLayerProxyModel, QgsProject, QgsRenderContext,
)
from qgis.gui import QgsMapLayerComboBox  # type: ignore

from ..services import kml_writer, label_memory
from .base_dialog import BaseDialog

# prefix/suffix memory, shared with the DXF export and symbology dialogs
_load_label_default = label_memory.load_default
_save_label_default = label_memory.save_default


def _layer_color(layer) -> QColor:
    """Opaque color of the layer's first renderer symbol, blue if there isn't one."""
    renderer = layer.renderer()
    if renderer is not None:
        try:
            symbols = renderer.symbols(QgsRenderContext())
        except Exception:
            symbols = []
        if symbols:
            color = QColor(symbols[0].color())
            if color.isValid():
                # fills are often semi-transparent and a KML outline inheriting that alpha is barely visible
                color.setAlpha(255)
                return color
    return QColor(0, 0, 255)


class KmzExportDialog(BaseDialog):

    _COL_LAYER, _COL_LABEL, _COL_COLOR = 0, 1, 2

    def __init__(self, iface=None, parent=None):
        super().__init__(iface, parent)
        self.setWindowTitle(self.tr("Export KMZ"))
        self.setMinimumWidth(560)
        self.setMinimumHeight(480)
        self._sample_values = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        add_group = QGroupBox(self.tr("Add layer"))
        add_form = QFormLayout()

        self.layer_combo = QgsMapLayerComboBox()
        self.layer_combo.setFilters(QgsMapLayerProxyModel.VectorLayer)
        self.layer_combo.layerChanged.connect(self._on_layer_changed)
        add_form.addRow(self.tr("Layer:"), self.layer_combo)

        # checkable fields with editable prefix/suffix and a live example
        self.field_tree = QTreeWidget()
        self.field_tree.setHeaderLabels([
            self.tr("Field"), self.tr("Prefix"), self.tr("Suffix"),
            self.tr("Example"),
        ])
        self.field_tree.setRootIsDecorated(False)
        self.field_tree.setMinimumHeight(200)
        self.field_tree.setToolTip(self.tr(
            "Check the fields to label with and edit their prefix/suffix"))
        fh = self.field_tree.header()
        fh.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        fh.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        fh.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        fh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        fh.resizeSection(0, 100)
        fh.resizeSection(1, 80)
        fh.resizeSection(2, 80)
        self.field_tree.itemChanged.connect(self._on_field_tree_item_changed)
        add_form.addRow(self.tr("Label fields:"), self.field_tree)

        btn_add = QPushButton(self.tr("Add"))
        btn_add.clicked.connect(self._on_add)
        add_form.addRow("", btn_add)

        add_group.setLayout(add_form)
        layout.addWidget(add_group)

        list_group = QGroupBox(self.tr("Layers to export"))
        list_layout = QVBoxLayout()

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([
            self.tr("Layer"), self.tr("Label"), self.tr("Color"),
        ])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        header = self.tree.header()
        header.setSectionResizeMode(self._COL_LAYER, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(
            self._COL_LABEL, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self._COL_COLOR, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(self._COL_COLOR, 60)
        self.tree.itemDoubleClicked.connect(self._on_tree_double_clicked)
        list_layout.addWidget(self.tree)

        btn_row = QHBoxLayout()
        btn_remove = QPushButton(self.tr("Remove selected"))
        btn_remove.clicked.connect(self._on_remove)
        btn_clear = QPushButton(self.tr("Remove all"))
        btn_clear.clicked.connect(self.tree.clear)
        btn_row.addWidget(btn_remove)
        btn_row.addWidget(btn_clear)
        btn_row.addStretch()
        list_layout.addLayout(btn_row)

        list_group.setLayout(list_layout)
        layout.addWidget(list_group)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_export = QPushButton(self.tr("Export KMZ..."))
        btn_export.setDefault(True)
        btn_export.clicked.connect(self._on_export)
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.reject)
        btn_layout.addWidget(btn_export)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        self.preselect_active_layer(self.layer_combo)
        self._on_layer_changed(self.layer_combo.currentLayer())

    # --- field tree ---

    def _on_layer_changed(self, layer):
        self.field_tree.blockSignals(True)
        self.field_tree.clear()
        if not layer:
            self.field_tree.blockSignals(False)
            return

        # one sample feature gives the example column realistic values
        self._sample_values = {}
        try:
            feat = next(layer.getFeatures())
            for field in layer.fields():
                val = feat.attribute(field.name())
                sval = str(val).strip() if val is not None else ""
                if sval and sval.upper() != "NULL":
                    self._sample_values[field.name()] = sval
        except StopIteration:
            pass

        italic = QFont()
        italic.setItalic(True)
        grey = QColor(120, 120, 120)

        for field in layer.fields():
            fname = field.name()
            prefix, suffix = _load_label_default(fname)
            sample = self._sample_values.get(fname, "...")
            example = f"{prefix}{sample}{suffix}"
            item = QTreeWidgetItem([fname, prefix, suffix, example])
            item.setFlags(
                item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable)
            # real field name goes in UserRole - the whole item is editable, so a renamed name column would break the fields().indexOf() lookup at export
            item.setData(0, Qt.ItemDataRole.UserRole, fname)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            item.setFont(3, italic)
            item.setForeground(3, grey)
            self.field_tree.addTopLevelItem(item)
        self.field_tree.blockSignals(False)

    def _on_field_tree_item_changed(self, item, column):
        fname = item.data(0, Qt.ItemDataRole.UserRole) or item.text(0)
        # name column is display-only, bounce edits back
        if column == 0 and item.text(0) != fname:
            self.field_tree.blockSignals(True)
            item.setText(0, fname)
            self.field_tree.blockSignals(False)
            return
        # keep the example column in sync, and revert manual edits to it
        if column in (1, 2, 3):
            sample = self._sample_values.get(fname, "...")
            example = f"{item.text(1)}{sample}{item.text(2)}"
            self.field_tree.blockSignals(True)
            item.setText(3, example)
            self.field_tree.blockSignals(False)

    # --- export list ---

    def _on_add(self):
        layer = self.layer_combo.currentLayer()
        if not layer:
            return

        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.data(self._COL_LAYER, Qt.ItemDataRole.UserRole) == layer.id():
                self.show_tool_warning(
                    self.tr("'{0}' is already in the list.").format(
                        layer.name()))
                return

        checked_fields = []
        display_parts = []
        for i in range(self.field_tree.topLevelItemCount()):
            fi = self.field_tree.topLevelItem(i)
            if fi.checkState(0) == Qt.CheckState.Checked:
                fname = fi.data(0, Qt.ItemDataRole.UserRole) or fi.text(0)
                prefix = fi.text(1)
                suffix = fi.text(2)
                checked_fields.append({
                    "field": fname,
                    "prefix": prefix,
                    "suffix": suffix,
                })
                _save_label_default(fname, prefix, suffix)
                display_parts.append(prefix + fname + suffix)

        label_text = (", ".join(display_parts) if display_parts
                      else self.tr("(no label)"))

        item = QTreeWidgetItem([layer.name(), label_text, ""])
        item.setData(self._COL_LAYER, Qt.ItemDataRole.UserRole, layer.id())
        item.setData(self._COL_LABEL, Qt.ItemDataRole.UserRole, checked_fields)
        self._set_item_color(item, _layer_color(layer))
        item.setToolTip(self._COL_COLOR,
                        self.tr("Double-click to change the color"))
        self.tree.addTopLevelItem(item)

    def _set_item_color(self, item, color: QColor):
        item.setData(self._COL_COLOR, Qt.ItemDataRole.UserRole, color)
        item.setBackground(self._COL_COLOR, QBrush(color))

    def _on_tree_double_clicked(self, item, column):
        if column != self._COL_COLOR:
            return
        current = item.data(self._COL_COLOR, Qt.ItemDataRole.UserRole) or QColor(0, 0, 255)
        color = QColorDialog.getColor(
            current, self, self.tr("Placemark color"))
        if color.isValid():
            self._set_item_color(item, color)

    def _on_remove(self):
        for item in self.tree.selectedItems():
            idx = self.tree.indexOfTopLevelItem(item)
            self.tree.takeTopLevelItem(idx)

    def _get_selection(self):
        """List of (layer, label_field_configs, color) from the export list."""
        project = QgsProject.instance()
        result = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            layer_id = item.data(self._COL_LAYER, Qt.ItemDataRole.UserRole)
            label_fields = item.data(self._COL_LABEL, Qt.ItemDataRole.UserRole) or []
            color = item.data(self._COL_COLOR, Qt.ItemDataRole.UserRole) or QColor(0, 0, 255)
            layer = project.mapLayer(layer_id)
            if layer:
                result.append((layer, label_fields, color))
        return result

    # --- export ---

    def _on_export(self):
        selection = self._get_selection()
        if not selection:
            self.show_tool_warning(
                self.tr("Add at least one layer to the list."))
            return

        output_path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Save the KMZ file"), "", self.tr("KMZ (*.kmz)"))
        if not output_path:
            return
        if not output_path.lower().endswith(".kmz"):
            output_path += ".kmz"

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            transform_context = QgsProject.instance().transformContext()
            folders = []
            total_features = 0
            for idx, (layer, label_fields, color) in enumerate(selection):
                content, count = kml_writer.layer_to_kml(
                    layer, label_fields, transform_context,
                    kml_writer.color_to_kml_abgr(color),
                    style_index=idx)
                folders.append((layer.name(), content))
                total_features += count

            kml_writer.write_kmz(
                output_path, kml_writer.build_kml_document(folders))
        except Exception as e:
            self.show_tool_failure(e)
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.log_message(
            f"KMZ export: {total_features} features, "
            f"{len(selection)} layers -> {output_path}", Qgis.Info)
        self.show_export_done(
            self.tr("Saved {0}\n{1} features from {2} layers.").format(
                output_path, total_features, len(selection)),
            output_path)
        self.accept()
