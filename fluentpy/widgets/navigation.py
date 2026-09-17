from __future__ import annotations

from dataclasses import dataclass

from ..qt import QtCore, QtGui, QtWidgets, Signal
from ..theme import theme


class NavigationItem(QtWidgets.QWidget):
    clicked = Signal(bool)

    EXPAND_WIDTH = 312

    def __init__(
        self,
        icon: QtGui.QIcon,
        text: str = "",
        selectable: bool = True,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._icon = icon
        self._text = text
        self._selectable = selectable
        self._compacted = True
        self._selected = False
        self._about_selected = False
        self._pressed = False
        self._hover = False
        self.setFixedSize(40, 36)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setToolTip(text)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        theme.subscribe(self, self.update)

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:
        self._text = text
        self.setToolTip(text)
        self.update()

    def setIcon(self, icon: QtGui.QIcon) -> None:
        self._icon = icon
        self.update()

    def setCompacted(self, compacted: bool) -> None:
        if compacted == self._compacted:
            return
        self._compacted = compacted
        self.setFixedSize(40, 36) if compacted else self.setFixedSize(self.EXPAND_WIDTH, 36)
        self.update()

    def setSelected(self, selected: bool) -> None:
        if not self._selectable:
            return
        self._selected = selected
        self._about_selected = False
        self.update()

    def setAboutSelected(self, selected: bool) -> None:
        self._about_selected = selected
        self.update()

    def indicatorRect(self) -> QtCore.QRectF:
        return QtCore.QRectF(0, 10, 3, 16)

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self._hover = False
        self._pressed = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        was_pressed = self._pressed
        self._pressed = False
        self.update()
        if was_pressed and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(True)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        tokens = theme.tokens
        painter = QtGui.QPainter(self)
        painter.setRenderHints(
            QtGui.QPainter.RenderHint.Antialiasing
            | QtGui.QPainter.RenderHint.TextAntialiasing
            | QtGui.QPainter.RenderHint.SmoothPixmapTransform
        )
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        if self._pressed:
            painter.setOpacity(0.7)
        if not self.isEnabled():
            painter.setOpacity(0.4)

        base = 255 if tokens.mode.value == "dark" else 0
        if self._selected:
            painter.setBrush(QtGui.QColor(base, base, base, 6 if self._hover else 10))
            painter.drawRoundedRect(QtCore.QRectF(self.rect()), 5, 5)
        elif (self._hover or self._about_selected) and self.isEnabled():
            painter.setBrush(QtGui.QColor(base, base, base, 6 if self._about_selected else 10))
            painter.drawRoundedRect(QtCore.QRectF(self.rect()), 5, 5)

        icon_rect = QtCore.QRectF(11.5, 10, 16, 16)
        if self._icon.isNull():
            self._draw_menu_icon(painter, icon_rect)
        else:
            self._draw_tinted_icon(painter, icon_rect.toRect())

        if self._compacted:
            return

        font = QtGui.QFont("Microsoft YaHei UI")
        font.setPointSize(10)
        painter.setFont(font)
        painter.setPen(QtGui.QColor(tokens.text))
        painter.drawText(
            QtCore.QRectF(44, 0, self.width() - 57, self.height()),
            QtCore.Qt.AlignmentFlag.AlignVCenter,
            self._text,
        )

    def _draw_menu_icon(self, painter: QtGui.QPainter, rect: QtCore.QRectF) -> None:
        painter.save()
        painter.setPen(
            QtGui.QPen(
                QtGui.QColor(theme.tokens.text),
                1.55,
                QtCore.Qt.PenStyle.SolidLine,
                QtCore.Qt.PenCapStyle.RoundCap,
            )
        )
        for y in (rect.top() + 4, rect.center().y(), rect.bottom() - 3):
            painter.drawLine(QtCore.QLineF(rect.left() + 1, y, rect.right() - 1, y))
        painter.restore()

    def _draw_tinted_icon(self, painter: QtGui.QPainter, rect: QtCore.QRect) -> None:
        pixmap = QtGui.QPixmap(rect.size())
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        icon_painter = QtGui.QPainter(pixmap)
        self._icon.paint(icon_painter, QtCore.QRect(QtCore.QPoint(0, 0), rect.size()))
        icon_painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_SourceIn)
        icon_painter.fillRect(pixmap.rect(), QtGui.QColor(theme.tokens.text))
        icon_painter.end()
        painter.drawPixmap(rect, pixmap)


class NavigationSeparator(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._compacted = True
        self.setFixedSize(48, 3)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        theme.subscribe(self, self.update)

    def setCompacted(self, compacted: bool) -> None:
        self._compacted = compacted
        self.setFixedSize(48 if compacted else NavigationItem.EXPAND_WIDTH + 10, 3)
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        base = 255 if theme.tokens.mode.value == "dark" else 0
        painter.setPen(QtGui.QPen(QtGui.QColor(base, base, base, 15), 1))
        painter.drawLine(0, 1, self.width(), 1)


class NavigationReturnButton(NavigationItem):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(QtGui.QIcon(), "", selectable=False, parent=parent)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        color = QtGui.QColor(theme.tokens.text)
        painter.setPen(QtGui.QPen(color, 1.45, QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenCapStyle.RoundCap))
        center_y = self.height() / 2
        painter.drawLine(QtCore.QLineF(24, center_y - 6, 17, center_y))
        painter.drawLine(QtCore.QLineF(17, center_y, 24, center_y + 6))
        painter.drawLine(QtCore.QLineF(18, center_y, 31, center_y))


class _NavigationIndicator(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        theme.subscribe(self, self.update)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor(theme.tokens.accent))
        rect = QtCore.QRectF(self.rect()).adjusted(0, 0, -0.5, -0.5)
        painter.drawRoundedRect(rect, rect.width() / 2, rect.width() / 2)


class NavigationSidebar(QtWidgets.QFrame):
    currentChanged = Signal(int)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("navigationSidebar")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self._expanded = False
        self._collapsed_width = 48
        self._expanded_width = 322
        self._items: list[NavigationItem] = []
        self._item_visibility: list[bool] = []
        self._widgets: list[QtWidgets.QWidget] = []
        self._current_index = -1
        self.setFixedWidth(self._collapsed_width)
        self._indicator = _NavigationIndicator(self)
        self._indicator.setGeometry(4, 15, 3, 16)
        self._indicator.hide()
        self._indicator_animation = QtCore.QPropertyAnimation(self._indicator, b"geometry", self)
        self._indicator_animation.setDuration(210)
        self._indicator_animation.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)

        self._width_animation = QtCore.QPropertyAnimation(self, b"minimumWidth", self)
        self._width_animation.setDuration(150)
        self._width_animation.setEasingCurve(QtCore.QEasingCurve.Type.OutQuad)
        self._width_animation.valueChanged.connect(self._sync_width)

        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(0, 5, 0, 5)
        self._layout.setSpacing(4)

        self._top_layout = QtWidgets.QVBoxLayout()
        self._top_layout.setContentsMargins(4, 0, 4, 0)
        self._top_layout.setSpacing(4)
        self._bottom_layout = QtWidgets.QVBoxLayout()
        self._bottom_layout.setContentsMargins(4, 0, 4, 0)
        self._bottom_layout.setSpacing(4)

        self._return = NavigationReturnButton(self)
        self._return.hide()

        self._toggle = NavigationItem(QtGui.QIcon(), "菜单", selectable=False, parent=self)
        self._toggle.clicked.connect(lambda _by_user=True: self.toggle())

        self._layout.addLayout(self._top_layout, 0)
        self._layout.addStretch(1)
        self._layout.addLayout(self._bottom_layout, 0)
        self._top_layout.addWidget(self._return, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        self._top_layout.addWidget(self._toggle, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        self._widgets.extend([self._return, self._toggle])

        theme.subscribe(self, self.update)
        self._sync_widgets()

    @property
    def expanded(self) -> bool:
        return self._expanded

    def add_item(self, icon: QtGui.QIcon, text: str) -> NavigationItem:
        return self._add_item(icon, text, self._top_layout)

    def add_footer_item(self, icon: QtGui.QIcon, text: str) -> NavigationItem:
        return self._add_item(icon, text, self._bottom_layout)

    def add_separator(self) -> None:
        separator = NavigationSeparator(self)
        self._top_layout.addWidget(separator, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        self._widgets.append(separator)
        separator.setCompacted(not self._expanded)

    def set_current_index(self, index: int, animated: bool = True) -> None:
        if not self.is_item_visible(index) or index == self._current_index:
            return
        previous_index = self._current_index
        self._current_index = index
        for item_index, item in enumerate(self._items):
            item.setSelected(item_index == index)
        self._move_indicator(index, animated=animated and previous_index >= 0)
        self.currentChanged.emit(index)

    def is_item_visible(self, index: int) -> bool:
        """Return configured visibility, including when the window is hidden."""
        return 0 <= index < len(self._items) and self._item_visibility[index]

    def set_item_visible(self, index: int, visible: bool) -> None:
        """Hide an entry without removing its page index or layout position."""
        if index < 0 or index >= len(self._items):
            return
        visible = bool(visible)
        if self._item_visibility[index] == visible:
            return
        self._item_visibility[index] = visible
        self._items[index].setVisible(visible)
        self._layout.invalidate()
        self._layout.activate()
        if not self.is_item_visible(self._current_index):
            replacement = next((i for i, enabled in enumerate(self._item_visibility) if enabled), -1)
            if replacement >= 0:
                self.set_current_index(replacement, animated=False)
            else:
                self._current_index = -1
                for item in self._items:
                    item.setSelected(False)
                self.currentChanged.emit(-1)
        self._refresh_indicator()

    def toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded_width(self, width: int) -> None:
        """Set the expanded width without changing the default appearance."""
        if not isinstance(width, int) or width < 120:
            raise ValueError("Expanded navigation width must be at least 120 pixels")
        self._expanded_width = width
        if self._expanded:
            self._width_animation.stop()
            self._sync_width(width)

    def set_expanded(self, expanded: bool) -> None:
        if expanded == self._expanded:
            return
        self._expanded = expanded
        start = self.width()
        end = self._expanded_width if expanded else self._collapsed_width
        self._width_animation.stop()
        self._width_animation.setStartValue(start)
        self._width_animation.setEndValue(end)
        self._width_animation.start()
        self._sync_widgets()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        tokens = theme.tokens
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        rect = QtCore.QRectF(self.rect())
        if tokens.mode.value == "light":
            color = QtGui.QColor(255, 255, 255, 180 if theme.active else 220)
        else:
            color = QtGui.QColor(32, 32, 32, 200 if theme.active else 230)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRect(rect)

        if self._expanded:
            tint = QtGui.QLinearGradient(rect.topLeft(), rect.bottomLeft())
            if tokens.mode.value == "light":
                tint.setColorAt(0, QtGui.QColor(255, 255, 255, 185))
                tint.setColorAt(1, QtGui.QColor(240, 248, 251, 170))
            else:
                tint.setColorAt(0, QtGui.QColor(22, 24, 26, 214))
                tint.setColorAt(1, QtGui.QColor(16, 21, 25, 204))
            painter.setBrush(tint)
            painter.drawRect(rect)

        divider = QtGui.QColor(0, 0, 0, 15) if tokens.mode.value == "light" else QtGui.QColor(255, 255, 255, 18)
        painter.setPen(QtGui.QPen(divider, 1))
        painter.drawLine(QtCore.QLineF(rect.right() - 0.5, 0, rect.right() - 0.5, rect.bottom()))

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_indicator"):
            self._refresh_indicator()

    def event(self, event: QtCore.QEvent) -> bool:
        result = super().event(event)
        if event.type() == QtCore.QEvent.Type.LayoutRequest and hasattr(self, "_indicator"):
            self._refresh_indicator()
        return result

    def _add_item(
        self,
        icon: QtGui.QIcon,
        text: str,
        layout: QtWidgets.QVBoxLayout,
    ) -> NavigationItem:
        item = NavigationItem(icon, text, parent=self)
        item.setCompacted(not self._expanded)
        index = len(self._items)
        item.clicked.connect(lambda _by_user=True, item_index=index: self.set_current_index(item_index))
        self._items.append(item)
        self._item_visibility.append(True)
        self._widgets.append(item)
        layout.addWidget(item, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        if self._current_index == -1:
            self.set_current_index(index)
        return item

    def _indicator_target(self, index: int) -> QtCore.QRect:
        item = self._items[index]
        pos = item.mapTo(self, QtCore.QPoint(0, 0))
        return QtCore.QRect(pos.x(), pos.y() + 10, 3, 16)

    def _move_indicator(self, index: int, animated: bool) -> None:
        if not self.is_item_visible(index):
            self._indicator_animation.stop()
            self._indicator.hide()
            return
        target = self._indicator_target(index)
        self._indicator.show()
        self._indicator.raise_()
        if not animated:
            self._indicator_animation.stop()
            self._indicator.setGeometry(target)
            return
        self._indicator_animation.stop()
        self._indicator_animation.setStartValue(self._indicator.geometry())
        self._indicator_animation.setEndValue(target)
        self._indicator_animation.start()

    def _sync_width(self, value: object) -> None:
        width = int(value)
        self.setMinimumWidth(width)
        self.setMaximumWidth(width)

    def _sync_widgets(self) -> None:
        compacted = not self._expanded
        for widget in self._widgets:
            if hasattr(widget, "setCompacted"):
                widget.setCompacted(compacted)  # type: ignore[attr-defined]
        self._refresh_indicator()

    def _refresh_indicator(self) -> None:
        self._move_indicator(self._current_index, animated=False)


class AnimatedStackedWidget(QtWidgets.QStackedWidget):
    currentAboutToChange = Signal(int, int)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._duration = 180
        self._animation: QtCore.QAbstractAnimation | None = None
        self.setObjectName("animatedStackedWidget")

    def setAnimationDuration(self, duration_ms: int) -> None:
        self._duration = max(0, duration_ms)

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802 - Qt API
        self.setCurrentIndexAnimated(index)

    def setCurrentWidget(self, widget: QtWidgets.QWidget) -> None:  # noqa: N802 - Qt API
        self.setCurrentIndexAnimated(self.indexOf(widget))

    def setCurrentIndexAnimated(self, index: int) -> None:
        if index < 0 or index >= self.count() or index == self.currentIndex():
            return
        old_index = self.currentIndex()
        old_widget = self.currentWidget()
        new_widget = self.widget(index)
        self.currentAboutToChange.emit(old_index, index)

        if self._animation:
            self._animation.stop()
            self._animation.deleteLater()
            self._animation = None

        if self._duration <= 0 or old_widget is None:
            QtWidgets.QStackedWidget.setCurrentIndex(self, index)
            return

        direction = 1 if index > old_index else -1
        offset = 22 * direction
        effect = QtWidgets.QGraphicsOpacityEffect(new_widget)
        effect.setOpacity(0.0)
        new_widget.setGraphicsEffect(effect)
        end_pos = new_widget.pos()
        start_pos = end_pos + QtCore.QPoint(offset, 0)
        new_widget.move(start_pos)
        QtWidgets.QStackedWidget.setCurrentIndex(self, index)

        opacity = QtCore.QPropertyAnimation(effect, b"opacity", self)
        opacity.setStartValue(0.0)
        opacity.setEndValue(1.0)
        opacity.setDuration(self._duration)
        opacity.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)

        slide = QtCore.QPropertyAnimation(new_widget, b"pos", self)
        slide.setStartValue(start_pos)
        slide.setEndValue(end_pos)
        slide.setDuration(self._duration)
        slide.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)

        group = QtCore.QParallelAnimationGroup(self)
        group.addAnimation(opacity)
        group.addAnimation(slide)
        group.finished.connect(lambda: self._finish_transition(new_widget, effect, group))
        self._animation = group
        group.start()

    def _finish_transition(
        self,
        widget: QtWidgets.QWidget,
        effect: QtWidgets.QGraphicsOpacityEffect,
        animation: QtCore.QParallelAnimationGroup,
    ) -> None:
        effect.setOpacity(1.0)
        widget.setGraphicsEffect(None)
        if self._animation is animation:
            self._animation = None
        animation.deleteLater()


@dataclass(frozen=True)
class NavigationPage:
    route_key: str
    widget: QtWidgets.QWidget
    item: NavigationItem


class NavigationView(QtWidgets.QWidget):
    currentChanged = Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.sidebar = NavigationSidebar(self)
        self.stack = AnimatedStackedWidget(self)
        self._pages: list[NavigationPage] = []
        self._routes: dict[str, NavigationPage] = {}

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.sidebar)
        layout.addWidget(self.stack, 1)
        self.sidebar.currentChanged.connect(self._on_sidebar_current_changed)

    def add_sub_interface(
        self,
        widget: QtWidgets.QWidget,
        icon: QtGui.QIcon,
        text: str,
        route_key: str | None = None,
        position: str = "top",
    ) -> NavigationItem:
        key = route_key or widget.objectName() or self._make_route_key(text)
        if key in self._routes:
            raise ValueError(f"Navigation route already exists: {key}")

        widget.setObjectName(key)
        item = self.sidebar.add_footer_item(icon, text) if position == "bottom" else self.sidebar.add_item(icon, text)
        page = NavigationPage(key, widget, item)
        self._pages.append(page)
        self._routes[key] = page
        self.stack.addWidget(widget)
        if len(self._pages) == 1:
            self.sidebar.set_current_index(0)
            QtWidgets.QStackedWidget.setCurrentIndex(self.stack, 0)
        elif not self.current_route():
            self.set_current_route(key, animated=False)
        return item

    def addSubInterface(
        self,
        widget: QtWidgets.QWidget,
        icon: QtGui.QIcon,
        text: str,
        routeKey: str | None = None,
        position: str = "top",
    ) -> NavigationItem:
        return self.add_sub_interface(widget, icon, text, routeKey, position)

    def add_separator(self) -> None:
        self.sidebar.add_separator()

    def set_current_route(self, route_key: str, animated: bool = True) -> None:
        page = self._routes.get(route_key)
        if not page or not self.is_route_visible(route_key):
            return
        self.stack.show()
        index = self._pages.index(page)
        if self.stack.currentIndex() == index:
            self.sidebar.set_current_index(index, animated=False)
            return
        if animated:
            self.sidebar.set_current_index(index)
            return

        was_blocked = self.sidebar.blockSignals(True)
        self.sidebar.set_current_index(index, animated=False)
        self.sidebar.blockSignals(was_blocked)
        QtWidgets.QStackedWidget.setCurrentIndex(self.stack, index)
        self.currentChanged.emit(route_key)

    def setCurrentRoute(self, routeKey: str, animated: bool = True) -> None:
        self.set_current_route(routeKey, animated)

    def current_route(self) -> str:
        index = self.stack.currentIndex()
        return self._pages[index].route_key if self.sidebar.is_item_visible(index) else ""

    def is_route_visible(self, route_key: str) -> bool:
        """Report route availability independently of window visibility."""
        page = self._routes.get(route_key)
        return page is not None and self.sidebar.is_item_visible(self._pages.index(page))

    def set_route_visible(self, route_key: str, visible: bool) -> None:
        """Show or hide a route, preserving its widget and navigation order.

        Hiding the active route selects the first available route. If none
        remain, the content area is hidden until a route becomes available.
        Unknown routes are ignored, matching :meth:`set_current_route`.
        """
        page = self._routes.get(route_key)
        if page is not None:
            self.sidebar.set_item_visible(self._pages.index(page), visible)

    def setRouteVisible(self, routeKey: str, visible: bool) -> None:
        self.set_route_visible(routeKey, visible)

    def isRouteVisible(self, routeKey: str) -> bool:
        return self.is_route_visible(routeKey)

    def _on_sidebar_current_changed(self, index: int) -> None:
        if index == -1:
            self.stack.hide()
            self.currentChanged.emit("")
            return
        if index < 0 or index >= len(self._pages):
            return
        if not self.sidebar.is_item_visible(index):
            return
        self.stack.show()
        self.stack.setCurrentIndexAnimated(index)
        self.currentChanged.emit(self._pages[index].route_key)

    def _make_route_key(self, text: str) -> str:
        base = "".join(ch.lower() if ch.isalnum() else "_" for ch in text).strip("_") or "page"
        key = base
        suffix = 2
        while key in self._routes:
            key = f"{base}_{suffix}"
            suffix += 1
        return key
