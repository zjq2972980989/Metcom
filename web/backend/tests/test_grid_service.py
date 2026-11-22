"""``services.grid_service`` 的离线测试（Grid Step 2）。

覆盖：坐标生成（与 ``_colon`` 同语义）、lat 外层 / lon 内层顺序、
真实 benchmark DEM 最近邻高程、site_file 格式与引擎兼容性、范围与写入异常。
不联网、不调用 retrieval / adapter 计算入口、不写 ``web/data``。
"""

from __future__ import annotations

import re
from pathlib import Path

import netCDF4
import numpy as np
import pytest

from config import PROJECT_ROOT
from metcom.utils.io import read_site_file
from services import elevation, grid_service, validation
from services.errors import InvalidRequestError, TaskExecutionError

BENCHMARK_DEM = PROJECT_ROOT / "benchmarks" / "DEM_Output" / "bth_dem_0125deg_rect.nc"

_LINE_RE = re.compile(r"^(G\d{6}) (-?\d+\.\d{6}) (-?\d+\.\d{6}) (-?\d+\.\d{3})$")


def _write_flat_dem(path: Path, height: float = 123.456) -> Path:
    """覆盖默认测试区域 115~115.5 / 37~37.5 的等高程 DEM。"""
    lons = np.array([115.0, 115.125, 115.25, 115.375, 115.5])
    lats = np.array([37.0, 37.125, 37.25, 37.375, 37.5])
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("lon", lons.size)
        ds.createDimension("lat", lats.size)
        ds.createVariable("lon", "f8", ("lon",))[:] = lons
        ds.createVariable("lat", "f8", ("lat",))[:] = lats
        ds.createVariable("elevation", "f8", ("lat", "lon"))[:] = np.full(
            (lats.size, lons.size), height)
    return path


# --------------------------------------------------------------- 坐标生成 --


def test_default_dem_path_is_repo_benchmark():
    assert grid_service.DEFAULT_DEM_PATH == BENCHMARK_DEM
    assert BENCHMARK_DEM.is_file()


def test_era5_coordinate_counts():
    lons, lats = grid_service.generate_grid_coordinates(
        dataset="era5", lon_min=73, lon_max=75, lat_min=35, lat_max=37)
    assert lons.size == 9 and lats.size == 9
    assert lons.size * lats.size == 81


def test_first_and_last_coordinates_follow_colon_semantics():
    lons, lats = grid_service.generate_grid_coordinates(
        dataset="era5", lon_min=73, lon_max=75, lat_min=35, lat_max=37)
    assert lons[0] == pytest.approx(73.0) and lons[-1] == pytest.approx(75.0)
    assert lats[0] == pytest.approx(35.0) and lats[-1] == pytest.approx(37.0)
    assert np.allclose(np.diff(lons), 0.25)
    assert np.allclose(np.diff(lats), 0.25)


def test_merra2_uses_05_lat_and_0625_lon():
    lons, lats = grid_service.generate_grid_coordinates(
        dataset="merra2", lon_min=113, lon_max=120, lat_min=36, lat_max=38)
    assert lats.size == 5 and lons.size == 12
    assert np.allclose(np.diff(lats), 0.5)
    assert np.allclose(np.diff(lons), 0.625)


def test_coordinate_counts_match_previous_stage_helper():
    """两种实现的格点数必须一致（同一公式），避免跨阶段口径漂移。"""
    cases = (
        ("era5", 73.0, 75.0, 35.0, 37.0),
        ("merra2", 113.0, 120.0, 36.0, 38.0),
        ("era5", 115.0, 116.0, 37.0, 38.0),
    )
    for dataset, lon_min, lon_max, lat_min, lat_max in cases:
        spec = validation.require_source(dataset)
        n_lon, n_lat, total = validation.grid_point_counts(
            spec, lon_min, lon_max, lat_min, lat_max)
        lons, lats = grid_service.generate_grid_coordinates(
            dataset=dataset, lon_min=lon_min, lon_max=lon_max,
            lat_min=lat_min, lat_max=lat_max)
        assert (lons.size, lats.size, lons.size * lats.size) == (n_lon, n_lat, total)


def test_unknown_dataset_rejected():
    with pytest.raises(InvalidRequestError):
        grid_service.generate_grid_coordinates(
            dataset="not-a-source", lon_min=115, lon_max=116, lat_min=37, lat_max=38)


# ---------------------------------------------------------------- site_file --


def _build(tmp_path: Path, **kwargs):
    params = dict(dataset="era5", lon_min=115.0, lon_max=115.5,
                  lat_min=37.0, lat_max=37.5, output_path=tmp_path / "grid.txt")
    params.update(kwargs)
    return grid_service.build_grid_site_file(**params)


def test_site_file_created_with_expected_line_count(tmp_path):
    meta = _build(tmp_path)
    lines = Path(meta["site_file"]).read_text(encoding="utf-8").splitlines()
    assert meta["grid"] == {"n_lon": 3, "n_lat": 3, "points": 9}
    assert len(lines) == 9


def test_site_file_first_line_format(tmp_path):
    meta = _build(tmp_path)
    first = Path(meta["site_file"]).read_text(encoding="utf-8").splitlines()[0]
    match = _LINE_RE.match(first)
    assert match is not None, first
    assert match.group(1) == "G000001"
    assert float(match.group(2)) == pytest.approx(37.0)   # lat
    assert float(match.group(3)) == pytest.approx(115.0)  # lon


def test_site_names_are_sequential(tmp_path):
    meta = _build(tmp_path)
    names = [line.split()[0] for line in
             Path(meta["site_file"]).read_text(encoding="utf-8").splitlines()]
    assert names == [f"G{index:06d}" for index in range(1, 10)]


def test_site_file_order_is_lat_outer_lon_inner(tmp_path):
    meta = _build(tmp_path)
    rows = [line.split() for line in
            Path(meta["site_file"]).read_text(encoding="utf-8").splitlines()]
    # 第 1 行 / 第 2 行：同纬度，经度 +0.25
    assert float(rows[1][1]) == pytest.approx(float(rows[0][1]))
    assert float(rows[1][2]) - float(rows[0][2]) == pytest.approx(0.25)
    # 第 4 行：纬度为下一行（0.25 后），经度回到最小
    assert float(rows[3][1]) - float(rows[0][1]) == pytest.approx(0.25)
    assert float(rows[3][2]) == pytest.approx(float(rows[0][2]))


def test_site_heights_match_dem_nearest_sampling(tmp_path):
    meta = _build(tmp_path)
    rows = [line.split() for line in
            Path(meta["site_file"]).read_text(encoding="utf-8").splitlines()]
    lons = np.array([float(row[2]) for row in rows])
    lats = np.array([float(row[1]) for row in rows])
    heights = np.array([float(row[3]) for row in rows])

    dem = elevation.load_dem(BENCHMARK_DEM)
    # site_file 高程保留 3 位小数，比较时给 1e-3 量级的绝对容差。
    assert np.allclose(heights, elevation.sample_nearest(dem, lons, lats), atol=1e-3)


def test_site_file_is_readable_by_engine_reader(tmp_path):
    meta = _build(tmp_path)
    sites = read_site_file(meta["site_file"])
    assert len(sites) == meta["grid"]["points"]
    assert sites[0].name == "G000001"
    assert sites[0].lat == pytest.approx(37.0)
    assert sites[0].lon == pytest.approx(115.0)
    assert all(site.height > -500.0 for site in sites)


def test_site_file_ends_with_newline(tmp_path):
    meta = _build(tmp_path)
    text = Path(meta["site_file"]).read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert "\r" not in text


def test_metadata_fields(tmp_path):
    meta = _build(tmp_path)
    assert meta["dataset"] == "era5"
    assert meta["resolution"] == {"lat_step": 0.25, "lon_step": 0.25}
    assert meta["bounds"] == {"lon_min": 115.0, "lon_max": 115.5,
                              "lat_min": 37.0, "lat_max": 37.5}
    assert meta["elevation_source"] == str(BENCHMARK_DEM)
    assert Path(meta["site_file"]).is_file()


def test_bounds_metadata_matches_actual_first_and_last_points(tmp_path):
    meta = _build(tmp_path, lon_min=115.0, lon_max=115.75,
                  lat_min=37.0, lat_max=37.75)
    lons, lats = grid_service.generate_grid_coordinates(
        dataset="era5", lon_min=115.0, lon_max=115.75,
        lat_min=37.0, lat_max=37.75)

    expected_requested = {
        "lon_min": 115.0, "lon_max": 115.75,
        "lat_min": 37.0, "lat_max": 37.75,
    }
    expected_effective = {
        "lon_min": float(lons[0]), "lon_max": float(lons[-1]),
        "lat_min": float(lats[0]), "lat_max": float(lats[-1]),
    }

    assert meta["requested_bounds"] == expected_requested
    assert meta["effective_bounds"] == expected_effective
    assert meta["bounds"] == expected_effective


def test_merra2_site_file_uses_source_resolution(tmp_path):
    meta = _build(tmp_path, dataset="merra2", lon_min=115.0, lon_max=117.5,
                  lat_min=37.0, lat_max=39.0)
    assert meta["grid"] == {"n_lon": 5, "n_lat": 5, "points": 25}
    assert meta["resolution"] == {"lat_step": 0.5, "lon_step": 0.625}
    rows = [line.split() for line in
            Path(meta["site_file"]).read_text(encoding="utf-8").splitlines()]
    assert float(rows[1][2]) - float(rows[0][2]) == pytest.approx(0.625)
    assert float(rows[5][1]) - float(rows[0][1]) == pytest.approx(0.5)


def test_custom_dem_path_is_used(tmp_path):
    dem_path = _write_flat_dem(tmp_path / "flat.nc", height=222.222)
    meta = _build(tmp_path, dem_path=dem_path)
    assert meta["elevation_source"] == str(dem_path)
    heights = [float(line.split()[3]) for line in
               Path(meta["site_file"]).read_text(encoding="utf-8").splitlines()]
    assert heights == [222.222] * 9


def test_output_directory_is_created(tmp_path):
    target = tmp_path / "nested" / "deeper" / "grid.txt"
    meta = _build(tmp_path, output_path=target)
    assert Path(meta["site_file"]).is_file()


def test_write_failure_raises_task_error(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    with pytest.raises(TaskExecutionError):
        _build(tmp_path, output_path=blocker / "grid.txt")


# --------------------------------------------------------------- 范围限制 --


def test_range_outside_dem_rejected(tmp_path):
    with pytest.raises(InvalidRequestError, match="exceeds DEM coverage"):
        _build(tmp_path, lon_min=112.0, lon_max=115.0)


def test_lat_range_outside_dem_rejected(tmp_path):
    with pytest.raises(InvalidRequestError, match="exceeds DEM coverage"):
        _build(tmp_path, lat_min=44.0, lat_max=45.0)


def test_oversized_grid_rejected_before_dem_read(tmp_path):
    with pytest.raises(InvalidRequestError, match="格点"):
        _build(tmp_path, lon_min=-180.0, lon_max=180.0,
               lat_min=-90.0, lat_max=90.0)


def test_failed_build_leaves_no_site_file(tmp_path):
    target = tmp_path / "grid.txt"
    with pytest.raises(InvalidRequestError):
        _build(tmp_path, lon_min=112.0, lon_max=115.0, output_path=target)
    assert not target.exists()
