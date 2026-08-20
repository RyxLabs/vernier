# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Base class for every Vernier dialog - message helpers, layer validation, the standard layer picker, progress-wired Processing runs, geometry persistence and remembered widget values."""

from typing import Optional

from qgis.PyQt.QtCore import Qt, QSettings  # type: ignore
from qgis.PyQt.QtWidgets import (  # type: ignore
    QApplication, QCheckBox, QDialog, QGroupBox, QHBoxLayout, QMessageBox,
    QProgressBar, QPushButton, QVBoxLayout,
)
from qgis.core import (  # type: ignore
    Qgis, QgsMapLayerProxyModel, QgsProcessingFeatureSourceDefinition,
    QgsProcessingFeedback, QgsVectorLayer,
)
from qgis.gui import QgsMapLayerComboBox  # type: ignore

from . import _ui_helpers
from ..i18n import tr as _tr
from ..services import dialog_memory


class BaseDialog(QDialog):
    # sizing only, no colors - the user's QGIS theme wins
    STYLESHEET = """
        QGroupBox {
            font-weight: bold;
            margin-top: 14px;
            padding: 12px 8px 8px 8px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 6px;
        }
        QPushButton {
            min-height: 26px;
            min-width: 80px;
            padding: 4px 14px;
        }
    """

    def tr(self, text: str) -> str:
        # one "Vernier" context for the whole plugin, see i18n.py. QObject.tr would use the class name instead and these strings would land outside it
        return _tr(text)

    def __init__(self, iface=None, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.plugin_name = _ui_helpers.PLUGIN_NAME
        self.setStyleSheet(self.STYLESHEET)
        self._geometry_key = f"Vernier/{type(self).__name__}/geometry"
        self._processing = False
        self._remembered = []

    def showEvent(self, event):
        """Restore window size/position from the previous session."""
        super().showEvent(event)
        geom = QSettings().value(self._geometry_key)
        if geom:
            self.restoreGeometry(geom)

    def _save_geometry(self):
        try:
            QSettings().setValue(self._geometry_key, self.saveGeometry())
        except RuntimeError:
            pass  # widget already destroyed

    def done(self, result):
        """Save geometry on every close path - accept()/reject() call done() directly and never send a QCloseEvent, so closeEvent alone loses the size."""
        self._save_geometry()
        super().done(result)

    def reject(self):
        # Escape during a Processing run pumps events, don't yank the dialog out from under the algorithm
        if self._processing:
            return
        super().reject()

    def closeEvent(self, event):
        if self._processing:
            event.ignore()
            return
        self._save_geometry()
        super().closeEvent(event)

    # --- remembered widget values ---

    def remember(self, key: str, widget, by_text: bool = False):
        """Register a widget whose value survives between sessions. by_text stores a combo's text rather than its index, for lists built out of layer fields, where the index means nothing next time."""
        self._remembered.append((key, widget, by_text))

    def restore_remembered(self):
        """Apply the stored values. Call it once the widgets exist and their lists are populated - a combo restored before it has items has nothing to select."""
        name = type(self).__name__
        for key, widget, by_text in self._remembered:
            stored = dialog_memory.load(name, key)
            if stored is None:
                continue
            try:
                _ui_helpers.apply_widget_state(widget, stored, by_text)
            except (TypeError, ValueError, RuntimeError):
                pass  # stored value does not fit this widget

    def save_remembered(self):
        """Store the current values. Call it when the tool actually runs, not on close - a dialog someone opened and cancelled shouldn't overwrite the settings that worked last time."""
        name = type(self).__name__
        for key, widget, by_text in self._remembered:
            try:
                value = _ui_helpers.read_widget_state(widget, by_text)
            except RuntimeError:
                continue  # widget already destroyed
            if value is not None:
                dialog_memory.save(name, key, value)

    # --- messages ---

    def show_error(self, title: str, message: str, log: bool = True):
        _ui_helpers.show_error(self, title, message, log=log)

    def show_warning(self, title: str, message: str, log: bool = True):
        _ui_helpers.show_warning(self, title, message, log=log)

    def show_info(self, title: str, message: str, log: bool = False):
        _ui_helpers.show_info(self, title, message, log=log)

    def show_success(self, message: str, duration: int = 5,
                     details: Optional[str] = None):
        _ui_helpers.show_success(message, iface=self.iface, duration=duration,
                                 details=details)

    def show_export_done(self, summary: str, file_path: Optional[str] = None):
        _ui_helpers.show_export_done(self, summary, file_path)

    def log_message(self, message: str, level=Qgis.Info):
        _ui_helpers.log_message(message, level)

    # --- result messages ---

    def show_tool_notice(self, message: str):
        """Neutral message bar - a canceled run and the like."""
        _ui_helpers.show_notice(message, iface=self.iface)

    def show_tool_warning(self, message: str):
        """Warning box titled with the tool's own name."""
        self.show_warning(self.windowTitle(), message)

    def show_tool_error(self, message: str):
        """Error box titled with the tool's own name."""
        self.show_error(self.windowTitle(), message)

    def show_tool_failure(self, error):
        """Error box for a failed run. The title already names the tool, so the body carries only the reason."""
        self.show_error(self.windowTitle(),
                        self.tr("The operation failed:\n\n{0}").format(error))

    def show_layer_created(self, layer, details: Optional[str] = None):
        """Success message for a tool that publishes one result layer."""
        count = layer.featureCount()
        if count < 0:
            # some providers don't know their count up front
            message = self.tr('Layer "{0}" created.').format(layer.name())
        else:
            message = self.tr('Layer "{0}" created - {1}.').format(
                layer.name(), _ui_helpers.features_phrase(count))
        self.show_success(message, details=details)

    def show_layers_created(self, count: int, details: Optional[str] = None):
        """Same message for the tools that publish several layers at once."""
        self.show_success(
            self.tr("{0} created.").format(_ui_helpers.layers_phrase(count)),
            details=details)

    def confirm_action(self, title: str, message: str) -> bool:
        reply = QMessageBox.question(
            self, title, message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        return reply == QMessageBox.StandardButton.Yes

    # --- layers ---

    def validate_layer(self, layer: Optional[QgsVectorLayer],
                       layer_name: str = "layer",
                       check_features: bool = False) -> bool:
        """True when the layer is a usable vector layer, optionally non-empty - shows the error box itself, so callers only branch on the result."""
        if not layer:
            self.show_error(self.tr("Layer error"),
                            self.tr("No {0} selected or found.").format(layer_name))
            return False
        if not isinstance(layer, QgsVectorLayer):
            self.show_error(self.tr("Layer error"),
                            self.tr("The chosen {0} is not a vector layer.")
                            .format(layer_name))
            return False
        if not layer.isValid():
            self.show_error(self.tr("Layer error"),
                            self.tr("The chosen {0} could not be loaded. "
                                    "Check that its source file or connection "
                                    "is available.")
                            .format(layer_name))
            return False
        if check_features and layer.featureCount() == 0:
            self.show_error(self.tr("Layer error"),
                            self.tr("The chosen {0} contains no features.")
                            .format(layer_name))
            return False
        return True

    def preselect_active_layer(self, combo) -> bool:
        """Preselect the active layer, but only if it passed the combo's filter - setLayer() on a filtered-out layer clears the selection instead."""
        active = self.iface.activeLayer() if self.iface else None
        if not isinstance(active, QgsVectorLayer):
            return False
        for i in range(combo.count()):
            lyr = combo.layer(i)
            if lyr is not None and lyr.id() == active.id():
                combo.setLayer(active)
                return True
        return False

    def create_layer_group(self, label_text,
                           filter_type=QgsMapLayerProxyModel.VectorLayer,
                           select_active=True):
        """QGroupBox with a layer combo and a "selected features only" box, returns (group, combo, checkbox). Pass select_active=False for the second combo of a two-layer op."""
        group = QGroupBox(label_text)
        layout = QVBoxLayout()
        combo = QgsMapLayerComboBox()
        combo.setFilters(filter_type)
        checkbox = QCheckBox(self.tr("Selected features only"))
        checkbox.setEnabled(False)
        layout.addWidget(combo)
        layout.addWidget(checkbox)
        group.setLayout(layout)

        # follow the current layer's selection too, not just layer swaps - a box left ticked after the user clears the canvas selection would send an empty "selected only" request
        tracked = {"layer": None}

        def _update_checkbox(*_args):
            try:
                lyr = combo.currentLayer()
                has_sel = lyr and lyr.selectedFeatureCount() > 0
                checkbox.setEnabled(bool(has_sel))
                if not has_sel:
                    checkbox.setChecked(False)
            except RuntimeError:
                pass  # widgets already deleted

        def _untrack():
            old = tracked["layer"]
            tracked["layer"] = None
            if old is not None:
                try:
                    old.selectionChanged.disconnect(_update_checkbox)
                except (TypeError, RuntimeError):
                    pass  # already disconnected or layer deleted

        def _on_layer_changed(*_args):
            _untrack()
            lyr = combo.currentLayer()
            tracked["layer"] = lyr
            if lyr is not None:
                lyr.selectionChanged.connect(_update_checkbox)
            _update_checkbox()

        combo.layerChanged.connect(_on_layer_changed)
        # dialogs linger parented to the main window after exec(), so drop the connection on close or it keeps firing into a hidden dialog all session
        self.finished.connect(lambda *_: _untrack())
        if select_active:
            self.preselect_active_layer(combo)
        _on_layer_changed()
        return group, combo, checkbox

    @staticmethod
    def processing_source(layer, selected_only: bool):
        """Processing input that honors "selected features only" without copying the selection into a temp memory layer."""
        if selected_only:
            return QgsProcessingFeatureSourceDefinition(
                layer.id(), selectedFeaturesOnly=True)
        return layer

    # --- processing ---

    def create_button_row(self, ok_text=None, cancel_text=None):
        """Standard OK/Close button row, as (layout, ok_btn, cancel_btn) so dialogs that enable/disable the buttons get real references instead of fishing them back out of the layout."""
        layout = QHBoxLayout()
        layout.addStretch()
        ok_btn = QPushButton(ok_text or self.tr("OK"))
        cancel_btn = QPushButton(cancel_text or self.tr("Close"))
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(ok_btn)
        layout.addWidget(cancel_btn)
        return layout, ok_btn, cancel_btn

    def create_progress_bar(self):
        """Hidden QProgressBar, add it to the dialog layout."""
        bar = QProgressBar()
        bar.setVisible(False)
        bar.setTextVisible(True)
        bar.setFormat("%p%")
        return bar

    def run_processing(self, algorithm_id, params, progress_bar=None):
        """Run a Processing algorithm and return processing.run()'s result dict."""
        # deferred - processing is itself a plugin, a module-level import would tie our load order to it
        import processing  # type: ignore

        feedback = QgsProcessingFeedback()

        if progress_bar:
            progress_bar.setValue(0)
            progress_bar.setVisible(True)

            def _on_progress(value):
                progress_bar.setValue(int(value))
                QApplication.processEvents()

            feedback.progressChanged.connect(_on_progress)

        # the progress callback pumps processEvents, so a second click mid-run would re-enter accept() and produce duplicate outputs. freeze the buttons, reject/closeEvent bounce on _processing
        self._processing = True
        frozen = [b for b in self.findChildren(QPushButton) if b.isEnabled()]
        for button in frozen:
            button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = processing.run(algorithm_id, params, feedback=feedback)
            if progress_bar:
                progress_bar.setValue(100)
            return result
        finally:
            QApplication.restoreOverrideCursor()
            self._processing = False
            for button in frozen:
                button.setEnabled(True)
            if progress_bar:
                progress_bar.setVisible(False)
