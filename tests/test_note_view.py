"""The application adapts score data without implementing another renderer."""

import unittest

from fluentpy import NoteEvent, NoteTimeline
from fluentpy.qt import QtCore, QtWidgets
from fluentmelody.client.note_view import NoteView


class NoteViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.view = NoteView()
        self.plan = {
            'duration': 3.0,
            'tracks': [
                {'events': [{'start': 0.0, 'duration': 0.5, 'midi': 48}]},
                {'events': [{'start': 1.0, 'duration': 0.25, 'midi': 85}]},
            ],
        }

    def tearDown(self):
        self.view.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)

    def test_arrangement_uses_public_widget_and_keeps_track_assignment(self):
        self.assertIsInstance(self.view, NoteTimeline)
        self.view.plan = self.plan
        self.assertIs(self.view.plan, self.plan)
        self.assertEqual(self.view.notes(), (NoteEvent(0, .5, 48, 0), NoteEvent(1, .25, 85, 1)))
        self.assertEqual(self.view.duration(), 3.0)
        self.assertEqual(self.view.pitch_range(), (48, 85))
        self.view.set_position(2.0)
        self.assertEqual(self.view.position(), 2.0)

    def test_reconversion_clears_notes_and_cursor_but_keeps_display_settings(self):
        self.view.plan = self.plan
        self.view.set_position(1.0)
        self.view.plan = None
        self.assertIsNone(self.view.plan)
        self.assertEqual(self.view.notes(), ())
        self.assertEqual(self.view.duration(), 0)
        self.assertEqual(self.view.position(), 0)
        self.assertEqual(self.view.pitch_range(), (48, 85))
        self.assertIn('载入歌曲', self.view.empty_text())
        self.view.plan = self.plan
        self.assertEqual(self.view.position(), 0)

    def test_invalid_replacement_keeps_previous_score_and_overview(self):
        self.view.plan = self.plan
        self.view.set_position(1.0)
        with self.assertRaises(ValueError):
            self.view.plan = {'duration': 1, 'tracks': [
                {'events': [{'start': 0, 'duration': .5, 'midi': 200}]},
            ]}
        self.assertIs(self.view.plan, self.plan)
        self.assertEqual(len(self.view.notes()), 2)
        self.assertEqual(self.view.position(), 1)


if __name__ == '__main__':
    unittest.main()
