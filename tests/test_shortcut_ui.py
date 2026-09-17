import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fluentpy.qt import QtCore, QtGui, QtWidgets
from PySide6.QtTest import QTest
from fluentmelody.client.app import MainWindow
from fluentmelody.client.config import Config
from fluentmelody.core.hotkeys import validate_bindings


class Registration:
    """Models an OS conflict without reserving or sending real keys."""
    def __init__(self):
        self.bindings={1:'F1',4:'F4'}
        self.suspended=False
        self.started=False
        self.conflict=None

    def reconfigure(self,bindings):
        bindings=validate_bindings(bindings)
        if self.conflict in bindings.values():
            raise OSError('快捷键已被其他程序占用')
        self.bindings=bindings

    def suspend(self): self.suspended=True
    def resume(self): self.suspended=False
    def start(self): self.started=True
    def stop(self): self.started=False


class ShortcutConfigTests(unittest.TestCase):
    def test_old_settings_migrate_without_losing_server_or_speed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'settings.json'
            p.write_text(json.dumps({'name':'小周','speed':.9,'multiplayer_url':'http://127.0.0.1:9000'}),encoding='utf-8')
            cfg=Config(tmp)
            self.assertEqual(cfg.values['name'],'小周')
            self.assertEqual(cfg.values['speed'],.9)
            self.assertEqual(cfg.values['multiplayer_url'],'http://127.0.0.1:9000')
            self.assertEqual(cfg.values['hotkey_start'],'F1')
            self.assertEqual(cfg.values['hotkey_pause'],'F4')

    def test_corrupt_shortcuts_reset_only_shortcuts(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'settings.json'
            p.write_text(json.dumps({'name':'保留昵称','hotkey_start':'Z','hotkey_pause':None}),encoding='utf-8')
            cfg=Config(tmp)
            self.assertEqual(cfg.values['name'],'保留昵称')
            self.assertEqual(cfg.values['hotkey_start'],'F1')
            self.assertTrue(cfg.warnings)


class ShortcutUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.w=MainWindow(dry_run=True,config_dir=self.directory.name)
        self.w.tick.stop(); self.w.net_tick.stop(); self.w.ai_tick.stop()
        self.w.hotkeys=Registration()

    def tearDown(self):
        self.w.close();self.w.deleteLater()
        self.app.sendPostedEvents(None,QtCore.QEvent.Type.DeferredDelete)
        self.directory.cleanup()

    def propose(self,start='F8',pause='Ctrl+Alt+P'):
        self.w.shortcut_start.setText(start)
        self.w.shortcut_pause.setText(pause)

    def test_apply_persists_and_updates_solo_and_room_hints(self):
        self.propose()
        self.w.apply_shortcuts()
        self.assertTrue(self.w.hotkeys.started)
        self.assertEqual(Config(self.directory.name).values['hotkey_pause'],'Ctrl+Alt+P')
        self.assertIn('F8',self.w.start_button.text())
        self.assertIn('Ctrl+Alt+P',self.w.pause_button.text())
        self.assertIn('F8',self.w.ready_button.text())
        self.assertNotIn('F1',self.w.control_help.text())
        self.w.room_code='123456'
        self.w.update_shortcut_labels()
        self.assertEqual(self.w.start_button.text(),'准备  F8')
        self.w.room_code=None

    def test_duplicate_and_conflicting_registration_keep_old_settings(self):
        self.propose('F8','F8');self.w.apply_shortcuts()
        self.assertEqual(self.w.start_key,'F1')
        self.assertIn('相同',self.w.shortcut_status.text())
        self.w.hotkeys.conflict='F9'
        self.propose('F8','F9');self.w.apply_shortcuts()
        self.assertEqual(self.w.hotkeys.bindings,{1:'F1',4:'F4'})
        self.assertEqual(self.w.pause_key,'F4')
        self.assertIn('原设置',self.w.shortcut_status.text())

    def test_capture_uses_flient_field_and_suppresses_queued_action(self):
        self.w.handle_f1=Mock()
        self.w.nav.set_current_route('settings',animated=False)
        self.w.show();self.app.processEvents()
        field=self.w.shortcut_start
        field.setFocus();self.app.processEvents()
        self.assertTrue(self.w.hotkeys.suspended)
        captured_generation=self.w._hotkey_generation
        self.w.on_hotkey((captured_generation,1))
        QTest.keyClick(field,QtCore.Qt.Key.Key_F8)
        self.assertEqual(field.text(),'F8')
        field.clearFocus();self.app.processEvents()
        self.assertFalse(self.w.hotkeys.suspended)
        self.w.on_hotkey((captured_generation,1))
        self.w.handle_f1.assert_not_called()
        self.w.on_hotkey((self.w._hotkey_generation,1))
        self.w.handle_f1.assert_called_once()

    def test_record_modified_key_and_escape_restore(self):
        field=self.w.shortcut_pause
        field._original='F4'
        QTest.keyClick(field,QtCore.Qt.Key.Key_P,QtCore.Qt.KeyboardModifier.ControlModifier|QtCore.Qt.KeyboardModifier.AltModifier)
        self.assertEqual(field.text(),'Ctrl+Alt+P')
        QTest.keyClick(field,QtCore.Qt.Key.Key_Escape)
        self.assertEqual(field.text(),'F4')

    def test_reset_defaults_and_disallow_change_during_performance(self):
        self.propose();self.w.apply_shortcuts()
        self.w.reset_shortcuts()
        self.assertEqual(self.w.shortcut_bindings(),{1:'F1',4:'F4'})
        self.w.room={'status':'playing'}
        self.propose();self.w.apply_shortcuts()
        self.assertEqual(self.w.start_key,'F1')
        self.assertIn('停止演奏',self.w.shortcut_status.text())
        self.w.room=None

    def test_disk_failure_rolls_back_registered_keys(self):
        self.propose()
        with patch.object(self.w.config,'save',side_effect=OSError('磁盘无法写入')):
            self.w.apply_shortcuts()
        self.assertEqual(self.w.shortcut_bindings(),{1:'F1',4:'F4'})
        self.assertEqual(self.w.hotkeys.bindings,{1:'F1',4:'F4'})
        self.assertIn('F1',self.w.start_button.text())


if __name__=='__main__':unittest.main()
