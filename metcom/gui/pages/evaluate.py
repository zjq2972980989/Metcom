"""05 · 精度评估与绘图：与参考数据逐时 / 逐站对比，输出 Bias 与 RMSE。

左栏是评估与绘图两套参数（分别对应 ``metcom_evaluation.yaml`` 与
``metcom_plot.yaml``），右栏是即时预览：五源指标对比表 + ZTD RMSE 站点散点 / 热力图。
「载入示例基准数据」会把绘图参数指向随仓库提供的 benchmarks 数据，
用于不接真实数据时先跑通链路。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QWidget

from metcom.gui import charts, charts_map, data
from metcom.gui.components.cards import Card
from metcom.gui.components.inputs import (FormRow, PathField, checkbox, combo,
                                          hint, line_edit)
from metcom.gui.pages.base import BasePage

EVAL_VARS = ["ZTD(m)", "PWV(m)", "ZHD(m)", "ZWD(m)", "Ps(hpa)", "Tm(K)"]
PLOT_VARS = ["ZTD_RMSE(mm)", "PWV_RMSE(mm)", "ZTD_Bias(mm)", "PWV_Bias(mm)",
             "ZTD(m)", "PWV(m)"]
PLOT_TYPES = [("sequence", "RMSE 时间序列图"),
              ("heatmap_rmse", "站点 RMSE 空间热力图"),
              ("heatmap_value", "单时刻数值热力图")]


class EvaluatePage(BasePage):
    """05 精度评估。"""

    page_key = "evaluation"
    module_key = "evaluation"
    page_title = "05 · 精度评估与绘图"
    page_subtitle = "与参考数据逐时 / 逐站对比，给出 Bias 与 RMSE，并输出成图"
    page_icon = "chart"

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        self._cfg: dict = {}
        self._plot_cfg: dict = {}
        self._ds = None
        super().__init__(ctx, parent)

    # ------------------------------------------------------------ 构建 --
    def build(self) -> None:
        run = self.run_button("运行评估")
        run.clicked.connect(self._run)
        self.add_action(run)
        plot = self.button("运行绘图", "chart", "ghost", "用 run_plot 出图")
        plot.clicked.connect(self._run_plot)
        self.add_action(plot)
        sample = self.button("载入示例数据", "wand", "ghost",
                             "把绘图参数指向 benchmarks 下的基准数据")
        sample.clicked.connect(self._load_sample)
        self.add_action(sample)
        save = self.button("保存配置", "save", "ghost")
        save.clicked.connect(self.save_config)
        self.add_action(save)

        self._ds = data.dataset(self.ctx.root)
        left, right = self.columns(3, 2)
        left.addWidget(self._build_eval_card())
        left.addWidget(self._build_plot_card())
        right.addWidget(self._build_metrics_card())
        right.addWidget(self._build_map_card())
        right.addStretch(1)
        self._load()
        self._refresh_metrics()

    def _build_eval_card(self) -> QWidget:
        card = Card("评估参数", "DIR_A 为反演结果，DIR_B 为参考真值（列结构一致）",
                    "compare")
        self._mode = combo(["逐时次评估（timeseries）", "逐站评估（bystation）"], 0)
        card.add_widget(FormRow("评估模式", self._mode))
        self._dir_a = PathField("dir", "", "选择数据源 A 目录")
        card.add_widget(FormRow("DIR_A", self._dir_a))
        self._name_a = line_edit("", "如 ERA5_MET")
        card.add_widget(FormRow("NAME_A", self._name_a))
        self._dir_b = PathField("dir", "", "选择参考数据目录")
        card.add_widget(FormRow("DIR_B", self._dir_b))
        self._name_b = line_edit("", "如 ERA5_GMET")
        card.add_widget(FormRow("NAME_B", self._name_b))
        self._time_input = line_edit("", "YYYYMMDDHH,YYYYMMDDHH")
        card.add_widget(FormRow("时间范围", self._time_input,
                                "用英文逗号分隔起止时刻"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        self._eval_boxes: dict[str, QWidget] = {}
        for position, name in enumerate(EVAL_VARS):
            box = checkbox(name, name in ("ZTD(m)", "PWV(m)"))
            grid.addWidget(box, position // 3, position % 3)
            self._eval_boxes[name] = box
        card.add_widget(hint("参与评估的变量"))
        card.add_layout(grid)
        self._output = PathField("dir", "", "评估结果输出目录")
        card.add_widget(FormRow("输出目录", self._output))
        self._tag = line_edit("", "默认自动生成 NAME_A_vs_NAME_B")
        card.add_widget(FormRow("输出标签", self._tag))
        return card

    def _build_plot_card(self) -> QWidget:
        card = Card("绘图参数", "对应 plot_ZTD_PWV_*.py 三个脚本", "map")
        self._plot_type = combo([label for _, label in PLOT_TYPES], PLOT_TYPES[0][1])
        self._plot_type.currentIndexChanged.connect(self._on_plot_type)
        card.add_widget(FormRow("图类型", self._plot_type))
        self._txt_file = PathField("file", "", "选择评估结果 txt",
                                  "文本文件 (*.txt)", "选择输入文件")
        card.add_widget(FormRow("输入文件", self._txt_file))
        self._plot_out = PathField("dir", "", "图片输出目录")
        card.add_widget(FormRow("输出目录", self._plot_out))
        self._region = combo(["BTH（京津冀）", "CHN（全国）"], "BTH（京津冀）")
        card.add_widget(FormRow("区域", self._region))
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        self._plot_boxes: dict[str, QWidget] = {}
        for position, name in enumerate(PLOT_VARS):
            box = checkbox(name, name in ("ZTD_RMSE(mm)", "PWV_RMSE(mm)"))
            grid.addWidget(box, position // 2, position % 2)
            self._plot_boxes[name] = box
        card.add_widget(hint("绘图变量（需与输入文件列名一致）"))
        card.add_layout(grid)
        self._plot_hint = hint("")
        card.add_widget(self._plot_hint)
        self._on_plot_type()
        return card

    def _build_metrics_card(self) -> QWidget:
        card = Card("即时预览 · 五源指标", "读取既有评估结果（参考真值 ERA5_GMET）",
                    "table")
        self._table = charts.HeatTable()
        self._table.setMinimumHeight(260)
        card.add_widget(self._table)
        return card

    def _build_map_card(self) -> QWidget:
        card = Card("即时预览 · 站点精度",
                    "按 ZTD RMSE 着色的站点散点 / 热力图", "map")
        self._map_layer = combo(["站点散点", "数值热力图"], "站点散点")
        self._map_layer.currentIndexChanged.connect(self._refresh_metrics)
        card.add_widget(FormRow("图层", self._map_layer,
                                "热力图把站点值插值成栅格"))
        if charts_map.available():
            self._map = charts_map.MapCanvas(compact=True)
            self._map.setMinimumHeight(260)
            card.add_widget(self._map, 1)
        else:
            self._map = None
            card.add_widget(hint(charts_map.unavailable_reason() or "地图不可用"))
        return card

    # ------------------------------------------------------------ 数据 --
    def _load(self) -> None:
        self._cfg = self.ctx.load_config("evaluation") or {}
        self._plot_cfg = self.ctx.load_config("plot") or {}
        mode = str(self._cfg.get("eval_mode", "timeseries")).lower()
        self._mode.setCurrentIndex(1 if mode.startswith("by") else 0)
        self._dir_a.set_value(self._cfg.get("dir_a", ""))
        self._name_a.setText(str(self._cfg.get("name_a", "")))
        self._dir_b.set_value(self._cfg.get("dir_b", ""))
        self._name_b.setText(str(self._cfg.get("name_b", "")))
        self._time_input.setText(str(self._cfg.get("time_input", "")))
        selected = set(self._cfg.get("eval_vars") or ["ZTD(m)", "PWV(m)"])
        for name, box in self._eval_boxes.items():
            box.setChecked(name in selected)
        self._output.set_value(self._cfg.get("output_dir", ""))
        self._tag.setText(str(self._cfg.get("output_tag") or ""))

        plot_type = str(self._plot_cfg.get("plot_type", "sequence"))
        labels = dict(PLOT_TYPES)
        self._plot_type.setCurrentText(labels.get(plot_type, PLOT_TYPES[0][1]))
        self._txt_file.set_value(self._plot_cfg.get("txt_file", ""))
        self._plot_out.set_value(self._plot_cfg.get("output_dir", ""))
        region = str(self._plot_cfg.get("region", "BTH")).upper()
        self._region.setCurrentIndex(1 if region.startswith("CHN") else 0)
        plot_selected = set(self._plot_cfg.get("plot_vars") or ["ZTD_RMSE(mm)"])
        for name, box in self._plot_boxes.items():
            box.setChecked(name in plot_selected)
        self.set_status("配置已载入 evaluation / plot", "accent")

    def _refresh_metrics(self) -> None:
        columns = [
            {"title": "ZTD RMSE", "key": "ztd_rmse", "dir": "low", "fmt": "{:.2f}"},
            {"title": "ZTD Bias", "key": "ztd_bias", "dir": "low", "fmt": "{:.2f}"},
            {"title": "PWV RMSE", "key": "pwv_rmse", "dir": "low", "fmt": "{:.3f}"},
            {"title": "PWV Bias", "key": "pwv_bias", "dir": "low", "fmt": "{:.3f}"},
            {"title": "MAE*", "key": "mae", "dir": "low", "fmt": "{:.2f}"},
            {"title": "时次数", "key": "steps", "dir": "high",
             "text": lambda v: f"{int(v)}"},
        ]
        rows = []
        for key in data.SOURCE_ORDER:
            source = self._ds.get(key)
            if source is None:
                continue
            summary = source.summary()
            rows.append({"label": self._ds.label(key), "values": [
                _num(summary.get("ztd_rmse")), _num(summary.get("ztd_bias")),
                _num(summary.get("pwv_rmse")), _num(summary.get("pwv_bias")),
                _mae(summary.get("ztd_rmse"), summary.get("ztd_bias")),
                _num(summary.get("steps")),
            ]})
        self._table.set_columns(columns, "数据源")
        self._table.set_rows(rows)
        if self._map is not None:
            geo = self._ds.geo()
            first = next((k for k in data.SOURCE_ORDER if self._ds.has(k)), None)
            source = self._ds.get(first) if first else None
            frame = pd.DataFrame()
            if source is not None and not geo.empty:
                stations = source.stations()
                if not stations.empty:
                    frame = geo.merge(stations[["site", "ztd_rmse"]], on="site",
                                      how="inner")
            if not frame.empty:
                self._map.set_sites(
                    frame, "ztd_rmse", label="ZTD RMSE", unit="mm",
                    mode="heat" if self._map_layer.currentIndex() == 1 else "points",
                    point_size=7.0, region="CHN")

    # ------------------------------------------------------------ 动作 --
    def _on_plot_type(self) -> None:
        plot_type = PLOT_TYPES[max(0, self._plot_type.currentIndex())][0]
        expects = {"sequence": "Timeseries", "heatmap_rmse": "ByStation",
                   "heatmap_value": "反演结果 ztd_*.txt"}[plot_type]
        self._plot_hint.setText(f"该图类型需要：{expects} 文件")

    def _load_sample(self) -> None:
        root = Path(self.ctx.root)
        results = root / "benchmarks" / "Results" / "output_results"
        sample = results / "ERA5_MET_vs_ERA5_GMET_Timeseries_2024010100_2024010223.txt"
        if sample.is_file():
            self._txt_file.set_value(str(sample))
        else:
            self._txt_file.set_value(str(results))
        self._plot_out.set_value(str(root / "benchmarks" / "Results" / "plotResults"))
        self._name_a.setText("ERA5_MET")
        self._name_b.setText("ERA5_GMET")
        self._dir_a.set_value(str(root / "benchmarks" / "output_ERA5"))
        self._time_input.setText("2024010100,2024010223")
        self.ctx.log("已载入 benchmarks 示例路径（评估仍需 DIR_B 参考真值）")
        self.set_status("已载入示例基准数据", "accent")

    def collect_config(self) -> dict:
        cfg = dict(self._cfg)
        cfg.update({
            "eval_mode": "bystation" if self._mode.currentIndex() == 1
            else "timeseries",
            "dir_a": self._dir_a.value(),
            "name_a": self._name_a.text().strip(),
            "dir_b": self._dir_b.value(),
            "name_b": self._name_b.text().strip(),
            "time_input": self._time_input.text().strip(),
            "eval_vars": [name for name, box in self._eval_boxes.items()
                          if box.isChecked()],
            "output_dir": self._output.value(),
            "output_tag": self._tag.text().strip() or None,
        })
        self._cfg = cfg
        return cfg

    def collect_plot_config(self) -> dict:
        cfg = dict(self._plot_cfg)
        cfg.update({
            "plot_type": PLOT_TYPES[max(0, self._plot_type.currentIndex())][0],
            "txt_file": self._txt_file.value(),
            "output_dir": self._plot_out.value(),
            "name_a": self._name_a.text().strip() or "A",
            "name_b": self._name_b.text().strip() or "B",
            "region": "CHN" if self._region.currentIndex() == 1 else "BTH",
            "plot_vars": [name for name, box in self._plot_boxes.items()
                          if box.isChecked()],
        })
        self._plot_cfg = cfg
        return cfg

    def _run(self) -> None:
        self.ctx.run_module(
            "evaluation", self.collect_config(), strict=True,
            on_done=lambda _result: (self._refresh_metrics(),
                                     self.set_status("评估完成", "ok")),
            on_fail=lambda message, _detail: self.set_status(
                f"失败：{message}", "bad"))

    def _run_plot(self) -> None:
        self.ctx.state.note_config("plot", self.collect_plot_config())
        self.ctx.run_module(
            "plot", self.collect_plot_config(), strict=True,
            on_done=lambda _result: self.set_status("出图完成", "ok"),
            on_fail=lambda message, _detail: self.set_status(
                f"失败：{message}", "bad"))

    def on_show(self) -> None:
        self._refresh_metrics()

    def refresh_theme(self) -> None:
        super().refresh_theme()
        if getattr(self, "_table", None) is not None:
            self._table.refresh_theme()
        if getattr(self, "_map", None) is not None:
            self._map.refresh_theme()


def _num(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _mae(rmse, bias):
    """由 RMSE 与 Bias 近似反推 MAE（高斯误差假设）。"""
    if rmse is None or bias is None:
        return None
    inner = float(rmse) ** 2 - float(bias) ** 2
    return data.GAUSS_MAE * float(np.sqrt(inner)) if inner > 0 else None


__all__ = ["EvaluatePage"]
