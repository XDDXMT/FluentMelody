"""One input-owning worker and interruptible scheduled playback."""
from __future__ import annotations

from bisect import bisect_right
import ctypes
from ctypes import wintypes
import math
import os
import threading
import time

from .hotkeys import Hotkeys

SCANS = (0x2c, 0x2d, 0x2e, 0x2f, 0x30, 0x31, 0x32, 0x33)
MOUSE_FLAGS = {'left': 2, 'right': 8, 'middle': 32}


class WindowsInput:
    def __init__(self):
        if os.name != 'nt':
            raise RuntimeError('真实键鼠演奏仅支持 Windows。')
        self.api = ctypes.WinDLL('user32', use_last_error=True)
        ulong_ptr = ctypes.c_size_t

        class Mouse(ctypes.Structure):
            _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG),
                        ('mouseData', wintypes.DWORD), ('dwFlags', wintypes.DWORD),
                        ('time', wintypes.DWORD), ('dwExtraInfo', ulong_ptr)]

        class Keyboard(ctypes.Structure):
            _fields_ = [('wVk', wintypes.WORD), ('wScan', wintypes.WORD),
                        ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD), ('dwExtraInfo', ulong_ptr)]

        class Hardware(ctypes.Structure):
            _fields_ = [('uMsg', wintypes.DWORD), ('wParamL', wintypes.WORD), ('wParamH', wintypes.WORD)]

        class Union(ctypes.Union):
            _fields_ = [('mi', Mouse), ('ki', Keyboard), ('hi', Hardware)]

        class Input(ctypes.Structure):
            _anonymous_ = ('u',)
            _fields_ = [('type', wintypes.DWORD), ('u', Union)]

        self.Mouse, self.Keyboard, self.Input = Mouse, Keyboard, Input
        self.api.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int)
        self.api.SendInput.restype = wintypes.UINT

    def _send(self, event):
        if self.api.SendInput(1, ctypes.byref(event), ctypes.sizeof(self.Input)) != 1:
            raise RuntimeError('系统未接受模拟输入，请使用管理员入口并检查游戏权限。')

    def key(self, index, up=False):
        self._send(self.Input(type=1, ki=self.Keyboard(0, SCANS[index], 8 | (2 if up else 0), 0, 0)))

    def mouse(self, name, up=False):
        self._send(self.Input(type=0, mi=self.Mouse(0, 0, 0, MOUSE_FLAGS[name] * (2 if up else 1), 0, 0)))


class NullInput:
    def key(self, index, up=False):
        pass

    def mouse(self, name, up=False):
        pass


class Player:
    """start_at uses UTC epoch seconds; position uses the song's seconds.

    Callback accepts one human-readable string and runs on the worker thread.
    The caller must marshal it to its UI thread. All native input belongs to
    this single worker; stop() joins it before another worker can start.
    """
    def __init__(self, on_status=None, dry_run=False, backend=None):
        self.on_status = on_status or (lambda message: None)
        self.dry_run = dry_run
        self._backend = backend
        self._lock = threading.RLock()
        self._wakeup = threading.Event()
        self._stop_event = threading.Event()
        self._thread = None
        self._state = 'stopped'
        self._paused = False
        self._position = 0.0
        self._origin = 0.0
        self._start_gate = 0.0
        self._duration = 0.0
        self._active_key = None
        self._active_mouse = []
        self._last_release = -math.inf
        self.error = None

    @property
    def state(self):
        with self._lock:
            return self._state

    @property
    def position(self):
        with self._lock:
            if self._state in ('playing', 'countdown') and not self._paused:
                if time.monotonic() < self._start_gate:
                    return self._position
                return max(0.0, min(self._duration, time.monotonic() - self._origin))
            return self._position

    def _notify(self, message):
        try:
            self.on_status(message)
        except Exception:
            # A GUI teardown must never prevent the native key-up finally block.
            pass

    @staticmethod
    def _number(value, label):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f'{label}必须是有限数字。')
        return float(value)

    def play(self, events, start_at=None, position=0):
        from .music import validate_arrangement
        if self._thread is threading.current_thread():
            raise RuntimeError('请将新的演奏请求发送到界面线程，不能从演奏回调内启动。')
        # A malformed replacement must not leave an earlier song pressing keys.
        self.stop()
        position = self._number(position, '播放位置')
        if position < 0:
            raise ValueError('播放位置不能为负数。')
        if start_at is not None:
            start_at = self._number(start_at, '开始时间')
        if not isinstance(events, (list, tuple)):
            raise ValueError('演奏事件必须是列表。')
        if events:
            normalized = validate_arrangement({'name': '演奏', 'tracks': [{'name': '本机', 'events': list(events)}]})
            events = normalized['tracks'][0]['events']
        if self._backend is None:
            self._backend = NullInput() if self.dry_run else WindowsInput()
        with self._lock:
            self.error = None
            self._stop_event.clear()
            self._wakeup.clear()
            self._paused = False
            self._duration = max((e['start'] + e['duration'] for e in events), default=0)
            self._position = min(position, self._duration)
            delay = (start_at - time.time()) if start_at is not None else 0.0
            self._origin = time.monotonic() + delay - position
            self._start_gate = time.monotonic() + delay
            self._state = 'countdown' if delay > 0 else 'playing'
            self._thread = threading.Thread(target=self._run, args=(events,), daemon=True, name='FluentMelody-input')
            self._thread.start()

    def pause(self):
        with self._lock:
            if self._state not in ('playing', 'countdown'):
                return
            self._position = self.position
            self._paused = True
            self._state = 'paused'
        self._wakeup.set()
        self._notify('已暂停，松开演奏按键。')

    def resume(self, start_at=None):
        if start_at is not None:
            start_at = self._number(start_at, '继续时间')
        with self._lock:
            if self._state != 'paused':
                return
            delay = (start_at - time.time()) if start_at is not None else 0.0
            self._origin = time.monotonic() + delay - self._position
            self._start_gate = time.monotonic() + delay
            self._paused = False
            self._state = 'countdown' if delay > 0 else 'playing'
        self._wakeup.set()
        self._notify('准备继续演奏。')

    def stop(self):
        self._stop_event.set()
        self._wakeup.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3)
            if thread.is_alive():
                raise RuntimeError('演奏线程未退出，已禁止启动新演奏以避免按键冲突。')
        with self._lock:
            self._thread = None
            self._paused = False
            if self._state != 'error':
                self._state = 'stopped'
            self._position = 0.0

    def _release(self):
        errors, released = [], False
        if self._active_key is not None:
            try:
                self._backend.key(self._active_key, up=True)
                self._active_key = None
                released = True
            except Exception as exc:
                errors.append(exc)
        for modifier in reversed(tuple(self._active_mouse)):
            try:
                self._backend.mouse(modifier, up=True)
                self._active_mouse.remove(modifier)
                released = True
            except Exception as exc:
                errors.append(exc)
        if released:
            self._last_release = time.monotonic()
        if errors:
            raise errors[0]

    def _wait(self, seconds=.005):
        self._wakeup.wait(max(.001, min(.01, seconds)))
        self._wakeup.clear()

    def _run(self, events):
        starts = [e['start'] for e in events]
        active_index = None
        self._notify('试运行已开始（不发送按键）。' if self.dry_run else '演奏已启动。')
        try:
            while not self._stop_event.is_set():
                with self._lock:
                    paused, origin, start_gate = self._paused, self._origin, self._start_gate
                    position = max(0, time.monotonic() - origin)
                if paused:
                    self._release()
                    active_index = None
                    self._wait()
                    continue
                if time.monotonic() < start_gate:
                    self._release()
                    active_index = None
                    self._wait()
                    continue
                with self._lock:
                    if self._state == 'countdown':
                        self._state = 'playing'
                if position >= self._duration:
                    break
                index = bisect_right(starts, position) - 1
                event = events[index] if index >= 0 else None
                desired = index if event and position < event['start'] + event['duration'] else None
                if active_index != desired:
                    self._release()
                    active_index = None
                if desired is not None and active_index is None:
                    if time.monotonic() - self._last_release < .1:
                        self._wait()
                        continue
                    for modifier in event['modifiers']:
                        self._backend.mouse(modifier)
                        self._active_mouse.append(modifier)
                    # Give the game one frame to see its octave/semitone mode.
                    if event['modifiers']:
                        deadline = time.monotonic() + .015
                        while time.monotonic() < deadline and not self._stop_event.is_set():
                            with self._lock:
                                if self._paused:
                                    break
                            self._wait(.003)
                    with self._lock:
                        if self._paused or self._stop_event.is_set():
                            self._release()
                            continue
                    if time.monotonic() - self._origin >= event['start'] + event['duration']:
                        self._release()
                        continue
                    self._backend.key(event['key_index'])
                    self._active_key = event['key_index']
                    active_index = desired
                self._wait()
            with self._lock:
                if not self._stop_event.is_set():
                    self._state = 'finished'
                    self._position = self._duration
            if not self._stop_event.is_set():
                self._notify('演奏完成。')
        except Exception as exc:
            with self._lock:
                self.error = str(exc)
                self._state = 'error'
            self._notify(f'演奏中断：{exc}')
        finally:
            for _ in range(3):
                try:
                    self._release()
                    break
                except Exception as exc:
                    self.error = str(exc)
                    self._state = 'error'
            if self._active_key is not None or self._active_mouse:
                self._notify('系统拒绝松键，请手动松开 Z～逗号及鼠标键。')
