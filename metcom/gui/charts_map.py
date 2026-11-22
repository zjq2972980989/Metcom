"""地图画布：matplotlib + cartopy 真实底图。

曲线/散点走 pyqtgraph（见 :mod:`metcom.gui.charts`），地图则必须用
matplotlib + cartopy 才能画出真实省界与海岸线，两套引擎都从
:func:`metcom.gui.theme.chart_style` 取色，保证"界面换肤 -> 地图同步换肤"。

渲染采用"离屏渲染 + QPixmap 显示"而不是嵌入式 Qt 画布，原因有二：

1. 评估绘图模块 ``metcom.evaluation.plots`` 在导入时会把 matplotlib 后端
   强制切到 ``Agg``，嵌入式 Qt 画布在那种情况下会失联；离屏渲染不受影响。
2. 缩放/平移全部自己算（滚轮缩放、拖拽平移、双击复位），不依赖
   matplotlib 自带的导航工具栏，交互更贴合桌面工具的手感。

底图数据随仓库分发在 ``assets/naturalearth``，全程不需要联网。
"""

from __future__ import annotations

import io
import pickle
from pathlib import Path

import numpy as np

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from metcom.gui import theme

ASSETS = Path(__file__).resolve().parent / "assets" / "naturalearth"

#: 常用制图范围：全国 / 京津冀
REGIONS = {
    "CHN": [73.0, 138.0, 15.0, 53.0],
    "BTH": [113.0, 120.5, 35.5, 43.0],
}

_RUNTIME: dict = {"ready": False, "error": ""}
_LAND: dict | None = None


# ------------------------------------------------------------ 环境准备 --
def _bootstrap() -> bool:
    """锁定底图目录并导入 cartopy。

    注意 ``cartopy.config["data_dir"]`` 必须早于 ``import cartopy.feature``，
    否则 cartopy 会去默认缓存目录（乃至联网）找底图。
    """
    if _RUNTIME["ready"]:
        return True
    if _RUNTIME["error"]:
        return False
    try:
        import matplotlib

        matplotlib.use("Agg")
        import cartopy

        cartopy.config["data_dir"] = str(ASSETS)
        cartopy.config["pre_existing_data_dir"] = str(ASSETS)
        import cartopy.crs  # noqa: F401 - 预热导入
        import cartopy.feature  # noqa: F401 - 预热导入
        from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: F401
        from matplotlib.figure import Figure  # noqa: F401

        _RUNTIME["ready"] = True
        return True
    except Exception as exc:                      # pragma: no cover - 依赖缺失兜底
        _RUNTIME["error"] = f"{type(exc).__name__}: {exc}"
        return False


def available() -> bool:
    """地图引擎是否可用（供设置页自检调用）。"""
    return _bootstrap()


def unavailable_reason() -> str:
    _bootstrap()
    return _RUNTIME["error"]


# ------------------------------------------------------------ 底图几何 --
def land_data() -> dict:
    """简化后的陆地 / 海岸线 / 国界几何；首次调用约 1 秒，之后走缓存。"""
    global _LAND
    if _LAND is None:
        _LAND = _load_land()
    return _LAND


def _load_land() -> dict:
    cache = ASSETS / "_land_mask.pkl"
    if cache.is_file():
        try:
            with open(cache, "rb") as handle:
                return pickle.load(handle)
        except Exception:
            pass
    data = _build_land()
    if data:
        try:
            with open(cache, "wb") as handle:
                pickle.dump(data, handle, protocol=4)
        except Exception:
            pass
    return data


def _build_land() -> dict:
    """由本地 Natural Earth 50m 矢量现算陆地多边形（无网络依赖）。"""
    root = ASSETS / "shapefiles" / "natural_earth"
    try:
        import geopandas as gpd
        from shapely.ops import unary_union

        frame = gpd.read_file(
            root / "cultural" / "ne_50m_admin_1_states_provinces_lakes.shp")
        land = unary_union(list(frame.geometry)).simplify(0.02,
                                                          preserve_topology=True)
        china = unary_union(
            list(frame[frame["adm0_a3"] == "CHN"].geometry)
        ).simplify(0.01, preserve_topology=True)
        coast = gpd.read_file(root / "physical" / "ne_50m_coastline.shp")
        border = gpd.read_file(
            root / "cultural" / "ne_50m_admin_0_boundary_lines_land.shp")
        return {
            "land": land,
            "china": china,
            "coastline": list(coast.geometry),
            "borders": list(border.geometry),
        }
    except Exception:
        return {}


# -------------------------------------------------------------- 取色 --
def _mix(first: str, second: str, ratio: float) -> str:
    """两色线性混合（ratio=0 取 first，ratio=1 取 second）。"""
    a, b = QColor(first), QColor(second)
    return "#%02X%02X%02X" % (
        round(a.red() + (b.red() - a.red()) * ratio),
        round(a.green() + (b.green() - a.green()) * ratio),
        round(a.blue() + (b.blue() - a.blue()) * ratio),
    )


def _palette_for_map(style: dict) -> dict:
    """由主题推导地图专用色（海面、陆地、中国、海岸线、国界）。"""
    bg = style["bg"]
    grid = style["grid"]
    axis = style["axis_text"]
    return {
        "sea": bg,
        "land": _mix(bg, grid, 0.62),
        "china": _mix(bg, grid, 1.0),
        "coast": _mix(grid, axis, 0.55),
        "border": _mix(grid, axis, 0.38),
        "frame": _mix(grid, axis, 0.75),
    }


# -------------------------------------------------------------- 画布 --
class MapCanvas(QWidget):
    """站点地图画布：真实底图 + 数值散点 + 可选数值热力栅格。

    交互：滚轮缩放（以光标为中心）、左键拖拽平移、双击复位、单击选中站点。
    """

    clicked = Signal(str)          # 点击到的站点名
    hovered = Signal(str)          # 光标附近的站点读数（供状态条显示）

    def __init__(self, parent: QWidget | None = None, *,
                 compact: bool = False) -> None:
        super().__init__(parent)
        self._compact = compact
        self._region = "CHN"
        self._home = list(REGIONS["CHN"])
        self._extent = list(REGIONS["CHN"])
        self._basemap = True
        self._frame = not compact

        self._mode = "points"
        self._lat = np.array([])
        self._lon = np.array([])
        self._site = np.array([], dtype=object)
        self._value: np.ndarray | None = None
        self._weight: np.ndarray | None = None
        self._label = ""
        self._unit = ""
        self._focus = ""

        self._pixmap: QPixmap | None = None
        self._drag: tuple | None = None
        self._message = ""

        self.setMinimumHeight(200 if compact else 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.CrossCursor)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self._view = QLabel()
        self._view.setObjectName("MapView")
        self._view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._view.setSizePolicy(QSizePolicy.Policy.Ignored,
                                 QSizePolicy.Policy.Ignored)
        root.addWidget(self._view)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._render)
        self.schedule()

    # ---------------------------------------------------------- 数据 --
    def set_sites(self, frame, column: str | None = None, *, label: str = "",
                  unit: str = "mm", mode: str = "points",
                  weight_column: str | None = None,
                  point_size: float = 9.0, region: str | None = None) -> None:
        """喂入站点表（``site`` / ``lat`` / ``lon`` + 数值列）。

        ``mode`` 为 ``points`` 时画散点，``heat`` 时叠加插值热力栅格。
        """
        if region:
            self.set_region(region)
        if frame is None or len(frame) == 0:
            self.clear()
            return
        table = frame.dropna(subset=["lat", "lon"])
        if table.empty:
            self.clear()
            return
        self._lat = table["lat"].to_numpy(dtype=float)
        self._lon = table["lon"].to_numpy(dtype=float)
        self._site = (table["site"].astype(str).to_numpy()
                      if "site" in table.columns
                      else np.array([""] * len(table), dtype=object))
        self._value = (self._numeric(table, column)
                       if column and column in table.columns else None)
        self._weight = (self._numeric(table, weight_column)
                        if weight_column and weight_column in table.columns
                        else None)
        self._mode = mode if self._value is not None else "points"
        self._label = label or (column or "")
        self._unit = unit
        self._point_size = float(point_size)
        self._message = ""
        self._fit_extent()
        if region:
            # 显式指定的区域范围优先：否则会被上面的数据自适应覆盖，
            # 导致「全国 / 京津冀」切换看不出差别。
            self.set_region(region)
        self.schedule()

    @staticmethod
    def _numeric(table, column: str) -> np.ndarray:
        import pandas as pd

        return pd.to_numeric(table[column], errors="coerce").to_numpy(dtype=float)

    def clear(self) -> None:
        self._lat = np.array([])
        self._lon = np.array([])
        self._site = np.array([], dtype=object)
        self._value = None
        self._weight = None
        self._message = "暂无数据"
        self.reset_view()

    def _fit_extent(self) -> None:
        if not len(self._lat):
            return
        lon0, lon1 = float(np.min(self._lon)), float(np.max(self._lon))
        lat0, lat1 = float(np.min(self._lat)), float(np.max(self._lat))
        pad_x = max(1.5, (lon1 - lon0) * 0.08)
        pad_y = max(1.2, (lat1 - lat0) * 0.10)
        self._extent = self._clamp([lon0 - pad_x, lon1 + pad_x,
                                    lat0 - pad_y, lat1 + pad_y])
        self._home = list(self._extent)

    # ---------------------------------------------------------- 视图 --
    def set_region(self, key: str) -> None:
        if key not in REGIONS:
            return
        self._region = key
        self._home = list(REGIONS[key])
        self._extent = list(self._home)
        self.schedule()

    def set_basemap(self, enabled: bool) -> None:
        self._basemap = bool(enabled)
        self.schedule()

    def set_frame(self, enabled: bool) -> None:
        self._frame = bool(enabled)
        self.schedule()

    def extent(self) -> list[float]:
        return list(self._extent)

    def set_extent(self, extent) -> None:
        self._extent = self._clamp(list(extent))
        self.schedule()

    def zoom(self, factor: float, center: tuple[float, float] | None = None) -> None:
        """以 ``center``（经纬度）为中心缩放；factor>1 为放大。"""
        x0, x1, y0, y1 = self._extent
        cx, cy = center or ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
        self._extent = self._clamp([
            cx + (x0 - cx) / factor, cx + (x1 - cx) / factor,
            cy + (y0 - cy) / factor, cy + (y1 - cy) / factor])
        self.schedule()

    def reset_view(self) -> None:
        self._extent = list(self._home)
        self.schedule()

    @staticmethod
    def _clamp(extent) -> list[float]:
        x0, x1, y0, y1 = (float(v) for v in extent)
        x0, x1 = min(x0, x1), max(x0, x1)
        y0, y1 = min(y0, y1), max(y0, y1)
        if x1 - x0 < 0.05:
            middle = (x0 + x1) / 2.0
            x0, x1 = middle - 0.025, middle + 0.025
        if y1 - y0 < 0.05:
            middle = (y0 + y1) / 2.0
            y0, y1 = middle - 0.025, middle + 0.025
        y0 = max(-89.0, y0)
        y1 = min(89.0, y1)
        x0 = max(-179.0, x0)
        x1 = min(179.0, x1)
        return [x0, x1, y0, y1]

    # -------------------------------------------------------- 交互 --
    def _data_delta(self, dx: float, dy: float) -> tuple[float, float]:
        x0, x1, y0, y1 = self._extent
        width = max(1.0, float(self._view.width()))
        height = max(1.0, float(self._view.height()))
        return -(x1 - x0) * dx / width, (y1 - y0) * dy / height

    def wheelEvent(self, event) -> None:      # noqa: N802 - Qt 命名
        steps = event.angleDelta().y() / 120.0
        if not steps:
            return
        position = event.position()
        width = max(1.0, float(self._view.width()))
        height = max(1.0, float(self._view.height()))
        x0, x1, y0, y1 = self._extent
        lon = x0 + (x1 - x0) * position.x() / width
        lat = y1 - (y1 - y0) * position.y() / height
        self.zoom(1.22 ** steps, (lon, lat))
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton:
            position = event.position()
            self._drag = (position.x(), position.y(), list(self._extent))
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:   # noqa: N802 - Qt 命名
        position = event.position()
        if self._drag is not None:
            start_x, start_y, origin = self._drag
            x0, x1, y0, y1 = origin
            width = max(1.0, float(self._view.width()))
            height = max(1.0, float(self._view.height()))
            dx = (position.x() - start_x) * (x1 - x0) / width
            dy = (position.y() - start_y) * (y1 - y0) / height
            self._extent = self._clamp([x0 - dx, x1 - dx, y0 + dy, y1 + dy])
            self.schedule()
            return
        self._emit_hover(position)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton and self._drag is not None:
            start_x, start_y, _ = self._drag
            self._drag = None
            self.setCursor(Qt.CursorShape.CrossCursor)
            position = event.position()
            if abs(position.x() - start_x) < 4 and abs(position.y() - start_y) < 4:
                self._emit_click(position)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:   # noqa: N802 - Qt 命名
        self.reset_view()

    def resizeEvent(self, event) -> None:      # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self.schedule()

    def _to_lonlat(self, position) -> tuple[float, float] | None:
        width = max(1.0, float(self._view.width()))
        height = max(1.0, float(self._view.height()))
        x0, x1, y0, y1 = self._extent
        return (x0 + (x1 - x0) * position.x() / width,
                y1 - (y1 - y0) * position.y() / height)

    def _nearest(self, lon: float, lat: float) -> int | None:
        if not len(self._lat):
            return None
        distance = (self._lon - lon) ** 2 + (self._lat - lat) ** 2
        return int(np.argmin(distance))

    def _emit_hover(self, position) -> None:
        point = self._to_lonlat(position)
        if point is None:
            return
        index = self._nearest(*point)
        if index is None:
            return
        name = str(self._site[index])
        if self._value is not None and np.isfinite(self._value[index]):
            self.hovered.emit(f"{name}   {self._label} "
                              f"{self._value[index]:.2f} {self._unit}".strip())
        else:
            self.hovered.emit(name)

    def _emit_click(self, position) -> None:
        point = self._to_lonlat(position)
        if point is None:
            return
        index = self._nearest(*point)
        if index is not None:
            self.clicked.emit(str(self._site[index]))

    def focus_site(self, site: str) -> None:
        """把某个站点设为高亮（受 :meth:`set_sites` 之后的渲染影响）。"""
        self._focus = str(site or "")
        self.schedule()

    # -------------------------------------------------------- 渲染 --
    def schedule(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def refresh_theme(self) -> None:
        self.schedule()

    def render_pixmap(self) -> QPixmap:
        """立即（同步）重绘并返回位图。"""
        self._render()
        return self._pixmap or QPixmap()

    def save_png(self, path) -> str:
        pixmap = self.render_pixmap()
        pixmap.save(str(path), "PNG")
        return str(path)

    def export_png(self, parent: QWidget | None = None) -> str | None:
        from PySide6.QtWidgets import QFileDialog

        target, _ = QFileDialog.getSaveFileName(
            parent or self, "导出地图", "metcom_map.png", "PNG 图片 (*.png)")
        if not target:
            return None
        return self.save_png(target)

    def _render(self) -> None:
        if not _bootstrap():
            self._show_message(f"地图引擎不可用\n{_RUNTIME['error']}")
            return
        width = int(max(260, self._view.width() or self.width()))
        height = int(max(180, self._view.height() or self.height()))
        ratio = max(1.0, float(self.devicePixelRatioF() or 1.0))
        try:
            pixmap = self._figure_pixmap(width, height, ratio)
        except Exception as exc:                  # pragma: no cover - 渲染兜底
            self._show_message(f"地图渲染失败\n{type(exc).__name__}: {exc}")
            return
        if pixmap is None or pixmap.isNull():
            self._show_message("地图渲染失败")
            return
        pixmap.setDevicePixelRatio(ratio)
        self._pixmap = pixmap
        self._view.setPixmap(pixmap)
        self._view.setText("")

    def _show_message(self, text: str) -> None:
        pal = theme.palette()
        self._view.setPixmap(QPixmap())
        self._view.setText(text)
        self._view.setStyleSheet(
            f"color: {pal.text_faint}; font-size: 12px; padding: 24px;")

    def _figure_pixmap(self, width: int, height: int, ratio: float) -> QPixmap:
        import cartopy.crs as ccrs
        import matplotlib.ticker as mticker
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.colors import Normalize
        from matplotlib.figure import Figure
        import matplotlib.pyplot as plt

        style = theme.chart_style()
        colors = _palette_for_map(style)
        dpi = 96.0 * ratio
        figure = Figure(figsize=(width / 96.0, height / 96.0), dpi=dpi,
                        facecolor=colors["sea"])
        canvas = FigureCanvasAgg(figure)
        rect = ([0.02, 0.02, 0.96, 0.96] if (self._compact or not self._frame)
                else [0.055, 0.075, 0.845, 0.895])
        axes = figure.add_axes(rect, projection=ccrs.PlateCarree())
        axes.set_facecolor(colors["sea"])
        axes.set_extent(self._extent, crs=ccrs.PlateCarree())

        if self._basemap:
            data = land_data()
            if data:
                axes.add_geometries([data["land"]], ccrs.PlateCarree(),
                                    facecolor=colors["land"], edgecolor="none",
                                    zorder=0.4)
                axes.add_geometries([data["china"]], ccrs.PlateCarree(),
                                    facecolor=colors["china"],
                                    edgecolor=colors["border"], linewidth=0.5,
                                    zorder=0.6)
                axes.add_geometries(data["coastline"], ccrs.PlateCarree(),
                                    facecolor="none", edgecolor=colors["coast"],
                                    linewidth=0.55, zorder=1.4)
                axes.add_geometries(data["borders"], ccrs.PlateCarree(),
                                    facecolor="none", edgecolor=colors["border"],
                                    linewidth=0.55, zorder=1.3)

        if self._frame:
            gridliner = axes.gridlines(
                crs=ccrs.PlateCarree(), draw_labels=True, linewidth=0.5,
                color=style["grid"], alpha=0.75, zorder=1.6,
                xlocs=mticker.MultipleLocator(10),
                ylocs=mticker.MultipleLocator(10))
            gridliner.top_labels = False
            gridliner.right_labels = False
            gridliner.rotate_labels = False
            gridliner.xlabel_style = {"size": 7.5, "color": style["axis_text"]}
            gridliner.ylabel_style = {"size": 7.5, "color": style["axis_text"]}

        mappable = self._draw_layer(axes, ccrs, style, colors)
        axes.spines["geo"].set_edgecolor(colors["frame"])
        axes.spines["geo"].set_linewidth(1.0)

        if mappable is not None and self._frame:
            cax = figure.add_axes([0.915, 0.075, 0.018, 0.895])
            bar = figure.colorbar(mappable, cax=cax)
            bar.outline.set_edgecolor(colors["frame"])
            bar.outline.set_linewidth(0.8)
            bar.ax.tick_params(colors=style["axis_text"], labelsize=7.5,
                               length=3, width=0.6)
            bar.set_label(f"{self._label} ({self._unit})".strip(),
                          color=style["axis_text"], fontsize=8)

        canvas.draw()
        buffer = io.BytesIO()
        canvas.print_png(buffer)
        pixmap = QPixmap()
        pixmap.loadFromData(buffer.getvalue(), "PNG")
        return pixmap

    def _draw_layer(self, axes, ccrs, style: dict, colors: dict):
        """按当前模式画数据层，返回用于色标的 mappable。"""
        if self._value is None or not len(self._lat):
            return None
        import matplotlib.pyplot as plt
        from matplotlib.colors import Normalize

        values = np.asarray(self._value, dtype=float)
        finite = np.isfinite(values)
        if not finite.any():
            return None
        low = float(np.nanmin(values[finite]))
        high = float(np.nanmax(values[finite]))
        if high - low < 1e-9:
            high = low + 1.0
        norm = Normalize(vmin=low, vmax=high)
        cmap = plt.get_cmap(style["cmap"])

        if self._mode == "heat":
            grid = self._heat_grid(values, norm)
            if grid is None:
                return None
            grid_x, grid_y, grid_z = grid
            return axes.pcolormesh(grid_x, grid_y, grid_z, cmap=cmap,
                                   norm=norm, shading="auto", alpha=0.94,
                                   transform=ccrs.PlateCarree(), zorder=2.0)

        sizes = np.full(len(values), float(getattr(self, "_point_size", 9.0)))
        if self._weight is not None:
            weights = np.asarray(self._weight, dtype=float)
            good = np.isfinite(weights)
            if good.any():
                scaled = np.zeros_like(weights)
                span = float(np.nanmax(weights[good]) - np.nanmin(weights[good]))
                scaled[good] = ((weights[good] - np.nanmin(weights[good])) /
                                (span or 1.0))
                sizes = sizes * (0.75 + 1.5 * scaled)
        return axes.scatter(self._lon, self._lat, c=values, s=sizes, cmap=cmap,
                            norm=norm, marker="o", linewidths=0.25,
                            edgecolors=colors["frame"],
                            transform=ccrs.PlateCarree(), zorder=2.6)

    def _heat_grid(self, values: np.ndarray, norm):
        """站点 -> 规则栅格插值（线性为主，最近邻补边），并裁掉远处外推。"""
        from scipy.interpolate import griddata
        from scipy.spatial import cKDTree

        x0, x1, y0, y1 = self._extent
        columns = int(np.clip((x1 - x0) * 2.4, 60, 260))
        rows = int(np.clip((y1 - y0) * 2.4, 60, 220))
        gx = np.linspace(x0, x1, columns)
        gy = np.linspace(y0, y1, rows)
        grid_x, grid_y = np.meshgrid(gx, gy)
        points = np.column_stack([self._lon, self._lat])
        linear = griddata(points, values, (grid_x, grid_y), method="linear")
        nearest = griddata(points, values, (grid_x, grid_y), method="nearest")
        grid_z = np.where(np.isnan(linear), nearest, linear)

        # 距离最近站点超过 2 个格距的区域不参与绘制，避免"整块外推"
        tree = cKDTree(points)
        distance, _ = tree.query(np.column_stack([grid_x.ravel(),
                                                  grid_y.ravel()]))
        margin = max((x1 - x0) / columns, (y1 - y0) / rows) * 2.0
        grid_z = np.ma.masked_where((distance > margin).reshape(grid_z.shape),
                                    grid_z)
        if grid_z.mask.all():
            return None
        return grid_x, grid_y, grid_z


__all__ = ["MapCanvas", "available", "unavailable_reason", "land_data",
           "REGIONS"]
