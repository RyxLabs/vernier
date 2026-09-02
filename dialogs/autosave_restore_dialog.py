# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Browse and restore autosave backups, grouped per backup event - one timestamp is one .qgz plus N .gpkg."""

import os
import shutil
from datetime import datetime

from qgis.PyQt.QtCore import Qt, QUrl  # type: ignore
from qgis.PyQt.QtGui import QDesktopServices  # type: ignore
from qgis.PyQt.QtWidgets import (  # type: ignore
    QFileDialog, QHBoxLayout, QLabel, QPushButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout,
)
from qgis.core import QgsProject  # type: ignore

from ..services import backup_index
from .base_dialog import BaseDialog

_PROJECT_EXTS = (".qgz", ".qgs")


def _fmt_size(num_bytes):
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


class AutosaveRestoreDialog(BaseDialog):

    def __init__(self, service, iface=None, parent=None):
        super().__init__(iface, parent)
        self._service = service
        self.setWindowTitle(self.tr("Restore Backup"))
        self.setMinimumSize(620, 440)
        self._setup_ui()
        self._populate()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        info = QLabel(self.tr(
            "Backups are grouped by the moment they were taken. Select "
            "an event and press <b>Restore project</b> to copy it to a "
            "new location and open it."))
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info)

        # top-level items are backup events, children are the files
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(
            [self.tr("Backup"), self.tr("Contents"), self.tr("Size")])
        self._tree.setColumnWidth(0, 220)
        self._tree.setColumnWidth(1, 240)
        self._tree.itemSelectionChanged.connect(self._on_selection_changed)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self._tree, 1)

        note = QLabel(self.tr(
            "Restoring copies the project file only - it still points "
            "at your original layer sources. The .gpkg files are "
            "snapshots of unsaved layer edits: take one out with "
            "<b>Copy layer file...</b> and add it to the project "
            "yourself."))
        note.setWordWrap(True)
        note.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(note)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel(self.tr("Folder:")))
        self._lbl_folder = QLabel()
        self._lbl_folder.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        folder_row.addWidget(self._lbl_folder, 1)
        self._btn_open_folder = QPushButton(self.tr("Open folder"))
        self._btn_open_folder.setToolTip(
            self.tr("Open the backup folder in the file manager"))
        self._btn_open_folder.clicked.connect(self._open_folder)
        folder_row.addWidget(self._btn_open_folder)
        layout.addLayout(folder_row)

        btn_row = QHBoxLayout()
        self._btn_restore = QPushButton(self.tr("Restore project"))
        self._btn_restore.setDefault(True)
        self._btn_restore.setEnabled(False)
        self._btn_restore.clicked.connect(self._restore_project)
        btn_row.addWidget(self._btn_restore)
        self._btn_copy = QPushButton(self.tr("Copy layer file..."))
        self._btn_copy.setEnabled(False)
        self._btn_copy.setToolTip(self.tr(
            "Copy the selected .gpkg snapshot out of the backup folder"))
        self._btn_copy.clicked.connect(self._copy_layer_file)
        btn_row.addWidget(self._btn_copy)
        btn_row.addStretch()
        btn_close = QPushButton(self.tr("Close"))
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

    def _populate(self):
        root_dir = self._service.resolve_backup_dir()
        if not root_dir:
            self._lbl_folder.setText(self.tr("(no folder configured)"))
            self._btn_open_folder.setEnabled(False)
            self._show_empty_message(self.tr("No backup folder configured."))
            return

        backup_dir = self._service.project_backup_dir(root_dir)
        self._lbl_folder.setText(backup_dir)
        self._btn_open_folder.setEnabled(os.path.isdir(backup_dir))

        if not os.path.isdir(backup_dir):
            self._show_empty_message(self.tr(
                "The backup folder does not exist yet - "
                "wait for the first automatic backup."))
            return

        try:
            files = os.listdir(backup_dir)
        except OSError as e:
            self._show_empty_message(
                self.tr("Could not read the folder: {0}").format(e))
            return

        groups = backup_index.group_by_timestamp(files)
        if not groups:
            self._show_empty_message(
                self.tr("No backups found in the folder."))
            return

        for ts in sorted(groups.keys(), reverse=True):
            entries = []
            for fname in sorted(groups[ts]):
                full = os.path.join(backup_dir, fname)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                entries.append((fname, full, size))

            try:
                dt = datetime.strptime(ts, backup_index.TS_FORMAT)
                label = dt.strftime("%d %b %Y, %H:%M:%S")
            except ValueError:
                label = ts

            project_files = [e for e in entries
                             if e[0].lower().endswith(_PROJECT_EXTS)]
            layer_files = [e for e in entries
                           if e[0].lower().endswith(".gpkg")]
            total_size = sum(s for _, _, s in entries)

            content_parts = []
            if project_files:
                content_parts.append(self.tr("project"))
            if layer_files:
                if len(layer_files) == 1:
                    content_parts.append(self.tr("1 layer"))
                else:
                    content_parts.append(
                        self.tr("{0} layers").format(len(layer_files)))
            content = ", ".join(content_parts) or "-"

            top = QTreeWidgetItem([label, content, _fmt_size(total_size)])
            # .qgz path, None when the event has no project file
            top.setData(0, Qt.ItemDataRole.UserRole,
                        project_files[0][1] if project_files else None)
            for fname, fullpath, size in entries:
                child = QTreeWidgetItem(["", fname, _fmt_size(size)])
                child.setData(0, Qt.ItemDataRole.UserRole, fullpath)
                top.addChild(child)
            self._tree.addTopLevelItem(top)

        if self._tree.topLevelItemCount() > 0:
            self._tree.topLevelItem(0).setExpanded(True)

    def _show_empty_message(self, msg):
        item = QTreeWidgetItem([msg, "", ""])
        item.setForeground(0, Qt.GlobalColor.gray)
        self._tree.addTopLevelItem(item)

    def _on_selection_changed(self):
        items = self._tree.selectedItems()
        if not items:
            self._btn_restore.setEnabled(False)
            self._btn_copy.setEnabled(False)
            return
        path = items[0].data(0, Qt.ItemDataRole.UserRole)
        # only rows that lead to a project file can be restored, layer snapshots can just be copied out
        is_project = path and path.lower().endswith(_PROJECT_EXTS)
        self._btn_restore.setEnabled(bool(is_project))
        self._btn_copy.setEnabled(
            bool(path and path.lower().endswith(".gpkg")))

    def _on_double_click(self, item, _column):
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path and path.lower().endswith(_PROJECT_EXTS):
            self._restore_project()

    def _restore_project(self):
        """Copy the selected backup somewhere else and open the copy - opening the backup in place means one reflexive Ctrl+S kills the recovery point."""
        items = self._tree.selectedItems()
        if not items:
            return
        backup_path = items[0].data(0, Qt.ItemDataRole.UserRole)
        if not backup_path or not backup_path.lower().endswith(_PROJECT_EXTS):
            return

        current = QgsProject.instance()
        if current.isDirty():
            if not self.confirm_action(
                    self.tr("Unsaved changes"),
                    self.tr("The current project has unsaved changes. They "
                            "will be lost if you open the backup.\n\n"
                            "Continue?")):
                return

        suggested = "restored_" + backup_index.original_name(
            os.path.basename(backup_path))
        if not suggested.lower().endswith(_PROJECT_EXTS):
            suggested = os.path.splitext(suggested)[0] + ".qgz"

        if current.fileName():
            start_dir = os.path.dirname(current.fileName())
        else:
            start_dir = os.path.expanduser("~")

        target_path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Save the restored project"),
            os.path.join(start_dir, suggested),
            self.tr("QGIS project (*.qgz *.qgs)"))
        if not target_path:
            return

        try:
            shutil.copy2(backup_path, target_path)
        except OSError as e:
            self.show_error(self.tr("Copy failed"),
                            self.tr("Could not copy the backup: "
                                    "{0}").format(e))
            return

        # close before opening the project so this doesn't linger over the new UI
        self.accept()

        if QgsProject.instance().read(target_path):
            self.iface.messageBar().pushSuccess(
                self.tr("Backup restored"),
                self.tr("The project was copied to {0} and opened.").format(
                    target_path))
        else:
            self.show_error(
                self.tr("Open failed"),
                self.tr("The project was copied to {0} but could not be "
                        "opened. You can open it manually.").format(
                            target_path))

    def _copy_layer_file(self):
        """Copy the selected .gpkg snapshot out. A restored project still points at the original layer sources, so snapshots come back by hand."""
        items = self._tree.selectedItems()
        if not items:
            return
        source_path = items[0].data(0, Qt.ItemDataRole.UserRole)
        if not source_path or not source_path.lower().endswith(".gpkg"):
            return

        suggested = backup_index.original_name(
            os.path.basename(source_path))
        current = QgsProject.instance()
        if current.fileName():
            start_dir = os.path.dirname(current.fileName())
        else:
            start_dir = os.path.expanduser("~")

        target_path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Save the layer snapshot"),
            os.path.join(start_dir, suggested),
            self.tr("GeoPackage (*.gpkg)"))
        if not target_path:
            return
        if not target_path.lower().endswith(".gpkg"):
            target_path += ".gpkg"

        try:
            shutil.copy2(source_path, target_path)
        except OSError as e:
            self.show_error(self.tr("Copy failed"),
                            self.tr("Could not copy the layer "
                                    "snapshot: {0}").format(e))
            return

        self.show_info(
            self.tr("Layer copied"),
            self.tr("The snapshot was copied to {0}.\n\n"
                    "Add it to the project with Layer > Add Layer > "
                    "Add Vector Layer.").format(target_path))

    def _open_folder(self):
        path = self._lbl_folder.text()
        if not path or not os.path.isdir(path):
            return
        # QDesktopServices rather than os.startfile, this one works on Linux/macOS too
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
