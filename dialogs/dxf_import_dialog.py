# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""DXF/DWG import - CAD drawing to styled GeoPackage, converted in a background QgsTask while the dialog stays open with a mini console."""

import os

from qgis.PyQt.QtGui import QFont  # type: ignore
from qgis.PyQt.QtWidgets import (  # type: ignore
    QCheckBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextEdit, QVBoxLayout,
)
from qgis.core import (  # type: ignore
    QgsApplication, QgsCoordinateReferenceSystem, QgsProject,
    QgsRectangle, QgsVectorLayer,
)
from qgis.gui import QgsProjectionSelectionWidget  # type: ignore

from ..services import deps, settings_service
from .base_dialog import BaseDialog


class DxfImportDialog(BaseDialog):

    def __init__(self, iface=None, parent=None):
        super().__init__(iface, parent)
        self.setWindowTitle(self.tr("Import DXF / DWG"))
        self.setMinimumWidth(560)
        self._task = None
        self._setup_ui()

    # --- ui ---

    def _setup_ui(self):
        main = QVBoxLayout()
        main.setSpacing(6)

        gb_in = QGroupBox(self.tr("Input file"))
        row_in = QHBoxLayout()
        self.edit_input = QLineEdit()
        self.edit_input.setPlaceholderText(
            self.tr("Path to the DXF or DWG file..."))
        btn_in = QPushButton(self.tr("Browse..."))
        btn_in.clicked.connect(self._browse_input)
        row_in.addWidget(self.edit_input)
        row_in.addWidget(btn_in)

        lay_in = QVBoxLayout()
        lay_in.addLayout(row_in)

        self.lbl_dwg_info = QLabel("")
        self.lbl_dwg_info.setWordWrap(True)
        self.lbl_dwg_info.setVisible(False)
        lay_in.addWidget(self.lbl_dwg_info)
        gb_in.setLayout(lay_in)
        main.addWidget(gb_in)

        gb_out = QGroupBox(self.tr("Output file"))
        row_out = QHBoxLayout()
        self.edit_output = QLineEdit()
        self.edit_output.setPlaceholderText(self.tr("GeoPackage path..."))
        btn_out = QPushButton(self.tr("Browse..."))
        btn_out.clicked.connect(self._browse_output)
        row_out.addWidget(self.edit_output)
        row_out.addWidget(btn_out)
        gb_out.setLayout(row_out)
        main.addWidget(gb_out)

        gb_opt = QGroupBox(self.tr("Options"))
        opt_layout = QFormLayout()

        # DXF/DWG carry no CRS, so this gets assigned to the output and never reprojected. last choice wins over the project CRS
        self.crs_widget = QgsProjectionSelectionWidget()
        last_crs = settings_service.get("dxf_import/last_crs")
        crs = QgsCoordinateReferenceSystem(last_crs) if last_crs else \
            QgsCoordinateReferenceSystem()
        if not crs.isValid():
            crs = QgsProject.instance().crs()
        self.crs_widget.setCrs(crs)
        self.crs_widget.setToolTip(self.tr(
            "CRS assigned to the imported data. DXF files have no CRS of\n"
            "their own - pick the system the drawing was made in."))
        opt_layout.addRow(self.tr("Assign CRS:"), self.crs_widget)

        self.spin_simplify = QDoubleSpinBox()
        self.spin_simplify.setRange(0.0, 5.0)
        self.spin_simplify.setValue(0.10)
        self.spin_simplify.setDecimals(2)
        self.spin_simplify.setSuffix(" m")
        self.spin_simplify.setSpecialValueText(self.tr("None"))
        self.spin_simplify.setToolTip(self.tr(
            "Geometry simplification at import.\n"
            "0 = no simplification. Recommended: 0.10 m."))
        opt_layout.addRow(self.tr("Simplify:"), self.spin_simplify)

        self.chk_skip = QCheckBox(self.tr("Skip CAD noise layers"))
        self.chk_skip.setChecked(
            settings_service.get("dxf_import/skip_enabled"))
        self.chk_skip.setToolTip(self.tr(
            "Exclude layers whose names contain any of the keywords "
            "below.\nEdit the list to match your drawings."))
        opt_layout.addRow(self.chk_skip)

        self.skip_edit = QLineEdit(
            settings_service.get("dxf_import/skip_keywords"))
        self.skip_edit.setToolTip(self.tr(
            "Comma-separated keywords, matched case-insensitively.\n"
            "Layers containing any of them are not imported."))
        self.skip_edit.setEnabled(self.chk_skip.isChecked())
        self.chk_skip.toggled.connect(self.skip_edit.setEnabled)
        opt_layout.addRow(self.tr("Keywords:"), self.skip_edit)

        gb_opt.setLayout(opt_layout)
        main.addWidget(gb_opt)

        self.progress_bar = self.create_progress_bar()
        main.addWidget(self.progress_bar)

        # fixed colors on purpose, it should read as a terminal in light and dark themes alike
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setFixedHeight(100)
        self.log_box.setFont(QFont("Courier New", 8))
        self.log_box.setStyleSheet(
            "background:#1A202C; color:#68D391; border-radius:4px;")
        main.addWidget(self.log_box)

        btn_row = QHBoxLayout()
        self.btn_import = QPushButton(self.tr("Import"))
        self.btn_import.setDefault(True)
        self.btn_import.clicked.connect(self.accept)

        self.btn_cancel_task = QPushButton(self.tr("Cancel"))
        self.btn_cancel_task.setEnabled(False)
        self.btn_cancel_task.clicked.connect(self._cancel_task)

        btn_close = QPushButton(self.tr("Close"))
        btn_close.clicked.connect(self.reject)

        btn_row.addWidget(self.btn_import)
        btn_row.addWidget(self.btn_cancel_task)
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        main.addLayout(btn_row)

        self.setLayout(main)

        self.remember("simplify", self.spin_simplify)
        self.restore_remembered()

    # --- helpers ---

    def _browse_input(self):
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr("Select the DXF or DWG file"), "",
            self.tr("DXF/DWG (*.dxf *.DXF *.dwg *.DWG);;All files (*)"))
        if not path:
            return
        self.edit_input.setText(path)
        base = os.path.splitext(path)[0]
        self.edit_output.setText(base + "_qgis.gpkg")

        if path.lower().endswith(".dwg"):
            if deps.find_oda_converter():
                self.lbl_dwg_info.setText(self.tr(
                    "DWG detected - it will be converted to DXF "
                    "automatically via ODA File Converter."))
            else:
                self.lbl_dwg_info.setText(self.tr(
                    "DWG detected. This needs ODA File Converter - a free "
                    "desktop program (not a Python package).\n"
                    "Download it from {0}").format(deps.ODA_DOWNLOAD_URL))
            self.lbl_dwg_info.setVisible(True)
        else:
            self.lbl_dwg_info.setVisible(False)

    def _browse_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Save the GeoPackage file"), "",
            self.tr("GeoPackage (*.gpkg);;All files (*)"))
        if path:
            if not path.lower().endswith(".gpkg"):
                path += ".gpkg"
            self.edit_output.setText(path)

    def _log(self, msg):
        self.log_box.append(msg)
        sb = self.log_box.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _cancel_task(self):
        if self._task and self._task.status() == self._task.Running:
            self._task.cancel()
            self._log(self.tr("Cancel requested..."))

    # --- import ---

    def accept(self):
        from ..services.dxf_import_service import DXFImportTask

        if self._task and self._task.status() == self._task.Running:
            return

        ogr2ogr = deps.find_ogr2ogr()
        if not ogr2ogr:
            self.show_tool_error(
                self.tr("ogr2ogr was not found.\n"
                        "It ships with every QGIS install (bin folder).\n"
                        "Add the QGIS bin folder to your PATH."))
            return

        dxf_path = self.edit_input.text().strip()
        if not dxf_path or not os.path.isfile(dxf_path):
            self.show_tool_error(
                self.tr("The input file does not exist."))
            return

        output_path = self.edit_output.text().strip()
        if not output_path:
            self.show_tool_error(
                self.tr("Choose an output file."))
            return
        # the sidecar style folder and the layer tree group are named after the file, so it can't go in without an extension
        if not output_path.lower().endswith(".gpkg"):
            output_path += ".gpkg"
            self.edit_output.setText(output_path)

        oda_path = None
        if dxf_path.lower().endswith(".dwg"):
            oda_path = deps.find_oda_converter()
            if not oda_path:
                # let them point at the .exe, otherwise send them to the download
                oda_path = deps.locate_oda_dialog(self)
                if not oda_path:
                    self.show_tool_error(
                        self.tr(
                            "DWG files need ODA File Converter - a free "
                            "desktop program (not a Python package).\n\n"
                            "Download it from:\n{0}\n\n"
                            "After installing, retry the import.").format(
                                deps.ODA_DOWNLOAD_URL))
                    return

        if os.path.exists(output_path):
            if not self.confirm_action(
                    self.tr("Overwrite"),
                    self.tr("The file already exists:\n{0}\n\n"
                            "Overwrite it?").format(output_path)):
                return

        crs = self.crs_widget.crs()
        if not crs.isValid():
            self.show_tool_error(
                self.tr("Choose a valid CRS to assign."))
            return
        # custom CRS have no authid, store and pass WKT instead
        crs_def = crs.authid() or crs.toWkt()
        settings_service.set_("dxf_import/last_crs", crs_def)

        simplify = self.spin_simplify.value()

        skip_keywords = None
        if self.chk_skip.isChecked():
            raw = self.skip_edit.text().strip()
            if raw:
                skip_keywords = {
                    kw.strip().upper() for kw in raw.split(",") if kw.strip()
                }
        settings_service.set_("dxf_import/skip_enabled",
                              self.chk_skip.isChecked())
        settings_service.set_("dxf_import/skip_keywords",
                              self.skip_edit.text().strip())
        self.save_remembered()

        self.btn_import.setEnabled(False)
        self.btn_cancel_task.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self._log(self.tr("Import: {0}").format(os.path.basename(dxf_path)))
        if simplify:
            self._log(self.tr("Simplify: {0} m").format(simplify))
        else:
            # the spin box shows "None" at 0, say the same here
            self._log(self.tr("Simplify: none"))
        if not deps.is_installed("ezdxf"):
            # same consent-gated offer as DXF export, the README promises ezdxf is what reads the CAD colors
            deps.ensure("ezdxf", parent=self)
        if not deps.is_installed("ezdxf"):
            self._log(self.tr(
                "ezdxf is not installed - CAD colors will not be read.\n"
                "For full symbology: python -m pip install ezdxf "
                "(OSGeo4W Shell on Windows)"))

        self._task = DXFImportTask(
            dxf_path, output_path, simplify, crs_def,
            skip_keywords, ogr2ogr, oda_path, self._on_task_finished,
        )
        self._task.progressChanged.connect(
            lambda v: self.progress_bar.setValue(int(v)))
        QgsApplication.taskManager().addTask(self._task)

    # --- task finished ---

    def _on_task_finished(self, success, output_path, stats, error_msg):
        # this can fire after the dialog was closed or destroyed
        try:
            if not self.isVisible():
                return
        except RuntimeError:
            return  # C++ object already deleted
        self.btn_import.setEnabled(True)
        self.btn_cancel_task.setEnabled(False)

        if not success:
            self.progress_bar.setVisible(False)
            canceled = False
            try:
                canceled = bool(self._task and self._task.isCanceled())
            except RuntimeError:
                pass  # C++ object already deleted
            # a user cancel isn't an error, no modal popup for it
            if canceled and not error_msg:
                self._log(self.tr("Import canceled."))
                self.show_tool_notice(self.tr("Import canceled."))
                return
            self._log(self.tr("ERROR: {0}").format(error_msg))
            self.show_tool_error(
                error_msg or self.tr(
                    "The import did not finish. Check Log "
                    "Messages > Vernier for the converter output."))
            return

        n_layers = stats.get("layers_written", 0)
        n_feat = stats.get("n_ok", 0)
        self._log(self.tr("OK: {0} layers, {1:,} features").format(
            n_layers, n_feat))

        self.progress_bar.setValue(100)

        loaded = self._load_in_qgis(output_path, stats)
        self._log(self.tr("{0} layers loaded into QGIS").format(loaded))

        self.show_success(self.tr(
            "Import finished: {0} layers from {1}").format(
                loaded, os.path.basename(output_path)))
        self.progress_bar.setVisible(False)

    # --- load layers into qgis ---

    def _load_in_qgis(self, gpkg_path, stats):
        """Load the GPKG layers with their subset strings - freeze the canvas, load everything, zoom, then one refresh."""
        layer_info = stats.get("layer_info", {})

        if not layer_info:
            self._log(self.tr("No layers to load."))
            return 0

        canvas = self.iface.mapCanvas()
        canvas.freeze(True)

        root = QgsProject.instance().layerTreeRoot()
        gname = os.path.splitext(os.path.basename(gpkg_path))[0]
        group = root.insertGroup(0, gname)

        loaded = 0
        text_count = 0
        combined_extent = QgsRectangle()

        try:
            for display_name, info in sorted(layer_info.items()):
                tbl = info["table"]
                subset = info["subset"]
                qml = info.get("qml_path", "")
                is_text = info.get("is_text", False)

                uri = f"{gpkg_path}|layername={tbl}"
                # every CAD class shares the entities table, so without this the layer takes the geometry type of whichever entity OGR saw first and the type-filtered tools stop offering it
                gtype_token = info.get("geometrytype", "")
                if gtype_token:
                    uri += f"|geometrytype={gtype_token}"
                lyr = QgsVectorLayer(uri, display_name, "ogr")
                if not lyr.isValid():
                    self._log(self.tr("  Invalid: {0}").format(display_name))
                    continue

                lyr.setSubsetString(subset)

                if qml and os.path.isfile(qml):
                    lyr.loadNamedStyle(qml)

                # text only shows up at large scale
                if is_text:
                    lyr.setScaleBasedVisibility(True)
                    lyr.setMinimumScale(5000)
                    lyr.setMaximumScale(1)

                QgsProject.instance().addMapLayer(lyr, False)
                node = group.addLayer(lyr)
                loaded += 1

                if is_text and node:
                    node.setItemVisibilityChecked(False)
                    text_count += 1

                ext = lyr.extent()
                if ext and not ext.isNull() and ext.width() > 0:
                    if combined_extent.isNull():
                        combined_extent = QgsRectangle(ext)
                    else:
                        combined_extent.combineExtentWith(ext)

        finally:
            canvas.freeze(False)

        if not combined_extent.isNull() and combined_extent.width() > 0:
            combined_extent.scale(1.05)
            canvas.setExtent(combined_extent)
        canvas.refresh()

        if text_count:
            self._log(self.tr(
                "  {0} text layers hidden "
                "(visible below 1:5000)").format(text_count))

        return loaded

    # --- close ---

    def _shutdown_task(self):
        # detach the progress signal so a running task can't poke destroyed widgets
        if self._task:
            try:
                self._task.progressChanged.disconnect()
            except (TypeError, RuntimeError):
                pass
            try:
                if self._task.status() == self._task.Running:
                    self._task.cancel()
            except RuntimeError:
                pass  # C++ object already deleted

    def reject(self):
        # Close and Esc never emit a QCloseEvent, so cancel here too or a closed dialog leaves the conversion running
        self._shutdown_task()
        super().reject()

    def closeEvent(self, event):
        self._shutdown_task()
        super().closeEvent(event)
