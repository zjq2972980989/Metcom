"""可视化绘图（plot_ZTD_PWV_Sequence_rmse / HeatMap_rmse / HeatMap_value 统一迁移）。

plot_type:
  sequence      —— RMSE 时间序列 + 分布直方图（需 Timeseries 评估 txt）
  heatmap_rmse  —— 站点 RMSE 空间热力图（需 ByStation 评估 txt）
  heatmap_value —— 单时刻数值热力图（需 03 反演结果 txt）
"""

from __future__ import annotations

import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "PingFang SC",
                                   "DejaVu Sans", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False

REGION_CONFIGS = {
    "CHN": {"extent": [73, 138, 15, 53],
            "prefixes": ["11", "12", "13", "14", "15", "21", "22", "23", "31", "32",
                         "33", "34", "35", "36", "37", "41", "42", "43", "44", "45",
                         "46", "50", "51", "52", "53", "54", "61", "62", "63", "64",
                         "65", "71", "81", "82"],
            "is_province_level": True},
    "BTH": {"extent": [113.0, 120.5, 35.5, 43.0],
            "prefixes": ["11", "12", "13"], "is_province_level": True},
}
SEA_COLOR = "white"
PROVINCE_LINE_COLOR = "#888888"
FRAME_LINEWIDTH = 2.0
INTERNAL_LINEWIDTH = 0.5
OUTLINE_LINEWIDTH = 1.2


# ------------------------------------------------------------------ 解析 --
def parse_timeseries_txt(file_path):
    header_cols, data_lines = [], []
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if line.startswith("Time") and "N" in line:
                header_cols = line.split()
            elif re.match(r"^\d{10}\s", line):
                data_lines.append(line.split())
    df = pd.DataFrame(data_lines, columns=header_cols)
    for col in df.columns:
        if col not in ("Time", "N"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["time_dt"] = pd.to_datetime(df["Time"], format="%Y%m%d%H")
    return df


def parse_by_station_txt(file_path):
    header_cols, data_lines = [], []
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if line.startswith("SITE") and "LAT" in line:
                header_cols = line.split()
            elif re.match(r"^[A-Za-z0-9]+\s+\d+\.\d+", line):
                data_lines.append(line.split())
    df = pd.DataFrame(data_lines, columns=header_cols)
    for col in df.columns:
        if col not in ("SITE", "Time_Range"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def read_ztd_result(file_path):
    """读取 03 反演结果文件（单时刻）—— 复用评估 IO 并统一绘图列名。"""
    from metcom.evaluation.io import read_dynamic_file
    df = read_dynamic_file(Path(file_path), {})
    return df.rename(columns={"LAT": "LAT/deg", "LON": "LON/deg",
                              "ALT": "ALT/m"})


# ------------------------------------------------------------- sequence --
def plot_sequence(cfg) -> list[Path]:
    txt_file, output_dir = cfg["txt_file"], Path(cfg["output_dir"])
    name_a, name_b = cfg.get("name_a", "A"), cfg.get("name_b", "B")
    region = cfg.get("region", "")
    plot_vars = cfg.get("plot_vars") or ["ZTD_RMSE(mm)", "PWV_RMSE(mm)"]

    df = parse_timeseries_txt(txt_file)
    if df.empty:
        raise ValueError(f"未从 {txt_file} 解析到数据行")
    t_start, t_end = df["time_dt"].min(), df["time_dt"].max()
    x_ticks = pd.date_range(start=t_start, end=t_end, periods=5)
    year_str = (f"{t_start.year}年" if t_start.year == t_end.year
                else f"{t_start.year}-{t_end.year}年")

    n_vars = len(plot_vars)
    fig = plt.figure(figsize=(16, 5 * n_vars), facecolor="w")
    bbox_style = dict(boxstyle="square,pad=0.5", fc="w", ec="k", lw=1.2)

    colors = ["red", "blue", "green", "orange", "purple"]
    for i, col_name in enumerate(plot_vars):
        if col_name not in df.columns:
            print(f"[WARN] 未找到列 {col_name}，跳过")
            continue
        color = colors[i % len(colors)]
        title = col_name.split("(")[0]
        unit = col_name.split("(")[-1].rstrip(")") if "(" in col_name else ""

        ax1 = fig.add_subplot(n_vars, 2, i * 2 + 1)
        ax1.plot(df["time_dt"], df[col_name], color=color, marker=".",
                 markersize=3, linewidth=0.8)
        ax1.set_title(f"{name_a} vs {name_b} {region} {title}", fontsize=14)
        ax1.set_ylabel(f"{title} ({unit})", fontsize=12)
        ax1.set_xlim(t_start, t_end)
        ax1.grid(True, linestyle="-", linewidth=0.5, alpha=0.7)
        ax1.set_xticks(x_ticks)
        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%m月%d日"))
        ax1.text(1.0, -0.15, year_str, transform=ax1.transAxes,
                 ha="right", va="top", fontsize=10)
        avg_val = df[col_name].mean()
        ax1.text(0.98, 0.95, f"Avg = {avg_val:.2f} {unit}", transform=ax1.transAxes,
                 ha="right", va="top", fontsize=12, fontweight="bold", bbox=bbox_style)

        ax2 = fig.add_subplot(n_vars, 2, i * 2 + 2)
        vals = df[col_name].dropna()
        weights = np.ones_like(vals) / max(len(vals), 1)
        ax2.hist(vals, bins=40, weights=weights, facecolor=color,
                 edgecolor="w", alpha=0.7)
        ax2.axvline(avg_val, color="black", linestyle="--", linewidth=1.5)
        ax2.set_title(f"{title} 分布", fontsize=14)
        ax2.set_xlabel(f"{title} ({unit})", fontsize=12)
        ax2.set_ylabel("概率", fontsize=12)
        ax2.grid(True, linestyle="-", linewidth=0.5, alpha=0.7)

    plt.tight_layout()
    output_dir.mkdir(parents=True, exist_ok=True)
    save_path = output_dir / f"{name_a}_vs_{name_b}_{region}_Timeseries.png"
    plt.savefig(save_path, dpi=300)
    plt.close(fig)
    print(f"图像已保存: {save_path}")
    return [save_path]


# ------------------------------------------------------------- heatmaps --
def _get_region_shapes(shp_path, region_key):
    import geopandas as gpd
    from shapely.ops import unary_union

    config = REGION_CONFIGS[region_key]
    gdf = gpd.read_file(shp_path)
    if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)
    if "ct_adcode" not in gdf.columns:
        for name in ("adcode", "code", "ADCODE", "CODE", "ad_code"):
            if name in gdf.columns:
                gdf["ct_adcode"] = gdf[name]
                break
    gdf["ct_adcode"] = gdf["ct_adcode"].astype(str)
    gdf["prov_code"] = gdf["ct_adcode"].str[:2]
    mask_gdf = gdf[gdf["prov_code"].isin(config["prefixes"])]
    if config["is_province_level"]:
        internal_geoms = list(mask_gdf.dissolve(by="prov_code").geometry)
    else:
        internal_geoms = list(mask_gdf.geometry)
    mask_outline = unary_union(internal_geoms)
    return mask_outline, internal_geoms


def _plot_heatmap_common(df, col_name, cfg, out_name, title_suffix="") -> Path:
    """两热力图脚本共享的插值+掩膜绘图管线（原版 cartopy 方案）。"""
    import cartopy.crs as ccrs
    from cartopy.mpl.patch import geos_to_path
    from cartopy.mpl.ticker import LatitudeFormatter, LongitudeFormatter
    from matplotlib.path import Path as MplPath
    from matplotlib.patches import PathPatch
    from scipy.interpolate import griddata

    region = cfg.get("region", "BTH")
    shp_path = cfg["china_shp"]
    grid_resolution = int(cfg.get("grid_resolution", 80))
    config = REGION_CONFIGS[region]
    extent_plot = config["extent"]
    mask_outline, internal_list = _get_region_shapes(shp_path, region)

    fig = plt.figure(figsize=(10, 8))
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
    ax.set_extent(extent_plot, crs=ccrs.PlateCarree())
    ax.patch.set_facecolor(SEA_COLOR)

    grid_x = np.linspace(extent_plot[0] - 1, extent_plot[1] + 1, grid_resolution)
    grid_y = np.linspace(extent_plot[2] - 1, extent_plot[3] + 1, grid_resolution)
    grid_lon, grid_lat = np.meshgrid(grid_x, grid_y)

    z_lin = griddata((df["LON/deg"], df["LAT/deg"]), df[col_name],
                     (grid_lon, grid_lat), method="linear")
    z_near = griddata((df["LON/deg"], df["LAT/deg"]), df[col_name],
                      (grid_lon, grid_lat), method="nearest")
    grid_z = np.where(np.isnan(z_lin), z_near, z_lin)

    vals = df[col_name].dropna().values
    vmin, vmax = np.nanpercentile(vals, [5, 95])
    if vmin == vmax:
        vmin, vmax = vmin - 0.5, vmax + 0.5

    pm = ax.pcolormesh(grid_lon, grid_lat, grid_z, transform=ccrs.PlateCarree(),
                       cmap="jet", shading="auto", vmin=vmin, vmax=vmax, zorder=3)
    paths = geos_to_path(mask_outline)
    patch = PathPatch(MplPath.make_compound_path(*paths), transform=ax.transData)
    pm.set_clip_path(patch)

    ax.add_geometries(internal_list, ccrs.PlateCarree(), facecolor="none",
                      edgecolor=PROVINCE_LINE_COLOR,
                      linewidth=INTERNAL_LINEWIDTH, zorder=5)
    ax.add_geometries([mask_outline], ccrs.PlateCarree(), facecolor="none",
                      edgecolor="black", linewidth=OUTLINE_LINEWIDTH, zorder=6)
    for spine in ax.spines.values():
        spine.set_linewidth(FRAME_LINEWIDTH)

    if region == "CHN":
        lon_ticks, lat_ticks = np.arange(80, 141, 20), np.arange(20, 61, 10)
    elif region == "BTH":
        lon_ticks, lat_ticks = np.arange(114, 121, 2), np.arange(36, 44, 2)
    else:
        lon_ticks, lat_ticks = np.arange(105, 111, 1), np.arange(28, 33, 1)
    ax.set_xticks(lon_ticks, crs=ccrs.PlateCarree())
    ax.set_yticks(lat_ticks, crs=ccrs.PlateCarree())
    ax.xaxis.set_major_formatter(LongitudeFormatter())
    ax.yaxis.set_major_formatter(LatitudeFormatter())
    ax.tick_params(direction="in", top=True, right=True,
                   width=FRAME_LINEWIDTH, length=6)

    ax.set_title(f"{col_name} 空间分布{title_suffix} | {region}",
                 fontsize=16, fontweight="bold", pad=15)
    cbar = fig.colorbar(pm, orientation="horizontal", pad=0.05, shrink=0.8)
    cbar.set_label(col_name, fontsize=14)
    exact_ticks = np.linspace(vmin, vmax, 5)
    cbar.set_ticks(exact_ticks)
    cbar.ax.xaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f"))

    output_dir = Path(cfg["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / out_name
    plt.savefig(out_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"[生成成功] 图像已保存: {out_path}")
    return out_path


def plot_heatmap_rmse(cfg) -> list[Path]:
    txt_file = cfg["txt_file"]
    plot_vars = cfg.get("plot_vars") or ["ZTD_RMSE(mm)", "PWV_RMSE(mm)"]
    df = parse_by_station_txt(txt_file)
    if df.empty:
        raise ValueError(f"未从 {txt_file} 解析到数据行")
    outs = []
    for col_name in plot_vars:
        if col_name not in df.columns:
            print(f"[WARN] 未找到包含 {col_name} 的列，跳过")
            continue
        out_name = Path(txt_file).name.replace(".txt", f"_{col_name}_Map.png")
        outs.append(_plot_heatmap_common(df, col_name, cfg, out_name))
    return outs


def plot_heatmap_value(cfg) -> list[Path]:
    txt_file = cfg["txt_file"]
    var_convert = cfg.get("var_convert") or {"ZTD(m)": 1000.0, "PWV(m)": 1000.0}
    plot_vars = cfg.get("plot_vars") or list(var_convert)

    df = read_ztd_result(txt_file)
    if df.empty:
        raise ValueError(f"未从 {txt_file} 读取到数据")

    m = re.search(r"ztd_(\d{10})_sit_", Path(txt_file).name)
    time_str = m.group(1) if m else ""
    title_suffix = f" ({time_str})" if time_str else ""

    outs = []
    for var in plot_vars:
        factor = float(var_convert.get(var, 1.0))
        col = f"VAR_{var}"
        df[col] = df[var] * factor if var in df.columns else np.nan
        out_name = f"{Path(txt_file).name}_{var}_Map.png"
        outs.append(_plot_heatmap_common(df, col, cfg, out_name, title_suffix))
    return outs


def run_plot(cfg: dict) -> dict:
    """运行入口（配置节 metcom_plot.yaml）。"""
    plot_type = cfg.get("plot_type", "sequence")
    if plot_type == "sequence":
        outs = plot_sequence(cfg)
    elif plot_type == "heatmap_rmse":
        outs = plot_heatmap_rmse(cfg)
    elif plot_type == "heatmap_value":
        outs = plot_heatmap_value(cfg)
    else:
        raise ValueError(f"未知绘图类型: {plot_type}")
    return {"type": plot_type, "files": [str(p) for p in outs]}
