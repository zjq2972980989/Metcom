"""05 . 可视化工作台：地图 / 曲线 / 五源对比。

本页是"以图形化展示为核心"这一设计目标的落点，采用
「左视图导航 + 中央大画布 + 右参数面板」三栏结构：

  叠加曲线    五源同图叠加（pyqtgraph，1443 站点任意缩放不卡）
  分面小倍数  行 = 数据源、列 = 变量，共享 X 轴与全局十字准线
  指标对比表  RMSE / Bias / MAE* / 样本数，按数值热度着色、可排序
  雷达图      多维归一化，看"哪个源在哪个维度上更强"
  站点地图    cartopy 真实省界底图 + 站点散点 / 数值热度栅格

数据来源在启动时自动判定：有用户自己跑出的结果就用用户结果，否则
载入 ``benchmarks/`` 下的基准数据作为演示；任一流程跑完后自动切换。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QComboBox, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QPushButton, QStackedWidget,
                               QVBoxLayout, QWidget)

from metcom.gui import charts, charts_map, data, icons, theme
from metcom.gui.components.cards import Card, KeyValueRow, section_label
from metcom.gui.components.inputs import (FormRow, checkbox, combo, hint,
                                          line_edit, spin)
from metcom.gui.pages.base import BasePage

VIEWS: list[tuple[str, str, str, str]] = [
    ("overlay", "叠加曲线", "trend", "五源同图叠加：看趋势、看分离度"),
    ("facets", "分面小倍数", "grid", "行 = 数据源、列 = 变量，一格一图"),
    ("table", "指标对比表", "table", "RMSE / Bias / MAE* / 样本数，热度着色"),
    ("radar", "雷达图", "radar", "多维归一化，强弱一眼可见"),
    ("map", "站点地图", "map", "省界底图 + 1443 站点散点 / 数值热度"),
]

MAP_FIELDS: list[tuple[str, str, str]] = [
    ("ztd_rmse", "ZTD RMSE", "mm"),
    ("ztd_bias", "ZTD Bias", "mm"),
    ("pwv_rmse", "PWV RMSE", "mm"),
    ("pwv_bias", "PWV Bias", "mm"),
    ("alt", "测站高程", "m"),
    ("ztd_value", "ZTD 数值", "mm"),
    ("pwv_value", "PWV 数值", "mm"),
]

METRIC_LABEL = "MAE*（由 RMSE 与 Bias 依 ``MAE = sqrt(2/pi) . sqrt(RMSE^2 - Bias^2)`` 推算）"

# 雷达图维度。口径不同的维度直接写进名字里：`*` 为派生值、`(规格)` 为非实测的
# 数据源规格，其余一律取实测产出，定义见 metcom.gui.data 模块文档「关于统计口径」。
RADAR_SOURCE_AXES: list[str] = ["精度 RMSE", "偏差 Bias", "相关性*", "采样间隔",
                                "空间分辨率(规格)", "站点覆盖"]
RADAR_STATION_AXES: list[str] = ["ZTD RMSE", "ZTD Bias", "PWV RMSE", "PWV Bias",
                                 "相关性*", "样本数"]
RADAR_BASIS = ("口径：精度 / 偏差 / 采样间隔 / 站点覆盖都是实测值——精度与偏差取全部站点的"
               "中位数，采样间隔取相邻产出时刻的中位间隔，站点覆盖 = 有效站点 ÷ 站网规模；"
               "相关性* = 1 - |Bias| / RMSE 为派生值；空间分辨率(规格) 取数据源声明的模式网格，"
               "五源都插值到同一套 GNSS 站网、站网间距实测五源相同，无法从产出中区分。")


class _Rail(QFrame):
    """左侧视图导航：图标 + 名称，选中态走强调色。"""

    changed = Signal(str)

    def __init__(self, views: list[tuple[str, str, str, str]],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Rail")
        self.setFixedWidth(180)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 14, 12, 14)
        root.setSpacing(6)

        head = QLabel("视图")
        head.setObjectName("RailHead")
        root.addWidget(head)
        root.addSpacing(2)

        self._buttons: dict[str, tuple[QPushButton, str]] = {}
        for key, label, icon_name, tip in views:
            button = QPushButton("  " + label)
            button.setObjectName("RailButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(tip)
            button.setIconSize(QSize(18, 18))
            button.setMinimumHeight(38)
            button.clicked.connect(lambda _=False, k=key: self.select(k))
            root.addWidget(button)
            self._buttons[key] = (button, icon_name)

        root.addStretch(1)
        self._foot = QLabel("")
        self._foot.setObjectName("RailHead")
        self._foot.setWordWrap(True)
        root.addWidget(self._foot)
        self._active = ""
        self.refresh_theme()

    def select(self, key: str) -> None:
        if key == self._active:
            return
        self.set_active(key)
        self.changed.emit(key)

    def set_active(self, key: str) -> None:
        self._active = key
        palette = theme.palette()
        for k, (button, icon_name) in self._buttons.items():
            active = k == key
            button.setChecked(active)
            button.setIcon(icons.icon(icon_name,
                                      palette.accent if active else palette.text_muted,
                                      18))

    def set_foot(self, text: str) -> None:
        self._foot.setText(text)

    def refresh_theme(self) -> None:
        self.set_active(self._active)


class VisualizePage(BasePage):
    """五源对比与空间展示的综合工作台。"""

    page_key = "visualize"
    module_key = "visualize"
    page_title = "可视化工作台"
    page_subtitle = "地图 · 曲线 · 五源对比 —— 反演结果与精度指标的图形化分析"
    page_icon = "map"

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        self._ds = None
        self._keys: list[str] = []
        self._site = ""
        self._var = "ztd"
        self._window = None
        self._view = "overlay"
        self._charts: list = []
        self._dirty = {item[0] for item in VIEWS}
        self._skip_view: str | None = None
        self._pending_reload = False
        super().__init__(ctx, parent)

    # ------------------------------------------------------------ 构建 --
    def build(self) -> None:
        self._ds = data.dataset(self.ctx.root)
        self._keys = list(self._ds.keys())
        self._site = self._default_site()
        self._window = self._ds.common_window(self._keys)
        self._view = "overlay"

        self._origin_chip = QLabel(self._ds.origin_text())
        self._origin_chip.setObjectName("OriginChip")
        self._origin_chip.setToolTip("数据来源：演示基准数据 / 我的反演结果")
        self.add_action(self._origin_chip)
        button = self.button("重新扫描", "refresh", "ghost", "重新扫描结果目录")
        button.clicked.connect(self._reload)
        self.add_action(button)

        row = QHBoxLayout()
        row.setSpacing(14)
        self._rail = _Rail(VIEWS)
        self._rail.changed.connect(self._switch_view)
        row.addWidget(self._rail, 0)
        row.addWidget(self._build_canvas(), 1)
        row.addWidget(self._build_side(), 0)
        self.body().addLayout(row, 1)
        self._sync_origin()
        self._switch_view("overlay", force=True)
        self.ctx.controller.finished.connect(self._on_task_done)

    def _default_site(self) -> str:
        sites = self._ds.sites()
        return sites[0] if sites else ""

    def _build_canvas(self) -> QWidget:
        host = QFrame()
        host.setObjectName("CanvasHost")
        root = QVBoxLayout(host)
        root.setContentsMargins(16, 14, 16, 16)
        root.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(10)
        self._canvas_title = QLabel("")
        self._canvas_title.setObjectName("CanvasTitle")
        head.addWidget(self._canvas_title)
        self._canvas_hint = QLabel("")
        self._canvas_hint.setObjectName("CanvasHint")
        head.addWidget(self._canvas_hint, 1)
        self._stat_pill = QLabel("")
        self._stat_pill.setObjectName("StatPill")
        head.addWidget(self._stat_pill, 0)
        reset = self.button("复位", "fit", "ghost", "复位当前视图的缩放")
        reset.clicked.connect(self._reset_view)
        head.addWidget(reset, 0)
        export = self.button("导出 PNG", "save", "ghost", "把当前视图保存成图片")
        export.clicked.connect(self._export_view)
        head.addWidget(export, 0)
        root.addLayout(head)

        self._stack = QStackedWidget()
        self._stack.setObjectName("CanvasArea")
        self._builders = {
            "overlay": self._build_overlay,
            "facets": self._build_facets,
            "table": self._build_table,
            "radar": self._build_radar,
            "map": self._build_map,
        }
        self._index: dict[str, int] = {}
        for key, _, _, _ in VIEWS:
            self._index[key] = self._stack.addWidget(self._builders[key]())
        root.addWidget(self._stack, 1)
        return host

    def _build_side(self) -> QWidget:
        holder = QWidget()
        holder.setFixedWidth(312)
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(14)
        column.addWidget(self._build_data_card())
        column.addWidget(self._build_option_card())
        column.addStretch(1)
        return holder

    # -------------------------------------------------------- 数据面板 --
    def _build_data_card(self) -> QWidget:
        card = Card("数据来源", "五源统一加载层，界面与业务共用同一份结果", "database")

        self._legend = charts.LegendBar()
        self._legend.set_items([(self._ds.label(key), icons.source_color(key))
                                for key in data.SOURCE_ORDER])
        card.add_widget(self._legend)

        self._site_combo = QComboBox()
        self._site_combo.setEditable(True)
        self._site_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._site_combo.addItems(list(self._ds.sites()))
        if self._site:
            self._site_combo.setCurrentText(self._site)
        completer = self._site_combo.completer()
        if completer is not None:
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._site_combo.currentTextChanged.connect(self._on_site_changed)
        card.add_widget(FormRow("站点", self._site_combo, label_width=66,
                                hint="输入编号或片段可模糊搜索 1443 个测站"))

        self._var_combo = combo(["ZTD (mm)", "PWV (mm)"], "ZTD (mm)")
        self._var_combo.currentIndexChanged.connect(self._on_var_changed)
        card.add_widget(FormRow("变量", self._var_combo, label_width=66))

        # 默认用的是"公共时段"，下拉框初值必须跟着它，否则界面显示"全部时段"
        # 而实际按公共时段出图。
        items = self._window_items()
        self._window_combo = combo(items, items[1] if len(items) > 1 else items[0])
        self._window_combo.currentIndexChanged.connect(self._on_window_changed)
        card.add_widget(FormRow("时间窗", self._window_combo, label_width=66))

        card.add_widget(section_label("参与对比的数据源"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        self._source_boxes: dict[str, QComboBox] = {}
        for position, key in enumerate(data.SOURCE_ORDER):
            box = checkbox(self._ds.label(key), key in self._keys)
            box.setIcon(QIcon(icons.source_badge(key, 20)))
            box.setIconSize(QSize(20, 20))
            box.toggled.connect(self._on_sources_changed)
            grid.addWidget(box, position // 2, position % 2)
            self._source_boxes[key] = box
        card.add_layout(grid)

        self._info_box = QVBoxLayout()
        self._info_box.setSpacing(4)
        card.add_layout(self._info_box)
        self._info_rows: list[KeyValueRow] = []
        for title in ("结果目录", "时间覆盖", "站点 / 时次"):
            row = KeyValueRow(title, "-", mono=title == "结果目录")
            self._info_rows.append(row)
            self._info_box.addWidget(row)
        return card

    # -------------------------------------------------------- 选项面板 --
    def _build_option_card(self) -> QWidget:
        card = Card("视图选项", "随左侧视图自动切换", "sliders")
        self._option_stack = QStackedWidget()
        builders = {
            "overlay": self._options_overlay,
            "facets": self._options_facets,
            "table": self._options_table,
            "radar": self._options_radar,
            "map": self._options_map,
        }
        self._option_index: dict[str, int] = {}
        for key, _, _, _ in VIEWS:
            self._option_index[key] = self._option_stack.addWidget(builders[key]())
        card.add_widget(self._option_stack)
        return card

    @staticmethod
    def _page() -> tuple[QWidget, QVBoxLayout]:
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        return holder, column

    def _options_overlay(self) -> QWidget:
        page, column = self._page()
        self._overlay_mode = combo(["站点数值曲线", "逐时次精度指标"], "站点数值曲线")
        self._overlay_mode.currentIndexChanged.connect(self._invalidate)
        column.addWidget(FormRow("曲线内容", self._overlay_mode, label_width=66))
        self._overlay_field = combo(["RMSE (mm)", "Bias (mm)"], "RMSE (mm)")
        self._overlay_field.currentIndexChanged.connect(self._invalidate)
        column.addWidget(FormRow("精度指标", self._overlay_field, label_width=66))
        self._box_zoom = checkbox("拖拽框选放大")
        self._box_zoom.toggled.connect(self._on_box_zoom)
        column.addWidget(self._box_zoom)
        column.addWidget(hint("悬停显示十字准线与各源读数；双击复位；"
                              "点击图例可单独隐藏某个源。"))
        return page

    def _options_facets(self) -> QWidget:
        page, column = self._page()
        self._facet_order = combo(["行 = 数据源", "行 = 变量"], "行 = 数据源")
        self._facet_order.currentIndexChanged.connect(self._on_facet_order)
        column.addWidget(FormRow("排列方式", self._facet_order, label_width=66))
        column.addWidget(hint("每个小图独立纵轴、共享时间横轴；"
                              "鼠标移动时十字准线在全部小图上联动。"))
        return page

    def _options_table(self) -> QWidget:
        page, column = self._page()
        self._table_mode = combo(["数据源汇总", "逐站明细"], "数据源汇总")
        self._table_mode.currentIndexChanged.connect(self._invalidate)
        column.addWidget(FormRow("表格内容", self._table_mode, label_width=66))
        self._table_field = combo(["ZTD RMSE", "ZTD Bias", "PWV RMSE", "PWV Bias"],
                                  "ZTD RMSE")
        self._table_field.currentIndexChanged.connect(self._invalidate)
        column.addWidget(FormRow("逐站字段", self._table_field, label_width=66))
        export = self.button("导出 CSV", "save", "ghost")
        export.clicked.connect(lambda: self._table.export_csv(self))
        column.addWidget(export)
        column.addWidget(hint("点击表头可按该列排序；绿色越深越好、红色越深越差。"))
        return page

    def _options_radar(self) -> QWidget:
        page, column = self._page()
        self._radar_mode = combo(["数据源对比", "站点对比"], "数据源对比")
        self._radar_mode.currentIndexChanged.connect(self._invalidate)
        column.addWidget(FormRow("对比模式", self._radar_mode, label_width=66))
        column.addWidget(hint("各维已归一化到 0~1，越靠外越好，不同量纲因此可以放在同一张"
                              "图里比较。鼠标悬停图表可查看归一化之前的原始数值与统计口径。"))
        return page

    def _options_map(self) -> QWidget:
        page, column = self._page()
        self._map_layer = combo(["站点散点", "数值热度"], "站点散点")
        self._map_layer.currentIndexChanged.connect(self._invalidate)
        column.addWidget(FormRow("图层", self._map_layer, label_width=66))
        self._map_field = combo([item[1] for item in MAP_FIELDS], "ZTD RMSE")
        self._map_field.currentIndexChanged.connect(self._invalidate)
        column.addWidget(FormRow("着色字段", self._map_field, label_width=66))
        self._map_source = combo([self._ds.label(k) for k in data.SOURCE_ORDER],
                                 self._ds.label(self._keys[0] if self._keys else "era5"))
        self._map_source.currentIndexChanged.connect(self._invalidate)
        column.addWidget(FormRow("数据源", self._map_source, label_width=66))
        self._map_region = combo(["全国", "京津冀"], "全国")
        self._map_region.currentIndexChanged.connect(self._on_map_region)
        column.addWidget(FormRow("范围", self._map_region, label_width=66))
        self._map_basemap = checkbox("显示省界与海岸线", True)
        self._map_basemap.toggled.connect(self._on_basemap)
        column.addWidget(self._map_basemap)
        self._map_size = spin(3, 20, 9, 1, " px")
        self._map_size.valueChanged.connect(self._invalidate)
        column.addWidget(FormRow("点大小", self._map_size, label_width=66))
        column.addWidget(hint("左键拖拽平移、滚轮缩放、双击复位；"
                              "单击站点会把其他视图同步切到该站。"))
        return page

    # ------------------------------------------------------ 画布构建 --
    def _build_overlay(self) -> QWidget:
        holder, column = self._page()
        self._overlay = charts.LinePlot()
        column.addWidget(self._overlay, 1)
        self._charts.append(self._overlay)
        return holder

    def _build_facets(self) -> QWidget:
        holder, column = self._page()
        self._facet_holder = column
        self._facets = charts.FacetGrid(var_first=False)
        column.addWidget(self._facets, 1)
        self._charts.append(self._facets)
        return holder

    def _build_table(self) -> QWidget:
        holder, column = self._page()
        self._table = charts.HeatTable()
        self._table_note = hint("")
        column.addWidget(self._table_note)
        column.addWidget(self._table, 1)
        self._charts.append(self._table)
        return holder

    def _build_radar(self) -> QWidget:
        holder, column = self._page()
        self._radar = charts.RadarChart()
        self._radar_note = hint("")
        column.addWidget(self._radar_note)
        column.addWidget(self._radar, 1)
        self._charts.append(self._radar)
        return holder

    def _build_map(self) -> QWidget:
        holder, column = self._page()
        self._map = None
        if not charts_map.available():
            message = QLabel(charts_map.unavailable_reason() or "地图组件不可用")
            message.setObjectName("CanvasHint")
            message.setAlignment(Qt.AlignmentFlag.AlignCenter)
            message.setWordWrap(True)
            column.addWidget(message, 1)
            return holder
        self._map = charts_map.MapCanvas()
        self._map.clicked.connect(self._on_map_clicked)
        self._map.hovered.connect(self._on_map_hovered)
        self._map_note = hint("")
        column.addWidget(self._map_note)
        column.addWidget(self._map, 1)
        self._charts.append(self._map)
        return holder

    # ------------------------------------------------------ 参数回调 --
    def _current_keys(self) -> list[str]:
        keys = [k for k in data.SOURCE_ORDER
                if self._source_boxes[k].isChecked() and self._ds.has(k)]
        return keys or [k for k in data.SOURCE_ORDER if self._ds.has(k)]

    def _on_sources_changed(self, _checked: bool = False) -> None:
        keys = self._current_keys()
        if keys == self._keys:
            return
        self._keys = keys
        self._rebuild_window_items()
        self._invalidate()

    def _on_site_changed(self, text: str) -> None:
        text = (text or "").strip()
        if not text or text == self._site:
            return
        self._site = text
        self._invalidate()

    def _on_var_changed(self, index: int) -> None:
        self._var = "pwv" if index == 1 else "ztd"
        self._invalidate()

    def _on_window_changed(self, index: int) -> None:
        self._window = self._resolve_window(index)
        self._invalidate()

    def _on_box_zoom(self, enabled: bool) -> None:
        self._overlay.set_box_zoom(enabled)

    def _on_facet_order(self, index: int) -> None:
        old = self._facets
        widget = charts.FacetGrid(var_first=index == 1)
        self._facet_holder.replaceWidget(old, widget)
        old.deleteLater()
        self._facets = widget
        try:
            self._charts[self._charts.index(old)] = widget
        except ValueError:
            self._charts.append(widget)
        self._invalidate()

    def _on_map_region(self, index: int) -> None:
        if self._map is not None:
            self._map.set_region("BTH" if index == 1 else "CHN")
        self._invalidate()

    def _on_basemap(self, enabled: bool) -> None:
        if self._map is not None:
            self._map.set_basemap(enabled)

    def _on_map_clicked(self, site: str) -> None:
        self.ctx.log(f"地图选中站点：{site}")
        if site and site != self._site:
            self._skip_view = "map"
            try:
                self._site_combo.setCurrentText(site)
            finally:
                self._skip_view = None

    def _on_map_hovered(self, site: str) -> None:
        if self._map is None:
            return
        self._map_note.setText(f"当前悬停：{site}" if site else "")

    def _on_task_done(self, *_args) -> None:
        """任一流程跑完：标记需要重扫；若正停在本页则立即刷新（切换演示/我的结果）。"""
        self._pending_reload = True
        if self.isVisible():
            self._reload()

    # ------------------------------------------------------ 视图切换 --
    def _window_items(self) -> list[str]:
        items = ["全部时段（各源自身覆盖）"]
        if self._ds.common_window(self._keys) is not None:
            start, end = self._ds.common_window(self._keys)
            items.append(f"公共时段 {_stamp(start)} → {_stamp(end)}")
        for key in self._ds.keys():
            items.append(f"{self._ds.label(key)} 覆盖 {_window_of(self._ds, key)}")
        return items

    def _resolve_window(self, index: int):
        if index <= 0:
            return None
        common = self._ds.common_window(self._keys)
        offset = 1 + (1 if common is not None else 0)
        if common is not None and index == 1:
            return common
        keys = list(self._ds.keys())
        position = index - offset
        if 0 <= position < len(keys):
            return self._ds.time_bounds([keys[position]])
        return common

    def _rebuild_window_items(self) -> None:
        current = self._window_combo.currentIndex()
        self._window_combo.blockSignals(True)
        self._window_combo.clear()
        self._window_combo.addItems(self._window_items())
        self._window_combo.setCurrentIndex(
            max(0, min(current, self._window_combo.count() - 1)))
        self._window_combo.blockSignals(False)
        self._window = self._resolve_window(self._window_combo.currentIndex())

    def _switch_view(self, key: str, force: bool = False) -> None:
        if not force and key == self._view:
            return
        self._view = key
        self._stack.setCurrentIndex(self._index[key])
        self._option_stack.setCurrentIndex(self._option_index[key])
        self._rail.set_active(key)
        for item_key, label, _, tip in VIEWS:
            if item_key == key:
                self._canvas_title.setText(label)
                self._canvas_hint.setText(tip)
                break
        self._apply_view(key)

    def _invalidate(self, *_args) -> None:
        """标脏全部视图并立即刷新当前视图。"""
        self._dirty = {key for key, _, _, _ in VIEWS}
        if self._skip_view:
            self._dirty.discard(self._skip_view)
        self._apply_view(self._view)

    def _apply_view(self, key: str) -> None:
        if key not in self._dirty:
            self._update_pill()
            return
        self._dirty.discard(key)
        handler = {"overlay": self._apply_overlay, "facets": self._apply_facets,
                   "table": self._apply_table, "radar": self._apply_radar,
                   "map": self._apply_map}[key]
        try:
            handler()
        except Exception as exc:                       # 单视图失败不影响整页
            self.ctx.log(f"警告: 视图「{key}」渲染失败（{type(exc).__name__}: {exc}）")
        self._update_pill()

    def _update_pill(self) -> None:
        text = f"{len(self._keys)} 源 · {self._site or '-'}"
        if self._window is not None:
            text += f" · {_stamp(self._window[0])}~{_stamp(self._window[1])}"
        self._stat_pill.setText(text)

    # ------------------------------------------------------ 视图数据 --
    def _apply_overlay(self) -> None:
        name = "PWV" if self._var == "pwv" else "ZTD"
        if self._overlay_mode.currentIndex() == 0:
            series = charts.station_series(self._ds, self._keys, self._site,
                                          self._var, self._window)
            ylabel = f"{name} (mm)"
        else:
            field = "bias" if self._overlay_field.currentIndex() == 1 else "rmse"
            series = charts.metric_series(self._ds, self._keys, self._var, field)
            ylabel = f"{name} {field.upper()} (mm)"
        self._overlay.set_series(series, ylabel=ylabel)

    def _apply_facets(self) -> None:
        self._facets.set_data(self._ds, self._keys, self._site, self._var,
                              (("ztd", "ZTD"), ("pwv", "PWV")), self._window)

    def _apply_table(self) -> None:
        if self._table_mode.currentIndex() == 0:
            columns = [
                {"title": "ZTD RMSE", "key": "ztd_rmse", "dir": "low", "fmt": "{:.2f}"},
                {"title": "ZTD Bias", "key": "ztd_bias", "dir": "low", "fmt": "{:.2f}"},
                {"title": "PWV RMSE", "key": "pwv_rmse", "dir": "low", "fmt": "{:.3f}"},
                {"title": "PWV Bias", "key": "pwv_bias", "dir": "low", "fmt": "{:.3f}"},
                {"title": "MAE*", "key": "mae", "dir": "low", "fmt": "{:.2f}"},
                {"title": "时次数", "key": "steps", "dir": "high",
                 "text": lambda v: f"{int(v)}"},
                {"title": "站点数", "key": "sites", "dir": "none",
                 "text": lambda v: f"{int(v)}"},
            ]
            self._table.set_columns(columns, "数据源")
            self._table.set_rows(self._summary_rows())
            self._table_note.setText(
                "全部站点中位数汇总（参考真值为 ERA5_GMET）；"
                + METRIC_LABEL)
        else:
            field = ("ztd_rmse", "ztd_bias", "pwv_rmse", "pwv_bias")[
                self._table_field.currentIndex()]
            columns = [{"title": self._ds.label(k), "key": k, "dir": "low",
                        "fmt": "{:.2f}"} for k in self._keys]
            self._table.set_columns(columns, "站点")
            rows, shown, total = self._station_rows(field)
            self._table.set_rows(rows)
            self._table_note.setText(
                f"逐站 {FIELD_LABELS[field]}：显示 {shown} / {total} 站"
                "（按各源中位数升序，越靠前表示整体越准）")

    def _summary_rows(self) -> list[dict]:
        rows: list[dict] = []
        for key in self._keys:
            summary = self._ds.get(key).summary()
            rmse, bias = summary.get("ztd_rmse"), summary.get("ztd_bias")
            rows.append({"label": self._ds.label(key), "values": [
                _num(rmse), _num(bias),
                _num(summary.get("pwv_rmse")), _num(summary.get("pwv_bias")),
                _mae(rmse, bias),
                _num(summary.get("steps")), _num(summary.get("sites")),
            ]})
        return rows

    def _station_rows(self, field: str, cap: int = 800) -> tuple[list[dict], int, int]:
        merged: pd.DataFrame | None = None
        for key in self._keys:
            source = self._ds.get(key)
            if source is None:
                continue
            frame = source.stations()
            if frame.empty or field not in frame.columns:
                continue
            part = frame[["site", field]].rename(columns={field: key})
            merged = part if merged is None else merged.merge(part, on="site",
                                                              how="outer")
        if merged is None or merged.empty:
            return [], 0, 0
        total = int(merged.shape[0])
        present = [k for k in self._keys if k in merged.columns]
        order = merged[present].median(axis=1, skipna=True).sort_values().index
        merged = merged.loc[order]
        rows = [{"label": str(record["site"]),
                 "values": [_num(record.get(k)) for k in self._keys]}
                for _, record in merged.head(cap).iterrows()]
        return rows, len(rows), total

    def _apply_radar(self) -> None:
        if self._radar_mode.currentIndex() == 0:
            axes, series, note = self._radar_sources()
        else:
            axes, series, note = self._radar_station()
        text = "\n".join(line for line in note if line)
        self._radar.set_data(axes, series, note=text)
        self._radar_note.setText(text)

    def _radar_sources(self) -> tuple[list[str], list[dict], list[str]]:
        """五源六维对比：每一维的原始值与统计口径一并产出（供悬停与图注）。"""
        rows = [(key, self._ds.stats(key)) for key in self._keys]
        scores: list[list[float | None]] = [[] for _ in RADAR_SOURCE_AXES]
        raws: list[list[str]] = [[] for _ in RADAR_SOURCE_AXES]
        for key, item in rows:
            summary = self._ds.get(key).summary()
            rmse = _num(summary.get("ztd_rmse"))
            bias = _num(summary.get("ztd_bias"))
            gap = abs(bias) if bias is not None else None
            link = 1.0 - min(1.0, gap / rmse) if rmse and gap is not None else None
            values = (rmse, gap, link,
                      item.interval_hours if item is not None else None,
                      item.grid_deg if item is not None else None,
                      item.site_ratio if item is not None else None)
            for slot, value in enumerate(values):
                scores[slot].append(value)
            raws[0].append(_cell(rmse, "{:.2f} mm"))
            raws[1].append(_cell(gap, "{:.2f} mm"))
            raws[2].append(_cell(link, "{:.3f}"))
            if item is None:
                raws[3].append("—")
                raws[4].append("—")
                raws[5].append("—")
            else:
                raws[3].append(_cell(item.interval_hours, "{:g} h")
                               + item.interval_text())
                raws[4].append(_cell(item.grid_deg, "{:g}°"))
                raws[5].append(f"{item.sites}/{item.network}")
        normalized = [_normalize(scores[0], "low"), _normalize(scores[1], "low"),
                      _normalize(scores[2], "high"), _normalize(scores[3], "low"),
                      _normalize(scores[4], "low"), _normalize(scores[5], "high")]
        series = self._radar_series(self._keys, normalized, raws)
        return list(RADAR_SOURCE_AXES), series, [RADAR_BASIS, _measured_line(rows)]

    def _radar_station(self) -> tuple[list[str], list[dict], list[str]]:
        scores: list[list[float | None]] = [[] for _ in RADAR_STATION_AXES]
        raws: list[list[str]] = [[] for _ in RADAR_STATION_AXES]
        for key in self._keys:
            row = _station_row(self._ds.get(key), self._site)
            zrmse, zbias = row.get("ztd_rmse"), row.get("ztd_bias")
            prmse, pbias = row.get("pwv_rmse"), row.get("pwv_bias")
            gap = abs(zbias) if zbias is not None else None
            spread = abs(pbias) if pbias is not None else None
            link = 1.0 - min(1.0, gap / zrmse) if zrmse and gap is not None else None
            counts = row.get("n")
            for slot, value in enumerate((zrmse, gap, prmse, spread, link, counts)):
                scores[slot].append(value)
            raws[0].append(_cell(zrmse, "{:.2f} mm"))
            raws[1].append(_cell(gap, "{:.2f} mm"))
            raws[2].append(_cell(prmse, "{:.3f} mm"))
            raws[3].append(_cell(spread, "{:.3f} mm"))
            raws[4].append(_cell(link, "{:.3f}"))
            raws[5].append(f"{int(counts)}" if counts is not None else "—")
        normalized = [_normalize(scores[0], "low"), _normalize(scores[1], "low"),
                      _normalize(scores[2], "low"), _normalize(scores[3], "low"),
                      _normalize(scores[4], "high"), _normalize(scores[5], "high")]
        series = self._radar_series(self._keys, normalized, raws)
        note = (f"口径：全部取站点 {self._site or '-'} 在评估指标表里的原始值（实测）；"
                "样本数 = 该站参与评估的时次数；相关性* = 1 - |ZTD Bias| / ZTD RMSE（派生）。")
        return list(RADAR_STATION_AXES), series, [note]

    def _radar_series(self, keys, normalized, raws=None) -> list[dict]:
        colors = charts.source_colors(keys)
        series = []
        for position, key in enumerate(keys):
            series.append({
                "name": self._ds.label(key),
                "color": colors.get(key),
                "values": [values[position] for values in normalized],
                "raw": [column[position] for column in raws] if raws else [],
            })
        return series

    # ---------------------------------------------------------- 地图 --
    def _map_key(self) -> str:
        index = max(0, self._map_source.currentIndex())
        keys = list(data.SOURCE_ORDER)
        return keys[min(index, len(keys) - 1)]

    def _map_column(self) -> tuple[str, str, str]:
        index = max(0, self._map_field.currentIndex())
        return MAP_FIELDS[min(index, len(MAP_FIELDS) - 1)]

    def _map_frame(self, key: str, column: str) -> pd.DataFrame:
        geo = self._ds.geo()
        source = self._ds.get(key)
        if geo.empty or source is None:
            return pd.DataFrame()
        stations = source.stations()
        if column in geo.columns:
            values = geo.set_index("site")[column]
        elif not stations.empty and column in stations.columns:
            values = stations.set_index("site")[column]
        else:
            snap = source.snapshot("pwv" if column.startswith("pwv") else "ztd")
            values = (snap.set_index("site")["value"] if not snap.empty
                      else pd.Series(dtype=float))
        if values.empty:
            return pd.DataFrame()
        table = geo.copy()
        table[column] = table["site"].map(values)
        if not stations.empty and "ztd_rmse" in stations.columns:
            table["ztd_rmse"] = table["site"].map(stations.set_index("site")["ztd_rmse"])
        return table.dropna(subset=[column])

    def _apply_map(self) -> None:
        if self._map is None:
            return
        key = self._map_key()
        column, label, unit = self._map_column()
        frame = self._map_frame(key, column)
        if frame.empty:
            self._map.clear()
            self._map_note.setText("当前数据源缺少可用的站点数据")
            return
        self._map.set_sites(
            frame, column, label=label, unit=unit,
            mode="heat" if self._map_layer.currentIndex() == 1 else "points",
            weight_column="ztd_rmse", point_size=self._map_size.value(),
            # 「全国」交给数据自适应（数据只覆盖局部时不会缩成一个小点），
            # 「京津冀」则锁定固定范围。
            region="BTH" if self._map_region.currentIndex() == 1 else None)
        self._map.set_basemap(self._map_basemap.isChecked())
        self._map_note.setText(
            f"{self._ds.label(key)} · {label} · {len(frame)} 个站点")

    # ------------------------------------------------------ 数据刷新 --
    def _reload(self) -> None:
        self._pending_reload = False
        self.ctx.log("正在重新扫描结果目录…")
        self._ds = data.refresh(self.ctx.root)
        available = list(self._ds.keys())
        self._keys = [k for k in self._keys if k in available] or available

        self._site_combo.blockSignals(True)
        self._site_combo.clear()
        self._site_combo.addItems(list(self._ds.sites()))
        sites = self._ds.sites()
        self._site_combo.setCurrentText(self._site if self._site in sites
                                        else self._default_site())
        self._site_combo.blockSignals(False)
        self._site = self._site_combo.currentText()

        for key, box in self._source_boxes.items():
            box.blockSignals(True)
            box.setChecked(key in self._keys)
            box.setEnabled(self._ds.has(key))
            box.blockSignals(False)
        self._rebuild_window_items()
        self._sync_origin()
        self._invalidate()

    def _sync_origin(self) -> None:
        origin = self._ds.origin()
        text = self._ds.origin_text()
        self._origin_chip.setText(text)
        self._origin_chip.setProperty("demo", "true" if origin == "demo" else "false")
        self._origin_chip.style().unpolish(self._origin_chip)
        self._origin_chip.style().polish(self._origin_chip)
        self.set_status(text, "warn" if origin == "demo" else "ok")
        self._rail.set_foot("数据来源\n" + text)

        overview = self._ds.overview()
        keys = self._keys or list(data.SOURCE_ORDER)
        source = self._ds.get(keys[0])
        root = str(getattr(source, "root", "-"))
        self._info_rows[0].set_value(root)
        window = self._ds.common_window(self._keys)
        self._info_rows[1].set_value(
            f"{_stamp(window[0])} → {_stamp(window[1])}" if window is not None
            else "各源覆盖不一致")
        self._info_rows[2].set_value(
            f"{overview.get('sites', 0)} 站 · {overview.get('steps', 0)} 时次")

    def on_show(self) -> None:
        if self._ds is None or self._pending_reload:
            self._reload()

    # ---------------------------------------------------------- 动作 --
    def _reset_view(self) -> None:
        if self._view == "overlay":
            self._overlay.reset_view()
        elif self._view == "map" and self._map is not None:
            self._map.reset_view()
        elif self._view == "facets":
            self._invalidate()

    def _export_view(self) -> None:
        target = None
        if self._view == "overlay":
            target = self._overlay.export_png(self)
        elif self._view == "table":
            target = self._table.export_csv(self)
        elif self._view == "map" and self._map is not None:
            target = self._map.export_png(self)
        if target:
            self.ctx.log(f"已导出: {target}")

    # ---------------------------------------------------------- 主题 --
    def refresh_theme(self) -> None:
        super().refresh_theme()
        if getattr(self, "_legend", None) is not None:
            self._legend.set_items([(self._ds.label(key), icons.source_color(key))
                                    for key in data.SOURCE_ORDER])
        for chart in getattr(self, "_charts", []):
            if chart is not None and hasattr(chart, "refresh_theme"):
                chart.refresh_theme()
        if getattr(self, "_rail", None) is not None:
            self._rail.refresh_theme()
        if getattr(self, "_stack", None) is not None:
            self._dirty = {item[0] for item in VIEWS}
            self._dirty.discard(self._view)
            self._apply_view(self._view)


# ------------------------------------------------------------ 小工具 --
FIELD_LABELS = {"ztd_rmse": "ZTD RMSE", "ztd_bias": "ZTD Bias",
                "pwv_rmse": "PWV RMSE", "pwv_bias": "PWV Bias"}


def _stamp(value) -> str:
    return pd.Timestamp(value).strftime("%m-%d %H:%M")


def _window_of(ds, key: str) -> str:
    source = ds.get(key)
    return source.window_text() if source is not None else "未知"


def _num(value):
    """数值 -> float；缺失 / NaN / inf 一律返回 None（表格显示为 —）。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _mae(rmse, bias):
    """由 RMSE 与 Bias 反推 MAE（高斯误差假设下的近似值）。"""
    if rmse is None or bias is None:
        return None
    inner = rmse * rmse - bias * bias
    return data.GAUSS_MAE * float(np.sqrt(inner)) if inner > 0 else None


def _normalize(values, direction: str = "low") -> list[float]:
    array = np.array([np.nan if value is None else value for value in values],
                     dtype=float)
    if not np.isfinite(array).any():
        return [0.0] * len(values)
    low, high = float(np.nanmin(array)), float(np.nanmax(array))
    if high - low < 1e-12:
        return [1.0 if np.isfinite(value) else 0.0 for value in array]
    ratio = (array - low) / (high - low)
    scores = 1.0 - ratio if direction == "low" else ratio
    return [float(score) if np.isfinite(score) else 0.0 for score in scores]


def _cell(value, pattern: str) -> str:
    """把数值按 pattern 格式化成口径表里的一格（缺失显示 —）。"""
    return "—" if value is None else pattern.format(float(value))


def _measured_line(rows) -> str:
    """雷达图图注里的实测值明细：采样间隔 · 时次数 · 站点覆盖。"""
    parts = []
    for _, item in rows:
        if item is None:
            continue
        parts.append(f"{item.label} {item.interval_hours:g}h{item.interval_text()}"
                     f"·{item.steps}次·{item.sites}/{item.network}站")
    if not parts:
        return ""
    return "实测值（采样间隔 · 时次数 · 站点覆盖）　" + " ｜ ".join(parts)


def _station_row(source, site: str) -> dict:
    if source is None:
        return {}
    frame = source.stations()
    if frame.empty or "site" not in frame.columns:
        return {}
    hit = frame[frame["site"].astype(str) == str(site)]
    if hit.empty:
        return {}
    record = hit.iloc[0]
    return {name: _num(record.get(name))
            for name in ("ztd_rmse", "ztd_bias", "pwv_rmse", "pwv_bias", "n")}


__all__ = ["VisualizePage"]
