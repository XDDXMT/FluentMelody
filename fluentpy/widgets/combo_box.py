from __future__ import annotations

from ..qt import QtCore, QtGui, QtWidgets, Signal
from ..theme import theme
from .line_edit import LineEdit


class ComboBox(QtWidgets.QWidget):
    currentIndexChanged = Signal(int)
    currentTextChanged = Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[str] = []
        self._current_index = -1
        self._popup: QtWidgets.QFrame | None = None
        self._hover = False
        self._pressed = False
        self.setMinimumSize(180, 34)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        theme.subscribe(self, self.update)

    def addItem(self, text: str) -> None:  # noqa: N802 - Qt API
        self.add_item(text)

    def addItems(self, texts: list[str]) -> None:  # noqa: N802 - Qt API
        for text in texts:
            self.add_item(text)

    def add_item(self, text: str) -> None:
        self._items.append(text)
        if self._current_index < 0:
            self.setCurrentIndex(0)

    def currentText(self) -> str:  # noqa: N802 - Qt API
        if 0 <= self._current_index < len(self._items):
            return self._items[self._current_index]
        return ""

    def currentIndex(self) -> int:  # noqa: N802 - Qt API
        return self._current_index

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802 - Qt API
        if index < 0 or index >= len(self._items) or index == self._current_index:
            return
        self._current_index = index
        self.currentIndexChanged.emit(index)
        self.currentTextChanged.emit(self.currentText())
        self.update()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        self._pressed = True
        self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self._pressed = False
        self.update()
        if self.rect().contains(event.position().toPoint()):
            self.show_popup()
        super().mouseReleaseEvent(event)

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self._hover = False
        self._pressed = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHints(QtGui.QPainter.RenderHint.Antialiasing | QtGui.QPainter.RenderHint.TextAntialiasing)
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        bg, border, lower = _input_colors(self._hover, self._pressed)
        painter.setBrush(bg)
        painter.setPen(QtGui.QPen(border, 1))
        painter.drawRoundedRect(rect, theme.tokens.radius, theme.tokens.radius)
        painter.setPen(QtGui.QPen(lower, 1))
        painter.drawLine(QtCore.QLineF(rect.left() + theme.tokens.radius, rect.bottom(), rect.right() - theme.tokens.radius, rect.bottom()))
        painter.setPen(QtGui.QColor(theme.tokens.text))
        painter.drawText(rect.adjusted(12, 0, -34, 0), QtCore.Qt.AlignmentFlag.AlignVCenter, self.currentText())
        _draw_chevron(painter, QtCore.QRectF(rect.right() - 27, rect.center().y() - 5, 12, 10), QtGui.QColor(theme.tokens.text_muted))

    def show_popup(self) -> None:
        if self._popup and self._popup.isVisible():
            self._popup.close()
            return
        popup_parent = self.window()
        self._popup = _ComboPopup(self._items, self._current_index, popup_parent)
        self._popup.itemSelected.connect(self.setCurrentIndex)
        pos = self.mapTo(popup_parent, QtCore.QPoint(0, self.height() + 1))
        self._popup.setMinimumWidth(self.width())
        self._popup.adjustSize()
        if pos.y() + self._popup.height() > popup_parent.height() - 6:
            pos.setY(self.mapTo(popup_parent, QtCore.QPoint(0, -self._popup.height() - 1)).y())
        if pos.x() + self._popup.width() > popup_parent.width() - 6:
            pos.setX(max(6, popup_parent.width() - self._popup.width() - 6))
        pos.setY(max(6, pos.y()))
        self._popup.move(pos)
        self._popup.show()
        self._popup.raise_()
        self._popup.update()


class EditableComboBox(ComboBox):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._line_edit = LineEdit(show_clear_button=False)
        self._line_edit.setParent(self)
        self._line_edit.setMinimumHeight(34)
        self._line_edit.set_trailing_inset(38)
        self._line_edit.textEdited.connect(self.currentTextChanged.emit)
        self._drop_button = _ComboDropButton(self)
        self._drop_button.clicked.connect(self.show_popup)
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)

    def lineEdit(self) -> LineEdit:  # noqa: N802 - Qt API
        return self._line_edit

    def currentText(self) -> str:  # noqa: N802 - Qt API
        return self._line_edit.text() or super().currentText()

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802 - Qt API
        super().setCurrentIndex(index)
        self._line_edit.setText(super().currentText())

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._line_edit.setGeometry(0, 0, self.width(), self.height())
        self._drop_button.setGeometry(self.width() - 36, 1, 35, max(1, self.height() - 2))

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        arrow_rect = QtCore.QRect(self.width() - 34, 0, 34, self.height())
        if arrow_rect.contains(event.position().toPoint()):
            self.show_popup()
            return
        super(ComboBox, self).mouseReleaseEvent(event)


class _ComboDropButton(QtWidgets.QAbstractButton):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self._hover = False

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        if self._hover:
            fill = QtGui.QColor("#ffffff" if theme.tokens.mode.value == "dark" else "#000000")
            fill.setAlpha(18 if theme.tokens.mode.value == "dark" else 8)
            painter.setBrush(fill)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(5, 5, -5, -5), 4, 4)
        _draw_chevron(painter, QtCore.QRectF(self.width() / 2 - 6, self.height() / 2 - 5, 12, 10), QtGui.QColor(theme.tokens.text_muted))


class _ComboPopup(QtWidgets.QFrame):
    itemSelected = Signal(int)

    def __init__(self, items: list[str], current: int, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("comboPopup")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 7)
        layout.setSpacing(1)
        for index, text in enumerate(items):
            item = _ComboPopupItem(text, selected=index == current)
            item.clicked.connect(lambda _checked=False, i=index: self._select(i))
            layout.addWidget(item)
        self.setFixedHeight(12 + len(items) * 33)
        self._apply_theme()
        theme.subscribe(self, self._apply_theme)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() == QtCore.QEvent.Type.MouseButtonPress and self.isVisible():
            mouse_event = event
            if hasattr(mouse_event, "globalPosition"):
                local_pos = self.mapFromGlobal(mouse_event.globalPosition().toPoint())
            else:
                local_pos = self.mapFromGlobal(mouse_event.globalPos())
            if not self.rect().contains(local_pos):
                self.close()
        return super().eventFilter(watched, event)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().closeEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        tokens = theme.tokens
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), _popup_outer_color())
        rect = QtCore.QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        border = QtGui.QColor(0, 0, 0, 24) if tokens.mode.value == "light" else QtGui.QColor(255, 255, 255, 18)
        painter.setPen(QtGui.QPen(border, 1))
        painter.setBrush(_popup_surface_color())
        painter.drawRoundedRect(rect, tokens.radius, tokens.radius)

    def _select(self, index: int) -> None:
        self.itemSelected.emit(index)
        self.close()

    def _apply_theme(self) -> None:
        self.setStyleSheet("QFrame#comboPopup { background: transparent; border: none; }")
        self.update()


class _ComboPopupItem(QtWidgets.QAbstractButton):
    def __init__(self, text: str, selected: bool = False) -> None:
        super().__init__()
        self.setText(text)
        self.setProperty("selected", selected)
        self.setFixedHeight(32)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._hover = False

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHints(QtGui.QPainter.RenderHint.Antialiasing | QtGui.QPainter.RenderHint.TextAntialiasing)
        if self._hover or self.property("selected"):
            fill = QtGui.QColor("#000000" if theme.tokens.mode.value == "light" else "#ffffff")
            fill.setAlpha(7 if theme.tokens.mode.value == "light" else 18)
            if self.property("selected"):
                fill.setAlpha(9 if theme.tokens.mode.value == "light" else 28)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(0, 0, 0, -1), theme.tokens.radius, theme.tokens.radius)
        if self.property("selected"):
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(theme.tokens.accent))
            painter.drawRoundedRect(QtCore.QRectF(0, 7, 3, 18), 1.5, 1.5)
        painter.setPen(QtGui.QColor(theme.tokens.text))
        painter.drawText(QtCore.QRectF(12, 0, self.width() - 16, self.height()), QtCore.Qt.AlignmentFlag.AlignVCenter, self.text())


def _input_colors(hover: bool, pressed: bool) -> tuple[QtGui.QColor, QtGui.QColor, QtGui.QColor]:
    tokens = theme.tokens
    bg = QtGui.QColor("#ffffff")
    bg.setAlpha(178 if tokens.mode.value == "light" else 16)
    if hover:
        bg.setAlpha(205 if tokens.mode.value == "light" else 24)
    if pressed:
        bg.setAlpha(150 if tokens.mode.value == "light" else 12)
    border = QtGui.QColor(0, 0, 0, 18) if tokens.mode.value == "light" else QtGui.QColor(255, 255, 255, 14)
    lower = QtGui.QColor(0, 0, 0, 46) if tokens.mode.value == "light" else QtGui.QColor(255, 255, 255, 24)
    return bg, border, lower


def _popup_surface_color() -> QtGui.QColor:
    if theme.tokens.mode.value == "light":
        return QtGui.QColor(255, 255, 255)
    return QtGui.QColor(31, 35, 47)


def _popup_outer_color() -> QtGui.QColor:
    return QtGui.QColor(theme.tokens.background if theme.active else theme.tokens.background_inactive)


def _draw_chevron(painter: QtGui.QPainter, rect: QtCore.QRectF, color: QtGui.QColor) -> None:
    painter.setPen(QtGui.QPen(color, 1.45, QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenCapStyle.RoundCap))
    painter.drawLine(QtCore.QLineF(rect.left() + 2, rect.top() + 3, rect.center().x(), rect.bottom() - 2))
    painter.drawLine(QtCore.QLineF(rect.center().x(), rect.bottom() - 2, rect.right() - 2, rect.top() + 3))
