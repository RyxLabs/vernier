# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Dissolve dialog: wraps native:dissolve with an optional group-by field."""

from qgis.PyQt.QtWidgets import (  # type: ignore
    QComboBox, QFormLayout, QGroupBox, QVBoxLayout,
)
from qgis.core import QgsProject  # type: ignore

from .base_dialog import BaseDialog


class DissolveDialog(BaseDialog):

    def __init__(self, iface=None, parent=None):
        super().__init__(iface, parent)
        self.setWindowTitle(self.tr("Dissolve"))
        self.setMinimumWidth(400)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        group, self.input_combo, self.selected_only = self.create_layer_group(
            self.tr("Input layer"))
        layout.addWidget(group)

        params_group = QGroupBox(self.tr("Parameters"))
        form = QFormLayout()
        self.field_combo = QComboBox()
        self.field_combo.setToolTip(self.tr(
            "Leave empty to dissolve all features into one"))
        form.addRow(self.tr("Dissolve field:"), self.field_combo)
        params_group.setLayout(form)
        layout.addWidget(params_group)

        self.progress_bar = self.create_progress_bar()
        layout.addWidget(self.progress_bar)

        button_row, _apply, _close = self.create_button_row(self.tr("Apply"))
        layout.addLayout(button_row)

        self.input_combo.layerChanged.connect(self._update_fields)
        self._update_fields()

        # by_text: field lists differ per layer, so the position of last run's field means nothing next time
        self.remember("field", self.field_combo, by_text=True)
        self.restore_remembered()

    def _update_fields(self):
        previous = self.field_combo.currentText()
        self.field_combo.clear()
        self.field_combo.addItem("")  # empty = dissolve everything
        layer = self.input_combo.currentLayer()
        if layer is not None:
            self.field_combo.addItems([f.name() for f in layer.fields()])
        # keep the chosen field across a layer swap when the new layer has it too
        index = self.field_combo.findText(previous)
        if index >= 0:
            self.field_combo.setCurrentIndex(index)

    def accept(self):
        layer = self.input_combo.currentLayer()
        if not self.validate_layer(layer, self.tr("input layer")):
            return

        field = self.field_combo.currentText()
        params = {
            "INPUT": self.processing_source(
                layer, self.selected_only.isChecked()),
            "FIELD": [field] if field else [],
            "OUTPUT": "memory:",
        }

        self.save_remembered()

        try:
            result = self.run_processing(
                "native:dissolve", params, self.progress_bar)
        except Exception as e:
            self.show_tool_failure(e)
            return

        output = result["OUTPUT"]
        output.setName(f"{layer.name()}_dissolve")
        QgsProject.instance().addMapLayer(output)
        self.show_layer_created(output)
        super().accept()
