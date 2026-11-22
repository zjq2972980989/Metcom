"""METCOM GUI 设计系统 —— 配色令牌、字体、图标素材与 QSS 样式表。

视觉概念
--------
以「大气垂直分层」为隐喻：深夜蓝（平流层以上）作基底，青蓝（对流层顶
附近）作强调色。信息层级依靠 **表面亮度 + 1px 描边** 表达，而非重阴影，
因此在 100%~200% 缩放下都保持锐利；强调元素用低透明度"柔光底"而非高饱和
色块，避免长时间盯屏的视觉疲劳——这是科研软件与消费级 App 的关键差别。

对外接口
--------
``Palette``                  配色令牌（不可变）
``DARK`` / ``LIGHT``         两套内置调色板
``build_qss(pal, assets)``   生成完整 Qt 样式表
``apply_theme(app, mode)``   应用主题（Fusion 基底 + 字体 + 样式表 + 素材）
``palette()`` / ``mode()``   读取当前主题
``set_mode(mode)``           切换并持久化主题模式
``series_colors(n)``         图表序列配色
``apply_shadow(widget)``     给指定控件加柔和投影（用于"英雄区"）

主题模式持久化于 ``~/.metcom/gui_prefs.json``；写入失败时静默降级为
内存态，不影响程序运行。
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from string import Template

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QWidget

# ------------------------------------------------------------------ 令牌 --


@dataclass(frozen=True)
class Palette:
    """一套完整的配色令牌。

    命名遵循语义而非色相（``accent`` 而不是 ``blue``），便于整体换肤。
    ``series`` 为图表分类配色，按顺序循环使用。
    """

    name: str            # "dark" | "light"
    label: str           # 中文显示名

    # 表面层级：由深到浅，层级越高越"靠近观察者"
    bg: str              # 窗口底
    sidebar: str         # 侧边导航底
    surface: str         # 卡片 / 面板
    surface_alt: str     # 次级面板：输入框、表头、悬停底
    surface_hi: str      # 更高层级：菜单、弹层

    # 描边
    border: str
    border_strong: str

    # 文本
    text: str
    text_muted: str
    text_faint: str

    # 品牌强调
    accent: str
    accent_hover: str
    accent_press: str
    accent_alt: str          # 渐变另一端（青）
    accent_soft: str         # 半透明柔光底（选中态）
    on_accent: str           # 强调色上的文字/图形

    # 语义色
    success: str
    warning: str
    danger: str
    info: str

    # 英雄区渐变（首页顶部横幅）
    hero_from: str
    hero_to: str

    # 控制台（两种主题下均保持深色，保证日志可读性）
    console_bg: str
    console_text: str

    series: tuple[str, ...]

    # ---------------- 图形令牌（pyqtgraph / matplotlib 两套引擎共用） ----------------
    # 说明：曲线、地图、雷达等可视化元素必须与界面同源取色，否则深浅主题
    # 切换后画布会"脱节"。这些令牌由 chart_style() 统一对外暴露。
    plot_bg: str          # 画布底色（比卡片更深，让数据线跳出来）
    plot_panel: str       # 画布内的浮层 / 小面板
    grid_line: str        # 网格线
    axis_line: str        # 坐标轴线
    axis_text: str        # 刻度文字
    crosshair: str        # 联动十字准线
    series_1: str         # 数据源固定色 1 —— ERA5
    series_2: str         # 数据源固定色 2 —— CMA-MESO
    series_3: str         # 数据源固定色 3 —— CRA40
    series_4: str         # 数据源固定色 4 —— CRA V1.5
    series_5: str         # 数据源固定色 5 —— MERRA2
    heatmap_cmap: str     # 数值热力图色带（单向量）
    div_cmap: str         # 偏差热力图色带（双向发散）


DARK = Palette(
    name="dark",
    label="深空",
    bg="#0A0F1C",
    sidebar="#0C1322",
    surface="#101726",
    surface_alt="#162033",
    surface_hi="#1B2740",
    border="#1E2A40",
    border_strong="#2E405E",
    text="#E8EEF9",
    text_muted="#8FA3C0",
    text_faint="#5F7191",
    accent="#2E9BF0",
    accent_hover="#4CACF5",
    accent_press="#1B84D8",
    accent_alt="#22D3EE",
    accent_soft="rgba(46, 155, 240, 0.16)",
    on_accent="#FFFFFF",
    success="#34D399",
    warning="#FBBF24",
    danger="#F87171",
    info="#38BDF8",
    hero_from="#12294A",
    hero_to="#0D1830",
    console_bg="#070C16",
    console_text="#9FB6D4",
    series=("#38BDF8", "#34D399", "#FBBF24", "#F472B6",
            "#A78BFA", "#FB7185", "#4ADE80", "#60A5FA"),

    plot_bg="#070C16",
    plot_panel="#0E1728",
    grid_line="#1A2942",
    axis_line="#2E405E",
    axis_text="#8FA3C0",
    crosshair="#38BDF8",
    series_1="#38BDF8",
    series_2="#F472B6",
    series_3="#FBBF24",
    series_4="#34D399",
    series_5="#A78BFA",
    heatmap_cmap="turbo",
    div_cmap="RdBu_r",
)

LIGHT = Palette(
    name="light",
    label="晨曦",
    bg="#F2F5FA",
    sidebar="#FFFFFF",
    surface="#FFFFFF",
    surface_alt="#F4F7FC",
    surface_hi="#FFFFFF",
    border="#E1E8F2",
    border_strong="#C6D3E4",
    text="#0E1A2B",
    text_muted="#5B6E88",
    text_faint="#8C9DB6",
    accent="#1478D4",
    accent_hover="#1E8AE8",
    accent_press="#0F63B4",
    accent_alt="#0891B2",
    accent_soft="rgba(20, 120, 212, 0.10)",
    on_accent="#FFFFFF",
    success="#16A34A",
    warning="#D97706",
    danger="#DC2626",
    info="#0284C7",
    hero_from="#E4F0FE",
    hero_to="#F7FAFF",
    console_bg="#0B1220",
    console_text="#A9C1DE",
    series=("#0EA5E9", "#10B981", "#F59E0B", "#EC4899",
            "#8B5CF6", "#F43F5E", "#22C55E", "#3B82F6"),

    plot_bg="#FFFFFF",
    plot_panel="#F4F7FC",
    grid_line="#E4EBF5",
    axis_line="#C6D3E4",
    axis_text="#5B6E88",
    crosshair="#1478D4",
    series_1="#0284C7",
    series_2="#DB2777",
    series_3="#B45309",
    series_4="#047857",
    series_5="#6D28D9",
    heatmap_cmap="turbo",
    div_cmap="RdBu_r",
)


# ------------------------------------------------------------------ 字体 --

UI_FONT_CANDIDATES = (
    "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC",
    "Source Han Sans SC", "Noto Sans CJK SC", "Segoe UI", "Sans Serif",
)
MONO_FONT_CANDIDATES = (
    "Cascadia Mono", "Cascadia Code", "JetBrains Mono",
    "Consolas", "DejaVu Sans Mono", "Courier New", "Monospace",
)


def pick_family(candidates: tuple[str, ...]) -> str:
    """返回系统中第一个可用的字体族（全部缺失时退回候选末位）。"""
    # 注意：没有 QGuiApplication 时查询字体族会让 Qt 直接崩溃（不是抛异常），
    # 因此必须先确认应用已创建，再问系统要字体清单。
    if QApplication.instance() is None:
        return candidates[0]
    try:
        available = set(QFontDatabase.families())
    except Exception:  # pragma: no cover - 无 QApplication 时的兜底
        return candidates[-1]
    for name in candidates:
        if name in available:
            return name
    return candidates[-1]


def build_font(size_pt: int = 10, weight: QFont.Weight = QFont.Weight.Normal,
               family: str | None = None) -> QFont:
    """构造界面字体（默认使用中文优先字体族）。"""
    font = QFont(family or pick_family(UI_FONT_CANDIDATES), size_pt)
    font.setWeight(weight)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def mono_font(size_pt: int = 10) -> QFont:
    """构造等宽字体（日志控制台用）。"""
    font = QFont(pick_family(MONO_FONT_CANDIDATES), size_pt)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def available_families() -> set:
    """系统可用字体族（未创建 QApplication 时返回空集合）。"""
    if QApplication.instance() is None:
        return set()
    try:
        return set(QFontDatabase.families())
    except Exception:  # pragma: no cover - 无 QApplication 时的兜底
        return set()


def font_report() -> dict:
    """字体自检：界面字体、等宽字体与中文字形是否可用。"""
    families = available_families()
    return {
        "ui": pick_family(UI_FONT_CANDIDATES),
        "mono": pick_family(MONO_FONT_CANDIDATES),
        "cjk": next((name for name in UI_FONT_CANDIDATES[:5] if name in families), ""),
        "count": len(families),
        "checked": bool(families),
    }


# ------------------------------------------------------- 运行期图标素材 --


def _cache_dir() -> Path:
    """控件素材缓存目录（不写入安装目录，避免只读部署失败）。"""
    path = Path(tempfile.gettempdir()) / "metcom_gui_assets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _render_asset(path: Path, size: tuple[int, int], draw) -> str:
    """离屏绘制一张 PNG 素材并返回 POSIX 风格路径（QSS 要求）。"""
    pixmap = QPixmap(*size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    try:
        draw(painter, size)
    finally:
        painter.end()
    pixmap.save(str(path), "PNG")
    return path.as_posix()


def ensure_assets(pal: Palette) -> dict[str, str]:
    """生成/复用当前主题所需的 QSS 图像素材，返回 {名称: 路径}。

    Qt 的 QSS 不支持内联矢量或 data-URI，勾选框、下拉箭头这类
    "必须由图片表达"的元素只能落到磁盘。素材按主题分别缓存，
    内容确定（同样的令牌生成同样的图），可安全复用。
    """
    root = _cache_dir()
    assets: dict[str, str] = {}

    def check(painter: QPainter, size: tuple[int, int]) -> None:
        pen = QPen(QColor(pal.on_accent))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        w, h = size
        path = QPainterPath()
        path.moveTo(w * 0.26, h * 0.52)
        path.lineTo(w * 0.44, h * 0.70)
        path.lineTo(w * 0.75, h * 0.32)
        painter.drawPath(path)

    def chevron(painter: QPainter, size: tuple[int, int]) -> None:
        pen = QPen(QColor(pal.text_muted))
        pen.setWidthF(1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        w, h = size
        path = QPainterPath()
        path.moveTo(w * 0.32, h * 0.42)
        path.lineTo(w * 0.50, h * 0.60)
        path.lineTo(w * 0.68, h * 0.42)
        painter.drawPath(path)

    def dot(painter: QPainter, size: tuple[int, int]) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(pal.on_accent))
        w, h = size
        painter.drawEllipse(int(w * 0.28), int(h * 0.28),
                            int(w * 0.44), int(h * 0.44))

    assets["check"] = _render_asset(root / f"check_{pal.name}.png", (20, 20), check)
    assets["chevron"] = _render_asset(root / f"chevron_{pal.name}.png", (24, 24), chevron)
    assets["dot"] = _render_asset(root / f"dot_{pal.name}.png", (18, 18), dot)
    return assets


# ------------------------------------------------------------------ QSS --

_QSS_TEMPLATE = Template("""
/* ============ 基础 ============ */
* { outline: 0; }
QWidget {
    color: $text;
    font-family: "$ui_font";
    font-size: 13px;
}
QMainWindow, QDialog { background-color: $bg; }
QWidget#Root { background-color: $bg; }
QLabel { background: transparent; }
QFrame { background: transparent; }

/* ============ 滚动条 ============ */
QScrollBar:vertical { background: transparent; width: 11px; margin: 2px 2px 2px 0; }
QScrollBar:horizontal { background: transparent; height: 11px; margin: 0 2px 2px 2px; }
QScrollBar::handle:vertical {
    background: $border_strong; border-radius: 4px; min-height: 32px;
}
QScrollBar::handle:horizontal {
    background: $border_strong; border-radius: 4px; min-width: 32px;
}
QScrollBar::handle:hover { background: $text_faint; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }

/* ============ 侧边导航 ============ */
QFrame#Sidebar {
    background-color: $sidebar;
    border-right: 1px solid $border;
}
QLabel#BrandName {
    color: $text; font-size: 17px; font-weight: 700;
}
QLabel#BrandTag {
    color: $text_faint; font-size: 11px;
}
QLabel#NavGroup {
    color: $text_faint; font-size: 11px; font-weight: 600;
    padding: 0 14px;
}
QPushButton#NavButton {
    background: transparent;
    color: $text_muted;
    border: none;
    border-radius: 8px;
    padding: 9px 12px;
    text-align: left;
    font-size: 13px;
}
QPushButton#NavButton:hover { background-color: $surface_alt; color: $text; }
QPushButton#NavButton:checked {
    background-color: $accent_soft; color: $accent; font-weight: 600;
}
QFrame#NavMark { background-color: $accent; border-radius: 2px; }
QFrame#SidebarSep { background-color: $border; }
QLabel#SidebarVersion { color: $text_faint; font-size: 11px; }

/* ============ 顶栏 ============ */
QFrame#TopBar { background-color: $bg; border-bottom: 1px solid $border; }
QLabel#PageTitle { color: $text; font-size: 19px; font-weight: 700; }
QLabel#PageSubtitle { color: $text_muted; font-size: 12px; }

/* ============ 英雄区 ============ */
QFrame#Hero {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 $hero_from, stop:1 $hero_to);
    border: 1px solid $border;
    border-radius: 14px;
}
QLabel#HeroTitle { color: $text; font-size: 22px; font-weight: 700; }
QLabel#HeroText { color: $text_muted; font-size: 13px; }
QLabel#HeroChip {
    color: $accent; background-color: $accent_soft;
    border-radius: 11px; padding: 4px 11px; font-size: 12px; font-weight: 600;
}

/* ============ 卡片 ============ */
QFrame#Card {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 12px;
}
QFrame#CardHover:hover { border-color: $border_strong; }
QLabel#CardTitle { color: $text; font-size: 14px; font-weight: 600; }
QLabel#CardDesc { color: $text_muted; font-size: 12px; }
QLabel#SectionLabel {
    color: $text_faint; font-size: 11px; font-weight: 700;
}
QLabel#MetricValue { color: $text; font-size: 26px; font-weight: 700; }
QLabel#MetricUnit { color: $text_muted; font-size: 12px; }
QLabel#MetricLabel { color: $text_muted; font-size: 12px; }
QLabel#Hint { color: $text_faint; font-size: 11px; }
QLabel#Mono { font-family: "$mono_font"; color: $text_muted; font-size: 12px; }
QFrame#Separator { background-color: $border; }
QLabel#StepBadge {
    color: $accent; background-color: $accent_soft;
    border-radius: 9px; font-size: 11px; font-weight: 700;
}

/* ============ 按钮 ============ */
QPushButton#PrimaryButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 $accent, stop:1 $accent_alt);
    color: $on_accent;
    border: none; border-radius: 9px;
    padding: 9px 20px; font-size: 13px; font-weight: 600;
}
QPushButton#PrimaryButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 $accent_hover, stop:1 $accent_alt);
}
QPushButton#PrimaryButton:pressed { background: $accent_press; }
QPushButton#PrimaryButton:disabled {
    background: $surface_alt; color: $text_faint;
}
QPushButton#GhostButton {
    background-color: $surface_alt;
    color: $text;
    border: 1px solid $border;
    border-radius: 9px;
    padding: 8px 16px;
}
QPushButton#GhostButton:hover { border-color: $border_strong; background-color: $surface_hi; }
QPushButton#GhostButton:pressed { background-color: $surface; }
QPushButton#GhostButton:disabled { color: $text_faint; border-color: $border; }
QPushButton#DangerButton {
    background-color: transparent;
    color: $danger;
    border: 1px solid $danger;
    border-radius: 9px;
    padding: 8px 16px;
}
QPushButton#DangerButton:hover { background-color: rgba(248, 113, 113, 0.12); }
QPushButton#DangerButton:disabled { color: $text_faint; border-color: $border; }
QPushButton#IconButton {
    background: transparent; border: none; border-radius: 8px; padding: 6px;
}
QPushButton#IconButton:hover { background-color: $surface_alt; }
QPushButton#LinkButton {
    background: transparent; border: none; color: $accent;
    padding: 2px 4px; text-align: left; font-size: 12px;
}
QPushButton#LinkButton:hover { color: $accent_hover; }

/* ============ 输入控件 ============ */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
    background-color: $surface_alt;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 7px 10px;
    color: $text;
    selection-background-color: $accent;
    selection-color: $on_accent;
}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {
    border-color: $border_strong;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {
    border-color: $accent;
}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
QComboBox:disabled, QPlainTextEdit:disabled {
    color: $text_faint; background-color: $surface;
}
QLineEdit[readOnly="true"] { color: $text_muted; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow { image: url($chevron_asset); width: 24px; height: 24px; }
QComboBox QAbstractItemView {
    background-color: $surface_hi;
    border: 1px solid $border_strong;
    border-radius: 8px;
    padding: 4px;
    selection-background-color: $accent_soft;
    selection-color: $accent;
    outline: 0;
}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    background: transparent; border: none; width: 16px;
}

/* ============ 复选 / 单选 ============ */
QCheckBox, QRadioButton { background: transparent; color: $text; spacing: 8px; }
QCheckBox::indicator {
    width: 20px; height: 20px;
    border: 1px solid $border_strong; border-radius: 6px;
    background-color: $surface_alt;
}
QCheckBox::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked {
    background-color: $accent; border-color: $accent;
    image: url($check_asset);
}
QRadioButton::indicator {
    width: 18px; height: 18px;
    border: 1px solid $border_strong; border-radius: 10px;
    background-color: $surface_alt;
}
QRadioButton::indicator:hover { border-color: $accent; }
QRadioButton::indicator:checked {
    background-color: $accent; border-color: $accent;
    image: url($dot_asset);
}

/* ============ 进度条 ============ */
QProgressBar {
    background-color: $surface_alt;
    border: 1px solid $border;
    border-radius: 8px;
    height: 16px;
    text-align: center;
    color: $text_muted;
    font-size: 11px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 $accent, stop:1 $accent_alt);
    border-radius: 7px;
}
QProgressBar#Indeterminate::chunk { border-radius: 7px; }

/* ============ 控制台 ============ */
QPlainTextEdit#Console {
    background-color: $console_bg;
    border: 1px solid $border;
    border-radius: 12px;
    font-family: "$mono_font";
    font-size: 12px;
    color: $console_text;
    padding: 10px;
    selection-background-color: $accent;
}
QPlainTextEdit#ConsoleReadonly { background-color: $console_bg; }

/* ============ 表格 ============ */
QTableWidget, QTableView, QListWidget, QTreeWidget {
    background-color: $surface;
    alternate-background-color: $surface_alt;
    gridline-color: $border;
    border: 1px solid $border;
    border-radius: 10px;
    selection-background-color: $accent_soft;
    selection-color: $text;
    outline: 0;
}
QTableWidget::item, QListWidget::item { padding: 6px 8px; border: none; }
QListWidget::item:selected { background-color: $accent_soft; color: $accent; }
QHeaderView::section {
    background-color: $surface_alt;
    color: $text_muted;
    border: none;
    border-bottom: 1px solid $border;
    border-right: 1px solid $border;
    padding: 8px 10px;
    font-weight: 600;
}
QHeaderView::section:last { border-right: none; }
QTableCornerButton::section { background-color: $surface_alt; border: none; }

/* ============ 选项卡 ============ */
QTabWidget::pane {
    border: 1px solid $border; border-radius: 10px;
    background-color: $surface; top: -1px;
}
QTabBar::tab {
    background: transparent; color: $text_muted;
    padding: 8px 16px; margin-right: 4px;
    border: 1px solid transparent;
    border-top-left-radius: 8px; border-top-right-radius: 8px;
}
QTabBar::tab:hover { color: $text; }
QTabBar::tab:selected {
    background-color: $surface; color: $accent;
    border-color: $border; border-bottom-color: $surface;
    font-weight: 600;
}

/* ============ 分组框 ============ */
QGroupBox {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 10px;
    margin-top: 14px;
    padding: 16px 14px 12px 14px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px; padding: 0 6px;
    color: $text_muted; font-weight: 600;
}

/* ============ 分隔器 / 状态栏 / 菜单 ============ */
QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal { width: 6px; }
QSplitter::handle:vertical { height: 6px; }
QStatusBar {
    background-color: $sidebar;
    border-top: 1px solid $border;
    color: $text_muted;
}
QStatusBar::item { border: none; }
QStatusBar QLabel { color: $text_muted; font-size: 12px; }
QMenuBar { background: transparent; }
QMenuBar::item { padding: 6px 10px; border-radius: 6px; background: transparent; }
QMenuBar::item:selected { background-color: $surface_alt; }
QMenu {
    background-color: $surface_hi;
    border: 1px solid $border_strong;
    border-radius: 10px;
    padding: 6px;
}
QMenu::item { padding: 7px 24px 7px 12px; border-radius: 6px; }
QMenu::item:selected { background-color: $accent_soft; color: $accent; }
QMenu::separator { height: 1px; background-color: $border; margin: 6px 8px; }

/* ============ 提示 ============ */
QToolTip {
    background-color: $surface_hi;
    color: $text;
    border: 1px solid $border_strong;
    border-radius: 6px;
    padding: 6px 9px;
}

/* ============ 无边框外壳 / 标题栏 ============ */
QWidget#Shell {
    background: transparent;
}
QFrame#ShellBody {
    background-color: $bg;
    border: 1px solid $border_strong;
    border-radius: 16px;
}
QFrame#ShellBody[flat="true"] {
    background-color: $bg;
    border: 1px solid $border;
    border-radius: 0px;
}
QFrame#TitleBar {
    background: transparent;
    border-bottom: 1px solid $border;
}
QFrame#Sidebar { border-bottom-left-radius: 15px; }
QFrame#Sidebar[flat="true"] { border-bottom-left-radius: 0px; }
QFrame#LogDrawer { border-bottom-right-radius: 15px; }
QFrame#LogDrawer[flat="true"] { border-bottom-right-radius: 0px; }
QLabel#Crumb { color: $text_faint; font-size: 12px; }
QLabel#CrumbSep { color: $text_faint; font-size: 11px; }
QLabel#CrumbStrong { color: $text; font-size: 12px; font-weight: 600; }
QLabel#CrumbTail { color: $text_muted; font-size: 12px; }
QPushButton#ChromeButton {
    background: transparent;
    border: none;
    border-radius: 7px;
    color: $text_muted;
    padding: 0px;
}
QPushButton#ChromeButton:hover { background-color: $surface_alt; color: $text; }
QPushButton#ChromeButton:pressed { background-color: $surface; }
QPushButton#ChromeButton:disabled { color: $text_faint; }
QPushButton#ChromeClose:hover {
    background-color: $danger;
    color: #FFFFFF;
}
QPushButton#ChromeClose:pressed { background-color: $danger; }
QPushButton#ToolChip {
    background-color: $surface_alt;
    border: 1px solid $border;
    border-radius: 9px;
    color: $text_muted;
    font-size: 12px;
    padding: 4px 10px;
}
QPushButton#ToolChip:hover { border-color: $border_strong; color: $text; }
QPushButton#ToolChip:checked {
    background-color: $accent_soft;
    border-color: $accent;
    color: $accent;
    font-weight: 600;
}
QLabel#LampText { color: $text_muted; font-size: 12px; }

/* ============ 可视化工作台 ============ */
QFrame#Rail {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 12px;
}
QLabel#RailHead {
    color: $text_faint;
    font-size: 11px;
    font-weight: 600;
}
QPushButton#RailButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
    color: $text_muted;
    font-size: 12px;
    padding: 8px 10px;
    text-align: left;
}
QPushButton#RailButton:hover { background-color: $surface_alt; color: $text; }
QPushButton#RailButton:checked {
    background-color: $accent_soft;
    border-color: $accent;
    color: $accent;
    font-weight: 600;
}
QFrame#CanvasHost {
    background-color: $plot_bg;
    border: 1px solid $border;
    border-radius: 12px;
}
QWidget#CanvasArea { background: transparent; }
QLabel#CanvasTitle { color: $text; font-size: 14px; font-weight: 600; }
QLabel#CanvasHint { color: $text_faint; font-size: 11px; }
QLabel#OriginChip {
    background-color: $accent_soft;
    border: 1px solid $accent;
    border-radius: 9px;
    color: $accent;
    font-size: 11px;
    font-weight: 600;
    padding: 2px 10px;
}
QLabel#OriginChip[demo="true"] {
    background-color: rgba(251, 191, 36, 0.14);
    border-color: $warning;
    color: $warning;
}
QLabel#StatPill {
    background-color: $surface_alt;
    border: 1px solid $border;
    border-radius: 8px;
    color: $text_muted;
    font-size: 11px;
    padding: 3px 9px;
}

/* ============ 日志抽屉 ============ */
QFrame#LogDrawer {
    background-color: $sidebar;
    border-left: 1px solid $border;
    border-bottom-right-radius: 15px;
}
QFrame#LogDrawerHead { background: transparent; }
QLabel#DrawerTitle { color: $text; font-size: 13px; font-weight: 600; }
QPushButton#DrawerTab {
    background-color: $surface;
    border: 1px solid $border;
    border-top-left-radius: 9px;
    border-bottom-left-radius: 9px;
    color: $text_muted;
    font-size: 11px;
    font-weight: 600;
    padding: 0px;
}
QPushButton#DrawerTab:hover { color: $text; border-color: $border_strong; }
QPushButton#DrawerTab:checked { color: $accent; border-color: $accent; }

/* ============ 启动画面 ============ */
QLabel#SplashTitle { color: $text; font-size: 27px; font-weight: 700; }
QLabel#SplashSub { color: $text_muted; font-size: 12px; }
QLabel#SplashStage { color: $text_muted; font-size: 12px; }
QLabel#SplashVersion { color: $text_faint; font-size: 11px; }
""")


def build_qss(pal: Palette, assets: dict[str, str] | None = None) -> str:
    """生成完整样式表（``assets`` 为空时自动生成控件素材）。"""
    assets = assets or ensure_assets(pal)
    context = asdict(pal)
    context.update(
        ui_font=pick_family(UI_FONT_CANDIDATES),
        mono_font=pick_family(MONO_FONT_CANDIDATES),
        check_asset=assets["check"],
        chevron_asset=assets["chevron"],
        dot_asset=assets["dot"],
    )
    return _QSS_TEMPLATE.substitute(context)


def apply_shadow(widget: QWidget, blur: int = 26, dy: int = 6,
                 color: str = "#000000", alpha: int = 70) -> None:
    """给控件加柔和投影（用于英雄区/浮动卡片）。"""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, dy)
    c = QColor(color)
    c.setAlpha(alpha)
    effect.setColor(c)
    widget.setGraphicsEffect(effect)


# ------------------------------------------------------------- 主题状态 --

_PREFS_PATH = Path.home() / ".metcom" / "gui_prefs.json"
_STATE: dict[str, str] = {"mode": "dark"}


def _load_pref_mode() -> str | None:
    try:
        data = json.loads(_PREFS_PATH.read_text(encoding="utf-8"))
        mode = data.get("mode")
        return mode if mode in ("dark", "light") else None
    except Exception:
        return None


def _save_pref_mode(mode: str) -> None:
    try:
        _PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PREFS_PATH.write_text(json.dumps({"mode": mode}, ensure_ascii=False),
                               encoding="utf-8")
    except Exception:
        pass


def palette() -> Palette:
    """当前调色板。"""
    return LIGHT if _STATE["mode"] == "light" else DARK


def mode() -> str:
    """当前主题模式（``"dark"`` / ``"light"``）。"""
    return _STATE["mode"]


def series_colors(n: int) -> list[str]:
    """取 n 个图表序列配色（循环使用内置分类色板）。"""
    base = list(palette().series)
    return [base[i % len(base)] for i in range(max(0, n))]


def source_series_colors(pal: Palette | None = None) -> tuple[str, ...]:
    """五个数据源的固定专属色（顺序与 SOURCE_KEYS 对齐）。

    传入 ``pal`` 则取该调色板的五源色：``chart_style(mode)`` 依赖这一行为，
    否则指定主题时会拿到"当前主题"的色值，两套引擎的取色就不同源了。
    """
    pal = pal if pal is not None else palette()   # 未指定则取当前主题
    return (pal.series_1, pal.series_2, pal.series_3, pal.series_4, pal.series_5)


def chart_style(mode_name: str | None = None) -> dict:
    """绘图引擎共用样式（pyqtgraph 与 matplotlib 都从这里取色）。

    返回字典包含画布底色、网格/轴线/刻度色、十字准线、五源固定色、
    热力图色带名与中英文字体族，保证"界面换肤 -> 图表同步换肤"。
    """
    if mode_name is None:
        pal = palette()
    else:
        pal = LIGHT if mode_name == "light" else DARK
    return {
        "mode": pal.name,
        "bg": pal.plot_bg,
        "panel": pal.plot_panel,
        "grid": pal.grid_line,
        "axis": pal.axis_line,
        "axis_text": pal.axis_text,
        "text": pal.text,
        "muted": pal.text_muted,
        "accent": pal.accent,
        "border": pal.border,
        "crosshair": pal.crosshair,
        "cmap": pal.heatmap_cmap,
        "div_cmap": pal.div_cmap,
        "sources": list(source_series_colors(pal)),
        "series": list(pal.series),
        "font": pick_family(UI_FONT_CANDIDATES),
        "mono": pick_family(MONO_FONT_CANDIDATES),
    }


def apply_theme(app: QApplication, mode_name: str | None = None) -> Palette:
    """应用主题：Fusion 基底 + 字体 + 样式表；返回生效的调色板。

    Fusion 样式是 Qt 中唯一在各平台都完整遵循 QSS 的内置风格，
    因此作为统一基底，保证 Windows/Linux/macOS 观感一致。
    """
    if mode_name in ("dark", "light"):
        _STATE["mode"] = mode_name
    else:
        _STATE["mode"] = _load_pref_mode() or _STATE["mode"]

    pal = palette()
    app.setStyle("Fusion")
    app.setFont(build_font(10))
    app.setStyleSheet(build_qss(pal, ensure_assets(pal)))
    return pal


def set_mode(mode_name: str, app: QApplication | None = None) -> Palette:
    """切换主题模式并持久化；传入 app 时立即重新应用样式。"""
    _STATE["mode"] = "light" if mode_name == "light" else "dark"
    _save_pref_mode(_STATE["mode"])
    if app is not None:
        return apply_theme(app, _STATE["mode"])
    return palette()


__all__ = [
    "Palette", "DARK", "LIGHT", "build_qss", "build_font", "mono_font",
    "pick_family", "apply_theme", "palette", "mode", "set_mode",
    "series_colors", "source_series_colors", "chart_style", "apply_shadow",
    "ensure_assets", "UI_FONT_CANDIDATES", "MONO_FONT_CANDIDATES",
]
