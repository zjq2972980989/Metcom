"""03 · 核心反演：逐时次、逐站点反演 ZTD / ZHD / ZWD / PWV。

平台的核心算法页。左栏是五源通用的反演参数，右栏给两样东西：
既有结果的单站 ZTD 曲线（确认量级正常）与该数据源的规格表
（层数、分辨率、时间分辨率、湿度处理方式）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from metcom.gui import charts, data, icons
from metcom.gui.components.cards import Card, KeyValueRow, section_label
from metcom.gui.components.inputs import (FormRow, PathField, checkbox, combo,
                                          dspin, hint, line_edit, spin)
from metcom.gui.pages.base import BasePage


class RetrievePage(BasePage):
    """03 核心反演。"""

    page_key = "retrieve"
    module_key = "retrieve"
    page_title = "03 · 核心反演"
    page_subtitle = "逐时次、逐站点反演 ZTD / ZHD / ZWD / PWV —— 平台核心算法"
    page_icon = "pulse"

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        self._cfg: dict = {}
        self._ds = None
        super().__init__(ctx, parent)

    # ------------------------------------------------------------ 构建 --
    def build(self) -> None:
        run = self.run_button("开始反演")
        run.clicked.connect(self._run)
        self.add_action(run)
        save = self.button("保存配置", "save", "ghost")
        save.clicked.connect(self.save_config)
        self.add_action(save)

        self._ds = data.dataset(self.ctx.root)
        left, right = self.columns(3, 2)
        left.addWidget(self._build_source_card())
        left.addWidget(self._build_io_card())
        left.addWidget(self._build_option_card())
        right.addWidget(self._build_preview_card())
        right.addWidget(self._build_spec_card())
        right.addStretch(1)
        self._load()

    def _build_source_card(self) -> QWidget:
        card = Card("数据源与时间范围", "切换数据源会联动右侧规格表", "satellite")
        self._source = combo([data.source_label(k) for k in data.SOURCE_ORDER],
                             data.source_label("era5"))
        self._source.currentIndexChanged.connect(self._on_source_changed)
        card.add_widget(FormRow("数据源", self._source))
        self._start = line_edit("", "YYYYMMDD 或 YYYYMMDDHH")
        card.add_widget(FormRow("起始时间", self._start))
        self._end = line_edit("", "YYYYMMDD 或 YYYYMMDDHH")
        card.add_widget(FormRow("结束时间", self._end))
        self._time_res = combo(["1h", "3h", "6h"], "1h")
        card.add_widget(FormRow("时间分辨率", self._time_res, "随数据源自动推导，一般无需改"))
        row = QHBoxLayout()
        row.setSpacing(8)
        self._res_lat = dspin(0.01, 2.0, 0.25, 3, 0.05)
        self._res_lon = dspin(0.01, 2.0, 0.25, 3, 0.05)
        row.addWidget(self._res_lat)
        row.addWidget(self._res_lon)
        card.add_widget(FormRow("空间分辨率", _wrap(row), "纬度 / 经度（度）"))
        return card

    def _build_io_card(self) -> QWidget:
        card = Card("输入与输出", "站点文件为正高，模块内部自动换算大地高", "folder")
        self._data_path = PathField("dir", "", "选择再分析资料根目录")
        card.add_widget(FormRow("输入目录", self._data_path,
                                "内含按 YYYYMMDD 命名的日文件夹"))
        self._output_path = PathField("dir", "", "反演结果输出目录")
        card.add_widget(FormRow("输出目录", self._output_path))
        self._site_file = PathField("file", "", "选择站点列表",
                                   "文本文件 (*.txt)", "选择站点列表")
        card.add_widget(FormRow("站点列表", self._site_file, "name lat lon h，正高"))
        self._format = combo(["txt", "mat", "dat"], "txt")
        card.add_widget(FormRow("输出格式", self._format))
        self._egm = PathField("file", "", "选择 EGM2008 文件（可留空）")
        card.add_widget(FormRow("EGM2008", self._egm, "留空则使用包内默认数据文件"))
        return card

    def _build_option_card(self) -> QWidget:
        card = Card("性能选项", "站点级并行（joblib）", "cpu")
        self._parallel = checkbox("启用站点级并行")
        self._parallel.toggled.connect(self._sync_parallel)
        card.add_widget(self._parallel)
        self._n_jobs = spin(0, 64, 0, 1, " 进程")
        card.add_widget(FormRow("并行进程数", self._n_jobs, "0 表示自动使用全部 CPU 核心"))
        self._sync_parallel()
        return card

    def _build_preview_card(self) -> QWidget:
        card = Card("即时预览 · 单站 ZTD", "从已有结果中取一个站点看量级是否正常", "trend")
        self._plot = charts.LinePlot(compact=True)
        self._plot.setMinimumHeight(200)
        card.add_widget(self._plot)
        self._preview_hint = hint("")
        card.add_widget(self._preview_hint)
        return card

    def _build_spec_card(self) -> QWidget:
        card = Card("数据源规格", "决定输入文件的组织方式与变量换算", "info")
        self._spec_rows: list[KeyValueRow] = []
        for title in ("层数", "水平分辨率", "时间分辨率", "湿度变量",
                      "GRIB2 直读", "本地结果"):
            row = KeyValueRow(title, "—")
            self._spec_rows.append(row)
            card.add_widget(row)
        return card

    # ------------------------------------------------------------ 数据 --
    def _source_key(self) -> str:
        index = max(0, self._source.currentIndex())
        return list(data.SOURCE_ORDER)[min(index, len(data.SOURCE_ORDER) - 1)]

    def _load(self) -> None:
        self._cfg = self.ctx.load_config("retrieve") or {}
        key = data.normalize_source(self._cfg.get("source")) or "era5"
        self._source.setCurrentText(data.source_label(key))
        self._start.setText(str(self._cfg.get("start_time", "")))
        self._end.setText(str(self._cfg.get("end_time", "")))
        self._time_res.setCurrentText(str(self._cfg.get("time_resolution", "1h")))
        resolution = self._cfg.get("spatial_resolution") or [0.25, 0.25]
        self._res_lat.setValue(float(resolution[0]))
        self._res_lon.setValue(float(resolution[1]))
        self._data_path.set_value(self._cfg.get("data_path", ""))
        self._output_path.set_value(self._cfg.get("output_path", ""))
        self._site_file.set_value(self._cfg.get("site_file", ""))
        self._format.setCurrentText(str(self._cfg.get("output_format", "txt")))
        self._egm.set_value(self._cfg.get("egm_file") or "")
        self._parallel.setChecked(bool(self._cfg.get("parallel", False)))
        self._n_jobs.setValue(int(self._cfg.get("n_jobs") or 0))
        self._on_source_changed()
        self.set_status("配置已载入 configs/metcom_era5.yaml", "accent")

    def _on_source_changed(self, *_args) -> None:
        key = self._source_key()
        from metcom.sources import SOURCES

        spec = SOURCES.get(key)
        if spec is not None:
            self._time_res.setCurrentText(str(spec.time_resolution))
            self._res_lat.setValue(float(spec.spatial_resolution[0]))
            self._res_lon.setValue(float(spec.spatial_resolution[1]))
            wet = _humidity_text(str(getattr(spec, "humidity_mode", "")))
            values = [f"{getattr(spec, 'levels', '—')} 层",
                      f"{spec.spatial_resolution[0]}° × {spec.spatial_resolution[1]}°",
                      str(spec.time_resolution), wet,
                      "需要" if getattr(spec, "needs_grib2_convert", False)
                      else "不需要"]
        else:
            values = ["—"] * 5
        source = self._ds.get(key)
        if source is None:
            values.append("未找到结果")
            self._spec_rows[-1].set_state("bad")
        else:
            summary = source.summary()
            values.append(f"{summary.get('steps', 0)} 时次 · "
                          f"{summary.get('sites', 0)} 站")
            self._spec_rows[-1].set_state("ok")
        for row, value in zip(self._spec_rows, values):
            row.set_value(value)
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        key = self._source_key()
        site = self._ds.sites()[0] if self._ds.sites() else ""
        series = charts.station_series(self._ds, [key], site, "ztd")
        self._plot.set_series(series, ylabel="ZTD (mm)")
        self._preview_hint.setText(
            f"示例站点 {site} · {self._ds.label(key)}；"
            "仅用于确认量级，实际输出以本次反演结果为准。")

    def _sync_parallel(self) -> None:
        self._n_jobs.setEnabled(self._parallel.isChecked())

    def collect_config(self) -> dict:
        cfg = dict(self._cfg)
        cfg.update({
            "source": self._source_key(),
            "data_path": self._data_path.value(),
            "output_path": self._output_path.value(),
            "output_format": self._format.currentText(),
            "site_file": self._site_file.value(),
            "start_time": self._start.text().strip(),
            "end_time": self._end.text().strip(),
            "spatial_resolution": [float(self._res_lat.value()),
                                   float(self._res_lon.value())],
            "time_resolution": self._time_res.currentText(),
            "parallel": bool(self._parallel.isChecked()),
            "n_jobs": int(self._n_jobs.value()) or None,
            "egm_file": self._egm.value() or None,
        })
        self._cfg = cfg
        return cfg

    # ------------------------------------------------------------ 运行 --
    def _run(self) -> None:
        self.ctx.run_module(
            "retrieve", self.collect_config(), strict=True,
            on_done=lambda _result: self.set_status("反演完成", "ok"),
            on_fail=lambda message, _detail: self.set_status(
                f"失败：{message}", "bad"))

    def refresh_theme(self) -> None:
        super().refresh_theme()
        if getattr(self, "_plot", None) is not None:
            self._plot.refresh_theme()


def _wrap(layout) -> QWidget:
    """把横向布局包成一个控件（用于 FormRow 的右侧）。"""
    holder = QWidget()
    holder.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    return holder


def _humidity_text(mode: str) -> str:
    """湿度处理方式说明。"""
    return {"none": "比湿 q（直接读取）",
            "rh_to_q": "相对湿度 RH → 比湿 q（需换算）",
            "q_to_rh": "比湿 q → 相对湿度 RH（需换算）"}.get(mode, "—")


__all__ = ["RetrievePage"]
