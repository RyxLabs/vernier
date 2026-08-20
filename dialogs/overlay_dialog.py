# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared dialog for the two-layer overlay algorithms - subclasses set ALGORITHM and NAME_SUFFIX and return their window title from title()."""

from qgis.PyQt.QtWidgets import QVBoxLayout  # type: ignore
from qgis.core import QgsProject  # type: ignore

from .base_dialog import BaseDialog


class OverlayDialog(BaseDialog):

    ALGORITHM = ""    # processing id, e.g. "native:intersection"
    NAME_SUFFIX = ""  # appended to the input layer's name for the output

    def __init__(self, iface=None, parent=None):
        super().__init__(iface, parent)
        self.setWindowTitle(self.title())
        self.setMinimumWidth(400)
        self._setup_ui()

    def title(self):
        """Window title, which is also the title of every message box this dialog shows. Kept as a tr() literal in the subclass so the extractor sees it."""
        raise NotImplementedError

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        group1, self.input_combo, self.input_selected = \
            self.create_layer_group(self.tr("Input layer"))
        layout.addWidget(group1)

        group2, self.overlay_combo, self.overlay_selected = \
            self.create_layer_group(self.tr("Overlay layer"),
                                    select_active=False)
        layout.addWidget(group2)

        self.progress_bar = self.create_progress_bar()
        layout.addWidget(self.progress_bar)

        button_row, _apply, _close = self.create_button_row(self.tr("Apply"))
        layout.addLayout(button_row)

    def accept(self):
        input_layer = self.input_combo.currentLayer()
        overlay_layer = self.overlay_combo.currentLayer()

        if not self.validate_layer(input_layer, self.tr("input layer")):
            return
        if not self.validate_layer(overlay_layer, self.tr("overlay layer")):
            return

        params = {
            "INPUT": self.processing_source(
                input_layer, self.input_selected.isChecked()),
            "OVERLAY": self.processing_source(
                overlay_layer, self.overlay_selected.isChecked()),
            "OUTPUT": "memory:",
        }

        try:
            result = self.run_processing(
                self.ALGORITHM, params, self.progress_bar)
        except Exception as e:
            self.show_tool_failure(e)
            return

        output = result["OUTPUT"]
        output.setName(f"{input_layer.name()}_{self.NAME_SUFFIX}")
        QgsProject.instance().addMapLayer(output)
        self.show_layer_created(output)
        super().accept()
