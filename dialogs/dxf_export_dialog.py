# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Multi-layer DXF export with style and labels - either one combined DXF or one DXF per value of a grouping field."""

import os

from qgis.PyQt.QtCore import Qt, QTimer  # type: ignore
from qgis.PyQt.QtGui import QColor, QFont  # type: ignore
from qgis.PyQt.QtWidgets import (  # type: ignore
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QSizePolicy, QTabWidget,
    QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)
from qgis.core import QgsProject, QgsVectorLayer, QgsWkbTypes  # type: ignore
from qgis.gui import QgsColorButton  # type: ignore

from ..services import deps, label_memory
from .base_dialog import BaseDialog

# layer table columns
COL_EXPORT = 0
COL_NAME = 1
COL_STROKE = 2
COL_WIDTH = 3
COL_TXT_CLR = 4
COL_LABELS = 5
N_COLS = 6


class DxfExportDialog(BaseDialog):

    def __init__(self, iface=None, parent=None):
        super().__init__(iface, parent)
        self.setWindowTitle(self.tr("Export DXF"))
        self.setMinimumWidth(700)
        self._layer_configs = {}  # layer_id -> label field config
        self._sample_values = {}  # layer_id -> {field: sample_value}
        self._tree_layer_id = None  # layer the field tree is currently showing
        self._setup_ui()

    # --- ui setup ---

    def _setup_ui(self):
        main = QVBoxLayout()
        main.setSpacing(6)

        table_group = QGroupBox(self.tr("Layers"))
        table_layout = QVBoxLayout()
        table_layout.setSpacing(2)

        self.table = QTableWidget()
        self.table.setColumnCount(N_COLS)
        self.table.setHorizontalHeaderLabels([
            self.tr("Export"), self.tr("Layer"), self.tr("Stroke"),
            self.tr("Width (mm)"), self.tr("Text color"), self.tr("Labels"),
        ])
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        h = self.table.horizontalHeader()
        h.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(COL_EXPORT, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_WIDTH, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_LABELS, QHeaderView.ResizeMode.ResizeToContents)
        # fixed width or the color buttons end up too narrow to see
        for col in (COL_STROKE, COL_TXT_CLR):
            h.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            h.resizeSection(col, 60)

        labels_header = self.table.horizontalHeaderItem(COL_LABELS)
        if labels_header:
            labels_header.setToolTip(self.tr(
                "Tick to write this layer's labels as DXF text, then pick "
                "the fields in the Labels panel below."))

        self.table.currentCellChanged.connect(self._on_row_changed)
        table_layout.addWidget(self.table)

        # names any layer with Labels ticked but no field chosen
        self.label_status = QLabel("")
        self.label_status.setWordWrap(True)
        table_layout.addWidget(self.label_status)

        sel_row = QHBoxLayout()
        btn_all = QPushButton(self.tr("All"))
        btn_all.setMaximumWidth(70)
        btn_all.clicked.connect(lambda: self._set_all_checked(True))
        btn_none = QPushButton(self.tr("None"))
        btn_none.setMaximumWidth(70)
        btn_none.clicked.connect(lambda: self._set_all_checked(False))
        btn_visible = QPushButton(self.tr("Visible only"))
        btn_visible.setMaximumWidth(100)
        btn_visible.clicked.connect(self._select_visible_only)
        sel_row.addWidget(btn_all)
        sel_row.addWidget(btn_none)
        sel_row.addWidget(btn_visible)
        sel_row.addStretch()
        btn_reset = QPushButton(self.tr("Reset style"))
        btn_reset.setMaximumWidth(130)
        btn_reset.clicked.connect(self._reset_current_layer)
        sel_row.addWidget(btn_reset)
        table_layout.addLayout(sel_row)

        table_group.setLayout(table_layout)
        main.addWidget(table_group)

        # stays hidden until a row is selected
        self.label_group = QGroupBox(self.tr("Labels"))
        self.label_group.setVisible(False)
        label_layout = QVBoxLayout()
        label_layout.setSpacing(4)

        label_hint = QLabel(self.tr(
            "Tick every field to write as text. Prefix, suffix and the "
            "example are editable in place."))
        label_hint.setWordWrap(True)
        label_hint.setStyleSheet("color: #707070; font-style: italic;")
        label_layout.addWidget(label_hint)

        self.field_tree = QTreeWidget()
        self.field_tree.setHeaderLabels([
            self.tr("Field"), self.tr("Prefix"), self.tr("Suffix"),
            self.tr("Example"),
        ])
        self.field_tree.setRootIsDecorated(False)
        self.field_tree.setMinimumHeight(100)
        th = self.field_tree.header()
        th.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        th.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        th.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        th.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        th.resizeSection(0, 110)
        th.resizeSection(1, 70)
        th.resizeSection(2, 70)
        self.field_tree.itemChanged.connect(self._on_field_item_changed)
        label_layout.addWidget(self.field_tree)

        opts_row1 = QHBoxLayout()
        opts_row1.addWidget(QLabel(self.tr("Separator:")))
        self.sep_edit = QLineEdit(",")
        self.sep_edit.setMaximumWidth(60)
        opts_row1.addWidget(self.sep_edit)

        self.newline_check = QCheckBox(self.tr("One field per line"))
        self.newline_check.setToolTip(self.tr(
            "Each checked field goes on its own label line,\n"
            "instead of being joined with the separator."))
        self.newline_check.toggled.connect(
            lambda on: self.sep_edit.setEnabled(not on))
        opts_row1.addWidget(self.newline_check)

        opts_row1.addStretch()
        self.shared_opts_check = QCheckBox(
            self.tr("Same settings for all layers"))
        self.shared_opts_check.setChecked(True)
        self.shared_opts_check.setToolTip(self.tr(
            "Separator, text size and display mode\n"
            "apply to every layer."))
        opts_row1.addWidget(self.shared_opts_check)
        label_layout.addLayout(opts_row1)

        opts_row2 = QHBoxLayout()
        opts_row2.addWidget(QLabel(self.tr("Text size:")))
        self.size_spin = QDoubleSpinBox()
        self.size_spin.setRange(0.5, 50)
        self.size_spin.setSingleStep(0.5)
        self.size_spin.setDecimals(1)
        self.size_spin.setValue(4.0)
        self.size_spin.setSuffix(" pt")
        self.size_spin.setMaximumWidth(80)
        opts_row2.addWidget(self.size_spin)

        opts_row2.addSpacing(15)
        self.adaptive_check = QCheckBox(self.tr("Auto (fit to geometry)"))
        self.adaptive_check.setChecked(True)
        self.adaptive_check.toggled.connect(self._on_adaptive_toggled)
        opts_row2.addWidget(self.adaptive_check)

        self.fixed_label = QLabel(self.tr("Fixed height:"))
        self.fixed_label.setVisible(False)
        opts_row2.addWidget(self.fixed_label)
        self.fixed_spin = QDoubleSpinBox()
        self.fixed_spin.setRange(0.1, 50.0)
        self.fixed_spin.setSingleStep(0.5)
        self.fixed_spin.setDecimals(1)
        self.fixed_spin.setValue(1.5)
        self.fixed_spin.setSuffix(" m")
        self.fixed_spin.setMaximumWidth(80)
        self.fixed_spin.setVisible(False)
        opts_row2.addWidget(self.fixed_spin)

        opts_row2.addStretch()
        label_layout.addLayout(opts_row2)
        self.label_group.setLayout(label_layout)
        main.addWidget(self.label_group)

        # sized to their content so the simple tab doesn't waste space, the stretch below eats the leftover height
        self.out_tabs = QTabWidget()
        self.out_tabs.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.out_tabs.addTab(self._build_simple_tab(), self.tr("Single file"))
        self.out_tabs.addTab(self._build_split_tab(), self.tr("Split by field"))
        self.out_tabs.currentChanged.connect(self._on_tab_changed)
        main.addWidget(self.out_tabs)
        main.addStretch(1)

        self.progress_bar = self.create_progress_bar()
        main.addWidget(self.progress_bar)

        btn_layout, self.run_btn, self.cancel_btn = \
            self.create_button_row(self.tr("Export"), self.tr("Close"))
        main.addLayout(btn_layout)

        self.setLayout(main)
        self._populate_layers()
        self._refresh_label_status()

        self.remember("separator", self.sep_edit)
        self.remember("one_field_per_line", self.newline_check)
        self.remember("shared_options", self.shared_opts_check)
        self.remember("text_size", self.size_spin)
        self.remember("adaptive_text", self.adaptive_check)
        self.remember("fixed_text_size", self.fixed_spin)
        self.remember("split_template", self.split_template_edit)
        self.remember("split_selected_only", self.split_selected_only)
        self.remember("output_mode", self.out_tabs)
        self.restore_remembered()

    # --- populate layer table ---

    def _populate_layers(self):
        from ..services.dxf_export_service import read_layer_style

        layers = [
            lyr for lyr in QgsProject.instance().mapLayers().values()
            if isinstance(lyr, QgsVectorLayer) and lyr.isValid()
        ]
        self.table.setRowCount(len(layers))

        for row, layer in enumerate(layers):
            style = read_layer_style(layer)

            # name gets a geometry glyph in front
            geom_icons = {
                QgsWkbTypes.PointGeometry: "●",
                QgsWkbTypes.LineGeometry: "╌",
                QgsWkbTypes.PolygonGeometry: "▣",
            }
            icon = geom_icons.get(layer.geometryType(), "◆")
            self.table.setItem(
                row, COL_NAME,
                QTableWidgetItem(f"{icon} {layer.name()}"),
            )
            self.table.item(row, COL_NAME).setData(Qt.ItemDataRole.UserRole, layer.id())

            chk = QCheckBox()
            chk.setChecked(False)
            chk.toggled.connect(self._on_export_toggled)
            self._center_widget(row, COL_EXPORT, chk)

            stroke_btn = QgsColorButton()
            stroke_btn.setColor(QColor(*style["stroke_color"]))
            stroke_btn.setMinimumWidth(50)
            stroke_btn.setMinimumHeight(24)
            self.table.setCellWidget(row, COL_STROKE, stroke_btn)

            width_spin = QDoubleSpinBox()
            width_spin.setRange(0, 5)
            width_spin.setSingleStep(0.05)
            width_spin.setDecimals(2)
            width_spin.setValue(style["stroke_width"])
            width_spin.setMaximumWidth(65)
            self.table.setCellWidget(row, COL_WIDTH, width_spin)

            txt_btn = QgsColorButton()
            txt_btn.setColor(QColor(*style["label_color"]))
            txt_btn.setMinimumWidth(50)
            txt_btn.setMinimumHeight(24)
            self.table.setCellWidget(row, COL_TXT_CLR, txt_btn)

            lbl_chk = QCheckBox()
            lbl_chk.setChecked(style["labels_enabled"])
            lbl_chk.toggled.connect(self._on_labels_toggled)
            self._center_widget(row, COL_LABELS, lbl_chk)

            # font off the QGIS style, used at export time
            self.table.item(row, COL_NAME).setData(
                Qt.ItemDataRole.UserRole + 1, style.get("label_font", "Open Sans"))

            # the box is ticked from the layer's labeling, so take the field from there too - ticked with no field exports a DXF with no text at all
            label_field = style.get("label_field")
            if (style["labels_enabled"] and label_field
                    and layer.fields().indexOf(label_field) >= 0):
                prefix, suffix = label_memory.load_default(label_field)
                self._layer_configs[layer.id()] = {
                    "fields": [{
                        "field": label_field,
                        "prefix": prefix,
                        "suffix": suffix,
                    }],
                }

            self._cache_sample_values(layer)

        self._adjust_table_height()

    def _adjust_table_height(self):
        """Cap the table to its visible rows, up to 6, plus header - it otherwise claims ~200px no matter how few rows and dominates the dialog height."""
        rows = self.table.rowCount()
        header_h = self.table.horizontalHeader().height() or 28
        if rows == 0:
            self.table.setFixedHeight(header_h + 20)
            return
        row_h = self.table.rowHeight(0) or 30
        visible = min(rows, 6)
        # frame plus a little padding so the last row doesn't touch the edge
        total = header_h + visible * row_h + 6
        self.table.setMinimumHeight(header_h + row_h + 6)
        self.table.setMaximumHeight(total)

    def _center_widget(self, row, col, widget):
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.addWidget(widget)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table.setCellWidget(row, col, container)

    def _get_checkbox(self, row, col):
        """Pull a QCheckBox back out of a centered cell widget."""
        container = self.table.cellWidget(row, col)
        if container:
            layout = container.layout()
            if layout and layout.count() > 0:
                return layout.itemAt(0).widget()
        return None

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

    # --- selection helpers ---

    def _set_all_checked(self, checked):
        for row in range(self.table.rowCount()):
            chk = self._get_checkbox(row, COL_EXPORT)
            if chk:
                chk.setChecked(checked)

    def _select_visible_only(self):
        """Check only the layers currently visible in QGIS."""
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_NAME)
            if not item:
                continue
            layer_id = item.data(Qt.ItemDataRole.UserRole)
            layer = QgsProject.instance().mapLayer(layer_id)
            visible = False
            if layer:
                root = QgsProject.instance().layerTreeRoot()
                node = root.findLayer(layer_id)
                visible = node.isVisible() if node else False
            chk = self._get_checkbox(row, COL_EXPORT)
            if chk:
                chk.setChecked(visible)

    # --- label config panel ---

    def _on_row_changed(self, row, _col, prev_row, _prev_col):
        """Show or hide the label panel as the row selection changes."""
        if row < 0:
            self.label_group.setVisible(False)
            return

        # save the previous layer's config before switching
        self._save_label_config_for_row(prev_row)

        lbl_chk = self._get_checkbox(row, COL_LABELS)
        labels_on = lbl_chk and lbl_chk.isChecked()

        item = self.table.item(row, COL_NAME)
        if not item:
            self.label_group.setVisible(False)
            return

        layer_id = item.data(Qt.ItemDataRole.UserRole)
        layer = QgsProject.instance().mapLayer(layer_id)

        if labels_on and layer:
            self.label_group.setTitle(
                self.tr("Labels: {0}").format(layer.name()))
            self._populate_field_tree(layer)
            self.label_group.setVisible(True)
        else:
            self._tree_layer_id = None
            self.label_group.setVisible(False)
        self._refresh_label_status()

    def _populate_field_tree(self, layer):
        """Fill the field tree for a layer, restoring whatever config was saved."""
        self.field_tree.blockSignals(True)
        self.field_tree.clear()
        layer_id = layer.id()
        self._tree_layer_id = layer_id
        saved = self._layer_configs.get(layer_id, {})
        saved_fields = saved.get("fields", [])
        saved_field_map = {f["field"]: f for f in saved_fields}

        samples = self._sample_values.get(layer_id, {})
        italic = QFont()
        italic.setItalic(True)
        grey = QColor(120, 120, 120)

        for field in layer.fields():
            fname = field.name()

            if fname in saved_field_map:
                cfg = saved_field_map[fname]
                prefix = cfg.get("prefix", "")
                suffix = cfg.get("suffix", "")
                checked = True
            else:
                # remembered from past exports, shared with the KMZ dialog
                prefix, suffix = label_memory.load_default(fname)
                checked = False

            sample = samples.get(fname, "...")
            example = f"{prefix}{sample}{suffix}"
            item = QTreeWidgetItem([fname, prefix, suffix, example])
            item.setFlags(
                item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable
            )
            # real field name goes in UserRole - the whole row is editable, and a renamed Field cell would get saved as the field to label and fail on every feature
            item.setData(0, Qt.ItemDataRole.UserRole, fname)
            item.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            item.setFont(3, italic)
            item.setForeground(3, grey)
            self.field_tree.addTopLevelItem(item)

        # per-layer settings, skipped when they're shared across layers
        if not self.shared_opts_check.isChecked():
            self.sep_edit.setText(saved.get("separator", ","))
            self.newline_check.setChecked(saved.get("newline", False))
            self.size_spin.setValue(saved.get("size_pt", 4.0))
            self.adaptive_check.setChecked(saved.get("adaptive", True))
            self.fixed_spin.setValue(saved.get("fixed_size", 1.5))

        self.field_tree.blockSignals(False)

    def _on_field_item_changed(self, item, column):
        """Keep the Example column live as Prefix or Suffix change."""
        fname = item.data(0, Qt.ItemDataRole.UserRole) or item.text(0)
        # Field column is display-only, bounce edits back
        if column == 0 and item.text(0) != fname:
            self.field_tree.blockSignals(True)
            item.setText(0, fname)
            self.field_tree.blockSignals(False)
            return
        # column 3 is the example itself, recomputing also reverts anything typed into it
        if column not in (1, 2, 3):
            # a tick or untick arrives as column 0
            self._refresh_label_status()
            return
        layer_id = self._current_layer_id()
        samples = self._sample_values.get(layer_id, {}) if layer_id else {}
        sample = samples.get(fname, "...")
        example = f"{item.text(1)}{sample}{item.text(2)}"
        self.field_tree.blockSignals(True)
        item.setText(3, example)
        self.field_tree.blockSignals(False)

    def _save_label_config_for_row(self, row=None):
        """Save the field tree state for a row, current row by default."""
        if not self.label_group.isVisible():
            return
        if row is None or row < 0:
            row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, COL_NAME)
        if not item:
            return
        layer_id = item.data(Qt.ItemDataRole.UserRole)

        fields = []
        for i in range(self.field_tree.topLevelItemCount()):
            fi = self.field_tree.topLevelItem(i)
            if fi.checkState(0) == Qt.CheckState.Checked:
                fields.append({
                    "field": (fi.data(0, Qt.ItemDataRole.UserRole)
                              or fi.text(0)),
                    "prefix": fi.text(1),
                    "suffix": fi.text(2),
                })

        self._layer_configs[layer_id] = {
            "fields": fields,
            "separator": self.sep_edit.text(),
            "newline": self.newline_check.isChecked(),
            "size_pt": self.size_spin.value(),
            "adaptive": self.adaptive_check.isChecked(),
            "fixed_size": self.fixed_spin.value(),
        }

    def _current_layer_id(self):
        """layer_id of the selected table row."""
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, COL_NAME)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _row_of_checkbox(self, widget, col):
        """Table row a checkbox in column col belongs to, None if it isn't one of ours."""
        if widget is None:
            return None
        for row in range(self.table.rowCount()):
            if self._get_checkbox(row, col) is widget:
                return row
        return None

    def _on_labels_toggled(self, checked):
        """Labels box toggled. Ticking one also selects its row, so the field chooser opens with it - the panel only ever shows the selected row."""
        row = self._row_of_checkbox(self.sender(), COL_LABELS)
        if row is None:
            row = self.table.currentRow()
        if row < 0:
            return
        if checked and self.table.currentRow() != row:
            # setCurrentCell fires _on_row_changed, which opens the panel
            self.table.setCurrentCell(row, COL_NAME)
        elif self.table.currentRow() == row:
            # unticking some other row must not close the panel of the one on screen
            self._on_row_changed(row, 0, row, 0)
        self._refresh_label_status()

    def _refresh_label_status(self):
        """The line under the table: what the Labels column is for, or which layers have it ticked with no field chosen."""
        missing = []
        for row in range(self.table.rowCount()):
            chk = self._get_checkbox(row, COL_LABELS)
            if not chk or not chk.isChecked():
                continue
            item = self.table.item(row, COL_NAME)
            if not item:
                continue
            layer_id = item.data(Qt.ItemDataRole.UserRole)
            if layer_id == self._tree_layer_id:
                # the panel is showing this layer, so its tree is newer than the saved config
                has_fields = any(
                    self.field_tree.topLevelItem(i).checkState(0)
                    == Qt.CheckState.Checked
                    for i in range(self.field_tree.topLevelItemCount()))
            else:
                has_fields = bool(
                    self._layer_configs.get(layer_id, {}).get("fields"))
            if not has_fields:
                layer = QgsProject.instance().mapLayer(layer_id)
                missing.append(layer.name() if layer else item.text())

        if missing:
            self.label_status.setText(self.tr(
                "Labels are ticked but no field is chosen for: {0}. "
                "Click the layer's row and tick its fields in the Labels "
                "panel.").format(", ".join(missing)))
            self.label_status.setStyleSheet("color: #d9822b;")
        else:
            self.label_status.setText(self.tr(
                "Tick Labels on a layer to choose which fields are written "
                "as text."))
            self.label_status.setStyleSheet(
                "color: #707070; font-style: italic;")

    def _on_adaptive_toggled(self, checked):
        self.fixed_label.setVisible(not checked)
        self.fixed_spin.setVisible(not checked)

    def _reset_current_layer(self):
        """Reset the row's style columns back to the live QGIS style."""
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, COL_NAME)
        if not item:
            return
        layer_id = item.data(Qt.ItemDataRole.UserRole)
        layer = QgsProject.instance().mapLayer(layer_id)
        if not layer:
            return

        from ..services.dxf_export_service import read_layer_style
        style = read_layer_style(layer)

        # cellWidget hands back None if a row's widgets were never built, so guard each one. only the visual columns reset, labels stay untouched
        w_stroke = self.table.cellWidget(row, COL_STROKE)
        if w_stroke:
            w_stroke.setColor(QColor(*style["stroke_color"]))
        w_width = self.table.cellWidget(row, COL_WIDTH)
        if w_width:
            w_width.setValue(style["stroke_width"])
        w_txt = self.table.cellWidget(row, COL_TXT_CLR)
        if w_txt:
            w_txt.setColor(QColor(*style["label_color"]))

    # --- output mode tabs ---

    def _build_simple_tab(self):
        """Tab 1, one combined DXF."""
        tab = QWidget()
        tab.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 10, 8, 8)

        row = QHBoxLayout()
        self.edit_output = QLineEdit()
        self.edit_output.setPlaceholderText(self.tr("DXF file path..."))
        btn_browse = QPushButton(self.tr("Browse..."))
        btn_browse.clicked.connect(self._browse_output)
        row.addWidget(self.edit_output)
        row.addWidget(btn_browse)
        layout.addLayout(row)
        layout.addStretch(1)
        return tab

    def _build_split_tab(self):
        """Tab 2, split by field value, one file per group."""
        tab = QWidget()
        tab.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 10, 8, 8)
        layout.setSpacing(6)

        row_field = QHBoxLayout()
        row_field.addWidget(QLabel(self.tr("Group by:")))
        self.split_field_combo = QComboBox()
        self.split_field_combo.setMinimumWidth(160)
        self.split_field_combo.currentTextChanged.connect(
            self._on_split_field_changed)
        row_field.addWidget(self.split_field_combo)

        self.split_selected_only = QCheckBox(
            self.tr("Selected features only"))
        self.split_selected_only.setToolTip(self.tr(
            "Process only the selected features of each layer."))
        self.split_selected_only.toggled.connect(
            lambda _on: self._refresh_split_values())
        row_field.addWidget(self.split_selected_only)
        row_field.addStretch()
        layout.addLayout(row_field)

        val_group = QGroupBox(self.tr("Values to export"))
        val_layout = QVBoxLayout()
        val_layout.setSpacing(2)
        self.split_values_list = QListWidget()
        self.split_values_list.setMinimumHeight(80)
        val_layout.addWidget(self.split_values_list)

        val_btn_row = QHBoxLayout()
        btn_all_vals = QPushButton(self.tr("All"))
        btn_all_vals.setMaximumWidth(70)
        btn_all_vals.clicked.connect(
            lambda: self._set_all_values_checked(True))
        btn_no_vals = QPushButton(self.tr("None"))
        btn_no_vals.setMaximumWidth(70)
        btn_no_vals.clicked.connect(
            lambda: self._set_all_values_checked(False))
        val_btn_row.addWidget(btn_all_vals)
        val_btn_row.addWidget(btn_no_vals)
        val_btn_row.addStretch()
        val_layout.addLayout(val_btn_row)
        val_group.setLayout(val_layout)
        layout.addWidget(val_group)

        row_folder = QHBoxLayout()
        row_folder.addWidget(QLabel(self.tr("Folder:")))
        self.split_folder_edit = QLineEdit()
        self.split_folder_edit.setPlaceholderText(self.tr("Output folder..."))
        btn_folder = QPushButton(self.tr("Browse..."))
        btn_folder.clicked.connect(self._browse_split_folder)
        row_folder.addWidget(self.split_folder_edit)
        row_folder.addWidget(btn_folder)
        layout.addLayout(row_folder)

        row_tpl = QHBoxLayout()
        row_tpl.addWidget(QLabel(self.tr("Filename template:")))
        self.split_template_edit = QLineEdit("{value}")
        self.split_template_edit.setToolTip(self.tr(
            "Variables: {value}, {layer}, {date}, {field}, {<field_name>}.\n"
            "The extension is added automatically per format."))
        self.split_template_edit.textChanged.connect(
            self._update_template_preview)
        row_tpl.addWidget(self.split_template_edit)
        layout.addLayout(row_tpl)

        self.split_preview_label = QLabel(self.tr("Example: -"))
        self.split_preview_label.setStyleSheet(
            "color: #707070; font-style: italic;")
        layout.addWidget(self.split_preview_label)

        return tab

    def _on_tab_changed(self, index):
        """Refresh the field and value lists on the way into the split tab, then refit the height."""
        if index == 1:
            self._refresh_split_fields()
        # deferred so Qt finishes the tab swap before we measure sizeHint
        QTimer.singleShot(0, self._fit_height_to_content)

    def _fit_height_to_content(self):
        """Cap the tab widget to the active tab's natural height and refit the dialog, width untouched - QSizePolicy.Maximum alone doesn't hold once saved geometry is restored."""
        current = self.out_tabs.currentWidget()
        if current is not None:
            current.adjustSize()
            content_h = current.sizeHint().height()
            bar_h = self.out_tabs.tabBar().sizeHint().height()
            # +12 covers the tab content padding and frame
            self.out_tabs.setMaximumHeight(content_h + bar_h + 12)
        self.resize(self.width(), self.sizeHint().height())

    def showEvent(self, event):
        """BaseDialog restores the saved geometry, then we override the height so this always opens compact. Width is kept."""
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_height_to_content)

    def _on_export_toggled(self, _checked):
        """Layer checked or unchecked, refresh the split fields if that tab is up."""
        if hasattr(self, "out_tabs") and self.out_tabs.currentIndex() == 1:
            self._refresh_split_fields()
            self._update_template_preview()

    def _checked_layers(self):
        """The layers whose table rows are checked."""
        layers = []
        for row in range(self.table.rowCount()):
            chk = self._get_checkbox(row, COL_EXPORT)
            if not chk or not chk.isChecked():
                continue
            item = self.table.item(row, COL_NAME)
            if not item:
                continue
            layer_id = item.data(Qt.ItemDataRole.UserRole)
            layer = QgsProject.instance().mapLayer(layer_id)
            if layer:
                layers.append(layer)
        return layers

    def _refresh_split_fields(self):
        """Refill the field combo with the fields the checked layers have in common."""
        from ..services.split_export_service import get_common_fields
        layers = self._checked_layers()
        previous = self.split_field_combo.currentText()

        self.split_field_combo.blockSignals(True)
        self.split_field_combo.clear()
        common = get_common_fields(layers)
        if common:
            self.split_field_combo.addItems(common)
            # put the previous selection back if it survived
            idx = self.split_field_combo.findText(previous)
            if idx >= 0:
                self.split_field_combo.setCurrentIndex(idx)
        self.split_field_combo.blockSignals(False)
        self._refresh_split_values()

    def _on_split_field_changed(self, _text):
        self._refresh_split_values()
        self._update_template_preview()

    def _refresh_split_values(self):
        """Refill the value list for the selected field."""
        from ..services.split_export_service import get_unique_values
        self.split_values_list.clear()
        field = self.split_field_combo.currentText()
        if not field:
            return
        layers = self._checked_layers()
        if not layers:
            return
        selected_only = self.split_selected_only.isChecked()
        counts = get_unique_values(layers, field, selected_only=selected_only)
        for value, count in counts.items():
            display = "(NULL)" if value is None else str(value)
            label = f"{display}  ({count})"
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, value)
            self.split_values_list.addItem(item)
        self._update_template_preview()

    def _set_all_values_checked(self, checked):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.split_values_list.count()):
            self.split_values_list.item(i).setCheckState(state)

    def _selected_split_values(self):
        out = []
        for i in range(self.split_values_list.count()):
            item = self.split_values_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                out.append(item.data(Qt.ItemDataRole.UserRole))
        return out

    def _update_template_preview(self, *_args):
        """Live filename preview off the first checked value."""
        from ..services.split_export_service import render_filename
        template = self.split_template_edit.text().strip() or "{value}"
        field = self.split_field_combo.currentText()
        sample_value = None
        for i in range(self.split_values_list.count()):
            item = self.split_values_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                sample_value = item.data(Qt.ItemDataRole.UserRole)
                break
        if sample_value is None and self.split_values_list.count() > 0:
            sample_value = self.split_values_list.item(0).data(Qt.ItemDataRole.UserRole)
        if sample_value is None:
            self.split_preview_label.setText(self.tr("Example: -"))
            return
        layers = self._checked_layers()
        layer_name = layers[0].name() if layers else ""
        base = render_filename(template, sample_value, layer_name, field)
        self.split_preview_label.setText(
            self.tr("Example: {0}").format(base + ".dxf"))

    def _browse_split_folder(self):
        path = QFileDialog.getExistingDirectory(
            self, self.tr("Choose the output folder"),
            self.split_folder_edit.text() or "")
        if path:
            self.split_folder_edit.setText(path)

    # --- output file ---

    def _browse_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Save the DXF file"), "",
            self.tr("DXF (*.dxf);;All files (*)"))
        if path:
            if not path.lower().endswith(".dxf"):
                path += ".dxf"
            self.edit_output.setText(path)

    # --- export ---

    def accept(self):
        # flush the current row's label config before collecting
        self._save_label_config_for_row()

        layers_config = self._collect_config()
        if not layers_config:
            self.show_tool_error(
                self.tr("Check at least one layer in the table to export."))
            return

        # a ticked Labels box with no field chosen writes no text at all
        no_field = [
            cfg["layer"].name() for cfg in layers_config
            if cfg["labels_enabled"] and not cfg["label_fields"]
        ]
        if no_field:
            if not self.confirm_action(
                    self.tr("Labels without fields"),
                    self.tr("These layers have Labels ticked but no field "
                            "chosen, so no text will be written:\n\n{0}\n\n"
                            "Click the layer's row and tick its fields in "
                            "the Labels panel, or export without text.\n\n"
                            "Export anyway?").format("\n".join(no_field))):
                return

        # remember prefix/suffix per field for next time, shared with the KMZ dialog
        for cfg in layers_config:
            for f in cfg["label_fields"]:
                label_memory.save_default(
                    f["field"], f["prefix"], f["suffix"])

        if not deps.ensure("ezdxf", parent=self):
            return

        self.save_remembered()
        is_split = self.out_tabs.currentIndex() == 1
        if is_split:
            self._run_split_export(layers_config)
        else:
            self._run_simple_export(layers_config)

    # --- run handlers ---

    def _run_simple_export(self, layers_config):
        """Single-file DXF flow."""
        output_path = self.edit_output.text().strip()
        if not output_path:
            self.show_tool_error(
                self.tr("Choose the output DXF file path."))
            return
        if not output_path.lower().endswith(".dxf"):
            output_path += ".dxf"

        if os.path.exists(output_path):
            if not self.confirm_action(
                    self.tr("Overwrite"),
                    self.tr("The file already exists:\n{0}\n\n"
                            "Overwrite it?").format(output_path)):
                return

        self._begin_export_ui()
        try:
            from ..services.dxf_export_service import export_layers_to_dxf

            self._progress_counter = 0

            def on_progress(current, total):
                self._progress_counter += 1
                if self._progress_counter % 50 == 0 or current == total:
                    self.progress_bar.setMaximum(total)
                    self.progress_bar.setValue(current)
                    QApplication.processEvents()

            ok, skip, err = export_layers_to_dxf(
                layers_config=layers_config,
                output_path=output_path,
                progress_callback=on_progress,
            )
        except Exception as e:
            self.show_tool_failure(e)
            return
        finally:
            self._end_export_ui()

        summary = self.tr(
            "Features exported: {0}\n"
            "Features skipped: {1}\n"
            "Errors: {2}").format(ok, skip, err)
        if ok > 0:
            self.show_export_done(summary, output_path)
        elif err > 0:
            self.show_tool_warning(
                summary + "\n\n" + self.tr(
                    "A file was still written to {0}; its contents may be "
                    "incomplete.").format(output_path))
        else:
            self.show_tool_warning(self.tr("No features were exported."))

    def _run_split_export(self, layers_config):
        """Per-group flow, one DXF per value of the grouping field."""
        field = self.split_field_combo.currentText()
        if not field:
            self.show_tool_error(
                self.tr("The checked layers have no common fields.\n"
                        "Check layers that share at least one field."))
            return

        # layers without the grouping field would just vanish from the output, so say so
        layers_missing = [
            cfg["layer"].name() for cfg in layers_config
            if cfg["layer"].fields().indexOf(field) < 0
        ]
        if layers_missing:
            self.show_tool_warning(
                self.tr("These layers have no '{0}' field and will be "
                        "skipped:\n\n{1}").format(
                            field, "\n".join(layers_missing)))

        values = self._selected_split_values()
        if not values:
            self.show_tool_error(
                self.tr("Check at least one value in the export list."))
            return

        output_dir = self.split_folder_edit.text().strip()
        if not output_dir:
            self.show_tool_error(
                self.tr("Choose the folder where the files will be saved."))
            return
        if not os.path.isdir(output_dir):
            try:
                os.makedirs(output_dir, exist_ok=True)
            except OSError as e:
                self.show_tool_error(
                    self.tr("Could not create the folder:\n{0}").format(e))
                return

        template = self.split_template_edit.text().strip() or "{value}"

        # the service predicts the exact names it will write, placeholders included, so the overwrite check can't drift from the export
        from ..services.split_export_service import predict_split_filenames
        selected_only = self.split_selected_only.isChecked()
        # reads every source layer once, so on a full sector this lands between the click and the overwrite prompt with nothing else on screen
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            predicted = predict_split_filenames(
                layers_config, field, values, template, selected_only)
        finally:
            QApplication.restoreOverrideCursor()
        existing = [
            name for name in predicted
            if os.path.exists(os.path.join(output_dir, name))
        ]
        if existing:
            preview = "\n".join(existing[:5])
            extra = (self.tr("\n...and {0} more").format(len(existing) - 5)
                     if len(existing) > 5 else "")
            count = len(existing)
            if count == 1:
                head = self.tr("1 file already exists in the folder:")
            else:
                head = self.tr(
                    "{0} files already exist in the folder:").format(count)
            if not self.confirm_action(
                    self.tr("Overwrite"),
                    f"{head}\n\n{preview}{extra}\n\n"
                    + self.tr("Overwrite them?")):
                return

        self._begin_export_ui()
        try:
            from ..services.split_export_service import export_split_groups

            self._progress_counter = 0
            total_groups = len(values)

            def on_progress(group_idx, total, current_feat, total_feat):
                self._progress_counter += 1
                if (self._progress_counter % 50 == 0
                        or current_feat == total_feat):
                    self.progress_bar.setMaximum(total_groups)
                    self.progress_bar.setValue(group_idx + 1)
                    self.progress_bar.setFormat(
                        self.tr("Group {0}/{1}").format(
                            group_idx + 1, total_groups))
                    QApplication.processEvents()

            result = export_split_groups(
                layers_config=layers_config,
                group_field=field,
                values=values,
                output_dir=output_dir,
                template=template,
                selected_only=self.split_selected_only.isChecked(),
                progress_callback=on_progress,
            )
        except Exception as e:
            self.show_tool_failure(e)
            return
        finally:
            self.progress_bar.setFormat("%p%")
            self._end_export_ui()

        skipped_groups = [
            g for g in result.per_group if not g["paths"] and g["err"] == 0]

        summary = (
            self.tr("Groups processed: {0}").format(result.total_groups)
            + "\n"
            + self.tr("Files written: {0}").format(result.files_written)
            + "\n"
            + self.tr("Features exported: {0}").format(result.total_success)
            + "\n"
            + self.tr("Errors: {0}").format(result.total_errors)
        )
        if skipped_groups:
            preview = ", ".join(
                str(g["value"]) for g in skipped_groups[:5])
            extra = "..." if len(skipped_groups) > 5 else ""
            summary += "\n\n" + self.tr(
                "Groups without features ({0}): {1}{2}").format(
                    len(skipped_groups), preview, extra)

        if result.files_written > 0:
            self.show_export_done(summary, output_dir)
        elif result.total_errors > 0:
            self.show_tool_warning(summary)
        else:
            self.show_tool_warning(self.tr("No files were exported."))

    def _begin_export_ui(self):
        """Kill the buttons and show the progress bar, both modes."""
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(0)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        # progress pumps events, block Escape and the window X until the run ends
        self._processing = True

    def _end_export_ui(self):
        """Put the UI back after an export, success or not."""
        self._processing = False
        QApplication.restoreOverrideCursor()
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setVisible(False)

    def _collect_config(self):
        """Build the layers_config list from the table state."""
        # in shared mode the widget values get read once and reused for every layer
        shared = self.shared_opts_check.isChecked()
        if shared:
            sh_sep = self.sep_edit.text()
            sh_newline = self.newline_check.isChecked()
            sh_size = self.size_spin.value()
            sh_adaptive = self.adaptive_check.isChecked()
            sh_fixed = self.fixed_spin.value()

        configs = []
        for row in range(self.table.rowCount()):
            export_chk = self._get_checkbox(row, COL_EXPORT)
            if not export_chk or not export_chk.isChecked():
                continue

            item = self.table.item(row, COL_NAME)
            if not item:
                continue
            layer_id = item.data(Qt.ItemDataRole.UserRole)
            layer = QgsProject.instance().mapLayer(layer_id)
            if not layer:
                continue

            stroke_btn = self.table.cellWidget(row, COL_STROKE)
            sc = stroke_btn.color()
            width_spin = self.table.cellWidget(row, COL_WIDTH)
            txt_btn = self.table.cellWidget(row, COL_TXT_CLR)
            tc = txt_btn.color()
            lbl_chk = self._get_checkbox(row, COL_LABELS)

            labels_on = lbl_chk and lbl_chk.isChecked()
            label_cfg = self._layer_configs.get(layer_id, {})

            if shared:
                sep, newline = sh_sep, sh_newline
                size_pt, adaptive, fixed = sh_size, sh_adaptive, sh_fixed
            else:
                sep = label_cfg.get("separator", ",")
                newline = label_cfg.get("newline", False)
                size_pt = label_cfg.get("size_pt", 4.0)
                adaptive = label_cfg.get("adaptive", True)
                fixed = label_cfg.get("fixed_size", 1.5)

            configs.append({
                "layer": layer,
                "stroke_color": (sc.red(), sc.green(), sc.blue()),
                "stroke_width": width_spin.value(),
                "labels_enabled": labels_on,
                "label_color": (tc.red(), tc.green(), tc.blue()),
                "label_size_pt": size_pt,
                "adaptive_text": adaptive,
                "fixed_text_size": fixed,
                "label_fields": label_cfg.get("fields", []),
                "label_separator": sep,
                "label_newline": newline,
                "label_font": item.data(Qt.ItemDataRole.UserRole + 1) or "Open Sans",
            })

        return configs
