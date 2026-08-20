# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Smart Snapping - one click applies a whole snapping profile that QGIS otherwise spreads across five controls, and hands the project's own values back when the toggle goes off so nothing half-applied is left behind."""

from qgis.core import (  # type: ignore
    Qgis, QgsProject, QgsSnappingConfig, QgsTolerance,
)

from ..services import settings_service

_TYPE_FLAGS = {
    "vertex_and_segment": Qgis.SnappingType.Vertex | Qgis.SnappingType.Segment,
    "vertex": Qgis.SnappingType.Vertex,
    "segment": Qgis.SnappingType.Segment,
}

# what the profile overwrote, held while it's applied so the toggle can put it back. None means we never applied it, and then turning snapping off has to leave the rest of the setup alone because it's the user's own. only the fields the profile writes get stored, per-layer settings stay in the project config so an advanced setup survives the round trip
_replaced = None


def _wanted_type_flag():
    """Snapping type flag for the stored profile."""
    return _TYPE_FLAGS.get(settings_service.get("snapping/type"),
                           _TYPE_FLAGS["vertex_and_segment"])


def _profile_fields(config):
    """The fields the profile writes, as one comparable tuple."""
    return (config.mode(), config.typeFlag(), config.tolerance(),
            config.units(), config.intersectionSnapping())


def _write_profile_fields(config, fields):
    """Write a _profile_fields tuple back into a snapping config."""
    mode, type_flag, tolerance, units, intersection = fields
    config.setMode(mode)
    config.setTypeFlag(type_flag)
    config.setTolerance(tolerance)
    config.setUnits(units)
    config.setIntersectionSnapping(intersection)


def is_enabled() -> bool:
    """Is snapping on for this project."""
    return QgsProject.instance().snappingConfig().enabled()


def toggle() -> bool:
    """Toggle snapping with the saved profile, returning the new state. Turning it on records what it replaced and turning it off puts that back, but only while the values on screen are still the ones the profile wrote - anything the user changed in the meantime wins. The project dirty flag is preserved, or this would earn a save prompt for a change nobody made to project data."""
    global _replaced
    project = QgsProject.instance()
    was_dirty = project.isDirty()
    config = project.snappingConfig()

    if config.enabled():
        replaced = _replaced
        _replaced = None
        if replaced is not None and replaced[0] == _profile_fields(config):
            _write_profile_fields(config, replaced[1])
            project.setTopologicalEditing(replaced[2])
        config.setEnabled(False)
        enabled = False
    else:
        old_fields = _profile_fields(config)
        was_topological = project.topologicalEditing()
        config.setEnabled(True)
        config.setMode(QgsSnappingConfig.SnappingMode.AllLayers)
        config.setTypeFlag(_wanted_type_flag())
        config.setIntersectionSnapping(
            settings_service.get("snapping/intersection"))
        config.setTolerance(settings_service.get("snapping/tolerance_px"))
        config.setUnits(QgsTolerance.UnitType.Pixels)
        project.setTopologicalEditing(
            settings_service.get("snapping/topological_editing"))
        _replaced = (_profile_fields(config), old_fields, was_topological)
        enabled = True

    project.setSnappingConfig(config)
    project.setDirty(was_dirty)
    return enabled
