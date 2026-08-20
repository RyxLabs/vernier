# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Constants that resolve across every supported QGIS build, Qt5 and Qt6 alike."""

from qgis.PyQt.QtCore import QVariant  # type: ignore
from qgis.core import QgsGeometry  # type: ignore

# PyQt6 dropped the QVariant.Type enum. QgsField only grew its QMetaType overload in 3.38, which is also the first Qt6 build, so the two branches never overlap and one import-time probe covers 3.28 through 4.x
try:  # Qt5 (QGIS 3.28 - 3.4x)
    FIELD_STRING = QVariant.String
    FIELD_INT = QVariant.Int
    FIELD_LONGLONG = QVariant.LongLong
    FIELD_DOUBLE = QVariant.Double
except AttributeError:  # Qt6 (QGIS 3.38+ built against Qt6, QGIS 4)
    from qgis.PyQt.QtCore import QMetaType  # type: ignore

    FIELD_STRING = QMetaType.Type.QString
    FIELD_INT = QMetaType.Type.Int
    FIELD_LONGLONG = QMetaType.Type.LongLong
    FIELD_DOUBLE = QMetaType.Type.Double


def _validator_geos(source):
    """The GEOS validation method off a QgsGeometry-shaped class. On 3.28 ValidationMethod exists but carries no members and the names sit on the class itself, so a present namespace is not enough - the member has to be checked for. Looked up by name so neither spelling is pinned as an attribute access."""
    scope = getattr(source, "ValidationMethod", None)
    found = getattr(scope, "ValidatorGeos", None)
    if found is None:
        found = getattr(source, "ValidatorGeos")
    return found


VALIDATOR_GEOS = _validator_geos(QgsGeometry)

__all__ = ["FIELD_DOUBLE", "FIELD_INT", "FIELD_LONGLONG", "FIELD_STRING",
           "VALIDATOR_GEOS"]
