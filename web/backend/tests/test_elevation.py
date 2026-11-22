"""``services.elevation`` 的离线测试（Grid Step 2）。

只测 DEM 读取与最近邻采样：不联网、不调用 retrieval/adapter/gridgen、
不写任务目录。合成 NetCDF 一律落在 pytest ``tmp_path``。
"""

from __future__ import annotations

from pathlib import Path

import netCDF4
import numpy as np
import pytest

from config import PROJECT_ROOT
from services import elevation
from services.errors import InvalidRequestError

BENCHMARK_DEM = PROJECT_ROOT / "benchmarks" / "DEM_Output" / "bth_dem_0125deg_rect.nc"


def _write_dem(
    path: Path,
    *,
    lons: np.ndarray,
    lats: np.ndarray,
    elevation_data: np.ndarray,
    lon_name: str = "lon",
    lat_name: str = "lat",
    elevation_name: str = "elevation",
    dim_order: tuple[str, str] = ("lat", "lon"),
) -> Path:
    """写一个最小 DEM NetCDF；``elevation_data`` 形状为 (n_lat, n_lon)。"""
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension(lon_name, lons.size)
        ds.createDimension(lat_name, lats.size)
        ds.createVariable(lon_name, "f8", (lon_name,))[:] = lons
        ds.createVariable(lat_name, "f8", (lat_name,))[:] = lats
        if dim_order == ("lat", "lon"):
            variable = ds.createVariable(elevation_name, "f8", (lat_name, lon_name))
            variable[:] = elevation_data
        else:
            variable = ds.createVariable(elevation_name, "f8", (lon_name, lat_name))
            variable[:] = elevation_data.T
    return path


def _value(lat: float, lon: float) -> float:
    """合成 DEM 的取值函数，便于验证翻转/采样方向。"""
    return lat * 100.0 + lon


def _grid_values(lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    return np.array([[_value(lat, lon) for lon in lons] for lat in lats], dtype=float)


# --------------------------------------------------------------- 真实 DEM --


def test_load_real_benchmark_dem():
    dem = elevation.load_dem(BENCHMARK_DEM)
    assert dem.lons.size == 57 and dem.lats.size == 57
    assert dem.elevation.shape == (57, 57)
    assert dem.lon_range == pytest.approx((113.0, 120.0))
    assert dem.lat_range == pytest.approx((36.0, 43.0))
    assert np.all(np.diff(dem.lons) > 0) and np.all(np.diff(dem.lats) > 0)
    assert np.all(np.isfinite(dem.elevation))
    assert dem.source == str(BENCHMARK_DEM)


def test_real_benchmark_dem_resolution_is_0125():
    dem = elevation.load_dem(BENCHMARK_DEM)
    assert np.allclose(np.diff(dem.lons), 0.125)
    assert np.allclose(np.diff(dem.lats), 0.125)


def test_real_dem_values_match_raw_variable():
    """高程按 (lat, lon) 标准化，与原始变量（lon, lat）的转置一致。"""
    with netCDF4.Dataset(BENCHMARK_DEM, "r") as ds:
        raw = np.asarray(ds.variables["elevation"][:], dtype=float)
    dem = elevation.load_dem(BENCHMARK_DEM)
    assert np.array_equal(dem.elevation, raw.T)


def test_missing_dem_file_rejected(tmp_path):
    with pytest.raises(InvalidRequestError):
        elevation.load_dem(tmp_path / "nope.nc")


# ----------------------------------------------------------- 方向规范化 --


def test_lat_descending_is_flipped(tmp_path):
    lats = np.array([39.0, 38.0, 37.0])
    lons = np.array([113.0, 114.0, 115.0])
    path = _write_dem(tmp_path / "lat_desc.nc", lons=lons, lats=lats,
                      elevation_data=_grid_values(lats, lons))
    dem = elevation.load_dem(path)
    assert np.allclose(dem.lats, [37.0, 38.0, 39.0])
    assert np.allclose(dem.elevation, _grid_values(dem.lats, dem.lons))


def test_lon_descending_is_flipped(tmp_path):
    lats = np.array([37.0, 38.0, 39.0])
    lons = np.array([115.0, 114.0, 113.0])
    path = _write_dem(tmp_path / "lon_desc.nc", lons=lons, lats=lats,
                      elevation_data=_grid_values(lats, lons))
    dem = elevation.load_dem(path)
    assert np.allclose(dem.lons, [113.0, 114.0, 115.0])
    assert np.allclose(dem.elevation, _grid_values(dem.lats, dem.lons))


def test_both_descending_are_flipped(tmp_path):
    lats = np.array([39.0, 38.0, 37.0])
    lons = np.array([115.0, 114.0, 113.0])
    path = _write_dem(tmp_path / "both_desc.nc", lons=lons, lats=lats,
                      elevation_data=_grid_values(lats, lons))
    dem = elevation.load_dem(path)
    assert np.allclose(dem.lons, [113.0, 114.0, 115.0])
    assert np.allclose(dem.lats, [37.0, 38.0, 39.0])
    assert np.allclose(dem.elevation, _grid_values(dem.lats, dem.lons))


def test_elevation_lon_lat_dim_order_supported(tmp_path):
    lats = np.array([37.0, 38.0])
    lons = np.array([113.0, 114.0, 115.0])
    path = _write_dem(tmp_path / "lon_lat.nc", lons=lons, lats=lats,
                      elevation_data=_grid_values(lats, lons), dim_order=("lon", "lat"))
    dem = elevation.load_dem(path)
    assert dem.elevation.shape == (2, 3)
    assert np.allclose(dem.elevation, _grid_values(lats, lons))


def test_elevation_lat_lon_dim_order_supported(tmp_path):
    lats = np.array([37.0, 38.0])
    lons = np.array([113.0, 114.0, 115.0])
    path = _write_dem(tmp_path / "lat_lon.nc", lons=lons, lats=lats,
                      elevation_data=_grid_values(lats, lons), dim_order=("lat", "lon"))
    dem = elevation.load_dem(path)
    assert dem.elevation.shape == (2, 3)
    assert np.allclose(dem.elevation, _grid_values(lats, lons))


# --------------------------------------------------------------- 异常数据 --


def test_nan_elevation_rejected(tmp_path):
    lats = np.array([37.0, 38.0])
    lons = np.array([113.0, 114.0])
    data = _grid_values(lats, lons)
    data[0, 0] = np.nan
    path = _write_dem(tmp_path / "nan.nc", lons=lons, lats=lats, elevation_data=data)
    with pytest.raises(InvalidRequestError, match="NaN"):
        elevation.load_dem(path)


def test_inf_elevation_rejected(tmp_path):
    lats = np.array([37.0, 38.0])
    lons = np.array([113.0, 114.0])
    data = _grid_values(lats, lons)
    data[1, 1] = np.inf
    path = _write_dem(tmp_path / "inf.nc", lons=lons, lats=lats, elevation_data=data)
    with pytest.raises(InvalidRequestError, match="NaN 或 Inf"):
        elevation.load_dem(path)


def test_non_monotonic_coordinate_rejected(tmp_path):
    path = _write_dem(tmp_path / "mono.nc", lons=np.array([113.0, 115.0, 114.0]),
                      lats=np.array([37.0, 38.0]),
                      elevation_data=np.zeros((2, 3)))
    with pytest.raises(InvalidRequestError, match="单调"):
        elevation.load_dem(path)


def test_duplicate_coordinate_rejected(tmp_path):
    path = _write_dem(tmp_path / "dup.nc", lons=np.array([113.0, 113.0, 114.0]),
                      lats=np.array([37.0, 38.0]),
                      elevation_data=np.zeros((2, 3)))
    with pytest.raises(InvalidRequestError, match="单调"):
        elevation.load_dem(path)


# --------------------------------------------------------------- 变量识别 --


def test_ambiguous_elevation_variable_rejected(tmp_path):
    path = tmp_path / "ambiguous.nc"
    lats = np.array([37.0, 38.0])
    lons = np.array([113.0, 114.0])
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("lon", lons.size)
        ds.createDimension("lat", lats.size)
        ds.createVariable("lon", "f8", ("lon",))[:] = lons
        ds.createVariable("lat", "f8", ("lat",))[:] = lats
        ds.createVariable("elevation", "f8", ("lat", "lon"))[:] = _grid_values(lats, lons)
        ds.createVariable("z", "f8", ("lat", "lon"))[:] = _grid_values(lats, lons)
    with pytest.raises(InvalidRequestError, match="无法唯一确定 DEM 高程变量"):
        elevation.load_dem(path)


def test_missing_elevation_variable_rejected(tmp_path):
    path = tmp_path / "noelev.nc"
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("lon", 2)
        ds.createDimension("lat", 2)
        ds.createVariable("lon", "f8", ("lon",))[:] = [113.0, 114.0]
        ds.createVariable("lat", "f8", ("lat",))[:] = [37.0, 38.0]
    with pytest.raises(InvalidRequestError):
        elevation.load_dem(path)


def test_elevation_with_unrelated_dims_rejected(tmp_path):
    path = tmp_path / "unrelated.nc"
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("lon", 2)
        ds.createDimension("lat", 2)
        ds.createDimension("row", 2)
        ds.createDimension("col", 2)
        ds.createVariable("lon", "f8", ("lon",))[:] = [113.0, 114.0]
        ds.createVariable("lat", "f8", ("lat",))[:] = [37.0, 38.0]
        ds.createVariable("elevation", "f8", ("row", "col"))[:] = np.zeros((2, 2))
    with pytest.raises(InvalidRequestError):
        elevation.load_dem(path)


def test_ambiguous_longitude_variable_rejected(tmp_path):
    path = tmp_path / "amb_lon.nc"
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("lon", 2)
        ds.createDimension("lat", 2)
        ds.createVariable("lon", "f8", ("lon",))[:] = [113.0, 114.0]
        ds.createVariable("longitude", "f8", ("lon",))[:] = [113.0, 114.0]
        ds.createVariable("lat", "f8", ("lat",))[:] = [37.0, 38.0]
        ds.createVariable("elevation", "f8", ("lat", "lon"))[:] = np.zeros((2, 2))
    with pytest.raises(InvalidRequestError, match="无法唯一确定经度坐标变量"):
        elevation.load_dem(path)


def test_missing_latitude_variable_rejected(tmp_path):
    path = tmp_path / "no_lat.nc"
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("lon", 2)
        ds.createDimension("lat", 2)
        ds.createVariable("lon", "f8", ("lon",))[:] = [113.0, 114.0]
        ds.createVariable("elevation", "f8", ("lat", "lon"))[:] = np.zeros((2, 2))
    with pytest.raises(InvalidRequestError, match="纬度"):
        elevation.load_dem(path)


# --------------------------------------------------------------- 最近邻采样 --


def test_sample_nearest_returns_exact_node_values():
    dem = elevation.load_dem(BENCHMARK_DEM)
    lons = np.array([113.0, 116.5, 120.0])
    lats = np.array([36.0, 39.5, 43.0])
    heights = elevation.sample_nearest(dem, lons, lats)
    lon_index = np.array([0, 28, 56])
    lat_index = np.array([0, 28, 56])
    assert np.allclose(heights, dem.elevation[lat_index, lon_index])


def test_sample_nearest_picks_closest_node():
    dem = elevation.load_dem(BENCHMARK_DEM)
    # 113.10 距 113.125 最近（0.025），距 113.000 为 0.100
    heights = elevation.sample_nearest(dem, np.array([113.10]), np.array([36.10]))
    assert heights[0] == dem.elevation[1, 1]


def test_sample_nearest_out_of_range_lon_rejected():
    dem = elevation.load_dem(BENCHMARK_DEM)
    with pytest.raises(InvalidRequestError, match="exceeds DEM coverage"):
        elevation.sample_nearest(dem, np.array([112.0]), np.array([37.0]))


def test_sample_nearest_out_of_range_lat_rejected():
    dem = elevation.load_dem(BENCHMARK_DEM)
    with pytest.raises(InvalidRequestError, match="exceeds DEM coverage"):
        elevation.sample_nearest(dem, np.array([115.0]), np.array([35.0]))


def test_sample_nearest_boundary_tolerance():
    dem = elevation.load_dem(BENCHMARK_DEM)
    inside = elevation.sample_nearest(
        dem, np.array([113.0 - 1e-10]), np.array([43.0 + 1e-10]))
    assert np.isfinite(inside[0])
    with pytest.raises(InvalidRequestError):
        elevation.sample_nearest(dem, np.array([113.0 - 1e-6]), np.array([37.0]))


def test_sample_nearest_rejects_non_finite_input():
    dem = elevation.load_dem(BENCHMARK_DEM)
    with pytest.raises(InvalidRequestError):
        elevation.sample_nearest(dem, np.array([np.nan]), np.array([37.0]))
    with pytest.raises(InvalidRequestError):
        elevation.sample_nearest(dem, np.array([115.0]), np.array([np.inf]))


def test_sample_nearest_rejects_length_mismatch():
    dem = elevation.load_dem(BENCHMARK_DEM)
    with pytest.raises(InvalidRequestError, match="不一致"):
        elevation.sample_nearest(dem, np.array([115.0, 116.0]), np.array([37.0]))


def test_sample_nearest_rejects_empty_points():
    dem = elevation.load_dem(BENCHMARK_DEM)
    with pytest.raises(InvalidRequestError):
        elevation.sample_nearest(dem, np.array([]), np.array([]))
