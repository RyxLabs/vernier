# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Attribute join - pull columns from several source layers into one target by matching key fields, either written into the target's own fields or attached as a virtual join."""

from qgis.PyQt.QtCore import Qt  # type: ignore
from qgis.PyQt.QtWidgets import (  # type: ignore
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)
from qgis.core import (  # type: ignore
    QgsFeature, QgsField, QgsFields, QgsMapLayerProxyModel, QgsProject,
    QgsVectorLayer, QgsVectorLayerJoinInfo, QgsWkbTypes,
)
from qgis.gui import QgsMapLayerComboBox  # type: ignore

from ..qt_compat import FIELD_STRING
from ..services import join_service
from .base_dialog import BaseDialog


class AttributeJoinDialog(BaseDialog):

    def __init__(self, iface=None, parent=None):
        super().__init__(iface, parent)
        self.setWindowTitle(self.tr("Join Attributes"))
        self.setMinimumSize(700, 620)
        self._setup_ui()
        self._populate_sources()

    # --- ui ---

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        sources_group = QGroupBox(
            self.tr("Source layers (where the values come from)"))
        sources_layout = QVBoxLayout()
        sources_layout.addWidget(QLabel(self.tr(
            "Check the source layers and pick each one's key field:")))

        self.sources_table = QTableWidget()
        self.sources_table.setColumnCount(2)
        self.sources_table.setHorizontalHeaderLabels(
            [self.tr("Source layer"), self.tr("Key field")])
        self.sources_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.sources_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        self.sources_table.verticalHeader().setVisible(False)
        self.sources_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.sources_table.setMaximumHeight(140)
        self.sources_table.itemChanged.connect(self._on_sources_changed)
        sources_layout.addWidget(self.sources_table)

        sources_layout.addWidget(QLabel(self.tr(
            "Columns to bring over (Ctrl+click for several):")))
        columns_row = QHBoxLayout()
        self.columns_list = QListWidget()
        self.columns_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.columns_list.setMaximumHeight(120)
        columns_row.addWidget(self.columns_list)
        column_buttons = QVBoxLayout()
        all_btn = QPushButton(self.tr("All"))
        all_btn.clicked.connect(self.columns_list.selectAll)
        none_btn = QPushButton(self.tr("None"))
        none_btn.clicked.connect(self.columns_list.clearSelection)
        column_buttons.addWidget(all_btn)
        column_buttons.addWidget(none_btn)
        column_buttons.addStretch()
        columns_row.addLayout(column_buttons)
        sources_layout.addLayout(columns_row)
        sources_group.setLayout(sources_layout)
        layout.addWidget(sources_group)

        target_group = QGroupBox(
            self.tr("Target layer (where the values go)"))
        target_form = QFormLayout()
        self.target_combo = QgsMapLayerComboBox()
        self.target_combo.setFilters(QgsMapLayerProxyModel.VectorLayer)
        self.target_combo.layerChanged.connect(self._update_target_fields)
        target_form.addRow(self.tr("Target layer:"), self.target_combo)
        self.target_key_combo = QComboBox()
        target_form.addRow(self.tr("Key field:"), self.target_key_combo)
        target_group.setLayout(target_form)
        layout.addWidget(target_group)

        output_group = QGroupBox(self.tr("Output"))
        output_layout = QVBoxLayout()
        self.permanent_check = QCheckBox(
            self.tr("Write values into the target layer's fields"))
        self.permanent_check.setChecked(True)
        self.permanent_check.setToolTip(self.tr(
            "Unchecked: a virtual join is attached instead\n"
            "(like Layer Properties > Joins) and nothing is written."))
        output_layout.addWidget(self.permanent_check)
        edit_note = QLabel(self.tr(
            "Writing leaves the target layer in editing mode: review the "
            "changes, then save or discard them yourself."))
        edit_note.setWordWrap(True)
        output_layout.addWidget(edit_note)

        self.scratch_check = QCheckBox(
            self.tr("Create a scratch layer with the joined records"))
        self.scratch_check.setChecked(True)
        self.scratch_check.setToolTip(self.tr(
            "A scratch memory layer with every target record plus the\n"
            "joined columns. It disappears when QGIS closes - keep it\n"
            "with Layer > Export > Save Features As..."))
        output_layout.addWidget(self.scratch_check)
        output_group.setLayout(output_layout)
        layout.addWidget(output_group)

        self.progress_bar = self.create_progress_bar()
        layout.addWidget(self.progress_bar)
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

        self.remember("write_into_target", self.permanent_check)
        self.remember("scratch_layer", self.scratch_check)
        self.restore_remembered()

        buttons = QHBoxLayout()
        self.preview_btn = QPushButton(self.tr("Preview matches"))
        self.preview_btn.clicked.connect(self._preview)
        buttons.addWidget(self.preview_btn)
        buttons.addStretch()
        self.run_btn = QPushButton(self.tr("Run join"))
        self.run_btn.setDefault(True)
        self.run_btn.clicked.connect(self._run)
        buttons.addWidget(self.run_btn)
        self.close_btn = QPushButton(self.tr("Close"))
        self.close_btn.clicked.connect(self.reject)
        buttons.addWidget(self.close_btn)
        layout.addLayout(buttons)

    def _populate_sources(self):
        layers = [layer for layer in
                  QgsProject.instance().mapLayers().values()
                  if isinstance(layer, QgsVectorLayer)]
        self.sources_table.blockSignals(True)
        self.sources_table.setRowCount(0)
        for layer in layers:
            row = self.sources_table.rowCount()
            self.sources_table.insertRow(row)
            item = QTableWidgetItem(layer.name())
            item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, layer.id())
            self.sources_table.setItem(row, 0, item)
            combo = QComboBox()
            combo.addItems([field.name() for field in layer.fields()])
            self.sources_table.setCellWidget(row, 1, combo)
        self.sources_table.blockSignals(False)

        self.preselect_active_layer(self.target_combo)
        self._update_target_fields()

    def _update_target_fields(self):
        self.target_key_combo.clear()
        layer = self.target_combo.currentLayer()
        if isinstance(layer, QgsVectorLayer):
            self.target_key_combo.addItems(
                [field.name() for field in layer.fields()])

    def _on_sources_changed(self, item):
        if item.column() == 0:
            self._rebuild_columns()

    def _rebuild_columns(self):
        self.columns_list.clear()
        sources = self._get_sources()
        multiple = len(sources) > 1
        for source, _ in sources:
            for field in source.fields():
                if multiple:
                    display = f"{field.name()}   ({source.name()})"
                else:
                    display = field.name()
                item = QListWidgetItem(display)
                item.setData(Qt.ItemDataRole.UserRole, (source.id(), field.name()))
                self.columns_list.addItem(item)
        self.columns_list.selectAll()

    # --- selection accessors ---

    def _get_sources(self):
        """[(layer, key_field), ...] for the checked source rows."""
        result = []
        for row in range(self.sources_table.rowCount()):
            item = self.sources_table.item(row, 0)
            if not item or item.checkState() != Qt.CheckState.Checked:
                continue
            layer = QgsProject.instance().mapLayer(item.data(Qt.ItemDataRole.UserRole))
            combo = self.sources_table.cellWidget(row, 1)
            key = combo.currentText() if combo else ""
            if layer and key:
                result.append((layer, key))
        return result

    def _get_target(self):
        """(layer, key_field) or None."""
        layer = self.target_combo.currentLayer()
        key = self.target_key_combo.currentText()
        if not isinstance(layer, QgsVectorLayer) or not key:
            return None
        return layer, key

    def _selected_columns(self):
        """[(layer_id, field_name), ...] from the columns list."""
        return [item.data(Qt.ItemDataRole.UserRole)
                for item in self.columns_list.selectedItems()
                if item.data(Qt.ItemDataRole.UserRole)]

    def _columns_by_source(self):
        by_source = {}
        for layer_id, field_name in self._selected_columns():
            by_source.setdefault(layer_id, []).append(field_name)
        return by_source

    # --- validation ---

    def _colliding_columns(self, sources, target):
        """Picked columns the target already has a field for - the joined values would replace them, so ask first."""
        existing = {field.name() for field in target.fields()}
        columns_by_source = self._columns_by_source()
        names = []
        for source, source_key in sources:
            for column in columns_by_source.get(source.id(), []):
                if column == source_key or column not in existing:
                    continue
                if column not in names:
                    names.append(column)
        return names

    def _validate(self):
        sources = self._get_sources()
        target = self._get_target()
        if not sources:
            self.show_tool_warning(
                self.tr("Check at least one source layer."))
            return False
        if target is None:
            self.show_tool_warning(
                self.tr("Pick a target layer and its key field."))
            return False
        if not self._selected_columns():
            self.show_tool_warning(
                self.tr("Select at least one column to bring over."))
            return False
        target_layer = target[0]
        if target_layer.id() in {layer.id() for layer, _ in sources}:
            if not self.confirm_action(
                    self.tr("Source is also the target"),
                    self.tr("'{0}' is both a source and the target layer.\n"
                            "The results can be unpredictable.\n\n"
                            "Continue?").format(target_layer.name())):
                return False
        return True

    def _confirm_overwrite(self):
        """Only writing touches the target's own fields, so a preview or a virtual join never asks."""
        if not self.permanent_check.isChecked():
            return True
        target = self._get_target()
        if target is None:
            return True
        collisions = self._colliding_columns(self._get_sources(), target[0])
        if not collisions:
            return True
        return self.confirm_action(
            self.tr("Columns already exist"),
            self.tr("These columns already exist in '{0}' and their current "
                    "values will be replaced by the joined ones:\n{1}\n\n"
                    "Continue?").format(target[0].name(),
                                        ", ".join(collisions)))

    # --- preview ---

    def _preview(self):
        if not self._validate():
            return
        target, target_key = self._get_target()
        columns_by_source = self._columns_by_source()
        total = target.featureCount()
        lines = [self.tr("Target: {0} ({1} records, key: {2})").format(
            target.name(), total, target_key), ""]
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for source, source_key in self._get_sources():
                keys = join_service.key_set(source, source_key)
                matched = join_service.count_matches(target, target_key, keys)
                columns = columns_by_source.get(source.id(), [])
                lines.append(self.tr(
                    "{0} (key: {1}, {2} columns): "
                    "{3} of {4} records match").format(
                        source.name(), source_key, len(columns),
                        matched, total))
        finally:
            QApplication.restoreOverrideCursor()
        self.show_info(self.tr("Match preview"), "\n".join(lines))

    # --- run ---

    def _set_busy(self, busy):
        for button in (self.preview_btn, self.run_btn, self.close_btn):
            button.setEnabled(not busy)
        self.run_btn.setText(
            self.tr("Working...") if busy else self.tr("Run join"))

    def _run(self):
        if not self._validate() or not self._confirm_overwrite():
            return
        self.save_remembered()
        self._set_busy(True)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        # progress pumps events, block Escape and the window X until the run ends
        self._processing = True
        try:
            self._execute()
        except Exception as e:
            self.show_tool_failure(e)
        finally:
            self._processing = False
            QApplication.restoreOverrideCursor()
            self.progress_bar.setVisible(False)
            self.status_label.setText("")
            self._set_busy(False)

    def _execute(self):
        target, target_key = self._get_target()
        sources = self._get_sources()
        columns_by_source = self._columns_by_source()
        permanent = self.permanent_check.isChecked()

        source_data = {}
        for source, source_key in sources:
            source_data[source.id()] = join_service.build_key_map(
                source, source_key, columns_by_source.get(source.id(), []))

        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(len(sources))
        self.progress_bar.setValue(0)

        results = []
        errors = []
        written = False
        for done, (source, source_key) in enumerate(sources, start=1):
            columns = [column
                       for column in columns_by_source.get(source.id(), [])
                       if column != source_key]
            if not columns:
                # nothing picked here, joining anyway attaches a join with no fields or flips the target into editing for zero changes
                results.append(self.tr("{0}: skipped, no columns selected")
                               .format(source.name()))
                self.progress_bar.setValue(done)
                continue
            self.status_label.setText(
                self.tr("Joining {0}...").format(source.name()))
            QApplication.processEvents()
            try:
                if permanent:
                    count, join_errors = self._write_values(
                        target, target_key, source,
                        source_data[source.id()], columns)
                    written = True
                    results.append(self.tr("{0}: {1} records updated").format(
                        source.name(), count))
                    errors.extend(join_errors)
                else:
                    self._attach_virtual_join(
                        target, target_key, source, source_key, columns)
                    results.append(self.tr("{0}: virtual join attached")
                                   .format(source.name()))
            except Exception as e:
                errors.append(f"{source.name()}: {e}")
            self.progress_bar.setValue(done)
            QApplication.processEvents()

        scratch = None
        if self.scratch_check.isChecked():
            self.status_label.setText(self.tr("Building the scratch layer..."))
            QApplication.processEvents()
            try:
                scratch = self._build_scratch_layer(
                    target, target_key, sources, source_data,
                    columns_by_source)
            except Exception as e:
                errors.append(self.tr("Scratch layer: {0}").format(e))

        target.triggerRepaint()
        if self.iface:
            self.iface.mapCanvas().refresh()

        details = "\n".join(results)
        if permanent and written:
            details += "\n\n" + self.tr(
                "The target layer is now in editing mode - review the "
                "changes and save or discard them.")

        if errors:
            # stays open, the user usually wants to fix the cause and run again
            self.show_tool_warning(
                details + "\n\n" + "\n".join(f"- {e}" for e in errors))
            return

        if scratch is not None:
            self.show_layer_created(scratch, details)
        elif permanent:
            self.show_success(
                self.tr('Values written into "{0}".').format(target.name()),
                details=details)
        else:
            self.show_success(
                self.tr('Virtual join attached to "{0}".').format(
                    target.name()),
                details=details)
        self.accept()

    # --- join modes ---

    def _write_values(self, target, target_key, source, data, columns):
        """Copy matched values into the target's own fields, leaving the layer in editing mode so the user reviews and saves or discards."""
        errors = []
        if not target.isEditable() and not target.startEditing():
            raise RuntimeError(self.tr(
                "Could not switch '{0}' to editing mode.").format(
                    target.name()))

        source_fields = {field.name(): field for field in source.fields()}
        existing = {field.name() for field in target.fields()}
        for column in columns:
            if column in existing:
                continue
            template = source_fields.get(column)
            field = (QgsField(template) if template
                     else QgsField(column, FIELD_STRING))
            if not target.addAttribute(field):
                errors.append(self.tr(
                    "Could not add field '{0}' to '{1}' (read-only "
                    "provider or a format limit, e.g. 255 fields for "
                    "shapefiles).").format(column, target.name()))
        target.updateFields()

        index_map = {}
        for column in columns:
            index = target.fields().indexOf(column)
            if index >= 0:
                index_map[column] = index

        count = 0
        for feature in target.getFeatures():
            key = join_service.normalize_key(feature[target_key])
            if key is None or key not in data:
                continue
            changes = {index: data[key].get(column)
                       for column, index in index_map.items()}
            if changes and target.changeAttributeValues(feature.id(),
                                                        changes):
                count += 1
        return count, errors

    def _attach_virtual_join(self, target, target_key, source, source_key,
                             columns):
        for existing in target.vectorJoins():
            if existing.joinLayerId() == source.id():
                target.removeJoin(source.id())
                break
        info = QgsVectorLayerJoinInfo()
        info.setJoinLayer(source)
        info.setJoinLayerId(source.id())
        info.setJoinFieldName(source_key)
        info.setTargetFieldName(target_key)
        info.setUsingMemoryCache(True)
        info.setPrefix("")
        # only the picked columns, and the source key is never one of them so it doesn't come over either
        info.setJoinFieldNamesSubset(columns)
        target.addJoin(info)

    # --- scratch layer ---

    def _build_scratch_layer(self, target, target_key, sources, source_data,
                             columns_by_source):
        has_geometry = target.geometryType() != QgsWkbTypes.NullGeometry
        if has_geometry:
            uri = QgsWkbTypes.displayString(target.wkbType())
        else:
            uri = "NoGeometry"
        # {input}_{operation}, the same shape every other Vernier tool names its result with
        scratch = QgsVectorLayer(uri, f"{target.name()}_join", "memory")
        if not scratch.isValid():
            raise RuntimeError(
                self.tr("Could not create the scratch layer."))
        if has_geometry:
            # setCrs rather than a ?crs= URI, custom CRS have an empty authid and the scratch layer would end up CRS-less
            scratch.setCrs(target.crs())
        provider = scratch.dataProvider()

        fields = QgsFields()
        names = set()
        for field in target.fields():
            fields.append(QgsField(field))
            names.add(field.name())
        for source, source_key in sources:
            source_fields = {field.name(): field
                             for field in source.fields()}
            for column in columns_by_source.get(source.id(), []):
                if column == source_key or column in names:
                    continue
                template = source_fields.get(column)
                fields.append(QgsField(template) if template
                              else QgsField(column, FIELD_STRING))
                names.add(column)
        provider.addAttributes(list(fields))
        scratch.updateFields()

        index_of = {field.name(): i
                    for i, field in enumerate(scratch.fields())}
        target_names = [field.name() for field in target.fields()]

        new_features = []
        for feature in target.getFeatures():
            out = QgsFeature(scratch.fields())
            if has_geometry:
                out.setGeometry(feature.geometry())
            attributes = [None] * len(index_of)
            for name in target_names:
                attributes[index_of[name]] = feature[name]
            key = join_service.normalize_key(feature[target_key])
            if key is not None:
                for source, source_key in sources:
                    row = source_data[source.id()].get(key)
                    if row is None:
                        continue
                    for column in columns_by_source.get(source.id(), []):
                        if column == source_key:
                            continue
                        index = index_of.get(column)
                        if index is not None:
                            attributes[index] = row.get(column)
            out.setAttributes(attributes)
            new_features.append(out)

        provider.addFeatures(new_features)
        scratch.updateExtents()
        QgsProject.instance().addMapLayer(scratch)
        return scratch
