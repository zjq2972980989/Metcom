"""CUSTOM 格网正高/椭球高站点文件：与 Python gridgen 原实现的一致性测试。

本文件只覆盖**离线**契约（不联网、不写 web/data）：
  - DEM 有效节点筛选（NaN 缺测节点按原实现剔除）；
  - Python 原生 A000… 站号与 ``%4s %12.6f %12.6f %10.3f`` 定宽文本；
  - 站点坐标/排序与 ``metcom.gridgen.orth_to_ell.process_dem_file`` 逐位一致；
  - 正高文件与椭球高文件逐行对应，且 h = H + N（按输出精度容差）；
  - 10000 点上限（超限拒绝、不静默截断）；
  - DEM 覆盖范围错误、缺失对应分辨率 EGM2008 的明确错误；
  - 任务目录隔离与临时文件清理。

真实 ERA5 3×3 反演验收由 ``test_custom_grid_task.py`` 的 module 级 fixture 承担。
"""

from __future__ import annotations

from pathlib import Path

import netCDF4
import numpy as np
import pytest

from config import PROJECT_ROOT, get_settings
from engine import adapter
from metcom.gridgen.orth_to_ell import process_dem_file
from metcom.utils.fmt import fmt_fixed_width
from services import egm_gridgen_adapter, grid_service, validation
from services.errors import InvalidRequestError, TaskExecutionError

REAL_DEPENDENT_DIR = PROJECT_ROOT / "web" / "data" / "dependent"
ERA5_EGM = REAL_DEPENDENT_DIR / "egm2008" / "geoidheight_EGM2008_0250.mat"
RECT_DEM = grid_service.DEFAULT_DEM_PATH

_FMT_SPECS = [(4, None), (12, 6), (12, 6), (10, 3)]


@pytest.fixture(autouse=True)
def real_egm_dir(monkeypatch):
    """把 EGM2008 目录指向真实只读依赖目录；运行期数据目录由 conftest 隔离。"""
    monkeypatch.setenv("METCOM_WEB_METCOM_DEPENDENT_DIR", str(REAL_DEPENDENT_DIR))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def egm_cache(tmp_path_factory) -> Path:
    """模块级共享 EGM2008 文本缓存目录（避免每用例重复生成）。"""
    return tmp_path_factory.mktemp("egm-gridgen-cache")


def _write_dem(path: Path, lons: np.ndarray, lats: np.ndarray,
               elev: np.ndarray) -> Path:
    """写合成 DEM：elevation 故意用 (lon, lat) 维序，检验按维度名定向。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with netCDF4.Dataset(path, "w", format="NETCDF4") as ds:
        ds.createDimension("lon", lons.size)
        ds.createDimension("lat", lats.size)
        ds.createVariable("lon", "f8", ("lon",))[:] = lons
        ds.createVariable("lat", "f8", ("lat",))[:] = lats
        ds.createVariable("elevation", "f8", ("lon", "lat"))[:] = elev.T
    return path


def _rows(path: Path) -> list[list[str]]:
    return [line.split() for line in path.read_text(encoding="utf-8").splitlines()]


def _build(tmp_path: Path, egm_cache: Path, **overrides) -> dict:
    params = dict(
        dataset="era5", lon_min=115.0, lon_max=115.5,
        lat_min=37.0, lat_max=37.5,
        orthometric_path=tmp_path / "custom_grid_orthometric.txt",
        ellipsoidal_path=tmp_path / "custom_grid_ellipsoidal.txt",
        cache_dir=egm_cache,
    )
    params.update(overrides)
    return grid_service.build_grid_height_files(**params)


# ------------------------------------------------------------ 编号与文本格式 --


def test_station_numbering_and_fixed_width_format(tmp_path, egm_cache):
    meta = _build(tmp_path, egm_cache)
    assert meta["grid"] == {"n_lon": 3, "n_lat": 3, "points": 9}

    ortho = _rows(Path(meta["orthometric_site_file"]))
    ellip = _rows(Path(meta["ellipsoidal_site_file"]))
    names = [row[0] for row in ortho]
    assert names == [f"A{index:03d}" for index in range(9)]      # Python 原生编号
    assert len(set(names)) == 9                                  # 不重复

    # 逐字符等于 Python 定宽输出 %4s %12.6f %12.6f %10.3f
    for row in ortho + ellip:
        name, lat, lon, h = row[0], float(row[1]), float(row[2]), float(row[3])
        expected = fmt_fixed_width([name, lat, lon, h], _FMT_SPECS, sep=" ")
        assert " ".join(row) == " ".join(expected.split())


def test_station_latlon_order_is_lat_major(tmp_path, egm_cache):
    meta = _build(tmp_path, egm_cache)
    rows = _rows(Path(meta["orthometric_site_file"]))
    lats = [float(r[1]) for r in rows]
    lons = [float(r[2]) for r in rows]
    assert lats == sorted(lats)
    assert (lats[0], lons[0]) == (37.0, 115.0)
    assert lons[:3] == [115.0, 115.25, 115.5]                    # 维度内经度升序


# ------------------------------------------------------------ DEM 有效节点 --


def test_valid_node_filtering_drops_missing_nodes(tmp_path, egm_cache):
    lons = 115.0 + 0.125 * np.arange(9)
    lats = 37.0 + 0.125 * np.arange(9)
    elev = np.full((lats.size, lons.size), 100.0)
    dropped = {(37.0, 115.0), (37.5, 115.5), (38.0, 116.0)}
    for la, lo in dropped:
        elev[list(lats).index(la), list(lons).index(lo)] = np.nan
    dem = _write_dem(tmp_path / "holes.nc", lons, lats, elev)

    meta = _build(tmp_path, egm_cache, dem_path=dem,
                  lon_min=115.0, lon_max=116.0, lat_min=37.0, lat_max=38.0)
    assert meta["grid"]["points"] == 25 - len(dropped)           # 5×5 降采样后剔除 3
    assert meta["grid"]["points"] == 22

    rows = _rows(Path(meta["orthometric_site_file"]))
    coords = {(float(r[1]), float(r[2])) for r in rows}
    assert coords.isdisjoint(dropped)
    assert all(float(r[3]) == 100.0 for r in rows)               # 其余节点高程不变
    assert [r[0] for r in rows] == [f"A{i:03d}" for i in range(22)]


def test_all_missing_dem_rejected(tmp_path, egm_cache):
    lons = 115.0 + 0.125 * np.arange(5)
    lats = 37.0 + 0.125 * np.arange(5)
    dem = _write_dem(tmp_path / "all_nan.nc", lons, lats,
                     np.full((5, 5), np.nan))
    with pytest.raises(InvalidRequestError, match="没有有效的 DEM 节点"):
        _build(tmp_path, egm_cache, dem_path=dem)


# ---------------------------------------------- 与 Python 原实现逐位一致 --


def test_output_is_byte_identical_to_process_dem_file(tmp_path, egm_cache):
    """同一 DEM（整幅）下，Web 输出必须与 process_dem_file 逐字节一致。"""
    meta = _build(tmp_path, egm_cache, lon_min=113.0, lon_max=120.0,
                  lat_min=36.0, lat_max=43.0)

    egm = egm_gridgen_adapter.load_gridgen_egm(ERA5_EGM, cache_dir=egm_cache)
    ref_ortho, ref_ellip = process_dem_file(
        RECT_DEM, egm.interpolator,
        output_prefix="ref", output_res=0.25, base_res=0.125,
        output_dir=tmp_path / "ref", max_stations=validation.MAX_GRID_POINTS,
    )

    assert meta["grid"]["points"] == 841
    assert Path(meta["orthometric_site_file"]).read_bytes() == ref_ortho.read_bytes()
    assert Path(meta["ellipsoidal_site_file"]).read_bytes() == ref_ellip.read_bytes()


def test_orthometric_and_ellipsoidal_rows_correspond(tmp_path, egm_cache):
    meta = _build(tmp_path, egm_cache)
    ortho = _rows(Path(meta["orthometric_site_file"]))
    ellip = _rows(Path(meta["ellipsoidal_site_file"]))

    assert len(ortho) == len(ellip) == meta["grid"]["points"]
    for o, e in zip(ortho, ellip):
        assert o[:3] == e[:3]                                    # 名称/纬度/经度逐行一致
        assert float(e[3]) >= float(o[3]) - 100.0                # 椭球高 = H + N（N 有界）

    egm = egm_gridgen_adapter.load_gridgen_egm(ERA5_EGM, cache_dir=egm_cache)
    lats = np.array([float(r[1]) for r in ortho])
    lons = np.array([float(r[2]) for r in ortho])
    H = np.array([float(r[3]) for r in ortho])
    h = np.array([float(r[3]) for r in ellip])
    lon0360 = np.where(lons < 0.0, lons + 360.0, lons)
    N = np.asarray(egm.interpolator(np.column_stack([lats, lon0360])), dtype=float)

    # H 与 h 各按 1e-3 舍入，故 |(h - H) - N| ≤ 2e-3
    assert np.all(np.isfinite(N))
    assert float(np.max(np.abs((h - H) - N))) <= 2e-3


# ------------------------------------------------------------ 上限与错误 --


def test_point_cap_rejected_without_truncation(tmp_path, egm_cache):
    lons = 110.0 + 0.125 * np.arange(241)          # 110..140
    lats = 30.0 + 0.125 * np.arange(241)           # 30..60
    dem = _write_dem(tmp_path / "big.nc", lons, lats, np.zeros((241, 241)))

    with pytest.raises(InvalidRequestError, match="10000"):
        _build(tmp_path, egm_cache, dem_path=dem,
               lon_min=110.0, lon_max=140.0, lat_min=30.0, lat_max=60.0)
    assert not (tmp_path / "custom_grid_orthometric.txt").exists()
    assert not any(p.name.startswith(".gridgen_tmp_") for p in tmp_path.iterdir())


def test_dem_coverage_error_is_explicit(tmp_path, egm_cache):
    with pytest.raises(InvalidRequestError, match="exceeds DEM coverage"):
        _build(tmp_path, egm_cache, lon_min=112.0, lon_max=115.0)


def test_missing_egm_file_errors_without_fallback(tmp_path, egm_cache, monkeypatch):
    empty = tmp_path / "empty-dependent"
    (empty / "egm2008").mkdir(parents=True)
    monkeypatch.setenv("METCOM_WEB_METCOM_DEPENDENT_DIR", str(empty))
    get_settings.cache_clear()

    with pytest.raises(InvalidRequestError) as excinfo:
        _build(tmp_path, egm_cache)
    assert "EGM2008" in str(excinfo.value)


def test_unsupported_resolution_never_falls_back_to_coarse(tmp_path, egm_cache):
    # 非 0.25° 数据源直接拒绝（不会用 0.25° 粗分辨率文件顶替）
    with pytest.raises(InvalidRequestError, match="0.25"):
        _build(tmp_path, egm_cache, dataset="crav15",
               lon_min=115.0, lon_max=115.5, lat_min=37.0, lat_max=37.5)

    # 细分辨率选择分支：明确报错，不回退
    era5 = adapter.source_spec("era5")
    with pytest.raises(InvalidRequestError, match="细分辨率"):
        grid_service._resolve_gridgen_egm_for_resolution(era5, 0.125)


# ------------------------------------------------------------ 目录隔离 --


def test_task_directory_isolation_and_cleanup(tmp_path, egm_cache, monkeypatch):
    task_a = tmp_path / "tasks" / "a"
    task_b = tmp_path / "tasks" / "b"
    task_b.mkdir(parents=True)
    marker = task_b / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    meta = _build(tmp_path, egm_cache,
                  orthometric_path=task_a / "custom_grid_orthometric.txt",
                  ellipsoidal_path=task_a / "custom_grid_ellipsoidal.txt")
    assert Path(meta["orthometric_site_file"]).parent == task_a
    assert not any(p.name.startswith(".gridgen_tmp_") for p in task_a.iterdir())
    assert not any(p.name.startswith(".gridgen_tmp_") for p in task_b.iterdir())
    assert marker.read_text(encoding="utf-8") == "keep"
    assert sorted(p.name for p in task_b.iterdir()) == ["keep.txt"]

    # 运行期失败：临时目录必须被清理，且不留下半成品文件
    def _boom(*args, **kwargs):
        raise OSError("simulated gridgen failure")

    monkeypatch.setattr(grid_service, "process_dem_file", _boom)
    with pytest.raises(TaskExecutionError):
        _build(tmp_path, egm_cache,
               orthometric_path=task_a / "fail_orthometric.txt",
               ellipsoidal_path=task_a / "fail_ellipsoidal.txt")
    assert not any(p.name.startswith(".gridgen_tmp_") for p in task_a.iterdir())
    assert not (task_a / "fail_orthometric.txt").exists()
    assert not (task_a / "fail_ellipsoidal.txt").exists()

