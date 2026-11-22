"""图形化工作台的绘图控件（pyqtgraph + 纯 QPainter）。

这一层只负责"把数据画出来"，不碰业务、不碰文件扫描：

  LinePlot      多序列曲线（时间轴 / 图例开关 / 十字准线 / 框选缩放 / 导出）
  FacetGrid     分面小倍数图（行=源、列=变量，X 轴与十字准线全局联动）
  RadarChart    极坐标雷达（源对比 / 站点对比）
  HeatTable     按数值热度着色的表格（可排序、可导出 CSV）
  TimelineBar   数据源时间覆盖条（下载页预览）
  ProgressRing  工作流进度环（首页仪表盘）
  Sparkline     极简趋势线（指标卡）

所有配色都来自 :func:`metcom.gui.theme.chart_style`，与界面同源取色，
换肤时图表一起换，不会"画布脱节"。
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPolygonF)
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QSizePolicy, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from metcom.gui import theme

_PG = None


def pg():
    """延迟导入 pyqtgraph（首次调用约 0.3s，避免拖慢启动画面）。"""
    global _PG
    if _PG is None:
        import pyqtgraph

        # 抗锯齿、关掉 OpenGL（部分集显驱动下反而更卡），背景由各画布自己管
        pyqtgraph.setConfigOptions(antialias=True, useOpenGL=False)
        _PG = pyqtgraph
    return _PG


def style_for(mode_name: str | None = None) -> dict:
    """当前（或指定）主题下的图表配色。"""
    return theme.chart_style(mode_name)


def _seconds(values) -> np.ndarray:
    """datetime64 / Timestamp 序列 -> pyqtgraph 用的浮点秒。"""
    array = np.asarray(values)
    if array.dtype.kind == "M":
        return array.astype("datetime64[ns]").astype("int64") / 1e9
    if array.dtype.kind in "OUS":
        import pandas as pd

        try:
            stamps = pd.to_datetime(pd.Series(np.ravel(array)), errors="coerce")
            nanos = stamps.to_numpy(dtype="datetime64[ns]").astype("int64")
            return nanos / 1e9
        except Exception:
            return array.astype(float)
    return array.astype(float)


def _stamp(value: float, with_time: bool = True) -> str:
    """浮点秒 -> 可读时间串。"""
    import pandas as pd

    moment = pd.Timestamp(value, unit="s")
    return f"{moment:%Y-%m-%d %H:%M}" if with_time else f"{moment:%Y-%m-%d}"


# --------------------------------------------------------------- 图例条 --
class LegendBar(QWidget):
    """可点击的图例：单击隐藏/显示对应曲线（联动绘图区与标签淡出）。"""

    toggled = Signal(str, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(2, 0, 2, 0)
        self._row.setSpacing(6)
        self._row.addStretch(1)
        self._chips: dict[str, tuple[QLabel, str]] = {}

    def set_items(self, items: list[tuple[str, str]]) -> None:
        """items = [(名称, 颜色)]，全部重建。"""
        while self._row.count() > 1:
            item = self._row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._chips = {}
        for name, color in items:
            label = QLabel(f" {name} ")
            label.setCursor(Qt.CursorShape.PointingHandCursor)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet(
                f"color: {color}; border: 1px solid {color}; border-radius: 9px;"
                " padding: 1px 8px; font-size: 11px;")
            label.mousePressEvent = (  # type: ignore[method-assign]
                lambda _event, key=name: self._toggle(key))
            self._row.insertWidget(self._row.count() - 1, label)
            self._chips[name] = (label, color)

    def hidden(self) -> set[str]:
        """当前被点掉的图例名（主题刷新重建后用于还原隐藏状态）。"""
        return {name for name, (label, _) in self._chips.items()
                if "dashed" in label.styleSheet()}

    def set_visible(self, name: str, visible: bool) -> None:
        chip = self._chips.get(name)
        if chip is None:
            return
        label, color = chip
        if visible:
            label.setStyleSheet(
                f"color: {color}; border: 1px solid {color}; border-radius: 9px;"
                " padding: 1px 8px; font-size: 11px;")
        else:
            label.setStyleSheet(
                "color: rgba(140, 160, 190, 0.75); border: 1px dashed"
                " rgba(140, 160, 190, 0.65); border-radius: 9px;"
                " padding: 1px 8px; font-size: 11px;")

    def _toggle(self, name: str) -> None:
        chip = self._chips.get(name)
        if chip is None:
            return
        visible = "dashed" in chip[0].styleSheet()
        self.set_visible(name, visible)
        self.toggled.emit(name, visible)


# ------------------------------------------------------------ 曲线画布 --
class LinePlot(QWidget):
    """多序列曲线画布。

    默认交互：滚轮缩放、中键平移、双击复位；``box_zoom`` 打开后左键拖拽
    即为框选放大（再点一次回到平移）。
    """

    station_clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None, *,
                 compact: bool = False) -> None:
        super().__init__(parent)
        drawing = pg()
        palette = theme.palette()
        self._compact = compact
        self._series: list[dict] = []
        self._curves: dict[str, object] = {}
        self._range: tuple[float, float] | None = None
        self._box_zoom = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self._plot = drawing.PlotWidget(
            axisItems={"bottom": drawing.DateAxisItem(orientation="bottom")})
        self._plot.setBackground(palette.plot_bg)
        item = self._plot.getPlotItem()
        item.hideButtons()
        item.setMenuEnabled(False)
        item.setClipToView(True)
        item.setDownsampling(auto=True, mode="peak")
        item.showGrid(x=True, y=True, alpha=0.16)
        for side in ("left", "bottom"):
            axis = item.getAxis(side)
            axis.setPen(drawing.mkPen(palette.grid_line, width=1))
            axis.setTextPen(drawing.mkPen(palette.axis_text))
            axis.setTickFont(QFont(theme.pick_family(theme.UI_FONT_CANDIDATES),
                                   8 if compact else 9))
        if compact:
            item.getAxis("left").setWidth(46)
            self._plot.setMinimumHeight(180)
        root.addWidget(self._plot, 1)

        self._view = item.getViewBox()
        self._cross_v = drawing.InfiniteLine(angle=90, movable=False,
                                             pen=drawing.mkPen(palette.crosshair, width=1,
                                                               style=Qt.PenStyle.DashLine))
        self._cross_h = drawing.InfiniteLine(angle=0, movable=False,
                                             pen=drawing.mkPen(palette.crosshair, width=1,
                                                               style=Qt.PenStyle.DashLine))
        for line in (self._cross_v, self._cross_h):
            line.setVisible(False)
            item.addItem(line, ignoreBounds=True)

        self._readout = QLabel("")
        self._readout.setObjectName("Mono")
        self._readout.setStyleSheet(
            f"color: {palette.text_muted}; font-size: 11px; padding: 0 4px;")
        self._readout.setVisible(False)

        self._empty = QLabel("暂无数据")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet(
            f"color: {palette.text_faint}; font-size: 13px;")
        root.addWidget(self._empty)
        self._empty.setVisible(False)

        self._legend = LegendBar()
        self._legend.toggled.connect(self._on_toggle)
        root.addWidget(self._legend)

        self._plot.scene().sigMouseMoved.connect(self._on_move)
        self._plot.mouseDoubleClickEvent = self._on_double  # type: ignore[method-assign]

    # ------------------------------------------------------------ 数据 --
    def set_series(self, series: list[dict], *, ylabel: str = "",
                   xlabel: str = "时间", keep_range: bool = False) -> None:
        """series 元素形如 ``{"name", "color", "x", "y", "dash"?}``。"""
        drawing = pg()
        item = self._plot.getPlotItem()
        for curve in self._curves.values():
            item.removeItem(curve)
        self._curves = {}
        self._series = list(series)
        shown = [(entry["name"], entry["color"]) for entry in series]
        self._legend.set_items(shown)
        self._legend.setVisible(bool(shown) and not self._compact)

        for entry in series:
            name = entry["name"]
            color = entry["color"]
            pen = drawing.mkPen(color, width=1.9,
                                style=Qt.PenStyle.DashLine if entry.get("dash")
                                else Qt.PenStyle.SolidLine)
            curve = item.plot(_seconds(entry["x"]), np.asarray(entry["y"], dtype=float),
                              pen=pen, connect="finite", name=name)
            curve.setZValue(10)
            self._curves[name] = curve

        label_kwargs = {"color": theme.palette().axis_text, "font-size": "10pt"}
        item.setLabel("left", ylabel or None, **label_kwargs)
        item.setLabel("bottom", xlabel or None, **label_kwargs)
        if not keep_range or self._range is None:
            self._view.enableAutoRange()
        self._empty.setVisible(not any(len(e["y"]) for e in series))
        self._readout.setVisible(self._readout.isVisible() and bool(series))

    def clear(self) -> None:
        self.set_series([], ylabel="")

    # ------------------------------------------------------------ 交互 --
    def box_zoom(self) -> bool:
        return self._box_zoom

    def set_box_zoom(self, enabled: bool) -> None:
        drawing = pg()
        self._box_zoom = bool(enabled)
        self._view.setMouseMode(drawing.ViewBox.RectMode if self._box_zoom
                                else drawing.ViewBox.PanMode)

    def reset_view(self) -> None:
        for curve in self._curves.values():
            curve.setVisible(True)
        for name, _ in self._legend._chips.items():
            self._legend.set_visible(name, True)
        self._view.enableAutoRange()
        self._view.autoRange()

    def fit_to(self, start, end) -> None:
        """把视窗固定到给定时间区间（时间窗切换用）。"""
        self._range = (_seconds([start])[0], _seconds([end])[0])
        self._view.setXRange(self._range[0], self._range[1], padding=0.01)
        self._view.enableAutoRange(axis="y")

    def export_png(self, parent: QWidget | None = None) -> str | None:
        """把绘图区导出成 PNG，返回保存路径。"""
        from pyqtgraph.exporters import ImageExporter

        target, _ = QFileDialog.getSaveFileName(
            parent or self, "导出图片", "metcom_chart.png", "PNG 图片 (*.png)")
        if not target:
            return None
        exporter = ImageExporter(self._plot.getPlotItem())
        exporter.parameters()["width"] = 1800
        exporter.export(str(target))
        return target

    # ------------------------------------------------------------ 事件 --
    def _on_toggle(self, name: str, visible: bool) -> None:
        curve = self._curves.get(name)
        if curve is not None:
            curve.setVisible(visible)

    def _on_double(self, event) -> None:
        self._view.enableAutoRange()
        self._view.autoRange()
        event.accept()

    def _on_move(self, position) -> None:
        item = self._plot.getPlotItem()
        if not item.sceneBoundingRect().contains(position) or not self._series:
            self._cross_v.setVisible(False)
            self._cross_h.setVisible(False)
            self._readout.setVisible(False)
            return
        point = self._view.mapSceneToView(position)
        self._cross_v.setPos(point.x())
        self._cross_v.setVisible(True)
        parts = [_stamp(point.x())]
        nearest_y = None
        for entry in self._series:
            xs = _seconds(entry["x"])
            ys = np.asarray(entry["y"], dtype=float)
            if not len(xs):
                continue
            index = int(np.argmin(np.abs(xs - point.x())))
            value = ys[index]
            if not np.isfinite(value):
                continue
            parts.append(f"{entry['short'] if 'short' in entry else entry['name']}"
                         f"={value:.2f}")
            if nearest_y is None:
                nearest_y = value
        self._readout.setText("   ".join(parts))
        if nearest_y is not None:
            self._cross_h.setPos(nearest_y)
            self._cross_h.setVisible(True)
        self._readout.setVisible(True)

    def refresh_theme(self) -> None:
        """换肤后重建画布配色（曲线颜色由调用方重新 set_series 决定）。"""
        palette = theme.palette()
        drawing = pg()
        self._plot.setBackground(palette.plot_bg)
        item = self._plot.getPlotItem()
        for side in ("left", "bottom"):
            axis = item.getAxis(side)
            axis.setPen(drawing.mkPen(palette.grid_line, width=1))
            axis.setTextPen(drawing.mkPen(palette.axis_text))
        self._cross_v.setPen(drawing.mkPen(palette.crosshair, width=1,
                                           style=Qt.PenStyle.DashLine))
        self._cross_h.setPen(drawing.mkPen(palette.crosshair, width=1,
                                           style=Qt.PenStyle.DashLine))
        self._readout.setStyleSheet(
            f"color: {palette.text_muted}; font-size: 11px; padding: 0 4px;")
        self._empty.setStyleSheet(
            f"color: {palette.text_faint}; font-size: 13px;")
        # 曲线颜色随主题切换（浅色主题改用浅色系五源色）
        colors = source_colors([entry["key"] for entry in self._series
                                if entry.get("key")])
        if colors:
            for entry in self._series:
                color = colors.get(entry.get("key"))
                if color is None:
                    continue
                entry["color"] = color
                curve = self._curves.get(entry["name"])
                if curve is not None:
                    curve.setPen(drawing.mkPen(
                        color, width=1.9,
                        style=Qt.PenStyle.DashLine if entry.get("dash")
                        else Qt.PenStyle.SolidLine))
            hidden = self._legend.hidden()
            self._legend.set_items([(entry["name"], entry["color"])
                                    for entry in self._series])
            for name in hidden:
                self._legend.set_visible(name, False)


def source_colors(keys) -> dict[str, str]:
    """按数据源 key 取固定专属色（与图例、地图、表格保持一致）。"""
    from metcom.gui import data

    style = style_for()
    palette = style["sources"]
    return {key: palette[i % len(palette)]
            for i, key in enumerate(data.SOURCE_ORDER)
            if key in set(keys)}


def station_series(ds, keys, site: str, var: str = "ztd", window=None) -> list[dict]:
    """某站点、某变量的五源时序（叠加曲线页签的数据) 。"""
    import pandas as pd

    colors = source_colors(keys)
    series: list[dict] = []
    for key in keys:
        source = ds.get(key)
        if source is None:
            continue
        x, y = source.series(site, var)
        if window is not None and len(x):
            start, end = (pd.Timestamp(window[0]), pd.Timestamp(window[1]))
            mask = (x >= np.datetime64(start)) & (x <= np.datetime64(end))
            x, y = x[mask], y[mask]
        series.append({"name": ds.label(key), "short": ds.label(key),
                       "color": colors[key], "x": x, "y": y, "key": key})
    return series


def metric_series(ds, keys, var: str = "ztd", field: str = "rmse") -> list[dict]:
    """五源指标随时间变化的曲线（Bias / RMSE 两条可选）。"""
    import pandas as pd

    colors = source_colors(keys)
    series: list[dict] = []
    for key in keys:
        source = ds.get(key)
        if source is None:
            continue
        frame = source.times()
        column = f"{var}_{field}"
        if frame.empty or column not in frame.columns:
            continue
        part = frame.dropna(subset=[column])
        series.append({"name": ds.label(key), "short": ds.label(key),
                       "color": colors[key],
                       "x": part["time"].to_numpy(),
                       "y": part[column].to_numpy(dtype=float),
                       "key": key})
    return series


# ------------------------------------------------------------ 分面小倍数 --
class FacetGrid(QWidget):
    """分面小倍数图：行=数据源、列=变量（或反向），共享 X 轴与十字准线。

    一眼能读出"哪个源在哪个时刻偏得最多"，比叠五条线更不容易看花。
    """

    def __init__(self, parent: QWidget | None = None, *,
                 var_first: bool = False) -> None:
        super().__init__(parent)
        self._var_first = var_first
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(6)
        self._plots: list[tuple[object, int, int]] = []
        self._vlines: list[object] = []
        self._hline: object | None = None
        self._built = False

    def _build(self, keys: list[str], variables: list[tuple[str, str]]) -> None:
        drawing = pg()
        palette = theme.palette()
        labels = [ds_label for _, ds_label in variables]
        from metcom.gui import data

        # 网格形状：默认 源×变量，切换后 变量×源
        if not self._var_first:
            row_labels = [data.source_label(k) for k in keys]
            col_labels = labels
            cells = [(k, v) for k in keys for v in variables]
        else:
            row_labels = labels
            col_labels = [data.source_label(k) for k in keys]
            cells = [(k, v) for v in variables for k in keys]
        self._shape = (len(row_labels), len(col_labels))

        for index, label in enumerate(col_labels):
            head = QLabel(label)
            head.setAlignment(Qt.AlignmentFlag.AlignCenter)
            head.setStyleSheet(
                f"color: {palette.text_muted}; font-size: 11px; font-weight: 600;")
            self._grid.addWidget(head, 0, index + 1)

        for row, (key, (var, _)) in enumerate(cells):
            grid_row = row // len(col_labels) + 1
            grid_col = row % len(col_labels) + 1
            if grid_col == 1:
                name = QLabel(row_labels[grid_row - 1])
                name.setStyleSheet(
                    f"color: {palette.text}; font-size: 11px; font-weight: 600;")
                name.setFixedWidth(78)
                name.setAlignment(Qt.AlignmentFlag.AlignRight |
                                  Qt.AlignmentFlag.AlignVCenter)
                self._grid.addWidget(name, grid_row, 0)
            widget = drawing.PlotWidget(
                axisItems={"bottom": drawing.DateAxisItem(orientation="bottom")})
            widget.setBackground(palette.plot_bg)
            widget.setMinimumHeight(150)
            item = widget.getPlotItem()
            item.hideButtons()
            item.setMenuEnabled(False)
            item.setMouseEnabled(x=True, y=True)
            item.showGrid(x=True, y=True, alpha=0.14)
            for side in ("left", "bottom"):
                axis = item.getAxis(side)
                axis.setPen(drawing.mkPen(palette.grid_line, width=1))
                axis.setTextPen(drawing.mkPen(palette.axis_text))
                axis.setTickFont(QFont(theme.pick_family(theme.UI_FONT_CANDIDATES), 8))
            if grid_row < self._shape[0]:
                item.getAxis("bottom").setStyle(showValues=False)
            line = drawing.InfiniteLine(
                angle=90, movable=False,
                pen=drawing.mkPen(palette.crosshair, width=1,
                                  style=Qt.PenStyle.DashLine))
            line.setVisible(False)
            item.addItem(line, ignoreBounds=True)
            hline = drawing.InfiniteLine(
                angle=0, movable=False,
                pen=drawing.mkPen(palette.crosshair, width=1,
                                  style=Qt.PenStyle.DotLine))
            hline.setVisible(False)
            item.addItem(hline, ignoreBounds=True)
            widget.scene().sigMouseMoved.connect(
                lambda position, w=widget, h=hline: self._on_move(w, h, position))
            self._plots.append((widget, key, var))
            self._vlines.append(line)
            self._grid.addWidget(widget, grid_row, grid_col)
        if self._plots:
            # 共享 X 轴：任一格缩放 / 平移，其余各格同步，横向可比。
            reference = self._plots[0][0].getPlotItem()
            for widget, _, _ in self._plots[1:]:
                widget.getPlotItem().setXLink(reference)
        for row in range(1, self._shape[0] + 1):
            self._grid.setRowStretch(row, 1)
        self._built = True

    def set_data(self, ds, keys, site: str, var: str = "ztd",
                 variables=None, window=None) -> None:
        variables = list(variables or (("ztd", "ZTD"), ("pwv", "PWV")))
        if not self._built:
            self._build(list(keys), variables)
        else:
            for widget, _, _ in self._plots:
                widget.getPlotItem().clear()
        colors = source_colors(keys)
        for widget, key, variable in self._plots:
            source = ds.get(key)
            if source is None:
                continue
            x, y = source.series(site, variable)
            if window is not None and len(x):
                import pandas as pd

                start, end = pd.Timestamp(window[0]), pd.Timestamp(window[1])
                mask = (x >= np.datetime64(start)) & (x <= np.datetime64(end))
                x, y = x[mask], y[mask]
            widget.getPlotItem().plot(_seconds(x), np.asarray(y, dtype=float),
                                      pen=pg().mkPen(colors[key], width=1.6),
                                      connect="finite")
            widget.getPlotItem().setTitle(
                None, color=theme.palette().text_muted)

    def refresh_theme(self) -> None:
        palette = theme.palette()
        drawing = pg()
        for widget, _, _ in self._plots:
            widget.setBackground(palette.plot_bg)
            item = widget.getPlotItem()
            for side in ("left", "bottom"):
                axis = item.getAxis(side)
                axis.setPen(drawing.mkPen(palette.grid_line, width=1))
                axis.setTextPen(drawing.mkPen(palette.axis_text))
        for line in self._vlines:
            line.setPen(drawing.mkPen(palette.crosshair, width=1,
                                      style=Qt.PenStyle.DashLine))
        colors = source_colors([key for _, key, _ in self._plots])
        for widget, key, _ in self._plots:
            color = colors.get(key)
            if color is None:
                continue
            for curve in widget.getPlotItem().listDataItems():
                curve.setPen(drawing.mkPen(color, width=1.6))

    def _on_move(self, widget, hline, position) -> None:
        item = widget.getPlotItem()
        if not item.sceneBoundingRect().contains(position):
            return
        point = item.getViewBox().mapSceneToView(position)
        for line in self._vlines:
            line.setPos(point.x())
            line.setVisible(True)
        hline.setPos(point.y())
        hline.setVisible(True)


# ---------------------------------------------------------------- 雷达图 --
class RadarChart(QWidget):
    """极坐标雷达图：多维归一化对比（源之间 / 站点之间）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._axes: list[str] = []
        self._series: list[dict] = []
        self._note = ""
        self.setMinimumHeight(320)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setToolTip("数值已归一化到 0~1，越靠外越好；悬停可查看归一化前的原始值")

    def set_data(self, axes: list[str], series: list[dict],
                 note: str = "") -> None:
        """写入维度与系列；``note`` 是统计口径说明，一并进悬停提示。"""
        self._axes = list(axes)
        self._series = [dict(item) for item in series]
        self._note = str(note or "")
        self.setToolTip(self._tooltip())
        self.update()

    def _tooltip(self) -> str:
        """悬停提示：列出归一化之前的原始值，让图上形状对得上口径。

        每个系列可以在 ``raw`` 里带一份与 ``values`` 等长的字符串列表，
        这里原样列出来（也用于区分实测值与规格回退值）。
        """
        head = "各维已归一化到 0~1，越靠外越好。"
        names = [str(item.get("name", "")) for item in self._series]
        if not self._axes or not names:
            return head
        rows: list[tuple[str, list[str]]] = []
        for index, axis in enumerate(self._axes):
            cells = []
            for item in self._series:
                raw = list(item.get("raw") or [])
                cells.append(str(raw[index]) if index < len(raw) else "—")
            rows.append((str(axis), cells))
        html = ["<table cellspacing='0' cellpadding='3'>",
                "<tr><th align='left'>维度</th>"
                + "".join(f"<th align='left'>{name}</th>" for name in names)
                + "</tr>"]
        for axis, cells in rows:
            html.append(f"<tr><td>{axis}</td>"
                        + "".join(f"<td align='right'>{cell}</td>" for cell in cells)
                        + "</tr>")
        html.append("</table>")
        for line in self._note.splitlines():
            html.append("<p>" + line + "</p>")
        return head + "".join(html)

    def refresh_theme(self) -> None:
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        palette = theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        try:
            if len(self._axes) < 3:
                painter.setPen(QColor(palette.text_faint))
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                                 "维度不足，无法绘制雷达图")
                return
            width, height = self.width(), self.height()
            label_room = 76
            radius = max(40.0, min(width / 2.0 - label_room,
                                   height / 2.0 - label_room * 0.6))
            center = QPointF(width / 2.0, height / 2.0 + 6)
            count = len(self._axes)

            def point_at(index: int, ratio: float) -> QPointF:
                angle = -np.pi / 2.0 + 2.0 * np.pi * index / count
                return QPointF(center.x() + np.cos(angle) * radius * ratio,
                               center.y() + np.sin(angle) * radius * ratio)

            # 网格环 + 辐射轴
            for step in (0.25, 0.5, 0.75, 1.0):
                path = QPainterPath()
                for index in range(count):
                    spot = point_at(index, step)
                    path.moveTo(spot) if index == 0 else path.lineTo(spot)
                path.closeSubpath()
                pen = QPen(QColor(palette.grid_line))
                pen.setWidthF(1.0)
                pen.setStyle(Qt.PenStyle.SolidLine if step == 1.0
                             else Qt.PenStyle.DotLine)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(path)
            painter.setPen(QPen(QColor(palette.grid_line), 1.0))
            for index in range(count):
                painter.drawLine(center, point_at(index, 1.0))

            # 数据多边形
            for entry in self._series:
                values = list(entry.get("values") or [])
                if len(values) != count:
                    continue
                polygon = QPolygonF([point_at(i, float(max(0.0, min(1.0, v))))
                                     for i, v in enumerate(values)])
                color = QColor(entry.get("color") or palette.accent)
                fill = QColor(color)
                fill.setAlpha(38)
                painter.setBrush(QBrush(fill))
                pen = QPen(color)
                pen.setWidthF(2.0)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.drawPolygon(polygon)
                for i, value in enumerate(values):
                    spot = point_at(i, float(max(0.0, min(1.0, value))))
                    painter.setBrush(QBrush(QColor(palette.plot_bg)))
                    painter.setPen(QPen(color, 1.6))
                    painter.drawEllipse(spot, 3.1, 3.1)

            # 轴标签
            font = QFont(theme.pick_family(theme.UI_FONT_CANDIDATES), 9)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(palette.text))
            for index, label in enumerate(self._axes):
                spot = point_at(index, 1.0)
                dx, dy = spot.x() - center.x(), spot.y() - center.y()
                box = QRectF(spot.x() - 62, spot.y() - 12, 124, 24)
                if abs(dx) > 6:
                    align = (Qt.AlignmentFlag.AlignRight if dx > 0
                             else Qt.AlignmentFlag.AlignLeft)
                else:
                    align = Qt.AlignmentFlag.AlignHCenter
                if abs(dy) > 6:
                    align |= (Qt.AlignmentFlag.AlignTop if dy > 0
                              else Qt.AlignmentFlag.AlignBottom)
                else:
                    align |= Qt.AlignmentFlag.AlignVCenter
                painter.drawText(box, align, label)

            # 图例
            legend_font = QFont(theme.pick_family(theme.UI_FONT_CANDIDATES), 9)
            painter.setFont(legend_font)
            x = 10.0
            for entry in self._series:
                color = QColor(entry.get("color") or palette.accent)
                painter.setPen(QPen(color, 2.0))
                painter.drawLine(QPointF(x, 12), QPointF(x + 18, 12))
                painter.setPen(QColor(palette.text_muted))
                text = str(entry.get("name", ""))
                painter.drawText(QPointF(x + 24, 16), text)
                x += 42 + painter.fontMetrics().horizontalAdvance(text)
        finally:
            painter.end()


def _blend(base: QColor, target: QColor, ratio: float) -> QColor:
    ratio = max(0.0, min(1.0, float(ratio)))
    return QColor(int(base.red() + (target.red() - base.red()) * ratio),
                  int(base.green() + (target.green() - base.green()) * ratio),
                  int(base.blue() + (target.blue() - base.blue()) * ratio))


def heat_color(ratio: float, direction: str = "low") -> QColor:
    """把 0~1 的归一化数值映射成底色（越小越好 / 越大越好 / 中性）。"""
    palette = theme.palette()
    base = QColor(palette.plot_panel)
    if direction == "none":
        return _blend(base, QColor(palette.accent), 0.10 + 0.34 * ratio)
    good, bad = QColor(palette.success), QColor(palette.danger)
    target = _blend(good, bad, ratio) if direction == "low" else _blend(bad, good, ratio)
    return _blend(base, target, 0.14 + 0.56 * ratio)


class _NumberItem(QTableWidgetItem):
    """按数值而非字符串排序的单元格。"""

    def __lt__(self, other) -> bool:  # noqa: D105 - Qt 运算符
        left = self.data(Qt.ItemDataRole.UserRole)
        right = other.data(Qt.ItemDataRole.UserRole)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return float(left) < float(right)
        return super().__lt__(other)


class HeatTable(QTableWidget):
    """指标对比表：单元格按数值热度着色，可排序、可导出 CSV。

    ``columns`` 每项形如 ``{"title", "key", "dir"("low"/"high"/"none"), "fmt"}``；
    第 0 列固定是文本标签列（站点名 / 数据源名）。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._columns: list[dict] = []
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSortingEnabled(True)
        self.setShowGrid(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(26)
        self.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setHighlightSections(False)
        self.setMinimumHeight(240)

    def set_columns(self, columns: list[dict], label_title: str = "名称") -> None:
        self._columns = list(columns)
        self.setColumnCount(len(columns) + 1)
        self.setHorizontalHeaderLabels([label_title] + [c["title"] for c in columns])

    def set_rows(self, rows: list[dict], sort_column: int | None = None) -> None:
        """rows 每项形如 ``{"label", "values": [v0, v1, ...]}``。"""
        self.setSortingEnabled(False)
        self.setRowCount(len(rows))
        for index, row in enumerate(rows):
            label = QTableWidgetItem(str(row.get("label", "")))
            label.setData(Qt.ItemDataRole.UserRole, str(row.get("label", "")))
            self.setItem(index, 0, label)
            for column, spec in enumerate(self._columns):
                values = row.get("values") or [None] * len(self._columns)
                value = values[column]
                if value is None:
                    text = "—"
                else:
                    formatter = spec.get("text")
                    text = (formatter(value) if callable(formatter)
                            else spec.get("fmt", "{:.2f}").format(value))
                item = _NumberItem(text)
                item.setData(Qt.ItemDataRole.UserRole,
                             float(value) if isinstance(value, (int, float)) else None)
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight |
                                      Qt.AlignmentFlag.AlignVCenter)
                item.setForeground(QBrush(QColor(theme.palette().text)))
                self.setItem(index, column + 1, item)
        self.apply_heat()
        self.setSortingEnabled(True)
        if sort_column is not None:
            self.sortItems(sort_column, Qt.SortOrder.AscendingOrder)

    def apply_heat(self) -> None:
        """按每列的极值重新上色（排序后调用也安全）。"""
        for column, spec in enumerate(self._columns):
            values = []
            for row in range(self.rowCount()):
                item = self.item(row, column + 1)
                if item is None:
                    continue
                value = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(value, (int, float)):
                    values.append((row, float(value)))
            if not values:
                continue
            low = min(v for _, v in values)
            high = max(v for _, v in values)
            span = (high - low) or 1.0
            for row, value in values:
                item = self.item(row, column + 1)
                ratio = (value - low) / span
                item.setBackground(QBrush(heat_color(ratio, spec.get("dir", "low"))))

    def export_csv(self, parent: QWidget | None = None) -> str | None:
        target, _ = QFileDialog.getSaveFileName(
            parent or self, "导出表格", "metcom_metrics.csv", "CSV 表格 (*.csv)")
        if not target:
            return None
        with open(target, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow([self.horizontalHeaderItem(c).text()
                             for c in range(self.columnCount())])
            for row in range(self.rowCount()):
                writer.writerow([
                    (self.item(row, c).text() if self.item(row, c) else "")
                    for c in range(self.columnCount())])
        return target

    def refresh_theme(self) -> None:
        self.apply_heat()


# ------------------------------------------------------------ 时间覆盖条 --
class TimelineBar(QWidget):
    """数据源时间覆盖条：一行一个源，直观看出哪段有重合。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[dict] = []
        self.setMinimumHeight(96)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_data(self, rows: list[dict]) -> None:
        """rows 元素形如 ``{"label", "color", "start", "end"}``。"""
        self._rows = list(rows)
        self.setMinimumHeight(max(96, 26 * len(self._rows) + 34))
        self.update()

    def refresh_theme(self) -> None:
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        palette = theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            if not self._rows:
                return
            import pandas as pd

            starts = [pd.Timestamp(r["start"]) for r in self._rows]
            ends = [pd.Timestamp(r["end"]) for r in self._rows]
            low, high = min(starts), max(ends)
            span = max(1.0, (high - low).total_seconds())
            left, right = 92.0, float(self.width()) - 16.0
            usable = max(20.0, right - left)

            font = QFont(theme.pick_family(theme.UI_FONT_CANDIDATES), 9)
            painter.setFont(font)

            # 底轴
            painter.setPen(QPen(QColor(palette.border), 1.2))
            base_y = 22.0 + 26.0 * len(self._rows)
            painter.drawLine(QPointF(left, base_y), QPointF(right, base_y))
            painter.setPen(QColor(palette.axis_text))
            for ratio, moment in ((0.0, low), (0.5, low + (high - low) / 2), (1.0, high)):
                x = left + usable * ratio
                painter.drawLine(QPointF(x, base_y), QPointF(x, base_y + 5))
                align = (Qt.AlignmentFlag.AlignLeft if ratio == 0.0
                         else Qt.AlignmentFlag.AlignHCenter if ratio == 0.5
                         else Qt.AlignmentFlag.AlignRight)
                painter.drawText(QRectF(x - 70, base_y + 6, 140, 18), align,
                                 f"{moment:%Y-%m-%d %H}")

            for index, row in enumerate(self._rows):
                start, end = pd.Timestamp(row["start"]), pd.Timestamp(row["end"])
                y = 14.0 + 26.0 * index
                painter.setPen(QColor(palette.text))
                painter.drawText(QRectF(0, y, 86, 20),
                                 Qt.AlignmentFlag.AlignRight |
                                 Qt.AlignmentFlag.AlignVCenter,
                                 str(row["label"]))
                x0 = left + usable * ((start - low).total_seconds() / span)
                x1 = left + usable * ((end - low).total_seconds() / span)
                color = QColor(row.get("color") or palette.accent)
                gradient = QLinearGradient(QPointF(x0, y), QPointF(x1, y))
                gradient.setColorAt(0.0, color)
                end_color = QColor(color)
                end_color.setAlpha(150)
                gradient.setColorAt(1.0, end_color)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(gradient))
                rect = QRectF(x0, y + 3, max(3.0, x1 - x0), 14.0)
                painter.drawRoundedRect(rect, 7.0, 7.0)
        finally:
            painter.end()


# ---------------------------------------------------------------- 进度环 --
class ProgressRing(QWidget):
    """完成度圆环（首页工作流概览）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._done = 0
        self._total = 5
        self._caption = "工作流"
        self.setMinimumSize(150, 150)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def set_value(self, done: int, total: int, caption: str = "") -> None:
        self._done, self._total = int(done), max(1, int(total))
        if caption:
            self._caption = caption
        self.update()

    def refresh_theme(self) -> None:
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        palette = theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            side = min(self.width(), self.height()) - 16
            rect = QRectF((self.width() - side) / 2.0, (self.height() - side) / 2.0,
                          side, side)
            pen = QPen(QColor(palette.surface_alt), 10.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(rect, 0, 360 * 16)
            ratio = min(1.0, self._done / self._total)
            if ratio > 0:
                gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
                gradient.setColorAt(0.0, QColor(palette.accent))
                gradient.setColorAt(1.0, QColor(palette.accent_alt))
                pen = QPen(QBrush(gradient), 10.0)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.drawArc(rect, 90 * 16, -int(360 * 16 * ratio))
            painter.setPen(QColor(palette.text))
            value_font = QFont(theme.pick_family(theme.UI_FONT_CANDIDATES), 24)
            value_font.setBold(True)
            painter.setFont(value_font)
            painter.drawText(rect.adjusted(0, -10, 0, -10),
                             Qt.AlignmentFlag.AlignCenter,
                             f"{self._done}/{self._total}")
            painter.setPen(QColor(palette.text_muted))
            painter.setFont(QFont(theme.pick_family(theme.UI_FONT_CANDIDATES), 10))
            painter.drawText(rect.adjusted(0, 30, 0, 30),
                             Qt.AlignmentFlag.AlignCenter, self._caption)
        finally:
            painter.end()


class Sparkline(QWidget):
    """指标卡里的迷你趋势线（几十个采样点，无需交互）。"""

    def __init__(self, parent: QWidget | None = None, color: str | None = None) -> None:
        super().__init__(parent)
        self._values: list[float] = []
        self._color = color
        self.setFixedHeight(30)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_values(self, values, color: str | None = None) -> None:
        self._values = [float(v) for v in values if v == v]
        if color:
            self._color = color
        self.update()

    def refresh_theme(self) -> None:
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if len(self._values) < 2:
            return
        palette = theme.palette()
        color = QColor(self._color or palette.accent)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            low, high = min(self._values), max(self._values)
            span = (high - low) or 1.0
            step = self.width() / max(1, len(self._values) - 1)
            points = [QPointF(i * step,
                              self.height() - 3 - (v - low) / span * (self.height() - 6))
                      for i, v in enumerate(self._values)]
            path = QPainterPath()
            path.moveTo(points[0])
            for point in points[1:]:
                path.lineTo(point)
            fill = QColor(color)
            fill.setAlpha(46)
            area = QPainterPath(path)
            area.lineTo(QPointF(points[-1].x(), self.height()))
            area.lineTo(QPointF(0, self.height()))
            area.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(fill))
            painter.drawPath(area)
            pen = QPen(color, 1.8)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        finally:
            painter.end()


__all__ = ["pg", "style_for", "LegendBar", "LinePlot", "FacetGrid", "RadarChart",
           "HeatTable", "TimelineBar", "ProgressRing", "Sparkline",
           "source_colors", "station_series", "metric_series", "heat_color"]
