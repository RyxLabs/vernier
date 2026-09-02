# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Spatial join - pull attribute values onto a polygon target from every layer that intersects it, with join_id / join_source / join_count recording where each value came from."""

from collections import defaultdict

from qgis.PyQt.QtCore import Qt  # type: ignore
from qgis.PyQt.QtWidgets import (  # type: ignore
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QVBoxLayout,
)
from qgis.core import (  # type: ignore
    QgsCoordinateTransform, QgsCsException, QgsFeature, QgsField,
    QgsFields, QgsMapLayerProxyModel, QgsMemoryProviderUtils, QgsProject,
    QgsSpatialIndex, QgsVectorLayer, QgsWkbTypes,
)

from ..qt_compat import FIELD_INT, FIELD_STRING
from ..services import join_service
from . import _ui_helpers
from .base_dialog import BaseDialog

_GEOM_MARKS = {
    QgsWkbTypes.GeometryType.PointGeometry: "● ",
    QgsWkbTypes.GeometryType.LineGeometry: "╌ ",
    QgsWkbTypes.GeometryType.PolygonGeometry: "▣ ",
}

# combo index -> separator between concatenated values
_SEPARATORS = (" | ", ", ", "; ")


class SpatialJoinDialog(BaseDialog):

    def __init__(self, iface=None, parent=None):
        super().__init__(iface, parent)
        self.setWindowTitle(self.tr("Spatial Join"))
        self.setMinimumSize(540, 620)
        self._setup_ui()

    # --- ui ---

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self.target_group, self.target_combo, self.selected_only = \
            self.create_layer_group(self.tr("Target layer (polygons)"),
                                    QgsMapLayerProxyModel.Filter.PolygonLayer)
        layout.addWidget(self.target_group)

        sources_group = QGroupBox(self.tr("Source layers"))
        sources_layout = QVBoxLayout()
        sources_layout.addWidget(QLabel(self.tr(
            "Ctrl+click to pick several (point, line or polygon):")))
        self.sources_list = QListWidget()
        self.sources_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.sources_list.setMaximumHeight(110)
        self.sources_list.itemSelectionChanged.connect(self._rebuild_columns)
        sources_layout.addWidget(self.sources_list)
        source_buttons, _src_all, _src_none = _ui_helpers.make_select_row(
            self.sources_list.selectAll, self.sources_list.clearSelection)
        sources_layout.addLayout(source_buttons)
        sources_group.setLayout(sources_layout)
        layout.addWidget(sources_group)
        self.sources_group = sources_group

        columns_group = QGroupBox(self.tr("Columns to bring over"))
        columns_layout = QVBoxLayout()
        columns_layout.addWidget(QLabel(self.tr(
            "From the selected sources, Ctrl+click for several:")))
        self.columns_list = QListWidget()
        self.columns_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.columns_list.setMaximumHeight(140)
        columns_layout.addWidget(self.columns_list)
        column_buttons, _col_all, _col_none = _ui_helpers.make_select_row(
            self.columns_list.selectAll, self.columns_list.clearSelection)
        columns_layout.addLayout(column_buttons)
        columns_group.setLayout(columns_layout)
        layout.addWidget(columns_group)
        self.columns_group = columns_group

        options_group = QGroupBox(self.tr("Options"))
        options_form = QFormLayout()
        self.multi_combo = QComboBox()
        self.multi_combo.addItem(self.tr("Concatenate them with the separator"))
        self.multi_combo.addItem(self.tr("Duplicate the feature, one copy per value"))
        options_form.addRow(self.tr("When several values match:"),
                            self.multi_combo)
        self.separator_combo = QComboBox()
        self.separator_combo.addItems(["|", ",", ";"])
        options_form.addRow(self.tr("Separator:"), self.separator_combo)
        self.dedup_check = QCheckBox(
            self.tr("Include repeated values only once"))
        self.dedup_check.setChecked(True)
        options_form.addRow(self.dedup_check)
        self.id_field_combo = QComboBox()
        self.id_field_combo.setToolTip(self.tr(
            "Written to the join_id provenance column; duplicated\n"
            "features get a /1, /2 suffix"))
        options_form.addRow(self.tr("Feature ID field:"), self.id_field_combo)
        options_group.setLayout(options_form)
        layout.addWidget(options_group)
        self.options_group = options_group

        self.progress_bar = self.create_progress_bar()
        layout.addWidget(self.progress_bar)
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.run_btn = QPushButton(self.tr("Run join"))
        self.run_btn.setDefault(True)
        self.run_btn.clicked.connect(self._run)
        buttons.addWidget(self.run_btn)
        self.close_btn = QPushButton(self.tr("Close"))
        self.close_btn.clicked.connect(self.reject)
        buttons.addWidget(self.close_btn)
        layout.addLayout(buttons)

        self._populate_sources()
        self.target_combo.layerChanged.connect(self._update_id_field)
        self._update_id_field()

        self.remember("multi_values", self.multi_combo)
        self.remember("separator", self.separator_combo)
        self.remember("dedup", self.dedup_check)
        self.restore_remembered()

    def _populate_sources(self):
        self.sources_list.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if not isinstance(layer, QgsVectorLayer):
                continue
            mark = _GEOM_MARKS.get(layer.geometryType())
            if mark is None:
                continue  # geometry-less tables can't join spatially
            item = QListWidgetItem(f"{mark}{layer.name()}")
            item.setData(Qt.ItemDataRole.UserRole, layer.id())
            self.sources_list.addItem(item)

    def _update_id_field(self):
        self.id_field_combo.clear()
        layer = self.target_combo.currentLayer()
        if not isinstance(layer, QgsVectorLayer):
            return
        names = [field.name() for field in layer.fields()]
        self.id_field_combo.addItems(names)
        # default to the provider's primary key when it has one
        primary = layer.primaryKeyAttributes()
        if primary and 0 <= primary[0] < len(names):
            self.id_field_combo.setCurrentIndex(primary[0])

    def _rebuild_columns(self):
        self.columns_list.clear()
        sources = self._get_sources()
        multiple = len(sources) > 1
        for source in sources:
            for field in source.fields():
                if field.name().lower() in ("fid", "geometry", "geom"):
                    continue
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
        result = []
        for item in self.sources_list.selectedItems():
            layer = QgsProject.instance().mapLayer(item.data(Qt.ItemDataRole.UserRole))
            if layer:
                result.append(layer)
        return result

    def _selected_columns(self):
        return [item.data(Qt.ItemDataRole.UserRole)
                for item in self.columns_list.selectedItems()
                if item.data(Qt.ItemDataRole.UserRole)]

    # --- validation ---

    def _validate(self):
        target = self.target_combo.currentLayer()
        sources = self._get_sources()
        if not self.validate_layer(target, self.tr("target layer")):
            return False
        if not sources:
            self.show_tool_warning(
                self.tr("Select at least one source layer."))
            return False
        if not self._selected_columns():
            self.show_tool_warning(
                self.tr("Select at least one column to bring over."))
            return False
        crs_ids = ({target.crs().authid()}
                   | {source.crs().authid() for source in sources})
        if len(crs_ids) > 1:
            if not self.confirm_action(
                    self.tr("Different CRS"),
                    self.tr("The layers use different coordinate "
                            "systems:\n{0}\n\nSources will be reprojected "
                            "to the target CRS on the fly. "
                            "Continue?").format(", ".join(sorted(crs_ids)))):
                return False
        return True

    # --- invalid geometry handling ---

    def _find_invalid(self, target, sources):
        """{layer_id: (layer_name, [feature_id, ...])} for bad geometries. Keyed by id because layer names are not unique in QGIS and two layers sharing one would pool their feature ids."""
        scans = [(target, self._target_features(target))]
        scans += [(source, source.getFeatures()) for source in sources]
        problems = {}
        for layer, features in scans:
            bad = []
            for feature in features:
                geometry = feature.geometry()
                if (geometry is None or geometry.isNull()
                        or not geometry.isGeosValid()):
                    bad.append(feature.id())
            if not bad:
                continue
            # a layer used as both target and source gets scanned twice
            known = problems.setdefault(layer.id(), (layer.name(), set()))
            known[1].update(bad)
        return {layer_id: (name, sorted(ids))
                for layer_id, (name, ids) in problems.items()}

    def _target_features(self, target):
        if self.selected_only.isChecked():
            return target.getSelectedFeatures()
        return target.getFeatures()

    def _ask_fix(self, problems):
        """True = repair, False = continue as-is, None = cancel."""
        total = sum(len(ids) for _, ids in problems.values())
        lines = []
        for name, ids in problems.values():
            preview = ", ".join(str(i) for i in ids[:8])
            if len(ids) > 8:
                preview += self.tr(" ... (+{0} more)").format(len(ids) - 8)
            lines.append(f"- {name}: {len(ids)} [ID: {preview}]")

        box = QMessageBox(self)
        box.setWindowTitle(self.tr("Invalid geometries"))
        box.setIcon(QMessageBox.Icon.Warning)
        if total == 1:
            box.setText(self.tr("1 invalid geometry was found."))
        else:
            box.setText(self.tr(
                "{0} invalid geometries were found.").format(total))
        box.setInformativeText(
            "\n".join(lines) + "\n\n" + self.tr(
                "Invalid geometries can produce wrong results.\n"
                "Repair them automatically for this run?\n"
                "(The original layers are not modified.)"))
        fix_btn = box.addButton(self.tr("Repair and continue"),
                                QMessageBox.ButtonRole.AcceptRole)
        box.addButton(self.tr("Continue anyway"),
                      QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = box.addButton(self.tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(fix_btn)
        box.exec()

        clicked = box.clickedButton()
        if clicked == fix_btn:
            return True
        if clicked == cancel_btn:
            return None
        return False

    # --- run ---

    def _set_busy(self, busy):
        for button in (self.run_btn, self.close_btn):
            button.setEnabled(not busy)
        # the run pumps events but works on a snapshot of these inputs - left enabled, they could still be changed mid-run with no effect
        for group in (self.target_group, self.sources_group,
                      self.columns_group, self.options_group):
            group.setEnabled(not busy)
        self.run_btn.setText(
            self.tr("Working...") if busy else self.tr("Run join"))

    def _run(self):
        if not self._validate():
            return

        target = self.target_combo.currentLayer()
        sources = self._get_sources()
        columns = self._selected_columns()
        self.save_remembered()

        # the scan walks every feature of every layer, so raise the wait state before it starts and drop it again for the question
        self._set_busy(True)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.status_label.setText(self.tr("Checking the geometries..."))
        self._processing = True
        try:
            QApplication.processEvents()
            problems = self._find_invalid(target, sources)
        finally:
            self._processing = False
            QApplication.restoreOverrideCursor()
            self.status_label.setText("")
            self._set_busy(False)

        do_fix = False
        if problems:
            answer = self._ask_fix(problems)
            if answer is None:
                return
            do_fix = answer

        self._set_busy(True)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.status_label.setText(self.tr("Building the spatial indexes..."))
        # progress pumps events, block Escape and the window X until the run ends
        self._processing = True

        try:
            result, stats = self._perform_join(
                target, sources, columns, do_fix, problems)
            QgsProject.instance().addMapLayer(result)

            lines = [
                self.tr("Input polygons: {0}").format(stats["total"]),
                self.tr("With matches: {0}").format(stats["matched"]),
                self.tr("Without matches: {0}").format(stats["no_match"]),
                self.tr("Duplicated per value: {0}").format(
                    stats["duplicated"]),
                self.tr("With combined values: {0}").format(
                    stats["combined"]),
            ]
            if stats["fixed"]:
                lines.append(self.tr("Repaired geometries: ") + ", ".join(
                    f"{name}: {count}" for name, count in stats["fixed"]))
            self.show_layer_created(result, "\n".join(lines))
            self._processing = False
            self.accept()
        except Exception as e:
            self.show_tool_failure(e)
        finally:
            self._processing = False
            QApplication.restoreOverrideCursor()
            self.progress_bar.setVisible(False)
            self.status_label.setText("")
            self._set_busy(False)

    # --- join logic ---

    def _perform_join(self, target, sources, selected_columns, do_fix,
                      problems):
        dedup = self.dedup_check.isChecked()
        separator = _SEPARATORS[self.separator_combo.currentIndex()]
        explode = self.multi_combo.currentIndex() == 1
        id_field = self.id_field_combo.currentText() or None
        problem_ids = set(problems)

        out_fields = QgsFields()
        existing = []
        for field in target.fields():
            out_fields.append(QgsField(field))
            existing.append(field.name())

        # joined values become string fields named <layer>_<field>, and this insertion order matches the appends down in the feature loop
        new_field_map = {}
        for layer_id, field_name in selected_columns:
            layer = QgsProject.instance().mapLayer(layer_id)
            if layer is None:
                continue
            short = layer.name()[:8].replace(" ", "_")
            out_name = f"{short}_{field_name}"[:20]
            base, i = out_name, 1
            while out_name in existing:
                out_name = f"{base[:17]}_{i}"
                i += 1
            existing.append(out_name)
            new_field_map[(layer_id, field_name)] = out_name
            out_fields.append(QgsField(out_name, FIELD_STRING))

        out_fields.append(QgsField("join_id", FIELD_STRING))
        out_fields.append(QgsField("join_source", FIELD_STRING))
        out_fields.append(QgsField("join_count", FIELD_INT))

        target_crs = target.crs()
        indexes = {}
        features_by_source = {}
        # pairs rather than a dict, two layers can share a name and their repair counts have to stay apart
        fixed_counts = []

        for source in sources:
            index = QgsSpatialIndex()
            feats = {}
            fixed = 0
            is_polygon = (source.geometryType()
                          == QgsWkbTypes.GeometryType.PolygonGeometry)
            transform = None
            source_crs = source.crs()
            if (source_crs.isValid() and target_crs.isValid()
                    and source_crs != target_crs):
                transform = QgsCoordinateTransform(
                    source_crs, target_crs, QgsProject.instance())

            for feature in source.getFeatures():
                geometry = feature.geometry()
                if do_fix and source.id() in problem_ids:
                    if (geometry is not None and not geometry.isNull()
                            and not geometry.isGeosValid()):
                        geometry = geometry.makeValid()
                        # only count it as repaired if it came back usable, a null one was never touched
                        if (geometry is not None and not geometry.isNull()
                                and not geometry.isEmpty()):
                            fixed += 1
                if (transform is not None and geometry is not None
                        and not geometry.isNull()):
                    try:
                        geometry.transform(transform)
                    except QgsCsException:
                        continue
                # polygon sources match on an interior point so touching boundaries aren't matches
                if (is_polygon and geometry is not None
                        and not geometry.isNull()):
                    geometry = geometry.pointOnSurface()
                # makeValid()/pointOnSurface() can hand back null or empty, and that can't be indexed or matched
                if (geometry is None or geometry.isNull()
                        or geometry.isEmpty()):
                    continue
                copy = QgsFeature(feature)
                copy.setGeometry(geometry)
                index.addFeature(copy)
                feats[feature.id()] = copy

            indexes[source.id()] = index
            features_by_source[source.id()] = feats
            if fixed:
                fixed_counts.append((source.name(), fixed))

        target_fixed = {}
        target_repaired = 0
        if do_fix and target.id() in problem_ids:
            for feature in self._target_features(target):
                geometry = feature.geometry()
                if (geometry is None or geometry.isNull()
                        or geometry.isGeosValid()):
                    continue
                geometry = geometry.makeValid()
                copy = QgsFeature(feature)
                copy.setGeometry(geometry)
                target_fixed[feature.id()] = copy
                if (geometry is not None and not geometry.isNull()
                        and not geometry.isEmpty()):
                    target_repaired += 1
            if target_repaired:
                fixed_counts.append((target.name(), target_repaired))

        selected_only = self.selected_only.isChecked()
        if selected_only:
            total = target.selectedFeatureCount()
            feature_iter = target.getSelectedFeatures()
        else:
            total = target.featureCount()
            feature_iter = target.getFeatures()

        stats = {"total": total, "matched": 0, "no_match": 0,
                 "duplicated": 0, "combined": 0, "fixed": fixed_counts}
        out_features = []

        self.progress_bar.setValue(10)
        self.status_label.setText(
            self.tr("Processing {0} polygons...").format(total))

        for i, feature in enumerate(feature_iter):
            self.progress_bar.setValue(10 + int(i / max(total, 1) * 85))
            if i % 50 == 0:
                QApplication.processEvents()

            if feature.id() in target_fixed:
                feature = target_fixed[feature.id()]
            geometry = feature.geometry()
            # a NULL in the chosen ID field would stringify to "NULL" in the provenance column, use the internal id instead
            raw_id = feature[id_field] if id_field else None
            feature_id = (raw_id
                          if id_field and not join_service.is_missing(raw_id)
                          else feature.id())

            # null/empty target geometry matches nothing, it falls through to the no-match branch and is still written out
            matched = []
            if (geometry is not None and not geometry.isNull()
                    and not geometry.isEmpty()):
                for source in sources:
                    index = indexes[source.id()]
                    feats = features_by_source[source.id()]
                    for fid in index.intersects(geometry.boundingBox()):
                        candidate = feats[fid]
                        try:
                            if geometry.intersects(candidate.geometry()):
                                matched.append(
                                    (source.id(), source.name(), candidate))
                        except Exception:  # nosec B110
                            pass  # still-invalid geometry - GEOS raises per candidate, and one bad feature must not abort the join

            if not matched:
                stats["no_match"] += 1
                out = QgsFeature(out_fields)
                out.setGeometry(feature.geometry())
                attributes = list(feature.attributes())
                attributes.extend([None] * len(new_field_map))
                attributes += [str(feature_id), "", 0]
                out.setAttributes(attributes)
                out_features.append(out)
                continue

            stats["matched"] += 1
            values = defaultdict(list)
            source_names = set()
            for layer_id, layer_name, candidate in matched:
                source_names.add(layer_name)
                for (col_layer_id, col_name), out_name \
                        in new_field_map.items():
                    if col_layer_id != layer_id:
                        continue
                    value = candidate[col_name]
                    if (not join_service.is_missing(value)
                            and str(value).strip()):
                        values[out_name].append(str(value))
            if dedup:
                for name in values:
                    values[name] = join_service.dedupe_preserve_order(
                        values[name])

            max_values = max((len(v) for v in values.values()), default=1)
            source_text = separator.join(sorted(source_names))
            match_count = len(matched)

            if max_values <= 1 or not explode:
                out = QgsFeature(out_fields)
                out.setGeometry(feature.geometry())
                attributes = list(feature.attributes())
                for out_name in new_field_map.values():
                    row_values = values.get(out_name, [])
                    attributes.append(
                        separator.join(row_values) if row_values else None)
                attributes += [str(feature_id), source_text, match_count]
                out.setAttributes(attributes)
                out_features.append(out)
                if max_values > 1:
                    stats["combined"] += 1
            else:
                stats["duplicated"] += 1
                for row in range(max_values):
                    out = QgsFeature(out_fields)
                    out.setGeometry(feature.geometry())
                    attributes = list(feature.attributes())
                    for out_name in new_field_map.values():
                        row_values = values.get(out_name, [])
                        attributes.append(row_values[row]
                                          if row < len(row_values) else None)
                    attributes += [f"{feature_id}/{row + 1}",
                                   source_text, match_count]
                    out.setAttributes(attributes)
                    out_features.append(out)

        self.progress_bar.setValue(98)
        self.status_label.setText(self.tr("Creating the result layer..."))

        # {input}_{operation}, the same shape every other Vernier tool names its result with
        result = QgsMemoryProviderUtils.createMemoryLayer(
            f"{target.name()}_spatialjoin", out_fields, target.wkbType(),
            target.crs())
        result.startEditing()
        result.addFeatures(out_features)
        result.commitChanges()

        stats["output"] = len(out_features)
        return result, stats
