"""MERRA2 数据读取器。

对应 MATLAB: METCOM_MERRA2/private/readSingleData.m、find_matching_files.m
与 MERRA2.mlapp 主循环（一天一文件、4 个 6h 时次切片）。

约定（与 MATLAB 一致）：
  - 变量 H（位势高 m，不除 g）/T/QV（比湿），气压坐标 lev (hPa)；
  - 坐标不做 flip/排序（MERRA2 原生 lat -90→90、lon -180~180 升序）；
  - fun_Merra2_site 中对地下层 NaN 做修剪（见 site_retrieval.nan_trim_columns）；
  - PS（地表气压）在 MATLAB 中读取但从未使用，此处不读（行为等价）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

import netCDF4
import numpy as np

from metcom.retrieval.readers.common import (
    check_latlon_order, check_level_order, read_coord, to_float64,
)
from metcom.retrieval.site_retrieval import TempData

_FILE_RE = re.compile(r"MERRA2_400\.inst6_3d_ana_Np\.(\d{8})\.SUB\.nc")


def _find_daily_files(base_folder: Path, start_date, end_date) -> list[Path]:
    """find_matching_files.m 复刻：在 yyyy 年子文件夹中按日期过滤。"""
    files: list[Path] = []
    for year in sorted({d.year for d in (start_date, end_date)}):
        year_folder = base_folder / f"{year}"
        if not year_folder.is_dir():
            continue
        for p in sorted(year_folder.glob("MERRA2_400.inst6_3d_ana_Np.*.SUB.nc"),
                        key=lambda x: x.name):
            m = _FILE_RE.fullmatch(p.name)
            if m is None:
                continue
            d = np.datetime64(f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}")
            if (np.datetime64(start_date) <= d <= np.datetime64(end_date)):
                files.append(p)
    return files


def _read_hour_slice(file_path: Path, hour_index: int) -> TempData:
    """读取单日文件的一个时次切片（MATLAB: TempData{...}(:,:,:,hourIndex)）。"""
    with netCDF4.Dataset(file_path, "r") as ds:
        pre = read_coord(ds, "lev", "level", "pressure_level")
        lon_g = read_coord(ds, "lon", "longitude")
        lat_g = read_coord(ds, "lat", "latitude")

        def read4(name: str) -> np.ndarray:
            var = ds.variables[name]
            arr = to_float64(var[:])
            # MERRA2 存储常为 (time, lev, lat, lon)：整理为 (lon, lat, lev, time)
            from metcom.retrieval.readers.common import classify_dims
            dims = var.dimensions
            ax = {d: i for i, d in enumerate(dims)}
            order = classify_dims(dims)
            if order is not None:
                lon_ax, lat_ax, lev_ax = order
                time_ax = [i for i in range(arr.ndim) if i not in order]
                arr = np.transpose(arr, (lon_ax, lat_ax, lev_ax, *time_ax))
            elif "time" in ax:
                rest = [i for i in range(arr.ndim) if i != ax["time"]]
                arr = np.transpose(arr, rest + [ax["time"]])
            arr = arr[..., hour_index - 1]  # hourIndex 为 1 基（MATLAB 语义）→ (lon, lat, lev)
            return np.squeeze(arr)

        geo = read4("H")
        tem = read4("T")
        sh = read4("QV")

    if geo.shape != (lon_g.size, lat_g.size, pre.size):
        raise ValueError(
            f"数据维度 {geo.shape} 与坐标 (lon={lon_g.size}, lat={lat_g.size}, "
            f"lev={pre.size}) 不一致: {file_path.name}"
        )
    check_latlon_order(lon_g, lat_g, file_path.name)
    check_level_order(geo, "H", file_path.name)

    return TempData(geo=geo, tem=tem, sh=sh, rh=None, pre=pre, lon=lon_g, lat=lat_g)


def iter_hours(base_folder, start_date, end_date) -> Iterator[tuple[str, TempData]]:
    """逐时次产出 (YYYYMMDDHH, TempData)：一天一文件读一次、切 4 个时次。"""
    base_folder = Path(base_folder)
    for file_path in _find_daily_files(base_folder, start_date, end_date):
        m = _FILE_RE.fullmatch(file_path.name)
        day_str = m.group(1)
        # MATLAB GUI: hourIndex 1..4 → 00/06/12/18 时
        for hour_index, hh in enumerate(("00", "06", "12", "18"), start=1):
            yield day_str + hh, _read_hour_slice(file_path, hour_index)


class Merra2Reader:
    """MERRA2 读取器。"""

    key = "merra2"

    @staticmethod
    def iter_hours(base_folder, start_date, end_date):
        return iter_hours(base_folder, start_date, end_date)
