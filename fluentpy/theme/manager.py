from __future__ import annotations

import weakref

from ..qt import QtCore, Signal, Slot
from .tokens import ThemeMode, ThemeTokens, default_tokens


class _ThemeSubscription(QtCore.QObject):
    """A receiver owned by the widget, so Qt disconnects it on destruction."""

    def __init__(self, owner: QtCore.QObject, method_name: str) -> None:
        super().__init__(owner)
        self._owner_ref = weakref.ref(owner)
        self._method_name = method_name

    @Slot(object)
    def refresh(self, _tokens: object) -> None:
        owner = self._owner_ref()
        if owner is not None:
            getattr(owner, self._method_name)()


class ThemeManager(QtCore.QObject):
    changed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._tokens = default_tokens()
        self._active = True

    def subscribe(self, owner: QtCore.QObject, callback) -> None:
        """Connect a no-argument owner method for the owner's Qt lifetime."""
        receiver = _ThemeSubscription(owner, callback.__name__)
        self.changed.connect(receiver.refresh)

    @property
    def tokens(self) -> ThemeTokens:
        return self._tokens

    @property
    def mode(self) -> ThemeMode:
        return self._tokens.mode

    @property
    def active(self) -> bool:
        return self._active

    def set_mode(self, mode: ThemeMode | str) -> None:
        next_mode = ThemeMode(mode)
        if next_mode is self._tokens.mode:
            return
        self._tokens = ThemeTokens(
            mode=next_mode,
            custom_accent=self._tokens.custom_accent,
            radius=self._tokens.radius,
            spacing=self._tokens.spacing,
            font_size=self._tokens.font_size,
            animation_ms=self._tokens.animation_ms,
        )
        self.changed.emit(self._tokens)

    def toggle(self) -> None:
        self.set_mode(
            ThemeMode.DARK if self._tokens.mode is ThemeMode.LIGHT else ThemeMode.LIGHT
        )

    def set_accent(self, color: str) -> None:
        if color == self._tokens.custom_accent:
            return
        self._tokens = ThemeTokens(
            mode=self._tokens.mode,
            custom_accent=color,
            radius=self._tokens.radius,
            spacing=self._tokens.spacing,
            font_size=self._tokens.font_size,
            animation_ms=self._tokens.animation_ms,
        )
        self.changed.emit(self._tokens)

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        self.changed.emit(self._tokens)


theme = ThemeManager()
