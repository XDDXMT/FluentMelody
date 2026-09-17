import threading
import time
import unittest

from fluentmelody.core.playback import Player


class RecordingInput:
    def __init__(self):
        self.events = []
        self.active = set()
        self.lock = threading.Lock()

    def record(self, kind, value, up):
        with self.lock:
            self.events.append((time.monotonic(), kind, value, up))
            if up:
                self.active.discard((kind, value))
            else:
                self.active.add((kind, value))

    def key(self, index, up=False):
        self.record('key', index, up)

    def mouse(self, name, up=False):
        self.record('mouse', name, up)


def note(start=0, duration=.3, index=0, modifiers=None, midi=60):
    return {'start': start, 'duration': duration, 'key_index': index,
            'modifiers': modifiers or [], 'midi': midi, 'layer': 0}


def wait_for(predicate, seconds=2):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.003)
    return False


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.backend = RecordingInput()
        self.player = Player(backend=self.backend)

    def tearDown(self):
        self.player.stop()

    def test_stop_releases_keyboard_and_modifiers(self):
        self.player.play([note(duration=1, modifiers=['right'], midi=72)])
        self.assertTrue(wait_for(lambda: ('key', 0) in self.backend.active))
        self.player.stop()
        self.assertFalse(self.backend.active)
        self.assertEqual(len([e for e in self.backend.events if e[3]]), 2)

    def test_pause_releases_and_resume_obeys_gap(self):
        self.player.play([note(duration=.4)])
        self.assertTrue(wait_for(lambda: self.backend.active))
        self.player.pause()
        self.assertTrue(wait_for(lambda: not self.backend.active))
        frozen = self.player.position
        time.sleep(.025)
        self.assertAlmostEqual(self.player.position, frozen, places=4)
        self.player.resume()
        self.assertTrue(wait_for(lambda: len([e for e in self.backend.events if not e[3]]) >= 2))
        ups = [e for e in self.backend.events if e[3]]
        downs = [e for e in self.backend.events if not e[3]]
        self.assertGreaterEqual(downs[1][0] - ups[0][0], .098)

    def test_scheduled_resume_uses_requested_epoch_at_nonzero_position(self):
        self.player.play([note(duration=1)])
        self.assertTrue(wait_for(lambda: self.player.position > .12))
        self.player.pause()
        self.assertTrue(wait_for(lambda: not self.backend.active))
        scheduled = time.time() + .2
        before = time.monotonic()
        self.player.resume(start_at=scheduled)
        time.sleep(.1)
        self.assertFalse(self.backend.active)
        self.assertTrue(wait_for(lambda: self.backend.active))
        self.assertGreaterEqual(self.backend.events[-1][0] - before, .18)

    def test_countdown_stop_never_presses(self):
        self.player.play([note()], start_at=time.time() + .3)
        time.sleep(.03)
        self.player.stop()
        self.assertEqual(self.backend.events, [])

    def test_scheduled_play_with_seek_waits_and_freezes_position(self):
        before = time.monotonic()
        self.player.play([note(start=10, duration=1)], start_at=time.time() + .2, position=10.1)
        time.sleep(.1)
        self.assertFalse(self.backend.active)
        self.assertEqual(self.player.state, 'countdown')
        self.assertAlmostEqual(self.player.position, 10.1, places=3)
        self.assertTrue(wait_for(lambda: self.backend.active))
        self.assertGreaterEqual(self.backend.events[-1][0] - before, .18)

    def test_real_release_interval_and_no_overlap(self):
        self.player.play([note(duration=.12), note(start=.22, duration=.12, index=1, midi=62)])
        self.assertTrue(wait_for(lambda: self.player.state == 'finished'))
        downs = [e for e in self.backend.events if not e[3]]
        ups = [e for e in self.backend.events if e[3]]
        self.assertEqual(len(downs), 2)
        self.assertGreaterEqual(downs[1][0] - ups[0][0], .098)
        self.assertFalse(self.backend.active)

    def test_failure_still_releases_modifiers(self):
        def broken_key(index, up=False):
            raise RuntimeError('injected failure')
        self.backend.key = broken_key
        self.player.play([note(modifiers=['right'], midi=72)])
        self.assertTrue(wait_for(lambda: self.player.state == 'error'))
        self.assertTrue(wait_for(lambda: not self.backend.active))
        self.assertIn('injected failure', self.player.error)

    def test_invalid_replacement_stops_old_song(self):
        self.player.play([note(duration=1, modifiers=['right'], midi=72)])
        self.assertTrue(wait_for(lambda: ('key', 0) in self.backend.active))
        with self.assertRaises(ValueError):
            self.player.play([note(midi=99)])
        self.assertFalse(self.backend.active)
        self.assertEqual(self.player.state, 'stopped')

    def test_replacement_keeps_gap_from_old_release(self):
        self.player.play([note(duration=1)])
        self.assertTrue(wait_for(lambda: ('key', 0) in self.backend.active))
        self.player.play([note(duration=.3, index=1, midi=62)])
        self.assertTrue(wait_for(lambda: ('key', 1) in self.backend.active))
        ups = [e for e in self.backend.events if e[1] == 'key' and e[3]]
        downs = [e for e in self.backend.events if e[1] == 'key' and not e[3]]
        self.assertGreaterEqual(downs[1][0] - ups[0][0], .098)

    def test_mouse_release_failure_retried_without_leaving_key_held(self):
        original = self.backend.mouse
        failed = []
        def once(name, up=False):
            if up and not failed:
                failed.append(True)
                raise RuntimeError('temporary mouse-up failure')
            original(name, up)
        self.backend.mouse = once
        self.player.play([note(duration=1, modifiers=['right'], midi=72)])
        self.assertTrue(wait_for(lambda: ('key', 0) in self.backend.active))
        self.player.stop()
        self.assertTrue(failed)
        self.assertFalse(self.backend.active)

    def test_octave_semitone_combinations_press_before_one_note_and_release_in_order(self):
        self.player.play([note(duration=.12, modifiers=['left', 'middle'], midi=49),
                          note(start=.24, duration=.12, index=7, modifiers=['right', 'middle'], midi=85),
                          note(start=.48, duration=.12, index=4, midi=67)])
        self.assertTrue(wait_for(lambda: self.player.state == 'finished'))
        self.assertTrue(wait_for(lambda: not self.backend.active))
        events = self.backend.events
        self.assertEqual([(kind, value, up) for _, kind, value, up in events], [
            ('mouse', 'left', False), ('mouse', 'middle', False), ('key', 0, False),
            ('key', 0, True), ('mouse', 'middle', True), ('mouse', 'left', True),
            ('mouse', 'right', False), ('mouse', 'middle', False), ('key', 7, False),
            ('key', 7, True), ('mouse', 'middle', True), ('mouse', 'right', True),
            ('key', 4, False), ('key', 4, True),
        ])
        self.assertGreaterEqual(events[6][0] - events[5][0], .099)
        self.assertGreaterEqual(events[12][0] - events[11][0], .099)

    def test_pause_resume_and_stop_release_both_modifiers(self):
        self.player.play([note(duration=1, index=7, modifiers=['right', 'middle'], midi=85)])
        self.assertTrue(wait_for(lambda: ('key', 7) in self.backend.active))
        self.player.pause()
        self.assertTrue(wait_for(lambda: not self.backend.active))
        released_at = self.backend.events[-1][0]
        self.player.resume()
        self.assertTrue(wait_for(lambda: ('key', 7) in self.backend.active))
        self.assertGreaterEqual(self.backend.events[6][0] - released_at, .099)
        self.player.stop()
        self.assertFalse(self.backend.active)
        self.assertEqual([(kind, value) for _, kind, value, up in self.backend.events[-3:]],
                         [('key', 7), ('mouse', 'middle'), ('mouse', 'right')])
        self.assertTrue(all(e[3] for e in self.backend.events[-3:]))

    def test_second_modifier_failure_releases_first_without_pressing_note(self):
        original = self.backend.mouse
        def fail_middle(name, up=False):
            if name == 'middle' and not up:
                raise RuntimeError('injected middle-down failure')
            original(name, up)
        self.backend.mouse = fail_middle
        self.player.play([note(modifiers=['left', 'middle'], midi=49)])
        self.assertTrue(wait_for(lambda: self.player.state == 'error'))
        self.assertTrue(wait_for(lambda: not self.backend.active))
        self.assertEqual([(kind, value, up) for _, kind, value, up in self.backend.events],
                         [('mouse', 'left', False), ('mouse', 'left', True)])

    def test_note_press_failure_releases_both_modifiers(self):
        def fail_key(index, up=False):
            raise RuntimeError('injected note-down failure')
        self.backend.key = fail_key
        self.player.play([note(modifiers=['left', 'middle'], midi=49)])
        self.assertTrue(wait_for(lambda: self.player.state == 'error'))
        self.assertTrue(wait_for(lambda: not self.backend.active))
        self.assertEqual([(kind, value, up) for _, kind, value, up in self.backend.events], [
            ('mouse', 'left', False), ('mouse', 'middle', False),
            ('mouse', 'middle', True), ('mouse', 'left', True)])


if __name__ == '__main__':
    unittest.main()
