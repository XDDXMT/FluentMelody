"""Game-confirmed chromatic range; all playback elsewhere uses fake input."""
import copy
from pathlib import Path
import struct
import tempfile
import unittest

from fluentmelody.core.music import arrange, read_music, score_to_arrangement, validate_arrangement, write_arrangement
from fluentmelody.core.nbs_engine import Layer, Note, Song, convert, convert_legacy
from fluentmelody.servers.ai_music import apply_directives


def song_for(pitches):
    return Song('半音音域', 5, 10, len(pitches) * 5, [Layer('Melody')],
                [Note(i * 5, 0, 0, pitch - 21) for i, pitch in enumerate(pitches)], 16, [], [])


def score_for(*parts):
    return {'name': '半音音域', 'tracks': [
        {'name': str(i), 'notes': [{'start': j * .5, 'duration': .2, 'midi': pitch}
                                 for j, pitch in enumerate(pitches)]}
        for i, pitches in enumerate(parts)]}


class PitchRangeTests(unittest.TestCase):
    def test_every_playable_pitch_retains_its_register(self):
        # Individual pitches catch the old median-based octave shift at both edges.
        for pitch in range(48, 86):
            with self.subTest(pitch=pitch):
                plan = score_to_arrangement(score_for([pitch]))
                self.assertEqual(plan['tracks'][0]['events'][0]['midi'], pitch)
        pitches = list(range(48, 86))
        self.assertEqual([e['midi'] for e in arrange(song_for(pitches))['tracks'][0]['events']], pitches)

    def test_equivalent_notes_use_the_fewest_mouse_buttons(self):
        pitches = [48, 49, 53, 60, 61, 65, 72, 73, 77, 84, 85]
        plan = score_to_arrangement(score_for(pitches))
        actual = {e['midi']: (e['key_index'], e['modifiers']) for e in plan['tracks'][0]['events']}
        self.assertEqual(actual, {
            48: (0, ['left']), 49: (0, ['left', 'middle']), 53: (3, ['left']),
            60: (0, []), 61: (0, ['middle']), 65: (3, []), 72: (7, []),
            73: (7, ['middle']), 77: (3, ['right']), 84: (7, ['right']),
            85: (7, ['right', 'middle']),
        })

    def test_legacy_and_smart_nbs_converters_enable_combinations_by_default(self):
        pitches = [48, 49, 51, 73, 78, 84, 85]
        for converter in (convert, convert_legacy):
            with self.subTest(converter=converter.__name__):
                plan = converter(song_for(pitches))
                self.assertEqual([e.midi for e in plan.events], pitches)
                self.assertEqual(plan.events[1].modifiers, (2, 32))
                self.assertEqual(plan.events[-1].modifiers, (8, 32))

    def test_nbs_roundtrip_retains_extreme_registers_in_separate_parts(self):
        parts = ([48, 49, 51, 58, 59, 60, 61], [72, 73, 78, 82, 84, 85])
        plan = score_to_arrangement(score_for(*parts), players=2)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'range.nbs'
            write_arrangement(plan, path)
            restored = arrange(read_music(path), players=2)
        for expected, track in zip(parts, restored['tracks']):
            self.assertEqual([e['midi'] for e in track['events']], expected)
            for earlier, later in zip(track['events'], track['events'][1:]):
                self.assertGreaterEqual(later['start'] - earlier['start'] - earlier['duration'], .1 - 1e-6)

    def test_real_midi_loading_preserves_both_chromatic_edges(self):
        pitches = [48, 49, 85]
        track = bytearray()
        for index, pitch in enumerate(pitches):
            track.extend(b'\x00' if not index else b'\x81\x70')
            track.extend(bytes((0x90, pitch, 100)))
            track.extend(b'\x81\x70' + bytes((0x80, pitch, 0)))
        track.extend(b'\x00\xff\x2f\x00')
        data = (b'MThd' + struct.pack('>IHHH', 6, 0, 1, 480) +
                b'MTrk' + struct.pack('>I', len(track)) + track)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'range.mid'
            path.write_bytes(data)
            plan = arrange(read_music(path))
        self.assertEqual([e['midi'] for e in plan['tracks'][0]['events']], pitches)

    def test_server_ai_directives_accept_both_octave_semitone_combinations(self):
        pitches = [48, 49, 60, 61, 73, 84, 85]
        directives = {'speed': 1, 'parts': [{'role': 'melody', 'layer_ids': [0],
            'transpose': 0, 'stride': 1, 'selection': 'top', 'sections': [], 'notes': []}]}
        plan = apply_directives(song_for(pitches), directives, players=1)
        events = validate_arrangement(plan, players=1)['tracks'][0]['events']
        self.assertEqual([e['midi'] for e in events], pitches)
        self.assertEqual(events[1]['modifiers'], ['left', 'middle'])
        self.assertEqual(events[-1]['modifiers'], ['right', 'middle'])

    def test_outside_range_folds_by_octave_without_changing_pitch_class(self):
        for pitch in (0, 47, 86, 127):
            with self.subTest(pitch=pitch):
                actual = score_to_arrangement(score_for([pitch]))['tracks'][0]['events'][0]['midi']
                self.assertTrue(48 <= actual <= 85)
                self.assertEqual(actual % 12, pitch % 12)

    def test_combinations_do_not_relax_invalid_keys_or_release_gaps(self):
        plan = score_to_arrangement(score_for([49, 85]))
        mutations = [lambda p: p['tracks'][0]['events'][0].update(modifiers=['left', 'right']),
                     lambda p: p['tracks'][0]['events'][0].update(modifiers=['left', 'middle', 'right']),
                     lambda p: p['tracks'][0]['events'][0].update(modifiers=['middle', 'middle']),
                     lambda p: p['tracks'][0]['events'][0].update(key_index=[0, 1]),
                     lambda p: p['tracks'][0]['events'][0].update(midi=48),
                     lambda p: p['tracks'][0]['events'][1].update(start=.29)]
        for mutate in mutations:
            bad = copy.deepcopy(plan)
            mutate(bad)
            with self.assertRaises(ValueError):
                validate_arrangement(bad)


if __name__ == '__main__':
    unittest.main()
