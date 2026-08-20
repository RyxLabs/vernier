# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Buffer dialog: wraps native:buffer with the full parameter set visible."""

from qgis.PyQt.QtWidgets import (  # type: ignore
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QSpinBox,
    QVBoxLayout,
)
from qgis.core import QgsProject, QgsUnitTypes  # type: ignore

from .base_dialog import BaseDialog


class BufferDialog(BaseDialog):

    def __init__(self, iface=None, parent=None):
        super().__init__(iface, parent)
        self.setWindowTitle(self.tr("Buffer"))
        self.setMinimumWidth(400)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        group, self.input_combo, self.selected_only = self.create_layer_group(
            self.tr("Input layer"))
        layout.addWidget(group)

        params_group = QGroupBox(self.tr("Parameters"))
        form = QFormLayout()

        self.distance_spin = QDoubleSpinBox()
        self.distance_spin.setRange(-100000, 100000)
        self.distance_spin.setDecimals(3)
        self.distance_spin.setSingleStep(0.1)
        self.distance_spin.setValue(0.0)
        self.distance_spin.setToolTip(self.tr(
            "Buffer distance in layer units.\n"
            "Positive grows the features, negative shrinks them."))
        form.addRow(self.tr("Distance:"), self.distance_spin)

        self.segments_spin = QSpinBox()
        self.segments_spin.setRange(1, 100)
        self.segments_spin.setValue(5)
        self.segments_spin.setToolTip(self.tr(
            "Segments per quarter circle - more means smoother curves"))
        form.addRow(self.tr("Segments:"), self.segments_spin)

        # combo indexes match the native:buffer enum values
        self.end_cap_combo = QComboBox()
        self.end_cap_combo.addItem(self.tr("Round"))
        self.end_cap_combo.addItem(self.tr("Flat"))
        self.end_cap_combo.addItem(self.tr("Square"))
        form.addRow(self.tr("End cap style:"), self.end_cap_combo)

        self.join_combo = QComboBox()
        self.join_combo.addItem(self.tr("Round"))
        self.join_combo.addItem(self.tr("Miter"))
        self.join_combo.addItem(self.tr("Bevel"))
        form.addRow(self.tr("Join style:"), self.join_combo)

        self.miter_spin = QDoubleSpinBox()
        self.miter_spin.setRange(1, 100)
        self.miter_spin.setDecimals(2)
        self.miter_spin.setValue(2.0)
        form.addRow(self.tr("Miter limit:"), self.miter_spin)

        self.dissolve_check = QCheckBox(self.tr("Dissolve result"))
        form.addRow(self.dissolve_check)

        params_group.setLayout(form)
        layout.addWidget(params_group)

        self.progress_bar = self.create_progress_bar()
        layout.addWidget(self.progress_bar)

        button_row, _apply, _close = self.create_button_row(self.tr("Apply"))
        layout.addLayout(button_row)

        # miter limit only applies to the miter join style
        self.join_combo.currentIndexChanged.connect(
            lambda index: self.miter_spin.setEnabled(index == 1))
        self.miter_spin.setEnabled(False)

        self.input_combo.layerChanged.connect(self._update_unit_suffix)
        self._update_unit_suffix()

        self.remember("distance", self.distance_spin)
        self.remember("segments", self.segments_spin)
        self.remember("end_cap", self.end_cap_combo)
        self.remember("join_style", self.join_combo)
        self.remember("miter_limit", self.miter_spin)
        self.remember("dissolve", self.dissolve_check)
        self.restore_remembered()

    def _update_unit_suffix(self):
        layer = self.input_combo.currentLayer()
        suffix = ""
        if layer is not None and layer.crs().isValid():
            suffix = " " + QgsUnitTypes.toAbbreviatedString(
                layer.crs().mapUnits())
        self.distance_spin.setSuffix(suffix)

    def accept(self):
        layer = self.input_combo.currentLayer()
        if not self.validate_layer(layer, self.tr("input layer")):
            return

        distance = self.distance_spin.value()
        if distance == 0:
            self.show_tool_warning(self.tr("Distance cannot be 0."))
            return

        params = {
            "INPUT": self.processing_source(
                layer, self.selected_only.isChecked()),
            "DISTANCE": distance,
            "SEGMENTS": self.segments_spin.value(),
            "END_CAP_STYLE": self.end_cap_combo.currentIndex(),
            "JOIN_STYLE": self.join_combo.currentIndex(),
            "MITER_LIMIT": self.miter_spin.value(),
            "DISSOLVE": self.dissolve_check.isChecked(),
            "OUTPUT": "memory:",
        }

        self.save_remembered()

        try:
            result = self.run_processing(
                "native:buffer", params, self.progress_bar)
        except Exception as e:
            self.show_tool_failure(e)
            return

        output = result["OUTPUT"]
        output.setName(f"{layer.name()}_buffer")
        QgsProject.instance().addMapLayer(output)
        self.show_layer_created(output)
        super().accept()
