"""内置矢量图标集与应用图标。

为什么不用 PNG 图标包：科研软件常被投到大屏或做 200% 缩放演示，位图会糊。
这里所有图标都是 24x24 视口内的 SVG 描边路径（Lucide 风格：圆头圆角、
1.8px 描边），在任意尺寸/DPI 下由 QtSvgRenderer 实时渲染，永远锐利，
且颜色可随主题动态注入，无需维护多套配色资源。

对外接口
--------
``icon(name, color=None, size=20)``   取 QIcon（默认用主题次要文字色）
``pixmap(name, color, size)``         取 QPixmap
``app_logo(size)``                    应用图标（渐变圆角方块 + 大气分层标记）
``names()``                           全部可用图标名
"""

from __future__ import annotations

import re
from functools import lru_cache

from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QIcon, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap)
from PySide6.QtSvg import QSvgRenderer

from metcom.gui import theme

# 底托圆角：48px 徽章对应 14px 圆角（规格值），其余尺寸按比例缩放
PLATE_RADIUS_RATIO = 14.0 / 48.0

# --------------------------------------------------------------- 图标路径 --
# 每个条目是 24x24 视口内的 SVG 片段；统一 stroke=currentColor，
# 由 _svg_document() 注入实际颜色与描边参数。
_ICONS: dict[str, str] = {
    # 导航
    "home": ('<path d="M3 11.2 12 3.2l9 8"/>'
             '<path d="M5.2 10v10.2h13.6V10"/>'
             '<path d="M9.8 20.2v-5.8h4.4v5.8"/>'),
    "grid": ('<rect x="3.2" y="3.2" width="7.2" height="7.2" rx="1.6"/>'
             '<rect x="13.6" y="3.2" width="7.2" height="7.2" rx="1.6"/>'
             '<rect x="3.2" y="13.6" width="7.2" height="7.2" rx="1.6"/>'
             '<rect x="13.6" y="13.6" width="7.2" height="7.2" rx="1.6"/>'),
    "download": ('<path d="M12 3.4v11.4"/>'
                 '<path d="M7.2 10.4 12 15.2l4.8-4.8"/>'
                 '<path d="M4.2 20.4h15.6"/>'),
    "pulse": ('<path d="M2.6 12.4h3.6l2.3-6.6 3 13.2 2.4-6.6h7.5"/>'),
    "clock": ('<circle cx="12" cy="12" r="8.6"/>'
              '<path d="M12 7.2V12l3.4 2.1"/>'),
    "chart": ('<path d="M3.4 20.4h17.2"/>'
              '<path d="M6.6 20.4v-6.9"/>'
              '<path d="M12 20.4V5.6"/>'
              '<path d="M17.4 20.4v-9.6"/>'),
    "map": ('<path d="M9.2 3.4 3.4 6.2v14.4l5.8-2.8 5.6 2.8 5.8-2.8V3.4l-5.8 2.8z"/>'
            '<path d="M9.2 3.4v14.4"/>'
            '<path d="M14.8 6.2v14.4"/>'),
    "sliders": ('<path d="M4 7.2h16"/><path d="M4 12h16"/><path d="M4 16.8h16"/>'
                '<circle cx="9" cy="7.2" r="2.1"/>'
                '<circle cx="15" cy="12" r="2.1"/>'
                '<circle cx="7.6" cy="16.8" r="2.1"/>'),
    # 动作
    "play": '<path d="M7.4 4.8 19 12 7.4 19.2z"/>',
    "stop": '<rect x="6.4" y="6.4" width="11.2" height="11.2" rx="2.2"/>',
    "refresh": ('<path d="M20.2 12a8.2 8.2 0 1 1-2.4-5.8"/>'
                '<path d="M20.4 4.6v5.2h-5.2"/>'),
    "folder": ('<path d="M3.2 6.6A1.6 1.6 0 0 1 4.8 5h4.1l2.1 2.6h8.2'
               'A1.6 1.6 0 0 1 20.8 9.2v9.2A1.6 1.6 0 0 1 19.2 20H4.8'
               'A1.6 1.6 0 0 1 3.2 18.4z"/>'),
    "save": ('<path d="M5.2 3.8h10.2l3.4 3.4v13H5.2z"/>'
             '<path d="M8.8 3.8v4.8h5.6V3.8"/>'
             '<path d="M8.4 13.6h7.2"/>'),
    "wand": ('<path d="M5 19 16.4 7.6"/>'
             '<path d="M14.6 5.8 18.2 9.4"/>'
             '<path d="M17.6 3.2v3.4"/><path d="M15.9 4.9h3.4"/>'
             '<path d="M6.6 4.4v2.6"/><path d="M5.3 5.7h2.6"/>'),
    "search": '<circle cx="10.8" cy="10.8" r="6.4"/><path d="M15.6 15.6 20.4 20.4"/>',
    "eye": ('<path d="M2.6 12S6.4 5.8 12 5.8 21.4 12 21.4 12'
            ' 17.6 18.2 12 18.2 2.6 12 2.6 12z"/><circle cx="12" cy="12" r="2.9"/>'),
    "window": ('<rect x="3.2" y="4.4" width="17.6" height="15.2" rx="2.2"/>'
               '<path d="M3.2 9.2h17.6"/><circle cx="6.4" cy="6.8" r="0.9"/>'
               '<circle cx="9.2" cy="6.8" r="0.9"/>'),
    # 状态
    "check": '<path d="M4.8 12.6 9.8 17.6 19.2 6.8"/>',
    "check-circle": '<circle cx="12" cy="12" r="8.6"/><path d="M8.2 12.2 11 15l5-6"/>',
    "alert": ('<path d="M12 4.2 21 19.4H3z"/><path d="M12 10v4.2"/>'
              '<circle cx="12" cy="16.9" r="0.9"/>'),
    "info": '<circle cx="12" cy="12" r="8.6"/><path d="M12 11v5.4"/><path d="M12 7.8v0.9"/>',
    "close": '<path d="M6.2 6.2 17.8 17.8"/><path d="M17.8 6.2 6.2 17.8"/>',
    "external": ('<path d="M14 4.4h5.6V10"/><path d="M19.6 4.4 11.4 12.6"/>'
                 '<path d="M18 14.4v4.2a1.4 1.4 0 0 1-1.4 1.4H5.8a1.4 1.4 0 0 1-1.4-1.4V7.8'
                 'a1.4 1.4 0 0 1 1.4-1.4h4.2"/>'),
    # 主题
    "sun": ('<circle cx="12" cy="12" r="4.2"/>'
            '<path d="M12 2.6v2.2"/><path d="M12 19.2v2.2"/>'
            '<path d="M2.6 12h2.2"/><path d="M19.2 12h2.2"/>'
            '<path d="M5.4 5.4 7 7"/><path d="M17 17l1.6 1.6"/>'
            '<path d="M18.6 5.4 17 7"/><path d="M7 17l-1.6 1.6"/>'),
    "moon": '<path d="M20.4 14.6A8.8 8.8 0 0 1 9.4 3.6a8.8 8.8 0 1 0 11 11z"/>',
    # 领域
    "globe": ('<circle cx="12" cy="12" r="8.6"/><path d="M3.4 12h17.2"/>'
              '<path d="M12 3.4a13 13 0 0 1 0 17.2a13 13 0 0 1 0-17.2z"/>'),
    "layers": ('<path d="M12 3.4 3.4 8 12 12.6 20.6 8z"/>'
               '<path d="M3.4 13.2 12 17.8 20.6 13.2"/>'),
    "droplet": ('<path d="M12 3.4s6.2 6.6 6.2 10.6a6.2 6.2 0 1 1-12.4 0'
                'C5.8 10 12 3.4 12 3.4z"/>'),
    "thermometer": ('<path d="M14.4 15V5.2a2.4 2.4 0 1 0-4.8 0V15a4.2 4.2 0 1 0 4.8 0z"/>'
                    '<path d="M12 9.6v6.6"/>'),
    "gauge": ('<path d="M4 17.6a9 9 0 1 1 16 0"/><path d="M12 17.6 16.2 11"/>'
              '<circle cx="12" cy="17.6" r="1.4"/>'),
    "terminal": ('<rect x="3.2" y="4.6" width="17.6" height="14.8" rx="2.2"/>'
                 '<path d="M7 9.6l2.8 2.6L7 14.8"/><path d="M12.4 15.2h4.4"/>'),
    "cpu": ('<rect x="7" y="7" width="10" height="10" rx="2.2"/>'
            '<path d="M10 3.4v3.6"/><path d="M14 3.4v3.6"/>'
            '<path d="M10 17v3.6"/><path d="M14 17v3.6"/>'
            '<path d="M3.4 10h3.6"/><path d="M3.4 14h3.6"/>'
            '<path d="M17 10h3.6"/><path d="M17 14h3.6"/>'),
    "calendar": ('<rect x="3.6" y="5.2" width="16.8" height="15.2" rx="2.2"/>'
                 '<path d="M3.6 10h16.8"/><path d="M8.4 3.4v3.6"/>'
                 '<path d="M15.6 3.4v3.6"/>'),
}

_SVG_DOC = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
    'width="{size}" height="{size}" fill="none" stroke="{color}" '
    'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round">'
    '{body}</svg>'
)


def names() -> list[str]:
    """全部可用图标名。"""
    return sorted(_ICONS)


def _svg_document(name: str, color: str, size: int, width: float) -> bytes:
    try:
        body = _ICONS[name]
    except KeyError:  # pragma: no cover - 开发期快速暴露笔误
        raise KeyError(f"未知图标 '{name}'，可用: {', '.join(names())}") from None
    return _SVG_DOC.format(size=size, color=color, width=width, body=body).encode("utf-8")


@lru_cache(maxsize=512)
def pixmap(name: str, color: str, size: int = 20, width: float = 1.8) -> QPixmap:
    """渲染图标为 QPixmap（按设备像素比放大，保证高分屏锐利）。"""
    from PySide6.QtWidgets import QApplication

    ratio = 2.0
    if QApplication.instance() is not None:
        screen = QApplication.primaryScreen()
        if screen is not None:
            ratio = float(screen.devicePixelRatio() or 1.0)
    physical = max(1, int(round(size * ratio)))
    pm = QPixmap(physical, physical)
    pm.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(_svg_document(name, color, physical, width * ratio))
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    pm.setDevicePixelRatio(ratio)
    return pm


@lru_cache(maxsize=512)
def icon(name: str, color: str | None = None, size: int = 20,
         width: float = 1.8) -> QIcon:
    """取 QIcon；``color`` 为空时使用当前主题的次要文字色。"""
    color = color or theme.palette().text_muted
    return QIcon(pixmap(name, QColor(color).name(), size, width))


def draw_app_logo(painter: QPainter, size: int, pal: theme.Palette | None = None,
                  rounded: bool = True) -> None:
    """在给定画笔上绘制应用徽标（窗口图标 / 启动画面 / 侧栏品牌区共用）。

    构图语言：135° 渐变底托 + 顶部柔光 + 内描边高光，主体是"地球 + 大气分层弧
    + 天顶射线 + 倾斜卫星轨道"。四层弧线对应大气分层，天顶射线与卫星轨道
    对应 GNSS 信号穿越大气产生延迟 —— 一眼能读出"天顶对流层延迟反演"。
    """
    pal = pal or theme.palette()
    s = float(size)
    white = QColor("#FFFFFF")

    if rounded:
        _draw_plate(painter, s, QColor(pal.accent), QColor(pal.accent_alt))

    def pen_of(color: QColor, width: float) -> QPen:
        pen = QPen(color)
        pen.setWidthF(width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return pen

    cx = s * 0.5
    earth_y = s * 0.715
    r = s * 0.145

    painter.setBrush(Qt.BrushStyle.NoBrush)
    if rounded:
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, s, s), s * PLATE_RADIUS_RATIO,
                            s * PLATE_RADIUS_RATIO)
    else:
        clip = QPainterPath()
        clip.addRect(QRectF(0, 0, s, s))
    painter.save()
    painter.setClipPath(clip)

    # 倾斜卫星轨道（暗示"从太空观测大气"）
    orbit = QColor(white)
    orbit.setAlpha(120)
    painter.setPen(pen_of(orbit, max(1.1, s * 0.032)))
    painter.save()
    painter.translate(cx, earth_y)
    painter.rotate(-27.0)
    painter.drawEllipse(QRectF(-s * 0.42, -s * 0.185, s * 0.84, s * 0.37))
    painter.restore()

    # 大气分层弧：三层同心弧，越外层越淡
    for radius, alpha, weight in ((s * 0.255, 245, 0.055),
                                  (s * 0.375, 170, 0.048),
                                  (s * 0.487, 95, 0.040)):
        color = QColor(white)
        color.setAlpha(alpha)
        painter.setPen(pen_of(color, max(1.15, s * weight)))
        rect = QRectF(cx - radius, earth_y - radius, radius * 2, radius * 2)
        # 只保留上半弧（Qt 角度逆时针，单位 1/16 度）
        painter.drawArc(rect, int(28 * 16), int(124 * 16))

    # 天顶射线（信号路径）+ 顶端信号点
    painter.setPen(pen_of(white, max(1.3, s * 0.05)))
    painter.drawLine(QPointF(cx, earth_y - r * 1.1), QPointF(cx, s * 0.205))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(white)
    painter.drawEllipse(QPointF(cx, s * 0.158), s * 0.055, s * 0.055)

    # 星球本体：白色实心 + 静脉线（只在球内绘制）
    painter.drawEllipse(QPointF(cx, earth_y), r, r)
    painter.save()
    planet = QPainterPath()
    planet.addEllipse(QPointF(cx, earth_y), r, r)
    painter.setClipPath(planet)
    meridian = QColor(pal.accent)
    meridian.setAlpha(150)
    painter.setPen(pen_of(meridian, max(0.85, r * 0.14)))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QRectF(cx - r * 0.42, earth_y - r, r * 0.84, r * 2))
    painter.drawLine(QPointF(cx - r * 1.1, earth_y + r * 0.34),
                     QPointF(cx + r * 1.1, earth_y - r * 0.10))
    painter.restore()

    painter.restore()


def _draw_plate(painter: QPainter, s: float, c1: QColor, c2: QColor,
                radius_ratio: float = PLATE_RADIUS_RATIO, gloss: bool = True) -> None:
    """徽章底托：135° 线性渐变 + 顶部柔光 + 1px 内高光描边。"""
    radius = s * radius_ratio
    grad = QLinearGradient(0.0, 0.0, s, s)
    grad.setColorAt(0.0, c1.lighter(114))
    grad.setColorAt(0.55, c1)
    grad.setColorAt(1.0, c2)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(grad)
    painter.drawRoundedRect(QRectF(0, 0, s, s), radius, radius)

    if gloss:
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, s, s), radius, radius)
        painter.save()
        painter.setClipPath(path)
        gloss_grad = QLinearGradient(0.0, 0.0, 0.0, s * 0.62)
        gloss_grad.setColorAt(0.0, QColor(255, 255, 255, 48))
        gloss_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(gloss_grad)
        painter.drawRect(QRectF(0, 0, s, s * 0.62))
        painter.restore()

    pen = QPen(QColor(255, 255, 255, 60))
    pen.setWidthF(max(1.0, s * 0.014))
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    inset = pen.widthF() * 0.5
    painter.drawRoundedRect(QRectF(inset, inset, s - 2 * inset, s - 2 * inset),
                            radius - inset, radius - inset)


@lru_cache(maxsize=32)
def app_logo(size: int = 64, mode_name: str = "dark") -> QPixmap:
    """应用图标（渐变圆角方块 + 大气分层标记），用作窗口/任务栏图标。"""
    pal = theme.LIGHT if mode_name == "light" else theme.DARK
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    try:
        draw_app_logo(painter, size, pal)
    finally:
        painter.end()
    return pm


# =========================================================== 双色调徽章 --
# 设计动机：把 24x24 线稿直接当功能图标放大用，界面会显得单薄。这里给每个
# 图标配一块 135 度渐变的圆角底托（48x48、圆角 14px），再把线稿按"主笔触
# 实色 + 次笔触 60% 透明"两层描边叠上去，形成成套彩色徽章——既区分功能
# 分区，又不必维护任何位图资源，任何 DPI 下都锐利。

_EXTRA_ICONS: dict[str, str] = {
    # 窗口控制
    "minimize": '<path d="M5 18.4h14"/>',
    "maximize": '<rect x="4.8" y="4.8" width="14.4" height="14.4" rx="2.6"/>',
    "restore": ('<rect x="3.8" y="8.2" width="12" height="12" rx="2.2"/>'
                '<path d="M8.2 8.2V6a1.6 1.6 0 0 1 1.6-1.6h8.6A1.6 1.6 0 0 1 20 6v8.6'
                'a1.6 1.6 0 0 1-1.6 1.6h-2.2"/>'),
    # 工具条 / 抽屉
    "chevron-left": '<path d="M14.6 5.2 7.8 12l6.8 6.8"/>',
    "chevron-right": '<path d="M9.4 5.2 16.2 12l-6.8 6.8"/>',
    "chevron-down": '<path d="M5.6 9.4 12 15.8l6.4-6.4"/>',
    "panel-right": ('<rect x="3.4" y="4.4" width="17.2" height="15.2" rx="2.4"/>'
                    '<path d="M14.4 4.4v15.2"/>'),
    "panel-left": ('<rect x="3.4" y="4.4" width="17.2" height="15.2" rx="2.4"/>'
                   '<path d="M9.6 4.4v15.2"/>'),
    # 可视化工作台
    "radar": ('<circle cx="12" cy="12" r="8.6"/><circle cx="12" cy="12" r="5.2"/>'
              '<path d="M12 3.4V12l7.6-3.6"/>'
              '<path d="M12 12 4.2 15.6"/><path d="M12 12l-1.2 8.2"/>'),
    "table": ('<rect x="3.4" y="4.6" width="17.2" height="14.8" rx="2.4"/>'
              '<path d="M3.4 9.6h17.2"/><path d="M9.6 9.6v9.8"/>'
              '<path d="M3.4 14.4h17.2"/>'),
    "zoom-in": ('<circle cx="10.8" cy="10.8" r="6.4"/><path d="M15.6 15.6 20.4 20.4"/>'
                '<path d="M10.8 8.2v5.2"/><path d="M8.2 10.8h5.2"/>'),
    "zoom-out": ('<circle cx="10.8" cy="10.8" r="6.4"/><path d="M15.6 15.6 20.4 20.4"/>'
                 '<path d="M8.2 10.8h5.2"/>'),
    "fit": ('<path d="M4.4 9V5.6A1.2 1.2 0 0 1 5.6 4.4H9"/>'
            '<path d="M15 4.4h3.4A1.2 1.2 0 0 1 19.6 5.6V9"/>'
            '<path d="M19.6 15v3.4a1.2 1.2 0 0 1-1.2 1.2H15"/>'
            '<path d="M9 19.6H5.6A1.2 1.2 0 0 1 4.4 18.4V15"/>'),
    "palette": ('<path d="M12 3.6a8.4 8.4 0 0 0 0 16.8c1.1 0 1.8-.8 1.8-1.6'
                ' 0-.9-.7-1.4-.7-2.2 0-.9.7-1.6 1.7-1.6h1.5a4.1 4.1 0 0 0 4.1-4.1'
                'c0-4-3.7-7.3-8.4-7.3z"/>'
                '<circle cx="8.3" cy="11.6" r="1.1"/><circle cx="11" cy="8" r="1.1"/>'
                '<circle cx="15.2" cy="9.3" r="1.1"/>'),
    # 领域 / 状态补充
    "trend": '<path d="M3.6 15.6 9 10l3.4 3.4 7.4-7.4"/><path d="M15 6h5v5"/>',
    "activity": '<path d="M3 12.4h3.4l2.2-6.2 3.4 12.6 2.4-6.4H21"/>',
    "database": ('<ellipse cx="12" cy="6.4" rx="7.4" ry="3"/>'
                 '<path d="M4.6 6.4v11.2c0 1.7 3.3 3 7.4 3s7.4-1.3 7.4-3V6.4"/>'
                 '<path d="M4.6 12c0 1.7 3.3 3 7.4 3s7.4-1.3 7.4-3"/>'),
    "cloud": ('<path d="M7.4 18.8h9.4a4 4 0 0 0 .5-8 5.6 5.6 0 0 0-10.7 1.3'
              ' 3.6 3.6 0 0 0 .8 6.7z"/>'),
    "satellite": ('<rect x="9.4" y="9.4" width="5.2" height="5.2" rx="1.4"/>'
                  '<rect x="2.8" y="2.8" width="5" height="5" rx="1.3"/>'
                  '<rect x="16.2" y="16.2" width="5" height="5" rx="1.3"/>'
                  '<path d="M7.8 7.8 9.4 9.4"/><path d="M14.6 14.6 16.2 16.2"/>'
                  '<path d="M12 9.4V4.2"/><path d="M14.6 12h5.2"/>'),
    "target": ('<circle cx="12" cy="12" r="8.4"/><circle cx="12" cy="12" r="4.4"/>'
               '<circle cx="12" cy="12" r="1.2"/>'),
    "crosshair": ('<circle cx="12" cy="12" r="7.4"/>'
                  '<path d="M12 2.4v4"/><path d="M12 17.6v4"/>'
                  '<path d="M2.4 12h4"/><path d="M17.6 12h4"/>'),
    "list": ('<path d="M8.4 6.6h12"/><path d="M8.4 12h12"/><path d="M8.4 17.4h12"/>'
             '<circle cx="4.6" cy="6.6" r="1.1"/><circle cx="4.6" cy="12" r="1.1"/>'
             '<circle cx="4.6" cy="17.4" r="1.1"/>'),
    "filter": '<path d="M3.6 5.4h16.8l-6.6 7.8v6.4l-3.6-2.4v-4z"/>',
    "sparkles": ('<path d="M11 3.6 12.5 8.1 17 9.6l-4.5 1.5L11 15.6 9.5 11.1 5 9.6l4.5-1.5z"/>'
                 '<path d="M18 14.4 18.9 17 21.5 17.9 18.9 18.8 18 21.4'
                 ' 17.1 18.8 14.5 17.9 17.1 17z"/>'),
    "file-text": ('<path d="M6.4 3.4h7.2l4.6 4.6v12.6H6.4z"/>'
                  '<path d="M13.4 3.4V8.2h4.6"/>'
                  '<path d="M9.2 12.2h6"/><path d="M9.2 15.8h6"/>'),
    "compare": ('<rect x="3.6" y="6.4" width="7.2" height="13.2" rx="1.8"/>'
                '<rect x="13.2" y="6.4" width="7.2" height="13.2" rx="1.8"/>'
                '<path d="M7.2 3.4v3"/><path d="M16.8 3.4v3"/>'),
}
_ICONS.update(_EXTRA_ICONS)


# 每个图标的默认色调（键名取自 _TONE_STOPS；未登记的图标回落到 accent）
_ICON_TONES: dict[str, str] = {
    "home": "indigo", "grid": "sky", "download": "emerald", "pulse": "rose",
    "clock": "amber", "chart": "violet", "map": "teal", "sliders": "slate",
    "play": "emerald", "stop": "rose", "refresh": "sky", "folder": "amber",
    "save": "teal", "wand": "violet", "search": "sky", "eye": "indigo",
    "window": "slate", "check": "emerald", "check-circle": "emerald",
    "alert": "amber", "info": "sky", "close": "rose", "external": "indigo",
    "sun": "amber", "moon": "indigo", "globe": "teal", "layers": "violet",
    "droplet": "sky", "thermometer": "rose", "gauge": "amber",
    "terminal": "slate", "cpu": "indigo", "calendar": "violet",
    "minimize": "slate", "maximize": "slate", "restore": "slate",
    "chevron-left": "slate", "chevron-right": "slate", "chevron-down": "slate",
    "panel-right": "indigo", "panel-left": "indigo",
    "radar": "violet", "table": "teal", "zoom-in": "sky", "zoom-out": "sky",
    "fit": "teal", "palette": "rose", "trend": "emerald", "activity": "rose",
    "database": "sky", "cloud": "indigo", "satellite": "violet",
    "target": "rose", "crosshair": "sky", "list": "slate", "filter": "amber",
    "sparkles": "amber", "file-text": "indigo", "compare": "teal",
}

# 色调梯度表：名称 -> (起点色, 终点色)；"accent" 由主题令牌动态决定
_TONE_STOPS: dict[str, tuple[str, str]] = {
    "sky": ("#3B82F6", "#22D3EE"),
    "teal": ("#0D9488", "#38BDF8"),
    "emerald": ("#10B981", "#84CC16"),
    "amber": ("#F59E0B", "#FB923C"),
    "rose": ("#F43F5E", "#FB7185"),
    "violet": ("#8B5CF6", "#38BDF8"),
    "indigo": ("#4F46E5", "#38BDF8"),
    "slate": ("#334155", "#64748B"),
}

_ELEM_RE = re.compile(r"<(path|circle|rect|ellipse|line|polyline|polygon)\b[^>]*/>")
_ELEM_HEAD_RE = re.compile(r"^<(\w+)")


def _device_ratio() -> float:
    """当前主屏的设备像素比（无应用实例时退化为 2.0，宁大勿糊）。"""
    from PySide6.QtWidgets import QApplication

    if QApplication.instance() is None:
        return 2.0
    screen = QApplication.primaryScreen()
    return float(screen.devicePixelRatio() or 1.0) if screen is not None else 2.0


def _tint(element: str, color: str, opacity: float) -> str:
    """给单个 SVG 元素注入描边色与透明度（不动几何）。"""
    extra = f' stroke="{color}"'
    if opacity < 0.999:
        extra += f' stroke-opacity="{opacity:.2f}"'
    return _ELEM_HEAD_RE.sub(lambda m: f"<{m.group(1)}{extra}", element, count=1)


def _glyph_document(name: str, size: int, primary: str, secondary: str,
                    width: float = 2.05) -> bytes:
    """把线稿拆成"主笔触 + 次笔触"两层描边，生成双色调 SVG。"""
    try:
        body = _ICONS[name]
    except KeyError:  # pragma: no cover - 开发期快速暴露笔误
        raise KeyError(f"未知图标 '{name}'，可用: {', '.join(names())}") from None
    elements = [m.group(0) for m in _ELEM_RE.finditer(body)] or [body]
    inner = _tint(elements[0], primary, 1.0)
    if len(elements) > 1:
        inner += "".join(_tint(el, secondary, 0.60) for el in elements[1:])
    return _SVG_DOC.format(size=size, color=primary, width=width,
                           body=inner).encode("utf-8")


def _tone_pair(tone: str, pal: theme.Palette) -> tuple[QColor, QColor]:
    if tone in (None, "", "accent"):
        return QColor(pal.accent), QColor(pal.accent_alt)
    start, end = _TONE_STOPS.get(tone, (pal.accent, pal.accent_alt))
    return QColor(start), QColor(end)


def badge_pixmap(name: str, size: int = 48, tone: str | None = None,
                 mode: str | None = None) -> QPixmap:
    """取"渐变底托 + 双色调线稿"徽章；包装层不缓存，主题切换即时生效。"""
    return _badge_cached(name, int(size), tone or _ICON_TONES.get(name, "accent"),
                         mode or theme.mode())


@lru_cache(maxsize=768)
def _badge_cached(name: str, size: int, tone: str, mode_name: str) -> QPixmap:
    pal = theme.LIGHT if mode_name == "light" else theme.DARK
    ratio = _device_ratio()
    physical = max(16, int(round(size * ratio)))
    pm = QPixmap(physical, physical)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    try:
        c1, c2 = _tone_pair(tone, pal)
        _draw_plate(painter, float(physical), c1, c2)
        glyph = float(physical) * 0.545
        renderer = QSvgRenderer(_glyph_document(name, int(round(glyph)),
                                                "#FFFFFF", "#FFFFFF"))
        offset = (physical - glyph) / 2.0
        renderer.render(painter, QRectF(offset, offset, glyph, glyph))
    finally:
        painter.end()
    pm.setDevicePixelRatio(ratio)
    return pm


def badge_icon(name: str, size: int = 48, tone: str | None = None,
               mode: str | None = None) -> QIcon:
    """把徽章包成 QIcon（按钮 / 列表项直接用）。"""
    return QIcon(badge_pixmap(name, size, tone, mode))


# --------------------------------------------------------- 数据源专属色 --

SOURCE_KEYS: tuple[str, ...] = ("era5", "cmameso", "cra40", "crav15", "merra2")

SOURCE_LABELS: dict[str, str] = {
    "era5": "ERA5",
    "cmameso": "CMA-MESO",
    "cra40": "CRA40",
    "crav15": "CRA V1.5",
    "merra2": "MERRA2",
}

# 每个数据源一个固定徽章图形，让"哪条曲线是谁"一眼可辨
SOURCE_GLYPHS: dict[str, str] = {
    "era5": "globe",
    "cmameso": "radar",
    "cra40": "database",
    "crav15": "layers",
    "merra2": "satellite",
}

_SOURCE_ALIASES: dict[str, str] = {
    "era5": "era5", "era5_met": "era5", "era5-met": "era5", "era5met": "era5",
    "cmameso": "cmameso", "cma-meso": "cmameso", "cma_meso": "cmameso",
    "cma": "cmameso", "meso": "cmameso",
    "cra40": "cra40", "cra-40": "cra40", "cra_40": "cra40",
    "crav15": "crav15", "crav1.5": "crav15", "cra_v1.5": "crav15",
    "cra v1.5": "crav15", "cra15": "crav15",
    "merra2": "merra2", "merra-2": "merra2", "merra_2": "merra2",
}


def normalize_source(key: str | None) -> str:
    """把各种写法（ERA5_MET / cma-meso / CRA V1.5 …）归一成源 key。"""
    text = str(key or "").strip().lower()
    if text in _SOURCE_ALIASES:
        return _SOURCE_ALIASES[text]
    for token, canonical in _SOURCE_ALIASES.items():
        if token and token in text:
            return canonical
    return ""


def source_index(key: str | None) -> int:
    """源在五源顺序中的下标（未知返回 -1）。"""
    canonical = normalize_source(key)
    return SOURCE_KEYS.index(canonical) if canonical in SOURCE_KEYS else -1


def source_label(key: str | None) -> str:
    """源的展示名（未知则原样返回）。"""
    canonical = normalize_source(key)
    return SOURCE_LABELS.get(canonical, str(key or "未知源"))


def source_color(key: str | None, mode: str | None = None) -> str:
    """数据源固定专属色（跨页面、跨图表保持一致）。"""
    index = source_index(key)
    pal = theme.LIGHT if (mode or theme.mode()) == "light" else theme.DARK
    if index < 0:
        return pal.text_faint
    return (pal.series_1, pal.series_2, pal.series_3, pal.series_4,
            pal.series_5)[index]


def source_badge(key: str | None, size: int = 40,
                 mode: str | None = None) -> QPixmap:
    """数据源徽章：底托用该源专属色，图形用该源约定图标。"""
    canonical = normalize_source(key) or "era5"
    return _source_badge_cached(canonical, int(size), mode or theme.mode())


@lru_cache(maxsize=64)
def _source_badge_cached(key: str, size: int, mode_name: str) -> QPixmap:
    base = QColor(source_color(key, mode_name))
    ratio = _device_ratio()
    physical = max(16, int(round(size * ratio)))
    pm = QPixmap(physical, physical)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    try:
        _draw_plate(painter, float(physical), base, base.darker(140))
        glyph = float(physical) * 0.56
        renderer = QSvgRenderer(_glyph_document(SOURCE_GLYPHS.get(key, "globe"),
                                                int(round(glyph)),
                                                "#FFFFFF", "#FFFFFF"))
        offset = (physical - glyph) / 2.0
        renderer.render(painter, QRectF(offset, offset, glyph, glyph))
    finally:
        painter.end()
    pm.setDevicePixelRatio(ratio)
    return pm


def export_app_icon(path=None, mode_name: str = "dark"):
    """导出多分辨率 .ico（16/24/32/48/64/128/256），供窗口与打包复用。"""
    from io import BytesIO
    from pathlib import Path

    target = Path(path) if path else Path(__file__).with_name("assets") / "metcom.ico"
    target.parent.mkdir(parents=True, exist_ok=True)

    ratio = _device_ratio()
    side = 256
    pm = QPixmap(int(side * ratio), int(side * ratio))
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    try:
        pal = theme.LIGHT if mode_name == "light" else theme.DARK
        draw_app_logo(painter, int(side * ratio), pal)
    finally:
        painter.end()

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pm.toImage().save(buffer, "PNG")
    buffer.close()

    from PIL import Image

    image = Image.open(BytesIO(bytes(buffer.data()))).convert("RGBA")
    image.save(target, format="ICO",
               sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                      (128, 128), (256, 256)])
    return target


def logo_path_hint() -> str:
    """说明文字（用于"关于"页）。"""
    return "渐变方块 + 大气分层弧 + 天顶射线 = 对流层延迟反演"


__all__ = [
    "names", "icon", "pixmap", "app_logo", "draw_app_logo", "logo_path_hint",
    "badge_pixmap", "badge_icon", "export_app_icon",
    "SOURCE_KEYS", "SOURCE_LABELS", "SOURCE_GLYPHS",
    "normalize_source", "source_index", "source_label", "source_color",
    "source_badge",
]
