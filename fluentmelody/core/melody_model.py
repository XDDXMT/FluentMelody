"""Offline note salience from the published ISMIR 2019 symbolic melody CNN.

The original learned kernels are used unchanged; only inference is ported from
Lasagne/Theano to NumPy. See assets/models/MODEL_CARD.md for provenance and limits.
This module never downloads models, executes pickle, or chooses playback notes.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import io
import math
from pathlib import Path
import threading

import numpy as np


MODEL_NAME = '符号旋律 CNN · POP（ISMIR 2019）'
MODEL_ID = 'simonetta2019-pop-numpy-v1'
MODEL_PATH = Path(__file__).resolve().parents[2] / 'assets' / 'models' / 'symbolic_melody_pop.npz'
MODEL_SHA256 = '82b2ec1984c5dfb91248c37caff503993dc188665d7bab0a8ec0e27f55664604'
MAX_NOTES = 50_000
MAX_DURATION = 1800.0
MAX_FRAMES = 120_000
_LOCK = threading.RLock()


class MelodyModelError(RuntimeError):
    pass


def _sigmoid(value):
    return 1.0 / (1.0 + np.exp(-np.clip(value, -60.0, 60.0)))


class _SymbolicCNN:
    def __init__(self, w1, w2):
        self.w1 = np.asarray(w1, dtype=np.float32)
        self.w2 = np.asarray(w2, dtype=np.float32)
        if self.w1.shape != (21, 1, 32, 16) or self.w2.shape != (21, 21, 32, 16):
            raise MelodyModelError('本地旋律模型的参数尺寸不正确。')
        if not np.isfinite(self.w1).all() or not np.isfinite(self.w2).all():
            raise MelodyModelError('本地旋律模型包含无效参数。')
        self._spectra = {}

    def _conv(self, x, kernel, *, full=False, cache_key=None):
        """Channelwise mathematical convolution (Lasagne flip_filters=True)."""
        height = x.shape[-2] + kernel.shape[-2] - 1
        width = x.shape[-1] + kernel.shape[-1] - 1
        fft_shape = ((height + 31) // 32 * 32, (width + 15) // 16 * 16)
        key = (cache_key, fft_shape)
        spectrum = self._spectra.get(key) if cache_key is not None else None
        if spectrum is None:
            spectrum = np.fft.rfft2(kernel, s=fft_shape, axes=(-2, -1))
            if cache_key is not None:
                self._spectra[key] = spectrum
        transformed = np.fft.rfft2(x, s=fft_shape, axes=(-2, -1))
        product = np.einsum('ochw,chw->ohw', spectrum, transformed)
        result = np.fft.irfft2(product, s=fft_shape, axes=(-2, -1))
        if full:
            return result[:, :height, :width].astype(np.float32)
        return result[:, kernel.shape[-2] - 1:x.shape[-2],
                      kernel.shape[-1] - 1:x.shape[-1]].astype(np.float32)

    def predict_window(self, roll):
        if roll.shape != (128, 64):
            raise ValueError('模型窗口需为 128 × 64。')
        first = self._conv(roll[np.newaxis], self.w1, cache_key='forward1')
        second = _sigmoid(self._conv(first, self.w2, cache_key='forward2'))
        # Lasagne InverseLayer differentiates the layer, including its sigmoid.
        # Treating it as an ordinary transposed convolution changes the model.
        gradient = second * second * (1.0 - second)
        inverse2 = self._conv(gradient, self.w2.transpose(1, 0, 2, 3)[:, :, ::-1, ::-1],
                              full=True, cache_key='inverse2')
        inverse1 = self._conv(inverse2, self.w1.transpose(1, 0, 2, 3)[:, :, ::-1, ::-1],
                              full=True, cache_key='inverse1')[0]
        return _sigmoid(inverse1) * roll


@lru_cache(maxsize=1)
def _load_model():
    try:
        data = MODEL_PATH.read_bytes()
        if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
            raise MelodyModelError('本地旋律模型校验失败，请重新解压完整程序。')
        with np.load(io.BytesIO(data), allow_pickle=False) as weights:
            return _SymbolicCNN(weights['w1'], weights['w2'])
    except MelodyModelError:
        raise
    except (OSError, ValueError, KeyError) as exc:
        raise MelodyModelError(f'本地旋律模型无法读取：{exc}') from exc


def predict_melody(notes, seconds_per_beat=0.5, *, progress=None, cancelled=None):
    """Return one learned melody probability per input note, in original order.

    Notes expose start (seconds), duration (seconds), and midi (0..127). Optional
    gate_duration uses key-release time instead of sustain-pedal decay. Tempo
    defaults to 120 BPM; callers should pass a representative beat duration when
    known. Probabilities help ranking; they are not calibrated musical certainty.
    progress, when provided, receives a 0..1 fraction. cancelled returns a bool.
    Inference is serialized so concurrent conversion jobs share one bounded cache.
    """
    notes = list(notes)
    if not notes:
        return []
    if len(notes) > MAX_NOTES:
        raise MelodyModelError('本地旋律模型一次最多处理 50000 个音符。')
    if (isinstance(seconds_per_beat, bool) or not isinstance(seconds_per_beat, (int, float))
            or not math.isfinite(seconds_per_beat) or not 0.125 <= seconds_per_beat <= 4.0):
        raise MelodyModelError('本地旋律模型的每拍时长需在 0.125～4 秒之间。')
    values = []
    for note in notes:
        try:
            gate = getattr(note, 'gate_duration', None)
            start = float(note.start)
            duration = float(note.duration if gate is None else gate)
            pitch = int(note.midi)
        except (AttributeError, TypeError, ValueError, OverflowError) as exc:
            raise MelodyModelError('音符缺少有效的时间或音高。') from exc
        if (not math.isfinite(start) or not math.isfinite(duration)
                or start < 0 or duration < 0 or start + duration > MAX_DURATION
                or not 0 <= pitch <= 127 or pitch != note.midi):
            raise MelodyModelError('音符超出本地旋律模型支持的范围。')
        values.append((start, duration, pitch))
    origin = min(start for start, _, _ in values)
    scale = 8.0 / seconds_per_beat
    indices = []
    for start, duration, pitch in values:
        begin = round((start - origin) * scale)
        # Match the published input encoder's one-cell release separation.
        end = max(begin + 1, round((start + duration - origin) * scale) - 1)
        indices.append((pitch, begin, end))
    length = max(end for _, _, end in indices)
    if length > MAX_FRAMES:
        raise MelodyModelError('歌曲超出本地旋律模型的时间窗口上限。')
    padded_length = ((length + 31) // 32) * 32 + 64
    roll = np.zeros((128, padded_length), dtype=np.float32)
    for pitch, begin, end in indices:
        roll[pitch, begin + 32:end + 32] = 1
    output = np.zeros_like(roll)
    windows = list(range(0, padded_length - 63, 32))
    with _LOCK:
        model = _load_model()
        for index, at in enumerate(windows):
            if cancelled is not None and cancelled():
                raise MelodyModelError('本地旋律识别已取消。')
            window = roll[:, at:at + 64]
            if window.any():
                output[:, at:at + 64] += model.predict_window(window) * 0.5
            if progress is not None:
                progress((index + 1) / len(windows))
    return [float(np.clip(np.median(output[pitch, begin + 32:end + 32]), 0, 1))
            for pitch, begin, end in indices]
