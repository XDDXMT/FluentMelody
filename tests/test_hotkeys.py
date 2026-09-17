import threading
import time
import unittest

from fluentmelody.core.hotkeys import Hotkeys, normalize_hotkey, parse_hotkey, validate_bindings


class ParserTests(unittest.TestCase):
    def test_canonical_names_and_order(self):
        self.assertEqual(normalize_hotkey(' alt + control + shift + p '), 'Ctrl+Alt+Shift+P')
        self.assertEqual(normalize_hotkey('ctrl+pgdn'), 'Ctrl+PageDown')
        self.assertEqual(normalize_hotkey('f24'), 'F24')
        self.assertEqual(parse_hotkey('Ctrl+Alt+F6').vk, 0x75)
        self.assertEqual(parse_hotkey('Ctrl+Alt+F6').modifiers, 3)

    def test_reject_note_keys_even_with_modifiers(self):
        for text in ('Z', 'Ctrl+X', 'Alt+C', 'Shift+V', 'Ctrl+B', 'N', 'M', ',', 'Ctrl+,'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                normalize_hotkey(text)

    def test_reject_system_chords_and_invalid_sequences(self):
        for text in ('', 'F12', 'Ctrl+F12', 'Alt+F4', 'Ctrl+Alt+Delete', 'Ctrl+Shift+Esc',
                     'Win+P', 'Meta+F2', 'Alt+Tab', 'Ctrl+Ctrl+P', 'F1,F2', 'Ctrl',
                     'P', '5', 'Ctrl++', 'F25', 'Ctrl+🐈', 'Ctrl+P+Q'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                normalize_hotkey(text)

    def test_bindings_complete_and_distinct(self):
        self.assertEqual(validate_bindings({1: 'f6', 4: 'alt+ctrl+p'}), {1: 'F6', 4: 'Ctrl+Alt+P'})
        for value in ({1: 'F1'}, {1: 'F1', 4: 'f1'}, {1: 'Ctrl+Alt+P', 4: 'Alt+Ctrl+P'},
                      {1: 'F1', 4: 'F4', 6: 'F6'}, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_bindings(value)


class FakeBackend:
    def __init__(self):
        self.active = {}
        self.blocked = set()
        self.messages = []
        self.pressed = set()
        self.thread_ids = set()
        self.calls = []
        self.lock = threading.Lock()

    def register(self, action, spec):
        with self.lock:
            self.thread_ids.add(threading.get_ident())
            self.calls.append(('register', action, spec.text))
            if spec.text in self.blocked:
                raise OSError(f'{spec.text} 已被其他程序占用。')
            if action in self.active:
                raise AssertionError('identifier already registered')
            self.active[action] = spec

    def unregister(self, action):
        with self.lock:
            self.thread_ids.add(threading.get_ident())
            self.calls.append(('unregister', action))
            del self.active[action]

    def poll(self):
        with self.lock:
            result, self.messages = self.messages, []
            return result

    def is_pressed(self, vk):
        with self.lock:
            return vk in self.pressed

    def press(self, spec, action):
        with self.lock:
            self.pressed.update(spec.keys)
            self.messages.append(action)

    def release(self, keys):
        with self.lock:
            self.pressed.difference_update(keys)


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.actions = []
        self.hotkeys = Hotkeys(self.actions.append, _backend_factory=lambda: self.backend)

    def tearDown(self):
        self.hotkeys.stop()

    def wait_for(self, check):
        deadline = time.monotonic() + .7
        while not check() and time.monotonic() < deadline:
            self.hotkeys._wakeup.set()
            time.sleep(.003)
        self.assertTrue(check())

    def test_synchronous_start_stop_and_single_owner(self):
        self.assertTrue(self.hotkeys.start())
        self.assertEqual(self.hotkeys.registered, {1, 4})
        self.assertEqual({key: spec.text for key, spec in self.backend.active.items()}, {1: 'F1', 4: 'F4'})
        self.hotkeys.reconfigure({1: 'F6', 4: 'Ctrl+Alt+P'})
        self.assertEqual(self.hotkeys.bindings, {1: 'F6', 4: 'Ctrl+Alt+P'})
        self.assertTrue(self.hotkeys.start())
        self.hotkeys.stop()
        self.assertFalse(self.backend.active)
        self.assertEqual(len(self.backend.thread_ids), 1)
        self.assertNotIn(threading.get_ident(), self.backend.thread_ids)

    def test_conflict_rolls_back_both_previous_bindings(self):
        self.hotkeys.start()
        self.backend.blocked.add('F8')
        with self.assertRaises(OSError):
            self.hotkeys.reconfigure({1: 'F6', 4: 'F8'})
        self.assertEqual(self.hotkeys.bindings, {1: 'F1', 4: 'F4'})
        self.assertEqual({key: spec.text for key, spec in self.backend.active.items()}, {1: 'F1', 4: 'F4'})
        self.assertEqual(self.hotkeys.registered, {1, 4})

    def test_start_failure_cleans_partial_registration_and_can_restart(self):
        self.backend.blocked.add('F4')
        with self.assertRaises(OSError):
            self.hotkeys.start()
        self.assertFalse(self.backend.active)
        self.assertFalse(self.hotkeys.registered)
        self.hotkeys.reconfigure({1: 'F6', 4: 'F8'})
        self.hotkeys.resume()
        self.assertTrue(self.hotkeys.start())
        self.assertEqual(self.hotkeys.registered, {1, 4})

    def test_suspend_reconfigure_resume_ignores_editor_held_key_and_stale_messages(self):
        self.hotkeys.start()
        self.hotkeys.suspend()
        self.assertFalse(self.backend.active)
        self.hotkeys.reconfigure({1: 'Ctrl+Alt+P', 4: 'F8'})
        self.assertFalse(self.backend.active)
        self.assertTrue(self.hotkeys.suspended)
        spec = parse_hotkey('Ctrl+Alt+P')
        self.backend.press(spec, 1)
        self.hotkeys.resume()
        self.backend.press(spec, 1)
        self.wait_for(lambda: not self.backend.messages)
        self.assertEqual(self.actions, [])
        self.backend.release(spec.keys)
        self.wait_for(lambda: self.hotkeys._armed[1])
        self.backend.press(spec, 1)
        self.wait_for(lambda: self.actions == [1])
        self.backend.press(spec, 1)
        self.wait_for(lambda: not self.backend.messages)
        self.assertEqual(self.actions, [1])
        # Repeated main-key taps work while Ctrl and Alt remain physically held.
        self.backend.release((spec.vk,))
        self.wait_for(lambda: self.hotkeys._armed[1])
        self.backend.press(spec, 1)
        self.wait_for(lambda: self.actions == [1, 1])

    def test_failed_configuration_while_suspended_preserves_previous_values(self):
        self.hotkeys.start()
        self.hotkeys.suspend()
        self.backend.blocked.add('F8')
        with self.assertRaises(OSError):
            self.hotkeys.reconfigure({1: 'F6', 4: 'F8'})
        self.assertEqual(self.hotkeys.bindings, {1: 'F1', 4: 'F4'})
        self.assertFalse(self.backend.active)
        self.assertTrue(self.hotkeys.suspended)
        self.hotkeys.resume()
        self.assertEqual(self.hotkeys.registered, {1, 4})

    def test_resume_conflict_is_visible_and_does_not_poll_reserved_key(self):
        self.hotkeys.start()
        self.hotkeys.suspend()
        self.backend.blocked.add('F1')
        with self.assertRaises(OSError):
            self.hotkeys.resume()
        self.assertTrue(self.hotkeys.suspended)
        self.assertFalse(self.backend.active)
        self.backend.press(parse_hotkey('F1'), 1)
        self.wait_for(lambda: not self.backend.messages)
        self.assertEqual(self.actions, [])

    def test_failed_rollback_disables_all_and_explains_failure(self):
        self.hotkeys.start()
        self.backend.blocked.update(('F8', 'F4'))
        with self.assertRaisesRegex(OSError, '原快捷键也无法恢复'):
            self.hotkeys.reconfigure({1: 'F6', 4: 'F8'})
        self.assertEqual(self.hotkeys.bindings, {1: 'F1', 4: 'F4'})
        self.assertTrue(self.hotkeys.suspended)
        self.assertFalse(self.backend.active)

    def test_callback_failure_reports_without_losing_registration(self):
        errors = []
        self.hotkeys.on_error = errors.append
        self.hotkeys.callback = lambda _: (_ for _ in ()).throw(ValueError('test callback failed'))
        self.hotkeys.start()
        self.backend.press(parse_hotkey('F1'), 1)
        self.wait_for(lambda: bool(errors))
        self.assertEqual(errors, ['test callback failed'])
        self.assertTrue(self.hotkeys._thread.is_alive())
        self.assertEqual(self.hotkeys.registered, {1, 4})


if __name__ == '__main__':
    unittest.main()
