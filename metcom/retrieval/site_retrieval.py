"""站点级反演 —— fun_*_site.m 的统一 Python 复刻。

原 MATLAB 五个模块的 fun_*_site 结构 95% 相同，差异仅在数据源参数
（位势除数、层数、EGM 文件、湿度模式），统一由 SourceSpec 描述。

TempData 约定（readers 各数据源负责产出，等价于原 MATLAB cell 但用命名字段）：
  geo : (n_lon, n_lat, n_lev) 位势（m²/s²，ERA5）或位势高（m，其余）
  tem : (n_lon, n_lat, n_lev) 温度 (K)
  sh  : (n_lon, n_lat, n_lev) 比湿 (kg/kg) 或 None（CMAMESO）
  rh  : (n_lon, n_lat, n_lev) 相对湿度 (%) 或 None（MERRA2 输入时）
  pre : (n_lev,) 气压 (hPa)，与层数据同序
  lon : (n_lon,) 升序，[-180, 180)
  lat : (n_lat,) 升序，[-90, 90]
"""

from __future__ import annotations

import numpy as np

from metcom.retrieval.core import fun_calculation_site
from metcom.sources import SourceSpec
from metcom.utils.egm2008 import EgmGrid
from metcom.utils.geodesy import geopotential_height_to_ellipsoidal


class TempData:
    """单个时次、单个数据源的完整气象场（见模块 docstring 的形状约定）。

    ``get_geo_height`` 提供带缓存的位势→位势高换算：原 MATLAB 在每个站点
    调用内对全球场做一次除法（func_ERA5_site 的 geoH_temp = geo_temp/g0），
    数值上等价于每时次只除一次（IEEE 浮点：相同输入产生相同输出），
    缓存后可消除逐站点的大数组重复运算。
    """

    __slots__ = ("geo", "tem", "sh", "rh", "pre", "lon", "lat",
                 "_geo_h", "_geo_h_divisor")

    def __init__(self, geo, tem, sh, rh, pre, lon, lat):
        self.geo = geo
        self.tem = tem
        self.sh = sh
        self.rh = rh
        self.pre = pre
        self.lon = lon
        self.lat = lat
        self._geo_h = None
        self._geo_h_divisor = None

    def get_geo_height(self, divisor: float | None):
        """返回位势高 (m)；divisor 非 None 时先做 geo/divisor（结果缓存）。"""
        if self._geo_h is None or self._geo_h_divisor != divisor:
            if divisor is not None:
                self._geo_h = self.geo / divisor
            else:
                self._geo_h = self.geo
            self._geo_h_divisor = divisor
        return self._geo_h


def find_latitude_position(lat_value: float, lat_range: np.ndarray,
                           sr_lat: float) -> tuple[list[int], list[int]]:
    """findLatitudePosition.m 复刻（返回 0 基索引）。

    raw: 数据网格上 [<=v 的最后一个, >v 的第一个（无则取前一个）]；
    egm: EGM2008 固定轴 -90:sr:90 上同样的语义。
    """
    lat_min = float(np.min(lat_range))
    lat_max = float(np.max(lat_range))
    if lat_value < lat_min or lat_value > lat_max:
        raise ValueError(f"Latitude value is out of the range.: {lat_value}")

    le = np.where(np.asarray(lat_range) <= lat_value)[0]
    raw_index1 = int(le[-1])
    gt = np.where(np.asarray(lat_range) > lat_value)[0]
    raw_index2 = int(gt[0]) if gt.size else raw_index1
    raw_lat_idx = [raw_index1, raw_index2]

    # egm2008 固定轴
    n = int(round(180.0 / sr_lat)) + 1
    egm_lat_range = np.linspace(-90.0, 90.0, n)
    le = np.where(egm_lat_range <= lat_value)[0]
    egm_index1 = int(le[-1])
    gt = np.where(egm_lat_range > lat_value)[0]
    egm_index2 = int(gt[0]) if gt.size else egm_index1
    return raw_lat_idx, [egm_index1, egm_index2]


def find_longitude_position(lon_value: float, lon_range: np.ndarray,
                            sr_lon: float) -> tuple[list[int], list[int]]:
    """findLongitudePosition.m 复刻（返回 0 基索引）。"""
    lon_min = float(np.min(lon_range))
    lon_max = float(np.max(lon_range))
    if lon_value < lon_min or lon_value > lon_max:
        raise ValueError(f"Longitude value is out of the range.: {lon_value}")

    le = np.where(np.asarray(lon_range) <= lon_value)[0]
    raw_index1 = int(le[-1])
    gt = np.where(np.asarray(lon_range) > lon_value)[0]
    raw_index2 = int(gt[0]) if gt.size else raw_index1
    raw_lon_idx = [raw_index1, raw_index2]

    n = int(round(360.0 / sr_lon)) + 1
    egm_lon_range = np.linspace(-180.0, 180.0, n)
    le = np.where(egm_lon_range <= lon_value)[0]
    egm_index1 = int(le[-1])
    gt = np.where(egm_lon_range > lon_value)[0]
    egm_index2 = int(gt[0]) if gt.size else egm_index1
    return raw_lon_idx, [egm_index1, egm_index2]


def _bilinear(g1: float, g2: float, g3: float, g4: float,
              dnlon1: float, dnlat1: float) -> float:
    """F15 双线性：R1=G1(1-fx)+G2·fx; R2=G3(1-fx)+G4·fx; out=R1(1-fy)+R2·fy。"""
    dnlon2 = 1.0 - dnlon1
    dnlat2 = 1.0 - dnlat1
    r1 = g1 * dnlon2 + g2 * dnlon1
    r2 = g3 * dnlon2 + g4 * dnlon1
    return float(r1 * dnlat2 + r2 * dnlat1)


def fun_site(sta_pos: tuple[float, float, float],
             sta_name: str,
             temp_data: TempData,
             spec: SourceSpec,
             egm: EgmGrid) -> dict:
    """站点级 ZTD/PWV 反演（fun_ERA5_site 等 5 个 MATLAB 函数的统一复刻）。

    处理链（每步标注对应关系）：
      1. find_longitude_position / find_latitude_position —— 站点 4 邻近格点
         定位（MATLAB find*Position.m）；
      2. 逐格点提取整列 → F1 位势高→椭球高（utils.geodesy）→ F2 减 EGM2008
         大地水准面差距 N（最近邻查表，对应 egm2008(egm_lonIdx, egm_latIdx)）
         → fun_calculation_site 垂直积分（core.py）；
      3. F15 双线性插值到站点经纬度。

    4 格点编号与原码一致（G1..G4，lat 在前）::

        G3=(lat2,lon1) ─ G4=(lat2,lon2)
        G1=(lat1,lon1) ─ G2=(lat1,lon2)

    双线性权重（F15）：dnlon1 = (lon - lon_g1)/sr_lon，dnlat1 = (lat - lat_g1)
    /sr_lat；R1 = G1·(1-fx) + G2·fx，R2 = G3·(1-fx) + G4·fx，
    out = R1·(1-fy) + R2·fy。ZTD = ZWD + ZHD（原码在插值后相加）。

    Parameters
    ----------
    sta_pos:
        (lat, lon, h) —— 纬度（-90~90）、经度（-180~180）、站点高度 (m)。
        站点文件给出的是正高，原 MATLAB GUI 直接将其作为 hell 传入（层高
        减 EGM2008 N 后同为正高基准，故自洽），此处保持原行为。
    sta_name:
        站点名（原样写入结果文件 name 列）。
    temp_data:
        单时次气象场，由各数据源 reader 产出（形状约定见模块 docstring）。
    spec:
        数据源规格（spatial_resolution / levels / geopotential_divisor /
        humidity_mode / trim_below_ground_nan）。
    egm:
        已加载的 EGM2008 格网（整个反演过程共享一份，等价于原版 addpath
        dependentDataFile 后逐站点 load 的行为）。

    Returns
    -------
    dict: 输出键与结果文件列一一对应（saveToText.m 列序）：
        name/lat/lon/h/ZTD/ZWD/ZHD/WVD/Tm/PWV/eE/Ps
        （eE = 站点水汽压，Ps = 站点气压；单位见 core.GridProfile）。

    Raises
    ------
    ValueError:
        站点超出数据网格范围（find*Position 报错，同原 MATLAB error）；或
        站点高度高于最高气压层（fun_calculation_site 报错）。
    """
    lat, lon, hell = float(sta_pos[0]), float(sta_pos[1]), float(sta_pos[2])
    sr_lat, sr_lon = spec.spatial_resolution

    lon_range = np.asarray(temp_data.lon, dtype=float)
    lat_range = np.asarray(temp_data.lat, dtype=float)

    # 位势 → 位势高（仅 ERA5 需要 ÷9.80665；结果在时次内缓存，避免逐站点重复除）
    geo_h = temp_data.get_geo_height(spec.geopotential_divisor)

    # ---- 4 个邻近格点（G1..G4 = (lat1,lon1),(lat1,lon2),(lat2,lon1),(lat2,lon2)）----
    raw_lon_idx, egm_lon_idx = find_longitude_position(lon, lon_range, sr_lon)
    raw_lat_idx, egm_lat_idx = find_latitude_position(lat, lat_range, sr_lat)

    grid_points = [
        (raw_lat_idx[0], raw_lon_idx[0]),
        (raw_lat_idx[0], raw_lon_idx[1]),
        (raw_lat_idx[1], raw_lon_idx[0]),
        (raw_lat_idx[1], raw_lon_idx[1]),
    ]  # (lat_idx, lon_idx) 0 基

    levels = spec.levels
    pre = np.asarray(temp_data.pre, dtype=float)[:levels]

    # ---- 先提取 4 格点整列（MERRA2 需先做地下层 NaN 修剪，MATLAB 顺序一致）----
    corner_cols: list[dict] = []
    lat_g = np.zeros(4)
    for gi, (lat_i, lon_i) in enumerate(grid_points, start=1):
        slc = (lon_i, lat_i)  # 数组维度为 (lon, lat, lev)
        col = {
            "geo": geo_h[slc[0], slc[1], :levels],
            "tem": temp_data.tem[slc[0], slc[1], :levels],
            "sh": temp_data.sh[slc[0], slc[1], :levels] if temp_data.sh is not None else None,
            "rh": temp_data.rh[slc[0], slc[1], :levels] if temp_data.rh is not None else None,
            "lat_gi": float(lat_range[lat_i]),
            "lon_gi": float(lon_range[lon_i]),
        }
        corner_cols.append(col)
        lat_g[gi - 1] = col["lat_gi"]

    if spec.trim_below_ground_nan:
        # 对应 fun_Merra2_site：rmmissing(tem 列与 sh 列) → min → 全变量保留尾部
        count_cols = [c["tem"] for c in corner_cols]
        sh_cols = [c["sh"] for c in corner_cols if c["sh"] is not None]
        count_cols += sh_cols
        valid = [int(np.count_nonzero(~np.isnan(c))) for c in count_cols]
        nanlen = min(valid)
        if nanlen < levels:
            for c in corner_cols:
                for k in ("geo", "tem", "sh", "rh"):
                    if c[k] is not None:
                        c[k] = c[k][-nanlen:]
            pre = pre[-nanlen:]
            for c in corner_cols:
                if np.isnan(c["tem"]).any() or (c["sh"] is not None and np.isnan(c["sh"]).any()):
                    print("警告: NaN 层修剪后柱内仍含 NaN（缺测不连续），结果可能为 NaN"
                          "（与原 MATLAB 行为一致）")
                    break

    profiles = []
    for gi, col in enumerate(corner_cols, start=1):
        lat_gi = col["lat_gi"]

        # F1: 位势高 → 椭球高（对整层向量化）
        ell_g = geopotential_height_to_ellipsoidal(col["geo"], lat_gi)

        # F2: 椭球高 → 正高所需的大地水准面差距 N（最近邻查表）
        egm_lon_i = egm_lon_idx[0] if grid_points[gi - 1][1] == raw_lon_idx[0] else egm_lon_idx[1]
        egm_lat_i = egm_lat_idx[0] if grid_points[gi - 1][0] == raw_lat_idx[0] else egm_lat_idx[1]
        n_geoid = egm.nearest(egm_lon_i, egm_lat_i)
        ell_g = ell_g - n_geoid

        profile = fun_calculation_site(
            grid_index=gi, lat_g=lat_g, hell=hell, pre=pre, ell_g=ell_g,
            tem_g=col["tem"], rh_g=col["rh"], sh_g=col["sh"],
            humidity_mode=spec.humidity_mode,
        )
        profiles.append(profile)

    # ---- F15: 双线性内插到站点（各取第 0 层 = 站点高度层）----
    dnlon1 = (lon - float(lon_range[grid_points[0][1]])) / sr_lon
    dnlat1 = (lat - float(lat_range[grid_points[0][0]])) / sr_lat

    p1, p2, p3, p4 = profiles
    pre_hell = _bilinear(p1.pre[0], p2.pre[0], p3.pre[0], p4.pre[0], dnlon1, dnlat1)
    tm_hell = _bilinear(p1.tm[0], p2.tm[0], p3.tm[0], p4.tm[0], dnlon1, dnlat1)
    e_hell = _bilinear(p1.e_vapor[0], p2.e_vapor[0], p3.e_vapor[0], p4.e_vapor[0], dnlon1, dnlat1)
    zhd_hell = _bilinear(p1.zhd[0], p2.zhd[0], p3.zhd[0], p4.zhd[0], dnlon1, dnlat1)
    zwd_hell = _bilinear(p1.zwd[0], p2.zwd[0], p3.zwd[0], p4.zwd[0], dnlon1, dnlat1)
    ztd_hell = zwd_hell + zhd_hell
    pwv_hell = _bilinear(p1.pwv[0], p2.pwv[0], p3.pwv[0], p4.pwv[0], dnlon1, dnlat1)
    wvd_hell = _bilinear(p1.wvd[0], p2.wvd[0], p3.wvd[0], p4.wvd[0], dnlon1, dnlat1)

    return {
        "name": sta_name,
        "lat": lat,
        "lon": lon,
        "h": hell,
        "ZTD": ztd_hell,
        "ZWD": zwd_hell,
        "ZHD": zhd_hell,
        "WVD": wvd_hell,
        "Tm": tm_hell,
        "PWV": pwv_hell,
        "eE": e_hell,
        "Ps": pre_hell,
    }
