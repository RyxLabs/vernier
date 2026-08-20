# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Autosave settings - status banner, backup folder, interval, retention. Also the first-run surface, the toolbar toggle sends you here until autosave/configured is set."""

import os

from qgis.PyQt.QtCore import Qt  # type: ignore
from qgis.PyQt.QtWidgets import (  # type: ignore
    QCheckBox, QFileDialog, QFrame, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QVBoxLayout,
)

from ..services import settings_service
from .base_dialog import BaseDialog


class AutosaveDialog(BaseDialog):

    # status-dot colors, readable on both light and dark themes
    _CLR_ACTIVE = "#2e7d32"
    _CLR_INACTIVE = "#808080"

    def __init__(self, service, iface=None, parent=None):
        super().__init__(iface, parent)
        self._service = service
        self.setWindowTitle(self.tr("Autosave Backups"))
        self.setMinimumSize(460, 380)
        self._setup_ui()
        self._load()
        self._update_status_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # status banner - dot, session info, start/stop
        banner = QFrame()
        banner.setFrameShape(QFrame.Shape.StyledPanel)
        banner_lay = QHBoxLayout(banner)
        banner_lay.setContentsMargins(10, 8, 10, 8)

        self._lbl_status = QLabel()
        self._lbl_status.setTextFormat(Qt.TextFormat.RichText)
        self._lbl_info = QLabel()
        self._btn_toggle = QPushButton()
        self._btn_toggle.setMinimumWidth(100)
        self._btn_toggle.clicked.connect(self._toggle)

        banner_lay.addWidget(self._lbl_status)
        banner_lay.addWidget(self._lbl_info, 1)
        banner_lay.addWidget(self._btn_toggle)
        layout.addWidget(banner)

        dir_group = QGroupBox(self.tr("Backup folder"))
        dir_lay = QHBoxLayout()
        self.edit_dir = QLineEdit()
        self.edit_dir.setPlaceholderText(
            self.tr('Default: a "_backup" folder next to the project file'))
        btn_browse = QPushButton("...")
        btn_browse.setMaximumWidth(32)
        btn_browse.setToolTip(self.tr("Choose the backup folder"))
        btn_browse.clicked.connect(self._browse)
        dir_lay.addWidget(self.edit_dir)
        dir_lay.addWidget(btn_browse)
        dir_group.setLayout(dir_lay)
        layout.addWidget(dir_group)

        settings_group = QGroupBox(self.tr("Settings"))
        settings_lay = QVBoxLayout()
        settings_lay.setSpacing(8)

        interval_row = QHBoxLayout()
        interval_row.addWidget(QLabel(self.tr("Every")))
        self.spin_interval = QSpinBox()
        self.spin_interval.setRange(1, 60)
        self.spin_interval.setMaximumWidth(55)
        interval_row.addWidget(self.spin_interval)
        interval_row.addWidget(QLabel(self.tr("minutes")))
        interval_row.addStretch()
        interval_row.addWidget(QLabel(self.tr("Keep last:")))
        self.spin_keep = QSpinBox()
        self.spin_keep.setRange(1, 100)
        self.spin_keep.setMaximumWidth(55)
        self.spin_keep.setToolTip(self.tr(
            "Number of backup events kept per project - "
            "older ones are deleted automatically"))
        interval_row.addWidget(self.spin_keep)
        interval_row.addWidget(QLabel(self.tr("backups")))
        settings_lay.addLayout(interval_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        settings_lay.addWidget(sep)

        self.chk_project = QCheckBox(self.tr("Project file (.qgz)"))
        self.chk_layers = QCheckBox(
            self.tr("Editable and temporary layers (.gpkg)"))
        self.chk_layers.setToolTip(self.tr(
            "Snapshots unsaved edits without committing them - "
            "editing continues unaffected"))
        settings_lay.addWidget(self.chk_project)
        settings_lay.addWidget(self.chk_layers)

        settings_group.setLayout(settings_lay)
        layout.addWidget(settings_group)

        layout.addStretch()

        hint = QLabel(self.tr(
            "The on/off state and the backup folder are stored in the "
            "project - save the project (Ctrl+S) to keep them after a "
            "restart."))
        hint.setWordWrap(True)
        layout.addWidget(hint)

        action_row = QHBoxLayout()
        btn_now = QPushButton(self.tr("Back up now"))
        btn_now.clicked.connect(self._backup_now)
        action_row.addWidget(btn_now)
        btn_restore = QPushButton(self.tr("Restore backup..."))
        btn_restore.clicked.connect(self._open_restore)
        action_row.addWidget(btn_restore)
        action_row.addStretch()
        layout.addLayout(action_row)

        button_row, _save, _close = self.create_button_row(
            self.tr("Save"), self.tr("Close"))
        layout.addLayout(button_row)

    # --- actions ---

    def _browse(self):
        start = self.edit_dir.text() or os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(
            self, self.tr("Choose the backup folder"), start)
        if chosen:
            self.edit_dir.setText(chosen)

    def _toggle(self):
        if self._service.is_active():
            self._service.set_enabled(False)
        else:
            if not self._validate():
                return
            self._save_settings()
            if not self._service.resolve_backup_dir():
                self.show_warning(
                    self.tr("No backup folder"),
                    self.tr("Save the project to disk or choose a backup "
                            "folder first."))
                return
            self._service.set_enabled(True)
        self._update_status_ui()

    def _backup_now(self):
        if not self._validate():
            return
        self._save_settings()
        if self._service.backup_now():
            # staging worked, the move runs in the background and reports failures on the message bar
            self.show_info(
                self.tr("Backup started"),
                self.tr("Files were written locally and are being copied "
                        "to the backup folder in the background. Copy "
                        "errors will appear above the map canvas."))
        else:
            self.show_warning(
                self.tr("Backup failed"),
                self.tr("The backup could not be started. Check the "
                        "backup folder and Log Messages > Vernier."))
        self._update_status_ui()

    def _open_restore(self):
        from .autosave_restore_dialog import AutosaveRestoreDialog
        AutosaveRestoreDialog(self._service, iface=self.iface,
                              parent=self).exec()

    # --- load/save ---

    def _load(self):
        # backup folder is per-project and lives in the .qgz, the rest is global
        self.edit_dir.setText(self._service.get_backup_dir_pref())
        self.spin_interval.setValue(
            settings_service.get("autosave/interval_minutes"))
        self.spin_keep.setValue(settings_service.get("autosave/max_backups"))
        self.chk_project.setChecked(
            settings_service.get("autosave/save_project"))
        self.chk_layers.setChecked(
            settings_service.get("autosave/save_layers"))

    def _validate(self):
        if (not self.chk_project.isChecked()
                and not self.chk_layers.isChecked()):
            self.show_warning(
                self.tr("Nothing selected"),
                self.tr("Select at least one thing to back up: the "
                        "project file or the layers."))
            return False
        return True

    def _save_settings(self):
        settings_service.set_("autosave/configured", True)
        settings_service.set_("autosave/interval_minutes",
                              self.spin_interval.value())
        settings_service.set_("autosave/save_project",
                              self.chk_project.isChecked())
        settings_service.set_("autosave/save_layers",
                              self.chk_layers.isChecked())
        settings_service.set_("autosave/max_backups", self.spin_keep.value())
        self._service.set_backup_dir_pref(self.edit_dir.text())

    def _update_status_ui(self):
        active = self._service.is_active()
        color = self._CLR_ACTIVE if active else self._CLR_INACTIVE
        label = self.tr("ACTIVE") if active else self.tr("OFF")
        self._lbl_status.setText(
            f'<span style="color:{color}; font-size:16px;">&#9679;</span>'
            f'&nbsp;<b style="color:{color};">{label}</b>')
        self._btn_toggle.setText(
            self.tr("Stop") if active else self.tr("Start"))

        last = self._service.last_save_time
        count = self._service.save_count
        if last and count:
            self._lbl_info.setText(
                self.tr("Last backup {0} · {1} this session").format(
                    last.strftime("%H:%M:%S"), count))
        elif count:
            self._lbl_info.setText(
                self.tr("{0} backups this session").format(count))
        else:
            self._lbl_info.setText("")

    def accept(self):
        if not self._validate():
            return
        self._save_settings()
        if self._service.is_active():
            self._service.restart()  # apply a changed interval immediately
        self.show_success(self.tr("Autosave settings saved."))
        super().accept()
