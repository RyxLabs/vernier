# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Field-type constants that resolve on both Qt5 and Qt6 builds of QGIS."""

from qgis.PyQt.QtCore import QVariant  # type: ignore

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

__all__ = ["FIELD_DOUBLE", "FIELD_INT", "FIELD_LONGLONG", "FIELD_STRING"]
