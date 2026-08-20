# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""CAD lines to polygons - runs qgis:linestopolygons once per value of the OGR DXF driver's "Layer" attribute, one output layer each."""

from collections import defaultdict

from qgis.PyQt.QtWidgets import (  # type: ignore
    QApplication, QCheckBox, QGroupBox, QHBoxLayout, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)
from qgis.core import (  # type: ignore
    Qgis, QgsFeature, QgsFeatureRequest, QgsMapLayerProxyModel,
    QgsProcessingFeedback, QgsProject, QgsVectorLayer, QgsWkbTypes,
)
from qgis.gui import QgsMapLayerComboBox  # type: ignore

from .base_dialog import BaseDialog


class ResponsiveFeedback(QgsProcessingFeedback):
    """Feedback that keeps the UI responsive during processing."""

    def setProgress(self, progress):
        super().setProgress(progress)
        QApplication.processEvents()


class LinesToPolygonsDialog(BaseDialog):

    def __init__(self, iface=None, parent=None):
        super().__init__(iface, parent)
        self.setWindowTitle(self.tr("CAD Lines to Polygons"))
        self.setMinimumWidth(380)
        self.setMinimumHeight(380)
        self._running = False
        self._setup_ui()

    def _setup_ui(self):
        main_layout = QVBoxLayout()

        layer_group = QGroupBox(self.tr("Line layer"))
        layer_layout = QVBoxLayout()
        self.layer_combo = QgsMapLayerComboBox()
        self.layer_combo.setFilters(QgsMapLayerProxyModel.LineLayer)
        self.layer_combo.setToolTip(
            self.tr("Choose the line layer to process"))
        layer_layout.addWidget(self.layer_combo)
        layer_group.setLayout(layer_layout)
        main_layout.addWidget(layer_group)

        self.preselect_active_layer(self.layer_combo)

        # one checkbox per unique value
        values_group = QGroupBox(self.tr("'Layer' attribute values"))
        values_layout = QVBoxLayout()
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.values_widget = QWidget()
        self.values_layout = QVBoxLayout(self.values_widget)
        self.scroll_area.setWidget(self.values_widget)
        values_layout.addWidget(self.scroll_area)
        values_group.setLayout(values_layout)
        main_layout.addWidget(values_group)

        self.progress_bar = self.create_progress_bar()
        main_layout.addWidget(self.progress_bar)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.run_btn = QPushButton(self.tr("Run"))
        self.run_btn.setToolTip(
            self.tr("Convert the checked line groups to polygons"))
        self.run_btn.setDefault(True)
        self.run_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.run_btn)
        self.cancel_btn = QPushButton(self.tr("Close"))
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)
        main_layout.addLayout(btn_layout)

        self.setLayout(main_layout)

        self._populate_values()
        self.layer_combo.layerChanged.connect(self._populate_values)

    # --- value list ---

    def _layer_field_name(self, layer):
        """Name of the CAD "Layer" attribute, matched case-insensitively."""
        return next(
            (f.name() for f in layer.fields()
             if f.name().lower() == "layer"), None)

    def _populate_values(self):
        """Fill the scroll area with a checkbox per unique value."""
        self._clear_values()
        layer = self.layer_combo.currentLayer()
        if not layer or not layer.isValid():
            return

        layer_field = self._layer_field_name(layer)
        if not layer_field:
            self.log_message(
                self.tr("No 'Layer' field in the selected layer."),
                Qgis.Warning)
            return

        idx = layer.fields().indexOf(layer_field)
        for value in layer.uniqueValues(idx):
            if value is None or value == "":
                continue
            cb = QCheckBox(str(value))
            self.values_layout.addWidget(cb)

    def _clear_values(self):
        while self.values_layout.count():
            child = self.values_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    def _get_selected(self):
        selected = []
        for i in range(self.values_layout.count()):
            cb = self.values_layout.itemAt(i).widget()
            if isinstance(cb, QCheckBox) and cb.isChecked():
                selected.append(cb.text())
        return selected

    # --- helpers ---

    def _create_lightweight_copy(self, layer, layer_field):
        """Memory copy carrying only the "Layer" field - a hand-rolled Refactor Fields, Processing is much faster without the full attribute table."""
        geom_type = QgsWkbTypes.displayString(layer.wkbType())
        temp = QgsVectorLayer(geom_type, "_temp_cad_lines", "memory")
        # setCrs rather than a ?crs= URI, custom CRS have an empty authid
        temp.setCrs(layer.crs())
        prov = temp.dataProvider()
        src_idx = layer.fields().indexOf(layer_field)
        prov.addAttributes([layer.fields().field(src_idx)])
        temp.updateFields()

        features = []
        for feat in layer.getFeatures():
            new_feat = QgsFeature(temp.fields())
            new_feat.setGeometry(feat.geometry())
            new_feat.setAttribute(0, feat.attribute(src_idx))
            features.append(new_feat)
        prov.addFeatures(features)
        temp.updateExtents()
        return temp

    def _group_feature_ids(self, layer, field_name, selected_values):
        """One pass over the features, grouping IDs by the field value."""
        selected_set = set(selected_values)
        groups = defaultdict(list)
        idx = layer.fields().indexOf(field_name)
        request = QgsFeatureRequest().setFlags(QgsFeatureRequest.NoGeometry)
        request.setSubsetOfAttributes([idx])
        for feat in layer.getFeatures(request):
            val = feat.attribute(idx)
            if val is not None and str(val) in selected_set:
                groups[str(val)].append(feat.id())
        return groups

    # --- run ---

    def accept(self):
        # deferred - processing is itself a plugin, a module-level import would tie our load order to it
        import processing  # type: ignore

        if self._running:
            return
        layer = self.layer_combo.currentLayer()
        if not self.validate_layer(layer, self.tr("line layer"),
                                   check_features=True):
            return

        layer_field = self._layer_field_name(layer)
        if not layer_field:
            self.show_tool_error(
                self.tr("The selected layer has no 'Layer' field. Import "
                        "the drawing with Import DXF / DWG first."))
            return

        selected = self._get_selected()
        if not selected:
            # nothing checked means everything
            idx = layer.fields().indexOf(layer_field)
            selected = [str(v) for v in layer.uniqueValues(idx)
                        if v is not None and v != ""]

        if not selected:
            self.show_tool_warning(self.tr("No values to process."))
            return

        # the feedback pumps the event loop, so freeze the dialog for the run - no second Run, no Close mid-run
        self._running = True
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.layer_combo.setEnabled(False)
        self.progress_bar.setFormat(self.tr("Preparing..."))
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(0)  # indeterminate
        QApplication.processEvents()

        # registered in the project but hidden from the layer tree, Processing needs to reference it by id
        work_layer = self._create_lightweight_copy(layer, layer_field)
        QgsProject.instance().addMapLayer(work_layer, False)

        groups = self._group_feature_ids(work_layer, layer_field, selected)

        self.progress_bar.setFormat("%v / %m")
        self.progress_bar.setMaximum(len(selected))
        self.progress_bar.setValue(0)
        QApplication.processEvents()

        # stop canvas redraws for the duration
        canvas = self.iface.mapCanvas()
        canvas.setRenderFlag(False)
        new_layers = []
        feedback = ResponsiveFeedback()

        try:
            for i, value in enumerate(selected):
                fids = groups.get(value, [])
                if not fids:
                    self.log_message(
                        self.tr("No features found for '{0}'").format(value),
                        Qgis.Warning)
                    self.progress_bar.setValue(i + 1)
                    QApplication.processEvents()
                    continue

                work_layer.selectByIds(fids)

                try:
                    result = processing.run("qgis:linestopolygons", {
                        "INPUT": self.processing_source(work_layer, True),
                        "OUTPUT": f"memory:{value}",
                    }, feedback=feedback)
                    if result and result["OUTPUT"]:
                        new_layer = result["OUTPUT"]
                        new_layer.setName(value)
                        new_layers.append(new_layer)
                except Exception as e:
                    self.log_message(
                        self.tr("Polygon build failed for '{0}': {1}").format(
                            value, e),
                        Qgis.Warning)

                self.progress_bar.setValue(i + 1)
                QApplication.processEvents()

            for nl in new_layers:
                QgsProject.instance().addMapLayer(nl)
        finally:
            canvas.setRenderFlag(True)
            self._running = False
            self.run_btn.setEnabled(True)
            self.cancel_btn.setEnabled(True)
            self.layer_combo.setEnabled(True)
            self.progress_bar.setVisible(False)
            self.progress_bar.setFormat("%p%")
            QgsProject.instance().removeMapLayer(work_layer.id())

        # failures so far only went to the log, so sort out the outcome here. nothing built keeps the dialog open
        skipped = len(selected) - len(new_layers)
        if not new_layers:
            self.show_tool_warning(
                self.tr("No polygon layers were created. See Log "
                        "Messages > Vernier for the reason."))
            return
        details = None
        if skipped > 0:
            if skipped == 1:
                details = self.tr("1 group produced nothing.")
            else:
                details = self.tr(
                    "{0} groups produced nothing.").format(skipped)
        self.show_layers_created(len(new_layers), details)
        super().accept()

    def reject(self):
        if self._running:
            return
        super().reject()

    def closeEvent(self, event):
        if self._running:
            event.ignore()
            return
        super().closeEvent(event)
