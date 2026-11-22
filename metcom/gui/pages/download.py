"""02 · 再分析数据下载：CDS API 拉取 ERA5，支持 IDM 多线程加速。

左栏是下载参数，右栏是即时预览：五源时间覆盖条 + 本次下载范围概要，
用来快速判断"我要的时间段，本地已经有哪些源覆盖了"。
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from metcom.gui import charts, data, icons
from metcom.gui.components.cards import Card, KeyValueRow
from metcom.gui.components.inputs import (FormRow, PathField, checkbox, combo,
                                          hint, line_edit, spin)
from metcom.gui.pages.base import BasePage


class DownloadPage(BasePage):
    """02 数据下载。"""

    page_key = "download"
    module_key = "download"
    page_title = "02 · 数据下载"
    page_subtitle = "从 CDS 拉取 ERA5 再分析资料，可按日期自动分目录、可续传"
    page_icon = "download"

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        self._cfg: dict = {}
        super().__init__(ctx, parent)

    # ------------------------------------------------------------ 构建 --
    def build(self) -> None:
        run = self.run_button("开始下载")
        run.clicked.connect(self._run)
        self.add_action(run)
        save = self.button("保存配置", "save", "ghost")
        save.clicked.connect(self.save_config)
        self.add_action(save)

        left, right = self.columns(3, 2)
        left.addWidget(self._build_range_card())
        left.addWidget(self._build_output_card())
        right.addWidget(self._build_preview_card())
        right.addWidget(self._build_tips_card())
        right.addStretch(1)
        self._load()

    def _build_range_card(self) -> QWidget:
        card = Card("时间范围与数据源", "对应 Download_ERA5_IDM_multi_pureInfo.py",
                    "calendar")
        self._start = line_edit("", "YYYYMMDDHH，如 2024010300")
        card.add_widget(FormRow("起始时刻", self._start))
        self._end = line_edit("", "YYYYMMDDHH，如 2024010312")
        card.add_widget(FormRow("结束时刻", self._end))
        self._format = combo(["nc", "grib"], "nc")
        card.add_widget(FormRow("下载格式", self._format, "nc 供 03 反演直接读取"))
        self._cdsapirc = PathField("file", "", "选择 CDS API 密钥文件",
                                   "所有文件 (*)", "选择 cdsapirc")
        card.add_widget(FormRow("CDS 密钥", self._cdsapirc,
                                "两行文本：url 与 key，见 CDSAPI/.cdsapirc_test"))
        return card

    def _build_output_card(self) -> QWidget:
        card = Card("输出与加速", "文件按 yyyyMMdd 日子目录组织", "download")
        self._output = PathField("dir", "", "下载目录")
        card.add_widget(FormRow("保存目录", self._output))
        self._use_idm = checkbox("使用 IDM 加速（仅 Windows）")
        card.add_widget(self._use_idm)
        self._idm_path = PathField("file", "", "选择 IDMan.exe",
                                   "可执行文件 (*.exe)", "选择 IDM")
        card.add_widget(FormRow("IDM 路径", self._idm_path))
        self._workers = spin(1, 16, 4, 1, " 个")
        card.add_widget(FormRow("并发数", self._workers, "建议不超过 4"))
        self._min_size = spin(1, 2000, 220, 10, " MB")
        card.add_widget(FormRow("完整文件阈值", self._min_size,
                                "小于该值视为不完整，会删除重下"))
        self._request_retries = spin(0, 10, 3, 1, " 次")
        card.add_widget(FormRow("请求重试", self._request_retries))
        self._download_retries = spin(0, 20, 5, 1, " 次")
        card.add_widget(FormRow("下载重试", self._download_retries))
        return card

    def _build_preview_card(self) -> QWidget:
        card = Card("即时预览 · 时间覆盖", "本地已有结果的五源时间覆盖对比", "calendar")
        self._ds = data.dataset(self.ctx.root)
        self._timeline = charts.TimelineBar()
        card.add_widget(self._timeline)
        self._rows: dict[str, KeyValueRow] = {}
        for title in ("本次下载范围", "预计文件数", "本地覆盖区间"):
            row = KeyValueRow(title, "—")
            self._rows[title] = row
            card.add_widget(row)
        self._preview_hint = hint("")
        card.add_widget(self._preview_hint)
        self._fill_timeline()
        return card

    def _build_tips_card(self) -> QWidget:
        card = Card("提示", "下载前请确认", "info")
        for text in (
            "CDS 密钥缺失时下载会直接失败，可先用「环境与设置」页自检。",
            "全球 0.25° 单时次约 220MB，按需缩短时间范围可显著提速。",
            "GRIB 格式需要 cfgrib 才能直读，建议保持 nc。",
        ):
            card.add_widget(hint("· " + text))
        return card

    # ------------------------------------------------------------ 数据 --
    def _fill_timeline(self) -> None:
        rows = []
        for key in data.SOURCE_ORDER:
            bounds = self._ds.time_bounds([key])
            if bounds is None:
                continue
            rows.append({"label": self._ds.label(key),
                         "color": icons.source_color(key),
                         "start": bounds[0], "end": bounds[1]})
        self._timeline.set_data(rows)

    def _load(self) -> None:
        self._cfg = self.ctx.load_config("download") or {}
        self._start.setText(str(self._cfg.get("start_time", "")))
        self._end.setText(str(self._cfg.get("end_time", "")))
        self._format.setCurrentText(str(self._cfg.get("format", "nc")))
        self._cdsapirc.set_value(self._cfg.get("cdsapirc_path", ""))
        self._output.set_value(self._cfg.get("output_dir", ""))
        self._use_idm.setChecked(bool(self._cfg.get("use_idm", False)))
        self._idm_path.set_value(self._cfg.get("idm_path", ""))
        self._workers.setValue(int(self._cfg.get("download_workers", 4) or 4))
        self._min_size.setValue(int(self._cfg.get("min_file_size_mb", 220) or 220))
        self._request_retries.setValue(int(self._cfg.get("request_retries", 3) or 3))
        self._download_retries.setValue(
            int(self._cfg.get("download_retries", 5) or 5))
        self._refresh_preview()
        self.set_status("配置已载入 configs/metcom_downloader.yaml", "accent")

    def _refresh_preview(self) -> None:
        start, end = self._start.text().strip(), self._end.text().strip()
        self._rows["本次下载范围"].set_value(
            f"{start} → {end}" if start and end else "请输入 YYYYMMDDHH 格式的范围")
        hours = _hours_between(start, end)
        self._rows["预计文件数"].set_value(
            f"{hours} 个时次（每小时 1 个文件）" if hours else "—")
        bounds = self._ds.time_bounds()
        self._rows["本地覆盖区间"].set_value(
            f"{bounds[0]:%Y-%m-%d %H} → {bounds[1]:%Y-%m-%d %H}" if bounds
            else "本地暂无结果")
        self._preview_hint.setText(
            "覆盖条越长的数据源时间跨度越大；下载完成后回到本页可对比是否已补齐。")

    def collect_config(self) -> dict:
        cfg = dict(self._cfg)
        cfg.update({
            "cdsapirc_path": self._cdsapirc.value(),
            "start_time": self._start.text().strip(),
            "end_time": self._end.text().strip(),
            "format": self._format.currentText(),
            "output_dir": self._output.value(),
            "use_idm": bool(self._use_idm.isChecked()),
            "idm_path": self._idm_path.value(),
            "download_workers": int(self._workers.value()),
            "min_file_size_mb": int(self._min_size.value()),
            "request_retries": int(self._request_retries.value()),
            "download_retries": int(self._download_retries.value()),
        })
        self._cfg = cfg
        return cfg

    # ------------------------------------------------------------ 运行 --
    def _run(self) -> None:
        self._refresh_preview()
        self.ctx.run_module(
            "download", self.collect_config(), strict=True,
            on_done=lambda _result: self.set_status("下载完成", "ok"),
            on_fail=lambda message, _detail: self.set_status(
                f"失败：{message}", "bad"))

    def refresh_theme(self) -> None:
        super().refresh_theme()
        if getattr(self, "_timeline", None) is not None:
            self._timeline.refresh_theme()


def _hours_between(start: str, end: str) -> int:
    """粗略估算时次数（用于"预计文件数"提示）。"""
    import pandas as pd

    try:
        left = pd.to_datetime(start, format="%Y%m%d%H")
        right = pd.to_datetime(end, format="%Y%m%d%H")
    except (ValueError, TypeError):
        return 0
    # 空字符串会让 pandas 返回 NaT 而不是抛错，相减得 NaN，int(NaN) 会连累
    # 整个界面构造，这里提前挡掉。
    if pd.isna(left) or pd.isna(right):
        return 0
    hours = int((right - left).total_seconds() // 3600) + 1
    return max(0, hours)


__all__ = ["DownloadPage"]
