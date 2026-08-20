# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The backup filename, grouping and retention contract. backup_index is pure stdlib but this runs under the QGIS python like the rest."""

import os
import sys
import unittest
from datetime import datetime

# make the plugins folder importable so the package-relative imports resolve
_PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _PLUGINS_DIR not in sys.path:
    sys.path.insert(0, _PLUGINS_DIR)

from vernier.services.backup_index import (  # noqa: E402
    TS_FORMAT, backup_filename, group_by_timestamp, original_name,
    project_folder_name, split_by_retention, timestamp_of,
)

T1 = "20251231_235900"  # oldest, before a year boundary
T2 = "20260101_000100"
T3 = "20260714_101500"
T4 = "20260714_113000"  # newest


def _event(ts, layers=2):
    """Filenames of one backup event: a project plus N layer snapshots."""
    files = [backup_filename("survey", ts, ".qgz")]
    files += [backup_filename(f"layer{i}_ab12cd", ts, ".gpkg")
              for i in range(layers)]
    return files


class TestFilenameContract(unittest.TestCase):

    def test_backup_filename_roundtrip(self):
        name = backup_filename("survey", T3, ".qgz")
        self.assertEqual(name, "survey_20260714_101500_backup.qgz")
        self.assertEqual(timestamp_of(name), T3)

    def test_ts_format_produces_parseable_timestamps(self):
        ts = datetime(2026, 7, 14, 9, 5, 3).strftime(TS_FORMAT)
        self.assertEqual(ts, "20260714_090503")
        self.assertEqual(timestamp_of(backup_filename("p", ts, ".qgz")), ts)

    def test_foreign_files_have_no_timestamp(self):
        for name in ("readme.txt", "survey.qgz", "survey_backup.qgz",
                     "notes_20260714_backup.qgz",  # date only, no time
                     "survey_20260714_101500_backup"):  # no extension
            self.assertIsNone(timestamp_of(name), name)

    def test_original_name_strips_suffix(self):
        self.assertEqual(
            original_name("survey_20260714_101500_backup.qgz"), "survey.qgz")
        self.assertEqual(
            original_name("layer0_ab12cd_20260714_101500_backup.gpkg"),
            "layer0_ab12cd.gpkg")

    def test_original_name_passthrough_for_foreign_files(self):
        self.assertEqual(original_name("readme.txt"), "readme.txt")


class TestGrouping(unittest.TestCase):

    def test_one_event_groups_project_and_layers(self):
        files = _event(T3, layers=3)
        groups = group_by_timestamp(files)
        self.assertEqual(list(groups.keys()), [T3])
        self.assertEqual(sorted(groups[T3]), sorted(files))

    def test_events_stay_separate(self):
        groups = group_by_timestamp(_event(T3) + _event(T4))
        self.assertEqual(sorted(groups.keys()), [T3, T4])
        self.assertEqual(len(groups[T3]), 3)
        self.assertEqual(len(groups[T4]), 3)

    def test_foreign_files_excluded(self):
        groups = group_by_timestamp(["readme.txt"] + _event(T3))
        self.assertEqual(list(groups.keys()), [T3])
        self.assertNotIn("readme.txt", groups[T3])


class TestRetention(unittest.TestCase):

    def test_keeps_newest_events_deletes_oldest(self):
        files = _event(T1) + _event(T2) + _event(T3) + _event(T4)
        kept, expired = split_by_retention(files, keep=2)
        self.assertEqual(sorted(kept), sorted(_event(T3) + _event(T4)))
        self.assertEqual(sorted(expired), sorted(_event(T1) + _event(T2)))

    def test_year_boundary_sorts_chronologically(self):
        kept, expired = split_by_retention(_event(T1) + _event(T2), keep=1)
        self.assertEqual(sorted(kept), sorted(_event(T2)))
        self.assertEqual(sorted(expired), sorted(_event(T1)))

    def test_whole_event_expires_together(self):
        files = _event(T1, layers=5) + _event(T2)
        _, expired = split_by_retention(files, keep=1)
        self.assertEqual(sorted(expired), sorted(_event(T1, layers=5)))

    def test_keep_equal_to_events_expires_nothing(self):
        files = _event(T3) + _event(T4)
        kept, expired = split_by_retention(files, keep=2)
        self.assertEqual(sorted(kept), sorted(files))
        self.assertEqual(expired, [])

    def test_keep_larger_than_events_expires_nothing(self):
        files = _event(T3) + _event(T4)
        kept, expired = split_by_retention(files, keep=50)
        self.assertEqual(sorted(kept), sorted(files))
        self.assertEqual(expired, [])

    def test_foreign_files_never_expire(self):
        foreign = ["readme.txt", "survey.qgz", "notes_backup.qgz"]
        files = foreign + _event(T1) + _event(T4)
        kept, expired = split_by_retention(files, keep=1)
        for name in foreign:
            self.assertIn(name, kept)
            self.assertNotIn(name, expired)
        self.assertEqual(sorted(expired), sorted(_event(T1)))

    def test_keep_zero_expires_every_event(self):
        foreign = ["readme.txt"]
        files = foreign + _event(T3) + _event(T4)
        kept, expired = split_by_retention(files, keep=0)
        self.assertEqual(kept, foreign)
        self.assertEqual(sorted(expired), sorted(_event(T3) + _event(T4)))

    def test_empty_folder(self):
        self.assertEqual(split_by_retention([], keep=10), ([], []))


class TestProjectFolderName(unittest.TestCase):
    """Per-project subfolder inside a shared backup root."""

    def test_readable_stem_with_hash_suffix(self):
        name = project_folder_name("C:/Sites/Alpha/Field Survey.qgz")
        self.assertRegex(name, r"^Field_Survey_[0-9a-f]{8}$")

    def test_same_basename_different_paths_do_not_collide(self):
        a = project_folder_name("C:/Sites/Alpha/Field.qgz")
        b = project_folder_name("D:/Data/Beta/Field.qgz")
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("Field_"))
        self.assertTrue(b.startswith("Field_"))

    def test_stable_across_calls_and_path_spelling(self):
        # the restore dialog has to find what the service wrote, whatever separator or trailing-slash form the path arrived in
        a = project_folder_name("C:/Sites/Alpha/Field.qgz")
        b = project_folder_name("C:/Sites/Alpha/./Field.qgz")
        self.assertEqual(a, b)
        if os.name == "nt":
            self.assertEqual(
                a, project_folder_name("c:\\sites\\alpha\\Field.qgz"))

    def test_illegal_characters_sanitized(self):
        name = project_folder_name("/tmp/a:b*c?.qgs")
        stem = name.rsplit("_", 1)[0]
        self.assertTrue(all(c.isalnum() or c in "-_" for c in stem))


if __name__ == "__main__":
    unittest.main()
