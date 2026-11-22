"""DEM 正交高程读取与最近邻采样（Grid Step 2）。

本模块只做两件事：

1. ``load_dem`` 打开 DEM NetCDF，自动识别经度 / 纬度 / 高程变量，
   把坐标统一规范化为**升序**，并同步翻转高程数组；
2. ``sample_nearest`` 对调用方给定的 lon/lat 点做最近邻采样。

设计约束（对应当前阶段任务书）：

- 只使用项目已有依赖（numpy + netCDF4），不新增依赖；
- 高程是**正交高程**（orthometric height）：不做 EGM2008 改正，
  不做正高 / 椭球高转换，直接把 DEM 高程写入站点文件；
- 采样只做最近邻，不做双线性等插值（当前 DEM 0.125° 已足够细）；
- 请求点必须完全落在 DEM 覆盖范围内：超出范围直接抛错，
  不裁剪请求范围、不静默丢弃格点；
- 默认任何 NaN / Inf 高程都视为数据错误；`allow_missing=True` 时允许高程含
  缺测节点（供 gridgen 复用路径按「DEM 有效节点」筛选，缺测节点由原实现剔除）。

内部统一布局：``DemGrid.elevation[lat_index, lon_index]``，即形状
``(n_lat, n_lon)``，与 ``DemGrid.lons`` / ``DemGrid.lats``（升序）一一对应。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import netCDF4
import numpy as np

from services.errors import InvalidRequestError

# 变量名候选（按优先级）；识别不到时再按"维度 + 二维 + 数值"扫描。
_LON_NAMES = ("lon", "longitude", "x")
_LAT_NAMES = ("lat", "latitude", "y")
_ELEVATION_NAMES = ("elevation", "dem", "z", "topo", "height")

# 覆盖范围容差：只用于吸收浮点端点误差，不做任何"约等于"的放宽。
_COORD_TOLERANCE = 1e-9


@dataclass(frozen=True)
class DemGrid:
    """规范化后的 DEM：升序 ``lons`` / 升序 ``lats`` / ``(n_lat, n_lon)`` 高程。"""

    lons: np.ndarray
    lats: np.ndarray
    elevation: np.ndarray
    source: str = ""

    @property
    def lon_range(self) -> tuple[float, float]:
        return float(self.lons[0]), float(self.lons[-1])

    @property
    def lat_range(self) -> tuple[float, float]:
        return float(self.lats[0]), float(self.lats[-1])


def _read_variable(variable: Any) -> np.ndarray:
    """把 netCDF 变量读成 float ndarray，掩码值统一转成 NaN。"""
    raw = variable[:]
    if np.ma.isMaskedArray(raw):
        return np.ma.filled(raw.astype(float), np.nan)
    return np.asarray(raw, dtype=float)


def _find_coordinate(ds: Any, label: str, candidates: tuple[str, ...]) -> tuple[np.ndarray, str]:
    """识别一维经纬度坐标，返回 (坐标值, 维度名)。

    只接受一维、数值、长度 >= 2、全部有限且**严格单调**的候选变量。
    命中多个候选时不猜，直接报错。
    """
    matches: list[tuple[str, np.ndarray, str]] = []
    for name in candidates:
        variable = ds.variables.get(name)
        if variable is None or variable.ndim != 1:
            continue
        values = _read_variable(variable).ravel()
        if values.size < 2 or not np.all(np.isfinite(values)):
            continue
        dim = variable.dimensions[0] if variable.dimensions else name
        matches.append((name, values, dim))

    if not matches:
        raise InvalidRequestError(
            f"DEM 中无法识别{label}坐标变量：{list(candidates)}。"
        )
    if len(matches) > 1:
        raise InvalidRequestError(
            f"DEM 中无法唯一确定{label}坐标变量：{[m[0] for m in matches]}。"
        )

    name, values, dim = matches[0]
    diffs = np.diff(values)
    if not (np.all(diffs > 0) or np.all(diffs < 0)):
        raise InvalidRequestError(
            f"DEM {label}坐标 {name} 不是单调递增或递减，无法做最近邻采样。"
        )
    return values, dim


def _find_elevation(ds: Any, lat_dim: str, lon_dim: str) -> tuple[str, tuple[str, ...]]:
    """识别二维高程变量，返回 (变量名, 维度顺序)。

    先按常见名称匹配，再按"二维 + 维度恰为 (lat, lon) 或 (lon, lat) + 尺寸一致"
    扫描。候选不唯一时报错，不猜。
    """
    if lat_dim == lon_dim:
        raise InvalidRequestError("DEM 经纬度坐标使用了同一维度，无法识别高程变量。")

    lat_size = len(ds.dimensions[lat_dim])
    lon_size = len(ds.dimensions[lon_dim])

    def _matched_dims(variable: Any) -> tuple[str, ...] | None:
        if variable.ndim != 2:
            return None
        dims = tuple(variable.dimensions)
        if set(dims) != {lat_dim, lon_dim}:
            return None
        sizes = {dim: len(ds.dimensions[dim]) for dim in dims}
        if sizes[lat_dim] != lat_size or sizes[lon_dim] != lon_size:
            return None
        return dims

    named: list[tuple[str, tuple[str, ...]]] = []
    for name in _ELEVATION_NAMES:
        variable = ds.variables.get(name)
        if variable is None:
            continue
        dims = _matched_dims(variable)
        if dims is not None:
            named.append((name, dims))
    if len(named) == 1:
        return named[0]
    if len(named) > 1:
        raise InvalidRequestError(
            f"无法唯一确定 DEM 高程变量：{[n for n, _ in named]}。"
        )

    scanned: list[tuple[str, tuple[str, ...]]] = []
    for name, variable in ds.variables.items():
        if not np.issubdtype(variable.dtype, np.number):
            continue
        dims = _matched_dims(variable)
        if dims is not None:
            scanned.append((name, dims))
    if len(scanned) == 1:
        return scanned[0]
    if len(scanned) > 1:
        raise InvalidRequestError(
            f"无法唯一确定 DEM 高程变量：{[n for n, _ in scanned]}。"
        )
    raise InvalidRequestError("DEM 中未找到与经纬度维度一致的二维高程变量。")


def load_dem(path: Path | str, *, allow_missing: bool = False) -> DemGrid:
    """打开 DEM NetCDF，返回规范化（升序坐标 + ``(n_lat, n_lon)`` 高程）的格子。

    Parameters
    ----------
    allow_missing:
        ``False``（默认）时任何非有限高程都报错；``True`` 时保留 NaN/Inf 缺测
        节点，交由调用方（gridgen 复用路径）按有效节点语义处理。
    """
    path = Path(path)
    if not path.is_file():
        raise InvalidRequestError(f"DEM 文件不存在：{path}")

    try:
        ds = netCDF4.Dataset(str(path), "r")
    except OSError as exc:
        raise InvalidRequestError(f"无法打开 DEM 文件：{path}（{exc}）") from exc

    try:
        lons, lon_dim = _find_coordinate(ds, "经度", _LON_NAMES)
        lats, lat_dim = _find_coordinate(ds, "纬度", _LAT_NAMES)
        elevation_name, elevation_dims = _find_elevation(ds, lat_dim, lon_dim)
        elevation = _read_variable(ds.variables[elevation_name])
    finally:
        ds.close()

    if elevation_dims == (lon_dim, lat_dim):
        elevation = elevation.T
    elif elevation_dims != (lat_dim, lon_dim):
        raise InvalidRequestError(
            f"DEM 高程变量 {elevation_name} 的维度顺序 {elevation_dims} 不是 "
            f"({lat_dim}, {lon_dim}) 或 ({lon_dim}, {lat_dim})。"
        )

    if elevation.shape != (lats.size, lons.size):
        raise InvalidRequestError(
            f"DEM 高程形状 {elevation.shape} 与经纬度坐标 "
            f"(lat={lats.size}, lon={lons.size}) 不一致。"
        )
    if not allow_missing and not np.all(np.isfinite(elevation)):
        raise InvalidRequestError("DEM 高程包含 NaN 或 Inf，无法用于格点采样。")

    # 坐标一律规范化为升序，翻转坐标时必须同步翻转高程。
    if lons[0] > lons[-1]:
        lons = lons[::-1]
        elevation = elevation[:, ::-1]
    if lats[0] > lats[-1]:
        lats = lats[::-1]
        elevation = elevation[::-1, :]

    return DemGrid(
        lons=np.ascontiguousarray(lons, dtype=float),
        lats=np.ascontiguousarray(lats, dtype=float),
        elevation=np.ascontiguousarray(elevation, dtype=float),
        source=str(path),
    )


def _nearest_index(axis: np.ndarray, values: np.ndarray) -> np.ndarray:
    """在升序一维坐标轴上取最近节点下标；等距时统一取较小下标（确定性）。"""
    indices = np.searchsorted(axis, values)
    indices = np.clip(indices, 1, axis.size - 1)
    left = axis[indices - 1]
    right = axis[indices]
    choose_left = np.abs(values - left) <= np.abs(right - values)
    return np.where(choose_left, indices - 1, indices)


def _require_within(axis: np.ndarray, values: np.ndarray, label: str) -> None:
    """请求坐标必须完全落在 DEM 覆盖范围内，超出即报错（不裁剪）。"""
    low, high = float(axis[0]), float(axis[-1])
    if np.any(values < low - _COORD_TOLERANCE) or np.any(values > high + _COORD_TOLERANCE):
        raise InvalidRequestError(
            f"Grid {label} range [{values.min():g}, {values.max():g}] "
            f"exceeds DEM coverage [{low:g}, {high:g}]"
        )


def sample_nearest(dem: DemGrid, lons: Any, lats: Any) -> np.ndarray:
    """对成对的 lon/lat 点做最近邻采样，返回一维高程数组（与输入等长）。

    超出 DEM 范围、非有限输入或采样结果非有限时抛 ``InvalidRequestError``。
    """
    lon_values = np.asarray(lons, dtype=float).ravel()
    lat_values = np.asarray(lats, dtype=float).ravel()
    if lon_values.size != lat_values.size:
        raise InvalidRequestError(
            f"经度点数 {lon_values.size} 与纬度点数 {lat_values.size} 不一致。"
        )
    if lon_values.size == 0:
        raise InvalidRequestError("格点点集为空，无法采样高程。")
    if not (np.all(np.isfinite(lon_values)) and np.all(np.isfinite(lat_values))):
        raise InvalidRequestError("格点经纬度包含 NaN 或 Inf，无法采样高程。")

    _require_within(dem.lons, lon_values, "longitude")
    _require_within(dem.lats, lat_values, "latitude")

    lon_index = _nearest_index(dem.lons, lon_values)
    lat_index = _nearest_index(dem.lats, lat_values)
    heights = np.asarray(dem.elevation[lat_index, lon_index], dtype=float)
    if not np.all(np.isfinite(heights)):
        raise InvalidRequestError("最近邻采样得到 NaN 或 Inf 高程。")
    return heights


__all__ = ["DemGrid", "load_dem", "sample_nearest"]
