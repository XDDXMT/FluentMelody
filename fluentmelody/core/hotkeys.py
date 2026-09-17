"""Validated, editable Windows hotkeys owned by one message-loop thread."""
from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import os
import queue
import threading


DEFAULT_BINDINGS = {1: 'F1', 4: 'F4'}
_MODIFIERS = {'Ctrl': 0x0002, 'Alt': 0x0001, 'Shift': 0x0004}
_MODIFIER_KEYS = {0x0002: 0x11, 0x0001: 0x12, 0x0004: 0x10}
_KEYS = {
    'Esc': 0x1B, 'Tab': 0x09, 'Space': 0x20, 'Backspace': 0x08,
    'Insert': 0x2D, 'Delete': 0x2E, 'Home': 0x24, 'End': 0x23,
    'PageUp': 0x21, 'PageDown': 0x22, 'Left': 0x25, 'Up': 0x26,
    'Right': 0x27, 'Down': 0x28, 'Pause': 0x13, 'ScrollLock': 0x91,
}
_ALIASES = {name.casefold(): name for name in _KEYS}
_ALIASES.update({'escape': 'Esc', 'pgup': 'PageUp', 'pgdown': 'PageDown',
                 'pgdn': 'PageDown', 'del': 'Delete', 'ins': 'Insert',
                 'scrolllock': 'ScrollLock', 'scroll lock': 'ScrollLock'})
_MOD_ALIASES = {'ctrl': 'Ctrl', 'control': 'Ctrl', 'alt': 'Alt', 'shift': 'Shift'}


@dataclass(frozen=True)
class HotkeySpec:
    text: str
    modifiers: int
    vk: int

    @property
    def keys(self):
        return (self.vk,) + tuple(vk for mask, vk in _MODIFIER_KEYS.items() if self.modifiers & mask)


def parse_hotkey(text):
    """Accept a single portable Qt-style chord, without stealing note keys."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError('快捷键不能为空，请按下一组按键。')
    if len(text) > 80 or ',' in text or '，' in text:
        raise ValueError('请使用一组快捷键；逗号是演奏按键，不能用作快捷键。')
    parts = [part.strip() for part in text.split('+')]
    if any(not part for part in parts):
        raise ValueError('快捷键格式不正确，例如 F6 或 Ctrl+Alt+P。')
    if any(part.casefold() in ('win', 'windows', 'meta', 'super', 'cmd', 'command') for part in parts):
        raise ValueError('不能使用 Windows 键组合，请选择其他快捷键。')
    modifiers = set()
    for part in parts[:-1]:
        modifier = _MOD_ALIASES.get(part.casefold())
        if modifier is None or modifier in modifiers:
            raise ValueError('每组快捷键只能包含一个主键和不重复的 Ctrl、Alt、Shift。')
        modifiers.add(modifier)
    key = parts[-1]
    upper = key.upper()
    if upper in tuple('ZXCVBNM'):
        raise ValueError('Z、X、C、V、B、N、M 和逗号用于演奏，不能用作快捷键主键。')
    if upper.startswith('F') and upper[1:].isascii() and upper[1:].isdigit() and 1 <= int(upper[1:]) <= 24:
        key = f'F{int(upper[1:])}'
        if key == 'F12':
            raise ValueError('F12 由 Windows 保留，请选择其他功能键。')
        vk = 0x6F + int(upper[1:])
    elif len(upper) == 1 and upper.isascii() and upper.isalnum():
        if not modifiers:
            raise ValueError('字母或数字快捷键需搭配 Ctrl、Alt 或 Shift，以免影响正常输入。')
        key, vk = upper, ord(upper)
    else:
        key = _ALIASES.get(key.casefold())
        if key is None:
            raise ValueError('请使用功能键，或 Ctrl、Alt、Shift 搭配字母、数字、方向键。')
        vk = _KEYS[key]
    if (('Alt' in modifiers and key in ('F4', 'Tab', 'Esc', 'Space'))
            or ('Ctrl' in modifiers and key == 'Esc')
            or ('Ctrl' in modifiers and 'Alt' in modifiers and key == 'Delete')):
        raise ValueError('这组按键用于系统操作，请选择其他快捷键。')
    ordered = [name for name in _MODIFIERS if name in modifiers]
    return HotkeySpec('+'.join(ordered + [key]), sum(_MODIFIERS[name] for name in ordered), vk)


def normalize_hotkey(text):
    return parse_hotkey(text).text


def validate_bindings(bindings):
    if not isinstance(bindings, dict) or set(bindings) != set(DEFAULT_BINDINGS):
        raise ValueError('请同时设置“开始／停止／准备”和“暂停／继续”的快捷键。')
    canonical = {action: normalize_hotkey(bindings[action]) for action in DEFAULT_BINDINGS}
    if len(set(canonical.values())) != len(canonical):
        raise ValueError('两个功能不能使用相同的快捷键。')
    return canonical


class _WindowsBackend:
    def __init__(self):
        if os.name != 'nt':
            raise OSError('全局快捷键仅支持 Windows。')
        self.api = ctypes.WinDLL('user32', use_last_error=True)
        self.api.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
        self.api.RegisterHotKey.restype = wintypes.BOOL
        self.api.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
        self.api.UnregisterHotKey.restype = wintypes.BOOL
        self.api.PeekMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                         wintypes.UINT, wintypes.UINT, wintypes.UINT)
        self.api.PeekMessageW.restype = wintypes.BOOL
        self.api.GetAsyncKeyState.argtypes = (ctypes.c_int,)
        self.api.GetAsyncKeyState.restype = ctypes.c_short

    def register(self, action, spec):
        if not self.api.RegisterHotKey(None, action, 0x4000 | spec.modifiers, spec.vk):
            code = ctypes.get_last_error()
            raise OSError(code, f'快捷键 {spec.text} 无法注册，可能已被其他程序占用。')

    def unregister(self, action):
        if not self.api.UnregisterHotKey(None, action):
            raise OSError(ctypes.get_last_error(), '无法释放原有快捷键。')

    def poll(self):
        result = []
        message = wintypes.MSG()
        # Only consume this worker's hotkey messages; no GUI messages belong here.
        while self.api.PeekMessageW(ctypes.byref(message), None, 0x0312, 0x0312, 1):
            result.append(int(message.wParam))
        return result

    def is_pressed(self, vk):
        return bool(self.api.GetAsyncKeyState(vk) & 0x8000)


class Hotkeys:
    """Callbacks receive actions 1 or 4 on the worker thread.

    Mutations wait for the worker's registration result and raise on failure.
    Failed replacements restore previous bindings; an OS conflict is never
    replaced by polling somebody else's reserved key. ``_backend_factory`` is
    an injection point for tests and does not send any simulated input.
    """
    def __init__(self, callback, bindings=None, on_error=None, *, _backend_factory=None):
        self.callback = callback
        self.on_error = on_error
        self.bindings = validate_bindings(DEFAULT_BINDINGS if bindings is None else bindings)
        self.registered = set()
        self.suspended = False
        self.error = None
        self._backend_factory = _backend_factory or _WindowsBackend
        self._backend = None
        self._commands = queue.Queue()
        self._wakeup = threading.Event()
        self._stop_event = threading.Event()
        self._thread = None
        self._armed = {}
        self._release_all = set()
        self._lifecycle = threading.RLock()

    def start(self):
        with self._lifecycle:
            if self._thread and self._thread.is_alive():
                return True
            self._stop_event.clear()
            self._commands = queue.Queue()
            done, result = threading.Event(), []
            self._thread = threading.Thread(target=self._run, args=(done, result),
                                            daemon=True, name='FluentMelody-hotkeys')
            self._thread.start()
            try:
                return self._wait(done, result)
            except Exception:
                self._stop_event.set()
                self._wakeup.set()
                # Registration failure must finish releasing partial bindings
                # before the GUI can retry with replacement settings.
                self._thread.join(timeout=.2)
                raise

    @staticmethod
    def _wait(done, result):
        if not done.wait(1):
            raise RuntimeError('快捷键服务响应超时，请重新启动程序。')
        if result and isinstance(result[0], BaseException):
            raise result[0]
        return True

    def _request(self, kind, value=None):
        with self._lifecycle:
            if not self._thread or not self._thread.is_alive():
                if kind == 'configure':
                    self.bindings = value
                elif kind == 'suspend':
                    self.suspended = True
                elif kind == 'resume':
                    self.suspended = False
                return True
            if threading.current_thread() is self._thread:
                self._handle(kind, value)
                return True
            done, result = threading.Event(), []
            self._commands.put((kind, value, done, result))
            self._wakeup.set()
            return self._wait(done, result)

    def reconfigure(self, bindings):
        return self._request('configure', validate_bindings(bindings))

    def suspend(self):
        return self._request('suspend')

    def resume(self):
        return self._request('resume')

    def stop(self):
        with self._lifecycle:
            self._stop_event.set()
            self._wakeup.set()
            thread = self._thread
            if thread and thread is not threading.current_thread():
                thread.join(timeout=1)
                if thread.is_alive():
                    raise RuntimeError('快捷键服务未能退出，请重新启动程序。')
                self._thread = None

    def _unregister_all(self):
        failures = []
        for action in tuple(self.registered):
            try:
                self._backend.unregister(action)
                self.registered.discard(action)
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise failures[0]

    def _register_all(self, bindings):
        for action, text in bindings.items():
            self._backend.register(action, parse_hotkey(text))
            self.registered.add(action)

    def _reset_edges(self):
        self._backend.poll()
        self._armed = {
            action: not any(self._backend.is_pressed(key) for key in parse_hotkey(text).keys)
            for action, text in self.bindings.items()
        }
        self._release_all = {action for action, armed in self._armed.items() if not armed}

    def _replace(self, bindings, suspended):
        old_bindings, old_suspended = dict(self.bindings), self.suspended
        try:
            self._unregister_all()
            self._backend.poll()
            self._register_all(bindings)
            if suspended:
                self._unregister_all()
        except Exception as exc:
            rollback_error = None
            try:
                self._unregister_all()
                if not old_suspended:
                    self._register_all(old_bindings)
            except Exception as restore_exc:
                rollback_error = restore_exc
                # Do not leave one action functional and another silently missing.
                try:
                    self._unregister_all()
                except Exception:
                    pass
            self.bindings, self.suspended = old_bindings, old_suspended
            self._reset_edges()
            if rollback_error:
                self.suspended = True
                message = f'{exc} 原快捷键也无法恢复，已停用快捷键，请重新保存设置：{rollback_error}'
                self.error = message
                raise OSError(message) from exc
            self.error = str(exc)
            raise
        self.bindings = dict(bindings)
        self.suspended = suspended
        self.error = None
        self._reset_edges()

    def _handle(self, kind, value):
        if kind == 'configure':
            self._replace(value, self.suspended)
        elif kind == 'suspend':
            self._unregister_all()
            self.suspended = True
            self._reset_edges()
        elif kind == 'resume':
            if self.suspended:
                self._replace(self.bindings, False)
            else:
                self._reset_edges()

    def _report_error(self, exc):
        self.error = str(exc)
        if self.on_error:
            try:
                self.on_error(self.error)
            except Exception:
                pass

    def _poll(self):
        for action in self._backend.poll():
            if self.suspended or action not in self.registered or not self._armed.get(action, False):
                continue
            self._armed[action] = False
            try:
                self.callback(action)
            except Exception as exc:
                self._report_error(exc)
        for action, text in self.bindings.items():
            spec = parse_hotkey(text)
            keys = spec.keys if action in self._release_all else (spec.vk,)
            if not any(self._backend.is_pressed(key) for key in keys):
                self._armed[action] = True
                self._release_all.discard(action)

    def _run(self, startup_done, startup_result):
        try:
            self._backend = self._backend_factory()
            if not self.suspended:
                self._register_all(self.bindings)
            self.error = None
            self._reset_edges()
            startup_done.set()
            while not self._stop_event.is_set():
                while True:
                    try:
                        kind, value, done, result = self._commands.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        self._handle(kind, value)
                    except Exception as exc:
                        result.append(exc)
                    finally:
                        done.set()
                self._poll()
                self._wakeup.wait(.008)
                self._wakeup.clear()
        except Exception as exc:
            if not startup_done.is_set():
                startup_result.append(exc)
            self._report_error(exc)
        finally:
            try:
                if self._backend is not None:
                    self._unregister_all()
            except Exception as exc:
                self._report_error(exc)
            startup_done.set()
            while True:
                try:
                    _, _, done, result = self._commands.get_nowait()
                except queue.Empty:
                    break
                result.append(RuntimeError('快捷键服务已停止。'))
                done.set()
