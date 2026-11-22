"""业务参数校验（后端必须自己校验，不能依赖前端）。

覆盖阶段三的两类请求：数据下载（dataset + 时间）与参数计算（dataset + 输出参数 + 时间）。
校验失败一律抛 ``InvalidRequestError``，由路由层转成 422。
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Iterable

from engine import adapter
from services.errors import InvalidRequestError


def require_source(dataset: Any) -> Any:
    """校验数据源，返回引擎数据源规格。"""
    key = str(dataset or "").strip().lower()
    if not key:
        raise InvalidRequestError("请选择数据源。")
    spec = adapter.source_spec(key)
    if spec is None:
        raise InvalidRequestError(f"不支持的数据源：{dataset}。")
    return spec


def parse_iso_datetime(value: Any, label: str) -> datetime:
    """解析 ISO 8601 日期时间；失败时给出面向用户的提示。"""
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        raise InvalidRequestError(f"请选择{label}。")
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise InvalidRequestError(f"{label}格式不正确，请使用日期时间选择器。") from exc


def parse_time_range(start_value: Any, end_value: Any) -> tuple[datetime, datetime]:
    """校验时间范围：可解析、且 start <= end。"""
    start = parse_iso_datetime(start_value, "开始时间")
    end = parse_iso_datetime(end_value, "结束时间")
    if start > end:
        raise InvalidRequestError("开始时间不能晚于结束时间。")
    return start, end


def validate_parameters(parameters: Iterable[Any]) -> list[str]:
    """校验输出参数：至少一个，且均在引擎支持的参数集合内。"""
    selected: list[str] = []
    for item in parameters or []:
        key = item.value if hasattr(item, "value") else str(item)
        key = key.strip()
        if key not in adapter.PARAM_COLUMNS:
            raise InvalidRequestError(f"不支持的输出参数：{key}。")
        if key not in selected:
            selected.append(key)
    if not selected:
        raise InvalidRequestError("请选择至少一个输出参数。")
    return selected


# 结果文件格式：只开放引擎真实具备写入器的格式（adapter.OUTPUT_FORMATS）。
SUPPORTED_OUTPUT_FORMATS: tuple[str, ...] = adapter.OUTPUT_FORMATS
DEFAULT_OUTPUT_FORMAT = "txt"


def validate_output_format(value: Any) -> str:
    """校验结果文件格式；省略 / 空白时返回平台默认格式。

    只接受引擎真实支持的格式，未知格式一律拒绝（422）——绝不"用户要 A、
    后台静默给 B"。前导点（``.txt``）按惯例归一化后再判定。
    """
    text = str(value or "").strip().lower().lstrip(".")
    if not text:
        return DEFAULT_OUTPUT_FORMAT
    if text not in SUPPORTED_OUTPUT_FORMATS:
        raise InvalidRequestError(
            "不支持的结果文件格式：" + str(value) + "；可选 "
            + " / ".join(SUPPORTED_OUTPUT_FORMATS) + "。"
        )
    return text


# 自定义测站的取值范围（与引擎站点文件四列一致；高程为正高）
SITE_NAME_MAX_LEN = 64
SITE_HEIGHT_MIN = -1200.0
SITE_HEIGHT_MAX = 12000.0


def _site_number(value: Any, index: int, label: str) -> float:
    """测站数值字段校验（拒绝 bool / None / 非数字 / NaN / Inf）。"""
    if value is None or isinstance(value, bool):
        raise InvalidRequestError(f"第 {index} 个测站{label}必须是数字。")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidRequestError(f"第 {index} 个测站{label}必须是数字。") from exc
    if not math.isfinite(number):
        raise InvalidRequestError(f"第 {index} 个测站{label}必须是有限数值。")
    return number


def validate_sites(sites: Any) -> list[dict[str, Any]] | None:
    """校验自定义测站列表，返回可直接落盘的规范化列表。

    返回值语义（保持旧请求行为不变）：

    - ``None``（未提供 / 显式传 null）→ 返回 ``None``，调用方沿用平台默认站点文件；
    - 空列表 → 拒绝（要求至少一个测站）；
    - 非空列表 → 逐项校验名称、纬度、经度、高程后返回规范化的四字段 dict 列表。

    站点名称不允许包含空白字符：引擎站点文件是空白分隔的四列格式，
    含空白的名称会被 ``metcom.utils.io.read_site_file`` 拆成多余列而解析失败。
    """
    if sites is None:
        return None
    if not isinstance(sites, (list, tuple)):
        raise InvalidRequestError("测站列表格式不正确。")
    if len(sites) == 0:
        raise InvalidRequestError("请至少添加一个测站，或改用默认站点。")

    cleaned: list[dict[str, Any]] = []
    for index, item in enumerate(sites, start=1):
        if hasattr(item, "model_dump"):
            data = item.model_dump()
        elif isinstance(item, dict):
            data = item
        else:
            raise InvalidRequestError(f"第 {index} 个测站格式不正确。")

        name = str(data.get("name") or "").strip()
        if not name:
            raise InvalidRequestError(f"第 {index} 个测站名称为空。")
        if len(name) > SITE_NAME_MAX_LEN:
            raise InvalidRequestError(
                f"第 {index} 个测站名称过长（最多 {SITE_NAME_MAX_LEN} 个字符）。"
            )
        if any(ch.isspace() for ch in name):
            raise InvalidRequestError(f"第 {index} 个测站名称不能包含空白字符。")

        lat = _site_number(data.get("lat"), index, "纬度")
        lon = _site_number(data.get("lon"), index, "经度")
        height = _site_number(data.get("height"), index, "高程")

        if not -90.0 <= lat <= 90.0:
            raise InvalidRequestError(f"第 {index} 个测站纬度超出范围（-90 ~ 90）：{lat}。")
        if not -180.0 <= lon <= 180.0:
            raise InvalidRequestError(f"第 {index} 个测站经度超出范围（-180 ~ 180）：{lon}。")
        if not SITE_HEIGHT_MIN <= height <= SITE_HEIGHT_MAX:
            raise InvalidRequestError(
                f"第 {index} 个测站高程超出范围（{SITE_HEIGHT_MIN:g} ~ "
                f"{SITE_HEIGHT_MAX:g} m）：{height}。"
            )

        cleaned.append({
            "name": name,
            "lat": float(lat),
            "lon": float(lon),
            "height": float(height),
        })
    return cleaned


# Web V1.0 单任务格点上限。格网模式下每个格点都要独立走一遍站点反演，
# 区域一旦失控（例如 ERA5 0.25° 覆盖全国约 3.6 万点）会直接压垮单任务，
# 因此在创建任务前就必须拒绝。保守取 10000：约等于一个 100×100 的格网。
MAX_GRID_POINTS = 10000

_GRID_ALIGNMENT_REL_TOL = 1e-9
_GRID_ALIGNMENT_ABS_TOL = 1e-9


def _grid_number(value: Any, label: str) -> float:
    """格网范围数值校验（拒绝 bool / None / 非数字 / NaN / Inf）。"""
    if value is None or isinstance(value, bool):
        raise InvalidRequestError(f"{label}必须是数字。")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidRequestError(f"{label}必须是数字。") from exc
    if not math.isfinite(number):
        raise InvalidRequestError(f"{label}必须是有限数值。")
    return number


def grid_point_counts(spec: Any, lon_min: float, lon_max: float,
                      lat_min: float, lat_max: float) -> tuple[int, int, int]:
    """按 gridgen ``_colon`` 语义估算格点数，返回 (n_lon, n_lat, total)。

    引擎 ``metcom/gridgen/shp_dem_to_region.py::_colon`` 为
    ``n = round((b - a) / step) + 1``（两端点都包含），这里保持同一算法，
    使"预计格点数"与将来真正生成的格点数一致。

    ``SourceSpec.spatial_resolution`` 的顺序是 ``(lat_step, lon_step)``，
    此处按该顺序取值，避免经纬写反。
    """
    lat_step, lon_step = spec.spatial_resolution
    n_lon = int(round((lon_max - lon_min) / float(lon_step))) + 1
    n_lat = int(round((lat_max - lat_min) / float(lat_step))) + 1
    return n_lon, n_lat, n_lon * n_lat


def _require_axis_alignment(spec_name: str, axis_label: str,
                            minimum: float, maximum: float, step: float) -> None:
    """Reject spans that are not an integer number of source grid intervals."""
    if not math.isfinite(step) or step <= 0.0:
        raise InvalidRequestError(
            f"{spec_name} 的{axis_label}分辨率无效：{step!r}。"
        )

    span = maximum - minimum
    units = span / step
    nearest_units = round(units)
    if math.isclose(
        units,
        nearest_units,
        rel_tol=_GRID_ALIGNMENT_REL_TOL,
        abs_tol=_GRID_ALIGNMENT_ABS_TOL,
    ):
        return

    raise InvalidRequestError(
        f"{spec_name} 的{axis_label}跨度 {span:g}° 不是数据源原生 {step:g}° "
        "分辨率的整数倍。请将最小、最大边界调整到相隔整数个格点；"
        "系统不会自动四舍五入、扩大或缩小计算区域。"
    )


def require_grid_alignment(spec: Any, lon_min: float, lon_max: float,
                           lat_min: float, lat_max: float) -> None:
    """Require both axes to align exactly to the source grid resolution."""
    lat_step, lon_step = spec.spatial_resolution
    _require_axis_alignment(spec.name, "经度", lon_min, lon_max, float(lon_step))
    _require_axis_alignment(spec.name, "纬度", lat_min, lat_max, float(lat_step))


def validate_grid(request: Any) -> dict[str, Any]:
    """校验格网（Grid）请求，返回规范化结果（含自动匹配的分辨率与预计格点数）。

    复用现有的数据源 / 参数 / 时间校验，只额外校验经纬度范围与格点数上限。
    分辨率不来自请求：直接取数据源原生分辨率（与 ``pipeline`` 的强校验一致），
    因此这里返回的 ``lat_step`` / ``lon_step`` 只用于展示与格点数计算。

    注意：本函数**只做校验与估算**，不生成 lon/lat 数组、不写 site_file、
    不调用 gridgen / DEM / retrieval / adapter 的计算入口。
    """
    spec = require_source(getattr(request, "dataset", None))
    parameters = validate_parameters(getattr(request, "parameters", None) or [])
    start, end = parse_time_range(getattr(request, "start_time", None),
                                  getattr(request, "end_time", None))

    lon_min = _grid_number(getattr(request, "lon_min", None), "最小经度")
    lon_max = _grid_number(getattr(request, "lon_max", None), "最大经度")
    lat_min = _grid_number(getattr(request, "lat_min", None), "最小纬度")
    lat_max = _grid_number(getattr(request, "lat_max", None), "最大纬度")

    if not -180.0 <= lon_min <= 180.0:
        raise InvalidRequestError(f"最小经度超出范围（-180 ~ 180）：{lon_min}。")
    if not -180.0 <= lon_max <= 180.0:
        raise InvalidRequestError(f"最大经度超出范围（-180 ~ 180）：{lon_max}。")
    if not -90.0 <= lat_min <= 90.0:
        raise InvalidRequestError(f"最小纬度超出范围（-90 ~ 90）：{lat_min}。")
    if not -90.0 <= lat_max <= 90.0:
        raise InvalidRequestError(f"最大纬度超出范围（-90 ~ 90）：{lat_max}。")

    if lon_min >= lon_max:
        raise InvalidRequestError(
            f"最小经度必须小于最大经度：{lon_min} >= {lon_max}。"
        )
    if lat_min >= lat_max:
        raise InvalidRequestError(
            f"最小纬度必须小于最大纬度：{lat_min} >= {lat_max}。"
        )

    require_grid_alignment(spec, lon_min, lon_max, lat_min, lat_max)

    lat_step, lon_step = spec.spatial_resolution
    lat_step, lon_step = float(lat_step), float(lon_step)
    n_lon, n_lat, total = grid_point_counts(
        spec, lon_min, lon_max, lat_min, lat_max)

    if total > MAX_GRID_POINTS:
        raise InvalidRequestError(
            f"当前区域按 {spec.name} 原生 {lon_step:g}° × {lat_step:g}° 分辨率预计生成 "
            f"{total} 个格点，超过 Web V1.0 单任务最大 {MAX_GRID_POINTS} 个格点。"
        )

    return {
        "dataset": spec.key,
        "parameters": parameters,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "lon_min": lon_min,
        "lon_max": lon_max,
        "lat_min": lat_min,
        "lat_max": lat_max,
        "lat_step": lat_step,
        "lon_step": lon_step,
        "n_lon": n_lon,
        "n_lat": n_lat,
        "grid_points": total,
    }


def resolve_download_options(request: Any) -> dict[str, Any]:
    """按数据源分流校验下载参数，返回该数据源**真实**的下载配置。

    - ``era5``：只接受已验证的 ERA5 pressure-level 配置（37 层 / 4 变量 / NetCDF /
      不使用 grid）。
    - ``merra2``：校验时间必须是 00/06/12/18 UTC 整点、输出固定为官方 NetCDF-4
      （.nc4）、且当前机器已配置 NASA Earthdata 凭据；绝不映射到 ERA5，也不复用
      ERA5 的 CDS / 气压层 / 变量 / grid / 220MB 最小文件限制。

    任何不满足都会在创建任务前抛 ``InvalidRequestError``（HTTP 422）——绝不
    "用户要 A、后台静默下 B"。
    """
    key = str(getattr(request, "dataset", "") or "").strip().lower()
    if key == "merra2":
        return _resolve_merra2_options(request)
    if key and key != "era5":
        spec = adapter.source_spec(key)
        name = spec.name if spec is not None else key
        raise InvalidRequestError(f"{name} 暂不支持在线下载，请选择 ERA5 或 MERRA-2。")
    return _resolve_era5_options(request)


def _resolve_era5_options(request: Any) -> dict[str, Any]:
    """ERA5 专用：只接受已验证的 pressure-level 下载配置。"""
    options = adapter.download_options()

    fmt = str(getattr(request, "output_format", "") or "nc").strip().lower().lstrip(".")
    if fmt not in options["formats"]:
        raise InvalidRequestError("第一版仅支持已验证的 ERA5 NetCDF（nc）下载格式。")

    levels = getattr(request, "pressure_levels", None)
    if levels is not None:
        requested = {str(item).strip() for item in levels}
        if requested != set(options["pressure_levels"]):
            raise InvalidRequestError(
                "第一版仅支持已验证的 ERA5 37 个气压层配置（1–1000 hPa）。"
            )

    variables = getattr(request, "variables", None)
    if variables is not None:
        requested = {str(item).strip() for item in variables}
        if requested != set(options["variables"]):
            raise InvalidRequestError(
                "第一版仅支持已验证的 ERA5 4 变量配置"
                "（geopotential / relative_humidity / specific_humidity / temperature）。"
            )

    return {
        "dataset": options["dataset"],
        "pressure_levels": list(options["pressure_levels"]),
        "variables": list(options["variables"]),
        "output_format": fmt,
    }


def _resolve_merra2_options(request: Any) -> dict[str, Any]:
    """MERRA-2 专用：凭据 + 时次 + 格式校验，返回官方 nc4 下载配置。"""
    # 先确认当前机器真的能下载（代码有下载器 ≠ 当前机器有凭据）
    available, reason = adapter.download_available("merra2")
    if not available:
        raise InvalidRequestError(reason)

    levels = getattr(request, "pressure_levels", None)
    variables = getattr(request, "variables", None)
    if levels is not None or variables is not None:
        raise InvalidRequestError(
            "MERRA-2 下载不支持自定义气压层或变量，将下载官方完整 inst6_3d_ana_Np 产品。"
        )

    fmt = str(getattr(request, "output_format", "") or "nc4").strip().lower().lstrip(".")
    if fmt not in ("nc", "nc4"):
        raise InvalidRequestError("MERRA-2 仅支持官方 NetCDF（.nc4）下载格式。")

    start, end = parse_time_range(
        getattr(request, "start_time", None), getattr(request, "end_time", None)
    )
    try:
        adapter.validate_merra2_time(start)
        adapter.validate_merra2_time(end)
    except adapter.AdapterError as exc:
        raise InvalidRequestError(str(exc)) from exc

    options = adapter.merra2_download_options()
    return {
        "dataset": options["dataset"],
        "product": options["product"],
        "stream": options["stream"],
        "output_format": options["output_format"],
    }


__all__ = [
    "DEFAULT_OUTPUT_FORMAT",
    "MAX_GRID_POINTS",
    "SUPPORTED_OUTPUT_FORMATS",
    "grid_point_counts",
    "parse_iso_datetime",
    "parse_time_range",
    "require_source",
    "resolve_download_options",
    "validate_grid",
    "validate_output_format",
    "validate_parameters",
    "validate_sites",
]
