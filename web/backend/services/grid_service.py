"""Grid 模式：DEM 有效节点 → 正高 / 椭球高站点文件。

生产链路（CUSTOM 格网任务）——``build_grid_height_files``——**优先复用**
``metcom/gridgen/orth_to_ell.py`` 的原始实现：把请求范围裁剪出的临时 DEM 交给
``process_dem_file``，由原实现完成整条科学链路::

    GridRequest
        -> _clip_dem_to_bounds()              仅按范围裁剪 DEM（不重采样/不重写算法）
        -> metcom.gridgen.orth_to_ell.process_dem_file()
             DEM 有效节点提取 -> 按 (lat, lon) 排序 -> EGM2008 双线性插值
             -> 椭球高 h = H + N -> %4s %12.6f %12.6f %10.3f 定宽文本
        -> custom_grid_orthometric.txt / custom_grid_ellipsoidal.txt

站点集来自 DEM 有效节点（NaN 缺测节点按原实现剔除），站号沿用 Python 原生
``A000…`` 规则，正高文件与椭球高文件逐行对应。单任务上限仍为
``validation.MAX_GRID_POINTS``（10000）：超限直接拒绝，**不静默截断**。

临时 DEM 与中间产物只落在当前任务目录内的独立临时子目录，异常时仅清理本次
创建的临时目录；不触碰正式 DEM、EGM2008 文件或已有缓存。

旧的无 EGM 链路 ``build_grid_site_file`` 为阶段 2 保留（仅离线测试引用），
仍按请求范围生成规则格点并最近邻采样 DEM、只写正高文件；生产任务不使用该入口。

空间分辨率只来自数据源自身的 ``SourceSpec.spatial_resolution``（``(lat_step, lon_step)``）。
"""

from __future__ import annotations

import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import netCDF4
import numpy as np

from config import PROJECT_ROOT, get_settings
from engine import adapter
from metcom.gridgen.orth_to_ell import process_dem_file
from services import egm_gridgen_adapter, elevation, validation
from services.errors import InvalidRequestError, TaskExecutionError

# 生产链路唯一允许使用的 DEM：仓库内已有 benchmark（0.125° 正交高程）。
DEFAULT_DEM_PATH = PROJECT_ROOT / "benchmarks" / "DEM_Output" / "bth_dem_0125deg_rect.nc"
GRIDGEN_SUPPORTED_RESOLUTION = (0.25, 0.25)

# 旧的无 EGM 链路站名前缀：G000001 起连续编号（生产链路用 Python 原生 A000…）。
_GRID_NAME_PREFIX = "G"

# DEM 覆盖判定容差：只吸收浮点端点误差，不做任何"约等于"放宽。
_DEM_COORD_TOL = 1e-9


def _colon(start: float, step: float, stop: float) -> np.ndarray:
    """``a:step:b`` 等价实现（与 metcom/gridgen 的 ``_colon`` 数值语义一致）。"""
    count = int(round((stop - start) / step)) + 1
    return np.linspace(start, start + (count - 1) * step, count)


def _bounds_metadata(
    lons: np.ndarray,
    lats: np.ndarray,
    lon_min: float,
    lon_max: float,
    lat_min: float,
    lat_max: float,
) -> tuple[dict[str, float], dict[str, float]]:
    """Return requested bounds and bounds derived from the generated axes."""
    requested_bounds = {
        "lon_min": float(lon_min),
        "lon_max": float(lon_max),
        "lat_min": float(lat_min),
        "lat_max": float(lat_max),
    }
    effective_bounds = {
        "lon_min": float(lons[0]),
        "lon_max": float(lons[-1]),
        "lat_min": float(lats[0]),
        "lat_max": float(lats[-1]),
    }
    return requested_bounds, effective_bounds


def _grid_steps(spec: Any) -> tuple[float, float]:
    """取数据源原生分辨率，返回 (lat_step, lon_step)。"""
    lat_step, lon_step = spec.spatial_resolution
    return float(lat_step), float(lon_step)


def _require_gridgen_resolution(spec: Any) -> None:
    """Reject sources whose resolution is not covered by the verified Gridgen grid."""
    lat_step, lon_step = _grid_steps(spec)
    expected_lat, expected_lon = GRIDGEN_SUPPORTED_RESOLUTION
    aligned = (
        math.isclose(lat_step, expected_lat, rel_tol=0.0, abs_tol=1e-12)
        and math.isclose(lon_step, expected_lon, rel_tol=0.0, abs_tol=1e-12)
    )
    if aligned:
        return

    expected_path = adapter.resolve_egm_path(spec)
    raise InvalidRequestError(
        f"{spec.name} 的分辨率为 {lat_step:g}° × {lon_step:g}°，当前 Gridgen "
        f"EGM2008 适配器仅验证支持 {expected_lat:g}° × {expected_lon:g}°；"
        f"不会使用未经验证的 {expected_path} 生成椭圆高。"
    )


def resolve_gridgen_egm_path(dataset: str) -> Path:
    """Resolve Gridgen EGM input from the same configured directory as retrieval."""
    spec = validation.require_source(dataset)
    _require_gridgen_resolution(spec)

    settings = get_settings()
    egm_dir = settings.engine_egm_dir
    expected_path = adapter.resolve_egm_path(spec)
    if not egm_dir.is_dir():
        raise InvalidRequestError(
            f"{spec.name} 的 EGM2008 目录配置无效，预期目录：{egm_dir}。"
        )
    if not expected_path.is_file():
        raise InvalidRequestError(
            f"{spec.name} 缺少 EGM2008 文件，预期路径：{expected_path}。"
        )
    return expected_path


def generate_grid_coordinates(
    *,
    dataset: str,
    lon_min: float,
    lon_max: float,
    lat_min: float,
    lat_max: float,
) -> tuple[np.ndarray, np.ndarray]:
    """按数据源原生分辨率生成规则格点，返回 ``(lons, lats)``（均升序）。

    只生成坐标，不读 DEM、不写文件。完整的请求校验由 ``validate_grid`` 负责；
    这里只解析数据源分辨率，数据源不存在时抛 ``InvalidRequestError``。
    """
    spec = validation.require_source(dataset)
    lat_step, lon_step = _grid_steps(spec)
    lons = _colon(float(lon_min), lon_step, float(lon_max))
    lats = _colon(float(lat_min), lat_step, float(lat_max))
    return lons, lats


def build_grid_site_file(
    *,
    dataset: str,
    lon_min: float,
    lon_max: float,
    lat_min: float,
    lat_max: float,
    output_path: Path | str,
    dem_path: Path | str | None = None,
) -> dict[str, Any]:
    """生成格点站点文件，返回任务无关的 metadata。

    顺序固定为 **lat 外层、lon 内层**（``np.meshgrid(lons, lats)`` 的 C 序展开），
    因此 ``G000001`` 是第一行第一列，编号从 1 起连续。

    ``dem_path`` 省略时使用仓库内 benchmark DEM。输出目录不存在时自动创建；
    调用方负责给出目标路径（本函数不猜测任何永久数据目录）。
    """
    spec = validation.require_source(dataset)
    lat_step, lon_step = _grid_steps(spec)
    lons = _colon(float(lon_min), lon_step, float(lon_max))
    lats = _colon(float(lat_min), lat_step, float(lat_max))

    lon_grid, lat_grid = np.meshgrid(lons, lats)   # (n_lat, n_lon)
    flat_lons = lon_grid.ravel()                   # C 序：lat 外层、lon 内层
    flat_lats = lat_grid.ravel()
    points = int(flat_lons.size)
    requested_bounds, effective_bounds = _bounds_metadata(
        lons, lats, lon_min, lon_max, lat_min, lat_max)

    # 防御性复核：调用方通常已通过 validate_grid，这里复用同一上限常量，
    # 避免直接调用时生成超大文件（不复制 validate_grid 的其余校验）。
    if points > validation.MAX_GRID_POINTS:
        raise InvalidRequestError(
            f"当前区域按 {spec.name} 原生 {lon_step:g}° × {lat_step:g}° 分辨率预计生成 "
            f"{points} 个格点，超过 Web V1.0 单任务最大 {validation.MAX_GRID_POINTS} 个格点。"
        )

    dem = elevation.load_dem(dem_path if dem_path is not None else DEFAULT_DEM_PATH)
    heights = elevation.sample_nearest(dem, flat_lons, flat_lats)

    if heights.size != points:
        raise TaskExecutionError(
            f"格点高程数量 {heights.size} 与格点数 {points} 不一致。"
        )
    if not np.all(np.isfinite(heights)):
        raise InvalidRequestError("格点高程包含 NaN 或 Inf，拒绝写出站点文件。")

    names = [f"{_GRID_NAME_PREFIX}{index:06d}" for index in range(1, points + 1)]
    lines = [
        f"{name} {lat:.6f} {lon:.6f} {height:.3f}"
        for name, lat, lon, height in zip(names, flat_lats, flat_lons, heights)
    ]

    output = Path(output_path)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError as exc:
        raise TaskExecutionError(f"格网站点文件写入失败：{output}（{exc}）") from exc

    return {
        "dataset": spec.key,
        "resolution": {"lat_step": lat_step, "lon_step": lon_step},
        "bounds": dict(effective_bounds),
        "requested_bounds": requested_bounds,
        "effective_bounds": effective_bounds,
        "grid": {
            "n_lon": int(lons.size),
            "n_lat": int(lats.size),
            "points": points,
        },
        "site_file": str(output),
        "elevation_source": dem.source,
    }


def _resolve_gridgen_egm_for_resolution(spec: Any, output_res: float) -> Path:
    """按输出分辨率选择 EGM2008 文件；细分辨率缺失时明确报错，绝不回退粗分辨率。"""
    if output_res >= 0.25:
        return resolve_gridgen_egm_path(spec.key)
    raise InvalidRequestError(
        f"{spec.name} 的格点分辨率 {output_res:g}° 需要细分辨率（<0.25°）EGM2008 "
        "数据，当前适配器仅提供 0.25° 粗分辨率文件；不会回退到粗分辨率数据。"
    )


def _read_source_dem(dem_path: Path | str) -> elevation.DemGrid:
    """读取源 DEM（允许 NaN 缺测节点），返回规范化的 ``DemGrid``。

    复用 ``services.elevation.load_dem(allow_missing=True)``：按**维度名**识别
    高程维序（正方形网格也正确）并统一坐标为升序；缺测节点保留，交由原实现剔除。
    """
    return elevation.load_dem(Path(dem_path), allow_missing=True)


def _dem_coverage_error(lon, lat, lon_min, lon_max,
                        lat_min, lat_max) -> InvalidRequestError:
    """构造覆盖范围错误（消息含 ``exceeds DEM coverage`` 以保持既有契约）。"""
    return InvalidRequestError(
        f"Grid range lon[{lon_min:g},{lon_max:g}] lat[{lat_min:g},{lat_max:g}] "
        f"exceeds DEM coverage lon[{float(lon[0]):g},{float(lon[-1]):g}] "
        f"lat[{float(lat[0]):g},{float(lat[-1]):g}]。"
    )


def _clip_dem_to_bounds(lon, lat, elev,
                        lon_min, lon_max, lat_min, lat_max):
    """按请求范围裁剪 DEM 子网格（纯切片，不重采样）；DEM 未覆盖时直接报错。"""
    if lon.size < 2 or lat.size < 2:
        raise InvalidRequestError(
            f"DEM 坐标轴长度不足，无法裁剪格点：lon={lon.size}，lat={lat.size}。"
        )
    tol = _DEM_COORD_TOL
    if (lon_min < lon[0] - tol or lon_max > lon[-1] + tol
            or lat_min < lat[0] - tol or lat_max > lat[-1] + tol):
        raise _dem_coverage_error(lon, lat, lon_min, lon_max, lat_min, lat_max)

    lon_mask = (lon >= lon_min - tol) & (lon <= lon_max + tol)
    lat_mask = (lat >= lat_min - tol) & (lat <= lat_max + tol)
    sub_lon, sub_lat = lon[lon_mask], lat[lat_mask]
    if (sub_lon.size == 0 or sub_lat.size == 0
            or sub_lon[0] > lon_min + tol or sub_lon[-1] < lon_max - tol
            or sub_lat[0] > lat_min + tol or sub_lat[-1] < lat_max - tol):
        raise _dem_coverage_error(lon, lat, lon_min, lon_max, lat_min, lat_max)
    return sub_lon, sub_lat, elev[np.ix_(lat_mask, lon_mask)]


def _write_dem_netcdf(path: Path, lon: np.ndarray, lat: np.ndarray,
                      elev: np.ndarray) -> None:
    """写临时 DEM（变量 lon/lat/elevation，elevation 维序 (lat, lon)）。

    仅供 ``process_dem_file`` 读取；临时文件只落在当前任务目录内。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    ds = netCDF4.Dataset(path, "w", format="NETCDF4")
    try:
        ds.createDimension("lon", lon.size)
        ds.createDimension("lat", lat.size)
        v_lon = ds.createVariable("lon", "f8", ("lon",))
        v_lon[:] = lon
        v_lat = ds.createVariable("lat", "f8", ("lat",))
        v_lat[:] = lat
        v_elev = ds.createVariable("elevation", "f8", ("lat", "lon"))
        v_elev[:] = elev
    finally:
        ds.close()


def _site_line_count(path: Path) -> int:
    """统计站点文件有效行数（跳过空行）。"""
    with open(path, "r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def build_grid_height_files(
    *,
    dataset: str,
    lon_min: float,
    lon_max: float,
    lat_min: float,
    lat_max: float,
    orthometric_path: Path | str,
    ellipsoidal_path: Path | str,
    dem_path: Path | str | None = None,
    egm_mat_path: Path | str | None = None,
    cache_dir: Path | str | None = None,
) -> dict[str, Any]:
    """复用 Python gridgen：由 DEM 有效节点生成正高 / h = H + N 站点文件。

    站点集、排序、EGM2008 双线性插值、椭球高换算与 ``%4s %12.6f %12.6f %10.3f``
    文本格式全部由 ``metcom.gridgen.orth_to_ell.process_dem_file`` 完成；本函数
    只负责「按请求范围裁剪临时 DEM → 选择 EGM2008 → 调用原实现 → 落到任务目录」，
    不复制任何科学计算逻辑。

    请求范围内有效节点超过 ``validation.MAX_GRID_POINTS`` 时直接抛
    ``InvalidRequestError``，不静默截断；返回的 ``grid.points`` 为实际有效节点数。
    """
    spec = validation.require_source(dataset)
    _require_gridgen_resolution(spec)
    lat_step, lon_step = _grid_steps(spec)
    output_res = float(lon_step)

    dem_source = Path(dem_path) if dem_path is not None else DEFAULT_DEM_PATH
    try:
        dem = _read_source_dem(dem_source)
    except InvalidRequestError:
        raise
    except (OSError, ValueError) as exc:
        raise InvalidRequestError(f"DEM 文件不可读或格式异常：{dem_source}（{exc}）") from exc
    lon, lat, elev = dem.lons, dem.lats, dem.elevation
    base_res = float(abs(lon[1] - lon[0])) if lon.size >= 2 else 0.0
    if not (base_res > 0.0):
        raise InvalidRequestError(f"DEM 经度分辨率无效：{base_res}。")

    sub_lon, sub_lat, sub_elev = _clip_dem_to_bounds(
        lon, lat, elev,
        float(lon_min), float(lon_max), float(lat_min), float(lat_max))

    step = max(1, int(round(output_res / base_res)))
    grid_lons = sub_lon[::step]
    grid_lats = sub_lat[::step]
    grid_elev = sub_elev[::step, ::step]

    # 与原实现一致：有效节点 = 降采样后非 NaN 的节点；超上限直接拒绝，不静默截断。
    valid_nodes = int(np.count_nonzero(~np.isnan(grid_elev)))
    if valid_nodes == 0:
        raise InvalidRequestError("请求范围内没有有效的 DEM 节点（全部为 NaN 缺测）。")
    if valid_nodes > validation.MAX_GRID_POINTS:
        raise InvalidRequestError(
            f"请求范围内有效 DEM 节点 {valid_nodes} 个，超过 Web V1.0 单任务最大 "
            f"{validation.MAX_GRID_POINTS} 个节点；不会静默截断，请缩小区域。"
        )

    egm_source = (
        Path(egm_mat_path) if egm_mat_path is not None
        else _resolve_gridgen_egm_for_resolution(spec, output_res)
    )
    try:
        egm = egm_gridgen_adapter.load_gridgen_egm(egm_source, cache_dir=cache_dir)
    except egm_gridgen_adapter.EgmGridgenError as exc:
        raise TaskExecutionError(f"EGM2008 Gridgen 处理失败：{exc}") from exc

    ortho_path = Path(orthometric_path)
    ellip_path = Path(ellipsoidal_path)
    if ortho_path.resolve() == ellip_path.resolve():
        raise TaskExecutionError("正高文件与椭球高文件不能是同一路径。")
    task_dir = ortho_path.parent
    try:
        task_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise TaskExecutionError(f"任务目录不可写：{task_dir}（{exc}）") from exc

    # 临时文件只落在当前任务目录内的独立临时目录；异常时仅清理本次创建的目录。
    # 写出「裁剪后但未降采样」的 DEM，降采样由原实现按 base_res/output_res 完成。
    temp_root = Path(tempfile.mkdtemp(prefix=".gridgen_tmp_", dir=task_dir))
    try:
        temp_dem = temp_root / "clipped_dem.nc"
        _write_dem_netcdf(temp_dem, sub_lon, sub_lat, sub_elev)
        try:
            produced_ortho, produced_ellip = process_dem_file(
                temp_dem, egm.interpolator,
                output_prefix="gridgen",
                output_res=output_res,
                base_res=base_res,
                output_dir=temp_root / "out",
                max_stations=validation.MAX_GRID_POINTS,
            )
        except ValueError as exc:               # 原实现「无有效数据点」等
            raise InvalidRequestError(f"格点站点生成失败：{exc}") from exc
        points = _site_line_count(produced_ortho)
        if points == 0 or _site_line_count(produced_ellip) != points:
            raise TaskExecutionError("正高文件与椭球高站点文件记录数不一致。")
        os.replace(produced_ortho, ortho_path)
        os.replace(produced_ellip, ellip_path)
    except OSError as exc:
        raise TaskExecutionError(f"格网双高度站点文件写入失败：{exc}") from exc
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    requested_bounds = {
        "lon_min": float(lon_min), "lon_max": float(lon_max),
        "lat_min": float(lat_min), "lat_max": float(lat_max),
    }
    effective_bounds = {
        "lon_min": float(grid_lons[0]), "lon_max": float(grid_lons[-1]),
        "lat_min": float(grid_lats[0]), "lat_max": float(grid_lats[-1]),
    }

    return {
        "dataset": spec.key,
        "resolution": {"lat_step": lat_step, "lon_step": lon_step},
        "bounds": dict(effective_bounds),
        "requested_bounds": requested_bounds,
        "effective_bounds": effective_bounds,
        "grid": {
            "n_lon": int(grid_lons.size),
            "n_lat": int(grid_lats.size),
            "points": int(points),
        },
        "site_file": str(ortho_path),
        "orthometric_site_file": str(ortho_path),
        "ellipsoidal_site_file": str(ellip_path),
        "height_datum": {
            "orthometric": "DEM valid-node orthometric H (Python gridgen)",
            "ellipsoidal": "H + EGM2008 N",
            "geoid": "EGM2008 Gridgen bilinear interpolation",
        },
        "egm_source": str(egm.source_path),
        "egm_gridgen_text": str(egm.text_path),
        "elevation_source": str(dem.source),
    }


__all__ = [
    "DEFAULT_DEM_PATH",
    "GRIDGEN_SUPPORTED_RESOLUTION",
    "build_grid_height_files",
    "build_grid_site_file",
    "generate_grid_coordinates",
    "resolve_gridgen_egm_path",
]
