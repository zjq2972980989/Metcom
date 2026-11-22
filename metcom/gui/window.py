"""自绘无边框主窗口：标题栏 + 侧边导航 + 页面堆栈 + 右侧日志抽屉。

外壳完全自绘，换取三件在原生窗口上做不到的事：

  1. 16px 圆角 + 外阴影 + 1px 高光描边，整体像一块浮在桌面上的面板；
  2. 标题栏里放得下前后导航、面包屑、主题开关与运行指示灯；
  3. 日志区从"底部占一大块"改成"右侧抽屉"——收起时只有 44px，
     运行任务时自动展开，把版面还给图形化内容。

拖动走系统原生 ``startSystemMove``（Windows 上手感与系统窗口一致），
八向缩放走 ``startSystemResize``，最大化时自动去圆角、去阴影、去外边距。
"""

from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QEvent, QRectF, Qt, QTimer,
                            QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                               QProgressBar, QPushButton, QStackedWidget,
                               QVBoxLayout, QWidget)

from metcom.gui import icons, theme
from metcom.gui.components.console import LogConsole
from metcom.gui.components.sidebar import Sidebar
from metcom.gui.state import NAV_ITEMS

MARGIN = 10
RADIUS = 16
TITLE_H = 46
GRIP = 9
DRAWER_COLLAPSED = 44
DRAWER_EXPANDED = 320


class _VerticalTab(QWidget):
    """日志抽屉侧面的竖排拉手（自绘，文字旋转 90 度）。"""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("展开 / 收起运行日志")
        self._title = "运行日志"
        self._open = False

    def set_open(self, value: bool) -> None:
        if self._open != bool(value):
            self._open = bool(value)
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if (event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.position().toPoint())):
            self.clicked.emit()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        palette = theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        try:
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            painter.setPen(QPen(QColor(palette.accent if self._open
                                       else palette.border), 1.0))
            painter.setBrush(QColor(palette.surface))
            painter.drawRoundedRect(rect, 9.0, 9.0)
            painter.translate(self.width() / 2.0, self.height() / 2.0)
            painter.rotate(-90)
            painter.setPen(QColor(palette.accent if self._open
                                  else palette.text_muted))
            font = QFont(theme.pick_family(theme.UI_FONT_CANDIDATES), 10)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(QRectF(-90, -11, 180, 22),
                             Qt.AlignmentFlag.AlignCenter, self._title)
        finally:
            painter.end()


class TitleBar(QFrame):
    """自绘标题栏：前后导航 + 面包屑 + 主题开关 + 运行指示灯 + 窗口按钮。"""

    back = Signal()
    forward = Signal()
    theme_toggled = Signal()
    minimize = Signal()
    maximize = Signal()
    close = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(TITLE_H)

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 6, 10, 6)
        root.setSpacing(6)

        self._back = self._chrome("chevron-left", 17, "后退")
        self._back.clicked.connect(self.back.emit)
        root.addWidget(self._back)
        self._forward = self._chrome("chevron-right", 17, "前进")
        self._forward.clicked.connect(self.forward.emit)
        root.addWidget(self._forward)
        root.addSpacing(6)

        self._group = QLabel("")
        self._group.setObjectName("Crumb")
        root.addWidget(self._group)
        self._sep1 = QLabel("/")
        self._sep1.setObjectName("CrumbSep")
        root.addWidget(self._sep1)
        self._page = QLabel("")
        self._page.setObjectName("CrumbStrong")
        root.addWidget(self._page)
        self._sep2 = QLabel("/")
        self._sep2.setObjectName("CrumbSep")
        root.addWidget(self._sep2)
        self._tail = QLabel("")
        self._tail.setObjectName("CrumbTail")
        root.addWidget(self._tail)
        root.addStretch(1)

        self._theme = self._chrome("sun", 17, "切换深色 / 浅色主题")
        self._theme.clicked.connect(self.theme_toggled.emit)
        root.addWidget(self._theme)
        root.addSpacing(6)

        self._lamp = QLabel("\u25cf")
        self._lamp.setObjectName("Lamp")
        root.addWidget(self._lamp)
        self._lamp_text = QLabel("空闲")
        self._lamp_text.setObjectName("LampText")
        root.addWidget(self._lamp_text)
        root.addSpacing(8)

        self._min = self._chrome("minimize", 15, "最小化")
        self._min.clicked.connect(self.minimize.emit)
        root.addWidget(self._min)
        self._max = self._chrome("maximize", 15, "最大化 / 还原")
        self._max.clicked.connect(self.maximize.emit)
        root.addWidget(self._max)
        self._close = self._chrome("close", 15, "关闭", danger=True)
        self._close.clicked.connect(self.close.emit)
        root.addWidget(self._close)
        self.refresh_theme()

    def _chrome(self, name: str, size: int, tooltip: str,
                danger: bool = False) -> QPushButton:
        button = QPushButton()
        button.setObjectName("ChromeClose" if danger else "ChromeButton")
        button.setFixedSize(34, 28)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(tooltip)
        button.setProperty("iconName", name)
        button.setProperty("glyphSize", size)
        return button

    # -- 状态 --
    def set_crumbs(self, group: str, page: str, tail: str = "") -> None:
        self._group.setText(group)
        self._page.setText(page)
        self._tail.setText(tail)
        self._sep2.setVisible(bool(tail))
        self._tail.setVisible(bool(tail))

    def set_nav_state(self, can_back: bool, can_forward: bool) -> None:
        self._back.setEnabled(can_back)
        self._forward.setEnabled(can_forward)

    def set_busy(self, busy: bool, name: str = "") -> None:
        palette = theme.palette()
        self._lamp.setStyleSheet(
            f"color: {palette.warning if busy else palette.success}; font-size: 13px;")
        self._lamp_text.setText(f"运行中 · {name}" if busy else "空闲")

    def set_maximized(self, value: bool) -> None:
        self._max.setProperty("iconName", "restore" if value else "maximize")
        self._max.setIcon(icons.icon("restore" if value else "maximize",
                                     theme.palette().text_muted, 15))

    def refresh_theme(self) -> None:
        palette = theme.palette()
        self.setObjectName("TitleBar")
        for button in (self._back, self._forward, self._theme, self._min):
            name = str(button.property("iconName"))
            size = int(button.property("glyphSize") or 16)
            button.setIcon(icons.icon(name, palette.text_muted, size))
            button.setIconSize(button.iconSize())
        size = int(self._max.property("glyphSize") or 15)
        name = "restore" if self._max.property("iconName") == "restore" else "maximize"
        self._max.setIcon(icons.icon(name, palette.text_muted, size))
        self._close.setIcon(icons.icon("close", palette.text_muted, 15))
        self._theme.setIcon(icons.icon("moon" if palette.name == "dark" else "sun",
                                       palette.text_muted, 17))
        self.set_busy(self._lamp_text.text().startswith("运行中"))


class LogDrawer(QFrame):
    """右侧日志抽屉：收起 44px、展开 320px，内容复用原有日志控制台。"""

    toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("LogDrawer")
        self._open = False

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._tab = _VerticalTab()
        self._tab.clicked.connect(self.toggle)
        root.addWidget(self._tab)

        self._panel = QWidget()
        panel = QVBoxLayout(self._panel)
        panel.setContentsMargins(12, 12, 12, 12)
        panel.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(6)
        title = QLabel("运行日志")
        title.setObjectName("DrawerTitle")
        head.addWidget(title)
        head.addStretch(1)
        self._clear = QPushButton()
        self._clear.setObjectName("ChromeButton")
        self._clear.setFixedSize(28, 24)
        self._clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear.setToolTip("清空日志")
        head.addWidget(self._clear)
        self._fold = QPushButton()
        self._fold.setObjectName("ChromeButton")
        self._fold.setFixedSize(28, 24)
        self._fold.setCursor(Qt.CursorShape.PointingHandCursor)
        self._fold.setToolTip("收起日志")
        head.addWidget(self._fold)
        panel.addLayout(head)

        self.console = LogConsole()
        panel.addWidget(self.console, 1)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        panel.addWidget(self._progress)
        self._hint = QLabel("等待任务…")
        self._hint.setObjectName("Hint")
        panel.addWidget(self._hint)
        root.addWidget(self._panel, 1)

        self._clear.clicked.connect(self.console.clear_log)
        self._fold.clicked.connect(self.toggle)
        self.set_open(False, animate=False)
        self.refresh_theme()

    # -- 对外 --
    def is_open(self) -> bool:
        return self._open

    def toggle(self) -> None:
        self.set_open(not self._open)

    def set_open(self, value: bool, animate: bool = True) -> None:
        self._open = bool(value)
        self._panel.setVisible(self._open)
        self._tab.set_open(self._open)
        target = DRAWER_EXPANDED if self._open else DRAWER_COLLAPSED
        if not animate:
            self.setFixedWidth(target)
        else:
            animation = QVariantAnimation(self)
            animation.setStartValue(float(self.width()))
            animation.setEndValue(float(target))
            animation.setDuration(180)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.valueChanged.connect(
                lambda value: self.setFixedWidth(int(value)))
            self._animation = animation
            animation.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)
        self.toggled.emit(self._open)
        if self._open:
            self.console.setFocus()

    def set_progress(self, value: int, text: str = "") -> None:
        self._progress.setValue(max(0, min(100, int(value))))
        if text:
            self._hint.setText(text)

    def clear_progress(self) -> None:
        self._progress.setValue(0)

    def refresh_theme(self) -> None:
        palette = theme.palette()
        self._clear.setIcon(icons.icon("close", palette.text_muted, 15))
        self._fold.setIcon(icons.icon("chevron-right" if not self._open else "chevron-left",
                                      palette.text_muted, 15))
        self.console.refresh_theme()


class _Toast(QLabel):
    """壳层底部的轻提示（跑完流程 / 切换数据来源时冒一下）。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("StatPill")
        self.setVisible(False)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self.setVisible(False))

    def show_message(self, text: str, msec: int = 3000) -> None:
        self.setText(text)
        self.adjustSize()
        self.reposition()
        self.setVisible(True)
        self.raise_()
        self._timer.start(msec)

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        x = max(16, (parent.width() - self.width()) // 2)
        y = max(16, parent.height() - self.height() - 26)
        self.move(x, y)


class MainWindow(QWidget):
    """MetCOM 桌面主窗口。"""

    def __init__(self, ctx, pages: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._pages = pages
        self._history: list[str] = []
        self._cursor = -1
        self._current = ""
        self._maximized = False
        from metcom.gui import data

        self._origin = data.dataset(ctx.root).origin()

        self.setWindowTitle("METCOM · 多源再分析 ZTD / PWV 反演与评估平台")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setMinimumSize(1180, 720)
        self.resize(1340, 860)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        outer.setSpacing(0)
        self._shell = QFrame()
        self._shell.setObjectName("ShellBody")
        outer.addWidget(self._shell)
        theme.apply_shadow(self._shell, blur=36, dy=10, alpha=120)

        shell = QVBoxLayout(self._shell)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        self.title_bar = TitleBar()
        self.title_bar.back.connect(self.go_back)
        self.title_bar.forward.connect(self.go_forward)
        self.title_bar.theme_toggled.connect(self.toggle_theme)
        self.title_bar.minimize.connect(self.showMinimized)
        self.title_bar.maximize.connect(self.toggle_maximized)
        self.title_bar.close.connect(self.close)
        shell.addWidget(self.title_bar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.sidebar = Sidebar(NAV_ITEMS)
        self.sidebar.nav_requested.connect(self.navigate)
        self.sidebar.theme_toggled.connect(self.toggle_theme)
        body.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        for page in self._pages.values():
            self.stack.addWidget(page)
        body.addWidget(self.stack, 1)

        self.drawer = LogDrawer()
        body.addWidget(self.drawer)
        shell.addLayout(body, 1)

        self._toast = _Toast(self._shell)
        self._wire()
        self.title_bar.installEventFilter(self)
        self.title_bar.set_nav_state(False, False)
        self._center()
        self.navigate("home")
        self._apply_state()

    # ------------------------------------------------------------ 装配 --
    def _center(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.center().x() - self.width() // 2,
                  max(area.top() + 10, area.center().y() - self.height() // 2))

    def _wire(self) -> None:
        controller = self.ctx.controller
        controller.log_line.connect(self._append_log)
        controller.busy_changed.connect(self._on_busy)
        controller.progress.connect(self.drawer.set_progress)
        controller.succeeded.connect(self._on_succeeded)
        controller.failed.connect(self._on_failed)
        controller.cancelled.connect(
            lambda name: self._toast.show_message(f"「{name}」已取消"))
        controller.finished.connect(self._on_finished)
        self.ctx.state.workflow_changed.connect(self._on_workflow)

    # ------------------------------------------------------ 日志 / 状态 --
    def _append_log(self, text: str) -> None:
        for line in str(text).splitlines() or [""]:
            self.drawer.console.append_line(line)

    def _on_busy(self, busy: bool, name: str) -> None:
        self.title_bar.set_busy(busy, name)
        self.sidebar.set_busy(busy, name)
        if busy:
            self.drawer.clear_progress()
            self.drawer.set_open(True)
        else:
            self.drawer.clear_progress()

    def _on_succeeded(self, name: str, result) -> None:
        self._toast.show_message(f"「{name}」已完成，视图已同步刷新")

    def _on_failed(self, name: str, message: str) -> None:
        self.drawer.set_open(True)
        self._toast.show_message(f"「{name}」失败：{message}", 5000)

    def _on_finished(self, name: str) -> None:
        from metcom.gui import data

        dataset = data.refresh(self.ctx.root)
        if dataset.origin() != self._origin:
            self._origin = dataset.origin()
            self.ctx.log(f"数据来源已切换：{dataset.origin_text()}")
            self._toast.show_message(f"数据来源已切换为「{dataset.origin_text()}」", 4200)
        self._breadcrumb()

    def _on_workflow(self) -> None:
        self._breadcrumb()

    # ---------------------------------------------------------- 导航 --
    def navigate(self, key: str, record: bool = True) -> None:
        page = self._pages.get(key)
        if page is None or key == self._current:
            return
        if record:
            del self._history[self._cursor + 1:]
            if not self._history or self._history[-1] != key:
                self._history.append(key)
            self._cursor = len(self._history) - 1
        self._current = key
        self.stack.setCurrentWidget(page)
        self.sidebar.set_active(key)
        self._breadcrumb()
        if hasattr(page, "on_show"):
            page.on_show()
        self.title_bar.set_nav_state(self._cursor > 0,
                                     self._cursor < len(self._history) - 1)

    def go_back(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self.navigate(self._history[self._cursor], record=False)

    def go_forward(self) -> None:
        if self._cursor < len(self._history) - 1:
            self._cursor += 1
            self.navigate(self._history[self._cursor], record=False)

    def _breadcrumb(self) -> None:
        group = "工作区"
        for entry in NAV_ITEMS:
            if entry[0] is None:
                group = entry[1]
            elif entry[0] == self._current:
                break
        page = self._pages.get(self._current)
        title = getattr(page, "page_title", self._current)
        from metcom.gui import data

        tail = data.dataset(self.ctx.root).origin_text()
        self.title_bar.set_crumbs(group, title, tail)

    # ---------------------------------------------------------- 主题 --
    def refresh_theme(self) -> None:
        self.title_bar.refresh_theme()
        self.sidebar.refresh_theme()
        self.drawer.refresh_theme()
        for page in self._pages.values():
            if hasattr(page, "refresh_theme"):
                page.refresh_theme()

    def toggle_theme(self) -> None:
        target = "light" if theme.mode() == "dark" else "dark"
        theme.set_mode(target, QApplication.instance())
        self.refresh_theme()

    # ------------------------------------------------------ 窗口行为 --
    def toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _apply_state(self) -> None:
        maximized = self.isMaximized()
        self._maximized = maximized
        margin = 0 if maximized else MARGIN
        self.layout().setContentsMargins(margin, margin, margin, margin)
        for widget in (self._shell, self.sidebar, self.drawer):
            widget.setProperty("flat", "true" if maximized else "false")
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        if maximized:
            self._shell.setGraphicsEffect(None)
        else:
            theme.apply_shadow(self._shell, blur=36, dy=10, alpha=120)
        self.title_bar.set_maximized(maximized)

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.type() == QEvent.Type.WindowStateChange:
            self._apply_state()
        super().changeEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._toast.reposition()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt 命名
        if obj is self.title_bar:
            if (event.type() == QEvent.Type.MouseButtonPress
                    and event.button() == Qt.MouseButton.LeftButton):
                handle = self.windowHandle()
                if handle is not None:
                    handle.startSystemMove()
                    return True
            if event.type() == QEvent.Type.MouseButtonDblClick:
                self.toggle_maximized()
                return True
        return super().eventFilter(obj, event)

    def _edges_at(self, point) -> Qt.Edge:
        if self._maximized:
            return Qt.Edge(0)
        edges = Qt.Edge(0)
        if point.x() <= GRIP:
            edges |= Qt.Edge.LeftEdge
        elif point.x() >= self.width() - GRIP:
            edges |= Qt.Edge.RightEdge
        if point.y() <= GRIP:
            edges |= Qt.Edge.TopEdge
        elif point.y() >= self.height() - GRIP:
            edges |= Qt.Edge.BottomEdge
        return edges

    @staticmethod
    def _cursor_for(edges: Qt.Edge) -> Qt.CursorShape:
        left = bool(edges & Qt.Edge.LeftEdge)
        right = bool(edges & Qt.Edge.RightEdge)
        top = bool(edges & Qt.Edge.TopEdge)
        bottom = bool(edges & Qt.Edge.BottomEdge)
        if (left and top) or (right and bottom):
            return Qt.CursorShape.SizeFDiagCursor
        if (right and top) or (left and bottom):
            return Qt.CursorShape.SizeBDiagCursor
        if left or right:
            return Qt.CursorShape.SizeHorCursor
        if top or bottom:
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        edges = self._edges_at(event.position().toPoint())
        self.setCursor(self._cursor_for(edges) if edges
                       else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton:
            edges = self._edges_at(event.position().toPoint())
            handle = self.windowHandle()
            if edges and handle is not None:
                handle.startSystemResize(edges)
                event.accept()
                return
        super().mousePressEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)


def build_window(ctx, pages: dict) -> MainWindow:
    """构造主窗口（供 :mod:`metcom.gui.app` 调用）。"""
    return MainWindow(ctx, pages)


__all__ = ["MainWindow", "TitleBar", "LogDrawer", "build_window"]
