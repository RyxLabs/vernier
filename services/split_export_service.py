# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Split vector layers by the values of a field and write one DXF per group. No UI here - it takes layers_config in the shape dxf_export_service.export_layers_to_dxf wants and calls that per group."""

import datetime
import os
import re
from collections import OrderedDict, namedtuple

from qgis.core import (  # type: ignore
    Qgis, QgsFeature, QgsMessageLog, QgsVectorLayer, QgsWkbTypes,
)

PLUGIN_NAME = "Vernier"

DEFAULT_TEMPLATE = "{value}"

DXF_EXTENSION = ".dxf"


SplitResult = namedtuple(
    "SplitResult",
    ["total_groups", "files_written", "total_success", "total_skip",
     "total_errors", "per_group", "output_dir"],
)
# per_group holds {value, paths, ok, skip, err, error_message} dicts


# --- filename utilities ---

# only the characters Windows and POSIX actually reject. accents, spaces, parentheses and brackets are all fine
_FS_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# Windows refuses these as a stem whatever the extension - CON.dxf and COM1.dxf both fail to create. applied everywhere so a shared folder behaves the same from Linux
_WIN_RESERVED = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", re.IGNORECASE)


def safe_filename(name):
    """Make a name filesystem-safe without going overboard - illegal characters out, trailing dots and spaces trimmed since Windows refuses those, and the reserved device names prefixed."""
    if name is None:
        return "NULL"
    s = _FS_ILLEGAL.sub("_", str(name))
    s = s.rstrip(". ")
    if _WIN_RESERVED.match(s):
        s = "_" + s
    return s or "_"


def render_filename(template, value, layer_name="", field_name="",
                    sample_feature=None):
    """Build a filename from a template, no extension. Placeholders are {value}, {layer}, {date}, {field}, and any attribute name off sample_feature."""
    if not template:
        template = DEFAULT_TEMPLATE

    placeholders = {
        "value": "NULL" if value is None else str(value),
        "layer": layer_name or "",
        "date": datetime.date.today().isoformat(),
        "field": field_name or "",
    }

    if sample_feature is not None:
        try:
            for f in sample_feature.fields():
                fname = f.name()
                if fname not in placeholders:
                    fval = sample_feature[fname]
                    placeholders[fname] = "" if fval is None else str(fval)
        except Exception:
            pass

    result = template
    for key, val in placeholders.items():
        result = result.replace("{" + key + "}", val)

    # unknown placeholders just lose their braces, {foo} becomes foo
    result = re.sub(r"\{([^}]*)\}", r"\1", result)
    return safe_filename(result)


# --- field discovery ---

def get_common_fields(layers):
    """Field names present in every layer, in the first layer's order."""
    if not layers:
        return []
    first_order = [f.name() for f in layers[0].fields()]
    common = set(first_order)
    for lyr in layers[1:]:
        common &= {f.name() for f in lyr.fields()}
    return [n for n in first_order if n in common]


def get_unique_values(layers, field, selected_only=False):
    """Count features per distinct value of field across all the layers, as an OrderedDict sorted by value with None last. Layers without the field are skipped."""
    counts = {}
    for layer in layers:
        idx = layer.fields().indexOf(field)
        if idx < 0:
            continue
        features = (layer.getSelectedFeatures() if selected_only
                    else layer.getFeatures())
        for feat in features:
            val = feat.attribute(idx)
            counts[val] = counts.get(val, 0) + 1

    # real values first, string-sorted, None at the end
    keyed = sorted(
        counts.items(),
        key=lambda kv: (kv[0] is None, str(kv[0]) if kv[0] is not None else ""),
    )
    return OrderedDict(keyed)


# --- split into memory layers ---

def bucket_by_field(layer, field, selected_only=False):
    """Every feature grouped by its value of field, in one pass. Callers that walk all the values need this - matching per value instead reads the whole layer once per group."""
    idx = layer.fields().indexOf(field)
    if idx < 0:
        return {}
    features = (layer.getSelectedFeatures() if selected_only
                else layer.getFeatures())
    buckets = {}
    for feat in features:
        buckets.setdefault(feat.attribute(idx), []).append(feat)
    return buckets


def build_split_layer(source, field, value, selected_only=False,
                      matched=None):
    """Memory layer holding the features where field == value, mirroring the source's fields, CRS, geometry type and name. Not added to the project, the caller owns its lifetime. matched skips the scan when the caller already bucketed the layer with bucket_by_field."""
    idx = source.fields().indexOf(field)
    if idx < 0:
        return None

    geom_map = {
        QgsWkbTypes.PointGeometry: "Point",
        QgsWkbTypes.LineGeometry: "LineString",
        QgsWkbTypes.PolygonGeometry: "Polygon",
    }
    base_geom = geom_map.get(source.geometryType(), "Polygon")
    # match multipart-ness or the writers downcast
    is_multi = QgsWkbTypes.isMultiType(source.wkbType())
    geom_str = f"Multi{base_geom}" if is_multi else base_geom

    mem = QgsVectorLayer(geom_str, source.name(), "memory")
    if not mem.isValid():
        return None
    # setCrs rather than a ?crs= URI, custom CRS have an empty authid
    mem.setCrs(source.crs())

    prov = mem.dataProvider()
    prov.addAttributes(source.fields())
    mem.updateFields()

    if matched is None:
        features = (source.getSelectedFeatures() if selected_only
                    else source.getFeatures())
        matched = [f for f in features if f.attribute(idx) == value]

    copied = []
    for feat in matched:
        new_feat = QgsFeature(mem.fields())
        new_feat.setGeometry(feat.geometry())
        new_feat.setAttributes(feat.attributes())
        copied.append(new_feat)

    if copied:
        prov.addFeatures(copied)
        mem.updateExtents()
    return mem


# --- orchestrator ---

def _sample_from_buckets(layers_config, buckets, value):
    """(primary layer name, sample feature) out of already-bucketed layers - the first configured layer holding the value, and its first match."""
    for cfg, bucket in zip(layers_config, buckets):
        matched = bucket.get(value)
        if matched:
            return cfg["layer"].name(), matched[0]
    return "", None


def predict_split_filenames(layers_config, group_field, values, template,
                            selected_only=False):
    """The bare filenames export_split_groups would write, empty groups skipped the same way the export skips them."""
    buckets = [bucket_by_field(cfg["layer"], group_field, selected_only)
               for cfg in layers_config]
    names = []
    for value in values:
        layer_name, sample = _sample_from_buckets(
            layers_config, buckets, value)
        if sample is None:
            continue
        base = render_filename(template, value, layer_name, group_field,
                               sample)
        names.append(base + DXF_EXTENSION)
    return names


def export_split_groups(layers_config, group_field, values, output_dir,
                        template, selected_only=False,
                        progress_callback=None):
    """For every value, build the temp layers and write them into one DXF per group - every checked layer lands in that same file, as its own DXF layer. Returns a SplitResult."""
    from .dxf_export_service import export_layers_to_dxf

    if not os.path.isdir(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    values = list(values)
    total = len(values)
    # one pass per source layer up front, so the value loop below is lookups rather than a full rescan each time round
    buckets = [bucket_by_field(cfg["layer"], group_field, selected_only)
               for cfg in layers_config]
    per_group = []
    files_written = 0
    total_success = 0
    total_skip = 0
    total_errors = 0

    for i, value in enumerate(values):
        group_config = []
        sample_feat = None
        primary_layer_name = ""
        total_in_group = 0

        for cfg, bucket in zip(layers_config, buckets):
            source = cfg["layer"]
            matched = bucket.get(value)
            if not matched:
                continue
            temp = build_split_layer(source, group_field, value, selected_only,
                                     matched=matched)
            if temp is None or temp.featureCount() == 0:
                continue
            total_in_group += temp.featureCount()
            if sample_feat is None:
                sample_feat = matched[0]
            if not primary_layer_name:
                primary_layer_name = source.name()

            new_cfg = dict(cfg)
            new_cfg["layer"] = temp
            group_config.append(new_cfg)

        if not group_config:
            per_group.append({
                "value": value, "paths": [],
                "ok": 0, "skip": 0, "err": 0,
                "error_message": "No features for this value.",
            })
            continue

        # same rule predict_split_filenames uses for the overwrite check
        base_name = render_filename(
            template, value, primary_layer_name, group_field, sample_feat,
        )

        def _inner_progress(current, total_features, group_idx=i,
                            tig=total_in_group):
            if progress_callback:
                progress_callback(group_idx, total, current, tig)

        paths_written = []
        group_msgs = []
        dxf_path = os.path.join(output_dir, base_name + DXF_EXTENSION)
        try:
            group_ok, group_skip, group_err = export_layers_to_dxf(
                layers_config=group_config,
                output_path=dxf_path,
                progress_callback=_inner_progress,
            )
            if group_ok > 0:
                paths_written.append(dxf_path)
                files_written += 1
        except Exception as e:
            QgsMessageLog.logMessage(
                f"Split DXF - group {value}: {e}", PLUGIN_NAME,
                Qgis.Warning)
            group_ok, group_skip, group_err = 0, 0, 1
            group_msgs.append(f"DXF: {e}")

        total_success += group_ok
        total_skip += group_skip
        total_errors += group_err
        per_group.append({
            "value": value, "paths": paths_written,
            "ok": group_ok, "skip": group_skip, "err": group_err,
            "error_message": "; ".join(group_msgs) if group_msgs else None,
        })

    return SplitResult(
        total_groups=total,
        files_written=files_written,
        total_success=total_success,
        total_skip=total_skip,
        total_errors=total_errors,
        per_group=per_group,
        output_dir=output_dir,
    )
