"""核心物理算法 —— fun_calculation_site.m 的 Python 复刻（公式 F0–F14）。

原 MATLAB 代码存在三个版本（ERA5/CRA40/CRAV15 十参版、CMAMESO 九参版、
MERRA2 九参向量化版），三者物理内核数学等价，仅湿度前处理不同，此处统一
为一个实现，由 ``humidity_mode`` 参数区分：

  none    : rh、sh 均为输入（ERA5 / CRA40 / CRA V1.5）
  rh_to_q : 仅输入 rh，由 Magnus 公式换算 sh（CMA-MESO，见 F11）
  q_to_rh : 仅输入 sh，反算 rh 并 clamp 到 [0, 100]（MERRA2，见 F12）

与原 MATLAB 代码保持一致的实现细节（迁移时**不得**当作 bug "修正"，否则
无法与 MATLAB 基准输出对齐）：

  - PWV 积分除以 g = 9.80655（F10），而外推分支静力常数用 9.80665（F4）
    ——原码即不一致，两处都保留；
  - ZHD 每层都加同一个 Saastamoinen 顶层修正（F9），ZWD 不加；
  - Tm 为逐层"后缀和之比"：Tm(i) = ∫(e/T)dh / ∫(e/T²)dh，积分区间为第 i 层
    至大气顶（F7）；
  - 折射率延迟用 flip(cumsum(flip(·))) 后缀和实现"第 i 层到顶层"的累积，
    ×1e-6 由折射率无量纲量转米（F8）；
  - 站点高于最高气压层时原 MATLAB 会越界崩溃（k 循环无 break 兜底），此处
    以显式 ValueError 替代（崩溃性 bug 修复，见 docs/porting_notes.md）。

公式编号 F0–F14 与 docs/porting_notes.md、探索报告一致；F1/F2（位势高→椭
球高→正高）在 utils/geodesy.py 与 site_retrieval.py 中。

输出长度约定（与 MATLAB 一致，随分支变化）：
  站点层插入后共 L 层（外推分支 L = N+1，内插分支 L = 1 + N - k0），
  pre/tem/sh/rh/e_vapor/ell 长 L，tm/zhd/zwd/pwv/wvd 长 L-1。
  site_retrieval 仅取各数组第 0 个元素（站点高度层）做双线性插值。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# ---- 原代码常数（数值与用途均不得改动）----
G_PWV = 9.80655          # F10 PWV 积分的重力加速度 (m/s²)
G_EXTRAP = 9.80665       # F4 外推分支静力学公式的重力加速度 (m/s²)
RD_RV_RATIO_CONST = 0.028965  # F4 ec1 = 9.80665·0.028965/(8.3143·Tv) 中的常数
R_DRY_AIR = 8.3143       # F4 气体常数近似值 (J/(mol·K))
EPS_WATER_VAPOR = 0.6077      # F4 虚温系数 Tv = T·(1 + 0.6077·q)
K1_DRY = 77.689          # F8 干折射率系数 (K/hPa)
K2_WET = 71.2952         # F8 湿折射率系数 (K/hPa)
K3_WET = 375463.0        # F8 湿折射率系数 (K²/hPa)
SAAS_A = 0.0022768       # F9 Saastamoinen ZHD 主体系数
SAAS_B = 0.00266         # F9 纬度项系数
SAAS_C = 0.00000028      # F9 高程项系数
RV = 461.5               # F13 水汽气体常数 (J/(kg·K))


def saturation_vapor_pressure_hpa(t_celsius: np.ndarray | float) -> np.ndarray | float:
    """Magnus 饱和水汽压 (hPa)（F6 的 es 形式；F11 中复用）。

    t >= 0（水面）: 6.1078·10^(7.5t/(t+237.3))
    t <  0（冰面）: 6.1078·10^(9.5t/(t+265.5))
    """
    t = np.asarray(t_celsius, dtype=float)
    es = np.where(
        t >= 0,
        6.1078 * np.power(10.0, 7.5 * t / (t + 237.3)),
        6.1078 * np.power(10.0, 9.5 * t / (t + 265.5)),
    )
    return es


def actual_vapor_pressure_hpa(t_celsius: np.ndarray, rh_percent: np.ndarray) -> np.ndarray:
    """每层实际水汽压 e (hPa) = es(T)·RH/100（F6）。"""
    return saturation_vapor_pressure_hpa(t_celsius) * (np.asarray(rh_percent) / 100.0)


def rh_to_specific_humidity(pre_hpa: np.ndarray, tem_k: np.ndarray,
                            rh_percent: np.ndarray) -> np.ndarray:
    """相对湿度 → 比湿 (kg/kg)（F11，CMA-MESO 前处理）。

    q = 0.622·e / (p − 0.378·e)   （原码写为 0.001·622·eE./(pre−0.378·eE)）
    """
    e = actual_vapor_pressure_hpa(np.asarray(tem_k) - 273.15, rh_percent)
    return 0.622 * e / (np.asarray(pre_hpa) - 0.378 * e)


def q_to_relative_humidity(pre_hpa: np.ndarray, tem_k: np.ndarray,
                           sh_kgkg: np.ndarray) -> np.ndarray:
    """比湿 → 相对湿度 (%)，clamp 到 [0,100]（F12，MERRA2 前处理）。

    e = q·p / (0.622 + 0.378·q)；RH = e/es(T)·100
    """
    sh = np.asarray(sh_kgkg, dtype=float)
    p = np.asarray(pre_hpa, dtype=float)
    e = sh * p / (0.622 + 0.378 * sh)
    es = saturation_vapor_pressure_hpa(np.asarray(tem_k) - 273.15)
    rh = e / es * 100.0
    return np.clip(rh, 0.0, 100.0)


def _interp_linear_extrap(x: float, xp: np.ndarray, fp: np.ndarray) -> float:
    """interp1(xp, fp, x, 'linear', 'extrap') 的等价实现。"""
    if x <= xp[0]:
        slope = (fp[1] - fp[0]) / (xp[1] - xp[0])
        return float(fp[0] + slope * (x - xp[0]))
    if x >= xp[-1]:
        slope = (fp[-1] - fp[-2]) / (xp[-1] - xp[-2])
        return float(fp[-1] + slope * (x - xp[-1]))
    return float(np.interp(x, xp, fp))


def _suffix_cumsum(x: np.ndarray) -> np.ndarray:
    """flip(cumsum(flip(x)))：第 i 个元素 = x[i:] 之和。"""
    return np.cumsum(x[::-1])[::-1]


@dataclass
class GridProfile:
    """单个格点、站点高度处的反演中间结果（对应 fun_calculation_site 输出）。

    字段说明（长度随分支而异，见模块 docstring；站点反演只用第 0 个元素）：

    ========  ==================================================  ======
    字段       含义                                                单位
    ========  ==================================================  ======
    pre       各层气压（站点层在前，低层→高层）                    hPa
    tem       各层温度                                             K
    sh        各层比湿                                             kg/kg
    rh        各层相对湿度（q_to_rh 模式下为反算值）               %
    tm        加权平均温度 Tm（逐层后缀比，仅站点层被使用）        K
    e_vapor   各层实际水汽压（MATLAB 输出名 es_g_hell/e_g_hell）   hPa
    zhd       天顶干延迟（含 Saastamoinen 顶层修正）               m
    zwd       天顶湿延迟                                           m
    pwv       大气可降水量                                         m
    wvd       水汽密度                                             g/m³
    ell       各层高度（站点层在前）                               m
    ========  ==================================================  ======
    """

    pre: np.ndarray
    tem: np.ndarray
    sh: np.ndarray
    rh: np.ndarray
    tm: np.ndarray
    e_vapor: np.ndarray
    zhd: np.ndarray
    zwd: np.ndarray
    pwv: np.ndarray
    wvd: np.ndarray
    ell: np.ndarray


def fun_calculation_site(grid_index: int,
                         lat_g: np.ndarray,
                         hell: float,
                         pre: np.ndarray,
                         ell_g: np.ndarray,
                         tem_g: np.ndarray,
                         rh_g: np.ndarray | None,
                         sh_g: np.ndarray | None,
                         humidity_mode: str = "none") -> GridProfile:
    """气压层垂直积分（fun_calculation_site.m 的忠实复刻，单时次单格点）。

    处理顺序：湿度前处理（视模式）→ F0 层序定位 → F3/F4 站点层内插或外推
    → F5 层序列重构 → F6 水汽压 → F7 Tm → F8/F9 ZHD/ZWD → F10 PWV → F13 WVD。

    Parameters
    ----------
    grid_index:
        格点序号 i（**1 基**，原 MATLAB 语义，用于取 ``lat_g(i)`` 计算顶层
        Saastamoinen 修正）。
    lat_g:
        4 个邻近格点的纬度数组（长度 4，1 基语义）。
    hell:
        站点高度 (m)。与 ``ell_g`` 同一垂直基准（层高已减 EGM2008 大地水准
        面差距转为正高，站点文件给出正高——见 site_retrieval.fun_site）。
    pre:
        气压层气压 (hPa)，**沿数组方向高度递增**（低层在前）；调用方负责
        保证层序（读取器有 check_level_order 校验）。
    ell_g / tem_g / rh_g / sh_g:
        该格点各气压层的层高 (m)、温度 (K)、相对湿度 (%)、比湿 (kg/kg)，
        长度须与 pre 一致；rh_g/sh_g 按 humidity_mode 可为 None。
    humidity_mode:
        "none" / "rh_to_q" / "q_to_rh"，见模块 docstring。

    Returns
    -------
    GridProfile: 各层积分结果（长度约定见模块 docstring 与类 docstring）。

    Raises
    ------
    ValueError:
        humidity_mode 非法；或站点高度高于最高气压层（原 MATLAB 越界崩溃
        的情形，此处显式报错）。
    """
    if humidity_mode not in ("none", "rh_to_q", "q_to_rh"):
        raise ValueError(f"未知 humidity_mode: {humidity_mode}")

    pre = np.asarray(pre, dtype=float)
    ell_g = np.asarray(ell_g, dtype=float)
    tem_g = np.asarray(tem_g, dtype=float)

    # ---- 湿度前处理（CMAMESO: RH→q；MERRA2: q→RH）----
    if humidity_mode == "rh_to_q":
        rh_g = np.asarray(rh_g, dtype=float)
        sh_g = rh_to_specific_humidity(pre, tem_g, rh_g)
    elif humidity_mode == "q_to_rh":
        sh_g = np.asarray(sh_g, dtype=float)
        rh_g = q_to_relative_humidity(pre, tem_g, sh_g)
    else:
        rh_g = np.asarray(rh_g, dtype=float)
        sh_g = np.asarray(sh_g, dtype=float)

    levels_num = len(pre)

    # ---- F0: 层序定位（MATLAB for k=1:N, if hell<ell_g(k) break）----
    k0 = levels_num  # 找不到时 = N（0 基下越界标记）
    for kk in range(levels_num):
        if hell < ell_g[kk]:
            k0 = kk
            break

    if k0 == 0:
        # ---- F4: 外推分支（站点低于最低气压层）----
        tem_hell = _interp_linear_extrap(hell, ell_g, tem_g)
        sh_hell = _interp_linear_extrap(hell, ell_g, sh_g)
        rh_hell = _interp_linear_extrap(hell, ell_g, rh_g)
        tv = tem_g[0] * (1.0 + EPS_WATER_VAPOR * sh_g[0])
        ec1 = G_EXTRAP * RD_RV_RATIO_CONST / (R_DRY_AIR * tv)
        dif_height_ls = hell - ell_g[0]
        pre_hell = pre[0] * math.exp(-ec1 * dif_height_ls)

        pre_level = np.concatenate(([pre_hell], pre))
        tem_level = np.concatenate(([tem_hell], tem_g))
        sh_level = np.concatenate(([sh_hell], sh_g))
        rh_level = np.concatenate(([rh_hell], rh_g))
        hell_level = np.concatenate(([hell], ell_g))
    elif k0 < levels_num:
        # ---- F3: 内插分支（站点位于第 k0-1 与 k0 层之间）----
        dif_height = ell_g[k0] - ell_g[k0 - 1]
        dif_pre = pre[k0] / pre[k0 - 1]
        ec2 = math.log(dif_pre) / dif_height
        dif_height_ls = hell - ell_g[k0 - 1]
        pre_hell = pre[k0 - 1] * math.exp(ec2 * dif_height_ls)
        tem_hell = float(np.interp(hell, ell_g, tem_g))
        sh_hell = float(np.interp(hell, ell_g, sh_g))
        rh_hell = float(np.interp(hell, ell_g, rh_g))

        pre_level = np.concatenate(([pre_hell], pre[k0:]))
        tem_level = np.concatenate(([tem_hell], tem_g[k0:]))
        sh_level = np.concatenate(([sh_hell], sh_g[k0:]))
        rh_level = np.concatenate(([rh_hell], rh_g[k0:]))
        hell_level = np.concatenate(([hell], ell_g[k0:]))
    else:
        # 站点高于最高气压层：原 MATLAB 越界崩溃，此处显式报错
        raise ValueError(
            f"站点高度 {hell:.1f} m 高于最高气压层高度 {ell_g[-1]:.1f} m，"
            f"无法反演（原 MATLAB 代码在相同情形下会越界报错）"
        )

    ell_hell = hell_level

    # ---- F6: 每层水汽压 e ----
    temc = tem_level - 273.15
    e_level = actual_vapor_pressure_hpa(temc, rh_level)

    # ---- F7: 加权平均温度 Tm（后缀和之比）----
    d_h = (hell_level[1:] - hell_level[:-1]) / 2.0
    tm_n = (e_level[:-1] / tem_level[:-1] + e_level[1:] / tem_level[1:]) * d_h
    tm_d = (e_level[:-1] / tem_level[:-1] ** 2
            + e_level[1:] / tem_level[1:] ** 2) * d_h
    tm = _suffix_cumsum(tm_n) / _suffix_cumsum(tm_d)

    # ---- F8: 折射率与离散延迟 ----
    pre_dry = pre_level - e_level
    n_dry = K1_DRY * pre_dry / tem_level
    n_wet = K2_WET * e_level / tem_level + K3_WET * e_level / tem_level / tem_level

    d_h_full = hell_level[1:] - hell_level[:-1]
    zhd_discrete = 0.5 * (n_dry[:-1] + n_dry[1:]) * d_h_full
    zwd_discrete = 0.5 * (n_wet[:-1] + n_wet[1:]) * d_h_full

    zhd_cumsum = _suffix_cumsum(zhd_discrete) * 1e-6
    zwd_cumsum = _suffix_cumsum(zwd_discrete) * 1e-6

    # ---- F9: Saastamoinen 顶层修正（lat_g(i) 为该格点纬度，1 基）----
    lat_i = lat_g[grid_index - 1]
    saas_correction = (SAAS_A * pre_level[-1]
                       / (1.0 - SAAS_B * math.cos(2.0 * lat_i * math.pi / 180.0)
                          - SAAS_C * hell_level[-1]))
    zhd = zhd_cumsum + saas_correction   # 每层都加同一修正（原码如此）
    zwd = zwd_cumsum                     # ZWD 无顶层修正

    # ---- F10: PWV（比湿分层梯形积分 + 后缀和）----
    pwv_discrete = 0.5 * (sh_level[:-1] + sh_level[1:]) * (pre_level[:-1] - pre_level[1:]) * 0.1
    pwv = _suffix_cumsum(pwv_discrete) / G_PWV

    # ---- F13: 水汽密度 WVD (g/m³) ----
    wvd = (e_level[:-1] * 100.0) * 1000.0 / (RV * tem_level[:-1])

    return GridProfile(
        pre=pre_level, tem=tem_level, sh=sh_level, rh=rh_level,
        tm=tm, e_vapor=e_level, zhd=zhd, zwd=zwd, pwv=pwv, wvd=wvd,
        ell=ell_hell,
    )
