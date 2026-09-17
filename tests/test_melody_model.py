from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from fluentmelody.core import melody_model as model


class MelodyModelTests(unittest.TestCase):
    def note(self, start, duration, midi):
        return SimpleNamespace(start=start, duration=duration, midi=midi)

    def test_published_reference_matches_numpy_port(self):
        # Author's MIT repository provides the pretrained Mozart kernels and
        # actual Theano predictions for Gluck, independently of this NumPy port.
        path = Path(__file__).parent / 'fixtures' / 'symbolic_cnn_reference.npz'
        with np.load(path, allow_pickle=False) as source:
            network = model._SymbolicCNN(source['w1'], source['w2'])
            second = source['pianoroll']
            first = np.pad(second[:, :32], ((0, 0), (32, 0)))
            result = (network.predict_window(first)[:, 32:64]
                      + network.predict_window(second)[:, :32]) / 2
            np.testing.assert_allclose(result, source['expected'], atol=2e-5, rtol=1e-4)

    def test_fft_convolution_matches_independent_spatial_sum(self):
        rng = np.random.default_rng(7)
        x = rng.normal(size=(2, 8, 7)).astype(np.float32)
        weights = rng.normal(size=(3, 2, 3, 2)).astype(np.float32)
        network = model._load_model()
        result = network._conv(x, weights)
        expected = np.zeros((3, 6, 6))
        for output in range(3):
            for row in range(6):
                for col in range(6):
                    expected[output, row, col] = np.sum(
                        x[:, row:row + 3, col:col + 2] * weights[output, :, ::-1, ::-1])
        np.testing.assert_allclose(result, expected, atol=3e-6)

    def test_probabilities_are_finite_ordered_and_do_not_edit_notes(self):
        notes = [self.note(1.0, .5, 72), self.note(0.0, .5, 48), self.note(.5, .5, 74)]
        before = [vars(note).copy() for note in notes]
        values = model.predict_melody(notes)
        reordered = model.predict_melody([notes[2], notes[0], notes[1]])
        self.assertEqual(len(values), 3)
        self.assertTrue(all(0 <= value <= 1 and np.isfinite(value) for value in values))
        np.testing.assert_allclose(reordered, [values[2], values[0], values[1]])
        self.assertEqual([vars(note) for note in notes], before)
        self.assertEqual(model.predict_melody([]), [])

    def test_validation_and_cancellation_are_explicit(self):
        for note in (self.note(float('nan'), .5, 60), self.note(-1, .5, 60),
                     self.note(0, .5, 128), self.note(0, .5, 60.5),
                     self.note(0, 1801, 60)):
            with self.subTest(note=note), self.assertRaises(model.MelodyModelError):
                model.predict_melody([note])
        with self.assertRaises(model.MelodyModelError):
            model.predict_melody([self.note(0, 1, 60)], seconds_per_beat=0)
        with self.assertRaisesRegex(model.MelodyModelError, '取消'):
            model.predict_melody([self.note(0, 1, 60)], cancelled=lambda: True)

    def test_physical_gate_replaces_sustain_decay_for_inference_only(self):
        source = [self.note(0, .2, 60), self.note(.25, .2, 64)]
        pedal = [SimpleNamespace(start=n.start, duration=2.0, midi=n.midi,
                                 gate_duration=n.duration) for n in source]
        expected = model.predict_melody(source)
        self.assertEqual(model.predict_melody(pedal), expected)
        self.assertTrue(all(note.duration == 2.0 for note in pedal))

    def test_parallel_requests_share_model_and_return_same_values(self):
        notes = [self.note(index * .25, .2, 60 + index % 8) for index in range(20)]
        progress = []
        expected = model.predict_melody(notes, progress=progress.append)
        self.assertEqual(progress[-1], 1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(model.predict_melody, [notes, notes]))
        self.assertEqual(results, [expected, expected])

    def test_corrupt_model_is_rejected_without_loading_pickle(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'model.npz'
            path.write_bytes(b'not a model')
            model._load_model.cache_clear()
            try:
                with patch.object(model, 'MODEL_PATH', path), self.assertRaisesRegex(model.MelodyModelError, '校验失败'):
                    model.predict_melody([self.note(0, 1, 60)])
            finally:
                model._load_model.cache_clear()


if __name__ == '__main__':
    unittest.main()
