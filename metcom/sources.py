"""五种再分析数据源的适配器配置。

对应 MATLAB 五个模块的差异点（探索结论）：
- TempData cell 字段序、层数、是否需要 geo/g0 转换、EGM2008 文件、NaN 修剪、湿度模式。
- 物理内核三版本数学等价：ERA5/CRA40/CRAV15 直接用 rh；CMAMESO 需 RH→q（F11）；
  MERRA2 需 q→RH（F12）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SourceSpec:
    """单个再分析数据源的静态规格。"""

    key: str                       # era5 / cmameso / cra40 / crav15 / merra2
    name: str                      # 显示名
    levels: int                    # 气压层数（代码实际使用的层数）
    spatial_resolution: tuple[float, float]   # (lat, lon) 度
    time_resolution: str           # "1h" / "3h" / "6h"
    egm_file: str                  # dependentDataFile 下的 EGM2008 mat 文件名
    # TempData 中比湿与相对湿度：ERA5/CRA40/CRAV15 两者都有；
    # CMAMESO 无 sh（函数内由 RH 转 q）；MERRA2 顺序不同（函数内由 q 转 RH）
    humidity_mode: str = "none"    # "none" | "rh_to_q" | "q_to_rh"
    geopotential_divisor: Optional[float] = None  # ERA5 的 z 需 /9.80665，其余为 None
    needs_grib2_convert: bool = False
    # MERRA2：站点周围柱可能含地下层 NaN（缺测），反演前按 fun_Merra2_site 的
    # rmmissing 逻辑修剪（保留尾部 nanlen 层）
    trim_below_ground_nan: bool = False
    file_pattern_doc: str = ""


SOURCES: dict[str, SourceSpec] = {
    "era5": SourceSpec(
        key="era5",
        name="ERA5",
        levels=37,
        spatial_resolution=(0.25, 0.25),
        time_resolution="1h",
        egm_file="geoidheight_EGM2008_0250.mat",
        humidity_mode="none",
        geopotential_divisor=9.80665,
        file_pattern_doc="era5_global_qrzt_YYYYMMDDHH.nc（单文件含 z/t/q/r）",
    ),
    "cmameso": SourceSpec(
        key="cmameso",
        name="CMA-MESO",
        levels=21,
        spatial_resolution=(0.03, 0.03),
        time_resolution="3h",
        egm_file="geoidheight_EGM2008_0030.mat",
        humidity_mode="rh_to_q",
        needs_grib2_convert=True,
        file_pattern_doc="GRAPES3KMORIG_(GPH|RHU|TEM)_100_YYYYMMDDHH_CHN_0_0.nc（3 文件/时次）",
    ),
    "cra40": SourceSpec(
        key="cra40",
        name="CRA40",
        levels=37,
        spatial_resolution=(0.25, 0.25),
        time_resolution="6h",
        egm_file="geoidheight_EGM2008_0250.mat",
        humidity_mode="none",
        needs_grib2_convert=True,
        file_pattern_doc="CRA40_(GPH|RHU|SHU|TEM)_YYYYMMDDHH_GLB_0P25_HOUR_V1_0_0.nc（4 文件/时次）",
    ),
    "crav15": SourceSpec(
        key="crav15",
        name="CRA V1.5",
        levels=37,  # 原代码固定取 37 层（readme 称 41 层，以代码为准）
        spatial_resolution=(0.10, 0.10),
        time_resolution="1h",
        egm_file="geoidheight_EGM2008_0100.mat",
        humidity_mode="none",
        needs_grib2_convert=True,
        file_pattern_doc="ART_ATM_GLB_0P10_6HOR_ANAL_YYYYMMDDHH[_YYYYMMDDHHMMSS]_(HGT|RH|SPFH|TMP).nc（4 文件/时次；14 位版本段可缺省）",
    ),
    "merra2": SourceSpec(
        key="merra2",
        name="MERRA2",
        levels=42,
        spatial_resolution=(0.5, 0.625),
        time_resolution="6h",
        egm_file="geoidheight_EGM2008_0500_0625.mat",
        humidity_mode="q_to_rh",
        trim_below_ground_nan=True,
        file_pattern_doc="MERRA2_400.inst6_3d_ana_Np.YYYYMMDD.SUB.nc（按年文件夹，一天一文件）",
    ),
}

__all__ = ["SourceSpec", "SOURCES"]
