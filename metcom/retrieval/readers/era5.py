"""ERA5 数据读取器。

对应 MATLAB: METCOM_ERA5/private/readSingleData.m、find_matching_files.m
与 ERA5.mlapp 主循环（单文件单时次）。

原 MATLAB 约定（本实现保持一致）：
  - 单文件含 z/t/q/r 四变量；纬度 90→-90 需翻转；经度 0~360 映射 [-180,180)
    后升序排序；气压层顺序不做任何重排（信任文件层序）；全部转 double。
Python 版增强：按维度名自动识别/重排为 (lon, lat, lev)，兼容不同 CDS API
产出的维度顺序（行为等价，见 docs/porting_notes.md）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import netCDF4

from metcom.config import parse_yyyymmddhh
from metcom.retrieval.readers.common import (
    apply_lonlat_convention, check_latlon_order, check_level_order,
    read_coord, read_field,
)
from metcom.retrieval.site_retrieval import TempData


def read_single_data(file_path: Path | str) -> TempData:
    """读取 ERA5 单时次 NC 文件（readSingleData.m 复刻）。"""
    file_path = Path(file_path)
    with netCDF4.Dataset(file_path, "r") as ds:
        pre = read_coord(ds, "pressure_level", "level", "lev", "plev")
        lon_g = read_coord(ds, "longitude", "lon")
        lat_g = read_coord(ds, "latitude", "lat")

        geo = read_field(ds, "z")   # 位势 (m²/s²)
        tem = read_field(ds, "t")   # 温度 (K)
        sh = read_field(ds, "q")    # 比湿 (kg/kg)
        rh = read_field(ds, "r")    # 相对湿度 (%)

    if geo.shape != (lon_g.size, lat_g.size, pre.size):
        raise ValueError(
            f"数据维度 {geo.shape} 与坐标 (lon={lon_g.size}, lat={lat_g.size}, "
            f"lev={pre.size}) 不一致: {file_path.name}"
        )

    fields, lon_g, lat_g = apply_lonlat_convention(
        {"geo": geo, "tem": tem, "sh": sh, "rh": rh}, lon_g, lat_g)
    check_level_order(fields["geo"], "z", file_path.name)
    check_latlon_order(lon_g, lat_g, file_path.name)

    return TempData(geo=fields["geo"], tem=fields["tem"], sh=fields["sh"],
                    rh=fields["rh"], pre=pre, lon=lon_g, lat=lat_g)


def _hour_bounds(start_time: str | int | None,
                 end_time: str | int | None) -> tuple[str, str] | None:
    """Return inclusive YYYYMMDDHH bounds, or None for date-only compatibility."""
    if start_time is None and end_time is None:
        return None
    if start_time is None or end_time is None:
        raise ValueError("小时级时间过滤必须同时提供 start_time 和 end_time")

    start_text = str(start_time).strip()
    end_text = str(end_time).strip()
    if len(start_text) == 8 and len(end_text) == 8:
        return None
    if len(start_text) != 10 or len(end_text) != 10:
        raise ValueError(
            "时间格式必须同时为 YYYYMMDD 或同时为 YYYYMMDDHH："
            f"start={start_time!r}, end={end_time!r}"
        )

    try:
        *_, start_hour = parse_yyyymmddhh(start_text)
        *_, end_hour = parse_yyyymmddhh(end_text)
    except ValueError as exc:
        raise ValueError(f"小时级时间格式无效: {start_time!r}..{end_time!r}") from exc
    if start_hour > 23 or end_hour > 23:
        raise ValueError(f"小时值必须为 00..23: {start_time!r}..{end_time!r}")
    if start_text > end_text:
        raise ValueError(f"起始时刻不能晚于结束时刻: {start_time!r}..{end_time!r}")
    return start_text, end_text


def iter_hours(base_folder: Path | str, start_date, end_date,
               start_time: str | int | None = None,
               end_time: str | int | None = None) -> Iterator[tuple[str, TempData]]:
    """逐时次产出 (YYYYMMDDHH, TempData)。

    Two-argument calls keep the historical date-only behavior. When both
    ``start_time`` and ``end_time`` are ten-digit YYYYMMDDHH strings, files
    outside that inclusive time range are filtered before they are read.
    """
    base_folder = Path(base_folder)
    bounds = _hour_bounds(start_time, end_time)
    for day_folder in _scan_day_folders(base_folder, start_date, end_date):
        for p in sorted(day_folder.glob("*.nc"), key=lambda x: x.name):
            ts = _extract_timestamp(p.name)
            if bounds is not None and not (bounds[0] <= ts <= bounds[1]):
                continue
            yield ts, read_single_data(p)


def _scan_day_folders(base_folder: Path, start_date, end_date):
    from metcom.retrieval.pipeline import filter_day_folders
    return filter_day_folders(base_folder, start_date, end_date)


def _extract_timestamp(filename: str) -> str:
    import re
    m = re.search(r"\d{10}", filename)
    if m is None:
        raise ValueError(f"文件名中未找到10位时间戳 (YYYYMMDDHH): {filename}")
    return m.group(0)


class Era5Reader:
    """ERA5 读取器（流水线按数据源 key 分派）。"""

    key = "era5"

    @staticmethod
    def iter_hours(base_folder, start_date, end_date,
                   start_time=None, end_time=None):
        return iter_hours(base_folder, start_date, end_date,
                          start_time=start_time, end_time=end_time)
