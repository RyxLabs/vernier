# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""One translation helper for the whole plugin - every string goes through the "Vernier" context instead of a per-module copy."""

from qgis.PyQt.QtCore import QCoreApplication  # type: ignore


def tr(text: str) -> str:
    return QCoreApplication.translate("Vernier", text)
