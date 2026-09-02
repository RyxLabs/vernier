# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Topology Validator - runs the five topology_service checks and lists the findings in a tree whose rows zoom and highlight, plus a styled memory error layer for every finding class."""

# owns canvas rubber bands, so the plugin has to call cleanup() from unload()

from qgis.PyQt.QtCore import Qt  # type: ignore
from qgis.PyQt.QtGui import QColor  # type: ignore
from qgis.PyQt.QtWidgets import (  # type: ignore
    QApplication, QCheckBox, QDockWidget, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QProgressBar, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)
from qgis.core import (  # type: ignore
    Qgis, QgsCategorizedSymbolRenderer, QgsCoordinateTransform,
    QgsCsException, QgsFeature, QgsFeatureRequest, QgsField, QgsFillSymbol,
    QgsGeometry, QgsMapLayerProxyModel, QgsMarkerSymbol, QgsMessageLog,
    QgsProject,
    QgsRectangle, QgsRendererCategory, QgsSingleSymbolRenderer, QgsUnitTypes,
    QgsVectorLayer, QgsWkbTypes,
)
from qgis.gui import QgsMapLayerComboBox, QgsRubberBand  # type: ignore

from .qt_compat import (
    DELETE_FEATURES, FIELD_DOUBLE, FIELD_LONGLONG, FIELD_STRING,
)
from .dialogs import _ui_helpers
from .i18n import tr as _tr
from .services import error_styles, settings_service, topology_service

# (key, label, polygon_only) in display and run order, labels translated at consumption
_CHECKS = (
    ("validity", "Invalid geometries", False),
    ("duplicates", "Duplicate geometries", False),
    ("overlaps", "Overlaps", True),
    ("gaps", "Gaps", True),
    ("vertex", "Vertex errors", True),
)

# marker so a later run, in any UI locale, can find and replace our layers without touching a user layer that happens to share the name
_ERROR_LAYER_FLAG = "vernier/topology_error"

# subtype -> (color, label) for the vertex error categories
_POINT_CATEGORIES = (
    (topology_service.VERTEX_DUPLICATE_POINT, "#d90429", "Duplicate point"),
    (topology_service.VERTEX_CLOSE_VERTICES, "#f77f00", "Close vertices"),
    (topology_service.VERTEX_SHORT_SEGMENT, "#fcbf49", "Short segment"),
)


def _discard_previous_results():
    project = QgsProject.instance()
    stale = [layer.id() for layer in project.mapLayers().values()
             if layer.customProperty(_ERROR_LAYER_FLAG)]
    if stale:
        project.removeMapLayers(stale)


def _polygon_renderer(fill_rgba, outline):
    return QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({
        "color": fill_rgba,
        "outline_color": outline,
        "outline_width": "0.66",
    }))


def _vertex_renderer():
    categories = []
    for subtype, color, label in _POINT_CATEGORIES:
        marker = QgsMarkerSymbol.createSimple({
            "name": "square",
            "color": color,
            "size": "2.8",
            "outline_color": "#003049",
            "outline_width": "0.3",
        })
        categories.append(QgsRendererCategory(subtype, marker, _tr(label)))
    return QgsCategorizedSymbolRenderer("issue", categories)


def _invalid_rows(errors):
    """(point, attributes) per invalid feature, as (complaint, feature id). The check_validity report is one error per feature, so no grouping is needed."""
    rows = []
    for error in errors:
        if error.location is None or error.location.isNull():
            continue
        rows.append((QgsGeometry(error.location),
                     [error.subtype or error.description,
                      error.feature_ids[0]]))
    return rows


def _duplicate_rows(errors):
    """(geometry, attributes) per duplicate group, as (kind, keeper id, copies). check_duplicates reports a group of n as n-1 pairs that all name the same keeper, so grouping on that id rebuilds the group. One row per group rather than per feature is deliberate: every copy in a group shares one footprint, so a row each would overprint the same translucent fill and shade a big group darker than a small one - the count goes in an attribute instead of into the ink."""
    groups = {}
    order = []
    for error in errors:
        keeper = error.feature_ids[0]
        if keeper not in groups:
            groups[keeper] = (error.conflict, set())
            order.append(keeper)
        groups[keeper][1].add(error.feature_ids[1])
    rows = []
    for keeper in order:
        geometry, others = groups[keeper]
        carrier = QgsGeometry(geometry)
        carrier.convertToMultiType()
        rows.append((carrier, [topology_service.KIND_DUPLICATE, keeper,
                               len(others) + 1]))
    return rows


def _duplicate_shape(geometry):
    """Memory layer type string for the duplicates layer. Multi, so a single and a multi part of the same family both land - unlike the other checks this one also runs on point and line layers."""
    return QgsWkbTypes.displayString(QgsWkbTypes.multiType(geometry.wkbType()))


def _publish_error_layer(crs, shape, name, schema, rows, renderer):
    """One styled memory error layer at the top of the layer tree, rows as (geometry, attributes) pairs. None when the layer could not be created."""
    layer = QgsVectorLayer(shape, name, "memory")
    if not layer.isValid():
        QgsMessageLog.logMessage(
            f"Could not create the error layer '{name}'.",
            "Vernier", level=Qgis.MessageLevel.Warning)
        return None
    layer.setCrs(crs)
    layer.setCustomProperty(_ERROR_LAYER_FLAG, True)
    provider = layer.dataProvider()
    provider.addAttributes(schema)
    layer.updateFields()
    carriers = []
    for geometry, attributes in rows:
        carrier = QgsFeature(layer.fields())
        carrier.setGeometry(geometry)
        carrier.setAttributes(attributes)
        carriers.append(carrier)
    provider.addFeatures(carriers)
    layer.updateExtents()
    layer.setRenderer(renderer)
    # top of the tree, or the overlay ends up buried under the data it marks
    project = QgsProject.instance()
    project.addMapLayer(layer, False)
    project.layerTreeRoot().insertLayer(0, layer)
    return layer


def build_error_layers(errors, crs):
    """Publish the error classes as styled memory layers, replacing whatever the previous run left. Returns what it made. Every class publishes: the derived ones (invalid locations, overlap slivers, gaps, vertex points) carry geometry you cannot see in the source layer, and duplicates carry the footprint itself because there is nothing else to show."""
    _discard_previous_results()
    published = []

    invalid = [e for e in errors
               if e.kind == topology_service.KIND_INVALID]
    if invalid:
        rows = _invalid_rows(invalid)
        if rows:
            published.append(_publish_error_layer(
                crs, "Point", _tr("Topology invalid geometries"),
                [QgsField("issue", FIELD_STRING),
                 QgsField("feature", FIELD_LONGLONG)],
                rows,
                error_styles.invalid_renderer()))

    duplicates = [e for e in errors
                  if e.kind == topology_service.KIND_DUPLICATE]
    if duplicates:
        rows = _duplicate_rows(duplicates)
        if rows:
            published.append(_publish_error_layer(
                crs, _duplicate_shape(rows[0][0]),
                _tr("Topology duplicates"),
                [QgsField("issue", FIELD_STRING),
                 QgsField("keeper", FIELD_LONGLONG),
                 QgsField("copies", FIELD_LONGLONG)],
                rows,
                error_styles.duplicate_renderer(rows[0][0].type())))

    overlaps = [e for e in errors
                if e.kind == topology_service.KIND_OVERLAP]
    if overlaps:
        published.append(_publish_error_layer(
            crs, "Polygon", _tr("Topology overlaps"),
            [QgsField("issue", FIELD_STRING),
             QgsField("feature_a", FIELD_LONGLONG),
             QgsField("feature_b", FIELD_LONGLONG),
             QgsField("area", FIELD_DOUBLE)],
            [(e.conflict,
              [e.kind, e.feature_ids[0], e.feature_ids[1], e.value])
             for e in overlaps],
            _polygon_renderer("216,17,89,110", "#a30d43")))

    gaps = [e for e in errors if e.kind == topology_service.KIND_GAP]
    if gaps:
        published.append(_publish_error_layer(
            crs, "Polygon", _tr("Topology gaps"),
            [QgsField("issue", FIELD_STRING),
             QgsField("area", FIELD_DOUBLE)],
            [(e.conflict, [e.kind, e.value]) for e in gaps],
            _polygon_renderer("2,136,209,110", "#01579b")))

    vertex = [e for e in errors if e.kind == topology_service.KIND_VERTEX]
    if vertex:
        # "issue" carries the subtype here, not the kind - the categorized renderer keys on it
        published.append(_publish_error_layer(
            crs, "Point", _tr("Topology vertex errors"),
            [QgsField("issue", FIELD_STRING),
             QgsField("feature", FIELD_LONGLONG),
             QgsField("length", FIELD_DOUBLE),
             QgsField("note", FIELD_STRING)],
            [(e.conflict,
              [e.subtype, e.feature_ids[0], e.value, e.description])
             for e in vertex],
            _vertex_renderer()))

    return [layer for layer in published if layer is not None]


class TopologyPanel(QDockWidget):
    """Built on first use, cleanup() comes from unload()."""

    def tr(self, text: str) -> str:
        # one "Vernier" context for the whole plugin, see i18n.py. QObject.tr would use the class name instead and these strings would land outside it
        return _tr(text)

    def __init__(self, iface, parent=None):
        super().__init__(parent or iface.mainWindow())
        self.iface = iface
        self.setWindowTitle(self.tr("Topology Validator"))
        self.setObjectName("VernierTopologyPanel")

        self._errors = []
        self._layer = None
        self._running = False

        canvas = iface.mapCanvas()
        # conflict band matches the overlap layer's crimson, the features involved get a muted slate so they read as context rather than error
        self._rb_conflict = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.PolygonGeometry)
        self._rb_conflict.setFillColor(QColor(216, 17, 89, 90))
        self._rb_conflict.setStrokeColor(QColor(216, 17, 89, 255))
        self._rb_conflict.setWidth(3)
        self._rb_conflict.setIcon(QgsRubberBand.IconType.ICON_CIRCLE)
        self._rb_conflict.setIconSize(12)
        self._rb_features = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.PolygonGeometry)
        self._rb_features.setFillColor(QColor(69, 123, 157, 55))
        self._rb_features.setStrokeColor(QColor(69, 123, 157, 170))
        self._rb_features.setWidth(3)

        self._setup_ui()

    def _setup_ui(self):
        content = QWidget()
        layout = QVBoxLayout(content)

        layer_row = QHBoxLayout()
        layer_row.addWidget(QLabel(self.tr("Layer:")))
        self.layer_combo = QgsMapLayerComboBox()
        self.layer_combo.setFilters(QgsMapLayerProxyModel.Filter.VectorLayer)
        layer_row.addWidget(self.layer_combo, 1)
        layout.addLayout(layer_row)

        self.crs_warning = QLabel(self.tr(
            "Warning: this layer uses a geographic CRS, so the tolerances "
            "below are in degrees. Reproject to a projected CRS to work "
            "in meters."))
        self.crs_warning.setWordWrap(True)
        self.crs_warning.setStyleSheet(
            "color: #d9822b; font-weight: bold; padding: 4px;")
        self.crs_warning.setVisible(False)
        layout.addWidget(self.crs_warning)

        checks_group = QGroupBox(self.tr("Checks"))
        checks_layout = QVBoxLayout()
        self.check_boxes = {}
        for key, label, _polygon_only in _CHECKS:
            box = QCheckBox(self.tr(label))
            box.setChecked(True)
            self.check_boxes[key] = box
            checks_layout.addWidget(box)
        select_row, _all_btn, _none_btn = _ui_helpers.make_select_row(
            lambda: self._set_all_checks(True),
            lambda: self._set_all_checks(False))
        checks_layout.addLayout(select_row)

        self.delete_duplicates_box = QCheckBox(self.tr(
            "Delete duplicate geometries after the run (keeps one per group)"))
        self.delete_duplicates_box.setChecked(False)
        self.delete_duplicates_box.setToolTip(self.tr(
            "Staged in the layer's edit buffer, not written to disk - "
            "review and save the layer, or press Ctrl+Z to undo."))
        duplicates_box = self.check_boxes["duplicates"]
        self.delete_duplicates_box.setEnabled(duplicates_box.isChecked())
        duplicates_box.toggled.connect(self.delete_duplicates_box.setEnabled)
        checks_layout.addWidget(self.delete_duplicates_box)

        checks_group.setLayout(checks_layout)
        layout.addWidget(checks_group)

        tolerances_group = QGroupBox(self.tr("Tolerances (layer units)"))
        form = QFormLayout()

        self.snap_spin = QDoubleSpinBox()
        self.snap_spin.setRange(0.0, 100000.0)
        self.snap_spin.setDecimals(6)
        self.snap_spin.setSingleStep(0.001)
        self.snap_spin.setValue(settings_service.get("topology/snap_tolerance"))
        self.snap_spin.setToolTip(self.tr(
            "Gap check: cracks narrower than this are closed by snapping\n"
            "before looking for gaps"))
        form.addRow(self.tr("Snap tolerance:"), self.snap_spin)

        self.gap_area_spin = QDoubleSpinBox()
        self.gap_area_spin.setRange(0.0, 1000000000.0)
        self.gap_area_spin.setDecimals(4)
        self.gap_area_spin.setSingleStep(0.01)
        self.gap_area_spin.setValue(
            settings_service.get("topology/gap_min_area"))
        self.gap_area_spin.setToolTip(self.tr(
            "Gaps smaller than this area are ignored"))
        form.addRow(self.tr("Minimum gap area:"), self.gap_area_spin)

        self.gap_buffer_spin = QDoubleSpinBox()
        self.gap_buffer_spin.setRange(0.0, 100000.0)
        self.gap_buffer_spin.setDecimals(6)
        self.gap_buffer_spin.setSingleStep(0.0001)
        self.gap_buffer_spin.setValue(
            settings_service.get("topology/gap_buffer"))
        self.gap_buffer_spin.setToolTip(self.tr(
            "Gap check: slivers narrower than about twice this value are\n"
            "treated as snapping residue and not reported"))
        form.addRow(self.tr("Sliver tolerance:"), self.gap_buffer_spin)

        self.vertex_spin = QDoubleSpinBox()
        self.vertex_spin.setRange(0.0, 100000.0)
        self.vertex_spin.setDecimals(6)
        self.vertex_spin.setSingleStep(0.001)
        self.vertex_spin.setValue(
            settings_service.get("topology/vertex_tolerance"))
        self.vertex_spin.setToolTip(self.tr(
            "Vertices closer than this are reported; segments shorter\n"
            "than 10x this are reported as short segments"))
        form.addRow(self.tr("Vertex tolerance:"), self.vertex_spin)

        tolerances_group.setLayout(form)
        layout.addWidget(tolerances_group)

        run_row = QHBoxLayout()
        self.run_button = QPushButton(self.tr("Run checks"))
        self.run_button.clicked.connect(self._run)
        run_row.addWidget(self.run_button)
        run_row.addStretch()
        layout.addLayout(run_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        layout.addWidget(self.progress_bar)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([
            self.tr("Check"), self.tr("Count"), self.tr("Description"),
        ])
        self.tree.setAlternatingRowColors(True)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tree.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.tree, 1)

        self.status_label = QLabel(self.tr("Not checked yet."))
        layout.addWidget(self.status_label)

        self.setWidget(content)

        self.layer_combo.layerChanged.connect(self._update_layer_info)
        self._preselect_active_layer()
        self._update_layer_info()

    # --- lifecycle ---

    def cleanup(self):
        """Take the rubber bands off the canvas. unload() calls this, and the panel is inert after it."""
        canvas = None
        try:
            canvas = self.iface.mapCanvas()
        except RuntimeError:
            pass
        for band in (self._rb_conflict, self._rb_features):
            if band is None:
                continue
            try:
                band.reset()
                if canvas is not None:
                    canvas.scene().removeItem(band)
            except RuntimeError:
                pass  # C++ object is gone
        self._rb_conflict = None
        self._rb_features = None
        self._errors = []
        self._layer = None

    def closeEvent(self, event):
        if self._running:
            event.ignore()
            return
        super().closeEvent(event)

    # --- layer selection ---

    def _preselect_active_layer(self):
        """Preselect the active layer, if it got past the combo's filter."""
        active = self.iface.activeLayer()
        if not isinstance(active, QgsVectorLayer):
            return
        for i in range(self.layer_combo.count()):
            layer = self.layer_combo.layer(i)
            if layer is not None and layer.id() == active.id():
                self.layer_combo.setLayer(active)
                return

    def _set_all_checks(self, checked):
        # disabled boxes stay put - _active_checks skips polygon-only ones on other layers anyway, but a ticked disabled box would look ready to run
        for box in self.check_boxes.values():
            if box.isEnabled():
                box.setChecked(checked)

    def _update_layer_info(self):
        layer = self.layer_combo.currentLayer()
        is_polygon = (isinstance(layer, QgsVectorLayer)
                      and layer.geometryType()
                      == QgsWkbTypes.GeometryType.PolygonGeometry)
        for key, _label, polygon_only in _CHECKS:
            if polygon_only:
                box = self.check_boxes[key]
                box.setEnabled(is_polygon)
                box.setToolTip(
                    "" if is_polygon else self.tr("Polygon layers only"))

        suffix = ""
        area_suffix = ""
        geographic = False
        if (isinstance(layer, QgsVectorLayer) and layer.isValid()
                and layer.crs().isValid()):
            units = layer.crs().mapUnits()
            suffix = " " + QgsUnitTypes.toAbbreviatedString(units)
            area_suffix = " " + QgsUnitTypes.toAbbreviatedString(
                QgsUnitTypes.distanceToAreaUnit(units))
            geographic = layer.crs().isGeographic()
        for spin in (self.snap_spin, self.gap_buffer_spin, self.vertex_spin):
            spin.setSuffix(suffix)
        self.gap_area_spin.setSuffix(area_suffix)
        self.crs_warning.setVisible(geographic)

    # --- running ---

    def _active_checks(self, layer):
        is_polygon = layer.geometryType() == QgsWkbTypes.GeometryType.PolygonGeometry
        return [(key, label) for key, label, polygon_only in _CHECKS
                if self.check_boxes[key].isChecked()
                and (is_polygon or not polygon_only)]

    def _run_check(self, key, layer, progress):
        if key == "validity":
            return topology_service.check_validity(layer, progress=progress)
        if key == "duplicates":
            return topology_service.check_duplicates(layer, progress=progress)
        if key == "overlaps":
            return topology_service.check_overlaps(layer, progress=progress)
        if key == "gaps":
            return topology_service.check_gaps(
                layer,
                snap_tolerance=self.snap_spin.value(),
                gap_min_area=self.gap_area_spin.value(),
                gap_buffer=self.gap_buffer_spin.value(),
                progress=progress)
        return topology_service.check_vertex_errors(
            layer, tolerance=self.vertex_spin.value(), progress=progress)

    def _run(self):
        # the progress callbacks pump the event loop, so a second click mid-run has to bounce
        if self._running:
            return
        layer = self.layer_combo.currentLayer()
        if (not isinstance(layer, QgsVectorLayer) or not layer.isValid()
                or layer.featureCount() == 0):
            self.iface.messageBar().pushMessage(
                self.tr("Topology Validator"),
                self.tr("Select a vector layer with features first."),
                level=Qgis.MessageLevel.Warning, duration=5)
            return
        checks = self._active_checks(layer)
        if not checks:
            self.iface.messageBar().pushMessage(
                self.tr("Topology Validator"),
                self.tr("Enable at least one check that applies to this "
                        "layer."),
                level=Qgis.MessageLevel.Warning, duration=5)
            return

        # keep the tolerances for next time
        settings_service.set_("topology/snap_tolerance",
                              self.snap_spin.value())
        settings_service.set_("topology/gap_min_area",
                              self.gap_area_spin.value())
        settings_service.set_("topology/gap_buffer",
                              self.gap_buffer_spin.value())
        settings_service.set_("topology/vertex_tolerance",
                              self.vertex_spin.value())

        self._clear_highlight()
        self._errors = []
        self._layer = layer
        self.tree.clear()

        self._running = True
        self.run_button.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            slice_size = 100.0 / len(checks)
            for position, (key, label) in enumerate(checks):
                base = position * slice_size

                def on_progress(percent, base=base):
                    self.progress_bar.setValue(
                        int(base + percent * slice_size / 100.0))
                    QApplication.processEvents()

                # the gap check's union phase reports no progress at all, so name the running check or a long wait reads as a freeze
                self.status_label.setText(
                    self.tr("Running: {0}").format(self.tr(label)))
                QApplication.processEvents()
                try:
                    errors = self._run_check(key, layer, on_progress)
                except Exception as e:  # one failing check shouldn't kill the run
                    QgsMessageLog.logMessage(
                        f"Topology check '{key}' failed: {e}", "Vernier",
                        level=Qgis.MessageLevel.Critical)
                    self._add_check_branch(label, None, str(e))
                    continue
                self._add_check_branch(label, errors, "")
                self._errors.extend(errors)
            self.progress_bar.setValue(100)

            build_error_layers(self._errors, layer.crs())
            self.iface.mapCanvas().refresh()

            # after publishing, so the map still shows what was found
            deleted = 0
            if (self.delete_duplicates_box.isChecked()
                    and self.check_boxes["duplicates"].isChecked()):
                deleted = self._delete_redundant_duplicates(layer)

            count = len(self._errors)
            if count == 0:
                self.status_label.setText(self.tr("No errors found."))
                self.iface.messageBar().pushMessage(
                    self.tr("Topology Validator"),
                    self.tr("No topology errors found."),
                    level=Qgis.MessageLevel.Success, duration=5)
            elif count == 1:
                self.status_label.setText(
                    self.tr("Found 1 error - click the row to zoom to it."))
            else:
                self.status_label.setText(
                    self.tr("Found {0} errors - click a row to zoom to "
                            "it.").format(count))

            if deleted:
                self.iface.messageBar().pushMessage(
                    self.tr("Topology Validator"),
                    self.tr("Deleted {0} duplicate features - review and save "
                            "the layer, or press Ctrl+Z to undo.")
                    .format(deleted),
                    level=Qgis.MessageLevel.Info, duration=8)
        finally:
            QApplication.restoreOverrideCursor()
            self.progress_bar.setVisible(False)
            self._running = False
            self.run_button.setEnabled(True)

    def _delete_redundant_duplicates(self, layer):
        """Stage the redundant copies in the layer's edit buffer, one keeper per group, and return how many went. Groups whose members disagree on attributes are reported and skipped rather than resolved by feature id. Nothing is committed - command_bar's toggle comment has the reasoning: a commitChanges() here would bake in whatever else the user had open and throw the undo stack away."""
        groups = topology_service.duplicate_id_groups(self._errors)
        if not groups:
            return 0
        wanted = [fid for group in groups for fid in group]
        attributes = {feature.id(): feature.attributes()
                      for feature in layer.getFeatures(
                          QgsFeatureRequest().setFilterFids(wanted))}
        ids, conflicted = topology_service.split_duplicate_groups(
            self._errors, attributes)
        if conflicted:
            self.iface.messageBar().pushMessage(
                self.tr("Topology Validator"),
                self.tr("{0} duplicate group(s) hold the same geometry with "
                        "different attributes and were left alone - the "
                        "copies are not interchangeable.").format(
                            len(conflicted)),
                level=Qgis.MessageLevel.Warning, duration=10)
        if not ids:
            return 0
        if not layer.dataProvider().capabilities() & DELETE_FEATURES:
            self.iface.messageBar().pushMessage(
                self.tr("Topology Validator"),
                self.tr("This layer's provider cannot delete features, so "
                        "the duplicates were left alone."),
                level=Qgis.MessageLevel.Warning, duration=7)
            return 0
        if not layer.isEditable() and not layer.startEditing():
            self.iface.messageBar().pushMessage(
                self.tr("Topology Validator"),
                self.tr("Could not open an edit session, so the duplicates "
                        "were left alone."),
                level=Qgis.MessageLevel.Warning, duration=7)
            return 0
        layer.beginEditCommand(self.tr("Delete duplicate geometries"))
        try:
            removed = layer.deleteFeatures(ids)
        except Exception:
            layer.destroyEditCommand()  # leave the buffer as we found it
            raise
        if not removed:
            layer.destroyEditCommand()
            return 0
        layer.endEditCommand()
        layer.triggerRepaint()
        return len(ids)

    def _add_check_branch(self, label, errors, failure):
        if errors is None:
            top = QTreeWidgetItem([
                self.tr(label), "-",
                self.tr("Check failed: {0}").format(failure)])
            self.tree.addTopLevelItem(top)
            return
        description = (self.tr("no errors") if not errors
                       else self.tr("{0} found").format(len(errors)))
        top = QTreeWidgetItem([self.tr(label), str(len(errors)), description])
        # this branch's errors get appended to self._errors right after, so their global indices start at the current length
        base = len(self._errors)
        for offset, error in enumerate(errors):
            child = QTreeWidgetItem(["", "", error.description])
            child.setData(0, Qt.ItemDataRole.UserRole, base + offset)
            top.addChild(child)
        self.tree.addTopLevelItem(top)
        top.setExpanded(bool(errors))

    # --- error navigation ---

    def _clear_highlight(self):
        for band in (self._rb_conflict, self._rb_features):
            if band is None:
                continue
            try:
                band.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
            except RuntimeError:
                pass

    def _on_item_clicked(self, item, column):
        index = item.data(0, Qt.ItemDataRole.UserRole)
        if index is None or self._rb_conflict is None:
            return
        if not 0 <= index < len(self._errors):
            return
        error = self._errors[index]
        conflict = error.conflict
        if conflict is None or conflict.isNull() or conflict.isEmpty():
            return

        layer = self._layer
        try:
            crs = layer.crs() if layer is not None else None
        except RuntimeError:
            self._layer = None  # C++ object deleted under us
            layer = None
            crs = None

        canvas = self.iface.mapCanvas()
        rect = QgsRectangle(conflict.boundingBox())
        if rect.width() == 0 and rect.height() == 0:
            # point errors get a close-up scaled off the vertex tolerance, so the zoom stays proportional to the layer units
            rect.grow(max(self.vertex_spin.value() * 200, 1e-6))
        else:
            rect.scale(1.5)
        if crs is not None and crs.isValid():
            try:
                rect = QgsCoordinateTransform(
                    crs, canvas.mapSettings().destinationCrs(),
                    QgsProject.instance()).transformBoundingBox(rect)
            except QgsCsException:
                return
        canvas.setExtent(rect)

        self._clear_highlight()
        self._rb_conflict.reset(conflict.type())
        self._rb_conflict.addGeometry(conflict, layer)
        # a duplicate's "features involved" ARE the conflict geometry, so drawing them would only wash the crimson out with slate and show nothing new
        context = [geometry for geometry in error.feature_geometries
                   if not geometry.isGeosEqual(conflict)]
        if context:
            self._rb_features.reset(context[0].type())
            for geometry in context:
                self._rb_features.addGeometry(geometry, layer)
        canvas.refresh()
        if crs is not None and crs.isValid():
            canvas.flashGeometries([conflict], crs)
