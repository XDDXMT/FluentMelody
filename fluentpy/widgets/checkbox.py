from __future__ import annotations

from ..qt import QtCore, QtGui, QtWidgets, Signal
from ..theme import theme


class CheckBox(QtWidgets.QAbstractButton):
    stateChanged = Signal(int)

    def __init__(self, text: str = "", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.setMinimumHeight(32)
        self._tristate = False
        self._state = QtCore.Qt.CheckState.Unchecked
        self._hover = False
        self._pressed = False
        theme.subscribe(self, self.update)

    def setTristate(self, enabled: bool = True) -> None:  # noqa: N802 - Qt API
        self._tristate = enabled

    def isTristate(self) -> bool:  # noqa: N802 - Qt API
        return self._tristate

    def checkState(self) -> QtCore.Qt.CheckState:  # noqa: N802 - Qt API
        return self._state

    def setCheckState(self, state: QtCore.Qt.CheckState) -> None:  # noqa: N802 - Qt API
        if state == self._state:
            return
        self._state = state
        QtWidgets.QAbstractButton.setChecked(self, state == QtCore.Qt.CheckState.Checked)
        self.stateChanged.emit(int(state.value))
        self.update()

    def isChecked(self) -> bool:
        return self._state == QtCore.Qt.CheckState.Checked

    def setChecked(self, checked: bool) -> None:
        self.setCheckState(QtCore.Qt.CheckState.Checked if checked else QtCore.Qt.CheckState.Unchecked)

    def nextCheckState(self) -> None:
        if self._tristate:
            order = (
                QtCore.Qt.CheckState.Unchecked,
                QtCore.Qt.CheckState.PartiallyChecked,
                QtCore.Qt.CheckState.Checked,
            )
            self.setCheckState(order[(order.index(self._state) + 1) % len(order)])
        else:
            self.setChecked(not self.isChecked())

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(self.fontMetrics().horizontalAdvance(self.text()) + 34, 32)

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self._hover = False
        self._pressed = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        self._pressed = True
        self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        tokens = theme.tokens
        painter = QtGui.QPainter(self)
        painter.setRenderHints(QtGui.QPainter.RenderHint.Antialiasing | QtGui.QPainter.RenderHint.TextAntialiasing)

        box = QtCore.QRectF(2, self.height() / 2 - 10, 20, 20)
        selected = self._state != QtCore.Qt.CheckState.Unchecked
        if selected:
            fill = QtGui.QColor(tokens.accent)
            if self._hover:
                fill = fill.lighter(104)
            if self._pressed:
                fill = fill.darker(110)
            painter.setBrush(fill)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
        else:
            fill = QtGui.QColor("#ffffff" if tokens.mode.value == "light" else "#ffffff")
            fill.setAlpha(150 if tokens.mode.value == "light" else 14)
            border = QtGui.QColor("#7b7f87" if tokens.mode.value == "light" else "#aeb5c3")
            if self._hover:
                fill.setAlpha(185 if tokens.mode.value == "light" else 24)
            painter.setBrush(fill)
            painter.setPen(QtGui.QPen(border, 1))
        painter.drawRoundedRect(box, 4, 4)

        if self._state == QtCore.Qt.CheckState.Checked:
            pen = QtGui.QPen(QtGui.QColor("#ffffff"), 1.7, QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenCapStyle.RoundCap, QtCore.Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawLine(QtCore.QLineF(box.left() + 5, box.center().y(), box.left() + 9, box.bottom() - 6))
            painter.drawLine(QtCore.QLineF(box.left() + 9, box.bottom() - 6, box.right() - 5, box.top() + 6))
        elif self._state == QtCore.Qt.CheckState.PartiallyChecked:
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor("#ffffff"))
            painter.drawRoundedRect(QtCore.QRectF(box.left() + 5, box.center().y() - 1.5, 10, 3), 1.5, 1.5)

        text_color = QtGui.QColor(tokens.text if self.isEnabled() else tokens.disabled)
        painter.setPen(text_color)
        painter.drawText(QtCore.QRectF(32, 0, self.width() - 34, self.height()), QtCore.Qt.AlignmentFlag.AlignVCenter, self.text())
