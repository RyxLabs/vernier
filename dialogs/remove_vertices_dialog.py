# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Remove Close Vertices - thin UI over tools/vertex_cleaner, marks every removed vertex on the canvas and can chain a native:snapgeometries pass."""

from qgis.PyQt.QtCore import Qt  # type: ignore
from qgis.PyQt.QtGui import QColor  # type: ignore
from qgis.PyQt.QtWidgets import (  # type: ignore
    QApplication, QCheckBox, QDoubleSpinBox, QFormLayout, QGroupBox, QLabel,
    QVBoxLayout,
)
from qgis.core import (  # type: ignore
    QgsCoordinateTransform, QgsCsException, QgsMapLayerProxyModel,
    QgsProject, QgsUnitTypes, QgsWkbTypes,
)
from qgis.gui import QgsFieldExpressionWidget, QgsVertexMarker  # type: ignore

from ..services import settings_service
from ..tools import vertex_cleaner
from .base_dialog import BaseDialog

# past this the markers just slow the canvas down without telling you anything new
_MAX_MARKERS = 2000


class RemoveVerticesDialog(BaseDialog):

    def __init__(self, iface=None, parent=None):
        super().__init__(iface, parent)
        self.setWindowTitle(self.tr("Remove Close Vertices"))
        self.setMinimumWidth(430)
        self._markers = []
        self._running = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        group, self.layer_combo, self.selected_only = self.create_layer_group(
            self.tr("Input layer"),
            filter_type=(QgsMapLayerProxyModel.PolygonLayer
                         | QgsMapLayerProxyModel.LineLayer))
        self.selected_only.setToolTip(self.tr(
            "Clean only the selected features; the rest are copied unchanged"))
        layout.addWidget(group)

        self.crs_warning = QLabel(self.tr(
            "Warning: this layer uses a geographic CRS, so the tolerances "
            "below are in degrees. Reproject to a projected CRS to work "
            "in meters."))
        self.crs_warning.setWordWrap(True)
        self.crs_warning.setStyleSheet(
            "color: #d9822b; font-weight: bold; padding: 4px;")
        self.crs_warning.setVisible(False)
        layout.addWidget(self.crs_warning)

        self.zm_warning = QLabel(self.tr(
            "Note: this layer carries Z/M values, which will be dropped - "
            "the cleaned result is 2D."))
        self.zm_warning.setWordWrap(True)
        self.zm_warning.setStyleSheet(
            "color: #d9822b; padding: 4px;")
        self.zm_warning.setVisible(False)
        layout.addWidget(self.zm_warning)

        params_group = QGroupBox(self.tr("Parameters"))
        form = QFormLayout()

        self.segment_spin = QDoubleSpinBox()
        self.segment_spin.setRange(0.0, 100000.0)
        self.segment_spin.setDecimals(6)
        self.segment_spin.setSingleStep(0.001)
        self.segment_spin.setValue(
            settings_service.get("vertex_cleaner/segment_tolerance"))
        self.segment_spin.setToolTip(self.tr(
            "Consecutive vertices closer than this are collapsed to one.\n"
            "The vertex shared with neighboring features survives, so\n"
            "common boundaries stay intact."))
        form.addRow(self.tr("Segment tolerance:"), self.segment_spin)

        self.dup_spin = QDoubleSpinBox()
        self.dup_spin.setRange(0.0, 100000.0)
        self.dup_spin.setDecimals(6)
        self.dup_spin.setSingleStep(0.001)
        self.dup_spin.setValue(
            settings_service.get("vertex_cleaner/dup_tolerance"))
        self.dup_spin.setToolTip(self.tr(
            "Final sweep: remaining duplicate nodes closer than this\n"
            "are merged. 0 disables the sweep."))
        form.addRow(self.tr("Duplicate node tolerance:"), self.dup_spin)

        self.skip_expression = QgsFieldExpressionWidget()
        self.skip_expression.setToolTip(self.tr(
            "Features matching this expression are copied unchanged.\n"
            "Leave empty to process every feature."))
        form.addRow(self.tr("Skip features matching:"), self.skip_expression)

        params_group.setLayout(form)
        layout.addWidget(params_group)

        snap_group = QGroupBox(self.tr("Snap pass"))
        snap_form = QFormLayout()
        self.snap_check = QCheckBox(self.tr(
            "Snap result geometries to each other after cleaning"))
        snap_form.addRow(self.snap_check)
        self.snap_spin = QDoubleSpinBox()
        self.snap_spin.setRange(0.0, 100000.0)
        self.snap_spin.setDecimals(6)
        self.snap_spin.setSingleStep(0.001)
        self.snap_spin.setValue(
            settings_service.get("vertex_cleaner/snap_tolerance"))
        self.snap_spin.setEnabled(False)
        self.snap_check.toggled.connect(self.snap_spin.setEnabled)
        snap_form.addRow(self.tr("Snap tolerance:"), self.snap_spin)
        snap_group.setLayout(snap_form)
        layout.addWidget(snap_group)

        self.progress_bar = self.create_progress_bar()
        layout.addWidget(self.progress_bar)

        button_row, run_btn, close_btn = self.create_button_row(self.tr("Run"))
        self._buttons = [run_btn, close_btn]
        layout.addLayout(button_row)

        self.layer_combo.layerChanged.connect(self._update_layer_info)
        self._update_layer_info()

        self.remember("snap_pass", self.snap_check)
        self.restore_remembered()

    def _update_layer_info(self):
        layer = self.layer_combo.currentLayer()
        self.skip_expression.setLayer(layer)
        suffix = ""
        geographic = False
        has_zm = False
        if layer is not None and layer.crs().isValid():
            suffix = " " + QgsUnitTypes.toAbbreviatedString(
                layer.crs().mapUnits())
            geographic = layer.crs().isGeographic()
        if layer is not None:
            has_zm = (QgsWkbTypes.hasZ(layer.wkbType())
                      or QgsWkbTypes.hasM(layer.wkbType()))
        for spin in (self.segment_spin, self.dup_spin, self.snap_spin):
            spin.setSuffix(suffix)
        self.crs_warning.setVisible(geographic)
        self.zm_warning.setVisible(has_zm)

    def accept(self):
        """Run the cleaner, and stay open on purpose - the markers stay visible and the tolerance can be tuned between runs. Close or Escape clears them."""
        if self._running:
            return
        layer = self.layer_combo.currentLayer()
        if not self.validate_layer(layer, self.tr("input layer"),
                                   check_features=True):
            return
        if QgsWkbTypes.isCurvedType(layer.wkbType()):
            self.show_tool_warning(self.tr(
                "This layer stores curved geometries (arcs). Cleaning "
                "would silently replace every arc with straight segments "
                "- convert the layer to straight segments first if that "
                "is what you want."))
            return
        tolerance = self.segment_spin.value()
        if tolerance <= 0:
            self.show_tool_warning(
                self.tr("Segment tolerance must be above 0."))
            return
        expression = (self.skip_expression.expression() or "").strip()
        if expression and not self.skip_expression.isValidExpression():
            self.show_tool_warning(
                self.tr("The skip expression could not be parsed. "
                        "Check the field names and syntax."))
            return

        self._store_tolerances()
        self.save_remembered()

        only_fids = None
        if self.selected_only.isChecked():
            only_fids = set(layer.selectedFeatureIds())

        self._clear_markers()
        # the progress callback pumps the event loop, so freeze the dialog for the run - no second Run, no Close mid-run
        self._running = True
        for button in self._buttons:
            button.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            try:
                result = vertex_cleaner.clean_layer(
                    layer, tolerance,
                    dup_tolerance=self.dup_spin.value(),
                    skip_expression=expression,
                    only_fids=only_fids,
                    progress=self._on_progress)
            except ValueError as e:
                self.show_tool_error(
                    self.tr("The skip expression failed: {0}").format(e))
                return
            except Exception as e:
                self.show_tool_failure(e)
                return

            final = result.layer
            if self.snap_check.isChecked():
                try:
                    snapped = self.run_processing("native:snapgeometries", {
                        "INPUT": result.layer,
                        "REFERENCE_LAYER": result.layer,
                        "TOLERANCE": self.snap_spin.value(),
                        # 7 = snap to anchor nodes (single-layer mode)
                        "BEHAVIOR": 7,
                        "OUTPUT": "memory:",
                    }, self.progress_bar)
                except Exception as e:
                    self.show_tool_failure(e)
                    return
                final = snapped["OUTPUT"]
                final.setCrs(layer.crs())

            final.setName(f"{layer.name()}_cleaned")
            QgsProject.instance().addMapLayer(final)
            self._place_markers(layer, result.removed_points)

            details = self.tr("Vertices removed: {0}").format(
                result.removed_count)
            if result.skipped_count:
                details += "\n" + self.tr(
                    "Features skipped by the expression: {0}").format(
                        result.skipped_count)
            self.show_layer_created(final, details)
        finally:
            QApplication.restoreOverrideCursor()
            self.progress_bar.setVisible(False)
            self._running = False
            for button in self._buttons:
                button.setEnabled(True)

    def _store_tolerances(self):
        """The tool dialog and Settings > Vertex cleanup edit the same three values, so a run writes back what it used and both screens keep agreeing."""
        settings_service.set_("vertex_cleaner/segment_tolerance",
                              self.segment_spin.value())
        settings_service.set_("vertex_cleaner/dup_tolerance",
                              self.dup_spin.value())
        settings_service.set_("vertex_cleaner/snap_tolerance",
                              self.snap_spin.value())

    def _on_progress(self, value):
        self.progress_bar.setValue(value)
        QApplication.processEvents()

    # --- canvas markers ---

    def _place_markers(self, layer, points):
        canvas = self.iface.mapCanvas() if self.iface else None
        if canvas is None or not points:
            return
        transform = QgsCoordinateTransform(
            layer.crs(), canvas.mapSettings().destinationCrs(),
            QgsProject.instance())
        for point in points[:_MAX_MARKERS]:
            try:
                center = transform.transform(point)
            except QgsCsException:
                continue
            marker = QgsVertexMarker(canvas)
            marker.setCenter(center)
            # amber, same accent as the warnings here and not what QGIS uses for its own editing markers
            marker.setColor(QColor("#d9822b"))
            marker.setIconType(QgsVertexMarker.ICON_CIRCLE)
            marker.setIconSize(8)
            marker.setPenWidth(2)
            self._markers.append(marker)

    def _clear_markers(self):
        if not self._markers:
            return
        canvas = self.iface.mapCanvas() if self.iface else None
        for marker in self._markers:
            if canvas is not None:
                canvas.scene().removeItem(marker)
        self._markers.clear()
        if canvas is not None:
            canvas.refresh()

    def reject(self):
        if self._running:
            return
        self._clear_markers()
        super().reject()

    def closeEvent(self, event):
        if self._running:
            event.ignore()
            return
        self._clear_markers()
        super().closeEvent(event)
