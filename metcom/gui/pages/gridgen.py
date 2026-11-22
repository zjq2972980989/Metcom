"""01 · 格点生成：DEM 区域裁剪 + 正高转大地高，输出站点列表。

左栏是配置表单（对应 ``configs/metcom_gridgen.yaml`` 的 dem / stations 两段），
右栏是即时预览：站点空间分布图 + 区域表 + 示例站点列表信息。
区域表结构较复杂，界面只做只读展示，编辑仍走 YAML。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView,
                               QTableWidget, QTableWidgetItem, QWidget)

from metcom.gui import charts_map, data
from metcom.gui.components.cards import Card, KeyValueRow, section_label
from metcom.gui.components.inputs import (FormRow, PathField, dspin, hint,
                                          line_edit, spin)
from metcom.gui.pages.base import BasePage


class GridgenPage(BasePage):
    """01 格点生成。"""

    page_key = "gridgen"
    module_key = "gridgen"
    page_title = "01 · 格点生成"
    page_subtitle = "由 DEM 与行政区划生成格点清单，并把正高换算为大地高"
    page_icon = "grid"

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        self._cfg: dict = {}
        super().__init__(ctx, parent)

    # ------------------------------------------------------------ 构建 --
    def build(self) -> None:
        run = self.run_button("生成格点文件")
        run.clicked.connect(self._run)
        self.add_action(run)
        save = self.button("保存配置", "save", "ghost")
        save.clicked.connect(self.save_config)
        self.add_action(save)

        left, right = self.columns(3, 2)
        left.addWidget(self._build_dem_card())
        left.addWidget(self._build_station_card())
        right.addWidget(self._build_preview_card())
        right.addWidget(self._build_region_card())
        right.addStretch(1)
        self._load()

    def _build_dem_card(self) -> QWidget:
        card = Card("步骤 1A · DEM 区域裁剪",
                    "对应原 MATLAB ShpDemToRegion_Masked_region.m", "layers")
        self._dem_path = PathField("file", "", "选择全球 DEM（NetCDF）",
                                   "NetCDF (*.nc)", "选择 DEM 文件")
        card.add_widget(FormRow("DEM 文件", self._dem_path))
        self._city_shp = PathField("file", "", "选择行政区划 shapefile",
                                   "Shapefile (*.shp)", "选择行政区划")
        card.add_widget(FormRow("行政区划", self._city_shp))
        self._dem_out = PathField("dir", "", "输出目录")
        card.add_widget(FormRow("输出目录", self._dem_out))
        self._resolution = dspin(0.05, 1.0, 0.125, 3, 0.025, " °")
        card.add_widget(FormRow("目标分辨率", self._resolution, "支持 0.125 / 0.25 及其整数倍"))
        return card

    def _build_station_card(self) -> QWidget:
        card = Card("步骤 1B · 格点文本生成",
                    "对应原 MATLAB OrthHeight2EllHeight.m", "grid")
        self._dem_file = PathField("file", "", "选择上一步输出的 DEM",
                                   "NetCDF (*.nc)", "选择 DEM 文件")
        card.add_widget(FormRow("输入 DEM", self._dem_file))
        self._prefix = line_edit("", "输出文件名前缀，如 bth")
        card.add_widget(FormRow("文件名前缀", self._prefix))
        self._base_res = dspin(0.05, 1.0, 0.125, 3, 0.025, " °")
        card.add_widget(FormRow("基础分辨率", self._base_res))
        self._out_res = dspin(0.05, 2.0, 0.125, 3, 0.025, " °")
        card.add_widget(FormRow("输出分辨率", self._out_res, "须为基础分辨率的整数倍"))
        self._sta_out = PathField("dir", "", "格点 txt 输出目录")
        card.add_widget(FormRow("输出目录", self._sta_out))
        self._max_stations = spin(100, 200000, 25000, 500, " 个")
        card.add_widget(FormRow("最大站点数", self._max_stations))
        self._egm_coarse = PathField("file", "", "0.25° EGM2008 格网")
        card.add_widget(FormRow("EGM 粗格网", self._egm_coarse))
        self._egm_fine = PathField("file", "", "0.125° EGM2008 格网")
        card.add_widget(FormRow("EGM 细格网", self._egm_fine))
        return card

    def _build_preview_card(self) -> QWidget:
        card = Card("即时预览 · 站点分布", "已有基准站点列表的空间分布", "map")
        self._ds = data.dataset(self.ctx.root)
        if charts_map.available():
            self._map = charts_map.MapCanvas(compact=True)
            self._map.setMinimumHeight(260)
            geo = self._ds.geo()
            if not geo.empty:
                self._map.set_sites(geo, "alt", label="测站高程", unit="m",
                                    mode="points", region="CHN")
            card.add_widget(self._map, 1)
        else:
            self._map = None
            card.add_widget(hint(charts_map.unavailable_reason() or "地图不可用"))
        sample = Path(self.ctx.root) / "benchmarks" / "bth_0125_orthometric.txt"
        rows = {"示例站点列表": str(sample) if sample.is_file() else "未找到",
                "站点数量": f"{len(self._ds.sites())} 个"}
        for title, value in rows.items():
            card.add_widget(KeyValueRow(title, value, mono=title.endswith("列表")))
        return card

    def _build_region_card(self) -> QWidget:
        card = Card("区域表（只读）", "复杂结构请在 configs/metcom_gridgen.yaml 中编辑",
                    "table")
        self._regions = QTableWidget(0, 4)
        self._regions.setHorizontalHeaderLabels(["名称", "范围 经度 / 纬度", "掩膜", "输出文件"])
        self._regions.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._regions.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._regions.verticalHeader().setVisible(False)
        self._regions.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self._regions.setMinimumHeight(180)
        card.add_widget(self._regions)
        return card

    # ------------------------------------------------------------ 数据 --
    def _load(self) -> None:
        self._cfg = self.ctx.load_config("gridgen") or {}
        dem = self._cfg.get("dem") or {}
        stations = self._cfg.get("stations") or {}
        self._dem_path.set_value(dem.get("dem_path", ""))
        self._city_shp.set_value(dem.get("city_shp", ""))
        self._dem_out.set_value(dem.get("output_dir", ""))
        self._resolution.setValue(float(dem.get("resolution", 0.125) or 0.125))
        self._dem_file.set_value(stations.get("dem_file", ""))
        self._prefix.setText(str(stations.get("output_prefix", "")))
        self._base_res.setValue(float(stations.get("base_resolution", 0.125) or 0.125))
        self._out_res.setValue(float(stations.get("output_resolution", 0.125) or 0.125))
        self._sta_out.set_value(stations.get("output_dir", ""))
        self._max_stations.setValue(int(stations.get("max_stations", 25000) or 25000))
        self._egm_coarse.set_value(stations.get("egm_coarse", "") or "")
        self._egm_fine.set_value(stations.get("egm_fine", "") or "")
        self._fill_regions(dem.get("regions") or [])
        self.set_status("配置已载入 configs/metcom_gridgen.yaml", "accent")

    def _fill_regions(self, regions: list) -> None:
        self._regions.setRowCount(len(regions))
        for row, region in enumerate(regions):
            extent = region.get("extent") or [None] * 4
            values = [
                str(region.get("name", "")),
                _extent_text(extent),
                "行政边界" if region.get("apply_mask") else "矩形",
                str(region.get("file_name", "")),
            ]
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft
                                      | Qt.AlignmentFlag.AlignVCenter)
                self._regions.setItem(row, column, item)

    def collect_config(self) -> dict:
        dem = dict(self._cfg.get("dem") or {})
        stations = dict(self._cfg.get("stations") or {})
        dem.update({
            "dem_path": self._dem_path.value(),
            "city_shp": self._city_shp.value(),
            "output_dir": self._dem_out.value(),
            "resolution": float(self._resolution.value()),
        })
        stations.update({
            "dem_file": self._dem_file.value(),
            "output_prefix": self._prefix.text().strip(),
            "base_resolution": float(self._base_res.value()),
            "output_resolution": float(self._out_res.value()),
            "output_dir": self._sta_out.value(),
            "max_stations": int(self._max_stations.value()),
            "egm_coarse": self._egm_coarse.value(),
            "egm_fine": self._egm_fine.value(),
        })
        self._cfg["dem"] = dem
        self._cfg["stations"] = stations
        return self._cfg

    # ------------------------------------------------------------ 运行 --
    def _run(self) -> None:
        self.ctx.run_module(
            "gridgen", self.collect_config(), strict=True,
            on_done=lambda _result: self.set_status("格点文件已生成", "ok"),
            on_fail=lambda message, _detail: self.set_status(
                f"失败：{message}", "bad"))

    def refresh_theme(self) -> None:
        super().refresh_theme()
        if getattr(self, "_map", None) is not None:
            self._map.refresh_theme()


def _extent_text(extent) -> str:
    try:
        return (f"{float(extent[0]):.2f} ~ {float(extent[1]):.2f} / "
                f"{float(extent[2]):.2f} ~ {float(extent[3]):.2f}")
    except (TypeError, ValueError, IndexError):
        return "—"


__all__ = ["GridgenPage"]
