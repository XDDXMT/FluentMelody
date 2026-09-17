"""Musical behavior regressions, with controlled model evidence and real MIDI."""
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from fluentmelody.core.melody_selection import gate_length, project_timing, select_melody
from fluentmelody.core.music import TimedNote, arrange, read_music, source_notes, validate_arrangement
from fluentmelody.core.nbs_engine import Layer, Song


def note(start, pitch=72, layer=0, duration=.2, gate=None):
    return TimedNote(start, duration, pitch, layer, 100, gate)


def ordered(notes):
    return sorted(notes, key=lambda n: (n.start, -n.midi, n.layer))


def timed_song(notes, names):
    song = Song('行为测试', 5, 10, 0, [Layer(name) for name in names], [], 16, [], [])
    song.timed_notes = ordered(notes)
    song.source_format = 'midi'
    return song


def vlq(value):
    result = [value & 127]
    while value >> 7:
        value >>= 7
        result.insert(0, (value & 127) | 128)
    return bytes(result)


def midi_file(tracks):
    result = b'MThd' + struct.pack('>IHHH', 6, 1, len(tracks), 480)
    for events in tracks:
        track = b''.join(vlq(delta) + event for delta, event in events)
        result += b'MTrk' + struct.pack('>I', len(track)) + track
    return result


class MelodyBehaviorTests(unittest.TestCase):
    def test_staggered_accompaniment_does_not_fill_named_vocal_rests(self):
        melody = [note(0, 72), note(.8, 74), note(1.6, 76)]
        backing = [note(.4, 60, 1), note(1.2, 64, 1)]
        notes = ordered(melody + backing)
        selected = select_melody(notes, ['Vocal', 'Piano accompaniment']).notes
        self.assertEqual(selected, melody)

    def test_short_rests_and_held_notes_do_not_pick_chord_fillers(self):
        melody = [note(0, 72, duration=.7), note(1.2, 74), note(2, 76)]
        backing = [note(t, p, 1, duration=.12) for t in (.2, .5, .8, 1.6)
                   for p in (60, 64)]
        notes = ordered(melody + backing)
        selected = select_melody(notes, ['主旋律', '伴奏']).notes
        self.assertEqual(selected, melody)

    def test_named_vocal_keeps_instrumental_intro_long_interlude_and_ending(self):
        vocal = [note(t, p) for t, p in ((3, 72), (3.6, 74), (4.2, 76), (10, 74), (10.6, 72))]
        instrumental = [note(t, p, 1) for t, p in (
            (0, 64), (.6, 67), (1.2, 69), (5.5, 67), (6.1, 69), (6.7, 71),
            (7.3, 69), (7.9, 67), (8.5, 64), (12, 67), (12.6, 64))]
        expected = ordered(vocal + instrumental)
        bass = [note(n.start, 48, 2) for n in expected]
        notes = ordered(expected + bass)
        probabilities = [.92 if n.layer != 2 else .03 for n in notes]
        selected = select_melody(notes, ['Vocal', 'Piano', 'Bass'], probabilities).notes
        self.assertEqual(selected, expected)

    def test_model_can_choose_lower_melody_over_higher_accompaniment(self):
        melody = [note(i * .5, p) for i, p in enumerate((60, 62, 64, 65, 64, 62))]
        backing = [note(i * .5, 84 if i % 2 else 83, 1) for i in range(len(melody))]
        notes = ordered(melody + backing)
        probabilities = [.98 if n.layer == 0 else .01 for n in notes]
        selected = select_melody(notes, ['Piano 1', 'Piano 2'], probabilities).notes
        self.assertEqual(selected, melody)

    def test_model_can_choose_lower_melody_inside_one_piano_track(self):
        melody = [note(i * .5, p) for i, p in enumerate((60, 62, 64, 65, 64, 62))]
        notes = ordered(melody + [note(i * .5, 84) for i in range(len(melody))])
        probabilities = [.01 if n.midi == 84 else .98 for n in notes]
        self.assertEqual(select_melody(notes, ['Piano'], probabilities).notes, melody)

    def test_globally_low_confidence_falls_back_to_a_complete_musical_line(self):
        notes = ordered([note(i * .5, pitch + layer, layer)
                         for i, pitch in enumerate((70, 72, 74, 75) * 3) for layer in (0, 1)])
        selected = select_melody(notes, ['Piano A', 'Piano B'], [.001] * len(notes)).notes
        self.assertGreaterEqual(len(selected), 10, 'Uncertain model output must not erase a sparse song.')
        self.assertLessEqual(selected[0].start, .5)
        self.assertGreaterEqual(selected[-1].start, 5)

    def test_playable_single_voice_keeps_every_note_even_with_low_confidence(self):
        melody = [note(i * .25, p, duration=.08) for i, p in enumerate((48, 72, 49, 85, 60, 77, 61, 84))]
        self.assertEqual(select_melody(melody, ['Piano'], [.001] * len(melody)).notes, melody)
        with patch('fluentmelody.core.melody_model.predict_melody', return_value=[.001] * len(melody)):
            events = arrange(timed_song(melody, ['Piano']))['tracks'][0]['events']
        self.assertEqual([e['midi'] for e in events], [n.midi for n in melody])
        self.assertEqual([e['start'] for e in events], [n.start for n in melody])

    def test_model_failure_still_returns_a_playable_melody(self):
        melody = [note(i * .5, 60 + i) for i in range(8)]
        with patch('fluentmelody.core.melody_model.predict_melody', side_effect=OSError('missing weights')):
            plan = arrange(timed_song(melody, ['Melody']))
        self.assertEqual(len(plan['tracks'][0]['events']), len(melody))
        validate_arrangement(plan)


class TimingAndPedalTests(unittest.TestCase):
    def test_timing_projection_does_not_accumulate_delay_over_many_pairs(self):
        starts = [i * .5 + offset for i in range(100) for offset in (0, .19)]
        adjusted = project_timing(starts, .2)
        self.assertEqual(len(adjusted), len(starts))
        self.assertGreaterEqual(adjusted[0], 0)
        self.assertLessEqual(max(abs(a - b) for a, b in zip(starts, adjusted)), .011)
        self.assertLessEqual(abs(adjusted[-1] - starts[-1]), .011)
        self.assertTrue(all(b - a >= .2 - 1e-9 for a, b in zip(adjusted, adjusted[1:])))

    def test_small_timing_edits_keep_dense_pairs_without_speed_drift(self):
        starts = [i * .5 + offset for i in range(12) for offset in (0, .19)]
        notes = [note(start, 48 + i, duration=.08) for i, start in enumerate(starts)]
        with patch('fluentmelody.core.melody_model.predict_melody', return_value=[.8] * len(notes)):
            plan = arrange(timed_song(notes, ['Melody']))
        events = plan['tracks'][0]['events']
        self.assertEqual(len(events), len(notes))
        self.assertLessEqual(max(abs(e['start'] - n.start) for e, n in zip(events, notes)), .05)
        self.assertLessEqual(abs(events[-1]['start'] - notes[-1].start), .05)
        validate_arrangement(plan)

    def test_impossible_density_is_simplified_with_a_single_clock_and_valid_gaps(self):
        notes = [note(i * .05, 48 + i, duration=.02) for i in range(30)]
        with patch('fluentmelody.core.melody_model.predict_melody', return_value=[.8] * len(notes)):
            plan = arrange(timed_song(notes, ['Melody']))
        events = plan['tracks'][0]['events']
        self.assertTrue(4 <= len(events) < len(notes))
        self.assertGreaterEqual(events[-1]['midi'] - events[0]['midi'], 24)
        # A uniform slowdown is allowed. Every local edit must fit the same
        # clock scale within 50 ms, rather than repeatedly delaying later notes.
        low, high = 1., 1. / .85
        for event in events:
            source_time = notes[event['midi'] - 48].start
            if source_time == 0:
                self.assertLessEqual(event['start'], .050001)
            else:
                low = max(low, (event['start'] - .050001) / source_time)
                high = min(high, (event['start'] + .050001) / source_time)
        self.assertLessEqual(low, high)
        for left, right in zip(events, events[1:]):
            self.assertGreaterEqual(right['start'] - left['start'] - left['duration'], .1 - 1e-6)
        validate_arrangement(plan)

    def test_real_midi_preserves_key_release_separately_from_pedal_and_tempo_map(self):
        data = midi_file([
            [(0, b'\xff\x51\x03\x07\xa1\x20'),
             (480, b'\xff\x51\x03\x0b\x71\xb0'), (480, b'\xff\x2f\x00')],
            [(0, b'\xb0\x40\x7f'), (0, b'\x90\x3c\x64'), (240, b'\x80\x3c\x00'),
             (240, b'\x90\x40\x64'), (240, b'\x80\x40\x00'),
             (240, b'\xb0\x40\x00'), (0, b'\xff\x2f\x00')],
        ])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'pedal.mid'
            path.write_bytes(data)
            song = read_music(path)
        copied = source_notes(song)
        self.assertEqual(len(copied), 2)
        for actual, start, gate, duration in zip(copied, (0, .5), (.25, .375), (1.25, .75)):
            self.assertAlmostEqual(actual.start, start)
            self.assertAlmostEqual(actual.gate_duration, gate)
            self.assertAlmostEqual(gate_length(actual), gate)
            self.assertAlmostEqual(actual.duration, duration)
            self.assertLess(actual.gate_duration, actual.duration)
        with patch('fluentmelody.core.melody_model.predict_melody', return_value=[.8, .8]):
            plan = arrange(song, speed=.5)
        validate_arrangement(plan)
        self.assertEqual([n.gate_duration for n in song.timed_notes], [.25, .375],
                         'Converting must not scale or mutate the source note objects.')


if __name__ == '__main__':
    unittest.main()
