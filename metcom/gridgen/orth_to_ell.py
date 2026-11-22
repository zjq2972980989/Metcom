"""格点坐标文本生成（OrthHeight2EllHeight.m 的 Python 复刻）。

读 DEM NetCDF → 降采样 → EGM2008 大地水准面高插值 → 椭球高 = 正高 + N →
输出固定宽度四列文本（%4s %12.6f %12.6f %10.3f）。

与原版数值行为完全一致的关键点：
  - EGM txt 网格重建顺序：reshape(n_lat, n_lon) + flipud（文件按纬度 +90→-90
    降序、经度 0→360 升序排列）；
  - 有效点提取顺序为 MATLAB 列主序（lat 最快、lon 最慢，order='F'）；
  - 先截取前 MAX_STATIONS 再按 (lat, lon) 排序（原版顺序）；
  - griddedInterpolant('linear','none') → RegularGridInterpolator + NaN 填充。
"""

from __future__ import annotations

from pathlib import Path

import netCDF4
import numpy as np
import pandas as pd
from scipy.interpolate import RegularGridInterpolator

from metcom.utils.fmt import fmt_fixed_width


def load_egm2008_txt(txt_file: Path) -> tuple[RegularGridInterpolator, np.ndarray, np.ndarray]:
    """load_egm2008_txt 复刻：构建 (lats, lons) 双线性插值器（范围外 NaN）。"""
    txt_file = Path(txt_file)
    if not txt_file.is_file():
        raise FileNotFoundError(f"无法打开文件: {txt_file}")

    df = pd.read_csv(txt_file, sep=r"\s+", header=None,
                     names=["lat", "lon", "N"], dtype=float)
    lat_all, lon_all, n_all = df["lat"].to_numpy(), df["lon"].to_numpy(), df["N"].to_numpy()

    unique_lats = np.unique(lat_all)   # 升序
    unique_lons = np.unique(lon_all)
    n_lat, n_lon = unique_lats.size, unique_lons.size
    print(f"  网格尺寸: {n_lat} (lat) x {n_lon} (lon)")

    # 文件顺序: lat 降序(+90→-90) 外层, lon 升序(0→360) 内层
    n_grid = n_all.reshape(n_lat, n_lon)   # 行 = lat 降序
    n_grid = np.flipud(n_grid)             # → lat 升序

    interp = RegularGridInterpolator(
        (unique_lats, unique_lons), n_grid,
        method="linear", bounds_error=False, fill_value=np.nan)
    return interp, unique_lats, unique_lons


def generate_station_names(n: int) -> list[str]:
    """A000, A001, ..., Z999（generate_station_names 复刻）。"""
    if n > 26000:
        raise ValueError(f"站点数 {n} 超过上限 26000")
    names = []
    for i in range(n):
        letter = chr(ord("A") + i // 1000)
        num = i % 1000
        names.append(f"{letter}{num:03d}")
    return names


def _save_to_text(filename: Path, station_names: list[str],
                  lats: np.ndarray, lons: np.ndarray, heights: np.ndarray) -> None:
    """%4s %12.6f %12.6f %10.3f 右对齐（save_to_text 复刻）。"""
    lines = [
        fmt_fixed_width([name, la, lo, h],
                        [(4, None), (12, 6), (12, 6), (10, 3)], sep=" ")
        for name, la, lo, h in zip(station_names, lats, lons, heights)
    ]
    filename.parent.mkdir(parents=True, exist_ok=True)
    with open(filename, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def process_dem_file(dem_file: Path, egm_interp: RegularGridInterpolator,
                     output_prefix: str, output_res: float, base_res: float,
                     output_dir: Path, max_stations: int) -> tuple[Path, Path]:
    """process_dem_file 复刻：读取 → 降采样 → EGM 插值 → 椭球高 → 保存。"""
    dem_file = Path(dem_file)
    print(f"\n>>> 处理: {dem_file}")

    ds = netCDF4.Dataset(dem_file, "r")
    try:
        lon_dem = np.asarray(ds.variables["lon"][:], dtype=float).ravel()
        lat_dem = np.asarray(ds.variables["lat"][:], dtype=float).ravel()
        elev_dem = np.asarray(ds.variables["elevation"][:], dtype=float)
        v = ds.variables["elevation"]
        # 确保维度顺序为 (lat, lon)：第一维为 lon 时转置
        if v.dimensions[0].strip().lower() == "lon":
            elev_dem = elev_dem.T
    finally:
        ds.close()

    # 分辨率降采样（抽稀）
    step = max(1, int(round(output_res / base_res)))
    if step > 1:
        print(f"  应用降采样: 步长 {step} (目标输出分辨率 {output_res:.3f}°)")
        lon_dem = lon_dem[::step]
        lat_dem = lat_dem[::step]
        elev_dem = elev_dem[::step, ::step]
    print(f"  DEM 维度: {elev_dem.shape[0]} (lat) x {elev_dem.shape[1]} (lon)")

    lon_g, lat_g = np.meshgrid(lon_dem, lat_dem)   # (n_lat, n_lon)

    # 经度转换到 0~360°（匹配 EGM 坐标）
    lon_0360 = np.where(lon_g < 0, lon_g + 360.0, lon_g)

    # 提取有效（非 NaN）网格点 —— MATLAB 逻辑索引为列主序（lat 最快、lon 最慢）
    valid_mask = ~np.isnan(elev_dem)                     # (n_lat, n_lon)
    keep = valid_mask.ravel(order="F")
    lon_valid = lon_0360.ravel(order="F")[keep]
    lat_valid = lat_g.ravel(order="F")[keep]
    elev_valid = elev_dem.ravel(order="F")[keep]

    n_valid = elev_valid.size
    print(f"  有效站点数: {n_valid}")
    if n_valid == 0:
        raise ValueError("无有效数据点！")

    if n_valid > max_stations:
        print(f"  超过上限 {max_stations}，截取前 {max_stations} 个")
        lon_valid = lon_valid[:max_stations]
        lat_valid = lat_valid[:max_stations]
        elev_valid = elev_valid[:max_stations]
        n_valid = max_stations

    # 按 (纬度, 经度) 排序（sortrows 稳定排序）
    print("  正在对输出点位进行排序 (纬度 -> 经度)...")
    sort_idx = np.lexsort((lon_valid, lat_valid))
    lat_valid, lon_valid, elev_valid = (lat_valid[sort_idx], lon_valid[sort_idx],
                                        elev_valid[sort_idx])

    # EGM2008 插值
    print("  正在插值 EGM2008 大地水准面高...")
    n_geoid = egm_interp(np.column_stack([lat_valid, lon_valid]))

    # 经度 360° 边界修正
    nan_mask = np.isnan(n_geoid)
    if nan_mask.any():
        print(f"  修正 {int(nan_mask.sum())} 个经度边界点...")
        lon_fix = np.where(lon_valid >= 360, lon_valid - 360, lon_valid)
        n_geoid[nan_mask] = egm_interp(
            np.column_stack([lat_valid[nan_mask], lon_fix[nan_mask]]))

    # 剔除仍为 NaN 的点
    final_mask = ~np.isnan(n_geoid)
    if not final_mask.all():
        print(f"  跳过 {int((~final_mask).sum())} 个无法插值的点")
        lon_valid, lat_valid = lon_valid[final_mask], lat_valid[final_mask]
        elev_valid, n_geoid = elev_valid[final_mask], n_geoid[final_mask]
        n_valid = elev_valid.size

    # 椭球高 = 正常高 + 大地水准面高
    ellip_valid = elev_valid + n_geoid

    # 经度转回 -180~180°
    lon_output = np.where(lon_valid > 180, lon_valid - 360, lon_valid)

    station_names = generate_station_names(n_valid)

    res_str = f"{int(round(output_res * 1000)):04d}"
    ortho_file = output_dir / f"{output_prefix}_{res_str}_orthometric.txt"
    ellip_file = output_dir / f"{output_prefix}_{res_str}_ellipsoidal.txt"
    _save_to_text(ortho_file, station_names, lat_valid, lon_output, elev_valid)
    _save_to_text(ellip_file, station_names, lat_valid, lon_output, ellip_valid)
    print(f"  已生成:\n    {ortho_file}\n    {ellip_file}")
    return ortho_file, ellip_file


def run_orth_to_ell(cfg: dict) -> dict:
    """执行格点文本生成（配置节 metcom_gridgen.yaml 的 stations: 节）。"""
    base = Path(cfg.get("workdir", "."))

    def resolve(p: str) -> Path:
        p = Path(p)
        return p if p.is_absolute() else base / p

    output_res = float(cfg["output_resolution"])
    egm_path = resolve(cfg["egm_coarse"] if output_res >= 0.25 else cfg["egm_fine"])
    print(f"选择{'粗' if output_res >= 0.25 else '细'}分辨率 EGM 插值器: {egm_path.name}")
    egm_interp, _, _ = load_egm2008_txt(egm_path)

    dem_file = resolve(cfg["dem_file"])
    if not dem_file.is_file():
        print(f"[跳过] 未找到 DEM 文件: {dem_file}")
        return {"skipped": str(dem_file)}

    ortho, ellip = process_dem_file(
        dem_file, egm_interp,
        output_prefix=cfg["output_prefix"],
        output_res=output_res,
        base_res=float(cfg["base_resolution"]),
        output_dir=resolve(cfg["output_dir"]),
        max_stations=int(cfg["max_stations"]),
    )
    return {"orthometric": str(ortho), "ellipsoidal": str(ellip)}
