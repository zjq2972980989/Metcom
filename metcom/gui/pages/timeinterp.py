"""04 · 时间插值：把粗时间分辨率的反演结果插值到目标分辨率。

右栏预览用一条"6h 采样 → 1h 插值"的示意图说明本步在做什么：
原始时刻点稀疏（虚线），插值后逐小时密集（实线），量级与走势保持一致。
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QWidget

from metcom.gui import charts, data, theme
from metcom.gui.components.cards import Card, KeyValueRow
from metcom.gui.components.inputs import (FormRow, PathField, checkbox, hint,
                                          line_edit, spin)
from metcom.gui.pages.base import BasePage

VARIABLES = ["ZTD(m)", "ZHD(m)", "ZWD(m)", "PWV(m)", "Ps(hpa)", "Tm(K)",
             "vapor_pressure(hpa)", "WVD"]


class TimeInterpPage(BasePage):
    """04 时间插值。"""

    page_key = "timeinterp"
    module_key = "timeinterp"
    page_title = "04 · 时间插值"
    page_subtitle = "把粗时间分辨率的反演结果插值到目标时间分辨率（默认 6h → 1h）"
    page_icon = "clock"

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        self._cfg: dict = {}
        super().__init__(ctx, parent)

    # ------------------------------------------------------------ 构建 --
    def build(self) -> None:
        run = self.run_button("开始插值")
        run.clicked.connect(self._run)
        self.add_action(run)
        save = self.button("保存配置", "save", "ghost")
        save.clicked.connect(self.save_config)
        self.add_action(save)

        left, right = self.columns(3, 2)
        left.addWidget(self._build_io_card())
        left.addWidget(self._build_vars_card())
        right.addWidget(self._build_preview_card())
        right.addWidget(self._build_tips_card())
        right.addStretch(1)
        self._load()

    def _build_io_card(self) -> QWidget:
        card = Card("输入输出与分辨率", "对应原 MATLAB 04_TimeInter/main.m", "clock")
        self._input = PathField("dir", "", "选择待插值的反演结果目录")
        card.add_widget(FormRow("输入目录", self._input))
        self._output = PathField("dir", "", "插值结果输出目录")
        card.add_widget(FormRow("输出目录", self._output))
        self._original = spin(1, 24, 6, 1, " 小时")
        self._original.valueChanged.connect(self._refresh_preview)
        card.add_widget(FormRow("原始分辨率", self._original, "仅作记录，实际由目标分辨率决定"))
        self._target = spin(1, 24, 1, 1, " 小时")
        self._target.valueChanged.connect(self._refresh_preview)
        card.add_widget(FormRow("目标分辨率", self._target, "1 表示逐小时"))
        return card

    def _build_vars_card(self) -> QWidget:
        card = Card("插值变量", "与原 MATLAB 默认一致，可按需增减", "layers")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        self._var_boxes: dict[str, QWidget] = {}
        for position, name in enumerate(VARIABLES):
            box = checkbox(name, True)
            grid.addWidget(box, position // 2, position % 2)
            self._var_boxes[name] = box
        card.add_layout(grid)
        return card

    def _build_preview_card(self) -> QWidget:
        card = Card("即时预览 · 插值示意", "稀疏原始时刻 → 逐小时结果", "trend")
        self._ds = data.dataset(self.ctx.root)
        self._plot = charts.LinePlot(compact=True)
        self._plot.setMinimumHeight(220)
        card.add_widget(self._plot)
        self._preview_hint = hint("")
        card.add_widget(self._preview_hint)
        return card

    def _build_tips_card(self) -> QWidget:
        card = Card("说明", "插值方式", "info")
        self._rows = {}
        for title in ("插值方法", "缺失处理", "输出命名"):
            row = KeyValueRow(title, "—")
            self._rows[title] = row
            card.add_widget(row)
        self._rows["插值方法"].set_value("按时间维线性插值（各变量独立）")
        self._rows["缺失处理"].set_value("原始区间外的时刻不外推，保持空缺")
        self._rows["输出命名"].set_value("与输入同名，写入输出目录")
        card.add_widget(hint("插值只改变时间采样密度，不改变反演算法本身。"))
        return card

    # ------------------------------------------------------------ 数据 --
    def _load(self) -> None:
        self._cfg = self.ctx.load_config("timeinterp") or {}
        self._input.set_value(self._cfg.get("input_dir", ""))
        self._output.set_value(self._cfg.get("output_dir", ""))
        self._original.setValue(int(self._cfg.get("original_interval", 6) or 6))
        self._target.setValue(int(self._cfg.get("target_interval", 1) or 1))
        selected = set(self._cfg.get("variables") or VARIABLES)
        for name, box in self._var_boxes.items():
            box.setChecked(name in selected)
        self._refresh_preview()
        self.set_status("配置已载入 configs/metcom_timeinterp.yaml", "accent")

    def _refresh_preview(self, *_args) -> None:
        key = "crav15" if self._ds.has("crav15") else (
            self._ds.keys()[0] if self._ds.keys() else "")
        site = self._ds.sites()[0] if self._ds.sites() else ""
        if not key or not site:
            self._plot.clear()
            return
        x, y = self._ds.get(key).series(site, "ztd")
        if not len(x):
            self._plot.clear()
            return
        step = max(1, int(self._original.value()) // max(1, int(self._target.value())))
        colors = theme.series_colors(2)
        series = [
            {"name": f"原始 {self._original.value()}h 采样", "color": colors[0],
             "x": x[::step], "y": y[::step], "dash": True},
            {"name": f"插值到 {self._target.value()}h", "color": colors[1],
             "x": x, "y": y},
        ]
        self._plot.set_series(series, ylabel="ZTD (mm)")
        self._preview_hint.setText(
            f"示例站点 {site} · {self._ds.label(key)}："
            f"每 {step} 个时刻取 1 个作为原始采样，其余由插值补齐。")

    def collect_config(self) -> dict:
        cfg = dict(self._cfg)
        cfg.update({
            "input_dir": self._input.value(),
            "output_dir": self._output.value(),
            "original_interval": int(self._original.value()),
            "target_interval": int(self._target.value()),
            "variables": [name for name, box in self._var_boxes.items()
                          if box.isChecked()],
        })
        self._cfg = cfg
        return cfg

    # ------------------------------------------------------------ 运行 --
    def _run(self) -> None:
        self.ctx.run_module(
            "timeinterp", self.collect_config(), strict=True,
            on_done=lambda _result: self.set_status("插值完成", "ok"),
            on_fail=lambda message, _detail: self.set_status(
                f"失败：{message}", "bad"))

    def refresh_theme(self) -> None:
        super().refresh_theme()
        if getattr(self, "_plot", None) is not None:
            self._plot.refresh_theme()
            self._refresh_preview()


__all__ = ["TimeInterpPage"]
