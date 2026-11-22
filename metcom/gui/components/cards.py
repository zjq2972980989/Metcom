"""卡片类基础控件。

所有"内容块"都由 :class:`Card` 派生，保证圆角、描边、内边距、标题排版
完全一致——界面观感的一致性主要来自这里，而不是各页面各写一套。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QSizePolicy,
                               QVBoxLayout, QWidget)

from metcom.gui import icons


class Card(QFrame):
    """标准卡片：可选标题区 + 内容区。

    用法::

        card = Card("参数配置", "对应 metcom_era5.yaml", "sliders")
        card.add_widget(FormRow("数据目录", PathField("dir")))
        card.body().addStretch()
    """

    def __init__(self, title: str | None = None, description: str | None = None,
                 icon_name: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(18, 16, 18, 18)
        self._root.setSpacing(12)

        self._header: QHBoxLayout | None = None
        if title:
            self._root.addLayout(self._build_header(title, description, icon_name))

        self._body = QVBoxLayout()
        self._body.setSpacing(10)
        self._root.addLayout(self._body)

    # -- 构建 --
    def _build_header(self, title: str, description: str | None,
                      icon_name: str | None) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(10)
        if icon_name:
            glyph = QLabel()
            glyph.setPixmap(icons.badge_pixmap(icon_name, 26))
            glyph.setFixedSize(26, 26)
            header.addWidget(glyph, 0, Qt.AlignmentFlag.AlignTop)
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self._title_label = QLabel(title)
        self._title_label.setObjectName("CardTitle")
        text_col.addWidget(self._title_label)
        if description:
            desc = QLabel(description)
            desc.setObjectName("CardDesc")
            desc.setWordWrap(True)
            text_col.addWidget(desc)
        header.addLayout(text_col)
        header.addStretch(1)
        self._header = header
        return header

    # -- 对外 --
    def body(self) -> QVBoxLayout:
        """内容区布局（往里加控件）。"""
        return self._body

    def header(self) -> QHBoxLayout:
        """标题区布局（往右端加按钮）；无标题时自动补一个。"""
        if self._header is None:
            self._header = QHBoxLayout()
            self._header.addStretch(1)
            self._root.insertLayout(0, self._header)
        return self._header

    def add_widget(self, widget: QWidget, stretch: int = 0) -> QWidget:
        """向内容区添加控件并返回它（便于链式构造）。"""
        self._body.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout) -> None:
        self._body.addLayout(layout)

    def set_title(self, text: str) -> None:
        if getattr(self, "_title_label", None) is not None:
            self._title_label.setText(text)


class MetricCard(Card):
    """指标卡：小标签 + 大数值 + 单位（首页/评估页概览用）。"""

    def __init__(self, label: str, value: str = "--", unit: str = "",
                 icon_name: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent=parent)
        self._root.setContentsMargins(16, 14, 16, 14)
        self._root.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(8)
        if icon_name:
            glyph = QLabel()
            glyph.setPixmap(icons.badge_pixmap(icon_name, 24))
            glyph.setFixedSize(24, 24)
            top.addWidget(glyph)
        name = QLabel(label)
        name.setObjectName("MetricLabel")
        top.addWidget(name)
        top.addStretch(1)
        self._root.addLayout(top)

        value_row = QHBoxLayout()
        value_row.setSpacing(6)
        self._value = QLabel(value)
        self._value.setObjectName("MetricValue")
        value_row.addWidget(self._value)
        self._unit = QLabel(unit)
        self._unit.setObjectName("MetricUnit")
        value_row.addWidget(self._unit, 0, Qt.AlignmentFlag.AlignBottom)
        value_row.addStretch(1)
        self._root.addLayout(value_row)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def set_value(self, value: str, unit: str | None = None) -> None:
        self._value.setText(str(value))
        if unit is not None:
            self._unit.setText(unit)


class HeroBanner(QFrame):
    """首页顶部英雄区：渐变底 + 标题 + 简介 + 特性标签。"""

    def __init__(self, title: str, subtitle: str,
                 chips: list[str] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Hero")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(8)

        title_label = QLabel(title)
        title_label.setObjectName("HeroTitle")
        layout.addWidget(title_label)

        text = QLabel(subtitle)
        text.setObjectName("HeroText")
        text.setWordWrap(True)
        layout.addWidget(text)

        if chips:
            row = QHBoxLayout()
            row.setSpacing(8)
            for item in chips:
                row.addWidget(chip(item))
            row.addStretch(1)
            layout.addSpacing(4)
            layout.addLayout(row)


def chip(text: str) -> QLabel:
    """小标签（柔光底药丸）。"""
    label = QLabel(text)
    label.setObjectName("HeroChip")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return label


def section_label(text: str) -> QLabel:
    """分组小标题（弱化色 + 加粗，用于卡片内的分区）。"""
    label = QLabel(text)
    label.setObjectName("SectionLabel")
    return label


def separator() -> QFrame:
    """1px 分隔线。"""
    line = QFrame()
    line.setObjectName("Separator")
    line.setFixedHeight(1)
    line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return line


class KeyValueRow(QWidget):
    """键值行（环境自检 / 关于页）。"""

    def __init__(self, key: str, value: str = "", mono: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self._key = QLabel(key)
        self._key.setObjectName("MetricLabel")
        self._key.setMinimumWidth(120)
        layout.addWidget(self._key)
        self._value = QLabel(value)
        self._value.setObjectName("Mono" if mono else "CardDesc")
        self._value.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self._value.setWordWrap(True)
        layout.addWidget(self._value, 1)

    def set_value(self, value: str) -> None:
        self._value.setText(value)

    def set_state(self, state: str) -> None:
        """state: normal | ok | warn | bad（仅切换文字颜色）。"""
        from metcom.gui import theme

        pal = theme.palette()
        color = {"ok": pal.success, "warn": pal.warning,
                 "bad": pal.danger}.get(state, pal.text_muted)
        self._value.setStyleSheet(f"color: {color};")


__all__ = ["Card", "MetricCard", "HeroBanner", "chip", "section_label",
           "separator", "KeyValueRow"]
