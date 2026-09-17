"""The local AI switch changes conversion, without network or game input."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fluentpy.qt import QtCore, QtGui, QtWidgets
from fluentmelody.client.app import MainWindow, ROOT
from fluentmelody.client.config import Config
from fluentmelody.core.music import read_music


def plan(name):
    return {'name': name, 'summary': name, 'warnings': [], 'duration': 1.0,
            'tracks': [{'events': [{'start': 0.0, 'duration': .2, 'midi': 60}]}]}


class OptionalAIConfigTests(unittest.TestCase):
    def test_existing_settings_default_on_and_keep_user_preferences(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'settings.json'
            saved = {'hotkey_start': 'F8', 'speed': .8, 'beta_mode': False}
            path.write_text(json.dumps(saved), encoding='utf-8')
            cfg = Config(tmp)
            self.assertIs(cfg.values['ai_auto_convert'], True)
            for key, value in saved.items():
                self.assertEqual(cfg.values[key], value)
            self.assertFalse(cfg.warnings)

    def test_only_boolean_values_are_accepted_and_false_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'settings.json'
            for value in (True, False, 'false', 'true', 0, 1, None, [], {}):
                with self.subTest(value=value):
                    path.write_text(json.dumps({'ai_auto_convert': value}), encoding='utf-8')
                    cfg = Config(tmp)
                    if type(value) is bool:
                        self.assertIs(cfg.values['ai_auto_convert'], value)
                        self.assertFalse(cfg.warnings)
                    else:
                        self.assertIs(cfg.values['ai_auto_convert'], True)
                        self.assertTrue(cfg.warnings)
                    cfg.save()
                    self.assertIs(Config(tmp).values['ai_auto_convert'], cfg.values['ai_auto_convert'])


class OptionalAIUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.w = MainWindow(dry_run=True, config_dir=self.directory.name)
        self.w.tick.stop(); self.w.net_tick.stop(); self.w.ai_tick.stop()
        self.gates = []

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        self.w.room_code = None
        self.w.close(); self.w.pool.shutdown(wait=True, cancel_futures=True)
        self.w.mp.close(); self.w.ai.close()
        self.w.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        self.directory.cleanup()

    def wait_until(self, predicate, message='operation did not finish'):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(.005)
        self.fail(message)

    def gate(self):
        event = threading.Event()
        self.gates.append(event)
        return event

    def set_song(self):
        self.w.song = read_music(ROOT / 'assets' / '两只老虎.nbs')
        self.w.plan = plan('original')
        self.w.notes_view.plan = self.w.plan

    def test_switch_is_on_main_page_and_independent_of_beta(self):
        self.w.show(); self.app.processEvents()
        self.assertEqual(self.w.nav.current_route(), 'solo')
        self.assertTrue(self.w.ai_convert_switch.isVisible())
        self.assertTrue(self.w.ai_convert_switch.isChecked())
        self.assertIn('已开启', self.w.ai_convert_hint.text())
        for beta in (True, False):
            self.w.set_beta_mode(beta)
            self.assertTrue(self.w.ai_convert_switch.isChecked())
            self.assertTrue(self.w.ai_convert_switch.isVisible())
        self.w.nav.set_current_route('settings', animated=False)
        self.app.processEvents()
        self.assertFalse(self.w.ai_convert_switch.isVisible())
        self.w.nav.set_current_route('solo', animated=False)
        self.app.processEvents()
        self.assertTrue(self.w.ai_convert_switch.isVisible())

    def test_without_song_switch_only_saves_and_restores_next_launch(self):
        with patch.object(self.w, 'run_task') as run, patch.object(self.w.player, 'stop') as stop:
            self.w.ai_convert_switch.setChecked(False)
            run.assert_not_called(); stop.assert_not_called()
        self.assertFalse(Config(self.directory.name).values['ai_auto_convert'])
        self.assertIn('已关闭', self.w.ai_convert_hint.text())
        restored = MainWindow(dry_run=True, config_dir=self.directory.name)
        try:
            restored.tick.stop(); restored.net_tick.stop(); restored.ai_tick.stop()
            self.assertFalse(restored.ai_convert_switch.isChecked())
            self.assertFalse(restored.config.values['ai_auto_convert'])
            self.assertIn('普通转换', restored.ai_convert_hint.text())
        finally:
            restored.close(); restored.pool.shutdown(wait=True, cancel_futures=True)
            restored.mp.close(); restored.ai.close()
            restored.deleteLater()
            self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)

    def test_failed_save_rolls_back_switch_and_keeps_current_performance(self):
        self.set_song()
        original = self.w.plan
        with patch.object(self.w.config, 'save', side_effect=OSError('磁盘无法写入')), \
                patch.object(self.w, 'convert_song') as convert, \
                patch.object(self.w.player, 'stop') as stop:
            self.w.ai_convert_switch.setChecked(False)
            convert.assert_not_called(); stop.assert_not_called()
        self.assertTrue(self.w.ai_convert_switch.isChecked())
        self.assertIs(self.w.config.values['ai_auto_convert'], True)
        self.assertIs(self.w.plan, original)
        self.assertIn('未能保存', self.w.logs.toPlainText())

    def test_toggle_stops_playback_and_clears_old_plan_before_converting(self):
        self.set_song()
        started, finish = self.gate(), self.gate()

        def convert(song, **options):
            self.assertFalse(options['use_ai'])
            self.assertEqual(options['gap'], .1)
            started.set()
            self.assertTrue(finish.wait(5))
            return plan('ordinary result')

        with patch('fluentmelody.client.app.arrange', side_effect=convert) as arrange, \
                patch.object(self.w.player, 'stop') as stop, \
                patch.object(self.w.player, 'play') as play:
            self.w.ai_convert_switch.setChecked(False)
            self.wait_until(started.is_set)
            stop.assert_called_once()
            self.assertIsNone(self.w.plan)
            self.assertIsNone(self.w.notes_view.plan)
            finish.set()
            self.wait_until(lambda: self.w.plan is not None)
            self.assertEqual(self.w.plan['name'], 'ordinary result')
            arrange.assert_called_once()
            play.assert_not_called()

    def test_repeated_toggles_during_conversion_run_latest_choice_once(self):
        self.set_song()
        started, finish, second_started, second_finish = (self.gate() for _ in range(4))
        calls = []

        def convert(song, **options):
            calls.append(options['use_ai'])
            if len(calls) == 1:
                started.set()
                self.assertTrue(finish.wait(5))
                return plan('outdated AI result')
            second_started.set()
            self.assertTrue(second_finish.wait(5))
            return plan('latest ordinary result')

        with patch('fluentmelody.client.app.arrange', side_effect=convert):
            self.w.convert_song()
            self.wait_until(started.is_set)
            for enabled in (False, True, False):
                self.w.ai_convert_switch.setChecked(enabled)
            self.w.convert_song(); self.w.convert_song()
            self.assertEqual(calls, [True])
            self.assertTrue(self.w._conversion_refresh_requested)
            finish.set()
            self.wait_until(second_started.is_set)
            self.assertIsNone(self.w.plan)
            self.assertNotIn('outdated AI result', self.w.logs.toPlainText())
            self.assertEqual(calls, [True, False])
            second_finish.set()
            self.wait_until(lambda: self.w.plan is not None)
            self.assertEqual(self.w.plan['name'], 'latest ordinary result')
            self.assertFalse(self.w._conversion_refresh_requested)
            self.assertNotIn('convert', self.w._pending)

    def test_outdated_conversion_error_does_not_cancel_new_choice(self):
        self.set_song()
        started, finish = self.gate(), self.gate()
        calls = []

        def convert(song, **options):
            calls.append(options['use_ai'])
            if len(calls) == 1:
                started.set()
                self.assertTrue(finish.wait(5))
                raise ValueError('stale model error')
            return plan('ordinary retry')

        with patch('fluentmelody.client.app.arrange', side_effect=convert):
            self.w.convert_song()
            self.wait_until(started.is_set)
            self.w.ai_convert_switch.setChecked(False)
            finish.set()
            self.wait_until(lambda: self.w.plan is not None)
            self.assertEqual(calls, [True, False])
            self.assertEqual(self.w.plan['name'], 'ordinary retry')
            self.assertNotIn('stale model error', self.w.logs.toPlainText())

    def test_current_failure_leaves_no_stale_plan_and_can_be_retried(self):
        self.set_song()
        with patch('fluentmelody.client.app.arrange', side_effect=ValueError('conversion failed')):
            self.w.convert_song()
            self.wait_until(lambda: 'convert' not in self.w._pending)
        self.assertIsNone(self.w.plan)
        self.assertIsNone(self.w.notes_view.plan)
        self.assertIn('转换失败', self.w.conversion_info.text())
        self.assertIn('conversion failed', self.w.logs.toPlainText())
        with patch('fluentmelody.client.app.arrange', return_value=plan('successful retry')):
            self.w.convert_song()
            self.wait_until(lambda: self.w.plan is not None)
        self.assertEqual(self.w.plan['name'], 'successful retry')

    def test_room_toggle_changes_only_future_solo_preference(self):
        self.set_song()
        original = self.w.plan
        self.w.room_code = '123456'
        with patch.object(self.w, 'convert_song') as convert, \
                patch.object(self.w.player, 'stop') as stop, \
                patch.object(self.w.mp, 'request') as request:
            self.w.ai_convert_switch.setChecked(False)
            convert.assert_not_called(); stop.assert_not_called(); request.assert_not_called()
        self.assertIs(self.w.plan, original)
        self.assertFalse(Config(self.directory.name).values['ai_auto_convert'])
        self.assertIn('下次单机转换', self.w.logs.toPlainText())

    def test_room_preference_change_rechecks_inflight_solo_result_after_leaving(self):
        for first_fails in (False, True):
            with self.subTest(first_fails=first_fails):
                self.w.song = None
                self.w.set_ai_auto_convert(True)
                self.set_song()
                started, finish, retry_started, retry_finish = (self.gate() for _ in range(4))
                calls = []

                def convert(song, **options):
                    calls.append(options['use_ai'])
                    if len(calls) == 1:
                        started.set()
                        self.assertTrue(finish.wait(5))
                        if first_fails:
                            raise ValueError('outdated pre-room model error')
                        return plan('outdated pre-room AI result')
                    retry_started.set()
                    self.assertTrue(retry_finish.wait(5))
                    return plan('post-room ordinary result')

                with patch('fluentmelody.client.app.arrange', side_effect=convert):
                    self.w.convert_song()
                    self.wait_until(started.is_set)
                    self.w.room_code = '123456'
                    self.w.ai_convert_switch.setChecked(False)
                    self.assertEqual(calls, [True])
                    self.assertTrue(self.w._conversion_refresh_requested)
                    self.w.room_code = None
                    finish.set()
                    self.wait_until(retry_started.is_set)
                    self.assertEqual(calls, [True, False])
                    self.assertIsNone(self.w.plan)
                    self.assertNotIn('outdated pre-room AI result', self.w.logs.toPlainText())
                    self.assertNotIn('outdated pre-room model error', self.w.logs.toPlainText())
                    retry_finish.set()
                    self.wait_until(lambda: self.w.plan is not None)
                    self.assertEqual(self.w.plan['name'], 'post-room ordinary result')
                    self.assertNotIn('convert', self.w._pending)

    def test_dropped_song_uses_saved_conversion_choice_without_upload_or_play(self):
        self.w.set_ai_auto_convert(False)
        self.w.nav.set_current_route('settings', animated=False)
        self.w.show(); self.app.processEvents()
        path = ROOT / 'assets' / '两只老虎.nbs'
        mime = QtCore.QMimeData()
        mime.setUrls([QtCore.QUrl.fromLocalFile(str(path))])
        enter = QtGui.QDragEnterEvent(QtCore.QPoint(8, 8), QtCore.Qt.DropAction.CopyAction,
                                     mime, QtCore.Qt.MouseButton.LeftButton,
                                     QtCore.Qt.KeyboardModifier.NoModifier)
        drop = QtGui.QDropEvent(QtCore.QPointF(8, 8), QtCore.Qt.DropAction.CopyAction,
                               mime, QtCore.Qt.MouseButton.LeftButton,
                               QtCore.Qt.KeyboardModifier.NoModifier)
        with patch('fluentmelody.client.app.arrange', return_value=plan('dropped ordinary')) as arrange, \
                patch.object(self.w.player, 'play') as play, \
                patch.object(self.w.ai, 'request') as request:
            target = self.w.logs.viewport()
            self.app.sendEvent(target, enter); self.app.sendEvent(target, drop)
            self.wait_until(lambda: self.w.plan is not None)
            self.assertTrue(drop.isAccepted())
            self.assertEqual(self.w.nav.current_route(), 'solo')
            self.assertEqual(self.w.file_path, path)
            self.assertFalse(arrange.call_args.kwargs['use_ai'])
            arrange.assert_called_once()
            play.assert_not_called(); request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
