import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import zipfile

SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("android_tools_updater_test", SOURCE / "main.py")
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "extensions"
        self.root.mkdir()
        self.target = self.root / updater.TARGET_ID
        self.target.mkdir()
        self.directory = self.root / updater.UPDATER_ID
        self.directory.mkdir()
        self.config_dir = Path(self.temp.name) / "config"
        self.config_dir.mkdir()
        self.config = self.config_dir / "extensions.json"
        self.config.write_text(json.dumps({"approved": [updater.TARGET_ID, updater.UPDATER_ID, "unrelated"],
                                           "inactive": ["unrelated"], "placement": {"unrelated": "none"},
                                           "unknown_future_setting": "preserve"}))
        self.old_meta = {"id": updater.TARGET_ID, "version": "0.1.0", "entry": "main.py", "naia_ext_api": 1}
        (self.target / "extension.json").write_text(json.dumps(self.old_meta))
        (self.target / "main.py").write_text("OLD-CODE")
        self.settings = {"plugin_enabled": True, "allow_edit": True, "allow_generate": True,
                         "low_memory": False, "cache_mb": 64, "custom_value": {"keep": "yes"}}
        (self.target / "settings.json").write_text(json.dumps(self.settings))
        private = self.config_dir / "android-tools"
        private.mkdir()
        self.pairing = private / "pairing.json"
        self.pairing.write_bytes(b'{"device_token":"UNCHANGED-PRIVATE"}')
        self.journal = private / "device-bridge-journal.json"
        self.journal.write_bytes(b'{"existing":{"acked":true}}')
        self.record = NS(ext_id=updater.TARGET_ID, directory=self.target, status="loaded", approved=True,
                         enabled=True, blocked=False, armed=True)
        self.own_record = NS(ext_id=updater.UPDATER_ID, status="loaded", approved=True,
                             enabled=True, blocked=False, armed=True)
        self.manager = NS(records=[self.record, self.own_record], _lock=threading.Lock(),
                          extensions_root=lambda: self.root)
        self.app = NS(extension_manager=self.manager, runtime_paths=NS(config_dir=self.config_dir),
                      is_generating=False, headless_generation_runner_active=False)
        self.ctx = NS(ext_dir=self.directory, _record=self.own_record, _app_context=self.app)
        self.closed = []
        self.old_controller = NS(ctx=NS(ext_dir=self.target), close=lambda: self.closed.append(True))
        module = NS(_controller=self.old_controller)
        self.module_patch = patch.dict(sys.modules, {"naia_ext_" + updater.TARGET_ID: module})
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        self.contents = {name: ("SAFE CONTENT " + name).encode() for name in updater.FILES}
        self.contents["extension.json"] = json.dumps({**self.old_meta, "version": updater.TO_VERSION}).encode()
        self.contents["main.py"] = b'raise RuntimeError("NEW CODE MUST NOT EXECUTE")\n'
        self.archive = self.directory / updater.ARCHIVE_NAME
        self.create_payload()

    def create_payload(self):
        with zipfile.ZipFile(self.archive, "w", zipfile.ZIP_DEFLATED) as zipped:
            for name, content in self.contents.items():
                zipped.writestr(name, content)
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        meta = {"id": updater.TARGET_ID, "from_version": updater.FROM_VERSION,
                "to_version": updater.TO_VERSION, "archive": updater.ARCHIVE_NAME, "sha256": digest,
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in self.contents.items()}}
        (self.directory / "payload-manifest.json").write_text(json.dumps(meta))
        self.digest_patch = patch.object(updater, "PAYLOAD_SHA256", digest)
        self.digest_patch.start()
        self.addCleanup(self.digest_patch.stop)

    def assert_old_untouched(self):
        self.assertEqual((self.target / "main.py").read_text(), "OLD-CODE")
        self.assertEqual(json.loads((self.target / "settings.json").read_text()), self.settings)
        self.assertEqual(self.closed, [])

    def test_transaction_preserves_pairing_and_settings_and_requires_new_approval(self):
        result = updater.perform_update(self.ctx)
        self.assertTrue(result["updated"])
        self.assertTrue(result["reapproval_required"])
        self.assertEqual(self.closed, [True])
        self.assertEqual((self.target / "main.py").read_bytes(), self.contents["main.py"])
        self.assertEqual(json.loads((self.target / "settings.json").read_text()),
                         {**self.settings, "allow_screen": False, "allow_control": False})
        self.assertEqual(self.pairing.read_bytes(), b'{"device_token":"UNCHANGED-PRIVATE"}')
        self.assertEqual(self.journal.read_bytes(), b'{"existing":{"acked":true}}')
        config = json.loads(self.config.read_text())
        self.assertNotIn(updater.TARGET_ID, config["approved"])
        self.assertIn("unrelated", config["approved"])
        self.assertEqual(config["unknown_future_setting"], "preserve")
        self.assertFalse(self.record.approved)
        self.assertFalse(self.record.enabled)
        self.assertFalse(self.record.armed)
        self.assertEqual(self.record.status, "loaded")
        backup = self.config_dir / "android-tools/update-0.2.0/previous-extension"
        self.assertEqual((backup / "main.py").read_text(), "OLD-CODE")
        self.assertFalse(backup.is_relative_to(self.root))
        self.assertTrue(self.directory.exists())

    def test_repeat_does_not_reset_new_user_permission_choices(self):
        updater.perform_update(self.ctx)
        settings = json.loads((self.target / "settings.json").read_text())
        settings["allow_screen"] = True
        (self.target / "settings.json").write_text(json.dumps(settings))
        self.assertTrue(updater.perform_update(self.ctx)["already_updated"])
        self.assertEqual(len(self.closed), 1)
        self.assertTrue(json.loads((self.target / "settings.json").read_text())["allow_screen"])

    def test_untrusted_disabled_disarmed_or_blocked_helper_cannot_update(self):
        for field, value in (("approved", False), ("enabled", False), ("armed", False), ("blocked", True)):
            with self.subTest(field=field):
                old = getattr(self.own_record, field)
                setattr(self.own_record, field, value)
                with self.assertRaises(updater.UpdateError):
                    updater.perform_update(self.ctx)
                setattr(self.own_record, field, old)
                self.assert_old_untouched()

    def test_unknown_installed_identity_is_refused(self):
        for key, value in (("version", "0.0.9"), ("id", "different_extension"), ("entry", "alternate.py")):
            with self.subTest(key=key):
                (self.target / "extension.json").write_text(json.dumps({**self.old_meta, key: value}))
                with self.assertRaises(updater.UpdateError):
                    updater.perform_update(self.ctx)
                self.assert_old_untouched()

    def test_archive_hash_is_pinned_in_code_not_just_mutable_manifest(self):
        with zipfile.ZipFile(self.archive, "a") as zipped:
            zipped.writestr("extra.py", b"unexpected")
        meta_path = self.directory / "payload-manifest.json"
        meta = json.loads(meta_path.read_text())
        meta["sha256"] = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        meta_path.write_text(json.dumps(meta))
        with self.assertRaises(updater.UpdateError):
            updater.perform_update(self.ctx)
        self.assert_old_untouched()

    def test_zip_traversal_is_rejected_even_if_archive_digest_is_valid(self):
        self.contents["../escape.py"] = self.contents.pop("README.md")
        self.create_payload()
        with self.assertRaises(updater.UpdateError):
            updater.perform_update(self.ctx)
        self.assertFalse((self.root / "escape.py").exists())
        self.assert_old_untouched()

    def test_symlink_inside_old_extension_is_refused(self):
        (self.target / "linked-secret").symlink_to(self.pairing)
        with self.assertRaises(updater.UpdateError):
            updater.perform_update(self.ctx)
        self.assert_old_untouched()

    def test_symlink_parent_is_refused(self):
        other = Path(self.temp.name) / "backup-outside"
        other.mkdir()
        (self.config_dir / "android-tools/update-0.2.0").symlink_to(other, target_is_directory=True)
        with self.assertRaises(updater.UpdateError):
            updater.perform_update(self.ctx)
        self.assertEqual(list(other.iterdir()), [])
        self.assert_old_untouched()

    def test_failed_second_rename_restores_old_files_without_restoring_trust(self):
        real_replace = os.replace
        def replace(source, destination):
            if Path(source).name == "new" and Path(destination) == self.target:
                raise OSError("simulated filesystem failure")
            return real_replace(source, destination)
        with patch.object(updater.os, "replace", side_effect=replace), self.assertRaises(updater.UpdateError):
            updater.perform_update(self.ctx)
        self.assertEqual((self.target / "main.py").read_text(), "OLD-CODE")
        self.assertEqual(json.loads((self.target / "settings.json").read_text()), self.settings)
        self.assertNotIn(updater.TARGET_ID, json.loads(self.config.read_text())["approved"])
        self.assertFalse(self.record.approved)
        self.assertEqual(self.closed, [True])

    def test_config_write_failure_restores_live_flags_before_any_stop(self):
        real_atomic = updater._atomic_json
        def write(path, value):
            if path == self.config:
                raise OSError("simulated config write failure")
            return real_atomic(path, value)
        with patch.object(updater, "_atomic_json", side_effect=write), self.assertRaises(OSError):
            updater.perform_update(self.ctx)
        self.assert_old_untouched()
        self.assertTrue(self.record.approved)
        self.assertTrue(self.record.enabled)
        self.assertTrue(self.record.armed)

    def test_interrupted_rename_gap_recovers_then_updates(self):
        previous = self.root / ".android-tools-update-0.2.0/previous"
        previous.parent.mkdir()
        os.replace(self.target, previous)
        self.assertTrue(updater.perform_update(self.ctx)["updated"])
        self.assertEqual((self.target / "main.py").read_bytes(), self.contents["main.py"])

    def test_generation_in_progress_blocks_update(self):
        self.app.is_generating = True
        with self.assertRaises(updater.UpdateError):
            updater.perform_update(self.ctx)
        self.assert_old_untouched()

    def test_changed_already_updated_code_is_not_overwritten(self):
        updater.perform_update(self.ctx)
        (self.target / "main.py").write_text("CUSTOM CHANGE")
        with self.assertRaises(updater.UpdateError):
            updater.perform_update(self.ctx)
        self.assertEqual((self.target / "main.py").read_text(), "CUSTOM CHANGE")

    def test_actual_upstream_loader_requires_reapproval_after_restart(self):
        runtime_source = SOURCE.parent / "source/NAIA2.0/core/extension_runtime.py"
        if not runtime_source.is_file():
            self.skipTest("Pinned upstream source is not in this checkout")
        runtime_spec = importlib.util.spec_from_file_location("updater_real_runtime", runtime_source)
        runtime = importlib.util.module_from_spec(runtime_spec)
        with patch.dict(sys.modules, {runtime_spec.name: runtime}):
            runtime_spec.loader.exec_module(runtime)
        updater.perform_update(self.ctx)
        paths = NS(config_dir=self.config_dir, extensions_dir=self.root)
        app = NS(runtime_paths=paths)
        restarted = runtime.ExtensionManager(app)
        # Other fixture manifest is intentionally absent; the target is discovered.
        records = restarted.load_all()
        record = next(r for r in records if r.ext_id == updater.TARGET_ID)
        self.assertEqual(record.version, "0.2.0")
        self.assertEqual(record.status, "discovered")
        self.assertFalse(record.approved)
        self.assertTrue(record.enabled)
        self.assertFalse(record.is_active)
        self.assertNotIn("NEW CODE MUST NOT EXECUTE", record.error)


if __name__ == "__main__":
    unittest.main()
