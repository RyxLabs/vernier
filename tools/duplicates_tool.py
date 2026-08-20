# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pull identical geometries out into a review layer. Two features count as duplicates when their normalized WKB matches, so a rotated vertex order or a reversed ring can't hide one - though it isn't full topological equality, an extra collinear vertex still makes a geometry distinct."""

from qgis.PyQt.QtCore import Qt  # type: ignore
from qgis.PyQt.QtWidgets import QProgressDialog  # type: ignore
from qgis.core import (  # type: ignore
    Qgis, QgsFeature, QgsField, QgsGeometry, QgsProject, QgsVectorLayer,
    QgsWkbTypes,
)

from ..qt_compat import FIELD_INT
from ..i18n import tr as _tr


def _normalized_wkb(geometry: QgsGeometry) -> bytes:
    # normalize() mutates, so hash a copy. after it a rotated vertex order and a reversed ring give byte-identical WKB
    geom = QgsGeometry(geometry)
    geom.normalize()
    return bytes(geom.asWkb())


def find_duplicates(layer, progress=None) -> list:
    """Group the features by identical normalized geometry, two or more per group. Invalid geometries take part on purpose - duplicates of broken geometries are still worth reviewing. progress gets 0-100 once per feature and returning False from it stops the scan."""
    by_wkb = {}
    total = layer.featureCount()
    if total < 1:
        total = 1  # unknown count, the bar just runs to the end
    done = 0
    for feature in layer.getFeatures():
        geom = feature.geometry()
        if geom is not None and not geom.isEmpty():
            by_wkb.setdefault(_normalized_wkb(geom), []).append(feature)
        done += 1
        if progress is not None:
            if progress(min(100, int(done * 100 / total))) is False:
                break
    return [group for group in by_wkb.values() if len(group) > 1]


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
