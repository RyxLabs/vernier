# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""KMZ export dialog - check the layers to export, then pick each one's label fields and the attribute columns for the Google Earth balloon. Styles follow the layer's QGIS symbology unless a flat color override is set on its row."""

# when sending through WhatsApp, the receiver gets output.kmz.zip. that's WhatsApp renaming it, the file is a valid KMZ and opens fine in Google Earth

from qgis.PyQt.QtCore import Qt  # type: ignore
from qgis.PyQt.QtGui import QBrush, QColor, QFont  # type: ignore
from qgis.PyQt.QtWidgets import (  # type: ignore
    QAbstractItemView, QApplication, QCheckBox, QColorDialog, QFileDialog,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)
from qgis.core import (  # type: ignore
    Qgis, QgsProject, QgsVectorLayer, QgsWkbTypes,
)

from ..services import kml_writer, label_memory
from . import _ui_helpers
from .base_dialog import BaseDialog

# layer table columns
COL_EXPORT = 0
COL_NAME = 1
COL_COLOR = 2
N_COLS = 3

# field tree columns
FLD_NAME = 0
FLD_PREFIX = 1
FLD_SUFFIX = 2
FLD_EXAMPLE = 3
FLD_DATA = 4

# parks a row's label tick while the checkbox indicator is hidden
_LABEL_TICK_ROLE = Qt.ItemDataRole.UserRole + 1

_GEOM_ICONS = {
    QgsWkbTypes.GeometryType.PointGeometry: "●",
    QgsWkbTypes.GeometryType.LineGeometry: "╌",
    QgsWkbTypes.GeometryType.PolygonGeometry: "▣",
}

# prefix/suffix memory, shared with the DXF export and symbology dialogs
_load_label_default = label_memory.load_default
_save_label_default = label_memory.save_default


class KmzExportDialog(BaseDialog):

    def __init__(self, iface=None, parent=None):
        super().__init__(iface, parent)
        self.setWindowTitle(self.tr("Export KMZ"))
        self.setMinimumWidth(680)
        self.setMinimumHeight(560)
        self._layer_configs = {}  # layer_id -> {"fields": [...], "data_fields": [...]}
        self._sample_values = {}  # layer_id -> {field: sample value}
        self._tree_layer_id = None  # layer the field tree is currently showing
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        table_group = QGroupBox(self.tr("Layers"))
        table_layout = QVBoxLayout()

        self.table = QTableWidget()
        self.table.setColumnCount(N_COLS)
        self.table.setHorizontalHeaderLabels([
            self.tr("Export"), self.tr("Layer"), self.tr("Color"),
        ])
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(COL_EXPORT, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(COL_COLOR, QHeaderView.ResizeMode.Fixed)
        h.resizeSection(COL_COLOR, 60)
        color_header = self.table.horizontalHeaderItem(COL_COLOR)
        if color_header:
            color_header.setToolTip(self.tr(
                "Auto follows the layer's QGIS symbology.\n"
                "Double-click a cell to force one flat color."))
        self.table.currentCellChanged.connect(self._on_row_changed)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        table_layout.addWidget(self.table)

        btn_visible = QPushButton(self.tr("Visible only"))
        btn_visible.clicked.connect(self._select_visible_only)
        sel_row, _all_btn, _none_btn = _ui_helpers.make_select_row(
            lambda: self._set_all_checked(True),
            lambda: self._set_all_checked(False),
            (btn_visible,))
        btn_auto = QPushButton(self.tr("Auto color"))
        btn_auto.setToolTip(self.tr(
            "Put the selected row's color back to the QGIS symbology"))
        btn_auto.clicked.connect(self._reset_current_color)
        sel_row.addWidget(btn_auto)
        table_layout.addLayout(sel_row)

        table_group.setLayout(table_layout)
        layout.addWidget(table_group)

        # master-detail: the group shows whichever row is selected in the table
        self.fields_group = QGroupBox(self.tr("Fields"))
        self.fields_group.setVisible(False)
        fields_layout = QVBoxLayout()

        self.qgis_labels_chk = QCheckBox(
            self.tr("Use the layer's QGIS labels"))
        self.qgis_labels_chk.setToolTip(self.tr(
            "Label the placemarks with what QGIS shows on the canvas.\n"
            "Bare text colors carry over; a label with a buffer keeps the\n"
            "viewer's default white, since KML has no text halo.\n"
            "Untick to pick label fields below."))
        self.qgis_labels_chk.toggled.connect(self._on_qgis_labels_toggled)
        fields_layout.addWidget(self.qgis_labels_chk)

        hint = QLabel(self.tr(
            "Field ticks build the placemark label, Data ticks pick the "
            "columns shown in the Google Earth balloon. Prefix, suffix and "
            "the example are editable in place."))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #707070; font-style: italic;")
        fields_layout.addWidget(hint)

        self.field_tree = QTreeWidget()
        self.field_tree.setHeaderLabels([
            self.tr("Field"), self.tr("Prefix"), self.tr("Suffix"),
            self.tr("Example"), self.tr("Data"),
        ])
        self.field_tree.setRootIsDecorated(False)
        self.field_tree.setMinimumHeight(160)
        fh = self.field_tree.header()
        fh.setSectionResizeMode(FLD_NAME, QHeaderView.ResizeMode.Interactive)
        fh.setSectionResizeMode(FLD_PREFIX, QHeaderView.ResizeMode.Interactive)
        fh.setSectionResizeMode(FLD_SUFFIX, QHeaderView.ResizeMode.Interactive)
        fh.setSectionResizeMode(FLD_EXAMPLE, QHeaderView.ResizeMode.Stretch)
        fh.setSectionResizeMode(FLD_DATA, QHeaderView.ResizeMode.ResizeToContents)
        fh.resizeSection(FLD_NAME, 110)
        fh.resizeSection(FLD_PREFIX, 70)
        fh.resizeSection(FLD_SUFFIX, 70)
        header_item = self.field_tree.headerItem()
        header_item.setToolTip(FLD_NAME, self.tr(
            "Checked fields become the placemark label"))
        header_item.setToolTip(FLD_DATA, self.tr(
            "Checked fields show as a table when the placemark is clicked"))
        self.field_tree.itemChanged.connect(self._on_field_tree_item_changed)
        fields_layout.addWidget(self.field_tree)

        check_row = QHBoxLayout()
        check_row.addWidget(QLabel(self.tr("Labels:")))
        self.btn_lbl_all = QPushButton(self.tr("All"))
        self.btn_lbl_all.clicked.connect(
            lambda: self._set_all_fields_checked(FLD_NAME, True))
        self.btn_lbl_none = QPushButton(self.tr("None"))
        self.btn_lbl_none.clicked.connect(
            lambda: self._set_all_fields_checked(FLD_NAME, False))
        check_row.addWidget(self.btn_lbl_all)
        check_row.addWidget(self.btn_lbl_none)
        check_row.addSpacing(20)
        check_row.addWidget(QLabel(self.tr("Data:")))
        btn_data_all = QPushButton(self.tr("All"))
        btn_data_all.clicked.connect(
            lambda: self._set_all_fields_checked(FLD_DATA, True))
        btn_data_none = QPushButton(self.tr("None"))
        btn_data_none.clicked.connect(
            lambda: self._set_all_fields_checked(FLD_DATA, False))
        check_row.addWidget(btn_data_all)
        check_row.addWidget(btn_data_none)
        check_row.addStretch()
        fields_layout.addLayout(check_row)

        self.fields_group.setLayout(fields_layout)
        layout.addWidget(self.fields_group, 1)

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

        self._populate_layers()

    # --- layer table ---

    def _populate_layers(self):
        layers = [
            lyr for lyr in QgsProject.instance().mapLayers().values()
            if isinstance(lyr, QgsVectorLayer) and lyr.isValid()
        ]
        self.table.setRowCount(len(layers))
        active = self.iface.activeLayer() if self.iface else None
        active_row = -1

        for row, layer in enumerate(layers):
            icon = _GEOM_ICONS.get(layer.geometryType(), "◆")
            name_item = QTableWidgetItem(f"{icon} {layer.name()}")
            name_item.setData(Qt.ItemDataRole.UserRole, layer.id())
            self.table.setItem(row, COL_NAME, name_item)

            chk = QCheckBox()
            chk.setChecked(False)
            _ui_helpers.center_table_widget(self.table, row, COL_EXPORT, chk)

            color_item = QTableWidgetItem(self.tr("Auto"))
            color_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            color_item.setToolTip(self.tr(
                "Double-click to force one flat color"))
            self.table.setItem(row, COL_COLOR, color_item)

            self._cache_sample_values(layer)
            if active is not None and layer.id() == active.id():
                active_row = row

        self._adjust_table_height()
        if active_row >= 0:
            self.table.setCurrentCell(active_row, COL_NAME)

    def _adjust_table_height(self):
        """Cap the table to its visible rows, up to 6, plus header - it otherwise claims ~200px no matter how few rows and squeezes the field tree."""
        rows = self.table.rowCount()
        header_h = self.table.horizontalHeader().height() or 28
        if rows == 0:
            self.table.setFixedHeight(header_h + 20)
            return
        row_h = self.table.rowHeight(0) or 30
        visible = min(rows, 6)
        total = header_h + visible * row_h + 6
        self.table.setMinimumHeight(header_h + row_h + 6)
        self.table.setMaximumHeight(total)

    def _cache_sample_values(self, layer):
        """First feature's values, used as sample data in the Example column."""
        samples = {}
        try:
            feat = next(layer.getFeatures())
            for field in layer.fields():
                val = feat.attribute(field.name())
                sval = str(val).strip() if val is not None else ""
                if sval and sval.upper() != "NULL":
                    samples[field.name()] = sval
        except StopIteration:
            pass
        self._sample_values[layer.id()] = samples

    def _set_all_checked(self, checked):
        for row in range(self.table.rowCount()):
            chk = _ui_helpers.centered_table_widget(self.table, row, COL_EXPORT)
            if chk:
                chk.setChecked(checked)

    def _select_visible_only(self):
        """Check only the layers currently visible in QGIS."""
        root = QgsProject.instance().layerTreeRoot()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_NAME)
            if not item:
                continue
            node = root.findLayer(item.data(Qt.ItemDataRole.UserRole))
            visible = node.isVisible() if node else False
            chk = _ui_helpers.centered_table_widget(self.table, row, COL_EXPORT)
            if chk:
                chk.setChecked(visible)

    # --- color override ---

    def _on_cell_double_clicked(self, row, col):
        if col != COL_COLOR:
            return
        item = self.table.item(row, COL_COLOR)
        if item is None:
            return
        current = item.data(Qt.ItemDataRole.UserRole) or QColor(0, 0, 255)
        color = QColorDialog.getColor(
            current, self, self.tr("Placemark color"))
        if color.isValid():
            item.setData(Qt.ItemDataRole.UserRole, color)
            item.setBackground(QBrush(color))
            item.setText("")

    def _reset_current_color(self):
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, COL_COLOR)
        if item is None:
            return
        item.setData(Qt.ItemDataRole.UserRole, None)
        item.setBackground(QBrush())
        item.setText(self.tr("Auto"))

    # --- field tree ---

    def _on_row_changed(self, row, _col, _prev_row, _prev_col):
        self._save_current_config()
        item = self.table.item(row, COL_NAME) if row >= 0 else None
        layer = (QgsProject.instance().mapLayer(
            item.data(Qt.ItemDataRole.UserRole)) if item else None)
        if not layer:
            self._tree_layer_id = None
            self.fields_group.setVisible(False)
            return
        self.fields_group.setTitle(self.tr("Fields: {0}").format(layer.name()))
        self._populate_field_tree(layer)
        self.fields_group.setVisible(True)

    @staticmethod
    def _has_qgis_labels(layer):
        """True when the layer labels through simple labeling - the only kind the export can evaluate."""
        labeling = layer.labeling()
        return bool(layer.labelsEnabled() and labeling is not None
                    and labeling.type() == "simple")

    def _on_qgis_labels_toggled(self, checked):
        # the field ticks only matter when labels are picked by hand
        self.btn_lbl_all.setEnabled(not checked)
        self.btn_lbl_none.setEnabled(not checked)
        self._set_label_ticks_visible(not checked)

    def _set_label_ticks_visible(self, visible):
        """Show or hide the label check boxes themselves - a tickable box under a mode that ignores it reads as broken. Hidden ticks park in _LABEL_TICK_ROLE and come back on show."""
        self.field_tree.blockSignals(True)
        for i in range(self.field_tree.topLevelItemCount()):
            item = self.field_tree.topLevelItem(i)
            has_indicator = item.data(
                FLD_NAME, Qt.ItemDataRole.CheckStateRole) is not None
            if visible and not has_indicator:
                item.setCheckState(
                    FLD_NAME,
                    Qt.CheckState.Checked if item.data(FLD_NAME, _LABEL_TICK_ROLE)
                    else Qt.CheckState.Unchecked)
            elif not visible and has_indicator:
                item.setData(FLD_NAME, _LABEL_TICK_ROLE,
                             item.checkState(FLD_NAME) == Qt.CheckState.Checked)
                # clearing the role removes the checkbox from the cell
                item.setData(FLD_NAME, Qt.ItemDataRole.CheckStateRole, None)
        self.field_tree.blockSignals(False)

    def _label_ticked(self, item) -> bool:
        if item.data(FLD_NAME, Qt.ItemDataRole.CheckStateRole) is None:
            return bool(item.data(FLD_NAME, _LABEL_TICK_ROLE))
        return item.checkState(FLD_NAME) == Qt.CheckState.Checked

    def _populate_field_tree(self, layer):
        """Fill the field tree for a layer, restoring whatever was configured earlier this session."""
        self.field_tree.blockSignals(True)
        self.field_tree.clear()
        layer_id = layer.id()
        self._tree_layer_id = layer_id
        saved = self._layer_configs.get(layer_id)

        has_qgis = self._has_qgis_labels(layer)
        # a None choice means the box was never choosable, so the layer's own labeling decides
        choice = None if saved is None else saved.get("qgis_labels")
        self.qgis_labels_chk.blockSignals(True)
        self.qgis_labels_chk.setEnabled(has_qgis)
        self.qgis_labels_chk.setChecked(
            has_qgis if choice is None else has_qgis and bool(choice))
        self.qgis_labels_chk.blockSignals(False)
        saved_fields = {f["field"]: f for f in (saved or {}).get("fields", [])}
        # data columns default to everything on a layer configured for the first time
        saved_data = None if saved is None else set(saved.get("data_fields", []))

        samples = self._sample_values.get(layer_id, {})
        italic = QFont()
        italic.setItalic(True)
        grey = QColor(120, 120, 120)

        for field in layer.fields():
            fname = field.name()
            if fname in saved_fields:
                cfg = saved_fields[fname]
                prefix = cfg.get("prefix", "")
                suffix = cfg.get("suffix", "")
                checked = True
            else:
                # remembered from past exports, shared with the DXF dialog
                prefix, suffix = _load_label_default(fname)
                checked = False
            sample = samples.get(fname, "...")
            example = f"{prefix}{sample}{suffix}"
            item = QTreeWidgetItem([fname, prefix, suffix, example, ""])
            item.setFlags(
                item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable)
            # real field name goes in UserRole - the whole item is editable, so a renamed name column would break the fields().indexOf() lookup at export
            item.setData(FLD_NAME, Qt.ItemDataRole.UserRole, fname)
            item.setCheckState(FLD_NAME, Qt.CheckState.Checked if checked
                               else Qt.CheckState.Unchecked)
            data_on = saved_data is None or fname in saved_data
            item.setCheckState(FLD_DATA, Qt.CheckState.Checked if data_on
                               else Qt.CheckState.Unchecked)
            item.setFont(FLD_EXAMPLE, italic)
            item.setForeground(FLD_EXAMPLE, grey)
            self.field_tree.addTopLevelItem(item)
        self.field_tree.blockSignals(False)
        # after the rows exist, so hiding the label ticks reaches them
        self._on_qgis_labels_toggled(self.qgis_labels_chk.isChecked())

    def _save_current_config(self):
        """Fold the field tree back into _layer_configs for the layer it shows."""
        layer_id = self._tree_layer_id
        if layer_id is None:
            return
        fields = []
        data_fields = []
        for i in range(self.field_tree.topLevelItemCount()):
            fi = self.field_tree.topLevelItem(i)
            fname = fi.data(FLD_NAME, Qt.ItemDataRole.UserRole) or fi.text(FLD_NAME)
            if self._label_ticked(fi):
                fields.append({
                    "field": fname,
                    "prefix": fi.text(FLD_PREFIX),
                    "suffix": fi.text(FLD_SUFFIX),
                })
            if fi.checkState(FLD_DATA) == Qt.CheckState.Checked:
                data_fields.append(fname)
        self._layer_configs[layer_id] = {
            "fields": fields,
            "data_fields": data_fields,
            # a disabled box is no choice - store None so labeling turned on later still activates the default
            "qgis_labels": (self.qgis_labels_chk.isChecked()
                            if self.qgis_labels_chk.isEnabled() else None),
        }

    def _set_all_fields_checked(self, column, checked):
        _ui_helpers.set_all_check_states(self.field_tree, checked, column)

    def _on_field_tree_item_changed(self, item, column):
        fname = item.data(FLD_NAME, Qt.ItemDataRole.UserRole) or item.text(FLD_NAME)
        # name and data columns carry no editable text, bounce edits back
        if column == FLD_NAME and item.text(FLD_NAME) != fname:
            self.field_tree.blockSignals(True)
            item.setText(FLD_NAME, fname)
            self.field_tree.blockSignals(False)
            return
        if column == FLD_DATA and item.text(FLD_DATA):
            self.field_tree.blockSignals(True)
            item.setText(FLD_DATA, "")
            self.field_tree.blockSignals(False)
            return
        # keep the example column in sync, and revert manual edits to it
        if column in (FLD_PREFIX, FLD_SUFFIX, FLD_EXAMPLE):
            samples = self._sample_values.get(self._tree_layer_id, {})
            sample = samples.get(fname, "...")
            example = f"{item.text(FLD_PREFIX)}{sample}{item.text(FLD_SUFFIX)}"
            self.field_tree.blockSignals(True)
            item.setText(FLD_EXAMPLE, example)
            self.field_tree.blockSignals(False)

    # --- export ---

    def _selected_layers(self):
        """[(layer, config_or_None, color_or_None)] for the checked rows, in table order."""
        project = QgsProject.instance()
        result = []
        for row in range(self.table.rowCount()):
            chk = _ui_helpers.centered_table_widget(self.table, row, COL_EXPORT)
            if not chk or not chk.isChecked():
                continue
            name_item = self.table.item(row, COL_NAME)
            if not name_item:
                continue
            layer = project.mapLayer(name_item.data(Qt.ItemDataRole.UserRole))
            if not layer:
                continue
            color_item = self.table.item(row, COL_COLOR)
            color = (color_item.data(Qt.ItemDataRole.UserRole)
                     if color_item else None)
            result.append((layer, self._layer_configs.get(layer.id()), color))
        return result

    def _on_export(self):
        self._save_current_config()
        selection = self._selected_layers()
        if not selection:
            self.show_tool_warning(
                self.tr("Check at least one layer to export."))
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
            for idx, (layer, config, color) in enumerate(selection):
                if config is None:
                    # a layer exported without ever opening its field panel ships every column, labeled the way its canvas is
                    data_fields = [f.name() for f in layer.fields()]
                    qgis_labels = self._has_qgis_labels(layer)
                else:
                    data_fields = config.get("data_fields", [])
                    choice = config.get("qgis_labels")
                    qgis_labels = (self._has_qgis_labels(layer)
                                   if choice is None else bool(choice))
                label_fields = ([] if qgis_labels
                                else (config or {}).get("fields", []))
                content, count = kml_writer.layer_to_kml(
                    layer, label_fields, transform_context,
                    color_abgr=(kml_writer.color_to_kml_abgr(color)
                                if isinstance(color, QColor) else None),
                    style_index=idx, data_fields=data_fields,
                    qgis_labels=qgis_labels)
                folders.append((layer.name(), content))
                total_features += count
                for cfg in label_fields:
                    _save_label_default(
                        cfg["field"], cfg["prefix"], cfg["suffix"])

            kml_writer.write_kmz(
                output_path, kml_writer.build_kml_document(folders))
        except Exception as e:
            self.show_tool_failure(e)
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.log_message(
            f"KMZ export: {total_features} features, "
            f"{len(selection)} layers -> {output_path}", Qgis.MessageLevel.Info)
        self.show_export_done(
            self.tr("Saved {0}\n{1} features from {2} layers.").format(
                output_path, total_features, len(selection)),
            output_path)
        self.accept()
