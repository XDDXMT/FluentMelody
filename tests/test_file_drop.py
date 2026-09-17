from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

from fluentpy import LineEdit, TextEdit
from fluentpy.qt import QtCore, QtGui, QtWidgets
from fluentmelody.client.file_drop import MusicFileDropFilter, local_music_paths, music_files


class MusicFileDropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.first = Path(self.directory.name) / '歌曲一.MID'
        self.second = Path(self.directory.name) / '歌曲二.nbs'
        self.third = Path(self.directory.name) / '歌曲三.midi'
        for path in (self.first, self.second, self.third):
            path.write_bytes(b'test score')
        self.window = QtWidgets.QWidget()
        self.window.setAcceptDrops(True)
        layout = QtWidgets.QVBoxLayout(self.window)
        self.log = TextEdit()
        self.log.setReadOnly(True)
        self.field = LineEdit()
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidget(QtWidgets.QLabel('歌曲列表'))
        for widget in (self.log, self.field, self.scroll):
            layout.addWidget(widget)
        self.load = Mock()
        self.message = Mock()
        self.filter = MusicFileDropFilter(self.window, self.load, self.message)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.filter.close()
        self.window.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        self.directory.cleanup()

    def mime(self, *paths):
        mime = QtCore.QMimeData()
        mime.setUrls([QtCore.QUrl.fromLocalFile(str(path)) for path in paths])
        return mime

    def events(self, mime):
        return (
            QtGui.QDragEnterEvent(QtCore.QPoint(4, 4), QtCore.Qt.DropAction.CopyAction,
                                 mime, QtCore.Qt.MouseButton.LeftButton,
                                 QtCore.Qt.KeyboardModifier.NoModifier),
            QtGui.QDragMoveEvent(QtCore.QPoint(4, 4), QtCore.Qt.DropAction.CopyAction,
                                mime, QtCore.Qt.MouseButton.LeftButton,
                                QtCore.Qt.KeyboardModifier.NoModifier),
            QtGui.QDropEvent(QtCore.QPointF(4, 4), QtCore.Qt.DropAction.CopyAction,
                            mime, QtCore.Qt.MouseButton.LeftButton,
                            QtCore.Qt.KeyboardModifier.NoModifier),
        )

    def drop(self, widget, mime):
        events = self.events(mime)
        for event in events:
            self.app.sendEvent(widget, event)
        return events

    def test_real_drag_events_load_from_window_and_child_controls(self):
        targets = (self.window, self.log.viewport(), self.field, self.scroll.viewport())
        for target in targets:
            with self.subTest(target=type(target).__name__):
                self.load.reset_mock()
                events = self.drop(target, self.mime(self.first))
                self.load.assert_called_once_with(self.first.resolve())
                self.assertTrue(all(event.isAccepted() for event in events))
                self.assertEqual(self.field.text(), '')
                self.assertEqual(self.log.toPlainText(), '')

    def test_multiple_files_use_first_supported_and_notify_once(self):
        unrelated = Path(self.directory.name) / '说明.txt'
        unrelated.write_text('notes', encoding='utf-8')
        self.drop(self.field, self.mime(unrelated, self.second, self.first, self.second))
        self.load.assert_called_once_with(self.second.resolve())
        self.message.assert_called_once()
        self.assertIn('2 个', self.message.call_args.args[0])

    def test_repeated_drop_is_consumed_once_but_new_drag_can_reload(self):
        mime = self.mime(self.third)
        enter, move, drop = self.events(mime)
        for event in (enter, move, drop, drop):
            self.app.sendEvent(self.window, event)
        self.load.assert_called_once_with(self.third.resolve())
        self.drop(self.window, mime)
        self.assertEqual(self.load.call_count, 2)

    def test_file_validation_excludes_directory_missing_and_remote_urls(self):
        directory = Path(self.directory.name) / '假歌曲.nbs'
        directory.mkdir()
        mime = self.mime(directory, directory / '不存在.mid')
        mime.setUrls(mime.urls() + [
            QtCore.QUrl('https://example.invalid/song.mid'),
            QtCore.QUrl('file://other-computer/music/song.nbs'),
        ])
        self.assertEqual(music_files(mime), ())
        self.drop(self.window, mime)
        self.load.assert_not_called()

    def test_other_window_and_normal_text_drop_keep_existing_behavior(self):
        other = QtWidgets.QLineEdit()
        other.show()
        try:
            self.drop(other, self.mime(self.first))
            self.load.assert_not_called()
            mime = QtCore.QMimeData()
            mime.setText('我的昵称')
            self.drop(self.field, mime)
            self.assertEqual(self.field.text(), '我的昵称')
            self.load.assert_not_called()
        finally:
            other.close()
            other.deleteLater()

    def test_close_removes_filter_and_is_idempotent(self):
        self.filter.close()
        self.filter.close()
        self.drop(self.window, self.mime(self.first))
        self.load.assert_not_called()

    def require_native_windows(self):
        if self.app.platformName().lower() != 'windows':
            self.skipTest('Windows native drop requires the Qt windows platform, not offscreen/minimal')

    @unittest.skipUnless(sys.platform == 'win32', 'Windows shell drop API')
    def test_native_hdrop_unicode_parsing_and_qt_native_deduplication(self):
        self.require_native_windows()
        import ctypes
        from ctypes import wintypes
        native = self.filter._native
        self.assertIsNotNone(native, 'Native fallback must initialize on Windows')
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel.GlobalLock.restype = ctypes.c_void_p
        kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel.GlobalUnlock.restype = wintypes.BOOL

        class DROPFILES(ctypes.Structure):
            _fields_ = [('pFiles', wintypes.DWORD), ('pt', wintypes.POINT),
                        ('fNC', wintypes.BOOL), ('fWide', wintypes.BOOL)]

        paths = [self.first, self.second]
        header = DROPFILES(ctypes.sizeof(DROPFILES), wintypes.POINT(0, 0), False, True)
        names = ('\0'.join(str(path) for path in paths) + '\0\0').encode('utf-16-le')
        payload = bytes(header) + names
        handle = kernel.GlobalAlloc(0x0042, len(payload))
        self.assertTrue(handle)
        address = kernel.GlobalLock(handle)
        self.assertTrue(address)
        ctypes.memmove(address, payload, len(payload))
        kernel.GlobalUnlock(handle)
        msg = wintypes.MSG()
        msg.hWnd = native._hwnd
        msg.message = native.WM_DROPFILES
        msg.wParam = handle
        # Calls the actual DragQueryFileW/DragFinish API on an owned HDROP;
        # no OS input events, external windows, or user's files are modified.
        self.assertEqual(native.nativeEventFilter(b'windows_generic_MSG', ctypes.addressof(msg)), (True, 0))
        self.load.assert_called_once_with(self.first.resolve())
        self.message.assert_called_once()
        self.drop(self.window, self.mime(*paths))
        self.load.assert_called_once_with(self.first.resolve())
        # A subsequent native drop is a separate gesture, even with the same song.
        self.filter._deliver(tuple(path.resolve() for path in paths), 'native')
        self.assertEqual(self.load.call_count, 2)

    def test_unc_paths_are_rejected_without_touching_the_filesystem(self):
        from unittest.mock import patch
        with patch.object(Path, 'is_file', side_effect=AssertionError('Network access forbidden')):
            self.assertEqual(local_music_paths([r'\\other-computer\share\song.mid']), ())

    @unittest.skipUnless(sys.platform == 'win32', 'Windows shell drop API')
    def test_ole_is_revoked_only_for_elevated_target_window(self):
        self.require_native_windows()
        native = self.filter._native
        self.assertIsNotNone(native)
        real_ole = native._ole32
        was_elevated = native._elevated
        try:
            native._ole32 = Mock()
            native._ole32.RevokeDragDrop.return_value = 0
            native._elevated = False
            native.refresh_window(force=True)
            native._ole32.RevokeDragDrop.assert_not_called()
            native._elevated = True
            native.refresh_window(force=True)
            native._ole32.RevokeDragDrop.assert_called_once_with(int(self.window.winId()))
            native._ole32.RevokeDragDrop.return_value = -2147221248  # Already unregistered
            native.refresh_window(force=True)
        finally:
            native._ole32 = real_ole
            native._elevated = was_elevated

    @unittest.skipUnless(sys.platform == 'win32', 'Windows shell drop API')
    def test_native_messages_for_other_windows_are_untouched(self):
        self.require_native_windows()
        import ctypes
        from ctypes import wintypes
        native = self.filter._native
        self.assertIsNotNone(native)
        msg = wintypes.MSG()
        msg.hWnd = native._hwnd + 1
        msg.message = native.WM_DROPFILES
        msg.wParam = 0
        self.assertEqual(native.nativeEventFilter(b'windows_generic_MSG', ctypes.addressof(msg)), (False, 0))
        self.load.assert_not_called()


if __name__ == '__main__':
    unittest.main()
