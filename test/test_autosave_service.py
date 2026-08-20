# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The _BackupMoveTask contract - a completed move lands and reports clean, a cancelled one stays distinguishable from a clean backup, or a cancel at unload reads as "Backup OK" over discarded files. run() and finished() are driven directly, no task manager and no threads. Plus the backup folder each project resolves to, which decides whose backups retention is allowed to expire."""

import os
import shutil
import sys
import tempfile
import unittest

from qgis.core import QgsApplication, QgsProject  # type: ignore

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from vernier.services.autosave_service import (  # noqa: E402
    AutosaveService, _BackupMoveTask,
)

QGS = None


def setUpModule():
    global QGS
    QGS = QgsApplication([], False)
    QGS.initQgis()


def tearDownModule():
    QGS.exitQgis()


def _same_path(path):
    return os.path.normcase(os.path.normpath(path))


class RecordingCallback:
    """Records (move_errors, cancelled) pairs handed to on_done."""

    def __init__(self):
        self.calls = []

    def __call__(self, move_errors, cancelled=False):
        self.calls.append((list(move_errors), cancelled))


class TestBackupMoveTask(unittest.TestCase):

    def setUp(self):
        self.stage_dir = tempfile.mkdtemp(prefix="va_autosave_stage_")
        self.dest_dir = tempfile.mkdtemp(prefix="va_autosave_dest_")

    def tearDown(self):
        shutil.rmtree(self.stage_dir, ignore_errors=True)
        shutil.rmtree(self.dest_dir, ignore_errors=True)

    def _stage(self, name="layer.gpkg"):
        src = os.path.join(self.stage_dir, name)
        with open(src, "w", encoding="utf-8") as f:
            f.write("data")
        return src, os.path.join(self.dest_dir, name)

    def test_completed_run_moves_files_and_reports_success(self):
        pair = self._stage()
        done = RecordingCallback()
        task = _BackupMoveTask([pair], self.stage_dir, done)
        result = task.run()  # direct call - no task manager needed
        task.finished(result)
        self.assertTrue(result)
        self.assertTrue(os.path.exists(pair[1]))
        self.assertFalse(os.path.exists(self.stage_dir))  # staging cleaned
        self.assertEqual(done.calls, [([], False)])

    def test_cancelled_task_does_not_read_as_clean_backup(self):
        pair = self._stage()
        done = RecordingCallback()
        task = _BackupMoveTask([pair], self.stage_dir, done)
        task.cancel()
        result = task.run()  # sees isCanceled() and bails out
        task.finished(result)
        self.assertFalse(result)
        self.assertFalse(os.path.exists(pair[1]))  # nothing was moved
        # no move_errors either, only the cancelled flag tells the callback this cycle isn't a successful backup
        self.assertEqual(done.calls, [([], True)])


class TestProjectBackupDir(unittest.TestCase):
    """_cleanup_old_backups expires a folder's contents by timestamp alone, so anything sharing a folder shares that pruning."""

    def setUp(self):
        self.job_dir = tempfile.mkdtemp(prefix="va_autosave_job_")
        self.service = AutosaveService(None)  # no iface needed for path logic

    def tearDown(self):
        QgsProject.instance().setFileName("")
        shutil.rmtree(self.job_dir, ignore_errors=True)

    def _resolve(self, project_name):
        QgsProject.instance().setFileName(
            os.path.join(self.job_dir, project_name))
        root = self.service.resolve_backup_dir()
        return root, self.service.project_backup_dir(root)

    def test_two_projects_in_one_folder_get_separate_backup_dirs(self):
        root_a, dir_a = self._resolve("Sector1.qgz")
        root_b, dir_b = self._resolve("Sector2.qgz")
        # a shared job folder puts both projects under the same implicit root
        self.assertEqual(root_a, root_b)
        # but retention prunes a folder wholesale, so they must not land in it
        self.assertNotEqual(dir_a, dir_b)
        # QgsProject hands back forward slashes, so compare normalized
        for root, sub in ((root_a, dir_a), (root_b, dir_b)):
            self.assertEqual(_same_path(os.path.dirname(sub)),
                             _same_path(root))

    def test_explicit_root_also_separates_projects(self):
        QgsProject.instance().setFileName(
            os.path.join(self.job_dir, "Sector1.qgz"))
        a = self.service.project_backup_dir(self.job_dir)
        QgsProject.instance().setFileName(
            os.path.join(self.job_dir, "Sector2.qgz"))
        b = self.service.project_backup_dir(self.job_dir)
        self.assertNotEqual(a, b)

    def test_moving_the_job_folder_keeps_the_same_subfolder(self):
        # the backups travel with the folder, so the name they are filed under must not depend on where that folder sits
        QgsProject.instance().setFileName("C:/jobs/site/Sector1.qgz")
        before = os.path.basename(
            self.service.project_backup_dir("C:/jobs/site/_backup"))
        QgsProject.instance().setFileName("Z:/archive/2026/site/Sector1.qgz")
        after = os.path.basename(
            self.service.project_backup_dir("Z:/archive/2026/site/_backup"))
        self.assertEqual(before, after)

    def test_shared_explicit_root_still_separates_same_named_projects(self):
        QgsProject.instance().setFileName("C:/SiteA/Field.qgz")
        a = self.service.project_backup_dir("D:/backups")
        QgsProject.instance().setFileName("D:/SiteB/Field.qgz")
        b = self.service.project_backup_dir("D:/backups")
        self.assertNotEqual(a, b)

    def test_unsaved_project_falls_back_to_the_shared_folder(self):
        QgsProject.instance().setFileName("")
        self.assertEqual(
            self.service.project_backup_dir(self.job_dir),
            os.path.join(self.job_dir, "_unsaved"))


if __name__ == "__main__":
    unittest.main()
