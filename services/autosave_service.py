# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Timed backups of the project file and editable layers - staged to a local temp folder on the main thread, since the PyQGIS file APIs aren't thread-safe, then moved to the real destination on a background QgsTask so a slow network share never blocks the UI."""

# on/off and the backup folder are per-project QgsProject entries so they ride along in the .qgz, which makes an explicit folder untrusted input - nothing gets written there until this machine's user confirms it in the dialog

import hashlib
import os
import shutil
import tempfile
from datetime import datetime, timedelta

from qgis.PyQt.QtCore import QTimer  # type: ignore
from qgis.core import (  # type: ignore
    Qgis, QgsApplication, QgsCoordinateTransformContext, QgsMessageLog,
    QgsProject, QgsSettings, QgsTask, QgsVectorFileWriter, QgsVectorLayer,
)

from ..i18n import tr as _tr
from . import backup_index, settings_service


class AutosaveService:
    """Timer-driven backup engine, no UI of its own. Built in initGui(), cleanup() comes from unload()."""

    # a QgsProject entry scope becomes an XML element name, so no '/' in it - the hierarchy goes in the key
    _PROJECT_SCOPE = "Vernier"
    _KEY_ENABLED = "autosave/enabled"
    _KEY_BACKUP_DIR = "autosave/backup_dir"
    # folders confirmed on this machine. one key per folder, so these go straight to QgsSettings instead of settings_service's static DEFAULTS
    _TRUST_GROUP = "Vernier/autosave/trusted_dirs"

    def __init__(self, iface):
        self.iface = iface
        self._timer = QTimer()
        self._timer.timeout.connect(self._do_backup)
        self._saving = False
        self._save_count = 0
        self._last_save_time = None
        # timestamps minted this session. the newest stops two backups in the same second overwriting each other, and pruning in the shared "_unsaved" folder is limited to these
        self._session_timestamps = set()
        # in-flight move, None when idle. guards against a slow network copy still running when the next tick lands
        self._active_task = None

    # --- per-project state ---

    def is_enabled(self):
        """Autosave preference for the current project, stored in the .qgz. Projects with no entry inherit the global default, so once it's configured on this machine new projects start with it on."""
        default = settings_service.get("autosave/configured")
        value, _ok = QgsProject.instance().readBoolEntry(
            self._PROJECT_SCOPE, self._KEY_ENABLED, default)
        return value

    def set_enabled(self, enabled):
        """Store the preference in the project and start or stop the timer. This dirties the project, so it only survives a restart once the user saves."""
        QgsProject.instance().writeEntry(
            self._PROJECT_SCOPE, self._KEY_ENABLED, bool(enabled))
        if not enabled:
            self.stop()
        elif self.backup_dir_is_trusted():
            self.start()
        else:
            self.stop()
            self._warn_untrusted_dir()

    def get_backup_dir_pref(self):
        """Backup folder configured for this project, "" means the default."""
        value, _ok = QgsProject.instance().readEntry(
            self._PROJECT_SCOPE, self._KEY_BACKUP_DIR, "")
        return value

    def set_backup_dir_pref(self, path):
        """Store the folder in the project and mark it confirmed here - getting this far means the user saw it in the dialog and saved, which is exactly what backup_dir_is_trusted() looks for."""
        clean = str(path or "").strip()
        QgsProject.instance().writeEntry(
            self._PROJECT_SCOPE, self._KEY_BACKUP_DIR, clean)
        if clean:
            QgsSettings().setValue(self._trust_key(clean), clean)

    def _trust_key(self, path):
        # a path can't be a settings key, its separators would nest groups, so a folder is recorded under a digest of itself
        normalized = os.path.normcase(os.path.normpath(path))
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
        return f"{self._TRUST_GROUP}/{digest}"

    def backup_dir_is_trusted(self):
        """Whether the resolved folder can be written to unattended. The folder rides inside the project file, so someone else's project can aim backups anywhere - the implicit "_backup" next to the project can't be redirected and is always fine, an explicit one needs this machine's user to have picked it."""
        configured = self.get_backup_dir_pref().strip()
        if not configured:
            return True
        return bool(QgsSettings().value(self._trust_key(configured), ""))

    def resolve_backup_dir(self):
        """Root backup folder - whatever is configured, or "_backup" next to the project. "" when the project is unsaved and nothing is set."""
        configured = self.get_backup_dir_pref().strip()
        if configured:
            return configured
        project_file = QgsProject.instance().fileName()
        if project_file:
            return os.path.join(os.path.dirname(project_file), "_backup")
        return ""

    def project_backup_dir(self, root_dir):
        """Per-project subfolder, for every root. Retention prunes a folder as a whole, so two projects sharing one expire each other's backups - which includes the implicit "_backup", because a job folder usually holds more than one project."""
        project_file = QgsProject.instance().fileName()
        if not project_file:
            # every unsaved project shares this one - there's no stable name to tell them apart, and a per-session folder would hide the last session's backups from the restore dialog right after a crash, which is when you want them. retention makes up for it by pruning only this session's events here
            return os.path.join(root_dir, "_unsaved")
        implicit_root = os.path.normpath(
            os.path.join(os.path.dirname(project_file), "_backup"))
        # a root beside the project only ever holds that folder's projects, so key on the filename - archiving the job folder to another drive then still finds its backups
        scope = ("name" if os.path.normpath(root_dir) == implicit_root
                 else "path")
        return os.path.join(
            root_dir,
            backup_index.project_folder_name(project_file, scope))

    # --- lifecycle ---

    def on_project_opened(self):
        """Called on readProject/cleared - reset the counters, apply the project's state."""
        self._save_count = 0
        self._last_save_time = None
        # the machine-level configured flag gates everything. a shared project carrying enabled=True must not start writing to disk on a machine whose user never set autosave up
        if not (settings_service.get("autosave/configured")
                and self.is_enabled() and self.resolve_backup_dir()):
            self.stop()
            return
        # same for a project naming a folder this machine never agreed to write into
        if not self.backup_dir_is_trusted():
            self.stop()
            self._warn_untrusted_dir()
            return
        self.start()

    def start(self):
        interval = settings_service.get("autosave/interval_minutes")
        self._timer.start(interval * 60 * 1000)

    def stop(self):
        self._timer.stop()

    def restart(self):
        self.stop()
        self.start()

    def is_active(self):
        return self._timer.isActive()

    def backup_now(self):
        """Manual backup right now. True if staging worked."""
        return self._do_backup()

    def cleanup(self):
        """Stop the timer, cancel any in-flight move. unload() calls this."""
        self.stop()
        if self._active_task is not None:
            try:
                self._active_task.cancel()
            except RuntimeError:
                pass  # task already finished and got deleted
            self._active_task = None

    @property
    def save_count(self):
        return self._save_count

    @property
    def last_save_time(self):
        return self._last_save_time

    # --- backup ---

    def _do_backup(self):
        if self._saving:
            return False
        self._saving = True
        try:
            return self._perform_backup()
        finally:
            self._saving = False

    def _perform_backup(self):
        # a previous move may still be going on a slow network drive, skipping a round beats stacking parallel copies
        if self._active_task is not None:
            msg = _tr("Previous backup still copying - "
                      "skipping this interval.")
            self._log(msg)
            self._status(msg)
            return False

        root_dir = self.resolve_backup_dir()
        if not root_dir:
            msg = _tr("Autosave stopped: save the project to disk or "
                      "choose a backup folder.")
            self._log(msg, Qgis.MessageLevel.Warning)
            self._status(msg)
            self.stop()
            return False

        # a project file can name any folder, so nothing goes to an explicit one before the user confirms it here
        if not self.backup_dir_is_trusted():
            self.stop()
            self._warn_untrusted_dir()
            return False

        backup_dir = self.project_backup_dir(root_dir)
        try:
            os.makedirs(backup_dir, exist_ok=True)
        except OSError as e:
            msg = _tr("Could not create the backup folder '{0}': "
                      "{1}").format(backup_dir, e)
            self._log(msg, Qgis.MessageLevel.Critical)
            self._push_warning(_tr("Autosave stopped"), msg, critical=True)
            self.stop()
            return False

        # stage locally, move in the background. the writes stay on the main thread but a local temp write is ~50-200ms where a network destination can take seconds
        try:
            temp_dir = tempfile.mkdtemp(prefix="vernier_autosave_")
        except OSError as e:
            msg = _tr("Could not create a temporary folder for the "
                      "backup: {0}").format(e)
            self._log(msg, Qgis.MessageLevel.Critical)
            self._push_warning(_tr("Backup failed"), msg, critical=True)
            return False

        project = QgsProject.instance()
        project_file = project.fileName()
        timestamp = self._next_timestamp()
        layer_warnings = []
        project_error = None
        staged = []  # (temp_path, final_path) pairs to move in background

        # 1. the project file. QgsProject.write(path) repoints fileName() and clears the dirty flag, so both get restored in the finally block or the live project is left corrupted. absolute paths, because relative ones would resolve against the staging folder and break every file-based layer once the backup moves
        if settings_service.get("autosave/save_project") and project_file:
            base = os.path.splitext(os.path.basename(project_file))[0]
            ext = os.path.splitext(project_file)[1] or ".qgz"
            filename = backup_index.backup_filename(base, timestamp, ext)
            temp_path = os.path.join(temp_dir, filename)
            final_path = os.path.join(backup_dir, filename)
            was_dirty = project.isDirty()
            was_absolute = project.readBoolEntry("Paths", "/Absolute", False)[0]
            try:
                project.writeEntry("Paths", "/Absolute", True)
                if project.write(temp_path) and os.path.getsize(temp_path) > 0:
                    staged.append((temp_path, final_path))
                    staged.extend(self._staged_sidecars(
                        temp_dir, filename, base, timestamp, backup_dir))
                else:
                    project_error = _tr("the file could not be written, "
                                        "or was written empty")
                    self._log("QgsProject.write() failed or produced an "
                              f"empty file: {temp_path}", Qgis.MessageLevel.Critical)
                    if (os.path.exists(temp_path)
                            and os.path.getsize(temp_path) == 0):
                        try:
                            os.remove(temp_path)
                        except OSError:
                            pass
            except OSError as e:
                project_error = str(e)
            finally:
                project.writeEntry("Paths", "/Absolute", was_absolute)
                project.setFileName(project_file)
                # setFileName() re-dirties the project, so put a clean one back to clean
                project.setDirty(was_dirty)

        # 2. stage editable and memory layers as GeoPackages
        if settings_service.get("autosave/save_layers"):
            for layer in self._get_backup_layers():
                safe_name = "".join(
                    c if c.isalnum() or c in "-_" else "_"
                    for c in layer.name())
                # tie-breaker for names that collide after sanitizing, "Roads/A" and "Roads A" both land on Roads_A. the tail of layer.id() is unique
                unique = "".join(
                    c if c.isalnum() else "_" for c in layer.id()[-6:])
                filename = backup_index.backup_filename(
                    f"{safe_name}_{unique}", timestamp, ".gpkg")
                temp_path = os.path.join(temp_dir, filename)
                final_path = os.path.join(backup_dir, filename)
                if self._write_layer(layer, temp_path, layer_warnings):
                    staged.append((temp_path, final_path))

        if not staged:
            shutil.rmtree(temp_dir, ignore_errors=True)
            self._handle_backup_completion(
                project_error=project_error,
                layer_warnings=layer_warnings,
                move_errors=[], saved_any=False)
            return False

        # 3. move to the real folder in the background, the closure carries the context back to the main thread when the task ends
        max_backups = settings_service.get("autosave/max_backups")
        # unsaved projects share "_unsaved", so retention there may only expire events this session wrote
        own_timestamps = (None if project_file
                          else set(self._session_timestamps))

        def on_done(move_errors, cancelled=False):
            # clear before the feedback so the next tick can run
            self._active_task = None
            if cancelled:
                # cancel comes from cleanup() at unload and the staged files are gone, so this cycle must not count as a backup, prune retention or report "Backup OK"
                self._log(_tr("Backup move canceled - this cycle was "
                              "not completed."), Qgis.MessageLevel.Warning)
                return
            try:
                self._cleanup_old_backups(backup_dir, max_backups,
                                          own_timestamps)
            except OSError:
                pass
            self._handle_backup_completion(
                project_error=project_error,
                layer_warnings=layer_warnings,
                move_errors=move_errors, saved_any=True)

        task = _BackupMoveTask(staged, temp_dir, on_done)
        self._active_task = task
        QgsApplication.taskManager().addTask(task)
        return True

    def _handle_backup_completion(self, project_error, layer_warnings,
                                  move_errors, saved_any):
        """One place for the feedback, called inline when nothing was staged and from the move task's callback otherwise."""
        if saved_any and not project_error and not move_errors:
            self._save_count += 1
            self._last_save_time = datetime.now()

        if project_error:
            self._log(_tr("Project: {0}").format(project_error),
                      Qgis.MessageLevel.Critical)
            self._push_warning(
                _tr("Project backup failed"),
                _tr("The project file was not backed up: {0}. Check free "
                    "space and write permissions on the backup folder.")
                .format(project_error),
                critical=True)
        elif move_errors:
            for err in move_errors:
                self._log(err, Qgis.MessageLevel.Critical)
            if len(move_errors) == 1:
                detail = _tr("1 file could not be moved to the backup "
                             "folder. Check the connection and "
                             "permissions.")
            else:
                detail = _tr("{0} files could not be moved to the backup "
                             "folder. Check the connection and "
                             "permissions.").format(len(move_errors))
            self._push_warning(_tr("Backup copy failed"), detail,
                               critical=True)
        elif layer_warnings:
            for warning in layer_warnings:
                self._log(warning, Qgis.MessageLevel.Warning)
            if len(layer_warnings) == 1:
                detail = _tr("1 layer could not be saved. See Log "
                             "Messages > Vernier for details.")
            else:
                detail = _tr("{0} layers could not be saved. See Log "
                             "Messages > Vernier for details.").format(
                                 len(layer_warnings))
            self._push_warning(_tr("Partial backup"), detail, critical=False)
        elif saved_any:
            self._status(_tr("Backup OK at {0} (#{1})").format(
                self._last_save_time.strftime("%H:%M:%S"),
                self._save_count), 4000)
        else:
            # a cycle that stages nothing would go by silently, and "Back up now" would then report a failure over an empty log
            if QgsProject.instance().fileName():
                msg = _tr("Nothing to back up: no layer is being edited "
                          "and the project file is not selected for "
                          "backup.")
            else:
                msg = _tr("Nothing to back up: save the project to disk, "
                          "or start editing a layer.")
            self._log(msg, Qgis.MessageLevel.Warning)
            self._status(msg)

    def _next_timestamp(self):
        """A timestamp not used yet this session. TS_FORMAT is whole seconds, so a manual backup landing in the same second as a tick would overwrite that event, and a clock stepped backwards would make an older event sort newer and get the wrong one expired - continuing one second past the newest stamp avoids both."""
        stamp = datetime.now().strftime(backup_index.TS_FORMAT)
        newest = max(self._session_timestamps, default=None)
        if newest is not None and stamp <= newest:
            bumped = (datetime.strptime(newest, backup_index.TS_FORMAT)
                      + timedelta(seconds=1))
            stamp = bumped.strftime(backup_index.TS_FORMAT)
        self._session_timestamps.add(stamp)
        return stamp

    def _staged_sidecars(self, temp_dir, project_filename, base, timestamp,
                         backup_dir):
        """The files QgsProject.write() drops beside a written .qgs - its auxiliary storage and attachments zip. Each gets renamed into the backup scheme so retention groups it with the event and original_name() hands back the name the restored project expects. A .qgz packs the lot, so nothing turns up there."""
        written_stem = os.path.splitext(project_filename)[0]
        pairs = []
        try:
            names = sorted(os.listdir(temp_dir))
        except OSError:
            return pairs
        for name in names:
            if name == project_filename or not name.startswith(written_stem):
                continue
            suffix = name[len(written_stem):]
            dot = suffix.rfind(".")
            if dot > 0:
                tag, ext = suffix[:dot], suffix[dot:]
            elif dot == 0:
                tag, ext = "", suffix
            else:
                tag, ext = suffix, ""
            final_name = backup_index.backup_filename(
                base + tag, timestamp, ext)
            pairs.append((os.path.join(temp_dir, name),
                          os.path.join(backup_dir, final_name)))
        return pairs

    def _get_backup_layers(self):
        """Layers worth backing up - the editable ones and the memory ones."""
        layers = []
        for layer in QgsProject.instance().mapLayers().values():
            if not isinstance(layer, QgsVectorLayer):
                continue
            if layer.isEditable():
                layers.append(layer)
            elif (layer.dataProvider()
                    and layer.dataProvider().name() == "memory"):
                layers.append(layer)
        return layers

    def _write_layer(self, layer, dest_path, warnings):
        """Write one layer as .gpkg into local temp, on the main thread."""
        try:
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"
            options.fileEncoding = "UTF-8"
            err, msg, _, _ = QgsVectorFileWriter.writeAsVectorFormatV3(
                layer, dest_path, QgsCoordinateTransformContext(), options)
            if err != QgsVectorFileWriter.WriterError.NoError:
                warnings.append(f"{layer.name()}: {msg}")
                return False
            # a full disk can hand back an empty file and no error
            if os.path.exists(dest_path) and os.path.getsize(dest_path) == 0:
                warnings.append(_tr("{0}: empty file written "
                                    "(disk full?)").format(layer.name()))
                try:
                    os.remove(dest_path)
                except OSError:
                    pass
                return False
            return True
        except Exception as e:
            warnings.append(f"{layer.name()}: {e}")
            return False

    def _cleanup_old_backups(self, backup_dir, max_backups,
                             own_timestamps=None):
        """Delete backup events past the newest max_backups. Grouping and the expiry call live in backup_index, shared with the restore dialog. own_timestamps narrows pruning to this session's events, for the "_unsaved" folder every unsaved project shares - another project's backups there aren't ours to expire."""
        try:
            names = os.listdir(backup_dir)
        except OSError:
            return
        if own_timestamps is not None:
            names = [n for n in names
                     if backup_index.timestamp_of(n) in own_timestamps]
        _, expired = backup_index.split_by_retention(names, max_backups)
        for name in expired:
            try:
                os.remove(os.path.join(backup_dir, name))
            except OSError:
                pass

    # --- feedback ---

    def _status(self, msg, duration=6000):
        self.iface.statusBarIface().showMessage(msg, duration)

    def _log(self, msg, level=Qgis.MessageLevel.Info):
        QgsMessageLog.logMessage(msg, "Vernier", level=level)

    def _warn_untrusted_dir(self):
        """Explain why the folder the project file named didn't get used."""
        msg = _tr("Autosave is off for this project: it asks for backups "
                  "in '{0}'. Open Autosave Settings and save that folder "
                  "to allow writing there.").format(
                      self.get_backup_dir_pref().strip())
        self._log(msg, Qgis.MessageLevel.Warning)
        self._push_warning(_tr("Autosave not started"), msg)

    def _push_warning(self, title, msg, critical=False):
        """Sticky messageBar warning, stays until dismissed. _status() doesn't."""
        try:
            bar = self.iface.messageBar()
            if critical:
                bar.pushCritical(title, msg)
            else:
                bar.pushWarning(title, msg)
        except (AttributeError, RuntimeError):
            self._status(f"{title}: {msg}", 8000)


class _BackupMoveTask(QgsTask):
    """Moves staged backup files to the real folder on a worker thread. Split this way because the PyQGIS writers aren't thread-safe but shutil.move is, and the move is the slow part over a network share."""

    def __init__(self, staged_files, temp_dir, on_done):
        super().__init__(_tr("Vernier autosave - copying backup"),
                         QgsTask.Flag.CanCancel)
        self._staged = staged_files  # (src, dst) pairs
        self._temp_dir = temp_dir
        self._on_done = on_done
        self._move_errors = []

    def run(self):
        # worker thread, stdlib only, no PyQGIS in here
        total = len(self._staged) or 1
        for i, (src, dst) in enumerate(self._staged):
            if self.isCanceled():
                return False
            try:
                shutil.move(src, dst)
            except (OSError, shutil.Error) as e:
                self._move_errors.append(f"{os.path.basename(src)}: {e}")
            self.setProgress(int((i + 1) / total * 100))
        return True

    def finished(self, result):
        # back on the main thread, PyQGIS and UI callbacks are fine now
        shutil.rmtree(self._temp_dir, ignore_errors=True)
        try:
            # result is False when run() bailed on cancellation, don't let the callback read that as a clean move
            self._on_done(self._move_errors, cancelled=not result)
        except Exception as e:
            # a raising callback shouldn't take the task manager with it
            QgsMessageLog.logMessage(
                _tr("Autosave callback error: {0}").format(e),
                "Vernier", level=Qgis.MessageLevel.Critical)
