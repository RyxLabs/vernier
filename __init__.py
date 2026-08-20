# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Vernier - vector editing toolkit for QGIS."""


def classFactory(iface):
    """Entry point QGIS calls to load the plugin."""
    # libs/ holds pip --target installs of the optional deps (ezdxf), so it has to be on sys.path before any dialog imports them
    from .services import deps
    deps.bootstrap_sys_path()
    from .vernier import Vernier
    return Vernier(iface)
