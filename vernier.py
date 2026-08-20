# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The plugin class - toolbar, menu and action wiring, all built from the catalog in features.py. A catalog entry with no matching method here is skipped with a log warning."""

import os

from qgis.PyQt.QtCore import (  # type: ignore
    QCoreApplication, QLocale, Qt, QTimer, QTranslator,
)
from qgis.PyQt.QtGui import QBrush, QColor, QIcon, QPalette  # type: ignore
from qgis.PyQt.QtWidgets import (  # type: ignore
    QAction, QApplication, QMenu, QToolButton,
)
from qgis.core import (  # type: ignore
    Qgis, QgsApplication, QgsMessageLog, QgsProject, QgsSettings,
)

from . import features
from .services import settings_service
from .services.autosave_service import AutosaveService
from .tools import duplicates_tool, snap_tool
from .tools.area_readout import AreaReadout

PLUGIN_NAME = "Vernier"

# QGIS ships its UI themes as Qt stylesheets, which never touch QPalette, so these only match by name. the palette check in _dark_ui() picks up OS dark mode and custom themes instead - neither test works alone
DARK_THEMES = frozenset(("night mapping", "blend of gray"))


class Vernier:

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.actions = {}
        self.toolbar = None
        self.menu_name = "&Vernier"
        self.translator = None
        self.area_readout = None
        self.autosave = None
        self.topology_panel = None
        self.detach_panel = None
        self.command_bar = None
        self._cad_grid = None
        self._cad_active = False
        self._original_canvas_color = None
        self._want_dark_canvas = False
        self._cad_ready_done = False
        self._init_translator()

    # --- i18n ---

    def _init_translator(self):
        locale = QgsSettings().value("locale/userLocale", "")
        if not locale:
            locale = QLocale().name()
        qm_path = os.path.join(
            self.plugin_dir, "i18n", f"vernier_{locale[:2]}.qm")
        if os.path.exists(qm_path):
            self.translator = QTranslator()
            if self.translator.load(qm_path):
                QCoreApplication.installTranslator(self.translator)

    def tr(self, text: str) -> str:
        return QCoreApplication.translate("Vernier", text)

    # --- icon theme ---

    def _dark_ui(self) -> bool:
        """True when the toolbar sits on a dark background."""
        if (QgsApplication.themeName() or "").strip().lower() in DARK_THEMES:
            return True
        palette = QApplication.instance().palette()
        return palette.color(QPalette.ColorRole.Window).lightness() < 128

    def _icons_dir(self) -> str:
        """Icon folder for the current theme. Resolved once in initGui(), so a mid-session theme change lands on the next QGIS start."""
        base = os.path.join(self.plugin_dir, "icons")
        return os.path.join(base, "dark") if self._dark_ui() else base

    # --- gui lifecycle ---

    def initGui(self):
        self.toolbar = self.iface.addToolBar(PLUGIN_NAME)
        self.toolbar.setObjectName("VernierToolbar")
        icons_dir = self._icons_dir()

        for feat in features.CATALOG:
            handler = getattr(self, feat.method, None)
            if not callable(handler):
                QgsMessageLog.logMessage(
                    f"Catalog entry '{feat.method}' has no implementation "
                    "- skipped.", PLUGIN_NAME, level=Qgis.MessageLevel.Warning)
                continue
            icon = QIcon(os.path.join(icons_dir, feat.icon))
            action = QAction(icon, self.tr(feat.label),
                             self.iface.mainWindow())
            # rich text so the name goes bold with the shortcut beside it and the hint under, icon-only toolbar buttons never show their name otherwise
            tooltip = f"<b>{self.tr(feat.label)}</b>"
            if feat.shortcut:
                tooltip += f" ({feat.shortcut})"
            tooltip += f"<br>{self.tr(feat.hint)}"
            action.setToolTip(tooltip)
            # kept so autosave can append its state without losing the shared name/hint structure
            action.setProperty("base_tooltip", tooltip)
            if feat.shortcut:
                action.setShortcut(feat.shortcut)
            if feat.checkable:
                action.setCheckable(True)
            action.triggered.connect(handler)
            self.actions[feat.method] = action

        for method in features.TOOLBAR_LEADING:
            if method in self.actions:
                self.toolbar.addAction(self.actions[method])

        for group_label, group_icon, members in features.TOOLBAR_GROUPS:
            button = QToolButton(self.toolbar)
            button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            button.setIcon(QIcon(os.path.join(icons_dir, group_icon)))
            button.setText(self.tr(group_label))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setToolTip(self.tr(group_label))
            menu = QMenu(button)
            # menus swallow action tooltips by default and the hints are worth having on the dropdown entries too
            menu.setToolTipsVisible(True)
            for member in members:
                if member is None:
                    menu.addSeparator()
                elif member in self.actions:
                    menu.addAction(self.actions[member])
            button.setMenu(menu)
            self.toolbar.addWidget(button)

        for method in features.TOOLBAR_TRAILING:
            if method in self.actions:
                self.toolbar.addAction(self.actions[method])

        for method in features.MENU:
            if method in self.actions:
                self.iface.addPluginToVectorMenu(
                    self.menu_name, self.actions[method])

        # keep the toggle in sync when snapping gets flipped elsewhere - native toolbar, S key, project load
        self._safe_reconnect(
            QgsProject.instance().snappingConfigChanged,
            self._sync_snap_action)
        self._sync_snap_action()

        # dispose any prior instance first - Plugin Reloader can re-run initGui without unload, and an orphaned readout keeps its label and layer signals alive forever
        if self.area_readout is not None:
            self.area_readout.cleanup()
        self.area_readout = AreaReadout(self.iface)

        # panels are built lazily on first open, only Plugin Reloader leftovers need disposing here
        if self.topology_panel is not None:
            self._dispose_topology_panel()
        if self.detach_panel is not None:
            self._dispose_detach_panel()

        if self.autosave is not None:
            self.autosave.cleanup()
        self.autosave = AutosaveService(self.iface)
        # project open/new decides whether the timer runs and keeps the toolbar toggle on the per-project state
        self._safe_reconnect(
            QgsProject.instance().readProject, self._on_project_for_autosave)
        self._safe_reconnect(
            QgsProject.instance().cleared, self._on_project_for_autosave)
        self.autosave.on_project_opened()
        self._sync_autosave_action()

        # off by default. dispose any prior command bar first, a leaked application-level event filter double-handles every keystroke
        if self.command_bar is not None:
            self._dispose_command_bar()
        if self._cad_grid is not None:
            self._cad_grid.cleanup()
            self._cad_grid = None
        # on a cold QGIS launch the canvas isn't ready in initGui so defer, on a reload QGIS is already up and the 0 ms timer fires next tick
        self._cad_ready_done = False
        self._safe_reconnect(self.iface.initializationCompleted,
                             self._on_qgis_ready_cad)
        QTimer.singleShot(0, self._on_qgis_ready_cad)

        if not settings_service.get("general/welcome_shown"):
            settings_service.set_("general/welcome_shown", True)
            QTimer.singleShot(1500, self._show_welcome)

    def unload(self):
        if self.command_bar is not None:
            self._dispose_command_bar()
        if self._cad_grid is not None:
            self._cad_grid.cleanup()
            self._cad_grid = None
        if self._original_canvas_color is not None:
            try:
                # give the canvas back, but only if CAD Mode actually owned it or a transparent leftover needs repairing
                if (self._cad_active or
                        self.iface.mapCanvas().canvasColor().alpha() == 0):
                    self._want_dark_canvas = False
                    self._cad_active = False
                    self._reapply_canvas_scheme()
            except RuntimeError:
                pass
        try:
            self.iface.initializationCompleted.disconnect(
                self._on_qgis_ready_cad)
        except TypeError:
            pass
        try:
            QgsProject.instance().readProject.disconnect(
                self._on_project_read_cad)
        except TypeError:
            pass
        if self.topology_panel is not None:
            self._dispose_topology_panel()
        if self.detach_panel is not None:
            self._dispose_detach_panel()
        if self.autosave is not None:
            self.autosave.cleanup()
            self.autosave = None
        for signal in (QgsProject.instance().readProject,
                       QgsProject.instance().cleared):
            try:
                signal.disconnect(self._on_project_for_autosave)
            except TypeError:
                pass
        if self.area_readout is not None:
            self.area_readout.cleanup()
            self.area_readout = None
        try:
            QgsProject.instance().snappingConfigChanged.disconnect(
                self._sync_snap_action)
        except TypeError:
            pass
        for action in self.actions.values():
            self.iface.removePluginVectorMenu(self.menu_name, action)
            # actions are parented to the main window and hold a handler bound to this plugin, so without an explicit delete every load/unload cycle leaks a full set plus the plugin instance behind them
            try:
                action.triggered.disconnect()
            except TypeError:
                pass
            action.setParent(None)
            action.deleteLater()
        self.actions.clear()
        if self.toolbar is not None:
            del self.toolbar
            self.toolbar = None

    def _dispose_topology_panel(self):
        self.topology_panel.cleanup()
        self.iface.removeDockWidget(self.topology_panel)
        self.topology_panel.deleteLater()
        self.topology_panel = None

    def _dispose_detach_panel(self):
        self.detach_panel.cleanup()
        self.iface.removeDockWidget(self.detach_panel)
        self.detach_panel.deleteLater()
        self.detach_panel = None

    @staticmethod
    def _safe_reconnect(signal, slot):
        """Disconnect then connect, so Plugin Reloader can't stack handlers."""
        try:
            signal.disconnect(slot)
        except TypeError:
            pass
        signal.connect(slot)

    # --- tools ---

    def toggle_snapping(self):
        enabled = snap_tool.toggle()
        action = self.actions.get("toggle_snapping")
        if action:
            action.setChecked(enabled)

    def _sync_snap_action(self):
        action = self.actions.get("toggle_snapping")
        if action:
            action.setChecked(snap_tool.is_enabled())

    def toggle_autosave(self):
        if self.autosave is None:
            return
        if self.autosave.is_active():
            self.autosave.set_enabled(False)
            self._sync_autosave_action()
            return
        # first enable with no saved config, or nowhere to write - open the settings dialog instead of failing quietly
        if (not settings_service.get("autosave/configured")
                or not self.autosave.resolve_backup_dir()):
            self.open_autosave_settings()
            return
        self.autosave.set_enabled(True)
        self._sync_autosave_action()

    def open_autosave_settings(self):
        from .dialogs.autosave_dialog import AutosaveDialog
        self._exec_dialog(AutosaveDialog(
            self.autosave, iface=self.iface,
            parent=self.iface.mainWindow()))
        self._sync_autosave_action()

    def _on_project_for_autosave(self):
        if self.autosave is not None:
            self.autosave.on_project_opened()
            self._sync_autosave_action()

    def _sync_autosave_action(self):
        action = self.actions.get("toggle_autosave")
        if not action or self.autosave is None:
            return
        active = self.autosave.is_active()
        action.setChecked(active)
        if active:
            interval = settings_service.get("autosave/interval_minutes")
            if interval == 1:
                state = self.tr("Active - every minute")
            else:
                state = self.tr("Active - every {0} minutes").format(interval)
        else:
            state = self.tr("Off")
        # append to the catalog tooltip instead of replacing it, or this one button loses the hint every other action shows
        base = action.property("base_tooltip")
        action.setToolTip(f"{base}<br>{state}" if base else state)

    def toggle_cad_mode(self):
        enabled = not settings_service.get("cad_mode/enabled")
        settings_service.set_("cad_mode/enabled", enabled)
        if enabled:
            # asking for CAD Mode is asking for its panel, even if it was closed with its X last time
            settings_service.set_("cad_mode/panel_visible", True)
        self.apply_cad_mode(enabled)
        self._sync_cad_mode_action()

    def _sync_cad_mode_action(self):
        action = self.actions.get("toggle_cad_mode")
        if action:
            action.setChecked(settings_service.get("cad_mode/enabled"))

    def apply_cad_mode(self, enabled):
        """Apply CAD Mode and its cad_mode/* sub-toggles."""
        turning_on = enabled and not self._cad_active
        want_bar = enabled and settings_service.get("cad_mode/command_bar")
        want_strip = enabled and settings_service.get("cad_mode/status_strip")
        want_grid = enabled and settings_service.get("cad_mode/grid")
        self._want_dark_canvas = (
            enabled and settings_service.get("cad_mode/dark_canvas"))

        # built lazily on first enable, nobody who leaves CAD Mode off pays for the widget tree
        if (want_bar or want_strip) and self.command_bar is None:
            from .command_bar import CommandBar
            self.command_bar = CommandBar(self)
            self.iface.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea,
                                     self.command_bar)
        if self.command_bar is not None:
            self.command_bar.apply_settings(want_bar, want_strip)

        canvas = self.iface.mapCanvas()
        if want_grid and self._cad_grid is None:
            from .cad_grid import CadGrid
            self._cad_grid = CadGrid(canvas)
        elif not want_grid and self._cad_grid is not None:
            self._cad_grid.cleanup()
            self._cad_grid = None

        # while off the canvas isn't ours to touch, so only repaint when the mode is or was active, or a crashed session left it transparent
        if (enabled or self._cad_active
                or canvas.canvasColor().alpha() == 0):
            self._reapply_canvas_scheme()
        canvas.refresh()

        if turning_on and not snap_tool.is_enabled():
            snap_tool.toggle()  # apply the saved snapping profile once

        # a project load brings back whatever canvas color the file was saved with, so re-correct while the mode owns the canvas and adopt the new color as the one to give back
        if enabled:
            self._safe_reconnect(QgsProject.instance().readProject,
                                 self._on_project_read_cad)
        else:
            try:
                QgsProject.instance().readProject.disconnect(
                    self._on_project_read_cad)
            except TypeError:
                pass

        self._cad_active = enabled
        if self.command_bar is not None:
            # deferred so Qt finishes laying the shown widgets out first
            QTimer.singleShot(0, self.command_bar.refresh_strip)

    def toggle_cad_grid(self):
        """Toggle grid visibility, F9 or the 'grid' command. Returns the new state."""
        if self._cad_grid is None:
            return False
        new_state = not self._cad_grid.is_visible()
        self._cad_grid.set_visible(new_state)
        self._reapply_canvas_scheme()
        self.iface.mapCanvas().refresh()
        return new_state

    def _reapply_canvas_scheme(self, *_args):
        """Set the canvas colors for the current dark-canvas/grid state. The grid draws below the map render so it needs a transparent render background, and the dirty flag is snapshotted because setCanvasColor would otherwise earn the user a save prompt they never caused."""
        if self._original_canvas_color is None:
            return  # canvas not captured yet (before _on_qgis_ready_cad)
        from .cad_grid import CANVAS_BG
        canvas = self.iface.mapCanvas()
        project = QgsProject.instance()
        was_dirty = project.isDirty()
        grid_on = self._cad_grid is not None and self._cad_grid.is_visible()
        if self._want_dark_canvas or grid_on:
            canvas.setCanvasColor(QColor(0, 0, 0, 0))
            canvas.setBackgroundBrush(QBrush(
                CANVAS_BG if self._want_dark_canvas
                else self._original_canvas_color))
        else:
            canvas.setCanvasColor(self._original_canvas_color)
            canvas.setBackgroundBrush(QBrush(self._original_canvas_color))
        project.setDirty(was_dirty)

    def _capture_canvas_color(self):
        """Snapshot the canvas color CAD Mode owes back. A fully transparent canvas is leftover state from a crash or a project saved with the mode on, so capture white instead."""
        cc = self.iface.mapCanvas().canvasColor()
        self._original_canvas_color = (
            cc if cc.alpha() > 0 else QColor(255, 255, 255))

    def _on_project_read_cad(self):
        """Re-own the canvas of a project opened while CAD Mode is on - the color to give back is the new project's, not the one captured at startup, or turning the mode off paints a foreign color in and the restored dirty flag hides it."""
        self._capture_canvas_color()
        self._reapply_canvas_scheme()

    def _on_qgis_ready_cad(self):
        """Runs once, when the canvas is safe to touch. See initGui."""
        if self._cad_ready_done:
            return
        self._cad_ready_done = True
        try:
            self.iface.initializationCompleted.disconnect(
                self._on_qgis_ready_cad)
        except TypeError:
            pass
        if self._original_canvas_color is None:
            self._capture_canvas_color()
        try:
            # False still runs the leftover-repair path
            self.apply_cad_mode(settings_service.get("cad_mode/enabled"))
        except Exception as e:
            QgsMessageLog.logMessage(f"CAD Mode startup failed: {e}",
                                     PLUGIN_NAME, level=Qgis.MessageLevel.Critical)
        self._sync_cad_mode_action()
        # QGIS can still restore the last project and panel layout after initializationCompleted, so these settle passes re-apply the canvas
        QTimer.singleShot(500, self._cad_startup_settle)
        QTimer.singleShot(2000, self._cad_startup_settle)

    def _cad_startup_settle(self):
        """Deferred startup fixup, safe to call more than once."""
        try:
            enabled = settings_service.get("cad_mode/enabled")
            canvas = self.iface.mapCanvas()
            # never repaint a canvas the feature doesn't own, while off only a leftover transparent one gets repaired
            if enabled or canvas.canvasColor().alpha() == 0:
                self._reapply_canvas_scheme()
            if self.command_bar is not None:
                # re-enforce visibility, QGIS may have restored the panel from a previous session's window state
                self.command_bar.apply_settings(
                    enabled and settings_service.get("cad_mode/command_bar"),
                    enabled and settings_service.get("cad_mode/status_strip"))
                self.command_bar.refresh_strip()
        except Exception as e:
            QgsMessageLog.logMessage(f"CAD Mode settle: {e}",
                                     PLUGIN_NAME, level=Qgis.MessageLevel.Warning)

    def _show_welcome(self):
        try:
            self.iface.messageBar().pushMessage(
                self.tr("Vernier"),
                self.tr("Vernier is installed. The tools are in the Vernier "
                        "toolbar and under Vector > Vernier."),
                level=Qgis.MessageLevel.Info, duration=12)
        except RuntimeError:
            pass

    def _dispose_command_bar(self):
        self.command_bar.cleanup()
        self.iface.removeDockWidget(self.command_bar)
        self.command_bar.deleteLater()
        self.command_bar = None

    def _exec_dialog(self, dialog):
        """Run a modal dialog and let Qt destroy it on close - they're parented to the main window, so an undeleted one keeps its whole widget tree alive for the session, once per opening."""
        try:
            return dialog.exec()
        finally:
            dialog.deleteLater()

    def find_duplicates(self):
        duplicates_tool.run(self.iface)

    def remove_close_vertices(self):
        from .dialogs.remove_vertices_dialog import RemoveVerticesDialog
        self._exec_dialog(RemoveVerticesDialog(
            iface=self.iface, parent=self.iface.mainWindow()))

    def extract_centerline(self):
        from .dialogs.centerline_dialog import CenterlineDialog
        self._exec_dialog(CenterlineDialog(
            iface=self.iface, parent=self.iface.mainWindow()))

    def run_attribute_join(self):
        from .dialogs.attribute_join_dialog import AttributeJoinDialog
        self._exec_dialog(AttributeJoinDialog(
            iface=self.iface, parent=self.iface.mainWindow()))

    def run_spatial_join(self):
        from .dialogs.spatial_join_dialog import SpatialJoinDialog
        self._exec_dialog(SpatialJoinDialog(
            iface=self.iface, parent=self.iface.mainWindow()))

    def export_kmz(self):
        from .dialogs.kmz_export_dialog import KmzExportDialog
        self._exec_dialog(KmzExportDialog(
            iface=self.iface, parent=self.iface.mainWindow()))

    def open_detach(self):
        # built lazily, nobody who never splits polygons should pay for the widget tree, map tool and rubber bands
        from .detach_panel import DetachPanel
        if self.detach_panel is None:
            self.detach_panel = DetachPanel(self.iface)
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea,
                                     self.detach_panel)
        elif self.detach_panel.isVisible():
            # close() rather than hide, closeEvent clears the preview bands and puts the map tool back
            self.detach_panel.close()
            return
        else:
            self.detach_panel.show()
        self.detach_panel.raise_()

    def check_topology(self):
        # built lazily, nobody who never runs the validator should pay for the widget tree and rubber bands at startup
        from .topology_panel import TopologyPanel
        if self.topology_panel is None:
            self.topology_panel = TopologyPanel(self.iface)
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea,
                                     self.topology_panel)
        else:
            self.topology_panel.setVisible(
                not self.topology_panel.isVisible())
        if self.topology_panel.isVisible():
            self.topology_panel.raise_()

    def import_dxf(self):
        from .dialogs.dxf_import_dialog import DxfImportDialog
        self._exec_dialog(DxfImportDialog(
            iface=self.iface, parent=self.iface.mainWindow()))

    def export_dxf(self):
        from .dialogs.dxf_export_dialog import DxfExportDialog
        self._exec_dialog(DxfExportDialog(
            iface=self.iface, parent=self.iface.mainWindow()))

    def lines_to_polygons(self):
        from .dialogs.lines_to_polygons_dialog import LinesToPolygonsDialog
        self._exec_dialog(LinesToPolygonsDialog(
            iface=self.iface, parent=self.iface.mainWindow()))

    def open_style(self):
        from .dialogs.style_dialog import StyleDialog
        self._exec_dialog(StyleDialog(
            iface=self.iface, parent=self.iface.mainWindow()))

    def run_buffer(self):
        from .dialogs.buffer_dialog import BufferDialog
        self._exec_dialog(BufferDialog(
            iface=self.iface, parent=self.iface.mainWindow()))

    def run_intersection(self):
        from .dialogs.intersection_dialog import IntersectionDialog
        self._exec_dialog(IntersectionDialog(
            iface=self.iface, parent=self.iface.mainWindow()))

    def run_difference(self):
        from .dialogs.difference_dialog import DifferenceDialog
        self._exec_dialog(DifferenceDialog(
            iface=self.iface, parent=self.iface.mainWindow()))

    def run_dissolve(self):
        from .dialogs.dissolve_dialog import DissolveDialog
        self._exec_dialog(DissolveDialog(
            iface=self.iface, parent=self.iface.mainWindow()))

    def run_multipart_to_single(self):
        from .dialogs.multi2single_dialog import Multi2SingleDialog
        self._exec_dialog(Multi2SingleDialog(
            iface=self.iface, parent=self.iface.mainWindow()))

    def open_settings(self):
        from .dialogs.settings_dialog import SettingsDialog
        self._exec_dialog(SettingsDialog(
            iface=self.iface, parent=self.iface.mainWindow()))
        if self.area_readout is not None:
            self.area_readout.refresh()
        self.apply_cad_mode(settings_service.get("cad_mode/enabled"))
        self._sync_cad_mode_action()

    def open_help(self):
        from .dialogs.help_dialog import HelpDialog
        self._exec_dialog(HelpDialog(
            iface=self.iface, parent=self.iface.mainWindow()))
