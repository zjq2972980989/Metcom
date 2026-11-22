"""DEM 区域裁剪（ShpDemToRegion_Masked_region.m 的 Python 复刻）。

功能：从全球地形数据（GMT netCDF，0.05°）裁剪目标区域，支持
  模式A 行政边界掩膜（apply_mask=true，geopandas + shapely 实现 inpolygon）
  模式B 经纬度矩形裁剪（apply_mask=false）
输出 CF-1.6 NetCDF：lon(nLon)/lat(nLat)/elevation(lon, lat)（与原版一致）。

与原版的差异（见 docs/porting_notes.md）：
  - inpolygon 的边界点语义用 shapely.covered_by 近似（含边界点）；
  - MATLAB colon 浮点步进改为 linspace 精确端点（差 <1e-13°）。
"""

from __future__ import annotations

from pathlib import Path

import netCDF4
import numpy as np


def _colon(a: float, step: float, b: float) -> np.ndarray:
    """MATLAB a:step:b 的等价实现（端点精确，等距）。"""
    n = int(round((b - a) / step)) + 1
    return np.linspace(a, a + (n - 1) * step, n)


def load_global_dem(dem_path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """读取 GMT 经典 netCDF（变量 X/Y/'Uniform Lattice #0'）并标准化为
    升序 lon[-180,180) / 升序 lat[-90,90] 与 (n_lat, n_lon) 高程矩阵。"""
    ds = netCDF4.Dataset(dem_path, "r")
    try:
        # 兼容经典命名与常规命名
        lon_name = "X" if "X" in ds.variables else ("lon" if "lon" in ds.variables else "x")
        lat_name = "Y" if "Y" in ds.variables else ("lat" if "lat" in ds.variables else "y")
        elev_name = ("Uniform Lattice #0" if "Uniform Lattice #0" in ds.variables
                     else ("z" if "z" in ds.variables else "elevation"))
        lon = np.asarray(ds.variables[lon_name][:], dtype=float).ravel()
        lat = np.asarray(ds.variables[lat_name][:], dtype=float).ravel()
        elev = np.asarray(ds.variables[elev_name][:], dtype=float)
    finally:
        ds.close()

    # 维度顺序标准化：统一为 (lat, lon) 行列约定（ncinfo 维度检查的等价逻辑）
    if elev.ndim != 2:
        raise ValueError(f"DEM 高程变量维度异常: {elev.shape}")
    if elev.shape == (lat.size, lon.size):
        pass
    elif elev.shape == (lon.size, lat.size):
        elev = elev.T          # (lon, lat) -> (lat, lon)
    else:
        raise ValueError(f"DEM 维度 {elev.shape} 与坐标 (lon={lon.size}, lat={lat.size}) 不符")

    # 经度归一化 [-180, 180) 并升序
    lon = np.mod(lon + 180.0, 360.0) - 180.0
    sort_idx = np.argsort(lon, kind="stable")
    lon = lon[sort_idx]
    elev = elev[:, sort_idx]

    # 纬度升序（降序则翻转）
    if lat.size >= 2 and lat[0] > lat[-1]:
        lat = lat[::-1].copy()
        elev = elev[::-1, :]
    return lon, lat, elev


def _interp_dem(lon, lat, elev, lons_q: np.ndarray, lats_q: np.ndarray) -> np.ndarray:
    """interp2(lon, lat, elev', Xq, Yq, 'linear') 等价（范围外 NaN）。"""
    from scipy.interpolate import RegularGridInterpolator
    interp = RegularGridInterpolator(
        (lat, lon), elev, method="linear", bounds_error=False, fill_value=np.nan)
    lon_g, lat_g = np.meshgrid(lons_q, lats_q)          # (n_lat, n_lon)
    pts = np.column_stack([lat_g.ravel(), lon_g.ravel()])
    vals = interp(pts).reshape(lats_q.size, lons_q.size)
    return vals


def _mask_features(city_shp: Path, prefixes: list[str],
                   is_province_level: bool):
    """按行政区划编码前缀筛选要素（对应 MATLAB shaperead + adcode 匹配）。"""
    import geopandas as gpd
    gdf = gpd.read_file(city_shp)
    adcode_col = None
    for cand in ("ct_adcode", "adcode"):
        if cand in gdf.columns:
            adcode_col = cand
            break
    if adcode_col is None:
        raise KeyError(f"shapefile 中未找到行政区划编码字段(ct_adcode/adcode): {city_shp}")

    codes = gdf[adcode_col].astype(str).str.strip().str.zfill(6)

    def match(code: str) -> bool:
        return any(code.startswith(p) for p in prefixes)

    if is_province_level:
        sel = codes.str[:2].apply(match)
    else:
        sel = codes.apply(lambda c: match(c[:4]) or match(c[:2]))
    return gdf[sel]


def _apply_mask(elev_grid: np.ndarray, lons_q: np.ndarray, lats_q: np.ndarray,
                geoms, resolution: float) -> np.ndarray:
    """行政掩膜：区域外 NaN（对应 MATLAB inpolygon + bbox 预筛选）。

    geoms: shapely 几何列表（MultiPolygon/Polygon，已按多部件拆分）。
    """
    from shapely import covers
    from shapely.geometry import Point

    masked = np.full_like(elev_grid, np.nan)
    pad = resolution * 0.5
    lon_g, lat_g = np.meshgrid(lons_q, lats_q)

    # 先求所有几何的联合外接矩形预筛选（等价 MATLAB 的逐要素 bbox 预筛）
    from shapely import unary_union
    union = unary_union(list(geoms))
    minx, miny, maxx, maxy = union.bounds

    cand = ((lon_g >= minx - pad) & (lon_g <= maxx + pad)
            & (lat_g >= miny - pad) & (lat_g <= maxy + pad))
    ci, cj = np.where(cand)
    if ci.size == 0:
        return masked

    pts = [Point(lon_g[i, j], lat_g[i, j]) for i, j in zip(ci, cj)]
    # inpolygon 语义（含边界点）≈ covers
    hits = np.zeros(ci.size, dtype=bool)
    for geom in geoms:
        hits |= covers(geom, pts)  # shapely 2.x 支持数组化的几何-点判断
    masked[ci[hits], cj[hits]] = elev_grid[ci[hits], cj[hits]]
    return masked


def _write_dem_nc(out_file: Path, lons_q: np.ndarray, lats_q: np.ndarray,
                  elev_out: np.ndarray, mask_mode: str, resolution: float) -> None:
    """写 CF-1.6 NetCDF（nccreate/ncwrite 等价：elevation 维度为 (lon, lat)）。"""
    out_file.parent.mkdir(parents=True, exist_ok=True)
    ds = netCDF4.Dataset(out_file, "w", format="NETCDF4")
    try:
        ds.createDimension("lon", lons_q.size)
        ds.createDimension("lat", lats_q.size)
        v_lon = ds.createVariable("lon", "f8", ("lon",))
        v_lon.units = "degrees_east"
        v_lon[:] = lons_q
        v_lat = ds.createVariable("lat", "f8", ("lat",))
        v_lat.units = "degrees_north"
        v_lat[:] = lats_q
        v_elev = ds.createVariable("elevation", "f8", ("lon", "lat"),
                                   zlib=True, complevel=4)
        v_elev.long_name = "elevation"
        v_elev.units = "meters"
        v_elev[:] = elev_out.T.astype(np.float64)   # (lat, lon) -> (lon, lat)
        ds.mask_mode = mask_mode
        ds.Conventions = "CF-1.6"
        ds.resolution_deg = float(resolution)
    finally:
        ds.close()


def run_dem_clip(cfg: dict) -> dict:
    """执行 DEM 区域裁剪（配置节 metcom_gridgen.yaml 的 dem: 节）。"""
    base = Path(cfg.get("workdir", "."))
    dem_path = Path(cfg["dem_path"])
    dem_path = dem_path if dem_path.is_absolute() else base / dem_path
    city_shp = Path(cfg["city_shp"])
    city_shp = city_shp if city_shp.is_absolute() else base / city_shp
    output_dir = Path(cfg["output_dir"])
    output_dir = output_dir if output_dir.is_absolute() else base / output_dir
    resolution = float(cfg["resolution"])

    lon, lat, elev = load_global_dem(dem_path)
    print(f"全球 DEM 加载完成: {elev.shape[0]} (lat) x {elev.shape[1]} (lon)")

    results = {"regions": [], "failed": []}
    for region in cfg.get("regions", []):
        name = region["name"]
        try:
            ext = region["extent"]
            lons_q = _colon(float(ext[0]), resolution, float(ext[1]))
            lats_q = _colon(float(ext[2]), resolution, float(ext[3]))

            elev_grid = _interp_dem(lon, lat, elev, lons_q, lats_q)

            if region.get("apply_mask"):
                sel = _mask_features(city_shp, list(region.get("prefixes", [])),
                                     bool(region.get("is_province_level")))
                geoms = [g for g in sel.geometry
                         if g is not None and not g.is_empty]
                elev_out = _apply_mask(elev_grid, lons_q, lats_q, geoms, resolution)
                mask_mode = "administrative_boundary"
            else:
                elev_out = elev_grid
                mask_mode = "rectangular_extent"

            n_valid = int(np.isfinite(elev_out).sum())
            pct = 100.0 * n_valid / elev_out.size
            print(f"区域 {name}: 有效格点 {n_valid}/{elev_out.size} ({pct:.1f}%)")

            out_file = output_dir / region["file_name"]
            _write_dem_nc(out_file, lons_q, lats_q, elev_out, mask_mode, resolution)
            results["regions"].append({"name": name, "file": str(out_file),
                                       "valid": n_valid})
        except Exception as e:  # noqa: BLE001 —— 与原版 try/catch 逐区域容错一致
            print(f"[失败] 区域 {name}: {e}")
            results["failed"].append({"name": name, "error": str(e)})
    return results
