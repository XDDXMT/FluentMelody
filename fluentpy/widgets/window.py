from __future__ import annotations

from ..qt import QtCore, QtGui, QtWidgets
from ..theme import ThemeMode, theme
from ..window_effects import apply_system_backdrop


class FluentWindow(QtWidgets.QMainWindow):
    def __init__(self, title: str = "FluentPy", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(980, 640)
        self._active = True
        self._root = QtWidgets.QWidget()
        self._root.setObjectName("fluentRoot")
        self._root.setAutoFillBackground(True)
        self._layout = QtWidgets.QVBoxLayout(self._root)
        self._layout.setContentsMargins(24, 24, 24, 24)
        self._layout.setSpacing(16)
        self.setCentralWidget(self._root)
        self._apply_theme()
        theme.subscribe(self, self._apply_theme)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self._apply_backdrop()

    def changeEvent(self, event: QtCore.QEvent) -> None:
        if event.type() == QtCore.QEvent.Type.ActivationChange:
            self._active = self.isActiveWindow()
            theme.set_active(self._active)
            self._apply_backdrop()
        super().changeEvent(event)

    @property
    def content_layout(self) -> QtWidgets.QVBoxLayout:
        return self._layout

    def set_dark(self, enabled: bool) -> None:
        theme.set_mode(ThemeMode.DARK if enabled else ThemeMode.LIGHT)

    def _apply_theme(self) -> None:
        tokens = theme.tokens
        background = tokens.background if theme.active else tokens.background_inactive
        palette = self.palette()
        palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(background))
        palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor(tokens.text))
        self.setPalette(palette)
        self._root.setPalette(palette)
        stylesheet = (
            f"""
QWidget#fluentRoot {{
    background: {background};
}}
QWidget {{
    color: {tokens.text};
    font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI";
    font-size: {tokens.font_size}px;
}}
QLabel {{
    background: transparent;
    border: none;
    color: {tokens.text};
}}
QFrame#componentCard {{
    background: {tokens.surface};
    border: 1px solid {tokens.border};
    border-radius: {tokens.radius}px;
}}
QLabel#muted {{
    background: transparent;
    border: none;
    color: {tokens.text_muted};
}}
"""
        )
        self.setStyleSheet(stylesheet)
        self._root.setStyleSheet(stylesheet)

    def _apply_backdrop(self) -> None:
        apply_system_backdrop(self, theme.tokens.mode, theme.active)
