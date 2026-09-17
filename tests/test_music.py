import copy
import json
from pathlib import Path
import struct
import tempfile
import unittest
import wave

from fluentmelody.core.music import (arrange, read_music, validate_arrangement,
    write_arrangement, render_preview, score_to_arrangement, source_score)
from fluentmelody.core.nbs_engine import Song, Layer, Note


def vlq(value):
    out = [value & 127]
    while value >> 7:
        value >>= 7
        out.insert(0, (value & 127) | 128)
    return bytes(out)


def midi_bytes(tracks, division=480):
    result = b'MThd' + struct.pack('>IHHH', 6, 1, len(tracks), division)
    for events in tracks:
        data = b''.join(vlq(delta) + message for delta, message in events)
        result += b'MTrk' + struct.pack('>I', len(data)) + data
    return result


def example_song():
    notes = []
    for tick, pitch in enumerate([60, 62, 64, 65, 67, 69, 71, 72]):
        notes.extend([Note(tick * 5, 0, 0, pitch - 21),
                      Note(tick * 5, 1, 1, 48 - 21),
                      Note(tick * 5, 2, 0, 55 - 21)])
    return Song('测试', 5, 10, 40, [Layer('Melody'), Layer('Bass'), Layer('Harmony')], notes, 16, [], [])


class MusicTests(unittest.TestCase):
    def test_single_and_three_voices_no_overlap(self):
        for count in (1, 2, 3, 8):
            plan = arrange(example_song(), count)
            self.assertEqual(len(plan['tracks']), count)
            self.assertEqual(len(plan['tracks'][0]['events']), 8)
            validate_arrangement(plan, count)
        self.assertEqual(sum(len(t['events']) for t in arrange(example_song(), 3)['tracks']), 24)

    def test_nbs_roundtrip_preserves_three_parts_and_sustain(self):
        plan = arrange(example_song(), 3)
        plan['tracks'][0]['events'][-1]['duration'] = 1.25
        plan['duration'] = 4.85
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'roundtrip.nbs'
            write_arrangement(plan, path)
            reloaded = read_music(path)
            self.assertTrue(reloaded.arranged)
            second = arrange(reloaded, 3)
            self.assertEqual([len(t['events']) for t in plan['tracks']], [len(t['events']) for t in second['tracks']])
            self.assertEqual(plan['tracks'][0]['events'][-1]['duration'], second['tracks'][0]['events'][-1]['duration'])
            for before, after in zip(plan['tracks'][0]['events'], second['tracks'][0]['events']):
                self.assertAlmostEqual(before['start'], after['start'], places=5)

    def test_midi_tempo_map_and_sustain(self):
        data = midi_bytes([
            [(0, b'\xff\x51\x03\x07\xa1\x20'), (480, b'\xff\x51\x03\x0f\x42\x40'), (480, b'\xff\x2f\x00')],
            [(0, b'\xb0\x40\x7f'), (0, b'\x90\x3c\x64'), (240, b'\x80\x3c\x00'),
             (480, b'\xb0\x40\x00'), (0, b'\x90\x3e\x64'), (240, b'\x80\x3e\x00'), (0, b'\xff\x2f\x00')]
        ])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'tempo.mid'
            path.write_bytes(data)
            song = read_music(path)
        self.assertAlmostEqual(song.timed_notes[0].duration, 1.0)
        self.assertAlmostEqual(song.timed_notes[1].start, 1.0)
        self.assertAlmostEqual(song.timed_notes[1].duration, .5)
        plan = arrange(song)
        self.assertAlmostEqual(plan['tracks'][0]['events'][0]['duration'], .885)
        self.assertAlmostEqual(plan['tracks'][0]['events'][1]['duration'], .5)
        self.assertEqual(len(source_score(song)['tracks'][0]['notes']), 2)

    def test_running_status_and_drums(self):
        data = midi_bytes([[(0, b'\x90\x3c\x64'), (480, b'\x3c\x00'),
                           (0, b'\x99\x24\x64'), (480, b'\x89\x24\x00'), (0, b'\xff\x2f\x00')]])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'running.mid'
            path.write_bytes(data)
            song = read_music(path)
        self.assertEqual(len(song.notes), 1)
        self.assertEqual(song.timed_notes[0].midi, 60)

    def test_instrumental_intro_and_outro_survive_named_vocal(self):
        song = example_song()
        song.notes = [Note(0, 1, 0, 64 - 21), Note(5, 1, 0, 67 - 21),
                      Note(10, 0, 0, 72 - 21), Note(15, 0, 0, 71 - 21),
                      Note(30, 1, 0, 69 - 21), Note(35, 1, 0, 67 - 21)]
        plan = arrange(song)
        self.assertEqual(len(plan['tracks'][0]['events']), 6)
        self.assertEqual(plan['tracks'][0]['events'][0]['start'], 0)
        self.assertEqual(plan['tracks'][0]['events'][-1]['start'], 3.5)

    def test_ai_score_clips_and_keeps_timeline(self):
        score = {'name': 'test', 'tracks': [{'name': 'melody', 'notes': [
            {'start': 0, 'duration': 2, 'midi': 60},
            {'start': .05, 'duration': .1, 'midi': 61},
            {'start': 1, 'duration': .5, 'midi': 64}]}]}
        plan = score_to_arrangement(score, 1)
        self.assertEqual([e['start'] for e in plan['tracks'][0]['events']], [0, 1])
        self.assertAlmostEqual(plan['tracks'][0]['events'][0]['duration'], .885)

    def test_invalid_plans_rejected(self):
        original = arrange(example_song())
        mutations = [lambda p: p['tracks'][0]['events'][0].update(start=float('nan')),
                     lambda p: p['tracks'][0]['events'][0].update(modifiers=['left', 'right']),
                     lambda p: p['tracks'][0]['events'][0].update(key_index=True),
                     lambda p: p['tracks'][0]['events'][0].update(midi=1),
                     lambda p: p['tracks'][0]['events'][1].update(start=.01),
                     lambda p: p.update(duration=1801)]
        for mutate in mutations:
            plan = copy.deepcopy(original)
            mutate(plan)
            with self.assertRaises(ValueError):
                validate_arrangement(plan)

    def test_preview_has_audible_pcm(self):
        plan = score_to_arrangement({'tracks': [{'notes': [{'start': 0, 'duration': .2, 'midi': 60}]}]})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'preview.wav'
            render_preview(plan, path, sample_rate=8000)
            with wave.open(str(path)) as handle:
                self.assertEqual(handle.getframerate(), 8000)
                self.assertGreater(handle.getnframes(), 2000)
                self.assertNotEqual(set(handle.readframes(1000)), {0})

    def test_reject_truncated_midi(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bad.mid'
            path.write_bytes(b'MThd' + bytes(10))
            with self.assertRaises(ValueError):
                read_music(path)


if __name__ == '__main__':
    unittest.main()
