# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Per-dialog widget values, so a tool reopens with whatever it was last run with instead of its factory defaults."""

# one key per (dialog, widget) pair, so this can't live in settings_service's static DEFAULTS - it talks to QgsSettings directly, like label_memory. reset_all() clears the whole Vernier group, this included

from qgis.core import QgsSettings  # type: ignore

GROUP = "Vernier/dialog_state"


def _key(dialog_name: str, name: str) -> str:
    return f"{GROUP}/{dialog_name}/{name}"


def load(dialog_name: str, name: str, default=None):
    """Stored value, or default when the dialog was never run."""
    return QgsSettings().value(_key(dialog_name, name), default)


def save(dialog_name: str, name: str, value):
    QgsSettings().setValue(_key(dialog_name, name), value)
