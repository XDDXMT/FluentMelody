"""Adapt arrangement dictionaries to FluentPy's format-independent overview."""

from fluentpy import NoteEvent, NoteTimeline


class NoteView(NoteTimeline):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._plan = None
        self.setMinimumHeight(78)
        self.setMaximumHeight(110)
        self.set_pitch_range(48, 85)
        self.set_empty_text('载入歌曲后，音符会显示在这里')

    @property
    def plan(self):
        return self._plan

    @plan.setter
    def plan(self, plan):
        if plan is None:
            self.clear()
        else:
            self.set_notes(
                (NoteEvent(event['start'], event['duration'], event['midi'], track_index)
                 for track_index, track in enumerate(plan['tracks'])
                 for event in track['events']),
                duration=plan['duration'],
            )
        self._plan = plan
