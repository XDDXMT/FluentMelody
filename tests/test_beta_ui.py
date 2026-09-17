"""Public UI behavior without external requests or game input."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fluentpy.qt import QtCore, QtGui, QtWidgets
from fluentmelody.client.app import MainWindow, ROOT
from fluentmelody.client.config import Config


class BetaConfigTests(unittest.TestCase):
    def test_existing_settings_default_to_solo_and_preserve_preferences(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'settings.json'
            saved = {'hotkey_start':'F8','speed':.8,'multiplayer_url':'http://127.0.0.1:9001'}
            path.write_text(json.dumps(saved),encoding='utf-8')
            cfg = Config(tmp)
            self.assertFalse(cfg.values['beta_mode'])
            for key,value in saved.items():
                self.assertEqual(cfg.values[key],value)
            for invalid in ('false','true',1,None,{}):
                path.write_text(json.dumps({**saved,'beta_mode':invalid}),encoding='utf-8')
                cfg = Config(tmp)
                self.assertFalse(cfg.values['beta_mode'])
                self.assertTrue(cfg.warnings)


class BetaUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.w = MainWindow(dry_run=True,config_dir=self.directory.name)
        self.w.tick.stop(); self.w.net_tick.stop(); self.w.ai_tick.stop()

    def tearDown(self):
        self.w.room_code = None
        self.w.close(); self.w.pool.shutdown(wait=True,cancel_futures=True)
        self.w.mp.close(); self.w.ai.close()
        self.w.deleteLater()
        self.app.sendPostedEvents(None,QtCore.QEvent.Type.DeferredDelete)
        self.directory.cleanup()

    def test_default_hides_routes_and_blocks_external_actions(self):
        self.assertFalse(self.w.beta_enabled)
        for key in ('room','ai'):
            self.assertFalse(self.w.nav.is_route_visible(key))
            self.w.nav.set_current_route(key,animated=False)
            self.assertEqual(self.w.nav.current_route(),'solo')
        self.assertTrue(self.w.connection_card.isHidden())
        with patch.object(self.w,'run_task') as run:
            self.w.create_room(); self.w.join_room()
            self.w.redeem('mp'); self.w.refresh_account('ai')
            self.w.submit_ai(); self.w.poll_room(); self.w.poll_job()
            run.assert_not_called()

    def test_toggle_persists_restores_and_returns_to_solo(self):
        self.w.beta_switch.setChecked(True)
        self.assertTrue(Config(self.directory.name).values['beta_mode'])
        self.assertTrue(self.w.nav.is_route_visible('room'))
        self.assertTrue(self.w.nav.is_route_visible('ai'))
        self.assertFalse(self.w.connection_card.isHidden())
        self.w.nav.set_current_route('ai',animated=False)
        self.w.beta_switch.setChecked(False)
        self.assertEqual(self.w.nav.current_route(),'solo')
        self.assertFalse(Config(self.directory.name).values['beta_mode'])
        self.w.beta_switch.setChecked(True)
        restored = MainWindow(dry_run=True,config_dir=self.directory.name)
        try:
            self.assertTrue(restored.nav.is_route_visible('ai'))
            self.assertTrue(restored.beta_switch.isChecked())
        finally:
            restored.close(); restored.mp.close(); restored.ai.close()
            restored.deleteLater()

    def test_cannot_hide_active_room_or_pending_submit(self):
        self.w.set_beta_mode(True)
        self.w.room_code = '123456'
        self.w.set_beta_mode(False)
        self.assertTrue(self.w.beta_switch.isChecked())
        self.w.room_code = None
        for key in ('room_join','room_leave','ai_submit','account_ai','redeem_mp'):
            self.w._pending.add(key)
            self.w.set_beta_mode(False)
            self.assertTrue(self.w.beta_enabled,key)
            self.w._pending.remove(key)
        self.w.set_beta_mode(False)
        self.assertFalse(self.w.beta_enabled)

    def test_ai_poll_pauses_when_beta_hidden_and_resumes_without_losing_job(self):
        self.w.set_beta_mode(True)
        self.w.job_id = 'example-job'
        self.w._pending.add('ai_poll')
        self.w.set_beta_mode(False)
        self.assertFalse(self.w.beta_enabled)
        self.assertEqual(self.w.job_id,'example-job')
        self.w._pending.remove('ai_poll')
        with patch.object(self.w,'run_task') as run:
            self.w.poll_job(); self.w.download_ai()
            run.assert_not_called()
            self.w.set_beta_mode(True)
            self.w.poll_job()
            self.assertEqual(run.call_args.kwargs['key'],'ai_poll')

    def test_save_error_preserves_visibility_and_switch(self):
        with patch.object(self.w.config,'save',side_effect=OSError('磁盘无法写入')):
            self.w.beta_switch.setChecked(True)
        self.assertFalse(self.w.beta_enabled)
        self.assertFalse(self.w.beta_switch.isChecked())
        self.assertFalse(self.w.nav.is_route_visible('room'))
        self.assertIn('未能保存',self.w.beta_status.text())

    def test_general_settings_save_without_beta_or_network(self):
        self.w.update_url.setText('https://example.com/releases.json')
        self.w.mp_url.setText('an unfinished beta address')
        with patch.object(self.w.mp,'close') as close:
            self.w.save_settings()
            close.assert_not_called()
        self.assertEqual(Config(self.directory.name).values['update_url'],
                         'https://example.com/releases.json')

    def test_about_authorship_and_local_license_link(self):
        self.assertTrue(self.w.nav.is_route_visible('about'))
        self.w.nav.set_current_route('about',animated=False)
        self.w.show(); self.app.processEvents()
        text = '\n'.join(w.text() for w in self.w.findChildren(QtWidgets.QLabel) if w.isVisible())
        self.assertIn('制作作者：性邓的小馒头',text)
        self.assertIn('自研 UI 组件库 FluentPy',text)
        self.assertIn('MIT',text)
        self.assertIn('GPLv3',text)
        self.assertIn('Qt / PySide6',text)
        with patch.object(QtGui.QDesktopServices,'openUrl',return_value=True) as open_url:
            self.w.open_notice('licenses/FluentPy-LICENSE.txt')
            path = Path(open_url.call_args.args[0].toLocalFile())
            self.assertTrue(path.is_file())
            self.assertIn('MIT License',path.read_text('utf-8'))

    def test_actual_nbs_drop_over_logs_loads_converts_but_does_not_play(self):
        self.w.nav.set_current_route('about',animated=False)
        self.w.show(); self.app.processEvents()
        mime = QtCore.QMimeData()
        path = ROOT/'assets'/'两只老虎.nbs'
        mime.setUrls([QtCore.QUrl.fromLocalFile(str(path))])
        target = self.w.logs.viewport()
        enter = QtGui.QDragEnterEvent(QtCore.QPoint(8,8),QtCore.Qt.DropAction.CopyAction,
                                     mime,QtCore.Qt.MouseButton.LeftButton,QtCore.Qt.KeyboardModifier.NoModifier)
        drop = QtGui.QDropEvent(QtCore.QPointF(8,8),QtCore.Qt.DropAction.CopyAction,
                               mime,QtCore.Qt.MouseButton.LeftButton,QtCore.Qt.KeyboardModifier.NoModifier)
        with patch.object(self.w.player,'play') as play, patch.object(self.w.ai,'request') as request:
            self.app.sendEvent(target,enter); self.app.sendEvent(target,drop)
            deadline = time.monotonic()+8
            while self.w.plan is None and time.monotonic()<deadline:
                self.app.processEvents(); time.sleep(.01)
            self.assertTrue(drop.isAccepted())
            self.assertEqual(self.w.nav.current_route(),'solo')
            self.assertEqual(self.w.file_path,path)
            self.assertTrue(self.w.plan['tracks'][0]['events'])
            play.assert_not_called(); request.assert_not_called()

    def test_drop_during_room_never_uploads_or_replaces_song(self):
        self.w.set_beta_mode(True)
        self.w.room_code = '123456'
        with patch.object(self.w,'upload_song') as upload, patch.object(self.w,'run_task') as run:
            self.w.load_dropped_song(ROOT/'assets'/'两只老虎.nbs')
            upload.assert_not_called(); run.assert_not_called()
        self.assertIsNone(self.w.file_path)
        self.assertIn('请先退出房间',self.w.logs.toPlainText())


if __name__=='__main__':
    unittest.main()
