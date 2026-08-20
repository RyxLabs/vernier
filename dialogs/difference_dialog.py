# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Difference dialog: wraps native:difference via OverlayDialog."""

from .overlay_dialog import OverlayDialog


class DifferenceDialog(OverlayDialog):

    ALGORITHM = "native:difference"
    NAME_SUFFIX = "difference"

    def title(self):
        return self.tr("Difference")
