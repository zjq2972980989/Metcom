"""EGM2008 大地水准面格网读取与查表。

对应 MATLAB 中 ``load("geoidheight_EGM2008_*.mat")`` 后的最近邻查表：
  egm2008(egm_lonIdx, egm_latIdx)   % 第一维经度、第二维纬度，最近邻取值

.mat 文件为 MATLAB v7.3（HDF5）格式：
  - MATLAB 数组 (n_lon, n_lat) 在 h5py 中读出为 (n_lat, n_lon)，需转置；
  - 纬度方向按首/末极点行判定并统一归一化为升序 -90 → +90（见 load_egm_mat）；
  - lon_axis 按网格是否闭合生成：闭合网格 -180 … +180（含重复端点，如 0250 的
    1441 点）或非闭合网格 -180 … 179.375（如 0500_0625 的 576 点）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import h5py
import numpy as np


@dataclass(frozen=True)
class EgmGrid:
    """EGM2008 大地水准面高格网（等价于 MATLAB 工作区中的 egm2008 矩阵）。

    values 形状为 (n_lon, n_lat)，与 MATLAB ``egm2008(lonIdx, latIdx)`` 索引一致；
    纬度维恒为升序（-90 → +90，见 ``load_egm_mat``）。
    """

    values: np.ndarray          # (n_lon, n_lat)
    lon_axis: np.ndarray        # -180 .. 180
    lat_axis: np.ndarray        # -90 .. 90

    def index_of_lon(self, lon_value: float) -> int:
        """等价于 findLongitudePosition 中 egm_lonRange<=v 的 'last' 语义（0 基）。"""
        idx = int(np.max(np.where(self.lon_axis <= lon_value)[0]))
        return idx

    def index_of_lat(self, lat_value: float) -> int:
        idx = int(np.max(np.where(self.lat_axis <= lat_value)[0]))
        return idx

    def nearest(self, lon_idx: int, lat_idx: int) -> float:
        """最近邻取值（原代码不做插值）。"""
        return float(self.values[lon_idx, lat_idx])


# ---- 纬度方向判定：EGM2008 南北极是仅有的沿经度近似常值的行 ----
# 极点大地水准面高典型值 (m)，仅用于识别格网纬度方向，不参与数值计算。
_EGM2008_POLE_NORTH_M = 14.899     # 北极 lat = +90
_EGM2008_POLE_SOUTH_M = -30.150    # 南极 lat = -90
_POLE_ROW_FLAT_TOL_M = 0.5         # 极点行沿经度近常值的极差容差
_POLE_VALUE_TOL_M = 2.0            # 与参考极点值的容差
_MERIDIAN_DUP_TOL_M = 1.0e-3       # 闭合经度网格首末子午线数值一致性的容差


def _pole_row_kind(row: np.ndarray) -> str | None:
    """识别一行是否为极点行：返回 "north" / "south"，都不是则 None。"""
    if float(np.ptp(row)) > _POLE_ROW_FLAT_TOL_M:
        return None
    mean = float(np.mean(row))
    if abs(mean - _EGM2008_POLE_NORTH_M) <= _POLE_VALUE_TOL_M:
        return "north"
    if abs(mean - _EGM2008_POLE_SOUTH_M) <= _POLE_VALUE_TOL_M:
        return "south"
    return None


def _lat_is_descending(values: np.ndarray) -> bool:
    """依据首/末极点行判定 (n_lon, n_lat) 格网的纬度维是否为 +90 → -90 降序。"""
    head = _pole_row_kind(values[:, 0])
    tail = _pole_row_kind(values[:, -1])
    if {head, tail} == {"north", "south"}:
        return head == "north"
    raise ValueError(
        "无法自动判定 EGM2008 格网的纬度方向：首/末行不是可识别的南北极点行"
        f"（首行均值 {values[:, 0].mean():.3f} m，末行均值 {values[:, -1].mean():.3f} m）；"
        "请用 lat_order='ascending'/'descending' 显式指定"
    )


def _lon_is_closed(values: np.ndarray) -> bool:
    """闭合经度网格（-180 … +180）首末列是同一条子午线，数值应一致。"""
    if values.shape[0] < 2:
        return False
    return bool(np.max(np.abs(values[0, :] - values[-1, :])) <= _MERIDIAN_DUP_TOL_M)


def load_egm_mat(path: Path | str, expected_sr: tuple[float, float] | None = None,
                 lat_order: str = "auto") -> EgmGrid:
    """读取 MATLAB v7.3 EGM2008 mat 文件，并统一坐标轴约定。

    Parameters
    ----------
    path:
        geoidheight_EGM2008_*.mat 文件路径。
    expected_sr:
        期望的 (lat_sr, lon_sr)，用于校验文件与数据源分辨率一致（可选）。
    lat_order:
        原始文件的纬度方向："auto"（默认，按首/末极点行判定）、
        "ascending"（-90 → +90）或 "descending"（+90 → -90）。

    返回的坐标轴与数值统一为：
      - lat_axis 恒为升序 -90 … +90；原始文件为降序时内部翻转 values 的纬度维，
        不改动经纬数据本身（下游 index_of_lat 依赖纬度升序）；
      - lon_axis 按网格是否闭合生成：闭合网格（-180 … +180，含重复端点）
        步长 360/(n_lon-1)，非闭合网格（如 MERRA2 -180 … 179.375）步长 360/n_lon。
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"EGM2008 高程改正文件不存在: {path}")

    with h5py.File(path, "r") as f:
        if "egm2008" not in f:
            raise KeyError(f"{path.name} 中未找到数据集 'egm2008'，实际包含: {list(f.keys())}")
        raw = np.asarray(f["egm2008"][()], dtype=float)

    # MATLAB (n_lon, n_lat) —— HDF5 行主序存储为 (n_lat, n_lon)，转置还原。
    # 经度轴跨度 360° > 纬度轴 180°，故 n_lon > n_lat：raw 第一维应为较短轴。
    if raw.shape[0] < raw.shape[1]:
        values = raw.T          # -> (n_lon, n_lat)
    else:
        values = raw
    n_lon, n_lat = values.shape

    # ---- 纬度方向归一化：无论原始文件是 -90→+90 还是 +90→-90，一律转为升序 ----
    if lat_order == "auto":
        descending = _lat_is_descending(values)
    elif lat_order in ("ascending", "descending"):
        descending = lat_order == "descending"
    else:
        raise ValueError(f"lat_order 只能取 'auto'/'ascending'/'descending'，收到: {lat_order!r}")
    if descending:
        values = values[:, ::-1].copy()      # +90 → -90 翻转为 -90 → +90

    lon_sr = None
    if expected_sr is not None:
        # 用格点数量校验与数据源相容的网格，而不是用 n-1 反推步长：
        # 部分数据源的经度轴不闭合（如 MERRA2 -180..179.375 共 576 点），
        # 首尾不重复时 360/(n-1) 会得到偏大的假步长（0.6260 vs 0.6250）。
        lat_sr_exp, lon_sr_exp = expected_sr
        lon_cells = int(round(360.0 / lon_sr_exp))
        lat_cells = int(round(180.0 / lat_sr_exp))
        if n_lon not in (lon_cells, lon_cells + 1) or n_lat not in (lat_cells, lat_cells + 1):
            lon_sr_calc = 360.0 / n_lon
            lat_sr_calc = 180.0 / (n_lat - 1)
            raise ValueError(
                f"EGM2008 文件分辨率({lat_sr_calc}, {lon_sr_calc}) 与数据源分辨率{expected_sr}不一致: {path.name}"
            )
        lon_sr = lon_sr_exp

    # ---- 经度轴：区分闭合(-180 … +180)/非闭合(-180 … 179.375)网格 ----
    if lon_sr is not None:
        closed = n_lon == int(round(360.0 / lon_sr)) + 1
    else:
        closed = _lon_is_closed(values)      # 无 expected_sr 时按首末子午线数值判定
    lon_spacing = 360.0 / (n_lon - 1) if closed else 360.0 / n_lon
    lon_axis = -180.0 + np.arange(n_lon, dtype=float) * lon_spacing
    lat_axis = np.linspace(-90.0, 90.0, n_lat)
    return EgmGrid(values=values, lon_axis=lon_axis, lat_axis=lat_axis)
