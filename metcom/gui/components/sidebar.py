"""侧边导航栏：品牌区 + 分组导航 + 运行状态 + 主题切换。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from metcom.gui import icons, theme

NAV_WIDTH = 232


class NavItem(QWidget):
    """一个导航项：左侧强调标记 + 图标按钮。"""

    def __init__(self, key: str, label: str, icon_name: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 1, 8, 1)
        row.setSpacing(6)

        self.mark = QFrame()
        self.mark.setObjectName("NavMark")
        self.mark.setFixedSize(3, 20)
        self.mark.setVisible(False)
        row.addWidget(self.mark)

        self.button = QPushButton(f"  {label}")
        self.button.setObjectName("NavButton")
        self.button.setCheckable(True)
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.setIcon(icons.icon(icon_name, None, 18))
        self.button.setIconSize(self.button.iconSize().expandedTo(
            self.button.minimumSizeHint()))
        row.addWidget(self.button, 1)

        self._icon_name = icon_name

    def set_active(self, active: bool) -> None:
        self.button.setChecked(active)
        self.mark.setVisible(active)
        color = theme.palette().accent if active else theme.palette().text_muted
        self.button.setIcon(icons.icon(self._icon_name, color, 18))

    def refresh_theme(self) -> None:
        self.set_active(self.button.isChecked())


class Sidebar(QFrame):
    """左侧导航；``nav_requested`` 携带页面 key。"""

    nav_requested = Signal(str)
    theme_toggled = Signal()

    def __init__(self, items: list[tuple], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(NAV_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 导航列放进滚动区：窗口压矮时滚动而非截断，窗口最小高度不再被导航列撑住
        self._scroll = QScrollArea()
        self._scroll.setObjectName("SidebarScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        holder.setObjectName("SidebarBody")
        root = QVBoxLayout(holder)
        root.setContentsMargins(12, 16, 12, 14)
        root.setSpacing(6)
        self._scroll.setWidget(holder)
        outer.addWidget(self._scroll)

        root.addLayout(self._build_brand())
        root.addSpacing(10)

        self._items: dict[str, NavItem] = {}
        for entry in items:
            if entry[0] is None:                       # 分组标题
                root.addSpacing(6)
                group = QLabel(entry[1])
                group.setObjectName("NavGroup")
                root.addWidget(group)
                root.addSpacing(2)
                continue
            key, label, icon_name = entry
            item = NavItem(key, label, icon_name)
            item.button.clicked.connect(lambda _=False, k=key: self._on_click(k))
            self._items[key] = item
            root.addWidget(item)

        root.addStretch(1)
        root.addWidget(self._make_sep())
        root.addSpacing(6)
        root.addWidget(self._build_status())
        root.addSpacing(6)
        self._theme_button = QPushButton()
        self._theme_button.setObjectName("GhostButton")
        self._theme_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._theme_button.clicked.connect(self.theme_toggled.emit)
        root.addWidget(self._theme_button)
        root.addSpacing(4)
        self._version = QLabel()
        self._version.setObjectName("SidebarVersion")
        self._version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._version)

        self._active: str | None = None
        self.refresh_theme()

    # -- 构建 --
    def _build_brand(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(10)
        self._logo = QLabel()
        self._logo.setFixedSize(38, 38)
        row.addWidget(self._logo)
        col = QVBoxLayout()
        col.setSpacing(0)
        self._brand = QLabel("METCOM")
        self._brand.setObjectName("BrandName")
        col.addWidget(self._brand)
        self._tag = QLabel("多源再分析反演平台")
        self._tag.setObjectName("BrandTag")
        col.addWidget(self._tag)
        row.addLayout(col)
        row.addStretch(1)
        return row

    def _make_sep(self) -> QFrame:
        line = QFrame()
        line.setObjectName("SidebarSep")
        line.setFixedHeight(1)
        return line

    def _build_status(self) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(8, 2, 8, 2)
        row.setSpacing(7)
        self._dot = QLabel("\u25cf")
        self._dot.setFixedWidth(12)
        row.addWidget(self._dot)
        self._status = QLabel("空闲")
        self._status.setObjectName("SidebarVersion")
        row.addWidget(self._status, 1)
        self._status_holder = holder
        return holder

    # -- 交互 --
    def _on_click(self, key: str) -> None:
        if key == self._active:
            for item in self._items.values():
                item.set_active(item.key == key)
            return
        self.set_active(key)
        self.nav_requested.emit(key)

    def set_active(self, key: str) -> None:
        self._active = key
        for item_key, item in self._items.items():
            item.set_active(item_key == key)

    def active(self) -> str | None:
        return self._active

    def set_busy(self, busy: bool, text: str = "") -> None:
        pal = theme.palette()
        color = pal.warning if busy else pal.success
        self._dot.setStyleSheet(f"color: {color}; font-size: 13px;")
        self._status.setText(text or ("运行中…" if busy else "空闲"))

    # -- 主题 --
    def refresh_theme(self) -> None:
        pal = theme.palette()
        self._logo.setPixmap(icons.app_logo(38, pal.name))
        for item in self._items.values():
            item.refresh_theme()
        light = pal.name == "light"
        self._theme_button.setText("  切换为深色" if light else "  切换为浅色")
        self._theme_button.setIcon(icons.icon("moon" if light else "sun", None, 17))
        self._theme_button.setIconSize(self._theme_button.iconSize())
        from metcom import __version__

        self._version.setText(f"v{__version__}  ·  Python 迁移版")
        self.set_busy(False)


__all__ = ["Sidebar", "NavItem", "NAV_WIDTH"]
