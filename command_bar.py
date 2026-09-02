# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""CAD Mode command bar - typed commands with prefix matching, an autocomplete dropup and a global key redirect, plus a status strip with live layer/edit/snap/grid indicators."""

# the plugin has to call cleanup() from unload(). it uninstalls the application-level event filter, which would otherwise survive a reload and double-handle every keystroke

from collections import deque

from qgis.PyQt.Qsci import QsciScintillaBase  # type: ignore
from qgis.PyQt.QtCore import (  # type: ignore
    QEvent, QPoint, QPointF, Qt, pyqtSignal,
)
from qgis.PyQt.QtGui import QColor, QKeyEvent, QMouseEvent  # type: ignore
from qgis.PyQt.QtWidgets import (  # type: ignore
    QAbstractItemView, QAbstractSpinBox, QAction, QApplication, QComboBox,
    QDockWidget, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPlainTextEdit, QTextEdit, QToolButton, QVBoxLayout,
    QWidget,
)
from qgis.core import (  # type: ignore
    Qgis, QgsCsException, QgsFillSymbol,
    QgsMarkerLineSymbolLayer, QgsMarkerSymbol, QgsProject, QgsRasterLayer,
    QgsSimpleMarkerSymbolLayer, QgsSingleSymbolRenderer,
    QgsVectorLayer, QgsWkbTypes,
)
from qgis.gui import QgsMapTool  # type: ignore

from .services import settings_service

from .features import CATALOG
from .i18n import tr as _tr
from .tools import snap_tool
from .tools.area_readout import format_area, measure_area_sqm

HISTORY_SIZE = 50
_LOG_MAX_LINES = 200
_POPUP_ITEM_HEIGHT = 24
_POPUP_MAX_HEIGHT = 240
_POPUP_PADDING = 4

# lets a later session spot scratch layers made by "pl" without going by display name
_CAD_LAYER_FLAG = "vernier/cad_layer"

# the key redirect has to skip these. QAbstractSpinBox covers the date/time edits too, QsciScintillaBase the code editors - the Python console lives inside the main window as well
_TEXT_WIDGETS = (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox,
                 QsciScintillaBase)


# --- pure command logic, no Qt, so test_command_registry can import it headless ---

class CommandRegistry:
    """Commands registered with their whole alias tuple, resolved by prefix. Aliases of one command share a record, so they never make a prefix ambiguous; claiming an alias twice raises - that's a packaging bug, not a runtime one."""

    def __init__(self):
        # alias -> (label, callback, hint); aliases of one register() call share the record object, identity is what dedups them
        self._aliases = {}

    def register(self, aliases, label, callback, hint=""):
        record = (label, callback, hint)
        for alias in aliases:
            alias = alias.lower().strip()
            if not alias:
                raise ValueError(f"Empty command alias in {aliases!r}")
            if alias in self._aliases:
                raise ValueError(f"Command alias collision: '{alias}'")
            self._aliases[alias] = record

    def __contains__(self, alias):
        return alias in self._aliases

    def __len__(self):
        return len(self._aliases)

    def names(self):
        return sorted(self._aliases)

    def get(self, alias):
        """(label, callback, hint) for an exact alias, None otherwise."""
        return self._aliases.get(alias)

    def _prefix_names(self, text):
        """First matching alias of every command the prefix hits, in alias sort order."""
        found = {}
        for name in sorted(self._aliases):
            if name.startswith(text):
                found.setdefault(id(self._aliases[name]), name)
        return list(found.values())

    def resolve(self, text):
        """Resolve a typed command to ("exact", name, label, callback), ("ambiguous", [names]) or ("unknown",)."""
        text = text.lower().strip()
        if not text:
            return ("unknown",)
        record = self._aliases.get(text)
        if record is None:
            names = self._prefix_names(text)
            if not names:
                return ("unknown",)
            if len(names) > 1:
                return ("ambiguous", names)
            text = names[0]
            record = self._aliases[text]
        label, callback, _hint = record
        return ("exact", text, label, callback)

    def prefix_matches(self, text):
        """Deduplicated, sorted (name, label) prefix matches, for display."""
        text = text.lower().strip()
        if not text:
            return []
        return [(name, self._aliases[name][0])
                for name in self._prefix_names(text)]


class CommandHistory:
    """Typed-command history. previous() walks back, next() walks forward and returns "" past the newest entry so the caller can clear the input."""

    def __init__(self, maxlen=HISTORY_SIZE):
        self._entries = deque(maxlen=maxlen)
        self._index = -1  # -1 = not navigating

    def __len__(self):
        return len(self._entries)

    def entries(self):
        return list(self._entries)

    def append(self, text):
        self._entries.append(text)
        self._index = -1

    def reset(self):
        self._index = -1

    def is_navigating(self):
        return self._index != -1

    def previous(self):
        """Older entry for the Up key, None when there's no history."""
        if not self._entries:
            return None
        if self._index == -1:
            self._index = len(self._entries) - 1
        elif self._index > 0:
            self._index -= 1
        return self._entries[self._index]

    def next(self):
        """Newer entry for the Down key. "" once past the newest, None when not navigating."""
        if not self._entries or self._index == -1:
            return None
        if self._index < len(self._entries) - 1:
            self._index += 1
            return self._entries[self._index]
        self._index = -1
        return ""


# CLI-only verbs with no toolbar equivalent: (aliases, label, CommandBar method, hint). labels and hints are English source strings, translated at registration
BUILTIN_COMMANDS = (
    # digitizing
    (("pl", "pline"), "Add Feature", "_cmd_polyline",
     "Start editing on the active layer, enable snapping and begin "
     "digitizing (creates a polygon scratch layer if the project has none)"),
    (("c", "close"), "Close Sketch", "_cmd_close_sketch",
     "Finish the current sketch (same as a right click)"),
    # edit tools (QGIS built-in)
    (("mv", "move"), "Move Feature", "_cmd_move",
     "Move the selected feature (starts editing automatically)"),
    (("cp", "copy"), "Copy Feature", "_cmd_copy",
     "Copy the selected feature to a new location"),
    (("ve", "vertex", "edit", "pe"), "Vertex Editor", "_cmd_vertex",
     "Edit individual vertices (enables snapping automatically)"),
    (("ro", "rotate"), "Rotate Feature", "_cmd_rotate",
     "Rotate the selected feature"),
    (("del", "erase"), "Delete Features", "_cmd_delete",
     "Delete the selected features from the active layer (starts editing "
     "automatically)"),
    # save / redo
    (("s", "save"), "Save Edits", "_cmd_save",
     "Save (commit) edits on the active layer and keep editing"),
    (("redo",), "Redo", "_cmd_redo",
     "Restore the last undone edit"),
    # basemap
    (("bm", "basemap"), "Basemap", "_cmd_basemap",
     "Toggle the configured basemap under your layers (F8; URL in "
     "Settings > CAD Mode)"),
    # navigation
    (("ze",), "Zoom Extents", "_cmd_zoom_extents",
     "Zoom to the full extent of all layers"),
    (("zs",), "Zoom Selection", "_cmd_zoom_selected",
     "Zoom to the selected features"),
    (("pan",), "Pan", "_cmd_pan",
     "Activate the pan tool"),
    (("sel", "select"), "Select", "_cmd_select",
     "Activate the select tool"),
    (("i", "inspect"), "Identify", "_cmd_inspect",
     "Click a feature to see its attributes"),
    (("di", "dist", "measure"), "Measure Distance", "_cmd_measure",
     "Measure distances on the map"),
    # selection
    (("deselect", "none"), "Clear Selection", "_cmd_deselect",
     "Clear the current selection"),
    # info / display
    (("aa", "area"), "Area", "_cmd_area",
     "Log the area of the selected features"),
    (("grid",), "Grid", "_cmd_grid",
     "Toggle the CAD grid overlay"),
)


def build_registry(plugin, command_bar, catalog=CATALOG):
    """Build the registry from the catalog plus the built-ins. A catalog entry with no plugin method is skipped quietly - initGui already logged a warning for it."""
    registry = CommandRegistry()
    for feat in catalog:
        if not feat.aliases:
            continue
        callback = getattr(plugin, feat.method, None)
        if callback is None:
            continue
        registry.register(feat.aliases, _tr(feat.label), callback,
                          _tr(feat.hint))
    for aliases, label, method, hint in BUILTIN_COMMANDS:
        registry.register(aliases, _tr(label), getattr(command_bar, method),
                          _tr(hint))
    return registry


def _apply_cad_style(layer):
    """CAD-look style for the scratch layers "pl" makes - no fill, thin grey outline, blue square vertices, readable on the dark canvas."""
    marker = QgsSimpleMarkerSymbolLayer(
        Qgis.MarkerShape.Square, 2.0, 0.0,
        Qgis.ScaleMethod.ScaleDiameter,
        QColor(30, 100, 255), QColor(30, 100, 255),
    )
    marker_symbol = QgsMarkerSymbol()
    marker_symbol.changeSymbolLayer(0, marker)

    vertex_layer = QgsMarkerLineSymbolLayer()
    vertex_layer.setPlacements(Qgis.MarkerLinePlacement.Vertex)
    vertex_layer.setSubSymbol(marker_symbol)

    symbol = QgsFillSymbol.createSimple({
        "color": "0,0,0,0",
        "outline_color": "80,80,80,255",
        "outline_width": "0.35",
        "outline_style": "solid",
    })
    symbol.appendSymbolLayer(vertex_layer)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()


# --- widgets ---

def _takes_text_input(widget):
    """True when the focused widget consumes typed characters itself. Every branch of the global filter goes through here so the Delete, Escape and typing redirects can't drift apart."""
    if widget is None:
        return False
    if isinstance(widget, QComboBox):
        return widget.isEditable()
    return isinstance(widget, _TEXT_WIDGETS)


class _ClickableLabel(QLabel):
    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class StatusStrip(QWidget):
    """Status strip with live layer, edit, snap and grid state."""

    STRIP_STYLE = """
        StatusStrip {
            background: #1a1a1a;
            border-top: 1px solid #333333;
        }
        QLabel.strip-indicator {
            font-family: Consolas, 'Courier New', monospace;
            font-size: 8pt;
            padding: 2px 8px;
            border-right: 1px solid #333333;
        }
    """

    def __init__(self, command_bar, iface):
        super().__init__()
        self._command_bar = command_bar
        self._iface = iface
        self.setFixedHeight(24)
        self.setStyleSheet(self.STRIP_STYLE)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._layer_label = QLabel("-")
        self._layer_label.setProperty("class", "strip-indicator")
        self._layer_label.setToolTip(_tr("Active layer"))
        layout.addWidget(self._layer_label)

        self._edit_label = _ClickableLabel(
            _tr("✎ [F2] Edit: {0}").format("-"))
        self._edit_label.setProperty("class", "strip-indicator")
        self._edit_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._edit_label.setToolTip(
            _tr("Click or press F2 to toggle editing"))
        self._edit_label.clicked.connect(self._toggle_editing)
        layout.addWidget(self._edit_label)

        self._snap_label = _ClickableLabel(
            _tr("⊕ [F4] Snap: {0}").format("-"))
        self._snap_label.setProperty("class", "strip-indicator")
        self._snap_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._snap_label.setToolTip(
            _tr("Click or press F4 to toggle snapping"))
        self._snap_label.clicked.connect(self._toggle_snap)
        layout.addWidget(self._snap_label)

        self._grid_label = _ClickableLabel(
            _tr("⊞ [F9] Grid: {0}").format("-"))
        self._grid_label.setProperty("class", "strip-indicator")
        self._grid_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._grid_label.setToolTip(
            _tr("Click or press F9 to toggle the grid"))
        self._grid_label.clicked.connect(self._toggle_grid)
        layout.addWidget(self._grid_label)

        layout.addStretch(1)

        iface.currentLayerChanged.connect(self._refresh_layer)
        QgsProject.instance().snappingConfigChanged.connect(
            self._refresh_snap)
        QgsProject.instance().layersWillBeRemoved.connect(
            self._on_layers_removing)
        self._current_layer = None
        self.refresh_all()

    # --- refresh ---

    def refresh_all(self):
        self._refresh_layer(self._iface.activeLayer())
        self._refresh_snap()
        self._refresh_grid()

    def _refresh_layer(self, layer=None):
        if self._current_layer:
            try:
                self._current_layer.editingStarted.disconnect(
                    self._refresh_edit)
                self._current_layer.editingStopped.disconnect(
                    self._refresh_edit)
            except (TypeError, RuntimeError):
                pass

        self._current_layer = layer
        if layer and isinstance(layer, QgsVectorLayer):
            self._layer_label.setText(layer.name())
            self._layer_label.setStyleSheet(
                "color: #569cd6;" if layer.isEditable() else "color: #aaaaaa;")
            layer.editingStarted.connect(self._refresh_edit)
            layer.editingStopped.connect(self._refresh_edit)
            self._refresh_edit()
        else:
            self._show_no_layer()

    def _show_no_layer(self):
        self._layer_label.setText(_tr("- (no layer)"))
        self._layer_label.setStyleSheet("color: #666666;")
        self._edit_label.setText(_tr("✎ [F2] Edit: {0}").format("-"))
        self._edit_label.setStyleSheet("color: #666666;")

    def _refresh_edit(self):
        layer = self._current_layer
        if layer and isinstance(layer, QgsVectorLayer):
            if layer.isEditable():
                self._edit_label.setText(
                    _tr("✎ [F2] Edit: {0}").format(_tr("ON")))
                self._edit_label.setStyleSheet("color: #4ec9b0;")
                self._layer_label.setStyleSheet("color: #569cd6;")
            else:
                self._edit_label.setText(
                    _tr("✎ [F2] Edit: {0}").format(_tr("OFF")))
                self._edit_label.setStyleSheet("color: #888888;")
                self._layer_label.setStyleSheet("color: #aaaaaa;")

    def _refresh_snap(self):
        if snap_tool.is_enabled():
            self._snap_label.setText(
                _tr("⊕ [F4] Snap: {0}").format(_tr("ON")))
            self._snap_label.setStyleSheet("color: #4ec9b0;")
        else:
            self._snap_label.setText(
                _tr("⊕ [F4] Snap: {0}").format(_tr("OFF")))
            self._snap_label.setStyleSheet("color: #888888;")

    def _refresh_grid(self):
        plugin = self._command_bar._plugin
        grid = getattr(plugin, "_cad_grid", None)
        if grid is not None and grid.is_visible():
            self._grid_label.setText(
                _tr("⊞ [F9] Grid: {0}").format(_tr("ON")))
            self._grid_label.setStyleSheet("color: #4ec9b0;")
        else:
            self._grid_label.setText(
                _tr("⊞ [F9] Grid: {0}").format(_tr("OFF")))
            self._grid_label.setStyleSheet("color: #888888;")

    def _on_layers_removing(self, layer_ids):
        """Drop the current layer reference when that layer is going away."""
        if self._current_layer and self._current_layer.id() in layer_ids:
            try:
                self._current_layer.editingStarted.disconnect(
                    self._refresh_edit)
                self._current_layer.editingStopped.disconnect(
                    self._refresh_edit)
            except (TypeError, RuntimeError):
                pass
            self._current_layer = None
            self._show_no_layer()

    # --- toggles ---

    def _toggle_editing(self):
        """Toggle editing through the QGIS action, which asks save/discard/cancel - a direct commitChanges() would bake in experimental edits and throw away the undo stack."""
        layer = self._iface.activeLayer()
        if not layer or not isinstance(layer, QgsVectorLayer):
            # the label is styled clickable, so a click that does nothing needs a visible message
            self._command_bar.log(_tr("! No active vector layer"))
            return
        action = self._iface.actionToggleEditing()
        if action is None:
            return
        was_editing = layer.isEditable()
        action.trigger()
        if layer.isEditable() == was_editing:
            return  # cancelled at the prompt, or the layer said no
        self._command_bar.log(
            _tr("Editing disabled on '{0}'").format(layer.name())
            if was_editing
            else _tr("Editing enabled on '{0}'").format(layer.name()))

    def _toggle_snap(self):
        """Toggle snapping using the saved Smart Snapping profile."""
        enabled = snap_tool.toggle()
        self._command_bar.log(
            _tr("Snapping enabled") if enabled else _tr("Snapping disabled"))

    def _toggle_grid(self):
        plugin = self._command_bar._plugin
        if hasattr(plugin, "toggle_cad_grid"):
            new_state = plugin.toggle_cad_grid()
            if not new_state and getattr(plugin, "_cad_grid", None) is None:
                self._command_bar.log(_tr(
                    "The grid is switched off in the CAD Mode settings."))
            else:
                self._command_bar.log(
                    _tr("Grid enabled") if new_state
                    else _tr("Grid disabled"))
            self._refresh_grid()

    def cleanup(self):
        try:
            self._iface.currentLayerChanged.disconnect(self._refresh_layer)
        except (TypeError, RuntimeError):
            pass
        try:
            QgsProject.instance().snappingConfigChanged.disconnect(
                self._refresh_snap)
        except (TypeError, RuntimeError):
            pass
        try:
            QgsProject.instance().layersWillBeRemoved.disconnect(
                self._on_layers_removing)
        except (TypeError, RuntimeError):
            pass
        if self._current_layer:
            try:
                self._current_layer.editingStarted.disconnect(
                    self._refresh_edit)
                self._current_layer.editingStopped.disconnect(
                    self._refresh_edit)
            except (TypeError, RuntimeError):
                pass
            self._current_layer = None


class CommandBar(QDockWidget):
    """Command bar docked at the bottom of the QGIS window."""

    STYLESHEET = """
        QDockWidget {
            background: #2d2d2d;
            border: none;
            color: #cccccc;
            font-size: 8pt;
        }
        QDockWidget::title {
            background: #252525;
            border-bottom: 1px solid #333333;
            padding: 2px 6px;
            text-align: left;
        }
        QLabel#cmdStatus {
            color: #aaaaaa;
            font-family: Consolas, 'Courier New', monospace;
            font-size: 9pt;
            padding-left: 4px;
            background: #2d2d2d;
        }
        QLineEdit#cmdInput {
            background: #1e1e1e;
            color: #eeeeee;
            font-family: Consolas, 'Courier New', monospace;
            font-size: 9pt;
            border: 1px solid #555555;
            padding: 2px 4px;
            selection-background-color: #264f78;
        }
        QPlainTextEdit#cmdHistory {
            background: #1e1e1e;
            color: #aaaaaa;
            font-family: Consolas, 'Courier New', monospace;
            font-size: 8pt;
            border: none;
            border-bottom: 1px solid #333333;
        }
        QLabel#cmdHint {
            color: #666666;
            font-family: Consolas, 'Courier New', monospace;
            font-size: 8pt;
            font-style: italic;
            padding: 1px 6px;
            background: #252525;
            border-bottom: 1px solid #333333;
        }
    """

    POPUP_STYLE = """
        QListWidget#cmdPopup {
            background: #2d2d2d;
            color: #eeeeee;
            font-family: Consolas, 'Courier New', monospace;
            font-size: 9pt;
            border: 1px solid #555555;
            outline: none;
        }
        QListWidget#cmdPopup::item {
            padding: 2px 6px;
        }
        QListWidget#cmdPopup::item:hover,
        QListWidget#cmdPopup::item:selected {
            background: #264f78;
        }
    """

    def __init__(self, plugin):
        self._iface = plugin.iface
        main_window = self._iface.mainWindow()
        # named for the Panels menu, where it sits among QGIS's own panels
        super().__init__(_tr("Vernier Command Bar"), main_window)
        self.setObjectName("VernierCommandBar")
        self.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        # not floatable: the autocomplete popup is a child of the main window and would be clipped out of a floating panel
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable)

        self._plugin = plugin
        self._history = CommandHistory(HISTORY_SIZE)
        self._bar_enabled = False
        self._strip_enabled = False
        self._panel_visible = settings_service.get("cad_mode/panel_visible")
        # set while apply_settings() drives visibility, so its own show/hide isn't mistaken for the user closing the panel
        self._applying = False

        container = QWidget()
        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # hidden while the bar is collapsed
        self._history_log = QPlainTextEdit()
        self._history_log.setObjectName("cmdHistory")
        self._history_log.setReadOnly(True)
        self._history_log.setMaximumBlockCount(_LOG_MAX_LINES)
        self._history_log.setVisible(False)
        main_layout.addWidget(self._history_log, 1)

        self._hint = QLabel(_tr("Type a command, or '?' for help"))
        self._hint.setObjectName("cmdHint")
        self._hint.setFixedHeight(20)
        main_layout.addWidget(self._hint, 0)

        # status label, input field, history toggle
        self._input_row = QWidget()
        input_layout = QHBoxLayout(self._input_row)
        input_layout.setContentsMargins(4, 2, 4, 2)
        input_layout.setSpacing(6)

        self._status = QLabel("")
        self._status.setObjectName("cmdStatus")
        self._status.setMinimumWidth(120)

        self._input = QLineEdit()
        self._input.setObjectName("cmdInput")
        self._input.setPlaceholderText(_tr("Type a command..."))
        self._input.returnPressed.connect(self._on_execute)
        self._input.textChanged.connect(self._on_text_changed)

        self._toggle_btn = QToolButton()
        self._toggle_btn.setText("▲")
        self._toggle_btn.setFixedWidth(22)
        self._toggle_btn.setToolTip(_tr("Show/hide the command history"))
        self._toggle_btn.clicked.connect(self._toggle_history)

        input_layout.addWidget(self._status, 0)
        input_layout.addWidget(self._input, 1)
        input_layout.addWidget(self._toggle_btn, 0)
        main_layout.addWidget(self._input_row, 0)

        self._strip = StatusStrip(self, self._iface)
        main_layout.addWidget(self._strip, 0)

        self.setWidget(container)
        self.setStyleSheet(self.STYLESHEET)

        # autocomplete popup, drops up rather than down
        self._popup = QListWidget(main_window)
        self._popup.setObjectName("cmdPopup")
        self._popup.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._popup.setStyleSheet(self.POPUP_STYLE)
        self._popup.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._popup.hide()
        self._popup.itemClicked.connect(self._on_popup_click)

        self._registry = build_registry(plugin, self)

        # this one is always on, the application-level filter only goes in through apply_settings() while the bar or the status strip is enabled
        self._input.installEventFilter(self)

        # only a real click on the Panels entry, not the check state Qt flips when the panel is tabbed behind another one
        self.toggleViewAction().triggered.connect(self._on_view_toggled)

        self._applying = True
        self.setVisible(False)  # hidden until apply_settings()
        self._applying = False

    # --- enable/disable ---

    def apply_settings(self, bar_enabled, strip_enabled):
        """Show or hide the bar and strip per the sub-toggles. The panel stays alive when disabled, only visibility and the global key filter change, so re-enabling is instant."""
        self._bar_enabled = bar_enabled
        self._strip_enabled = strip_enabled
        self._panel_visible = settings_service.get("cad_mode/panel_visible")

        self._hint.setVisible(bar_enabled)
        self._input_row.setVisible(bar_enabled)
        if not bar_enabled:
            self._history_log.setVisible(False)
            self._toggle_btn.setText("▲")
            self._input.clear()
            self._popup.hide()
        self._strip.setVisible(strip_enabled)

        self._applying = True
        try:
            self.setVisible(
                (bar_enabled or strip_enabled) and self._panel_visible)
        finally:
            self._applying = False

        # the strip needs the filter for the F-keys too, not just typing
        app = QApplication.instance()
        if bar_enabled or strip_enabled:
            # re-installing an existing filter just moves it to the front of the chain, it doesn't stack
            app.installEventFilter(self)
        else:
            # removing a filter that isn't installed is a documented no-op
            app.removeEventFilter(self)

    def _remember_panel_visible(self, visible):
        settings_service.set_("cad_mode/panel_visible", visible)
        self._panel_visible = visible

    def _on_view_toggled(self, checked):
        """View > Panels > Vernier Command Bar. triggered() only fires on a real click, so tabbing the panel behind another one never lands here."""
        if not self._applying:
            self._remember_panel_visible(bool(checked))

    def closeEvent(self, event):
        """The panel's own X. The choice is stored so no later apply_settings() puts it back, and CAD Mode itself stays on."""
        if not self._applying:
            self._remember_panel_visible(False)
            try:
                self._iface.messageBar().pushMessage(
                    _tr("Vernier"),
                    _tr("Command Bar hidden. Bring it back from "
                        "View > Panels > Vernier Command Bar, or by "
                        "switching CAD Mode off and on."),
                    level=Qgis.MessageLevel.Info, duration=8)
            except (RuntimeError, AttributeError):
                pass
        super().closeEvent(event)

    def refresh_strip(self):
        """Refresh every status-strip indicator, safe to call deferred."""
        try:
            self._strip.refresh_all()
        except RuntimeError:
            pass

    def cleanup(self):
        """Pull the event filters this widget installed. unload() has to call it, a leaked application-level filter survives a reload and double-handles every keystroke."""
        try:
            self._strip.cleanup()
        except RuntimeError:
            pass
        try:
            self._input.removeEventFilter(self)
        except RuntimeError:
            pass
        try:
            QApplication.instance().removeEventFilter(self)
        except RuntimeError:
            pass
        try:
            self._popup.hide()
            self._popup.deleteLater()
        except RuntimeError:
            pass

    # --- log / history pane ---

    def resizeEvent(self, event):
        """Pop the history log open when the panel gets dragged taller."""
        super().resizeEvent(event)
        # this can fire during __init__, before the widgets exist
        if not hasattr(self, "_history_log"):
            return
        if (self._bar_enabled and event.size().height() > 120
                and not self._history_log.isVisible()):
            self._history_log.setVisible(True)
            self._toggle_btn.setText("▼")

    def log(self, message):
        """Append a timestamped line to the history log."""
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M")
        self._history_log.appendPlainText(f"[{ts}] {message}")
        # open the log as soon as there's something in it
        if self._bar_enabled and not self._history_log.isVisible():
            self._history_log.setVisible(True)
            self._toggle_btn.setText("▼")

    def _toggle_history(self):
        visible = not self._history_log.isVisible()
        self._history_log.setVisible(visible)
        self._toggle_btn.setText("▼" if visible else "▲")

    # --- hint + autocomplete ---

    def _on_text_changed(self, text):
        """Keep the popup and hint line current as the user types."""
        self._history.reset()
        self._update_popup(text)
        self._update_hint(text)

    def _update_hint(self, text):
        text = text.strip().lower()
        if not text:
            self._hint.setText(_tr("Type a command, or '?' for help"))
            return

        entry = self._registry.get(text)
        if entry is not None:
            label, _cb, hint = entry
            self._hint.setText(f"{text} > {label}: {hint}")
            return

        matches = self._registry.prefix_matches(text)
        if len(matches) == 1:
            name, label = matches[0]
            _l, _cb, hint = self._registry.get(name)
            self._hint.setText(f"{name} > {label}: {hint}")
        elif len(matches) > 1:
            names = ", ".join(f"{n} ({lbl})" for n, lbl in matches[:4])
            suffix = "..." if len(matches) > 4 else ""
            self._hint.setText(f"'{text}' > {names}{suffix}")
        else:
            self._hint.setText(
                _tr("'{0}' - unknown command").format(text))

    def _update_popup(self, text):
        text = text.strip().lower()
        self._popup.clear()

        if not text:
            self._popup.hide()
            return

        matches = self._registry.prefix_matches(text)

        # nothing to show for no matches, or for a single exact match they already typed
        if not matches or (len(matches) == 1 and matches[0][0] == text):
            self._popup.hide()
            return

        for name, label in matches:
            item = QListWidgetItem(f"{name}  -  {label}")
            # the alias rides along as data - display text is for reading, not for parsing back
            item.setData(Qt.ItemDataRole.UserRole, name)
            self._popup.addItem(item)

        self._position_popup(len(matches))
        self._popup.show()
        self._popup.raise_()

    def _position_popup(self, count):
        """Put the popup above the input rather than below it."""
        main_window = self._iface.mainWindow()
        input_pos = self._input.mapTo(main_window, QPoint(0, 0))
        popup_height = min(count * _POPUP_ITEM_HEIGHT + _POPUP_PADDING,
                           _POPUP_MAX_HEIGHT)
        self._popup.setGeometry(
            input_pos.x(),
            input_pos.y() - popup_height,
            self._input.width(),
            popup_height,
        )

    def _on_popup_click(self, item):
        """Run the command behind a clicked popup item through the normal execute path."""
        self._popup.hide()
        self._input.setText(item.data(Qt.ItemDataRole.UserRole))
        self._on_execute()

    # --- execution ---

    def _on_execute(self):
        """Enter or Space, run whatever is typed."""
        text = self._input.text().strip()
        if not text:
            return

        self._popup.hide()
        self._input.clear()

        result = self._registry.resolve(text)

        if result[0] == "exact":
            _, _name, label, callback = result
            self._status.setText(f"{label} >")
            self._history.append(text)
            try:
                callback()
            except Exception as exc:
                self._status.setText(f"! {label}: {exc}")
                self.log(f"! {label}: {exc}")
        elif result[0] == "ambiguous":
            names = ", ".join(result[1])
            self._status.setText(f"? '{text}': {names}")
            self.log(_tr("? '{0}' is ambiguous: {1}").format(text, names))
            return
        else:
            self._status.setText(
                _tr("? '{0}' unknown").format(text))
            self.log(_tr(
                "? '{0}' unknown - start typing to see the "
                "matching commands"
            ).format(text))
            return

        # focus only goes back to the canvas after a command actually ran
        canvas = self._iface.mapCanvas()
        if canvas:
            canvas.setFocus()

    # --- built-in commands ---

    def _cmd_polyline(self):
        """Start Add Feature on the active layer, creating a scratch polygon layer in the project CRS if there's no vector layer at all."""
        layer = self._iface.activeLayer()
        if not layer or not isinstance(layer, QgsVectorLayer):
            vector_layers = [
                lyr for lyr in QgsProject.instance().mapLayers().values()
                if isinstance(lyr, QgsVectorLayer)
            ]
            if len(vector_layers) == 1:
                layer = vector_layers[0]
                self._iface.setActiveLayer(layer)
                self.log(_tr("(layer '{0}' selected automatically)").format(
                    layer.name()))
            elif len(vector_layers) == 0:
                crs = QgsProject.instance().crs()
                uri = ("Polygon?crs=" + crs.authid()) if crs.isValid() \
                    else "Polygon"
                layer = QgsVectorLayer(uri, _tr("Scratch layer"), "memory")
                layer.setCustomProperty(_CAD_LAYER_FLAG, "1")
                _apply_cad_style(layer)
                QgsProject.instance().addMapLayer(layer)
                self._iface.setActiveLayer(layer)
                self.log(_tr(
                    "(scratch polygon layer created in the project CRS)"))
            else:
                self._status.setText(
                    _tr("! Select a layer in the Layers panel"))
                self.log(_tr(
                    "! {0} layers - select one in the Layers panel"
                ).format(len(vector_layers)))
                return

        if not layer.isEditable():
            layer.startEditing()
            QApplication.processEvents()
            self.log(_tr("(editing enabled automatically on '{0}')").format(
                layer.name()))

        self._enable_snapping()
        self._trigger_qgis_action("mActionAddFeature")

    def _cmd_close_sketch(self):
        """Finish the sketch by faking a right click."""
        canvas = self._iface.mapCanvas()
        if not canvas:
            return

        # QPointF because the Qt6 QMouseEvent constructor wants a float position, and Qt5 accepts one as well
        center = QPointF(canvas.width() / 2, canvas.height() / 2)

        press = QMouseEvent(
            QEvent.Type.MouseButtonPress, center, Qt.MouseButton.RightButton,
            Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier,
        )
        release = QMouseEvent(
            QEvent.Type.MouseButtonRelease, center, Qt.MouseButton.RightButton,
            Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier,
        )
        canvas.mousePressEvent(press)
        canvas.mouseReleaseEvent(release)

    def _ensure_editable(self):
        """The active vector layer, in edit mode, or None. Picks the only vector layer if none is active and starts editing when it has to."""
        layer = self._iface.activeLayer()

        if not layer or not isinstance(layer, QgsVectorLayer):
            vector_layers = [
                lyr for lyr in QgsProject.instance().mapLayers().values()
                if isinstance(lyr, QgsVectorLayer)
            ]
            if len(vector_layers) == 1:
                layer = vector_layers[0]
                self._iface.setActiveLayer(layer)
                self.log(_tr("(layer '{0}' selected automatically)").format(
                    layer.name()))
            elif len(vector_layers) == 0:
                self._status.setText(
                    _tr("! No vector layer in the project"))
                self.log(_tr("! No vector layer in the project"))
                return None
            else:
                self._status.setText(
                    _tr("! Select a layer in the Layers panel"))
                self.log(_tr(
                    "! {0} layers - select one in the Layers panel"
                ).format(len(vector_layers)))
                return None

        if not layer.isEditable():
            layer.startEditing()
            QApplication.processEvents()
            self.log(_tr("(editing enabled automatically on '{0}')").format(
                layer.name()))

        return layer

    def _trigger_qgis_action(self, object_name):
        """Find a QGIS action by objectName and fire it."""
        for action in self._iface.mainWindow().findChildren(QAction):
            if action.objectName() == object_name:
                action.trigger()
                return True
        return False

    @staticmethod
    def _enable_snapping():
        """Turn snapping on with the saved profile, if it isn't already."""
        if not snap_tool.is_enabled():
            snap_tool.toggle()

    def _cmd_move(self):
        if not self._ensure_editable():
            return
        self._trigger_qgis_action("mActionMoveFeature")

    def _cmd_copy(self):
        """Copy/Move Feature tool, in copy mode."""
        if not self._ensure_editable():
            return
        self._trigger_qgis_action("mActionMoveFeatureCopy")

    def _cmd_vertex(self):
        """Vertex Tool, with snapping switched on."""
        if not self._ensure_editable():
            return
        self._enable_snapping()
        self._trigger_qgis_action("mActionVertexToolActiveLayer")

    def _cmd_rotate(self):
        if not self._ensure_editable():
            return
        self._trigger_qgis_action("mActionRotateFeature")
        self.log(_tr("Rotate > rotate tool active"))

    def _cmd_delete(self):
        layer = self._ensure_editable()
        if not layer:
            return
        n = layer.selectedFeatureCount()
        if n == 0:
            self.log(_tr("! No features selected"))
            return
        layer.deleteSelectedFeatures()
        self.log(_tr("Delete > {0} features deleted from '{1}'").format(
            n, layer.name()))

    def _cmd_basemap(self):
        """Toggle the configured XYZ basemap at the bottom of the tree. Ships pointing at OpenStreetMap, whose tile policy allows it - the URL is configurable, so any other provider is the user's own call."""
        project = QgsProject.instance()
        existing = [lyr for lyr in project.mapLayers().values()
                    if lyr.customProperty("vernier/cad_basemap")]
        if existing:
            project.removeMapLayers([lyr.id() for lyr in existing])
            self.log(_tr("Basemap > removed"))
            return
        url = str(settings_service.get("cad_mode/basemap_url")).strip()
        if not url:
            self.log(_tr("! No basemap URL configured "
                         "(Settings > CAD Mode)"))
            return
        from urllib.parse import quote
        uri = f"type=xyz&url={quote(url, safe=':/?&=')}&zmin=0&zmax=19"
        layer = QgsRasterLayer(uri, _tr("Basemap"), "wms")
        if not layer.isValid():
            self.log(_tr("! Basemap failed to load - check the URL in "
                         "Settings > CAD Mode"))
            return
        layer.setCustomProperty("vernier/cad_basemap", True)
        project.addMapLayer(layer, False)
        # bottom of the tree so it sits under the working layers
        project.layerTreeRoot().insertLayer(-1, layer)
        self.log(_tr("Basemap > added under your layers"))

    def _cmd_save(self):
        """Commit edits on the active layer and go straight back into edit mode."""
        layer = self._iface.activeLayer()
        if not layer or not isinstance(layer, QgsVectorLayer):
            self.log(_tr("! No active vector layer"))
            return
        if not layer.isEditable():
            self.log(_tr("! '{0}' is not in edit mode").format(layer.name()))
            return
        # the number worth reporting is what the buffer is about to write, not how many features the layer already holds
        buffer = layer.editBuffer()
        touched = set()
        if buffer is not None:
            touched.update(buffer.addedFeatures())
            touched.update(buffer.changedGeometries())
            touched.update(buffer.changedAttributeValues())
            touched.update(buffer.deletedFeatureIds())
        if layer.commitChanges():
            layer.startEditing()
            self.log(_tr(
                "Edits saved on '{0}' ({1} features changed)").format(
                    layer.name(), len(touched)))
        else:
            errors = layer.commitErrors()
            self.log(_tr("! Commit failed on '{0}': {1}").format(
                layer.name(), "; ".join(errors)))
            layer.startEditing()  # back in, so the work isn't lost

    def _cmd_redo(self):
        """Redo the last undone edit on the active layer."""
        layer = self._iface.activeLayer()
        if not layer or not isinstance(layer, QgsVectorLayer):
            self.log(_tr("! No active layer"))
            return
        if not layer.isEditable():
            self.log(_tr("! The layer is not in edit mode"))
            return
        if not layer.undoStack().canRedo():
            self.log(_tr("! Nothing to redo"))
            return
        layer.undoStack().redo()
        self.log(_tr("Redo > operation restored"))

    def _cmd_zoom_extents(self):
        self._iface.zoomFull()
        self.log(_tr("Zoom > full extent"))

    def _cmd_zoom_selected(self):
        layer = self._iface.activeLayer()
        if (layer and isinstance(layer, QgsVectorLayer)
                and layer.selectedFeatureCount()):
            n = layer.selectedFeatureCount()
            self._iface.mapCanvas().zoomToSelected(layer)
            self.log(_tr("Zoom > to {0} selected features").format(n))
        else:
            self.log(_tr("! No active selection"))

    def _cmd_pan(self):
        self._trigger_qgis_action("mActionPan")
        self.log(_tr("Pan > pan tool active"))

    def _cmd_select(self):
        self._trigger_qgis_action("mActionSelectFeatures")
        self.log(_tr("Select > select tool active"))

    def _cmd_inspect(self):
        self._trigger_qgis_action("mActionIdentify")
        self.log(_tr("Identify > click a feature to see its attributes"))

    def _cmd_measure(self):
        self._trigger_qgis_action("mActionMeasure")
        self.log(_tr("Measure > click the map to measure distances"))

    def _cmd_deselect(self):
        layer = self._iface.activeLayer()
        if not layer or not isinstance(layer, QgsVectorLayer):
            self.log(_tr("! No active layer"))
            return
        n = layer.selectedFeatureCount()
        if n == 0:
            self.log(_tr("! No features selected"))
            return
        layer.removeSelection()
        self.log(_tr("Selection cleared ({0} features deselected)").format(n))

    def _cmd_area(self):
        """Log the area of whatever is selected."""
        layer = self._iface.activeLayer()
        if not layer or not isinstance(layer, QgsVectorLayer):
            self.log(_tr("! No active layer"))
            return
        # lines and points would print "Area: 0.00 m²" as if it had been measured
        if layer.geometryType() != QgsWkbTypes.GeometryType.PolygonGeometry:
            self.log(_tr("! Not a polygon layer"))
            return
        features = layer.selectedFeatures()
        if not features:
            self.log(_tr("! No features selected"))
            return
        # normalized to m² inside the helper, so a geographic layer's square degrees never reach format_area, and NaN comes back as None instead of printing
        try:
            sqm = measure_area_sqm((f.geometry() for f in features),
                                   layer.crs(), QgsProject.instance())
        except QgsCsException:
            sqm = None
        if sqm is None:
            self.log(_tr("! Cannot measure area in this layer's CRS"))
            return
        text = "{0}  ({1})".format(
            format_area(sqm, "m2"), format_area(sqm, "ha"))
        if len(features) == 1:
            self.log(_tr("Area: {0}").format(text))
        else:
            self.log(_tr("Total area ({0} features): {1}").format(
                len(features), text))

    def _cmd_grid(self):
        self._strip._toggle_grid()

    # --- global key handling ---

    def eventFilter(self, obj, event):
        """Application-level filter - F-key toggles, Delete-to-erase, Escape-to-pan and redirecting printable typing into the input."""

        # this sees every event in the app, so bail on non-key ones first
        if event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(obj, event)

        # F-key toggles, from anywhere in the main window
        if isinstance(event, QKeyEvent):
            key = event.key()
            focused = QApplication.instance().focusWidget()
            is_main = (focused is not None
                       and focused.window() is self._iface.mainWindow())
            # system chords pass through untouched, Alt+F4 closes the window and Ctrl+F4 closes tabs - neither is our snap toggle
            has_modifier = bool(event.modifiers() & (
                Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier))

            if (is_main and not has_modifier
                    and key == Qt.Key.Key_F2 and not event.isAutoRepeat()):
                # F2 means rename in a tree or table view, leave it alone
                if not isinstance(focused, QAbstractItemView):
                    self._strip._toggle_editing()
                    return True
            if (is_main and not has_modifier
                    and key == Qt.Key.Key_F4 and not event.isAutoRepeat()):
                self._strip._toggle_snap()
                return True
            if (is_main and not has_modifier
                    and key == Qt.Key.Key_F9 and not event.isAutoRepeat()):
                self._strip._toggle_grid()
                return True
            if (is_main and not has_modifier
                    and key == Qt.Key.Key_F8 and not event.isAutoRepeat()):
                self._cmd_basemap()
                return True

            # Delete erases the selection CAD-style, but not from text widgets or item views like the attribute table and layer panel
            if (self._bar_enabled and is_main and key == Qt.Key.Key_Delete
                    and not event.isAutoRepeat() and not has_modifier):
                # an active edit tool owns Delete. stealing it would wipe the feature the user was only trimming vertices off
                tool = self._iface.mapCanvas().mapTool()
                tool_is_editing = False
                try:
                    tool_is_editing = bool(
                        tool and tool.flags() & QgsMapTool.Flag.EditTool)
                except (RuntimeError, AttributeError):
                    pass
                # a bare key press shouldn't pick a layer or open an edit session for you, so only an already-editing active layer can be erased from the keyboard
                active = self._iface.activeLayer()
                is_editing_layer = (isinstance(active, QgsVectorLayer)
                                    and active.isEditable())
                if (is_editing_layer and not tool_is_editing
                        and not _takes_text_input(focused)
                        and not isinstance(focused, QAbstractItemView)
                        and focused is not self._input):
                    self._cmd_delete()
                    return True

        if obj is self._input:
            key = event.key()

            # Tab completes to the first prefix match without running it
            if key == Qt.Key.Key_Tab:
                matches = self._registry.prefix_matches(
                    self._input.text().strip())
                if matches:
                    self._input.setText(matches[0][0])
                    self._input.end(False)
                return True

            # with the popup up, Up/Down walk it and Enter/Space pick
            if self._popup.isVisible():
                if key == Qt.Key.Key_Up:
                    row = self._popup.currentRow()
                    self._popup.setCurrentRow(
                        row - 1 if row > 0 else self._popup.count() - 1)
                    return True
                if key == Qt.Key.Key_Down:
                    row = self._popup.currentRow()
                    self._popup.setCurrentRow(
                        row + 1 if row < self._popup.count() - 1 else 0)
                    return True
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                    current = self._popup.currentItem()
                    if current:
                        self._on_popup_click(current)
                        return True
            else:
                # without it, Up/Down walk the command history
                if key == Qt.Key.Key_Up and len(self._history):
                    entry = self._history.previous()
                    if entry is not None:
                        self._input.setText(entry)
                    return True

                if key == Qt.Key.Key_Down and len(self._history):
                    entry = self._history.next()
                    if entry == "":
                        self._input.clear()
                    elif entry is not None:
                        self._input.setText(entry)
                    return True

            # Space on non-empty text runs it, same as Enter
            if key == Qt.Key.Key_Space and self._input.text().strip():
                self._on_execute()
                return True

            # Escape cancels the sketch, switches to Pan and focuses the canvas
            if key == Qt.Key.Key_Escape:
                self._cancel_to_pan()
                return True

            return super().eventFilter(obj, event)

        # the popup handles its own events
        if obj is self._popup:
            return super().eventFilter(obj, event)

        if not isinstance(event, QKeyEvent):
            return super().eventFilter(obj, event)

        # only the typing redirect below needs the bar, the F-keys already ran
        if not self._bar_enabled:
            return super().eventFilter(obj, event)

        # modified keystrokes are shortcuts, not commands
        mods = event.modifiers()
        if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
            return super().eventFilter(obj, event)

        # Escape anywhere in the main window cancels the sketch and goes to Pan
        if event.key() == Qt.Key.Key_Escape:
            focused = QApplication.instance().focusWidget()
            if focused and focused.window() is self._iface.mainWindow():
                if not _takes_text_input(focused):
                    self._cancel_to_pan()
                    return True
            return super().eventFilter(obj, event)

        typed = event.text()
        if not typed or not typed.isprintable():
            return super().eventFilter(obj, event)

        # only redirect while the main QGIS window is active, not from dialogs
        focused = QApplication.instance().focusWidget()
        if not focused:
            return super().eventFilter(obj, event)

        if focused.window() is not self._iface.mainWindow():
            return super().eventFilter(obj, event)

        # typing into a line edit, spin box or code editor has to reach it, not us
        if focused is self._input:
            return super().eventFilter(obj, event)

        if _takes_text_input(focused):
            return super().eventFilter(obj, event)

        # a panel the user closed stays closed, typing on the canvas must not resurrect it
        if not self._panel_visible:
            return super().eventFilter(obj, event)

        # tabbed behind another panel: raise it, or the keystroke lands in an input nobody can see
        if not self.isVisible():
            self._applying = True
            try:
                self.show()
            finally:
                self._applying = False
        self.raise_()

        self._input.setFocus()
        self._input.setText(self._input.text() + typed)
        return True

    def _cancel_to_pan(self):
        """What Escape does - clear the input, cancel the sketch, go to Pan."""
        self._input.clear()
        self._popup.hide()
        canvas = self._iface.mapCanvas()
        if canvas:
            canvas.keyPressEvent(
                QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
            canvas.keyReleaseEvent(
                QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
        self._trigger_qgis_action("mActionPan")
        if canvas:
            canvas.setFocus()
