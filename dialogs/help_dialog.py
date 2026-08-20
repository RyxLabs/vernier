# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Help/About dialog. The tool table is generated from the feature catalog so it cannot drift from what the toolbar offers."""

import configparser
import os

from qgis.PyQt.QtCore import Qt  # type: ignore
from qgis.PyQt.QtWidgets import (  # type: ignore
    QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from .. import features
from .base_dialog import BaseDialog


def _plugin_version() -> str:
    meta = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "metadata.txt")
    parser = configparser.ConfigParser()
    try:
        parser.read(meta, encoding="utf-8")
        return parser.get("general", "version", fallback="?")
    except (configparser.Error, OSError):
        return "?"


class HelpDialog(BaseDialog):

    def __init__(self, iface=None, parent=None):
        super().__init__(iface, parent)
        self.setWindowTitle(self.tr("Vernier Help"))
        self.setMinimumSize(560, 420)
        # roomy default so the description column is readable on first open, BaseDialog.showEvent overrides it with the saved geometry
        self.resize(940, 620)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        tabs.addTab(self._build_tools_tab(), self.tr("Tools"))
        tabs.addTab(self._build_about_tab(), self.tr("About"))
        # read-only dialog, so no OK/Close pair - one Close is enough
        button_row = QHBoxLayout()
        button_row.addStretch()
        close_btn = QPushButton(self.tr("Close"))
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        button_row.addWidget(close_btn)
        layout.addLayout(button_row)

    def _build_tools_tab(self):
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        table = QTableWidget(len(features.CATALOG), 4)
        table.setHorizontalHeaderLabels(
            [self.tr("Tool"), self.tr("Command"), self.tr("Shortcut"),
             self.tr("Description")])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setWordWrap(True)

        for row, feat in enumerate(features.CATALOG):
            table.setItem(row, 0, QTableWidgetItem(self.tr(feat.label)))
            table.setItem(row, 1, QTableWidgetItem(
                ", ".join(feat.aliases)))  # typed in the CAD Mode bar
            table.setItem(row, 2, QTableWidgetItem(feat.shortcut or ""))
            hint_item = QTableWidgetItem(self.tr(feat.hint))
            hint_item.setToolTip(self.tr(feat.hint))
            table.setItem(row, 3, hint_item)

        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        # rows have to size to the wrapped text and redo it on every resize - a one-shot resizeRowsToContents() here measures the pre-layout width and never updates, so descriptions come out elided
        table.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        # let long descriptions be read in full even at a small window size
        table.setTextElideMode(Qt.TextElideMode.ElideNone)
        tab_layout.addWidget(table)

        # the area readout has no toolbar action so the generated table can't list it, this note is the only place it shows up
        note = QLabel(self.tr(
            "Commands are typed in the CAD Mode command bar. Also "
            "included: a live area readout in the status bar (bottom "
            "right) whenever polygon features are selected - units "
            "configurable under Settings > Display."))
        note.setWordWrap(True)
        tab_layout.addWidget(note)
        return tab

    def _build_about_tab(self):
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        about = QLabel(
            f"<h2>Vernier {_plugin_version()}</h2>"
            + "<p>" + self.tr(
                "Vector editing tools for QGIS.") + "</p>"
            + "<p>" + self.tr("Author") + ": RyxLabs "
            "(<a href='https://ryxlabs.dev'>ryxlabs.dev</a>)<br>"
            "<a href='mailto:hello@ryxlabs.dev'>hello@ryxlabs.dev</a></p>"
            + "<p>" + self.tr(
                "Licensed under the GNU GPL v2 or later.") + " "
            + self.tr("Some icons adapted from Tabler Icons (MIT).")
            + "</p>"
            + "<p><a href='https://github.com/ryxlabs/vernier'>"
            + self.tr("Source code and issue tracker") + "</a></p>"
        )
        about.setOpenExternalLinks(True)
        about.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        about.setWordWrap(True)
        tab_layout.addWidget(about)
        tab_layout.addStretch()
        return tab
