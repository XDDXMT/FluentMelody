import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest

from fluentpy.qt import QtCore, QtGui, QtWidgets
from fluentpy.widgets.navigation import NavigationView


class NavigationVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.view = NavigationView()
        self.view.stack.setAnimationDuration(0)
        self.items = {}
        self.pages = {}
        for key, position in [('solo', 'top'), ('room', 'top'), ('ai', 'top'), ('settings', 'bottom')]:
            self.pages[key] = QtWidgets.QLabel(key)
            self.items[key] = self.view.add_sub_interface(self.pages[key], QtGui.QIcon(), key, key, position)
        self.view.resize(600, 500)
        self.view.show()
        self.app.processEvents()

    def tearDown(self):
        self.view.close()
        self.view.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)

    def settle(self):
        self.app.processEvents()
        self.view.sidebar.layout().activate()
        self.app.processEvents()

    def test_hiding_preserves_page_objects_and_visual_order(self):
        self.view.set_route_visible('room', False)
        self.view.set_route_visible('ai', False)
        self.settle()
        self.assertTrue(self.items['room'].isHidden())
        self.assertTrue(self.items['ai'].isHidden())
        self.assertEqual(self.view.stack.count(), 4)
        self.assertIs(self.view.stack.widget(1), self.pages['room'])
        self.view.set_route_visible('ai', True)
        self.view.set_route_visible('room', True)
        self.settle()
        self.assertLess(self.items['solo'].y(), self.items['room'].y())
        self.assertLess(self.items['room'].y(), self.items['ai'].y())
        self.assertLess(self.items['ai'].y(), self.items['settings'].y())

    def test_hidden_routes_reject_navigation_and_click_signals(self):
        self.view.set_route_visible('room', False)
        self.view.set_current_route('room')
        self.view.setCurrentRoute('room', animated=False)
        self.view.sidebar.set_current_index(1)
        self.items['room'].clicked.emit(True)
        self.assertEqual(self.view.current_route(), 'solo')
        self.assertIs(self.view.stack.currentWidget(), self.pages['solo'])

    def test_hiding_current_route_selects_visible_fallback(self):
        self.view.set_current_route('ai', animated=False)
        changed = []
        self.view.currentChanged.connect(changed.append)
        self.view.set_route_visible('solo', False)
        self.view.set_route_visible('ai', False)
        self.settle()
        self.assertEqual(self.view.current_route(), 'room')
        self.assertEqual(changed, ['room'])
        self.assertTrue(self.items['room']._selected)
        self.assertFalse(self.items['ai']._selected)
        sidebar = self.view.sidebar
        self.assertEqual(sidebar._indicator.geometry(), sidebar._indicator_target(1))

    def test_indicator_tracks_layout_changes_for_existing_selection(self):
        self.view.set_current_route('ai', animated=False)
        self.settle()
        old_y = self.view.sidebar._indicator.y()
        self.view.set_route_visible('room', False)
        self.settle()
        self.assertLess(self.view.sidebar._indicator.y(), old_y)
        self.assertEqual(self.view.sidebar._indicator.geometry(), self.view.sidebar._indicator_target(2))
        self.view.set_current_route('settings', animated=False)
        self.view.resize(600, 700)
        self.settle()
        self.assertEqual(self.view.sidebar._indicator.geometry(), self.view.sidebar._indicator_target(3))

    def test_all_hidden_clears_content_and_restoring_selects_page(self):
        for route in self.items:
            self.view.set_route_visible(route, False)
        self.settle()
        self.assertEqual(self.view.current_route(), '')
        self.assertTrue(self.view.stack.isHidden())
        self.assertTrue(self.view.sidebar._indicator.isHidden())
        self.assertTrue(all(not item._selected for item in self.items.values()))
        self.view.set_route_visible('settings', True)
        self.settle()
        self.assertEqual(self.view.current_route(), 'settings')
        self.assertFalse(self.view.stack.isHidden())
        self.assertFalse(self.view.sidebar._indicator.isHidden())

    def test_new_page_can_be_added_after_all_routes_hidden(self):
        for route in self.items:
            self.view.set_route_visible(route, False)
        about = QtWidgets.QLabel('about')
        self.view.add_sub_interface(about, QtGui.QIcon(), 'about', 'about', 'bottom')
        self.settle()
        self.assertEqual(self.view.current_route(), 'about')
        self.assertIs(self.view.stack.currentWidget(), about)
        self.assertFalse(self.view.stack.isHidden())

    def test_route_visibility_is_independent_of_parent_window(self):
        self.view.hide()
        self.assertTrue(self.view.is_route_visible('room'))
        self.view.setRouteVisible('room', False)
        self.assertFalse(self.view.isRouteVisible('room'))
        self.view.setRouteVisible('room', True)
        self.assertTrue(self.view.isRouteVisible('room'))

    def test_unknown_or_repeated_requests_do_not_change_current_route(self):
        changed = []
        self.view.currentChanged.connect(changed.append)
        self.view.set_route_visible('missing', False)
        self.view.set_route_visible('solo', True)
        self.assertFalse(self.view.is_route_visible('missing'))
        self.assertEqual(self.view.current_route(), 'solo')
        self.assertEqual(changed, [])


if __name__ == '__main__':
    unittest.main()
