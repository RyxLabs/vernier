# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Settings dialog, one tab per tool. Widgets load from settings_service on open and are written back only on OK, so Cancel discards everything."""

from qgis.PyQt.QtWidgets import (  # type: ignore
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from ..services import settings_service
from .base_dialog import BaseDialog

_SNAP_TYPES = ("vertex_and_segment", "vertex", "segment")

# combo order, has to stay in step with tools.area_readout.UNIT_MODES
_AREA_UNITS = ("auto", "m2", "ha", "km2", "acres", "ft2")

# same for tools.area_readout.SECONDARY_MODES
_AREA_UNITS_SECONDARY = ("none", "auto", "m2", "ha", "km2", "acres", "ft2")


def _default(key):
    """Default value of a setting, ignoring whatever is stored."""
    return settings_service.DEFAULTS[key]


class SettingsDialog(BaseDialog):

    def __init__(self, iface=None, parent=None):
        super().__init__(iface, parent)
        self.setWindowTitle(self.tr("Vernier Settings"))
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self._build_snapping_tab()
        self._build_display_tab()
        self._build_cad_mode_tab()
        self._build_vertex_cleanup_tab()
        self._build_topology_tab()

        buttons = QHBoxLayout()
        reset_btn = QPushButton(self.tr("Restore defaults"))
        reset_btn.clicked.connect(self._restore_defaults)
        buttons.addWidget(reset_btn)
        buttons.addStretch()
        ok_btn = QPushButton(self.tr("OK"))
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(ok_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

        self._load()

    # --- tabs ---

    def _build_snapping_tab(self):
        tab = QWidget()
        form = QFormLayout(tab)

        self.snap_tolerance = QSpinBox()
        self.snap_tolerance.setRange(1, 100)
        self.snap_tolerance.setSuffix(self.tr(" px"))
        form.addRow(self.tr("Tolerance:"), self.snap_tolerance)

        self.snap_type = QComboBox()
        self.snap_type.addItem(self.tr("Vertex and segment"))
        self.snap_type.addItem(self.tr("Vertex only"))
        self.snap_type.addItem(self.tr("Segment only"))
        form.addRow(self.tr("Snap to:"), self.snap_type)

        self.snap_intersection = QCheckBox(self.tr("Snap on intersections"))
        form.addRow(self.snap_intersection)

        self.snap_topological = QCheckBox(
            self.tr("Enable topological editing"))
        self.snap_topological.setToolTip(self.tr(
            "Moving a shared vertex moves it on every layer that uses it"))
        form.addRow(self.snap_topological)

        note = QLabel(self.tr(
            "Applied by the Smart Snapping toolbar toggle."))
        note.setWordWrap(True)
        form.addRow(note)

        self.tabs.addTab(tab, self.tr("Snapping"))

    def _build_display_tab(self):
        tab = QWidget()
        form = QFormLayout(tab)

        self.area_show = QCheckBox(
            self.tr("Show area readout in the status bar"))
        self.area_show.setToolTip(self.tr(
            "Live area of the selected polygon features on the active layer"))
        form.addRow(self.area_show)

        self.area_units = QComboBox()
        self.area_units.addItem(self.tr("Auto (m² / ha / km²)"))
        self.area_units.addItem(self.tr("Square meters"))
        self.area_units.addItem(self.tr("Hectares"))
        self.area_units.addItem(self.tr("Square kilometers"))
        self.area_units.addItem(self.tr("Acres"))
        self.area_units.addItem(self.tr("Square feet"))
        form.addRow(self.tr("Area units:"), self.area_units)

        self.area_units_secondary = QComboBox()
        self.area_units_secondary.addItem(self.tr("None"))
        self.area_units_secondary.addItem(self.tr("Auto (pairs with the unit above)"))
        self.area_units_secondary.addItem(self.tr("Square meters"))
        self.area_units_secondary.addItem(self.tr("Hectares"))
        self.area_units_secondary.addItem(self.tr("Square kilometers"))
        self.area_units_secondary.addItem(self.tr("Acres"))
        self.area_units_secondary.addItem(self.tr("Square feet"))
        self.area_units_secondary.setToolTip(self.tr(
            "Shown in parentheses after the main figure, e.g.\n"
            "\"1.2345 km² (123.4500 ha)\". Auto pairs km² with ha,\n"
            "ha with m², m² with ha, acres with ha and ft² with\n"
            "acres."))
        form.addRow(self.tr("Secondary unit:"), self.area_units_secondary)

        self.tabs.addTab(tab, self.tr("Display"))

    def _build_cad_mode_tab(self):
        tab = QWidget()
        form = QFormLayout(tab)

        self.cad_mode_enabled = QCheckBox(self.tr("Enable CAD Mode"))
        self.cad_mode_enabled.setToolTip(self.tr(
            "Dark canvas with a scalable grid, a typed command bar\n"
            "and a status strip."))
        form.addRow(self.cad_mode_enabled)

        self.cad_mode_dark = QCheckBox(self.tr("Dark canvas"))
        form.addRow(self.cad_mode_dark)

        self.cad_mode_grid = QCheckBox(self.tr("Grid overlay (F9)"))
        form.addRow(self.cad_mode_grid)

        self.cad_mode_bar = QCheckBox(self.tr("Command bar"))
        self.cad_mode_bar.setToolTip(self.tr(
            "Type commands anywhere: buffer, split, pl, ze... Typing is\n"
            "redirected to the bar; Tab completes, Up/Down browse history"))
        form.addRow(self.cad_mode_bar)

        self.cad_mode_strip = QCheckBox(self.tr("Status strip"))
        self.cad_mode_strip.setToolTip(self.tr(
            "Live layer/edit/snap/grid indicators with F2/F4/F9 toggles"))
        form.addRow(self.cad_mode_strip)

        self.cad_mode_basemap = QLineEdit()
        self.cad_mode_basemap.setToolTip(self.tr(
            "XYZ tile URL for the F8 / \"bm\" basemap toggle.\n"
            "Default is OpenStreetMap; any other provider is your own\n"
            "choice under that provider's terms."))
        form.addRow(self.tr("Basemap URL (F8):"), self.cad_mode_basemap)

        self.cad_mode_basemap_note = QLabel()
        self.cad_mode_basemap_note.setWordWrap(True)
        self.cad_mode_basemap_note.setVisible(False)
        form.addRow(self.cad_mode_basemap_note)

        # sub-toggles only do anything while CAD Mode is on
        for sub in (self.cad_mode_dark, self.cad_mode_grid,
                    self.cad_mode_bar, self.cad_mode_strip,
                    self.cad_mode_basemap):
            self.cad_mode_enabled.toggled.connect(sub.setEnabled)

        note = QLabel(self.tr(
            "Disabling CAD Mode restores your previous canvas color. The "
            "Command Bar panel can be dragged, tabbed with the other bottom "
            "panels or closed with its X - View > Panels > Vernier Command "
            "Bar brings it back."))
        note.setWordWrap(True)
        form.addRow(note)

        self._cad_tab = self.tabs.addTab(tab, self.tr("CAD Mode"))

    def _build_vertex_cleanup_tab(self):
        tab = QWidget()
        form = QFormLayout(tab)

        self.vertex_segment = QDoubleSpinBox()
        self.vertex_segment.setRange(0.0, 100000.0)
        self.vertex_segment.setDecimals(6)
        self.vertex_segment.setSingleStep(0.001)
        self.vertex_segment.setSuffix(self.tr(" layer units"))
        self.vertex_segment.setToolTip(self.tr(
            "Consecutive vertices closer than this are collapsed to one"))
        form.addRow(self.tr("Segment tolerance:"), self.vertex_segment)

        self.vertex_dup = QDoubleSpinBox()
        self.vertex_dup.setRange(0.0, 100000.0)
        self.vertex_dup.setDecimals(6)
        self.vertex_dup.setSingleStep(0.001)
        self.vertex_dup.setSuffix(self.tr(" layer units"))
        self.vertex_dup.setToolTip(self.tr(
            "Final duplicate-node sweep tolerance; 0 disables the sweep"))
        form.addRow(self.tr("Duplicate node tolerance:"), self.vertex_dup)

        self.vertex_snap = QDoubleSpinBox()
        self.vertex_snap.setRange(0.0, 100000.0)
        self.vertex_snap.setDecimals(6)
        self.vertex_snap.setSingleStep(0.001)
        self.vertex_snap.setSuffix(self.tr(" layer units"))
        self.vertex_snap.setToolTip(self.tr(
            "Tolerance for the optional snap pass after cleaning"))
        form.addRow(self.tr("Snap tolerance:"), self.vertex_snap)

        note = QLabel(self.tr(
            "Defaults for the Remove Close Vertices dialog."))
        note.setWordWrap(True)
        form.addRow(note)

        self.tabs.addTab(tab, self.tr("Vertex Cleanup"))

    def _build_topology_tab(self):
        tab = QWidget()
        form = QFormLayout(tab)

        self.topo_snap = QDoubleSpinBox()
        self.topo_snap.setRange(0.0, 100000.0)
        self.topo_snap.setDecimals(6)
        self.topo_snap.setSingleStep(0.001)
        self.topo_snap.setSuffix(self.tr(" layer units"))
        self.topo_snap.setToolTip(self.tr(
            "Gap check: cracks narrower than this are closed by snapping\n"
            "before looking for gaps"))
        form.addRow(self.tr("Snap tolerance:"), self.topo_snap)

        self.topo_gap_area = QDoubleSpinBox()
        self.topo_gap_area.setRange(0.0, 1000000000.0)
        self.topo_gap_area.setDecimals(4)
        self.topo_gap_area.setSingleStep(0.01)
        self.topo_gap_area.setSuffix(self.tr(" layer units²"))
        self.topo_gap_area.setToolTip(self.tr(
            "Gaps smaller than this area are ignored"))
        form.addRow(self.tr("Minimum gap area:"), self.topo_gap_area)

        self.topo_gap_buffer = QDoubleSpinBox()
        self.topo_gap_buffer.setRange(0.0, 100000.0)
        self.topo_gap_buffer.setDecimals(6)
        self.topo_gap_buffer.setSingleStep(0.0001)
        self.topo_gap_buffer.setSuffix(self.tr(" layer units"))
        self.topo_gap_buffer.setToolTip(self.tr(
            "Gap slivers narrower than twice this are dismissed as\n"
            "coordinate noise rather than reported"))
        form.addRow(self.tr("Sliver tolerance:"), self.topo_gap_buffer)

        self.topo_vertex = QDoubleSpinBox()
        self.topo_vertex.setRange(0.0, 100000.0)
        self.topo_vertex.setDecimals(6)
        self.topo_vertex.setSingleStep(0.001)
        self.topo_vertex.setSuffix(self.tr(" layer units"))
        self.topo_vertex.setToolTip(self.tr(
            "Vertices closer than this are reported; segments shorter\n"
            "than 10x this are reported as short segments"))
        form.addRow(self.tr("Vertex tolerance:"), self.topo_vertex)

        note = QLabel(self.tr(
            "Defaults for the Topology Validator panel."))
        note.setWordWrap(True)
        form.addRow(note)

        self.tabs.addTab(tab, self.tr("Topology"))

    # --- load/save ---

    def _load(self, getter=settings_service.get):
        """Fill the widgets from getter(key) - settings_service.get on open, _default for Restore defaults, which must not write anything before OK."""
        self.snap_tolerance.setValue(
            getter("snapping/tolerance_px"))
        stored = getter("snapping/type")
        self.snap_type.setCurrentIndex(
            _SNAP_TYPES.index(stored) if stored in _SNAP_TYPES else 0)
        self.snap_intersection.setChecked(
            getter("snapping/intersection"))
        self.snap_topological.setChecked(
            getter("snapping/topological_editing"))
        self.area_show.setChecked(getter("area/show_readout"))
        stored_units = getter("area/units")
        self.area_units.setCurrentIndex(
            _AREA_UNITS.index(stored_units) if stored_units in _AREA_UNITS
            else 0)
        stored_secondary = getter("area/units_secondary")
        self.area_units_secondary.setCurrentIndex(
            _AREA_UNITS_SECONDARY.index(stored_secondary)
            if stored_secondary in _AREA_UNITS_SECONDARY else 0)
        self.cad_mode_enabled.setChecked(
            getter("cad_mode/enabled"))
        self.cad_mode_dark.setChecked(
            getter("cad_mode/dark_canvas"))
        self.cad_mode_grid.setChecked(getter("cad_mode/grid"))
        self.cad_mode_bar.setChecked(
            getter("cad_mode/command_bar"))
        self.cad_mode_strip.setChecked(
            getter("cad_mode/status_strip"))
        self.cad_mode_basemap.setText(
            getter("cad_mode/basemap_url"))
        for sub in (self.cad_mode_dark, self.cad_mode_grid,
                    self.cad_mode_bar, self.cad_mode_strip,
                    self.cad_mode_basemap):
            sub.setEnabled(self.cad_mode_enabled.isChecked())
        self.vertex_segment.setValue(
            getter("vertex_cleaner/segment_tolerance"))
        self.vertex_dup.setValue(
            getter("vertex_cleaner/dup_tolerance"))
        self.vertex_snap.setValue(
            getter("vertex_cleaner/snap_tolerance"))
        self.topo_snap.setValue(
            getter("topology/snap_tolerance"))
        self.topo_gap_area.setValue(
            getter("topology/gap_min_area"))
        self.topo_gap_buffer.setValue(
            getter("topology/gap_buffer"))
        self.topo_vertex.setValue(
            getter("topology/vertex_tolerance"))

    def accept(self):
        if not self._check_basemap_url():
            return
        settings_service.set_("snapping/tolerance_px",
                              self.snap_tolerance.value())
        settings_service.set_("snapping/type",
                              _SNAP_TYPES[self.snap_type.currentIndex()])
        settings_service.set_("snapping/intersection",
                              self.snap_intersection.isChecked())
        settings_service.set_("snapping/topological_editing",
                              self.snap_topological.isChecked())
        settings_service.set_("area/show_readout",
                              self.area_show.isChecked())
        settings_service.set_("area/units",
                              _AREA_UNITS[self.area_units.currentIndex()])
        settings_service.set_(
            "area/units_secondary",
            _AREA_UNITS_SECONDARY[
                self.area_units_secondary.currentIndex()])
        settings_service.set_("cad_mode/enabled",
                              self.cad_mode_enabled.isChecked())
        settings_service.set_("cad_mode/dark_canvas",
                              self.cad_mode_dark.isChecked())
        settings_service.set_("cad_mode/grid",
                              self.cad_mode_grid.isChecked())
        settings_service.set_("cad_mode/command_bar",
                              self.cad_mode_bar.isChecked())
        settings_service.set_("cad_mode/status_strip",
                              self.cad_mode_strip.isChecked())
        settings_service.set_("cad_mode/basemap_url",
                              self.cad_mode_basemap.text().strip())
        settings_service.set_("vertex_cleaner/segment_tolerance",
                              self.vertex_segment.value())
        settings_service.set_("vertex_cleaner/dup_tolerance",
                              self.vertex_dup.value())
        settings_service.set_("vertex_cleaner/snap_tolerance",
                              self.vertex_snap.value())
        settings_service.set_("topology/snap_tolerance",
                              self.topo_snap.value())
        settings_service.set_("topology/gap_min_area",
                              self.topo_gap_area.value())
        settings_service.set_("topology/gap_buffer",
                              self.topo_gap_buffer.value())
        settings_service.set_("topology/vertex_tolerance",
                              self.topo_vertex.value())
        super().accept()

    def _check_basemap_url(self):
        """XYZ tiles need the {z}/{x}/{y} placeholders or the URL only fails later, when F8 cannot build the layer. Skipped while CAD Mode is off since the field is disabled then."""
        url = self.cad_mode_basemap.text().strip()
        if (not self.cad_mode_enabled.isChecked() or not url
                or all(p in url for p in ("{z}", "{x}", "{y}"))):
            self.cad_mode_basemap_note.setVisible(False)
            return True
        self.cad_mode_basemap_note.setText(self.tr(
            "The basemap URL needs the {z}, {x} and {y} tile "
            "placeholders, as in "
            "https://tile.openstreetmap.org/{z}/{x}/{y}.png - or leave "
            "it empty for no basemap."))
        self.cad_mode_basemap_note.setVisible(True)
        self.tabs.setCurrentIndex(self._cad_tab)
        self.cad_mode_basemap.setFocus()
        return False

    def _restore_defaults(self):
        # only the widgets shown here, and only in memory - writes still happen on OK, so Cancel discards
        if self.confirm_action(
                self.tr("Restore defaults"),
                self.tr("Reset the settings in this dialog to their "
                        "defaults? They are stored when you press OK.")):
            self._load(_default)
