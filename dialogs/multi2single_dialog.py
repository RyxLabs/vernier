# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Multipart to singleparts dialog: wraps native:multiparttosingleparts."""

from qgis.PyQt.QtWidgets import QVBoxLayout  # type: ignore
from qgis.core import QgsProject  # type: ignore

from .base_dialog import BaseDialog


class Multi2SingleDialog(BaseDialog):

    def __init__(self, iface=None, parent=None):
        super().__init__(iface, parent)
        self.setWindowTitle(self.tr("Multipart to Singleparts"))
        self.setMinimumWidth(400)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        group, self.input_combo, self.selected_only = self.create_layer_group(
            self.tr("Input layer"))
        layout.addWidget(group)

        self.progress_bar = self.create_progress_bar()
        layout.addWidget(self.progress_bar)

        button_row, _convert, _close = self.create_button_row(
            self.tr("Convert"))
        layout.addLayout(button_row)

    def accept(self):
        layer = self.input_combo.currentLayer()
        if not self.validate_layer(layer, self.tr("input layer")):
            return

        params = {
            "INPUT": self.processing_source(
                layer, self.selected_only.isChecked()),
            "OUTPUT": "memory:",
        }

        try:
            result = self.run_processing(
                "native:multiparttosingleparts", params, self.progress_bar)
        except Exception as e:
            self.show_tool_failure(e)
            return

        output = result["OUTPUT"]
        output.setName(f"{layer.name()}_singleparts")
        QgsProject.instance().addMapLayer(output)
        self.show_layer_created(output)
        super().accept()
