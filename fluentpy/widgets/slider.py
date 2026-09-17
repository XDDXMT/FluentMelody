from __future__ import annotations

from ..qt import QtCore, QtGui, QtWidgets
from ..theme import theme


class Slider(QtWidgets.QSlider):
    def __init__(
        self,
        orientation: QtCore.Qt.Orientation = QtCore.Qt.Orientation.Horizontal,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(orientation, parent)
        self.setMinimumHeight(34)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._hovered_handle = False
        self._pressed_handle = False
        self._center_progress = 0.0
        self._press_progress = 0.0
        self._center_animation = QtCore.QVariantAnimation(self)
        self._center_animation.setDuration(theme.tokens.animation_ms)
        self._center_animation.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        self._center_animation.valueChanged.connect(self._set_center_progress)
        self._press_animation = QtCore.QVariantAnimation(self)
        self._press_animation.setDuration(90)
        self._press_animation.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        self._press_animation.valueChanged.connect(self._set_press_progress)
        self.setMouseTracking(True)
        theme.subscribe(self, self.update)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        hovered = self._handle_rect().contains(event.position())
        if hovered != self._hovered_handle:
            self._hovered_handle = hovered
            self._animate_center(1.0 if hovered else 0.0)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        self._pressed_handle = self._handle_rect().contains(event.position())
        if self._pressed_handle:
            self._animate_press(1.0)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._pressed_handle:
            self._pressed_handle = False
            self._animate_press(0.0)
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        if self._hovered_handle:
            self._hovered_handle = False
            self._animate_center(0.0)
        if self._pressed_handle:
            self._pressed_handle = False
            self._animate_press(0.0)
        super().leaveEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        if self.orientation() != QtCore.Qt.Orientation.Horizontal:
            return super().paintEvent(event)
        del event
        tokens = theme.tokens
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        left, right, center_y, knob_x = self._geometry_values()

        track_color = QtGui.QColor("#000000" if tokens.mode.value == "light" else "#ffffff")
        track_color.setAlpha(80 if tokens.mode.value == "light" else 90)
        accent = QtGui.QColor(tokens.accent)

        track_rect = QtCore.QRectF(left, center_y - 2, right - left, 4)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(track_rect, 2, 2)

        filled_rect = QtCore.QRectF(left, center_y - 2, max(0, knob_x - left), 4)
        painter.setBrush(accent)
        painter.drawRoundedRect(filled_rect, 2, 2)

        painter.setBrush(QtGui.QColor(tokens.surface if tokens.mode.value == "dark" else "#ffffff"))
        painter.setPen(QtGui.QPen(QtGui.QColor(tokens.border), 1))
        painter.drawEllipse(QtCore.QPointF(knob_x, center_y), 9, 9)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        center_radius = max(1.9, 4 + 1.4 * self._center_progress - 2.7 * self._press_progress)
        painter.drawEllipse(QtCore.QPointF(knob_x, center_y), center_radius, center_radius)

    def _geometry_values(self) -> tuple[float, float, float, float]:
        left = 12.0
        right = float(self.width() - 12)
        center_y = self.height() / 2
        span = max(1, self.maximum() - self.minimum())
        progress = (self.value() - self.minimum()) / span
        knob_x = left + (right - left) * progress
        return left, right, center_y, knob_x

    def _handle_rect(self) -> QtCore.QRectF:
        _left, _right, center_y, knob_x = self._geometry_values()
        return QtCore.QRectF(knob_x - 11, center_y - 11, 22, 22)

    def _animate_center(self, end: float) -> None:
        self._center_animation.stop()
        self._center_animation.setStartValue(self._center_progress)
        self._center_animation.setEndValue(end)
        self._center_animation.start()

    def _animate_press(self, end: float) -> None:
        self._press_animation.stop()
        self._press_animation.setStartValue(self._press_progress)
        self._press_animation.setEndValue(end)
        self._press_animation.start()

    def _set_center_progress(self, value: object) -> None:
        self._center_progress = float(value)
        self.update()

    def _set_press_progress(self, value: object) -> None:
        self._press_progress = float(value)
        self.update()
