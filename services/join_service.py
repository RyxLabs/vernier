# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""UI-free helpers shared by the two join dialogs. Keys are normalized so int 1, float 1.0 and "1" all match - plain str() misses float keys, which is a silent join failure against DBF numerics since those are always doubles."""

import math

from qgis.PyQt.QtCore import QVariant  # type: ignore


def is_missing(value) -> bool:
    """True for None and for the NULL QVariant QGIS hands back."""
    return value is None or (isinstance(value, QVariant) and value.isNull())


def normalize_key(value):
    """Canonical string form of a join key, None when missing. Ints and integral floats collapse together so DBF numerics still match text keys, and blank strings count as missing - matching every blank row to every other blank row is never the join anyone meant."""
    if is_missing(value):
        return None
    if isinstance(value, QVariant):
        value = value.value()
        if value is None:
            return None
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, int):
        return str(int(value))
    text = str(value).strip()
    return text if text else None


def key_set(layer, key_field) -> set:
    """Normalized keys present in the layer, missing ones left out."""
    keys = set()
    for feature in layer.getFeatures():
        key = normalize_key(feature[key_field])
        if key is not None:
            keys.add(key)
    return keys


def build_key_map(layer, key_field, columns) -> dict:
    """{normalized_key: {column: value}} over the whole layer. Later features win on duplicate keys, same as QGIS's own joins do."""
    data = {}
    for feature in layer.getFeatures():
        key = normalize_key(feature[key_field])
        if key is None:
            continue
        data[key] = {column: feature[column] for column in columns}
    return data


def count_matches(target_layer, target_key, source_keys) -> int:
    """How many target features carry a key that's in source_keys."""
    count = 0
    for feature in target_layer.getFeatures():
        key = normalize_key(feature[target_key])
        if key is not None and key in source_keys:
            count += 1
    return count


def dedupe_preserve_order(values) -> list:
    seen = set()
    out = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out

