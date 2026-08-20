# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Free-function UI helpers shared by BaseDialog and any other QWidget, so panels get the same behavior without inheriting anything."""

import os
from typing import Optional

from qgis.PyQt.QtCore import QUrl  # type: ignore
from qgis.PyQt.QtGui import QDesktopServices  # type: ignore
from qgis.PyQt.QtWidgets import (  # type: ignore
    QCheckBox, QComboBox, QDoubleSpinBox, QGroupBox, QLineEdit, QMessageBox,
    QRadioButton, QSpinBox, QTabWidget, QWidget,
)
from qgis.core import QgsMessageLog, Qgis  # type: ignore

from ..i18n import tr as _tr

PLUGIN_NAME = "Vernier"


def log_message(message: str, level=Qgis.MessageLevel.Info, plugin_name: str = PLUGIN_NAME):
    QgsMessageLog.logMessage(message, plugin_name, level=level)


def show_error(parent: QWidget, title: str, message: str, *,
               log: bool = True, plugin_name: str = PLUGIN_NAME):
    QMessageBox.critical(parent, title, message)
    if log:
        log_message(message, Qgis.MessageLevel.Critical, plugin_name)


def show_warning(parent: QWidget, title: str, message: str, *,
                 log: bool = True, plugin_name: str = PLUGIN_NAME):
    QMessageBox.warning(parent, title, message)
    if log:
        log_message(message, Qgis.MessageLevel.Warning, plugin_name)


def show_info(parent: QWidget, title: str, message: str, *,
              log: bool = False, plugin_name: str = PLUGIN_NAME):
    QMessageBox.information(parent, title, message)
    if log:
        log_message(message, Qgis.MessageLevel.Info, plugin_name)


# long enough to reach the "More" button, still short enough that repeated runs of an iterative tool do not stack bars down the canvas
DETAILED_SUCCESS_DURATION = 10


def show_success(message: str, *, iface=None, duration: int = 5,
                 details: Optional[str] = None):
    """Green message bar. Details land behind a "More" button and in the Vernier log, so nothing depends on catching the bar in time."""
    if details:
        log_message(f"{message}\n{details}")
    if not iface:
        return
    bar = iface.messageBar()
    if details:
        bar.pushMessage(_tr("Success"), message, details, Qgis.MessageLevel.Success,
                        DETAILED_SUCCESS_DURATION)
    else:
        bar.pushMessage(_tr("Success"), message, level=Qgis.MessageLevel.Success,
                        duration=duration)


def show_notice(message: str, *, iface=None, duration: int = 5,
                plugin_name: str = PLUGIN_NAME):
    """Neutral message bar for something that is neither a result nor a problem - a run the user canceled, mostly."""
    log_message(message, Qgis.MessageLevel.Info, plugin_name)
    if iface:
        iface.messageBar().pushMessage(
            plugin_name, message, level=Qgis.MessageLevel.Info, duration=duration)


def features_phrase(count: int) -> str:
    """"1 feature" or "N features" - tr() has no plural form outside a numerus context."""
    if count == 1:
        return _tr("1 feature")
    return _tr("{0} features").format(count)


def layers_phrase(count: int) -> str:
    if count == 1:
        return _tr("1 layer")
    return _tr("{0} layers").format(count)


def show_export_done(parent: QWidget, summary: str,
                     file_path: Optional[str] = None):
    """Export-finished dialog. file_path may be a file, a folder or None - the extra button follows."""
    msg = QMessageBox(parent)
    msg.setIcon(QMessageBox.Icon.Information)
    msg.setWindowTitle(_tr("Export finished"))
    msg.setText(summary)
    msg.addButton(_tr("OK"), QMessageBox.ButtonRole.AcceptRole)
    btn_open = None
    if file_path and os.path.isfile(file_path):
        btn_open = msg.addButton(_tr("Open file"), QMessageBox.ButtonRole.ActionRole)
    elif file_path and os.path.isdir(file_path):
        btn_open = msg.addButton(_tr("Open folder"), QMessageBox.ButtonRole.ActionRole)
    msg.exec()
    if btn_open and msg.clickedButton() == btn_open:
        # QDesktopServices rather than os.startfile, this one works on Linux/macOS too
        QDesktopServices.openUrl(QUrl.fromLocalFile(file_path))


# --- remembered widget values ---

def _as_bool(value) -> bool:
    # QSettings hands booleans back as strings on some platforms
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return bool(value)


def read_widget_state(widget, by_text: bool = False):
    """Serializable value of a widget a dialog remembers, None for a type this doesn't handle."""
    if isinstance(widget, (QCheckBox, QRadioButton, QGroupBox)):
        return bool(widget.isChecked())
    if isinstance(widget, QSpinBox):
        return int(widget.value())
    if isinstance(widget, QDoubleSpinBox):
        return float(widget.value())
    if isinstance(widget, QComboBox):
        return widget.currentText() if by_text else int(widget.currentIndex())
    if isinstance(widget, QLineEdit):
        return widget.text()
    if isinstance(widget, QTabWidget):
        return int(widget.currentIndex())
    return None


def apply_widget_state(widget, value, by_text: bool = False):
    """Put a stored value back. Every branch coerces, and anything that does not fit the widget - a field the layer lacks, an index past the end - is left alone rather than forced."""
    if isinstance(widget, (QCheckBox, QRadioButton, QGroupBox)):
        widget.setChecked(_as_bool(value))
    elif isinstance(widget, QSpinBox):
        widget.setValue(int(float(value)))
    elif isinstance(widget, QDoubleSpinBox):
        widget.setValue(float(value))
    elif isinstance(widget, QComboBox):
        if by_text:
            index = widget.findText(str(value))
        else:
            index = int(float(value))
        if 0 <= index < widget.count():
            widget.setCurrentIndex(index)
    elif isinstance(widget, QLineEdit):
        widget.setText(str(value))
    elif isinstance(widget, QTabWidget):
        index = int(float(value))
        if 0 <= index < widget.count():
            widget.setCurrentIndex(index)
