import contextlib
import io
import sys
import unittest

from fluentpy import FluentWindow, Card, LineEdit, Toast, NavigationSidebar, theme
from fluentpy.qt import QtWidgets, QtCore

class LifetimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_destroyed_widgets_disconnect_theme(self):
        errors=[]
        previous=sys.excepthook
        sys.excepthook=lambda typ,exc,tb:errors.append(exc)
        try:
            for _ in range(3):
                window=FluentWindow()
                window.content_layout.addWidget(Card())
                window.content_layout.addWidget(LineEdit())
                toast=Toast.success('已保存',parent=window,timeout_ms=100000)
                self.assertTrue(toast.testAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose))
                toast.close()
                window.deleteLater()
                self.app.sendPostedEvents(None,QtCore.QEvent.Type.DeferredDelete)
                theme.toggle()
                self.app.processEvents()
            self.assertEqual(errors,[])
        finally:
            sys.excepthook=previous

    def test_navigation_custom_width_preserves_collapse(self):
        sidebar=NavigationSidebar()
        sidebar.set_expanded_width(172)
        sidebar.set_expanded(True)
        sidebar._width_animation.setCurrentTime(150)
        self.assertEqual(sidebar.width(),172)
        sidebar.set_expanded(False)
        sidebar._width_animation.setCurrentTime(150)
        self.assertEqual(sidebar.width(),48)
        sidebar.deleteLater()

if __name__=='__main__':
    unittest.main()
