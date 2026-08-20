# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The command bar as a QGIS panel: it has to be movable, tabbable and closable, and once the user closes it nothing may drag it back until they ask. Needs a GUI-enabled QgsApplication, offscreen is fine. iface is faked down to the handful of members CommandBar and StatusStrip touch."""

import os
import sys
import unittest

_PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _PLUGINS_DIR not in sys.path:
    sys.path.insert(0, _PLUGINS_DIR)

from qgis.core import QgsApplication  # noqa: E402  # type: ignore
from qgis.gui import QgsMapCanvas, QgsMessageBar  # noqa: E402  # type: ignore
from qgis.PyQt.QtCore import (  # noqa: E402  # type: ignore
    QEvent, QObject, Qt, pyqtSignal,
)
from qgis.PyQt.QtGui import QKeyEvent  # noqa: E402  # type: ignore
from qgis.PyQt.QtWidgets import (  # noqa: E402  # type: ignore
    QDockWidget, QMainWindow,
)

from vernier.command_bar import CommandBar  # noqa: E402
from vernier.services import settings_service  # noqa: E402

QGS = None


def setUpModule():
    global QGS
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QGS = QgsApplication([], True)
    QGS.initQgis()


def tearDownModule():
    QGS.exitQgis()


class _FakeIface(QObject):
    """Only what CommandBar and StatusStrip actually call."""

    currentLayerChanged = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self._window = QMainWindow()
        self._canvas = QgsMapCanvas(self._window)
        self._bar = QgsMessageBar(self._window)

    def mainWindow(self):
        return self._window

    def mapCanvas(self):
        return self._canvas

    def messageBar(self):
        return self._bar

    def activeLayer(self):
        return None


class _FakePlugin:
    def __init__(self, iface):
        self.iface = iface
        self._cad_grid = None


class CommandBarPanelTests(unittest.TestCase):

    def setUp(self):
        settings_service.set_("cad_mode/panel_visible", True)
        self.iface = _FakeIface()
        self.bar = CommandBar(_FakePlugin(self.iface))
        self.iface.mainWindow().addDockWidget(
            Qt.DockWidgetArea.BottomDockWidgetArea, self.bar)
        self.iface.mainWindow().show()
        QgsApplication.processEvents()

    def tearDown(self):
        self.bar.cleanup()
        self.bar.deleteLater()
        settings_service.set_("cad_mode/panel_visible", True)

    def test_behaves_like_a_qgis_panel(self):
        features = self.bar.features()
        flags = QDockWidget.DockWidgetFeature
        self.assertTrue(features & flags.DockWidgetClosable,
                        "no X, so the panel cannot be dismissed")
        self.assertTrue(features & flags.DockWidgetMovable,
                        "not movable, so it cannot be tabbed with Log "
                        "Messages or the Python console")
        # the autocomplete popup is a child of the main window and would be clipped out of a floating panel
        self.assertFalse(features & flags.DockWidgetFloatable)
        self.assertIsNotNone(self.bar.toggleViewAction())

    def test_closing_it_is_remembered_and_survives_a_settle_pass(self):
        self.bar.apply_settings(True, True)
        self.assertTrue(self.bar.isVisible())

        self.bar.close()  # the panel's own X
        QgsApplication.processEvents()
        self.assertFalse(self.bar.isVisible())
        self.assertFalse(settings_service.get("cad_mode/panel_visible"))

        # startup and project-load both re-apply the settings, neither may undo the user
        self.bar.apply_settings(True, True)
        QgsApplication.processEvents()
        self.assertFalse(self.bar.isVisible())

    def test_typing_does_not_resurrect_a_closed_panel(self):
        self.bar.apply_settings(True, True)
        self.bar.close()
        QgsApplication.processEvents()
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_B,
                          Qt.KeyboardModifier.NoModifier, "b")
        self.bar.eventFilter(self.iface.mapCanvas(), event)
        QgsApplication.processEvents()
        self.assertFalse(self.bar.isVisible())

    def test_panels_menu_brings_it_back(self):
        self.bar.apply_settings(True, True)
        self.bar.close()
        QgsApplication.processEvents()
        self.bar.toggleViewAction().trigger()
        QgsApplication.processEvents()
        self.assertTrue(self.bar.isVisible())
        self.assertTrue(settings_service.get("cad_mode/panel_visible"))

    def test_disabling_cad_mode_is_not_a_user_close(self):
        self.bar.apply_settings(True, True)
        self.bar.apply_settings(False, False)
        QgsApplication.processEvents()
        self.assertFalse(self.bar.isVisible())
        self.assertTrue(settings_service.get("cad_mode/panel_visible"))
        self.bar.apply_settings(True, True)
        QgsApplication.processEvents()
        self.assertTrue(self.bar.isVisible())


if __name__ == "__main__":
    unittest.main()
