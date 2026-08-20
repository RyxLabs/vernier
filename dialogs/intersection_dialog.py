# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Intersection dialog: wraps native:intersection via OverlayDialog."""

from .overlay_dialog import OverlayDialog


class IntersectionDialog(OverlayDialog):

    ALGORITHM = "native:intersection"
    NAME_SUFFIX = "intersection"

    def title(self):
        return self.tr("Intersection")
