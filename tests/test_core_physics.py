"""核心物理算法单元测试（fun_calculation_site F0–F14 的独立对照验证）。

测试中的"期望值"均在测试内用独立的写法重新推导（非复制被测代码路径），
用于捕捉迁移过程中的转录错误。
"""

import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from metcom.retrieval.core import (
    actual_vapor_pressure_hpa,
    fun_calculation_site,
    q_to_relative_humidity,
    rh_to_specific_humidity,
    saturation_vapor_pressure_hpa,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MATLAB_ROOT = PROJECT_ROOT.parent / "METCOM_ver3.1"


# ---------------------------------------------------------------- 基础公式 --
def test_magnus_saturation_vapor_pressure():
    # 20°C: 6.1078*10^(150/257.3) ≈ 23.38 hPa（公开查表值 ≈ 23.4）
    assert saturation_vapor_pressure_hpa(20.0) == pytest.approx(23.38, rel=1e-2)
    # -10°C 冰面: 6.1078*10^(-95/255.5) ≈ 2.60 hPa（查表 ≈ 2.60）
    assert saturation_vapor_pressure_hpa(-10.0) == pytest.approx(2.60, rel=1e-2)
    arr = saturation_vapor_pressure_hpa(np.array([20.0, -10.0]))
    assert arr.shape == (2,)


def test_actual_vapor_pressure():
    # RH=50% 时应为饱和值的一半
    e = actual_vapor_pressure_hpa(np.array([20.0]), np.array([50.0]))
    assert e[0] == pytest.approx(0.5 * float(saturation_vapor_pressure_hpa(20.0)))


def test_rh_to_q_and_back():
    # q = 0.622e/(p-0.378e) 的正反演一致性
    p = np.array([1000.0, 850.0])
    t = np.array([288.15, 278.15])
    rh = np.array([80.0, 60.0])
    q = rh_to_specific_humidity(p, t, rh)
    assert np.all(q > 0) and np.all(q < 0.05)
    rh2 = q_to_relative_humidity(p, t, q)
    assert np.allclose(rh2, rh, atol=1e-8)


# ------------------------------------------------------------ 合成廓线工具 --
def make_synthetic_profile(n_lev=10, p_top=100.0, h_top=9000.0):
    """构造线性 T(h)/rh(h)/sh(h) 的合成气压层廓线（高度升序）。"""
    pre = np.linspace(1000.0, p_top, n_lev)          # hPa，降序（低层在前）
    ell = np.linspace(0.0, h_top, n_lev)             # m，升序
    tem = 288.15 - 6.5e-3 * ell                       # K
    rh = 50.0 + 10.0 * ell / h_top                    # %
    sh = 0.004 - 3.0e-7 * ell                         # kg/kg
    return pre, ell, tem, rh, sh


def test_interpolation_branch():
    pre, ell, tem, rh, sh = make_synthetic_profile()
    hell = 500.0
    lat_g = np.array([36.0, 36.0, 36.5, 36.5])
    prof = fun_calculation_site(1, lat_g, hell, pre, ell, tem, rh, sh, "none")

    n = len(pre)
    # 站点层插入后总层数 = 1 + (n - k0)，k0=1（丢弃站点以下 1 层）
    assert prof.pre.size == 1 + (n - 1)
    assert prof.ell[0] == hell

    # ---- F3 气压内插（指数插值）独立推导 ----
    k = 1  # ell[1]=1000 > 500 → 0 基 k0=1，即 MATLAB k=2
    ec2 = math.log(pre[k] / pre[k - 1]) / (ell[k] - ell[k - 1])
    p_expect = pre[k - 1] * math.exp(ec2 * (hell - ell[k - 1]))
    assert prof.pre[0] == pytest.approx(p_expect, rel=1e-12)

    # ---- F6 每层水汽压独立推导 ----
    temc = prof.tem - 273.15
    e_expect = np.where(
        temc >= 0,
        6.1078 * 10.0 ** (7.5 * temc / (temc + 237.3)),
        6.1078 * 10.0 ** (9.5 * temc / (temc + 265.5)),
    ) * (prof.rh / 100.0)
    assert np.allclose(prof.e_vapor, e_expect, rtol=1e-12)

    # ---- F7 Tm: 后缀和之比独立推导 ----
    d_h = np.diff(prof.ell) / 2.0
    tm_n = (prof.e_vapor[:-1] / prof.tem[:-1]
            + prof.e_vapor[1:] / prof.tem[1:]) * d_h
    tm_d = (prof.e_vapor[:-1] / prof.tem[:-1] ** 2
            + prof.e_vapor[1:] / prof.tem[1:] ** 2) * d_h
    tm_expect = np.array([tm_n[i:].sum() / tm_d[i:].sum() for i in range(len(tm_n))])
    assert np.allclose(prof.tm, tm_expect, rtol=1e-12)

    # ---- F8/F9 ZHD/ZWD: 总和 + Saastamoinen 独立推导 ----
    p_dry = prof.pre - prof.e_vapor
    n_dry = 77.689 * p_dry / prof.tem
    n_wet = 71.2952 * prof.e_vapor / prof.tem + 375463.0 * prof.e_vapor / prof.tem ** 2
    zhd_disc = 0.5 * (n_dry[:-1] + n_dry[1:]) * np.diff(prof.ell)
    zwd_disc = 0.5 * (n_wet[:-1] + n_wet[1:]) * np.diff(prof.ell)
    saas = (0.0022768 * prof.pre[-1]
            / (1 - 0.00266 * math.cos(2 * lat_g[0] * math.pi / 180)
               - 0.00000028 * prof.ell[-1]))
    assert prof.zhd[0] == pytest.approx(zhd_disc.sum() * 1e-6 + saas, rel=1e-12)
    assert prof.zwd[0] == pytest.approx(zwd_disc.sum() * 1e-6, rel=1e-12)
    # 中间层的后缀和性质：zhd[1] < zhd[0]（高度越高延迟越小）
    assert np.all(np.diff(prof.zhd) < 0)
    assert np.all(np.diff(prof.zwd) < 0)
    # ZTD = ZHD + ZWD（在站点层校验 site_retrieval 中完成，这里检查单调一致）
    # 站点层插入后总层数 = n+1 - k0(丢弃站点以下层)，派生数组 = 总层数 - 1
    assert prof.zhd.size == prof.zwd.size == prof.pre.size - 1
    assert prof.tm.size == prof.pre.size - 1

    # ---- F10 PWV 独立推导（注意原式为 p(k)-p(k+1)，气压降序为正值）----
    pwv_disc = 0.5 * (prof.sh[:-1] + prof.sh[1:]) * (prof.pre[:-1] - prof.pre[1:]) * 0.1
    assert prof.pwv[0] == pytest.approx(pwv_disc.sum() / 9.80655, rel=1e-12)
    assert np.all(np.diff(prof.pwv) < 0)

    # ---- F13 WVD 独立推导 ----
    wvd_expect = (prof.e_vapor[:-1] * 100.0) * 1000.0 / (461.5 * prof.tem[:-1])
    assert np.allclose(prof.wvd, wvd_expect, rtol=1e-12)


def test_extrapolation_branch():
    pre, ell, tem, rh, sh = make_synthetic_profile()
    hell = -200.0  # 低于最低层（0 m）
    lat_g = np.array([36.0, 36.0, 36.5, 36.5])
    prof = fun_calculation_site(1, lat_g, hell, pre, ell, tem, rh, sh, "none")

    # 外推分支保留全部层 + 站点层 = n+1
    assert prof.pre.size == len(pre) + 1

    # ---- F4 虚温静力外推独立推导 ----
    tv = tem[0] * (1 + 0.6077 * sh[0])
    ec1 = 9.80665 * 0.028965 / (8.3143 * tv)
    p_expect = pre[0] * math.exp(-ec1 * (hell - ell[0]))
    assert prof.pre[0] == pytest.approx(p_expect, rel=1e-12)

    # T/sh/rh 为线性外推（廊线本身线性 → 精确值可直接算）
    assert prof.tem[0] == pytest.approx(288.15 - 6.5e-3 * hell, rel=1e-12)
    assert prof.sh[0] == pytest.approx(0.004 - 3.0e-7 * hell, rel=1e-12)


def test_above_top_layer_raises():
    pre, ell, tem, rh, sh = make_synthetic_profile()
    lat_g = np.array([36.0, 36.0, 36.5, 36.5])
    with pytest.raises(ValueError, match="高于最高气压层"):
        fun_calculation_site(1, lat_g, ell[-1] + 100.0, pre, ell, tem, rh, sh, "none")


def test_humidity_modes_equivalent():
    """rh_to_q / q_to_rh 前处理后主干应与直接给双湿度一致。"""
    pre, ell, tem, rh, sh = make_synthetic_profile()
    lat_g = np.array([36.0, 36.0, 36.5, 36.5])
    hell = 300.0
    # 只给 rh（CMAMESO 路径）：由 rh 算出的 sh 应与合成 sh 不同，
    # 但与 rh_to_q 的正变换一致 —— 这里验证与手算 q 相同
    q_manual = rh_to_specific_humidity(pre, tem, rh)
    got = fun_calculation_site(1, lat_g, hell, pre, ell, tem, rh, None, "rh_to_q")
    # 站点层(插值)之后的气压层 sh 应与手算 q 一致
    assert np.allclose(got.sh[1:], q_manual[1:], rtol=1e-12)
    # 只给 sh（MERRA2 路径）：反算的 rh 再正算 sh 应回到原 sh（在 clamp 未触发时）
    got2 = fun_calculation_site(1, lat_g, hell, pre, ell, tem, None, sh, "q_to_rh")
    assert np.allclose(got2.sh[1:], sh[1:], rtol=1e-9)


def test_real_egm_mat_load():
    mat = MATLAB_ROOT / "03_DataProcessing/METCOM_ERA5/dependentDataFile/geoidheight_EGM2008_0250.mat"
    if not mat.is_file():
        pytest.skip("原软件包不在本机")
    from metcom.utils.egm2008 import load_egm_mat
    egm = load_egm_mat(mat, expected_sr=(0.25, 0.25))
    assert egm.values.shape == (1441, 721)  # (n_lon, n_lat)
    assert egm.lon_axis[0] == -180.0 and egm.lon_axis[-1] == 180.0
    assert egm.lat_axis[0] == -90.0 and egm.lat_axis[-1] == 90.0
    # 合理量级：大地水准面高在 -110 ~ +90 m
    assert egm.values.min() > -120 and egm.values.max() < 95
