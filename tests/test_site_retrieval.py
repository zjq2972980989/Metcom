"""site_retrieval（fun_*_site 统一复刻）与定位函数测试。"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from metcom.retrieval.core import fun_calculation_site
from metcom.retrieval.site_retrieval import (
    TempData, find_latitude_position, find_longitude_position, fun_site,
)
from metcom.sources import SOURCES
from metcom.utils.egm2008 import EgmGrid

SR = (0.25, 0.25)


def make_fake_egm(value=30.0, sr=0.25):
    n_lon = int(round(360.0 / sr)) + 1
    n_lat = int(round(180.0 / sr)) + 1
    return EgmGrid(
        values=np.full((n_lon, n_lat), value),
        lon_axis=np.linspace(-180.0, 180.0, n_lon),
        lat_axis=np.linspace(-90.0, 90.0, n_lat),
    )


def make_uniform_tempdata(n_lev=37, geo_h0=-500.0, geo_h1=28000.0):
    """常值气象场合成 TempData（geoH 线性升序，T/rh/sh 常数）。"""
    lon = np.arange(113.0, 115.01, SR[1])
    lat = np.arange(35.0, 37.01, SR[0])
    n_lon, n_lat = lon.size, lat.size
    geo_h = np.linspace(geo_h0, geo_h1, n_lev)
    geo = np.tile(geo_h[None, None, :] * 9.80665, (n_lon, n_lat, 1))  # 位势 m²/s²
    tem = np.full((n_lon, n_lat, n_lev), 280.0)
    rh = np.full((n_lon, n_lat, n_lev), 60.0)
    sh = np.full((n_lon, n_lat, n_lev), 0.003)
    pre = np.linspace(1013.0, 30.0, n_lev)
    return TempData(geo=geo, tem=tem, sh=sh, rh=rh, pre=pre, lon=lon, lat=lat)


def test_find_positions():
    lat_range = np.linspace(-90.0, 90.0, 721)
    lon_range = np.linspace(-180.0, 180.0, 1441)
    raw_lat, egm_lat = find_latitude_position(36.125, lat_range, 0.25)
    # 36.125 位于 36.0(idx 504) 与 36.25(idx 505) 之间
    assert raw_lat == [504, 505]
    assert egm_lat == [504, 505]
    raw_lon, egm_lon = find_longitude_position(114.625, lon_range, 0.25)
    assert raw_lon == [1178, 1179]  # (114.5+180)/0.25 = 1178
    # 边界：站点恰在最大值上 → 第二索引回退到第一索引
    raw_lat2, _ = find_latitude_position(90.0, lat_range, 0.25)
    assert raw_lat2 == [720, 720]
    with pytest.raises(ValueError):
        find_latitude_position(95.0, lat_range, 0.25)
    with pytest.raises(ValueError):
        find_longitude_position(-181.0, lon_range, 0.25)


def test_uniform_field_result_independent_of_position():
    """常值场：不同位置的差异仅来自椭球转换的纬度项（~1e-7 相对量级）。"""
    spec = SOURCES["era5"]
    egm = make_fake_egm(30.0)
    td = make_uniform_tempdata()
    r1 = fun_site((36.0, 114.0, 50.0), "A000", td, spec, egm)      # 恰在格点
    r2 = fun_site((36.125, 114.625, 50.0), "A001", td, spec, egm)  # 格点中央
    r3 = fun_site((36.1, 114.3, 50.0), "A002", td, spec, egm)      # 任意位置
    for key in ("ZTD", "ZWD", "ZHD", "PWV", "Tm", "Ps", "eE", "WVD"):
        assert r1[key] == pytest.approx(r2[key], rel=1e-5), key
        assert r1[key] == pytest.approx(r3[key], rel=1e-5), key

    # 与直接调用 fun_calculation_site 的一致性：站点恰在 G1(36.0, 114.0)，
    # 权重全部落在 G1 列上，应与手拼 G1 列的单列计算完全一致
    from metcom.utils.geodesy import geopotential_height_to_ellipsoidal
    geo_h = np.linspace(-500.0, 28000.0, 37)
    ell_g = np.asarray(geopotential_height_to_ellipsoidal(geo_h, 36.0)) - 30.0
    prof = fun_calculation_site(1, np.array([36.0] * 4), 50.0, td.pre, ell_g,
                                np.full(37, 280.0), np.full(37, 60.0),
                                np.full(37, 0.003), "none")
    assert r1["ZHD"] == pytest.approx(prof.zhd[0], rel=1e-10)
    assert r1["ZWD"] == pytest.approx(prof.zwd[0], rel=1e-10)
    assert r1["PWV"] == pytest.approx(prof.pwv[0], rel=1e-10)
    assert r1["Tm"] == pytest.approx(prof.tm[0], rel=1e-10)
    assert r1["Ps"] == pytest.approx(prof.pre[0], rel=1e-10)
    # 输出结构（对应 saveToText 字段）
    assert set(r1) == {"name", "lat", "lon", "h", "ZTD", "ZWD", "ZHD",
                       "WVD", "Tm", "PWV", "eE", "Ps"}
    assert r1["ZTD"] == pytest.approx(r1["ZHD"] + r1["ZWD"], rel=1e-12)


def test_bilinear_weights():
    """权重选择验证：站点恰在 G1 时其他角点的扰动不影响结果。"""
    spec = SOURCES["era5"]
    egm = make_fake_egm(30.0)
    lon_range, lat_range = make_uniform_tempdata().lon, make_uniform_tempdata().lat

    # 站点恰在格点 (36.0, 114.0) → dnlon1 = dnlat1 = 0，全部权重落在 G1
    td_a = make_uniform_tempdata()
    td_b = make_uniform_tempdata()
    li0 = np.where(lat_range <= 36.0)[0][-1]
    oi0 = np.where(lon_range <= 114.0)[0][-1]
    li1 = np.where(lat_range > 36.0)[0][0]
    oi1 = np.where(lon_range > 114.0)[0][0]
    td_a.tem[oi0, li0, :] = 290.0                       # 仅扰动 G1
    td_b.tem[oi0, li0, :] = 290.0
    td_b.tem[oi1, li0, :] = 250.0                       # 额外扰动其他角点
    td_b.tem[oi0, li1, :] = 250.0
    td_b.tem[oi1, li1, :] = 250.0
    r_a = fun_site((36.0, 114.0, 50.0), "A000", td_a, spec, egm)
    r_b = fun_site((36.0, 114.0, 50.0), "A000", td_b, spec, egm)
    for key in ("ZTD", "ZWD", "ZHD", "PWV", "Tm", "Ps"):
        assert r_a[key] == pytest.approx(r_b[key], rel=1e-12), key

    # 非零权重情形（严格恒等式）：双线性权重和为 1，场扰动线性叠加——
    # out(G1,G2 同时扰动) ≡ out(仅G1) + out(仅G2) − out(基线)，对所有输出键成立
    site_mid = (36.0, 114.0 + SR[1] / 2, 50.0)  # 恰在 G1-G2 经度中央，权重 (0.5, 0.5)
    td_0 = make_uniform_tempdata()
    td_c = make_uniform_tempdata()
    td_c.tem[oi0, li0, :] = 290.0   # 仅扰动 G1
    td_d = make_uniform_tempdata()
    td_d.tem[oi1, li0, :] = 290.0   # 仅扰动 G2
    td_cd = make_uniform_tempdata()
    td_cd.tem[oi0, li0, :] = 290.0
    td_cd.tem[oi1, li0, :] = 290.0
    r_0 = fun_site(site_mid, "A000", td_0, spec, egm)
    r_c = fun_site(site_mid, "A000", td_c, spec, egm)
    r_d = fun_site(site_mid, "A000", td_d, spec, egm)
    r_cd = fun_site(site_mid, "A000", td_cd, spec, egm)
    for key in ("ZTD", "ZWD", "ZHD", "PWV", "Tm", "Ps", "eE", "WVD"):
        assert r_cd[key] == pytest.approx(
            r_c[key] + r_d[key] - r_0[key], rel=1e-9), key


def test_out_of_range_site():
    spec = SOURCES["era5"]
    egm = make_fake_egm()
    td = make_uniform_tempdata()
    with pytest.raises(ValueError):
        fun_site((40.0, 114.0, 50.0), "A000", td, spec, egm)  # 纬度超出网格
