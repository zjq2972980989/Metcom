"""P2 readers 测试：CRA40 风格多变量分组、MERRA2 日文件切片与 NaN 修剪。"""

import sys
from pathlib import Path

import netCDF4
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from metcom.retrieval.pipeline import RetrievalOptions, run_retrieval
from metcom.retrieval.readers.grib_sources import _group_by_timestamp
from metcom.retrieval.readers.merra2 import iter_hours as merra2_iter_hours
from metcom.utils.io import get_doy, read_result_file

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EGM_0250 = PROJECT_ROOT.parent / "METCOM_ver3.1/03_DataProcessing/METCOM_ERA5/dependentDataFile/geoidheight_EGM2008_0250.mat"
EGM_MERRA2 = PROJECT_ROOT.parent / "METCOM_ver3.1/03_DataProcessing/METCOM_MERRA2/dependentDataFile/geoidheight_EGM2008_0500_0625.mat"

N_LEV = 37
LON = np.arange(113.0, 115.01, 0.25)
LAT = np.arange(35.0, 37.01, 0.25)


def _write_var_nc(path, var_name, plev, spatial):
    """写单变量 NC（转换工具输出风格：gh/t/r/q + isobaricInhPa，纬度降序）。"""
    n_lev = plev.size
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("longitude", LON.size)
        ds.createDimension("latitude", LAT.size)
        ds.createDimension("isobaricInhPa", n_lev)
        v = ds.createVariable("longitude", "f8", ("longitude",))
        v[:] = LON  # 0~360 约定（东经 113° 即 113）
        v = ds.createVariable("latitude", "f8", ("latitude",))
        v[:] = LAT[::-1]    # 90→-90 降序约定
        v = ds.createVariable("isobaricInhPa", "f4", ("isobaricInhPa",))
        v[:] = plev
        arr = np.empty((LON.size, LAT.size, n_lev))
        geo_h = np.linspace(-100.0, 28000.0, n_lev)
        for k in range(n_lev):
            base = {"gh": geo_h[k], "t": 285.0 - 5.5 * k,
                    "r": np.clip(75.0 - 1.1 * k, 1, 100),
                    "q": max(0.004 - 8e-5 * k, 1e-6)}[var_name]
            arr[:, :, k] = base * (1.0 + 0.02 * spatial)
        v = ds.createVariable(var_name, "f4",
                              ("longitude", "latitude", "isobaricInhPa"))
        v[:] = arr.astype(np.float32)


@pytest.fixture()
def cra40_data(tmp_path):
    """CRA40 风格：一天文件夹 + 4 变量 × 2 时次共 8 个 NC。"""
    day = tmp_path / "20240101"
    day.mkdir()
    plev = np.linspace(1000.0, 1.0, N_LEV)
    spatial = (np.sin(np.deg2rad(LAT))[None, :]
               + np.cos(np.deg2rad(LON))[:, None])
    for ts in ("2024010100", "2024010106"):
        for tag, var in (("GPH", "gh"), ("RHU", "r"), ("SHU", "q"), ("TEM", "t")):
            name = f"CRA40_{tag}_{ts}_GLB_0P25_HOUR_V1_0_0.nc"
            _write_var_nc(day / name, var, plev, spatial)
    return tmp_path


def test_group_by_timestamp(cra40_data):
    groups = _group_by_timestamp(_cfg_cra40(), cra40_data / "20240101")
    assert sorted(groups) == ["2024010100", "2024010106"]
    assert set(groups["2024010100"]) == {"GPH", "RHU", "SHU", "TEM"}


def _cfg_cra40():
    from metcom.retrieval.readers.grib_sources import _CFG
    return _CFG["cra40"]


def test_cra40_end_to_end(cra40_data, tmp_path):
    if not EGM_0250.is_file():
        pytest.skip("原软件包 EGM2008 文件不在本机")
    site_file = tmp_path / "sites.txt"
    site_file.write_text("A000    36.125000   114.625000     51.500\n", encoding="utf-8")
    options = RetrievalOptions(
        source_key="cra40",
        data_path=str(cra40_data),
        output_path=str(tmp_path / "out"),
        site_file=str(site_file),
        start_time="20240101",
        end_time="20240101",
        spatial_resolution=(0.25, 0.25),
        egm_file=str(EGM_0250),
    )
    stats = run_retrieval(options)
    assert stats["hours"] == 2 and stats["outputs"] == 2
    # getDOY 以 GPH 文件名（filelist{1}）为准
    for ts in ("2024010100", "2024010106"):
        f = tmp_path / "out" / get_doy(ts)[0] / f"ztd_{ts}_sit_cra40.txt"
        assert f.is_file(), f
    columns, data = read_result_file(tmp_path / "out" / "2024001" / "ztd_2024010100_sit_cra40.txt")
    assert np.all(np.isfinite(data[:, 1:]))


# ------------------------------------------------------------------ MERRA2 --
def _write_merra2_day(path, with_below_ground_nan=False):
    """MERRA2 风格：一天一文件，(time, lev, lat, lon) 存储。

    with_below_ground_nan: 模拟高地形下地下 5 层缺测（NaN 位于数组头部，
    即气压层自底层向顶层存储时的地下层，与 fun_Merra2_site 的 rmmissing 假设一致）。
    """
    n_lev = 42
    lon = np.arange(112.0, 114.01, 0.625)
    lat = np.arange(34.0, 38.01, 0.5)
    plev = np.linspace(1000.0, 0.1, n_lev)  # 低层在前
    n_time = 4
    geo_h = np.linspace(-100.0, 65000.0, n_lev)
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("time", n_time)
        ds.createDimension("lev", n_lev)
        ds.createDimension("lat", lat.size)
        ds.createDimension("lon", lon.size)
        for name, vals in (("time", np.arange(n_time)), ("lev", plev),
                           ("lat", lat), ("lon", lon)):
            v = ds.createVariable(name, "f8", (name,))
            v[:] = vals
        for name, base_fn in (("H", lambda k: geo_h[k]),
                              ("T", lambda k: max(180.0, 288.15 - 6.5
                                                  * (geo_h[k] - geo_h[0]) / 1000.0)),
                              ("QV", lambda k: max(0.004 - 6e-5 * k, 1e-6))):
            arr = np.empty((n_time, n_lev, lat.size, lon.size))
            for k in range(n_lev):
                arr[:, k, :, :] = base_fn(k)
            if with_below_ground_nan:
                arr[:, :5, :, :] = np.nan
            v = ds.createVariable(name, "f4", ("time", "lev", "lat", "lon"))
            v[:] = arr.astype(np.float32)
    return lon, lat, plev


def test_merra2_iter_hours(tmp_path):
    day_path = tmp_path / "2024" / "MERRA2_400.inst6_3d_ana_Np.20240101.SUB.nc"
    day_path.parent.mkdir()
    _write_merra2_day(day_path)
    import datetime as _dt
    entries = list(merra2_iter_hours(tmp_path, _dt.date(2024, 1, 1), _dt.date(2024, 1, 1)))
    assert [ts for ts, _ in entries] == ["2024010100", "2024010106", "2024010112", "2024010118"]
    td = entries[0][1]
    assert td.pre.size == 42
    assert td.geo.shape == (td.lon.size, td.lat.size, 42)


def test_merra2_end_to_end(tmp_path):
    """无缺测数据的完整流程：海平面站点结果应全部有限且 Ps 量级合理。"""
    if not EGM_MERRA2.is_file():
        pytest.skip("原软件包 EGM2008 文件不在本机")
    year_dir = tmp_path / "data" / "2024"
    year_dir.mkdir(parents=True)
    _write_merra2_day(year_dir / "MERRA2_400.inst6_3d_ana_Np.20240101.SUB.nc")

    site_file = tmp_path / "sites.txt"
    site_file.write_text("A000    36.000000   113.000000     51.500\n", encoding="utf-8")
    options = RetrievalOptions(
        source_key="merra2",
        data_path=str(tmp_path / "data"),
        output_path=str(tmp_path / "out"),
        site_file=str(site_file),
        start_time="20240101",
        end_time="20240101",
        spatial_resolution=(0.5, 0.625),
        egm_file=str(EGM_MERRA2),
    )
    stats = run_retrieval(options)
    assert stats["hours"] == 4 and stats["outputs"] == 4
    columns, data = read_result_file(tmp_path / "out" / "2024001" / "ztd_2024010100_sit_merra2.txt")
    assert np.all(np.isfinite(data[:, 1:]))
    ps = data[0, columns.index("Ps(hpa)")]
    assert 900.0 < ps < 1100.0


def test_merra2_nan_trim_semantics(tmp_path):
    """NaN 修剪语义：带地下 NaN 的柱 + 高站点 ≡ 手工去掉 NaN 层后的柱。"""
    from metcom.retrieval.site_retrieval import TempData, fun_site
    from metcom.sources import SOURCES
    from metcom.utils.egm2008 import EgmGrid

    spec = SOURCES["merra2"]
    n_lon, n_lat, n_lev = 3, 3, 42
    lon = np.array([112.0, 112.625, 113.25])
    lat = np.array([35.0, 36.0, 37.0])
    pre = np.linspace(1000.0, 0.1, n_lev)
    geo_h = np.linspace(-100.0, 65000.0, n_lev)
    base_geo = np.tile(geo_h[None, None, :], (n_lon, n_lat, 1))
    base_tem = np.full((n_lon, n_lat, n_lev), 280.0)
    base_sh = np.full((n_lon, n_lat, n_lev), 0.003)

    def make(with_nan):
        geo, tem, sh = base_geo.copy(), base_tem.copy(), base_sh.copy()
        if with_nan:
            tem[:, :, :5] = np.nan
            sh[:, :, :5] = np.nan
        return TempData(geo=geo, tem=tem, sh=sh, rh=None, pre=pre.copy(),
                        lon=lon, lat=lat)

    egm = EgmGrid(values=np.full((577, 361), 30.0),
                  lon_axis=np.linspace(-180, 180, 577),
                  lat_axis=np.linspace(-90, 90, 361))
    hell = 9000.0  # 高于修剪后最低层(~7.8 km)，进入内插分支
    r_nan = fun_site((36.0, 112.625, hell), "A000", make(True), spec, egm)

    # 手工等价操作：去掉 NaN 的 5 层（pre/geo/tem/sh 同步切片）
    td_manual = TempData(geo=base_geo[:, :, 5:], tem=base_tem[:, :, 5:],
                         sh=base_sh[:, :, 5:], rh=None, pre=pre[5:],
                         lon=lon, lat=lat)
    r_manual = fun_site((36.0, 112.625, hell), "A000", td_manual, spec, egm)
    for key in ("ZTD", "ZWD", "ZHD", "PWV", "Tm", "Ps"):
        assert r_nan[key] == pytest.approx(r_manual[key], rel=1e-12), key
