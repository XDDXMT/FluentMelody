from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys

from .qt import QtWidgets
from .theme import ThemeMode


class _Margins(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


def _dwm_set_int(hwnd: int, attr: int, value: int) -> None:
    data = ctypes.c_int(value)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(data), ctypes.sizeof(data))


def _get_window_long_ptr(hwnd: int, index: int) -> int:
    user32 = ctypes.windll.user32
    if hasattr(user32, "GetWindowLongPtrW"):
        func = user32.GetWindowLongPtrW
        func.restype = ctypes.c_longlong
    else:
        func = user32.GetWindowLongW
        func.restype = ctypes.c_long
    func.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
    return int(func(hwnd, index))


def _set_window_long_ptr(hwnd: int, index: int, value: int) -> None:
    user32 = ctypes.windll.user32
    if hasattr(user32, "SetWindowLongPtrW"):
        func = user32.SetWindowLongPtrW
        func.restype = ctypes.c_longlong
        func.argtypes = [ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
    else:
        func = user32.SetWindowLongW
        func.restype = ctypes.c_long
        func.argtypes = [ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_long]
    func(hwnd, index, value)


def apply_system_backdrop(widget: QtWidgets.QWidget, mode: ThemeMode, active: bool) -> bool:
    """Best-effort Windows 11 Mica backdrop."""

    if sys.platform != "win32":
        return False

    try:
        hwnd = int(widget.winId())
        _dwm_set_int(hwnd, 20, 1 if mode is ThemeMode.DARK else 0)
        _dwm_set_int(hwnd, 38, 2 if active else 2)
        return True
    except Exception:
        return False


def apply_frameless_window_effect(widget: QtWidgets.QWidget, mode: ThemeMode) -> bool:
    if sys.platform != "win32":
        return False

    try:
        hwnd = int(widget.winId())
        dwmapi = ctypes.windll.dwmapi
        user32 = ctypes.windll.user32
        gwl_style = -16
        gwl_exstyle = -20
        ws_caption = 0x00C00000
        ws_ex_noanimation = 0x08000000
        exstyle = _get_window_long_ptr(hwnd, gwl_exstyle)
        _set_window_long_ptr(hwnd, gwl_exstyle, exstyle | ws_ex_noanimation)
        _set_window_long_ptr(hwnd, gwl_style, ws_caption)
        margins = _Margins(-1, -1, -1, -1)
        dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
        # DWMWA_USE_IMMERSIVE_DARK_MODE = 20.
        _dwm_set_int(hwnd, 20, 1 if mode is ThemeMode.DARK else 0)
        # DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_ROUND = 2.
        _dwm_set_int(hwnd, 33, 2)
        # DWMWA_BORDER_COLOR = 34. COLORREF is 0x00bbggrr.
        _dwm_set_int(hwnd, 34, 0x00262626 if mode is ThemeMode.DARK else 0x00D9D9D9)
        swp_nomove = 0x0002
        swp_nosize = 0x0001
        swp_nozorder = 0x0004
        swp_noactivate = 0x0010
        swp_framechanged = 0x0020
        user32.SetWindowPos(
            hwnd,
            0,
            0,
            0,
            0,
            0,
            swp_nomove | swp_nosize | swp_nozorder | swp_noactivate | swp_framechanged,
        )
        return True
    except Exception:
        return False
