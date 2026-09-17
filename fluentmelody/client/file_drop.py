"""Route local score drops to the window, including drops on input widgets."""
from __future__ import annotations

from pathlib import Path
import sys
import time
from typing import Callable

from fluentpy.qt import QtCore, QtWidgets


MUSIC_SUFFIXES = frozenset({'.mid', '.midi', '.nbs'})


def local_music_paths(values) -> tuple[Path, ...]:
    """Validate paths without accessing UNC network shares."""
    result: list[Path] = []
    seen: set[Path] = set()
    for value in values:
        if str(value).startswith(('\\\\', '//')):
            continue
        path = Path(value)
        if not path.is_absolute() or path.suffix.lower() not in MUSIC_SUFFIXES:
            continue
        try:
            if not path.is_file():
                continue
            path = path.resolve()
        except (OSError, ValueError, RuntimeError):
            continue
        if path not in seen:
            result.append(path)
            seen.add(path)
    return tuple(result)


def music_files(mime: QtCore.QMimeData) -> tuple[Path, ...]:
    """Return existing local score files in drag order, without duplicates."""
    if not mime.hasUrls():
        return ()
    values = []
    for url in mime.urls():
        # isLocalFile alone also accepts file://other-computer/share URLs.
        if not url.isLocalFile() or url.host():
            continue
        values.append(url.toLocalFile())
    return local_music_paths(values)


class WindowsFileDropFilter(QtCore.QAbstractNativeEventFilter):
    """WM_DROPFILES fallback for Explorer -> elevated application drops.

    Qt uses OLE drag-and-drop, which cannot cross the UAC integrity boundary.
    The legacy shell drop path is separate and must consume/free its own HDROP.
    Only the supplied top-level window opts into the required message filters.
    """
    WM_DROPFILES = 0x0233
    WM_COPYDATA = 0x004A
    WM_COPYGLOBALDATA = 0x0049

    def __init__(self, window, callback):
        super().__init__()
        import ctypes
        from ctypes import wintypes
        self._ctypes = ctypes
        self._wintypes = wintypes
        self._window = window
        self._callback = callback
        self._app = QtWidgets.QApplication.instance()
        self._closed = False
        self._hwnd = 0
        self._changed = []
        self._user32 = ctypes.WinDLL('user32', use_last_error=True)
        self._shell32 = ctypes.WinDLL('shell32', use_last_error=True)
        self._ole32 = ctypes.WinDLL('ole32', use_last_error=True)
        self._ole32.RevokeDragDrop.argtypes = [wintypes.HWND]
        self._ole32.RevokeDragDrop.restype = wintypes.LONG
        self._shell32.IsUserAnAdmin.argtypes = []
        self._shell32.IsUserAnAdmin.restype = wintypes.BOOL
        self._elevated = bool(self._shell32.IsUserAnAdmin())
        self._user32.ChangeWindowMessageFilterEx.argtypes = [wintypes.HWND, wintypes.UINT,
                                                           wintypes.DWORD, ctypes.c_void_p]
        self._user32.ChangeWindowMessageFilterEx.restype = wintypes.BOOL
        self._shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
        self._shell32.DragAcceptFiles.restype = None
        self._shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                               wintypes.LPWSTR, wintypes.UINT]
        self._shell32.DragQueryFileW.restype = wintypes.UINT
        self._shell32.DragFinish.argtypes = [wintypes.HANDLE]
        self._shell32.DragFinish.restype = None
        try:
            self.refresh_window()
            self._app.installNativeEventFilter(self)
        except Exception:
            self.close()
            raise

    def _release_window(self):
        if self._hwnd:
            self._shell32.DragAcceptFiles(self._hwnd, False)
            for message in self._changed:
                self._user32.ChangeWindowMessageFilterEx(self._hwnd, message, 0, None)
        self._changed = []
        self._hwnd = 0

    def _enable_shell_drop(self):
        if self._elevated:
            # Explorer's OLE fallback requires a non-OLE target. Qt otherwise
            # registers the top-level HWND automatically, even for child fields.
            # Keep Qt OLE intact in an ordinary (non-elevated) process.
            result = self._ole32.RevokeDragDrop(self._hwnd) & 0xFFFFFFFF
            if result not in (0, 0x80040100):  # S_OK or DRAGDROP_E_NOTREGISTERED
                raise OSError(f'Windows 拖入通道初始化失败：0x{result:08X}')
        self._shell32.DragAcceptFiles(self._hwnd, True)

    def refresh_window(self, force=False):
        if self._closed:
            return
        hwnd = int(self._window.winId())
        if hwnd == self._hwnd:
            if force:
                self._enable_shell_drop()
            return
        self._release_window()
        self._hwnd = hwnd
        # CHANGEFILTERSTRUCT: cbSize, ExtStatus (two DWORDs).
        for message in (self.WM_DROPFILES, self.WM_COPYDATA, self.WM_COPYGLOBALDATA):
            status = (self._wintypes.DWORD * 2)(8, 0)
            if not self._user32.ChangeWindowMessageFilterEx(hwnd, message, 1,
                                                            self._ctypes.byref(status)):
                raise self._ctypes.WinError(self._ctypes.get_last_error())
            if status[1] == 0:
                self._changed.append(message)
        self._enable_shell_drop()

    def _read_drop(self, handle):
        files = []
        try:
            count = self._shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
            # Bound work for malformed or unreasonably large shell payloads.
            for index in range(min(count, 4096)):
                length = self._shell32.DragQueryFileW(handle, index, None, 0)
                if not 0 < length <= 32767:
                    continue
                buffer = self._ctypes.create_unicode_buffer(length + 1)
                self._shell32.DragQueryFileW(handle, index, buffer, length + 1)
                files.append(buffer.value)
        finally:
            self._shell32.DragFinish(handle)
        return local_music_paths(files)

    def nativeEventFilter(self, event_type, message):
        if self._closed or bytes(event_type) not in (b'windows_generic_MSG', b'windows_dispatcher_MSG'):
            return False, 0
        msg = self._ctypes.cast(int(message), self._ctypes.POINTER(self._wintypes.MSG)).contents
        if msg.hWnd != self._hwnd or msg.message != self.WM_DROPFILES:
            return False, 0
        paths = self._read_drop(msg.wParam)
        if paths:
            self._callback(paths)
        return True, 0

    def close(self):
        if not self._closed:
            self._closed = True
            self._app.removeNativeEventFilter(self)
            self._release_window()
            self._callback = None


class MusicFileDropFilter(QtCore.QObject):
    """Intercept score drags before text edits and scroll viewports consume them.

    Construct once after creating the window. The caller supplies a loading-only
    callback; this helper never plays, converts, or uploads a file. Call close()
    from the window's closeEvent. Parenting also removes the filter on deletion.
    Non-score Qt drags retain the receiving widget's existing behavior. Elevated
    Windows uses shell file drops instead of OLE, so Explorer can reach the app.
    """

    _drag_types = frozenset({
        QtCore.QEvent.Type.DragEnter,
        QtCore.QEvent.Type.DragMove,
        QtCore.QEvent.Type.Drop,
    })

    def __init__(self, window: QtWidgets.QWidget,
                 on_file: Callable[[Path], None],
                 on_message: Callable[[str], None] | None = None):
        super().__init__(window)
        app = QtWidgets.QApplication.instance()
        if app is None:
            raise RuntimeError('文件拖入需要先创建应用。')
        self._window = window
        self._on_file = on_file
        self._on_message = on_message
        self._app = app
        self._closed = False
        self._drop_consumed = False
        self._last_drop = None
        self._native = None
        app.installEventFilter(self)
        window.destroyed.connect(self.close)
        # Offscreen/minimal Qt backends can run on Windows without a Win32 HWND.
        if sys.platform == 'win32' and app.platformName().lower() == 'windows':
            try:
                self._native = WindowsFileDropFilter(window, lambda paths: self._deliver(paths, 'native'))
            except OSError as exc:
                if on_message is not None:
                    on_message(f'Windows 文件拖入通道未启用：{exc}。仍可使用“载入歌曲”选择文件。')

    def close(self):
        if not self._closed:
            self._closed = True
            self._app.removeEventFilter(self)
            if self._native is not None:
                self._native.close()
                self._native = None
            self._on_file = None
            self._on_message = None

    def _deliver(self, paths, source):
        if self._closed:
            return
        now = time.monotonic()
        if self._last_drop is not None:
            previous_paths, previous_source, previous_time = self._last_drop
            if paths == previous_paths and source != previous_source and now - previous_time < .5:
                return
        self._last_drop = (paths, source, now)
        if len(paths) > 1 and self._on_message is not None:
            self._on_message(f'一次载入一首歌曲，已选择：{paths[0].name}（共拖入 {len(paths)} 个歌曲文件）')
        if self._on_file is not None:
            self._on_file(paths[0])

    def eventFilter(self, watched, event):
        if (not self._closed and watched is self._window and self._native is not None
                and event.type() in (QtCore.QEvent.Type.WinIdChange, QtCore.QEvent.Type.Show)):
            try:
                self._native.refresh_window(force=True)
            except OSError as exc:
                self._native.close()
                self._native = None
                if self._on_message is not None:
                    self._on_message(f'Windows 文件拖入通道未启用：{exc}。请使用“载入歌曲”选择文件。')
        if self._closed or event.type() not in self._drag_types:
            return False
        if not isinstance(watched, QtWidgets.QWidget):
            return False
        if watched is not self._window and not self._window.isAncestorOf(watched):
            return False
        paths = music_files(event.mimeData())
        if not paths:
            return False
        if event.type() == QtCore.QEvent.Type.DragEnter:
            self._drop_consumed = False
        # Loading does not move or delete the file at its source.
        event.setDropAction(QtCore.Qt.DropAction.CopyAction)
        event.accept()
        if event.type() == QtCore.QEvent.Type.Drop and not self._drop_consumed:
            self._drop_consumed = True
            self._deliver(paths, 'qt')
        return True
