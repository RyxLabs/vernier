# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pull identical geometries out into a review layer. Two features count as duplicates when their normalized WKB matches, so a rotated vertex order or a reversed ring can't hide one - though it isn't full topological equality, an extra collinear vertex still makes a geometry distinct."""

from qgis.PyQt.QtCore import Qt  # type: ignore
from qgis.PyQt.QtWidgets import QProgressDialog  # type: ignore
from qgis.core import (  # type: ignore
    Qgis, QgsFeature, QgsFeatureRequest, QgsField, QgsProject, QgsVectorLayer,
    QgsWkbTypes,
)

from ..qt_compat import FIELD_INT
from ..i18n import tr as _tr
from ..services import error_styles, topology_service


def find_duplicates(layer, progress=None) -> list:
    """Groups of features sharing one geometry, two or more per group, as whole QgsFeatures so the review layer can carry their attributes. Detection is topology_service.duplicate_groups - one definition of "duplicate" for the plugin, so this button and the Topology Validator cannot report different counts for the same layer. Invalid geometries take part on purpose: duplicates of broken geometries are still worth reviewing. progress gets 0-100 once per feature and returning False from it stops the scan."""
    def report(percent):
        if progress is None:
            return None
        return progress(min(100, int(percent)))

    groups = topology_service.duplicate_groups(layer, progress=report)
    if not groups:
        return []
    # the grouping pass keeps geometries only, and the review layer needs the attributes too, so the members come back in one request keyed by id
    wanted = [fid for group in groups for fid, _geom in group]
    by_id = {feature.id(): feature for feature in layer.getFeatures(
        QgsFeatureRequest().setFilterFids(wanted))}
    resolved = []
    for group in groups:
        members = [by_id[fid] for fid, _geom in group if fid in by_id]
        if len(members) > 1:
            resolved.append(members)
    return resolved


def run(iface):
    """Pull the duplicate groups off the active layer into a review layer."""
    title = _tr("Duplicate Geometries")

    layer = iface.activeLayer()
    if not isinstance(layer, QgsVectorLayer) or not layer.isValid():
        iface.messageBar().pushMessage(
            title, _tr("Select a vector layer first."),
            level=Qgis.MessageLevel.Warning, duration=5)
        return

    dialog = QProgressDialog(
        _tr("Looking for duplicate geometries..."), _tr("Cancel"), 0, 100,
        iface.mainWindow())
    dialog.setWindowTitle(title)
    # modal, so setValue() pumps the event loop and Cancel is actually clickable
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    # a layer small enough to finish right away never shows the dialog
    dialog.setMinimumDuration(500)

    def report(percent):
        dialog.setValue(percent)
        return not dialog.wasCanceled()

    try:
        groups = find_duplicates(layer, report)
        canceled = dialog.wasCanceled()
    finally:
        dialog.close()
    if canceled:
        return

    if not groups:
        iface.messageBar().pushMessage(
            title,
            _tr('No duplicate geometries found in "{0}".').format(
                layer.name()),
            level=Qgis.MessageLevel.Info, duration=5)
        return

    # group_id must not shadow a source field
    taken = {field.name().lower() for field in layer.fields()}
    group_field = "group_id"
    suffix = 1
    while group_field.lower() in taken:
        suffix += 1
        group_field = f"group_id_{suffix}"

    review = QgsVectorLayer(QgsWkbTypes.displayString(layer.wkbType()),
                            _tr("Duplicate geometries"), "memory")
    if not review.isValid():
        iface.messageBar().pushMessage(
            title, _tr("Could not create the review layer."),
            level=Qgis.MessageLevel.Critical, duration=5)
        return
    review.setCrs(layer.crs())
    provider = review.dataProvider()
    if not provider.addAttributes(
            list(layer.fields()) + [QgsField(group_field, FIELD_INT)]):
        iface.messageBar().pushMessage(
            title, _tr("Could not create the review layer fields."),
            level=Qgis.MessageLevel.Critical, duration=5)
        return
    review.updateFields()

    # review fields are the source fields plus group_id at the end, so attributes() + [group_id] lines up with no name matching
    copies = []
    for group_id, group in enumerate(groups, start=1):
        for feature in group:
            copy = QgsFeature(review.fields())
            copy.setGeometry(feature.geometry())
            copy.setAttributes(feature.attributes() + [group_id])
            copies.append(copy)
    ok, added = provider.addFeatures(copies)
    review.updateExtents()
    # same violet the Topology Validator marks duplicates with, so the two views of one problem read as one thing
    review.setRenderer(error_styles.duplicate_renderer(review.geometryType()))
    QgsProject.instance().addMapLayer(review)

    if not ok or len(added) != len(copies):
        iface.messageBar().pushMessage(
            title,
            _tr("Copied {0} of {1} duplicate features - "
                "some could not be added to the review layer.").format(
                    len(added), len(copies)),
            level=Qgis.MessageLevel.Warning, duration=8)
        return
    if len(groups) == 1:
        message = _tr("Found {0} duplicate features in 1 group.").format(
            len(added))
    else:
        message = _tr("Found {0} duplicate features in {1} groups.").format(
            len(added), len(groups))
    iface.messageBar().pushMessage(
        title, message, level=Qgis.MessageLevel.Success, duration=5)
