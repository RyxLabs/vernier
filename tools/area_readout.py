# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Live area readout on the status bar - "n features | area" for the current polygon selection, measured with QgsDistanceArea so it holds up in a geographic CRS, which planar geometry.area() does not."""

from qgis.PyQt.QtCore import QObject, Qt, QTimer  # type: ignore
from qgis.PyQt.QtWidgets import QLabel  # type: ignore
from qgis.core import (  # type: ignore
    QgsCsException, QgsDistanceArea, QgsFeatureRequest, QgsProject,
    QgsUnitTypes, QgsVectorLayer, QgsWkbTypes,
)

from ..i18n import tr as _tr
from ..services import settings_service

# unit key -> (factor from square meters, suffix, decimals)
_UNITS = {
    "m2": (1.0, "m²", 2),
    "ha": (1.0 / 10_000.0, "ha", 4),
    "km2": (1.0 / 1_000_000.0, "km²", 4),
    "acres": (1.0 / 4_046.8564224, "ac", 4),
    "ft2": (1.0 / 0.09290304, "ft²", 2),
}

# geometryChanged fires once per feature, so dragging an n-feature selection would otherwise measure the whole selection n times. one pass per burst instead, short enough to read as live
_DEBOUNCE_MS = 150

# valid values for area/units, in the settings tab's combo order
UNIT_MODES = ("auto", "m2", "ha", "km2", "acres", "ft2")

# valid values for area/units_secondary - no second figure, one derived from the primary, or a fixed unit
SECONDARY_MODES = ("none", "auto") + UNIT_MODES[1:]

# companion unit when the secondary is left on "auto", keyed by the resolved primary. always one step up or down the ladder, never two - m² pairs with ha and not km², because 850 m² prints as 0.0000 km² and a second figure that reads as zero is worse than none
_AUTO_SECONDARY = {
    "m2": "ha",
    "ha": "m2",
    "km2": "ha",
    "acres": "ha",     # metric companion for acres
    "ft2": "acres",
}


def resolve_units(sqm: float, units: str = "auto") -> str:
    """The concrete unit a setting resolves to at this magnitude - "auto" takes m² below 1 ha, hectares up to 100, km² above, and anything unknown falls back to m²."""
    if units == "auto":
        if sqm < 10_000.0:
            return "m2"
        if sqm < 1_000_000.0:
            return "ha"
        return "km2"
    return units if units in _UNITS else "m2"


def _one(sqm: float, units: str) -> str:
    factor, suffix, decimals = _UNITS[units]
    return f"{sqm * factor:.{decimals}f} {suffix}"


def format_area(sqm: float, units: str = "auto",
                secondary: str = "none") -> str:
    """Readable area out of a value in square meters, with the secondary figure in parentheses. The secondary is dropped when it's none, unknown, or resolves to the same unit as the primary, so picking ha for both never gives "0.5000 ha (0.5000 ha)"."""
    units = resolve_units(sqm, units)
    text = _one(sqm, units)

    if secondary == "auto":
        secondary = _AUTO_SECONDARY.get(units)
    if not secondary or secondary == units or secondary not in _UNITS:
        return text
    return f"{text} ({_one(sqm, secondary)})"


def area_calculator(crs, project) -> QgsDistanceArea:
    """QgsDistanceArea for a layer CRS, ellipsoidal where that matters. A project carries no ellipsoid by default, which on a geographic layer means square degrees converted with a fixed factor that ignores latitude and comes out tens of percent off, so use the CRS's own ellipsoid there."""
    calc = QgsDistanceArea()
    calc.setSourceCrs(crs, project.transformContext())
    ellipsoid = project.ellipsoid()
    if crs.isGeographic() and (not ellipsoid or ellipsoid.upper() == "NONE"):
        if not calc.setEllipsoid(crs.ellipsoidAcronym()):
            calc.setEllipsoid("WGS84")
    else:
        calc.setEllipsoid(ellipsoid)
    return calc


class AreaReadout(QObject):
    """Owns the status-bar label and the active-layer signal wiring. Built in initGui(), cleanup() comes from unload()."""

    def tr(self, text: str) -> str:
        # one "Vernier" context for the whole plugin, see i18n.py. QObject.tr would use the class name instead and these strings would land outside it
        return _tr(text)

    def __init__(self, iface):
        super().__init__()
        self.iface = iface
        self._layer = None
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # reserve room or the status bar jumps as text comes and goes
        self._label.setMinimumWidth(
            self._label.fontMetrics().horizontalAdvance(
                "0000 features | 000000.0000 km² (000000.0000 ha)"))
        iface.statusBarIface().addPermanentWidget(self._label)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._update)

        iface.mapCanvas().currentLayerChanged.connect(self._on_layer_changed)
        QgsProject.instance().layersWillBeRemoved.connect(
            self._on_layers_removed)
        self._on_layer_changed(iface.activeLayer())

    # --- lifecycle ---

    def cleanup(self):
        """Disconnect everything and drop the label. unload() calls this."""
        self._disconnect_layer()
        # a queued timeout would reach _update after the label is gone
        self._debounce.stop()
        try:
            self.iface.mapCanvas().currentLayerChanged.disconnect(
                self._on_layer_changed)
        except (TypeError, RuntimeError):
            pass
        try:
            QgsProject.instance().layersWillBeRemoved.disconnect(
                self._on_layers_removed)
        except (TypeError, RuntimeError):
            pass
        if self._label is not None:
            try:
                self.iface.statusBarIface().removeWidget(self._label)
            except RuntimeError:
                pass
            self._label.deleteLater()
            self._label = None

    def refresh(self):
        """Re-read the settings and repaint, after the settings dialog for instance."""
        self._update()

    # --- signal wiring ---

    def _on_layer_changed(self, layer=None):
        if layer is None:
            layer = self.iface.activeLayer()
        self._disconnect_layer()
        if isinstance(layer, QgsVectorLayer):
            self._layer = layer
            layer.selectionChanged.connect(self._schedule)
            layer.geometryChanged.connect(self._schedule)
        self._update()

    def _schedule(self, *args):
        """Coalesce a burst of selection or geometry signals into one recompute."""
        self._debounce.start()

    def _disconnect_layer(self):
        if self._layer is None:
            return
        try:
            self._layer.selectionChanged.disconnect(self._schedule)
            self._layer.geometryChanged.disconnect(self._schedule)
        except (TypeError, RuntimeError):
            pass  # never connected, or the C++ object is gone
        self._layer = None

    def _on_layers_removed(self, layer_ids):
        if self._layer is None:
            return
        try:
            still_present = self._layer.id() not in layer_ids
        except RuntimeError:
            still_present = False
        if not still_present:
            self._disconnect_layer()
            self._update()

    # --- display ---

    def _update(self, *args):
        if self._label is None:
            return
        if not settings_service.get("area/show_readout"):
            self._label.setVisible(False)
            return
        text = self._readout_text()
        self._label.setText(text)
        # hidden when empty so the status bar doesn't hold dead space
        self._label.setVisible(bool(text))

    def _readout_text(self) -> str:
        layer = self._layer
        if layer is None:
            return ""
        try:
            count = layer.selectedFeatureCount()
            if (layer.geometryType() != QgsWkbTypes.PolygonGeometry
                    or count == 0):
                return ""
            # geometries only - this runs on every vertex edit while the selection lives and we never read attributes
            request = QgsFeatureRequest().setFilterFids(
                layer.selectedFeatureIds()).setNoAttributes()
            features = layer.getFeatures(request)
            crs = layer.crs()
        except RuntimeError:
            self._layer = None  # C++ object deleted under us
            return ""

        calc = area_calculator(crs, QgsProject.instance())

        # measureArea raises QgsCsException when the layer CRS won't transform, and this runs inside a signal handler so nothing may propagate
        try:
            total = 0.0
            for feature in features:
                geom = feature.geometry()
                if geom is None or geom.isEmpty():
                    continue
                total += calc.measureArea(geom)
            # measureArea() units follow the CRS and ellipsoid, normalize to m²
            sqm = calc.convertAreaMeasurement(
                total, QgsUnitTypes.AreaSquareMeters)
        except QgsCsException:
            return ""

        if count == 1:
            prefix = self.tr("1 feature")
        else:
            prefix = self.tr("{0} features").format(count)
        return f"{prefix} | " + format_area(
            sqm,
            settings_service.get("area/units"),
            settings_service.get("area/units_secondary"))
