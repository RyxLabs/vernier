# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Per-field label prefix/suffix memory, shared by the KMZ export, DXF export and Quick Symbology dialogs so a field labelled once is prefilled the same way everywhere after."""

# one key per field name ever used, so this can't live in settings_service's static DEFAULTS and talks to QgsSettings directly

from qgis.core import QgsSettings  # type: ignore

GROUP = "Vernier/export_labels"


def _key(field_name: str) -> str:
    # a slash in a field name would nest settings groups
    safe = field_name.lower().replace("/", "_").replace("\\", "_")
    return f"{GROUP}/{safe}"


def load_default(field_name: str):
    """Remembered (prefix, suffix) for a field, ("", "") if there isn't one."""
    base = _key(field_name)
    settings = QgsSettings()
    prefix = settings.value(f"{base}/prefix", "")
    suffix = settings.value(f"{base}/suffix", "")
    return str(prefix or ""), str(suffix or "")


def save_default(field_name: str, prefix: str, suffix: str):
    base = _key(field_name)
    settings = QgsSettings()
    settings.setValue(f"{base}/prefix", prefix)
    settings.setValue(f"{base}/suffix", suffix)
