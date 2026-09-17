"""Shortcut recording field using the existing FluentPy line-edit appearance."""
from fluentpy import LineEdit
from fluentpy.qt import QtCore, QtGui, Signal
from ..core.hotkeys import normalize_hotkey


class ShortcutEdit(LineEdit):
    recordingStarted = Signal()
    recordingFinished = Signal()
    invalidShortcut = Signal(str)

    def __init__(self, value, parent=None):
        super().__init__('点击后按下快捷键',parent,show_clear_button=False)
        self.setReadOnly(True)
        self.setText(value)
        self.setMinimumWidth(180)
        self.setMaximumWidth(240)
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.NoContextMenu)
        self.setToolTip('点击后按键录入；Esc 取消，Tab 切换输入框。')
        self._original = value
        self._recording = False

    def focusInEvent(self,event):
        super().focusInEvent(event)
        self._original = self.text()
        self._recording = True
        self.recordingStarted.emit()
        self.selectAll()

    def focusOutEvent(self,event):
        super().focusOutEvent(event)
        if self._recording:
            self._recording = False
            self.recordingFinished.emit()

    def keyPressEvent(self,event):
        key = event.key()
        if event.isAutoRepeat():
            event.accept(); return
        if key==QtCore.Qt.Key.Key_Escape and not event.modifiers():
            self.setText(self._original)
            self.clearFocus(); event.accept(); return
        if key in (QtCore.Qt.Key.Key_Tab,QtCore.Qt.Key.Key_Backtab):
            super().keyPressEvent(event); return
        if key in (QtCore.Qt.Key.Key_Control,QtCore.Qt.Key.Key_Shift,QtCore.Qt.Key.Key_Alt,
                   QtCore.Qt.Key.Key_Meta,QtCore.Qt.Key.Key_AltGr):
            event.accept(); return
        value = QtGui.QKeySequence(event.keyCombination()).toString(QtGui.QKeySequence.SequenceFormat.PortableText)
        try:
            self.setText(normalize_hotkey(value))
        except ValueError as exc:
            self.invalidShortcut.emit(str(exc))
        event.accept()
