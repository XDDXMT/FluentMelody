from __future__ import annotations

import weakref

from ..qt import Property, QtCore, QtGui, QtWidgets, Signal
from ..theme import theme
from .button import AccentButton, Button


class FramelessDialog(QtWidgets.QDialog):
    def __init__(
        self,
        title: str = "",
        content: str = "",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._target_pos: QtCore.QPoint | None = None
        self._pending_reveal = False
        self.setWindowFlags(
            QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowSystemMenuHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setSizeGripEnabled(False)
        self.setWindowOpacity(0.0)
        self.move(self._offscreen_pos())

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(0)

        self._panel = QtWidgets.QFrame(self)
        self._panel.setObjectName("dialogPanel")
        root.addWidget(self._panel)

        self._shadow = QtWidgets.QGraphicsDropShadowEffect(self._panel)
        self._shadow.setBlurRadius(34)
        self._shadow.setOffset(0, 10)
        self._panel.setGraphicsEffect(self._shadow)

        layout = QtWidgets.QVBoxLayout(self._panel)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        self._title_label = QtWidgets.QLabel(title)
        self._title_label.setObjectName("dialogTitle")
        self._title_label.setWordWrap(True)

        self._content_label = QtWidgets.QLabel(content)
        self._content_label.setObjectName("dialogContent")
        self._content_label.setWordWrap(True)
        self._content_label.setVisible(bool(content))

        self._content_layout = QtWidgets.QVBoxLayout()
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(10)

        button_row = QtWidgets.QHBoxLayout()
        button_row.setContentsMargins(0, 10, 0, 0)
        button_row.setSpacing(12)
        self.yes_button = AccentButton("\u786e\u8ba4")
        self.cancel_button = Button("\u53d6\u6d88")
        self.yes_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        button_row.addWidget(self.yes_button)
        button_row.addWidget(self.cancel_button)

        layout.addWidget(self._title_label)
        layout.addWidget(self._content_label)
        layout.addLayout(self._content_layout)
        layout.addLayout(button_row)

        self._apply_theme()
        weak_self = weakref.ref(self)

        def update_theme(_tokens: object) -> None:
            dialog = weak_self()
            if dialog is not None:
                dialog._apply_theme()

        self._theme_slot = update_theme
        theme.changed.connect(self._theme_slot)
        self.destroyed.connect(self._disconnect_theme_slot)
        self._warm_up_surface()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        if self._pending_reveal:
            QtCore.QTimer.singleShot(48, self._reveal_at_target)

    def exec(self) -> int:  # type: ignore[override]
        self._prepare_geometry()
        return super().exec()

    def open(self) -> None:  # noqa: A003
        self._prepare_geometry()
        super().open()

    def setContentWidget(self, widget: QtWidgets.QWidget) -> None:  # noqa: N802 - Qt API friendly
        self.add_content_widget(widget)

    def add_content_widget(self, widget: QtWidgets.QWidget) -> None:
        self._content_layout.addWidget(widget)

    def setYesButtonText(self, text: str) -> None:  # noqa: N802
        self.yes_button.setText(text)

    def setCancelButtonText(self, text: str) -> None:  # noqa: N802
        self.cancel_button.setText(text)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        super().mouseMoveEvent(event)

    def _prepare_geometry(self) -> None:
        self._pending_reveal = True
        self.setWindowOpacity(0.0)
        layout = self.layout()
        if layout is not None:
            layout.activate()
        self.adjustSize()
        hint = self.sizeHint()
        self.resize(max(self.minimumWidth(), hint.width()), max(self.minimumHeight(), hint.height()))
        parent = self.parentWidget()
        if parent is not None:
            parent_rect = parent.frameGeometry()
            self._target_pos = parent_rect.center() - QtCore.QPoint(self.width() // 2, self.height() // 2)
        else:
            screen = QtWidgets.QApplication.primaryScreen()
            available = screen.availableGeometry() if screen is not None else QtCore.QRect(0, 0, 1200, 800)
            self._target_pos = available.center() - QtCore.QPoint(self.width() // 2, self.height() // 2)
        self.move(self._offscreen_pos())

    def _reveal_at_target(self) -> None:
        if not self._pending_reveal:
            return
        self._pending_reveal = False
        if self._target_pos is not None:
            self.move(self._target_pos)
        self.raise_()
        self.activateWindow()
        QtCore.QTimer.singleShot(0, self._finish_reveal)

    def _finish_reveal(self) -> None:
        self.setWindowOpacity(1.0)

    def _warm_up_surface(self) -> None:
        self._pending_reveal = False
        self.setWindowOpacity(0.0)
        self.move(self._offscreen_pos())
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.show()
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
            app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        self.hide()
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen, False)

    def _offscreen_pos(self) -> QtCore.QPoint:
        screen = QtWidgets.QApplication.primaryScreen()
        virtual = screen.virtualGeometry() if screen is not None else QtCore.QRect(0, 0, 1200, 800)
        return QtCore.QPoint(virtual.left() - self.width() - 300, virtual.top() - self.height() - 300)

    def _disconnect_theme_slot(self) -> None:
        try:
            theme.changed.disconnect(self._theme_slot)
        except (RuntimeError, TypeError):
            pass

    def _apply_theme(self) -> None:
        tokens = theme.tokens
        is_light = tokens.mode.value == "light"
        panel = "#ffffff" if is_light else "#2b2b2b"
        border = "#d9d9d9" if is_light else "#202020"
        self._shadow.setColor(QtGui.QColor(0, 0, 0, 95 if is_light else 150))
        self.setStyleSheet(
            f"""
QDialog {{
    background: transparent;
    border: none;
}}
QFrame#dialogPanel {{
    background: {panel};
    border: 1px solid {border};
    border-radius: 7px;
}}
QLabel#dialogTitle {{
    color: {tokens.text};
    font-size: 22px;
    font-weight: 600;
    background: transparent;
    border: none;
}}
QLabel#dialogContent {{
    color: {tokens.text};
    background: transparent;
    border: none;
    font-size: {tokens.font_size}px;
}}
"""
        )


class MaskedDialog(QtWidgets.QWidget):
    accepted = Signal()
    rejected = Signal()
    finished = Signal(int)

    Accepted = int(QtWidgets.QDialog.DialogCode.Accepted)
    Rejected = int(QtWidgets.QDialog.DialogCode.Rejected)

    def __init__(
        self,
        title: str = "",
        content: str = "",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        if parent is None:
            parent = QtWidgets.QApplication.activeWindow()
        super().__init__(parent)
        self._event_loop: QtCore.QEventLoop | None = None
        self._result = self.Rejected
        self._finished = False
        self._closable_on_mask_clicked = False
        self._fade_done_slot: object | None = None
        self._mask_opacity = 0.0
        self._mask_alpha = 76

        if parent is not None:
            parent.installEventFilter(self)

        self.setObjectName("maskedDialog")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.hide()

        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(32, 32, 32, 32)
        root.setSpacing(0)
        root.addStretch(1)

        self._panel = QtWidgets.QFrame(self)
        self._panel.setObjectName("dialogPanel")
        self._panel.setMinimumWidth(560)
        self._panel.setMaximumWidth(760)
        root.addWidget(self._panel, 0, QtCore.Qt.AlignmentFlag.AlignCenter)
        root.addStretch(1)

        self._shadow = QtWidgets.QGraphicsDropShadowEffect(self._panel)
        self._shadow.setBlurRadius(60)
        self._shadow.setOffset(0, 10)
        self._panel.setGraphicsEffect(self._shadow)

        layout = QtWidgets.QVBoxLayout(self._panel)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        self._title_label = QtWidgets.QLabel(title)
        self._title_label.setObjectName("dialogTitle")
        self._title_label.setWordWrap(True)

        self._content_label = QtWidgets.QLabel(content)
        self._content_label.setObjectName("dialogContent")
        self._content_label.setWordWrap(True)
        self._content_label.setVisible(bool(content))

        self._content_layout = QtWidgets.QVBoxLayout()
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(10)

        button_row = QtWidgets.QHBoxLayout()
        button_row.setContentsMargins(0, 10, 0, 0)
        button_row.setSpacing(12)
        self.yes_button = AccentButton("\u786e\u8ba4")
        self.cancel_button = Button("\u53d6\u6d88")
        self.yes_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        button_row.addWidget(self.yes_button)
        button_row.addWidget(self.cancel_button)

        layout.addWidget(self._title_label)
        layout.addWidget(self._content_label)
        layout.addLayout(self._content_layout)
        layout.addLayout(button_row)

        self._fade = QtCore.QPropertyAnimation(self, b"maskOpacity", self)
        self._fade.setEasingCurve(QtCore.QEasingCurve.Type.InOutSine)

        self._apply_theme()
        weak_self = weakref.ref(self)

        def update_theme(_tokens: object) -> None:
            dialog = weak_self()
            if dialog is not None:
                dialog._apply_theme()

        self._theme_slot = update_theme
        theme.changed.connect(self._theme_slot)
        self.destroyed.connect(self._disconnect_theme_slot)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if watched is self.parentWidget() and event.type() == QtCore.QEvent.Type.Resize and self.isVisible():
            self._fit_to_parent()
        return super().eventFilter(watched, event)

    def exec(self) -> int:  # type: ignore[override]
        self.open()
        self._event_loop = QtCore.QEventLoop(self)
        self.finished.connect(self._event_loop.quit)
        self._event_loop.exec()
        return self._result

    def open(self) -> None:  # noqa: A003
        self._finished = False
        self._result = self.Rejected
        if self._fade_done_slot is not None:
            try:
                self._fade.finished.disconnect(self._fade_done_slot)
            except (RuntimeError, TypeError):
                pass
            self._fade_done_slot = None
        self._fit_to_parent()
        self.setMaskOpacity(0.0)
        self.show()
        self.raise_()
        self._panel.raise_()
        self.setFocus(QtCore.Qt.FocusReason.PopupFocusReason)
        self._fade.stop()
        self._fade.setDuration(180)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()

    def result(self) -> int:
        return self._result

    def accept(self) -> None:
        self.done(self.Accepted)

    def reject(self) -> None:
        self.done(self.Rejected)

    def done(self, result: int) -> None:
        if self._finished:
            return
        self._finished = True
        self._result = result
        self._fade.stop()
        self._fade.setDuration(100)
        self._fade.setStartValue(self._mask_opacity)
        self._fade.setEndValue(0.0)
        if self._fade_done_slot is not None:
            try:
                self._fade.finished.disconnect(self._fade_done_slot)
            except (RuntimeError, TypeError):
                pass
        self._fade_done_slot = lambda: self._finish_done(result)
        self._fade.finished.connect(self._fade_done_slot)
        self._fade.start()

    def setClosableOnMaskClicked(self, closable: bool) -> None:  # noqa: N802
        self._closable_on_mask_clicked = closable

    def isClosableOnMaskClicked(self) -> bool:  # noqa: N802
        return self._closable_on_mask_clicked

    def setContentWidget(self, widget: QtWidgets.QWidget) -> None:  # noqa: N802
        self.add_content_widget(widget)

    def add_content_widget(self, widget: QtWidgets.QWidget) -> None:
        self._content_layout.addWidget(widget)

    def setYesButtonText(self, text: str) -> None:  # noqa: N802
        self.yes_button.setText(text)

    def setCancelButtonText(self, text: str) -> None:  # noqa: N802
        self.cancel_button.setText(text)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        color = QtGui.QColor(0, 0, 0, round(self._mask_alpha * self._mask_opacity))
        painter.fillRect(self.rect(), color)
        super().paintEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._closable_on_mask_clicked and not self._panel.geometry().contains(event.pos()):
            self.reject()
            return
        event.accept()

    def _finish_done(self, result: int) -> None:
        self.hide()
        if result == self.Accepted:
            self.accepted.emit()
        else:
            self.rejected.emit()
        self.finished.emit(result)

    def getMaskOpacity(self) -> float:  # noqa: N802
        return self._mask_opacity

    def setMaskOpacity(self, opacity: float) -> None:  # noqa: N802
        self._mask_opacity = max(0.0, min(1.0, float(opacity)))
        self.update()

    maskOpacity = Property(float, getMaskOpacity, setMaskOpacity)

    def _fit_to_parent(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        else:
            screen = QtWidgets.QApplication.primaryScreen()
            self.setGeometry(screen.availableGeometry() if screen is not None else QtCore.QRect(0, 0, 960, 640))

    def _disconnect_theme_slot(self) -> None:
        try:
            theme.changed.disconnect(self._theme_slot)
        except (RuntimeError, TypeError):
            pass

    def _apply_theme(self) -> None:
        tokens = theme.tokens
        is_light = tokens.mode.value == "light"
        panel = "#ffffff" if is_light else "#2b2b2b"
        border = "#d9d9d9" if is_light else "#202020"
        self._mask_alpha = 76 if is_light else 112
        self._shadow.setColor(QtGui.QColor(0, 0, 0, 70 if is_light else 110))
        self.setStyleSheet(
            f"""
QFrame#dialogPanel {{
    background: {panel};
    border: 1px solid {border};
    border-radius: 7px;
}}
QLabel#dialogTitle {{
    color: {tokens.text};
    font-size: 22px;
    font-weight: 600;
    background: transparent;
    border: none;
}}
QLabel#dialogContent {{
    color: {tokens.text};
    background: transparent;
    border: none;
    font-size: {tokens.font_size}px;
}}
"""
        )


MaskDialog = MaskedDialog
MessageBox = MaskedDialog
