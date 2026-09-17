from __future__ import annotations

from ..qt import QtCore, QtGui, QtWidgets
from ..theme import theme


class Toast(QtWidgets.QFrame):
    def __init__(
        self,
        title: str,
        detail: str = "",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlags(QtCore.Qt.WindowType.FramelessWindowHint | QtCore.Qt.WindowType.ToolTip)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(3)
        self._title = QtWidgets.QLabel(title)
        self._detail = QtWidgets.QLabel(detail)
        self._detail.setVisible(bool(detail))
        layout.addWidget(self._title)
        layout.addWidget(self._detail)
        self._apply_theme()
        theme.subscribe(self, self._apply_theme)

    @classmethod
    def success(
        cls,
        title: str,
        *,
        detail: str = "",
        placement: str = "top-right",
        timeout_ms: int = 2000,
        parent: QtWidgets.QWidget | None = None,
    ) -> "Toast":
        toast = cls(title, detail, parent or QtWidgets.QApplication.activeWindow())
        toast.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        toast.show_at(placement)
        QtCore.QTimer.singleShot(timeout_ms, toast.close)
        return toast

    def show_at(self, placement: str = "top-right") -> None:
        self.adjustSize()
        parent = self.parentWidget()
        screen_rect = QtGui.QGuiApplication.primaryScreen().availableGeometry()
        base_rect = parent.frameGeometry() if parent else screen_rect
        margin = 18

        if placement.endswith("right"):
            x = base_rect.right() - self.width() - margin
        else:
            x = base_rect.left() + margin
        if placement.startswith("bottom"):
            y = base_rect.bottom() - self.height() - margin
        else:
            y = base_rect.top() + margin

        self.move(max(screen_rect.left(), x), max(screen_rect.top(), y))
        self.show()
        self.raise_()

    def _apply_theme(self) -> None:
        tokens = theme.tokens
        self.setStyleSheet(
            f"""
QFrame {{
    background: {tokens.surface};
    border: 1px solid {tokens.border};
    border-radius: {tokens.radius}px;
}}
QLabel {{
    color: {tokens.text};
    background: transparent;
    border: 0;
    font-size: {tokens.font_size}px;
}}
"""
        )
        font = self._title.font()
        font.setBold(True)
        self._title.setFont(font)
        self._detail.setStyleSheet(f"color: {tokens.text_muted};")
