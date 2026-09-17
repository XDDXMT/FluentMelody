"""The main-page conversion preference controls actual local model use."""
import builtins
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fluentmelody.core.music import (
    TimedNote, arrange, read_music, validate_arrangement, write_arrangement,
)
from fluentmelody.core.nbs_engine import Layer, Song


def sample_song(dense=False):
    song = Song('可选 AI 测试', 5, 10, 0,
                [Layer('Melody'), Layer('Bass')], [], 16, [], [])
    interval = .075 if dense else .5
    song.timed_notes = [
        TimedNote(i * interval, .8, pitch, layer)
        for i in range(16)
        for layer, pitch in ((0, 87 + i % 5), (1, 31 + i % 3))
    ]
    song.source_format = 'midi'
    song.seconds_per_beat = .5
    return song


class OptionalAITests(unittest.TestCase):
    def test_disabled_does_not_import_or_call_model(self):
        original_import = builtins.__import__

        def guard_import(name, *args, **kwargs):
            if name.endswith('melody_model'):
                raise AssertionError('Disabled AI must not import its model.')
            return original_import(name, *args, **kwargs)

        with patch('fluentmelody.core.melody_model.predict_melody') as predict:
            with patch('builtins.__import__', side_effect=guard_import):
                plan = arrange(sample_song(), use_ai=False)
            predict.assert_not_called()
        self.assertTrue(any('AI 自动改编已关闭' in text for text in plan['warnings']))
        self.assertFalse(any('暂不可用' in text for text in plan['warnings']))
        self.assertEqual(len(plan['tracks'][0]['events']), 16)

    def test_default_and_explicit_enabled_use_model(self):
        for kwargs in ({}, {'use_ai': True}):
            with self.subTest(kwargs=kwargs):
                with patch('fluentmelody.core.melody_model.predict_melody',
                           side_effect=lambda notes, **kw: [.8] * len(notes)) as predict:
                    plan = arrange(sample_song(), **kwargs)
                predict.assert_called_once()
                self.assertEqual(predict.call_args.kwargs['seconds_per_beat'], .5)
                self.assertTrue(any('已使用本地旋律识别模型' in text for text in plan['warnings']))
                self.assertFalse(any('已关闭' in text for text in plan['warnings']))

    def test_disabled_keeps_dense_polyphony_playable_and_selected_track(self):
        for players in (1, 2):
            with self.subTest(players=players):
                plan = arrange(sample_song(dense=True), players=players,
                               selected=[0], speed=1.2, gap=.12, use_ai=False)
                self.assertEqual(validate_arrangement(plan), plan)
                self.assertEqual(len(plan['tracks']), players)
                self.assertLess(sum(len(t['events']) for t in plan['tracks']), 16)
                for track in plan['tracks']:
                    for event in track['events']:
                        self.assertEqual(event['layer'], 0)
                        self.assertTrue(48 <= event['midi'] <= 85)
                        self.assertTrue(0 <= event['key_index'] <= 7)
                        self.assertFalse({'left', 'right'} <= set(event['modifiers']))
                    for a, b in zip(track['events'], track['events'][1:]):
                        self.assertGreaterEqual(b['start'] - a['start'] - a['duration'], .12 - 1e-6)

    def test_model_failure_is_distinct_from_user_disabled(self):
        with patch('fluentmelody.core.melody_model.predict_melody',
                   side_effect=OSError('模型文件不可读')):
            fallback = arrange(sample_song())
        ordinary = arrange(sample_song(), use_ai=False)
        self.assertEqual(fallback['tracks'], ordinary['tracks'])
        self.assertTrue(any('暂不可用' in text for text in fallback['warnings']))
        self.assertFalse(any('已关闭' in text for text in fallback['warnings']))

    def test_existing_arrangement_preserved_in_both_modes(self):
        original = arrange(sample_song(), use_ai=False)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '已转换.nbs'
            write_arrangement(original, path)
            song = read_music(path)
        with patch('fluentmelody.core.melody_model.predict_melody') as predict:
            enabled = arrange(song)
            disabled = arrange(song, use_ai=False)
            predict.assert_not_called()
        self.assertEqual(enabled, disabled)
        self.assertEqual(original['tracks'], disabled['tracks'])
        self.assertIn('读取已有演奏编排', disabled['summary'])

    def test_invalid_preference_rejected(self):
        for value in (None, 0, 1, 'false', [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                arrange(sample_song(), use_ai=value)


if __name__ == '__main__':
    unittest.main()
