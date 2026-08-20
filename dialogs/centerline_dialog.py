# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Centerline dialog - medial axis of polygon features, extracted in a background QgsTask while the dialog stays usable."""

from qgis.PyQt.QtWidgets import (  # type: ignore
    QCheckBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLineEdit, QPushButton, QSpinBox, QVBoxLayout,
)
from qgis.core import (  # type: ignore
    QgsApplication, QgsMapLayerProxyModel, QgsProject, QgsUnitTypes,
)

from .base_dialog import BaseDialog

# preset -> (densify step, smoothing on, smoothing passes)
_PRESETS = {
    "roads": (0.5, False, 2),
    "rivers": (3.0, True, 4),
    "fine": (0.2, False, 2),
}


class CenterlineDialog(BaseDialog):

    def __init__(self, iface=None, parent=None):
        super().__init__(iface, parent)
        self.setWindowTitle(self.tr("Extract Centerline"))
        self.setMinimumWidth(460)
        self._task = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        group, self.layer_combo, self.selected_only = self.create_layer_group(
            self.tr("Input layer"), QgsMapLayerProxyModel.Filter.PolygonLayer)
        layout.addWidget(group)

        params_group = QGroupBox(self.tr("Parameters"))
        form = QFormLayout()

        self.densify_spin = QDoubleSpinBox()
        self.densify_spin.setRange(0.01, 1000.0)
        self.densify_spin.setDecimals(2)
        self.densify_spin.setValue(1.0)
        self.densify_spin.setToolTip(self.tr(
            "Spacing of the points sampled along the polygon boundary, "
            "in layer units.\n"
            "Smaller is more precise but slower.\n"
            "Roads preset uses 0.5; Rivers preset uses 3.0."))
        form.addRow(self.tr("Densify step:"), self.densify_spin)

        presets = QHBoxLayout()
        for label, key in ((self.tr("Roads"), "roads"),
                           (self.tr("Rivers"), "rivers"),
                           (self.tr("Fine detail"), "fine")):
            btn = QPushButton(label)
            step, smooth_on, passes = _PRESETS[key]
            if smooth_on:
                tip = self.tr(
                    "Densify step {0}, {1} smoothing passes").format(
                        step, passes)
            else:
                tip = self.tr(
                    "Densify step {0}, straight output").format(step)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda checked, k=key: self._apply_preset(k))
            presets.addWidget(btn)
        form.addRow(self.tr("Presets:"), presets)

        params_group.setLayout(form)
        layout.addWidget(params_group)

        output_group = QGroupBox(self.tr("Output"))
        output_form = QFormLayout()

        self.extend_check = QCheckBox(
            self.tr("Extend line to the polygon boundary"))
        self.extend_check.setChecked(True)
        self.extend_check.setToolTip(self.tr(
            "Prolong the axis until it reaches the polygon outline\n"
            "instead of stopping short of the edges."))
        output_form.addRow(self.extend_check)

        self.output_name = QLineEdit()
        self.output_name.setToolTip(self.tr(
            "Name of the result layer. It follows the input layer until "
            "you type your own."))
        output_form.addRow(self.tr("Output name:"), self.output_name)

        output_group.setLayout(output_form)
        layout.addWidget(output_group)

        self._adv_toggle = QCheckBox(self.tr("Advanced options"))
        self._adv_toggle.setChecked(False)
        layout.addWidget(self._adv_toggle)

        self._adv_group = QGroupBox()
        adv_form = QFormLayout()

        self.straighten_check = QCheckBox(
            self.tr("Straighten (remove sampling noise)"))
        self.straighten_check.setChecked(True)
        self.straighten_check.setToolTip(self.tr(
            "Collapse the sampling zigzag into straight segments and trim\n"
            "the corner spurs at the ends, so a straight polygon yields one\n"
            "straight line from the middle of one end to the other."))
        adv_form.addRow(self.straighten_check)

        self.smooth_check = QCheckBox(self.tr("Enable smoothing"))
        self.smooth_check.setChecked(False)
        self.smooth_check.setToolTip(self.tr(
            "Round the bends with Chaikin smoothing.\n"
            "Suited to organic shapes like rivers; leave off for\n"
            "parcels and roads that should stay straight."))
        adv_form.addRow(self.smooth_check)

        self.smooth_iter_spin = QSpinBox()
        self.smooth_iter_spin.setRange(1, 8)
        self.smooth_iter_spin.setValue(3)
        self.smooth_iter_spin.setToolTip(self.tr(
            "Number of smoothing passes.\n"
            "1–2 light, 3–4 moderate, 5+ very smooth"))
        adv_form.addRow(self.tr("Smoothing passes:"), self.smooth_iter_spin)

        self.trunk_check = QCheckBox(self.tr("Main trunk only"))
        self.trunk_check.setChecked(True)
        self.trunk_check.setToolTip(self.tr(
            "Keep only the longest continuous path\n"
            "and drop every side branch."))
        adv_form.addRow(self.trunk_check)

        self._adv_group.setLayout(adv_form)
        self._adv_group.setVisible(False)
        self._adv_toggle.toggled.connect(self._adv_group.setVisible)
        layout.addWidget(self._adv_group)

        self.progress_bar = self.create_progress_bar()
        layout.addWidget(self.progress_bar)

        btn_row, self.run_btn, self.close_btn = \
            self.create_button_row(self.tr("Extract"))
        self.cancel_btn = QPushButton(self.tr("Cancel"))
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setToolTip(self.tr("Stop the running extraction"))
        self.cancel_btn.clicked.connect(self._cancel_task)
        # before the Extract and Close that create_button_row already added
        btn_row.insertWidget(btn_row.count() - 2, self.cancel_btn)
        layout.addLayout(btn_row)

        self.layer_combo.layerChanged.connect(self._on_layer_changed)
        self._auto_name = ""
        self._on_layer_changed()

        self.remember("densify", self.densify_spin)
        self.remember("extend", self.extend_check)
        self.remember("advanced", self._adv_toggle)
        self.remember("straighten", self.straighten_check)
        self.remember("smooth", self.smooth_check)
        self.remember("smooth_passes", self.smooth_iter_spin)
        self.remember("trunk_only", self.trunk_check)
        self.restore_remembered()

    def _on_layer_changed(self):
        self._update_unit_suffix()
        self._update_output_name()

    def _update_unit_suffix(self):
        layer = self.layer_combo.currentLayer()
        suffix = ""
        if layer is not None and layer.crs().isValid():
            suffix = " " + QgsUnitTypes.toAbbreviatedString(
                layer.crs().mapUnits())
        self.densify_spin.setSuffix(suffix)

    def _update_output_name(self):
        """Keep the output name in step with the input layer, the way every other tool names its result - but never overwrite a name the user typed."""
        current = self.output_name.text().strip()
        if current and current != self._auto_name:
            return
        layer = self.layer_combo.currentLayer()
        self._auto_name = (f"{layer.name()}_centerline" if layer is not None
                           else self.tr("Centerline"))
        self.output_name.setText(self._auto_name)

    def _apply_preset(self, key):
        step, smooth_on, passes = _PRESETS[key]
        self.densify_spin.setValue(step)
        self.straighten_check.setChecked(True)
        self.smooth_check.setChecked(smooth_on)
        self.smooth_iter_spin.setValue(passes)
        self.trunk_check.setChecked(True)
        # smoothing and trunk live in the advanced group, open it so every value the preset touched is visible
        self._adv_toggle.setChecked(True)

    def accept(self):
        # deferred so a missing shapely fails here with a clear message instead of at plugin load
        from ..services import centerline_service

        if not centerline_service.HAS_SHAPELY:
            self.show_tool_error(
                self.tr(
                    "This tool needs the 'shapely' Python package, which "
                    "ships with most QGIS installs.\n\n"
                    "Your QGIS Python environment does not have it. Install "
                    "it there (for example, from the OSGeo4W Shell:\n"
                    "python -m pip install shapely) and try again."))
            return

        layer = self.layer_combo.currentLayer()
        if not self.validate_layer(layer, self.tr("polygon layer"),
                                   check_features=True):
            return

        # densify step is a distance in layer units, degrees make the sampling meaningless
        if layer.crs().isValid() and layer.crs().isGeographic():
            self.show_tool_warning(
                self.tr(
                    "The layer CRS ({0}) uses degrees, but the densify step "
                    "is a distance in layer units.\n\n"
                    "Reproject the layer to a projected CRS (meters) and "
                    "try again.").format(layer.crs().authid()))
            return

        if (self.selected_only.isChecked()
                and layer.selectedFeatureCount() == 0):
            self.show_tool_warning(
                self.tr("No features are selected. Clear "
                        "'Selected features only' to process the whole "
                        "layer."))
            return

        self.save_remembered()

        self._task = centerline_service.CenterlineTask(
            layer=layer,
            densify_distance=self.densify_spin.value(),
            straighten=self.straighten_check.isChecked(),
            smooth=self.smooth_check.isChecked(),
            smooth_iterations=self.smooth_iter_spin.value(),
            trunk_only=self.trunk_check.isChecked(),
            extend_to_ends=self.extend_check.isChecked(),
            output_name=self.output_name.text().strip()
            or self._auto_name or self.tr("Centerline"),
            selected_only=self.selected_only.isChecked(),
            finished_cb=self._on_task_finished,
        )
        self._task.progressChanged.connect(self._on_task_progress)
        self._set_running(True)
        QgsApplication.taskManager().addTask(self._task)
        # stays open on purpose, this work is iterative - tweak the step, run again. Close dismisses it

    def _set_running(self, running):
        """Freeze the run controls while the task is out. _processing also bounces Escape and the window X, so the dialog can't go away with a task still pointing at it."""
        self._processing = running
        self.run_btn.setEnabled(not running)
        self.close_btn.setEnabled(not running)
        self.cancel_btn.setEnabled(running)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(running)

    def _on_task_progress(self, value):
        try:
            self.progress_bar.setValue(int(value))
        except RuntimeError:
            pass  # dialog already destroyed

    def _cancel_task(self):
        if self._task is not None:
            self.cancel_btn.setEnabled(False)
            self._task.cancel()

    def _on_task_finished(self, success, task):
        """Back on the main thread, where the result layer has to be built."""
        self._task = None
        try:
            self._set_running(False)
        except RuntimeError:
            return  # dialog already destroyed, nothing left to report into

        if not success:
            if task.isCanceled():
                self.show_tool_notice(self.tr("Extraction canceled."))
            else:
                self.show_tool_error(
                    task.error_msg or self.tr(
                        "The extraction did not finish. See Log "
                        "Messages > Vernier for details."))
            return

        output_layer = task.build_layer()
        if output_layer is not None and task.ok > 0:
            QgsProject.instance().addMapLayer(output_layer)
            details = None
            if task.skipped or task.errors:
                details = self.tr(
                    "Features skipped: {0}\nErrors: {1}").format(
                        task.skipped, task.errors)
            self.show_layer_created(output_layer, details)
        elif task.errors > 0:
            self.show_tool_warning(
                self.tr(
                    "No centerlines extracted: {0} skipped, {1} errors. "
                    "See the Vernier log for details.").format(
                        task.skipped, task.errors))
        else:
            self.show_tool_warning(
                self.tr(
                    "No centerlines could be extracted.\n"
                    "Try a smaller densify step."))
