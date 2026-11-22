"""五步工作流进度条（自绘）。

为什么自绘而不是用 QSS 拼：步骤条需要"已完成=渐变实心+对勾""进行中=强调色
描边+光晕""未开始=弱化描边"三种状态在圆、连线、文字上同时生效，用 QSS 需要
大量动态属性 + re-polish，且圆角与连线接缝容易出现毛边。用 QPainter 一次性
绘制，配合抗锯齿，在任意缩放下都干净利落。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QLinearGradient, QPainter,
                           QPainterPath, QPen)
from PySide6.QtWidgets import QSizePolicy, QWidget

from metcom.gui import theme

_RADIUS = 15.0
_TOP = 24.0          # 圆心 y
_SIDE = 34.0         # 左右留白


class WorkflowStepper(QWidget):
    """横向步骤条；``set_state(i, "idle|active|done")`` 驱动状态。"""

    def __init__(self, steps: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._steps = list(steps)
        self._states = ["idle"] * len(self._steps)
        self.setFixedHeight(74)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip("工作流：格点生成 → 数据下载 → 核心反演 → 时间插值 → 精度评估")

    # -- 状态 --
    def set_state(self, index: int, state: str) -> None:
        if 0 <= index < len(self._states) and self._states[index] != state:
            self._states[index] = state
            self.update()

    def set_states(self, states: list[str]) -> None:
        self._states = list(states)[: len(self._steps)]
        self._states += ["idle"] * (len(self._steps) - len(self._states))
        self.update()

    def states(self) -> list[str]:
        return list(self._states)

    def reset(self) -> None:
        self.set_states(["idle"] * len(self._steps))

    # -- 绘制 --
    def _centers(self) -> list[float]:
        n = len(self._steps)
        w = float(self.width())
        if n <= 1:
            return [w / 2.0]
        span = max(1.0, w - 2 * _SIDE)
        return [_SIDE + span * i / (n - 1) for i in range(n)]

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        pal = theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        try:
            centers = self._centers()
            cy = _TOP

            # ---- 连接线（先画，让圆覆盖端头）----
            for i in range(len(centers) - 1):
                done = self._states[i] == "done"
                color = QColor(pal.accent if done else pal.border)
                pen = QPen(color)
                pen.setWidthF(2.0)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                y = cy + (_RADIUS if False else 0)
                painter.drawLine(QPointF(centers[i] + _RADIUS + 3, y),
                                 QPointF(centers[i + 1] - _RADIUS - 3, y))

            # ---- 圆 + 序号/对勾 ----
            num_font = QFont(theme.pick_family(theme.UI_FONT_CANDIDATES), 11)
            num_font.setBold(True)
            for i, cx in enumerate(centers):
                state = self._states[i]
                rect = QRectF(cx - _RADIUS, cy - _RADIUS, _RADIUS * 2, _RADIUS * 2)

                if state == "done":
                    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
                    grad.setColorAt(0.0, QColor(pal.accent))
                    grad.setColorAt(1.0, QColor(pal.accent_alt))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(grad)
                    painter.drawEllipse(rect)
                    pen = QPen(QColor(pal.on_accent))
                    pen.setWidthF(2.1)
                    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                    painter.setPen(pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    path = QPainterPath()
                    path.moveTo(cx - 5.6, cy + 0.4)
                    path.lineTo(cx - 1.6, cy + 4.4)
                    path.lineTo(cx + 5.8, cy - 4.2)
                    painter.drawPath(path)
                elif state == "active":
                    soft = QColor(pal.accent)
                    soft.setAlpha(44)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(soft)
                    painter.drawEllipse(rect)
                    pen = QPen(QColor(pal.accent))
                    pen.setWidthF(2.2)
                    painter.setPen(pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawEllipse(rect)
                    painter.setPen(QColor(pal.accent))
                    painter.setFont(num_font)
                    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(i + 1))
                else:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(pal.surface_alt))
                    painter.drawEllipse(rect)
                    pen = QPen(QColor(pal.border_strong))
                    pen.setWidthF(1.4)
                    painter.setPen(pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawEllipse(rect)
                    painter.setPen(QColor(pal.text_faint))
                    painter.setFont(num_font)
                    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(i + 1))

            # ---- 文字标签 ----
            label_font = QFont(theme.pick_family(theme.UI_FONT_CANDIDATES), 9)
            label_font.setBold(False)
            for i, cx in enumerate(centers):
                state = self._states[i]
                label_font.setBold(state == "active")
                painter.setFont(label_font)
                color = {"done": pal.text, "active": pal.accent}.get(state, pal.text_muted)
                painter.setPen(QColor(color))
                text_rect = QRectF(cx - 62, cy + _RADIUS + 6, 124, 22)
                painter.drawText(text_rect,
                                 Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                                 self._steps[i])
        finally:
            painter.end()


__all__ = ["WorkflowStepper"]
