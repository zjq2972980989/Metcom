"""GUI 启动流程：QApplication -> 启动画面 -> 自绘主窗口。

启动画面刻意做得极短（约 1.5 秒，不可跳过），但会把三件事讲清楚：
环境自检、加载页面、就绪；结束时 400ms 淡出并与主窗口交叉混合，
不会出现"闪一下白屏"的廉价感。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from PySide6.QtCore import (QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer)
from PySide6.QtGui import (QBrush, QColor, QIcon, QLinearGradient, QPainter,
                           QPainterPath, QPen)
from PySide6.QtWidgets import (QApplication, QGraphicsOpacityEffect, QLabel,
                               QProgressBar, QVBoxLayout, QWidget)

from metcom import __version__
from metcom.gui import icons, theme
from metcom.gui.pages import build_pages
from metcom.gui.state import AppContext, AppState, ConfigStore
from metcom.gui.window import build_window
from metcom.gui.workers import TaskController

ASSETS = Path(__file__).resolve().parent / "assets"
STAGES = ("环境自检", "加载页面", "就绪")
PROBE = ("PySide6", "pyqtgraph", "numpy", "pandas", "matplotlib", "cartopy")


def project_root() -> Path:
    """项目根目录（``metcom`` 包的上一级）。"""
    return Path(__file__).resolve().parents[2]


class Splash(QWidget):
    """启动画面：渐变底 + 徽标 + 版本号 + 三段加载文案。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.SplashScreen
                            | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(430, 268)

        root = QVBoxLayout(self)
        root.setContentsMargins(30, 26, 30, 22)
        root.setSpacing(8)

        logo = QLabel()
        logo.setPixmap(icons.app_logo(78, theme.mode()))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(logo)

        title = QLabel("METCOM")
        title.setObjectName("SplashTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)

        subtitle = QLabel("多源再分析 ZTD / PWV 反演与评估平台")
        subtitle.setObjectName("SplashSub")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(subtitle)
        root.addStretch(1)

        self._stage_labels = []
        for text in STAGES:
            label = QLabel(text)
            label.setObjectName("SplashStage")
            self._stage_labels.append(label)
            root.addWidget(label)

        self._bar = QProgressBar()
        self._bar.setRange(0, len(STAGES))
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(5)
        root.addWidget(self._bar)

        version = QLabel(f"v{__version__}  ·  Python 迁移版")
        version.setObjectName("SplashVersion")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(version)
        self.set_stage(0)
        self._center()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        palette = theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            path = QPainterPath()
            path.addRoundedRect(rect, 18.0, 18.0)
            gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
            gradient.setColorAt(0.0, QColor(palette.hero_from))
            gradient.setColorAt(1.0, QColor(palette.hero_to))
            painter.fillPath(path, QBrush(gradient))
            highlight = QLinearGradient(rect.topLeft(), rect.topRight())
            highlight.setColorAt(0.0, QColor(255, 255, 255, 40))
            highlight.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setPen(QPen(QBrush(highlight), 1.2))
            painter.drawPath(path)
        finally:
            painter.end()

    def set_stage(self, index: int) -> None:
        for position, label in enumerate(self._stage_labels):
            if position < index:
                label.setText("\u2713  " + STAGES[position])
                label.setStyleSheet(f"color: {theme.palette().success};")
            elif position == index:
                label.setText("\u25cf  " + STAGES[position] + " …")
                label.setStyleSheet(
                    f"color: {theme.palette().text}; font-weight: 600;")
            else:
                label.setText("    " + STAGES[position])
                label.setStyleSheet(f"color: {theme.palette().text_faint};")
        self._bar.setValue(index)
        QApplication.processEvents()

    def _center(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.center().x() - self.width() // 2,
                  area.center().y() - self.height() // 2)


def _probe_environment() -> list[str]:
    """启动前快速探测可选依赖（只查规格，不真正导入）。"""
    missing = []
    for name in PROBE:
        try:
            if importlib.util.find_spec(name) is None:
                missing.append(name)
        except (ImportError, ValueError):
            missing.append(name)
    return missing


def _reveal(splash: Splash, window) -> None:
    """主窗口淡入、启动画面淡出（400ms 交叉混合）。"""
    window.setWindowOpacity(0.0)
    window.show()

    effect = QGraphicsOpacityEffect(splash)
    splash.setGraphicsEffect(effect)
    fade = QPropertyAnimation(effect, b"opacity", splash)
    fade.setDuration(400)
    fade.setStartValue(1.0)
    fade.setEndValue(0.0)
    fade.setEasingCurve(QEasingCurve.Type.InOutQuad)
    fade.finished.connect(splash.close)
    fade.start()

    rise = QPropertyAnimation(window, b"windowOpacity", window)
    rise.setDuration(400)
    rise.setStartValue(0.0)
    rise.setEndValue(1.0)
    rise.setEasingCurve(QEasingCurve.Type.OutCubic)
    rise.start()


def run(argv: list[str] | None = None) -> int:
    """启动图形界面，返回进程退出码。"""
    argv = list(sys.argv if argv is None else argv)
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("METCOM")
    app.setApplicationDisplayName("METCOM")

    icon = QIcon(str(ASSETS / "metcom.ico"))
    if not icon.isNull():
        app.setWindowIcon(icon)

    mode = "light" if "--light" in argv else "dark" if "--dark" in argv else None
    theme.apply_theme(app, mode)

    root = project_root()
    splash = Splash()
    splash.show()
    app.processEvents()

    missing = _probe_environment()

    def start() -> None:
        store = ConfigStore(root / "configs")
        controller = TaskController()
        state = AppState()
        ctx = AppContext(root=root, store=store, controller=controller, state=state)
        pages = build_pages(ctx)
        window = build_window(ctx, pages)
        if not icon.isNull():
            window.setWindowIcon(icon)
        for name in missing:
            ctx.log(f"警告: 未检测到可选依赖 {name}，对应功能可能不可用")
        splash.set_stage(2)
        QTimer.singleShot(140, lambda: _reveal(splash, window))

    def step_pages() -> None:
        splash.set_stage(1)
        QTimer.singleShot(110, start)

    QTimer.singleShot(220, step_pages)
    return app.exec()


__all__ = ["run", "Splash", "project_root"]
