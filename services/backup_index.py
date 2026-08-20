# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Naming, grouping and retention for autosave backups - one event writes several files sharing a timestamp, as {stem}_{YYYYMMDD_HHMMSS}_backup.{ext}."""

# sole owner of that contract: the service builds names and prunes through here, the restore dialog groups and renames through here, so one regex change can't quietly break grouping. pure stdlib, testable anywhere

import hashlib
import os
import re
from typing import Dict, Iterable, List, Optional, Tuple

# fixed-width on purpose, so lexicographic order is chronological order
TS_FORMAT = "%Y%m%d_%H%M%S"

_TS_RE = re.compile(r"_(\d{8}_\d{6})_backup(\.\w+)$")


def backup_filename(stem: str, timestamp: str, ext: str) -> str:
    """Name for one backed-up file, ext carries the leading dot."""
    return f"{stem}_{timestamp}_backup{ext}"


def project_folder_name(project_file: str, scope: str = "path") -> str:
    """Per-project subfolder inside a backup root, as "{stem}_{hash8}". The stem says which project it is, the digest separates the ones it would merge - sanitizing turns both "Sector 1.qgz" and "Sector.1.qgz" into "Sector_1". scope="path" hashes the whole path, which is what a root shared by projects from anywhere needs to keep C:/SiteA/Field.qgz and D:/SiteB/Field.qgz apart. scope="name" hashes the filename alone, for a root that already sits beside the project - filenames are unique within one folder, and keying on the name means moving the job folder still finds its backups."""

    # the digest only disambiguates, it guards nothing
    base = os.path.splitext(os.path.basename(project_file))[0]
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in base)
    if scope == "name":
        key = os.path.normcase(os.path.basename(project_file))
    else:
        key = os.path.normcase(os.path.normpath(project_file))
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    return f"{safe}_{digest}"


def timestamp_of(filename: str) -> Optional[str]:
    """The embedded timestamp, None for files we didn't write."""
    match = _TS_RE.search(filename)
    return match.group(1) if match else None


def original_name(filename: str) -> str:
    """Strip the backup suffix - survey_20260714_101500_backup.qgz becomes survey.qgz, anything else passes through."""
    match = _TS_RE.search(filename)
    if not match:
        return filename
    return filename[:match.start()] + match.group(2)


def group_by_timestamp(filenames: Iterable[str]) -> Dict[str, List[str]]:
    """Backup files grouped per event, names we don't recognize are skipped."""
    groups: Dict[str, List[str]] = {}
    for name in filenames:
        ts = timestamp_of(name)
        if ts is not None:
            groups.setdefault(ts, []).append(name)
    return groups


def split_by_retention(filenames: Iterable[str],
                       keep: int) -> Tuple[List[str], List[str]]:
    """Split into (kept, expired), newest `keep` events survive. Grouped on the filename timestamp rather than mtime so a slow network copy can't scatter one event, and anything unrecognized is always kept."""
    filenames = list(filenames)
    groups = group_by_timestamp(filenames)
    ordered = sorted(groups)
    keep_ts = set(ordered[-keep:]) if keep > 0 else set()
    kept, expired = [], []
    for name in filenames:
        ts = timestamp_of(name)
        if ts is None or ts in keep_ts:
            kept.append(name)
        else:
            expired.append(name)
    return kept, expired
