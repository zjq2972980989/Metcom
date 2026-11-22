"""数据读取器公共工具：维度整理、坐标处理（与 MATLAB readSingleData 语义一致）。

各数据源读取器（era5/grib_sources/merra2）共用的底层函数：

- ``classify_dims`` / ``read_field``：netCDF 变量按**维度名**自动重排为
  MATLAB ncread 期望的 (lon, lat, lev) 布局（兼容不同 CDS API 产物的维度
  顺序），squeeze 时间维、统一 float64；
- ``read_coord``：一维坐标读取（多候选名容错）；
- ``to_float64``：masked array 的 _FillValue → NaN（对应 MATLAB ncread 行为，
  缺测随后由 MERRA2 的 rmmissing 式修剪处理）；
- ``apply_lonlat_convention``：纬度翻转（90→-90 → 升序）+ 经度 0~360 映射
  [-180,180) 后升序排序（ERA5/CRA 系列数据的统一约定；MERRA2 原生升序
  不走此函数）；
- ``check_level_order`` / ``check_latlon_order``：层序/坐标方向的健全性
  警告（fun_calculation_site 依赖"层序沿数组方向高度递增"）。
"""

from __future__ import annotations

import netCDF4
import numpy as np

_LON_DIMS = {"longitude", "lon", "x"}
_LAT_DIMS = {"latitude", "lat", "y"}
_LEV_DIMS = {"pressure_level", "level", "lev", "plev",
             "isobaricInhPa", "isobaric", "pressure"}


def classify_dims(dims: tuple[str, ...]) -> tuple[int, int, int] | None:
    """返回 (lon_ax, lat_ax, lev_ax) 在变量维度中的位置；无法识别时返回 None。"""
    pos: dict[str, int] = {}
    for ax, d in enumerate(dims):
        if d in _LON_DIMS:
            pos.setdefault("lon", ax)
        elif d in _LAT_DIMS:
            pos.setdefault("lat", ax)
        elif d in _LEV_DIMS:
            pos.setdefault("lev", ax)
        # 其余（time/valid_time 等）忽略
    if {"lon", "lat", "lev"} <= pos.keys():
        return pos["lon"], pos["lat"], pos["lev"]
    return None


def to_float64(data) -> np.ndarray:
    """转 float64；netCDF masked array 的缺测（_FillValue）填 NaN。

    对应 MATLAB ncread 的行为（FillValue → NaN），缺测随后由
    fun_Merra2_site 的 rmmissing 式修剪处理。
    """
    if np.ma.isMaskedArray(data):
        return np.asarray(data.filled(np.nan), dtype=np.float64)
    return np.asarray(data, dtype=np.float64)


def read_field(ds: netCDF4.Dataset, name: str) -> np.ndarray:
    """读取变量并整理为 (n_lon, n_lat, n_lev) 的 float64 数组（等价 MATLAB
    ncread + squeeze + 维度重排；MATLAB 期望布局为 lon×lat×lev）。"""
    var = ds.variables[name]
    arr = to_float64(var[:])
    order = classify_dims(var.dimensions)
    if order is not None:
        lon_ax, lat_ax, lev_ax = order
        other = [ax for ax in range(arr.ndim) if ax not in order]
        arr = np.transpose(arr, (lon_ax, lat_ax, lev_ax, *other))
    arr = np.squeeze(arr)
    if arr.ndim != 3:
        raise ValueError(f"变量 {name} squeeze 后维度为 {arr.shape}，期望 (lon, lat, lev)")
    return arr


def read_coord(ds: netCDF4.Dataset, *names: str) -> np.ndarray:
    """读取一维坐标变量（取第一个存在的名字）。"""
    for n in names:
        if n in ds.variables:
            return to_float64(ds.variables[n][:]).ravel()
    raise KeyError(f"文件中未找到坐标变量 {names}，实际有: {list(ds.variables)}")


def apply_lonlat_convention(fields: dict[str, np.ndarray], lon: np.ndarray,
                            lat: np.ndarray) -> tuple[dict[str, np.ndarray],
                                                      np.ndarray, np.ndarray]:
    """统一经纬度约定（MATLAB 各 readSingleData 的 flip + mod + sort 步骤）：
    纬度翻转为 -90→90 升序；经度 0~360 映射 [-180,180) 后升序排序。"""
    lon = np.asarray(lon, dtype=float)
    lat = np.asarray(lat, dtype=float)

    lat = lat[::-1].copy()
    fields = {k: v[:, ::-1, :] for k, v in fields.items()}

    lon_temp = np.mod(lon + 180.0, 360.0) - 180.0
    sort_idx = np.argsort(lon_temp, kind="stable")
    lon = lon_temp[sort_idx].copy()
    fields = {k: v[sort_idx, :, :] for k, v in fields.items()}
    return fields, lon, lat


def check_level_order(geo_like: np.ndarray, name: str, file_name: str) -> None:
    """校验层序沿数组方向高度递增（fun_calculation_site 的 k 搜索依赖此序）。"""
    col = geo_like[0, 0, :]
    if col.size >= 2 and col[0] > col[-1]:
        print(f"警告: {file_name} 中 {name} 沿层维高度递减，"
              f"与算法假设的升序不符，结果可能异常（原 MATLAB 代码同样信任文件层序）")


def check_latlon_order(lon: np.ndarray, lat: np.ndarray, file_name: str) -> None:
    if lat.size >= 2 and lat[0] > lat[-1]:
        print(f"警告: {file_name} 纬度坐标非升序（-90→90），结果可能异常")
    if lon.size >= 2 and (lon[0] > lon[-1] or lon[-1] > 180.0):
        print(f"警告: {file_name} 经度坐标非 [-180,180) 升序，结果可能异常")
