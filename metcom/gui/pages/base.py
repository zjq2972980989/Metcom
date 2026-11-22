"""页面基类：统一的标题区 + 滚动内容区 + 主题刷新机制。

界面一致性主要靠这里保证：所有页面的标题排版、内容内边距、控件注册与
主题刷新流程都在基类里做一次，子类只关心"放什么控件、点按钮时干什么"。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from metcom.gui import icons, theme

_ROLE_COLORS = {
    "accent": "accent",
    "text": "text",
    "muted": "text_muted",
    "faint": "text_faint",
    "danger": "danger",
    "success": "success",
    "warning": "warning",
}


class BasePage(QWidget):
    """所有页面的父类。"""

    page_key: str = ""
    module_key: str = ""
    page_title: str = "页面"
    page_subtitle: str = ""
    page_icon: str = "home"

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._icon_registry: list[tuple[Any, str, str, int]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(14)
        root.addLayout(self._build_header())

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setObjectName("PageScroll")
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        holder.setObjectName("PageContent")
        self._body = QVBoxLayout(holder)
        self._body.setContentsMargins(0, 0, 8, 8)
        self._body.setSpacing(14)
        self._scroll.setWidget(holder)
        root.addWidget(self._scroll, 1)

        self.build()
        self.refresh_theme()

    # ------------------------------------------------------------ 构建 --
    def _build_header(self) -> QHBoxLayout:
        head = QHBoxLayout()
        head.setSpacing(14)

        self._glyph = QLabel()
        self._glyph.setFixedSize(42, 42)
        self._register_icon(self._glyph, self.page_icon, "accent", 22)
        head.addWidget(self._glyph, 0, Qt.AlignmentFlag.AlignTop)

        column = QVBoxLayout()
        column.setSpacing(3)
        title = QLabel(self.page_title)
        title.setObjectName("PageTitle")
        column.addWidget(title)
        if self.page_subtitle:
            subtitle = QLabel(self.page_subtitle)
            subtitle.setObjectName("PageSubtitle")
            subtitle.setWordWrap(True)
            column.addWidget(subtitle)
        head.addLayout(column, 1)

        self._status = QLabel("")
        self._status.setObjectName("HeroChip")
        self._status.setVisible(False)
        head.addWidget(self._status, 0, Qt.AlignmentFlag.AlignTop)

        self._actions = QHBoxLayout()
        self._actions.setSpacing(8)
        self._actions.setAlignment(Qt.AlignmentFlag.AlignTop)
        head.addLayout(self._actions)
        return head

    def build(self) -> None:
        """子类在这里往 :meth:`body` 里放控件。"""

    # ------------------------------------------------------------ 便捷 --
    def body(self) -> QVBoxLayout:
        return self._body

    def columns(self, left_stretch: int = 3, right_stretch: int = 2,
                spacing: int = 14) -> tuple[QVBoxLayout, QVBoxLayout]:
        """在内容区建两列，返回 ``(左列, 右列)`` 两个纵向布局。"""
        row = QHBoxLayout()
        row.setSpacing(spacing)
        left = QVBoxLayout()
        left.setSpacing(14)
        right = QVBoxLayout()
        right.setSpacing(14)
        row.addLayout(left, left_stretch)
        row.addLayout(right, right_stretch)
        self._body.addLayout(row)
        return left, right

    def add_action(self, widget: QWidget) -> QWidget:
        self._actions.addWidget(widget)
        return widget

    def button(self, text: str, icon_name: str | None = None, kind: str = "ghost",
               tooltip: str = "") -> QPushButton:
        """按钮工厂；``kind`` 取 primary / ghost / danger。"""
        style = {"primary": "PrimaryButton", "ghost": "GhostButton",
                 "danger": "DangerButton"}[kind]
        button = QPushButton(text)
        button.setObjectName(style)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            button.setToolTip(tooltip)
        if icon_name:
            role = {"primary": "accent_on", "danger": "danger",
                    "ghost": "muted"}[kind]
            self._register_icon(button, icon_name, role, 17)
        return button

    def run_button(self, text: str = "开始运行") -> QPushButton:
        return self.button(text, "play", "primary")

    def cancel_button(self) -> QPushButton:
        button = self.button("取消", "stop", "ghost")
        button.clicked.connect(self.ctx.controller.cancel)
        return button

    def set_status(self, text: str, state: str = "muted") -> None:
        """右上角状态药丸。"""
        if not text:
            self._status.setVisible(False)
            return
        pal = theme.palette()
        color = {"ok": pal.success, "warn": pal.warning, "bad": pal.danger,
                 "accent": pal.accent}.get(state, pal.text_muted)
        self._status.setStyleSheet(
            f"color: {color}; border: 1px solid {color}; border-radius: 10px;"
            " padding: 2px 10px; font-size: 11px;")
        self._status.setText(text)
        self._status.setVisible(True)

    def add_stretch(self) -> None:
        self._body.addStretch(1)

    # ---------------------------------------------------------- 主题 --
    def _register_icon(self, widget: Any, icon_name: str, role: str = "muted",
                       size: int = 18) -> None:
        self._icon_registry.append((widget, icon_name, role, size))

    def refresh_theme(self) -> None:
        """主题切换后重绘所有已注册图标（子类可覆写，记得先调 super()）。"""
        pal = theme.palette()
        for widget, name, role, size in self._icon_registry:
            if role == "accent_on":
                color = pal.on_accent
            else:
                color = getattr(pal, _ROLE_COLORS.get(role, "text_muted"),
                                pal.text_muted)
            if isinstance(widget, QAbstractButton):
                widget.setIcon(icons.icon(name, color, size))
            elif isinstance(widget, QLabel):
                widget.setPixmap(icons.pixmap(name, color, size))

    def on_show(self) -> None:
        """页面被切换到前台时调用。"""

    # -------------------------------------------------------- 配置钩子 --
    def collect_config(self) -> dict | None:
        """返回本页当前配置（供"一键流水线"使用）；默认返回 None 表示未实现。"""
        return None

    def save_config(self) -> None:
        """把当前配置写回 configs/ 下的 YAML（子类可覆写）。"""
        cfg = self.collect_config()
        if cfg is None:
            return
        name = self.ctx.config_name(self.module_key)
        path = self.ctx.store.save(name, cfg)
        self.ctx.log(f"配置已保存: {path}")
