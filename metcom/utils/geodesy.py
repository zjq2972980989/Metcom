"""大地测量转换（对应 fun_*_site.m 中的位势高→椭球高环节）。

注意：原 MATLAB 代码不涉及完整大地坐标转换，仅有：
  1) 位势(m²/s²) ÷ g0 → 位势高(m)（在 readSingleData/fun_*_site 中完成）
  2) 位势高 → 椭球高：WGS84 一阶近似  h ≈ Zg·(1 + Zg/N(φ))
  3) 椭球高 → 正高：H = h_ell − N_geoid（N 来自 EGM2008 格网）
"""

from __future__ import annotations

import numpy as np

# WGS84 椭球参数（与原 MATLAB 代码一致）
_WGS84_A_KM = 6378.137            # 赤道半径 (km)
_WGS84_F = 1.0 / 298.257223563    # 扁率
_WGS84_E2 = 2.0 * _WGS84_F - _WGS84_F**2  # 第一偏心率平方


def prime_vertical_radius_km(lat_deg: np.ndarray | float) -> np.ndarray | float:
    """卯酉圈曲率半径 N(φ)，单位 km。"""
    phi = np.asarray(lat_deg, dtype=float) * np.pi / 180.0
    return _WGS84_A_KM / np.sqrt(1.0 - _WGS84_E2 * np.sin(phi) ** 2)


def geopotential_height_to_ellipsoidal(geo_height_m: np.ndarray | float,
                                       lat_deg: float) -> np.ndarray | float:
    """位势高(m) → 椭球高(m)，WGS84 一阶近似（与原代码逐式对应）。

    h_km = Zg_km * (1 + Zg_km / N_phi_km)
    """
    n_phi_km = prime_vertical_radius_km(lat_deg)
    zg_km = np.asarray(geo_height_m, dtype=float) / 1000.0
    h_km = zg_km * (1.0 + zg_km / n_phi_km)
    return h_km * 1000.0


# 兼容别名
geopotential_to_ellipsoidal_height = geopotential_height_to_ellipsoidal
