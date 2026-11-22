"""定制产品业务服务（阶段 5，V1.0 只实现 GRID 模式）。

调用链与在线计算保持一致，只把"站点文件"换成"格网生成的站点文件"、把任务类型
换成 CUSTOM::

    POST /api/custom  →  custom_service.submit_custom  →  TaskService（CUSTOM 任务）
    TaskRunner        →  custom_service.execute_custom →  engine.adapter
                      →  metcom.retrieval.pipeline.run_retrieval

一个任务的全部落盘都限定在该任务自己的目录 ``data/tasks/results/<task_id>/`` 内::

    custom_grid_orthometric.txt               反演使用的正高格点文件
    custom_grid_ellipsoidal.txt               椭球高参考文件
    data/<年积日>/ztd_<时次>_sit_<源>.txt      真实反演结果

路由层不接触引擎；科学计算仍完全由现有 metcom 引擎提供，本模块只负责参数转换、
调用 adapter 与结果校验，绝不复制计算逻辑。

阶段划分：创建任务前该拒绝的（mode / 数据源 / 参数 / 时间 / 经纬度范围 / 格点数上限 /
DEM 覆盖范围）一律 422 且不创建任务；运行期才暴露的问题（DEM 缺失或损坏、反演失败、
输出缺失或为空）→ 任务 FAILED。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

import numpy as np

from config import get_settings
from engine import adapter
from metcom.utils.io import read_result_file, read_site_file
from models.schemas import (CustomMode, CustomRequest, GridRequest, TaskResponse,
                            TaskType)
from services import elevation, grid_service, results, validation
from services.errors import InvalidRequestError, TaskExecutionError
from services.task_service import get_task_service

logger = logging.getLogger("metcom.web.tasks")

ProgressCallback = Callable[[int, str], None]

# 任务专属格点站点文件名（任务结束后保留在结果目录，作为结果的一部分）
CUSTOM_GRID_ORTHOMETRIC_FILENAME = "custom_grid_orthometric.txt"
CUSTOM_GRID_ELLIPSOIDAL_FILENAME = "custom_grid_ellipsoidal.txt"
# 兼容旧测试/调用方对反演站点文件常量的引用；语义改为正高文件。
CUSTOM_GRID_SITES_FILENAME = CUSTOM_GRID_ORTHOMETRIC_FILENAME
# 反演结果子目录（与在线计算结果布局一致：results/<task_id>/data/<年积日>/）
DATA_SUBDIR = "data"

# task.params 中记录的 mode 取值（对外统一大写，便于阅读与排查）
MODE_GRID = "GRID"
STATION_NOT_IMPLEMENTED = "Custom station mode is not implemented yet"


def _grid_request_from_params(params: dict[str, Any]) -> GridRequest:
    """task.params → GridRequest；执行阶段重新走一遍同一套参数契约。"""
    payload = {key: value for key, value in params.items() if key != "mode"}
    try:
        return GridRequest(**payload)
    except Exception as exc:                        # noqa: BLE001 - 面向用户收敛
        raise TaskExecutionError("任务参数不完整，无法执行格网计算。") from exc


def _require_dem_coverage(validated: dict[str, Any]) -> None:
    """创建任务前确认区域完全落在当前 DEM 覆盖范围内。

    复用执行阶段完全相同的入口（``grid_service.generate_grid_coordinates`` +
    ``elevation.sample_nearest``）做覆盖范围判定，只校验、不使用采样值，
    因此不会出现"提交时通过、执行时越界"的口径差异。超出范围直接 422，
    既不自动裁剪请求区域，也不静默丢弃格点。

    DEM 本体缺失或损坏属于**运行期**失败（任务创建成功 → FAILED），
    因此这里只跳过预检并记日志，不提前拒绝任务。
    """
    lons, lats = grid_service.generate_grid_coordinates(
        dataset=validated["dataset"],
        lon_min=validated["lon_min"],
        lon_max=validated["lon_max"],
        lat_min=validated["lat_min"],
        lat_max=validated["lat_max"],
    )
    # sample_nearest 接受的是"成对"的格点，不是两个坐标轴，因此按
    # build_grid_site_file 相同的方式展平（lat 外层、lon 内层）。
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    try:
        dem = elevation.load_dem(grid_service.DEFAULT_DEM_PATH)
    except InvalidRequestError as exc:
        logger.warning("Skip DEM coverage pre-check: %s", exc)
        return

    try:
        elevation.sample_nearest(dem, lon_grid.ravel(), lat_grid.ravel())
    except InvalidRequestError as exc:
        lon_low, lon_high = dem.lon_range
        lat_low, lat_high = dem.lat_range
        raise InvalidRequestError(
            f"{exc}。当前 Grid DEM 仅覆盖 {lon_low:g}~{lon_high:g}°E / "
            f"{lat_low:g}~{lat_high:g}°N，请调整区域后重新提交。"
        ) from exc


def submit_custom(request: CustomRequest) -> TaskResponse:
    """校验并创建 CUSTOM（GRID）任务；STATION 未实现，创建前直接 422。"""
    if request.mode != CustomMode.GRID:
        raise InvalidRequestError(STATION_NOT_IMPLEMENTED)

    validated = validation.validate_grid(request)          # 复用阶段 1 的全部校验
    _require_dem_coverage(validated)
    spec = validation.require_source(validated["dataset"])

    params: dict[str, Any] = {
        "mode": MODE_GRID,
        "dataset": validated["dataset"],
        "parameters": validated["parameters"],
        "start_time": validated["start_time"],
        "end_time": validated["end_time"],
        "lon_min": validated["lon_min"],
        "lon_max": validated["lon_max"],
        "lat_min": validated["lat_min"],
        "lat_max": validated["lat_max"],
    }
    return get_task_service().create_task(
        name=f"{spec.name} 格网定制计算",
        type=TaskType.CUSTOM,
        params=params,
    )


def _expected_site_names(site_file: Path, expected_sites: int) -> list[str]:
    """Read the generated grid site names used to validate result identity/order."""
    try:
        sites = read_site_file(site_file)
    except (OSError, ValueError) as exc:
        raise TaskExecutionError("无法读取本次任务的格点站点文件，结果不可验证。") from exc
    if len(sites) != expected_sites:
        raise TaskExecutionError(
            f"格点站点文件包含 {len(sites)} 条记录，与预期 {expected_sites} 个格点不一致。"
        )
    return [site.name for site in sites]


def _validate_result_file(
    path: Path,
    *,
    expected_names: list[str],
    required_columns: list[str],
) -> None:
    """Validate one text result file independently of engine summary statistics."""
    if path.suffix.lower() not in {".txt", ".dat"}:
        raise TaskExecutionError(
            f"结果文件格式无法完整验证：{path.name}（当前仅支持 TXT/DAT 结果）。"
        )

    try:
        raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise TaskExecutionError(f"结果文件读取失败：{path.name}。") from exc
    if not raw_lines or not raw_lines[0].split():
        raise TaskExecutionError(f"结果文件缺少有效表头：{path.name}。")

    raw_header = raw_lines[0].split()
    raw_rows = [line.split() for line in raw_lines[1:] if line.strip()]
    expected_count = len(expected_names)
    if len(raw_rows) != expected_count:
        raise TaskExecutionError(
            f"结果文件 {path.name} 的有效数据记录数为 {len(raw_rows)}，"
            f"与预期格点数 {expected_count} 不一致。"
        )
    if any(len(cells) != len(raw_header) for cells in raw_rows):
        raise TaskExecutionError(f"结果文件数据列数不完整：{path.name}。")

    actual_names = [cells[0] for cells in raw_rows]
    if actual_names != expected_names:
        raise TaskExecutionError(
            f"结果文件 {path.name} 的站点标识或顺序与格点站点文件不一致。"
        )

    try:
        columns, data = read_result_file(path)
    except (OSError, TypeError, ValueError) as exc:
        raise TaskExecutionError(f"结果文件格式无法解析：{path.name}。") from exc
    if columns != raw_header or not columns:
        raise TaskExecutionError(f"结果文件表头格式异常：{path.name}。")
    if columns[0] != "name":
        raise TaskExecutionError(f"结果文件缺少 name 首列：{path.name}。")
    if len(columns) != len(set(columns)):
        raise TaskExecutionError(f"结果文件表头包含重复列：{path.name}。")

    missing = [column for column in required_columns if column not in columns]
    if missing:
        raise TaskExecutionError(
            f"结果文件 {path.name} 缺少必需列：" + "、".join(missing) + "。"
        )
    if data.ndim != 2 or data.shape[0] != expected_count:
        raise TaskExecutionError(f"结果文件有效数据记录数不完整：{path.name}。")

    for column in required_columns:
        if column == "name":
            continue
        index = columns.index(column)
        if index >= data.shape[1] or not np.all(np.isfinite(data[:, index])):
            raise TaskExecutionError(
                f"结果文件 {path.name} 的 {column} 列包含无法解析或非有限数值。"
            )


def _validate_outcome(outcome: dict[str, Any], *, task_dir: Path,
                      site_file: Path, expected_sites: int,
                      parameters: list[str]) -> tuple[dict[str, Any], list[Path]]:
    """结果校验：任何一项不满足都让任务 FAILED，绝不出现"SUCCESS 但没有结果"。"""
    stats = dict(outcome.get("stats") or {})
    files = [Path(item) for item in (outcome.get("files") or [])]
    hours = int(stats.get("hours") or 0)
    sites = int(stats.get("sites") or 0)

    if hours <= 0:
        raise TaskExecutionError("所选时间范围内未读取到任何时次的数据，未生成结果。")
    if sites <= 0:
        raise TaskExecutionError("引擎未反演任何格点，未生成结果。")
    if sites != expected_sites:
        raise TaskExecutionError(
            f"引擎反演格点数 {sites} 与格点文件 {expected_sites} 不一致，结果不可信。"
        )
    if not files:
        raise TaskExecutionError("计算完成但未生成任何结果文件。")

    task_root = Path(task_dir).resolve()
    if not Path(site_file).resolve().is_relative_to(task_root):
        raise TaskExecutionError("格点站点文件不在当前任务目录内。")

    expected_names = _expected_site_names(site_file, expected_sites)
    required_columns = list(adapter.BASE_COLUMNS) + [
        adapter.PARAM_COLUMNS[key] for key in parameters
    ]

    for path in files:
        if not path.is_file():
            raise TaskExecutionError(f"结果文件缺失：{path.name}。")
        if path.stat().st_size <= 0:
            raise TaskExecutionError(f"结果文件为空：{path.name}。")
        if not path.resolve().is_relative_to(task_root):
            raise TaskExecutionError("结果文件越出当前任务目录。")
        _validate_result_file(
            path,
            expected_names=expected_names,
            required_columns=required_columns,
        )

    return stats, files


def execute_custom(task: TaskResponse, progress: ProgressCallback) -> dict[str, Any]:
    """执行 CUSTOM（GRID）任务：生成格点站点文件 → adapter → 真实反演。"""
    params = dict(task.params or {})
    if str(params.get("mode") or "").strip().upper() != MODE_GRID:
        raise TaskExecutionError(STATION_NOT_IMPLEMENTED)

    request = _grid_request_from_params(params)
    try:
        validated = validation.validate_grid(request)
    except InvalidRequestError as exc:
        raise TaskExecutionError(str(exc)) from exc
    spec = validation.require_source(validated["dataset"])

    settings = get_settings()
    task_dir = settings.results_dir / task.id
    orthometric_file = task_dir / CUSTOM_GRID_ORTHOMETRIC_FILENAME
    ellipsoidal_file = task_dir / CUSTOM_GRID_ELLIPSOIDAL_FILENAME
    site_file = orthometric_file
    output_dir = task_dir / DATA_SUBDIR

    progress(15, "正在生成格网高程与站点文件")
    try:
        grid_meta = grid_service.build_grid_height_files(
            dataset=validated["dataset"],
            lon_min=validated["lon_min"],
            lon_max=validated["lon_max"],
            lat_min=validated["lat_min"],
            lat_max=validated["lat_max"],
            orthometric_path=orthometric_file,
            ellipsoidal_path=ellipsoidal_file,
        )
    except InvalidRequestError as exc:
        raise TaskExecutionError(str(exc)) from exc
    except OSError as exc:
        raise TaskExecutionError("格网站点文件写入失败，无法执行计算。") from exc

    progress(30, "正在读取再分析资料")
    logger.info("Custom grid calculation started: %s (%s)", task.id, spec.key)
    try:
        outcome = adapter.run_calculation(
            source_key=spec.key,
            start_time=request.start_time,
            end_time=request.end_time,
            output_dir=output_dir,
            parameters=validated["parameters"],
            site_file=site_file,
        )
    except adapter.AdapterError as exc:
        logger.error("Custom grid calculation failed: %s — %s", task.id, exc)
        raise TaskExecutionError(str(exc)) from exc
    except TaskExecutionError:
        raise
    except Exception:                               # noqa: BLE001 - 后台任务必须收敛所有异常
        logger.exception("Custom grid calculation failed: %s", task.id)
        raise TaskExecutionError("格网参数计算失败，请查看任务详情。") from None

    progress(90, "正在校验并整理结果")
    stats, files = _validate_outcome(
        outcome,
        task_dir=task_dir,
        site_file=site_file,
        expected_sites=int(grid_meta["grid"]["points"]),
        parameters=list(validated["parameters"]),
    )

    summary = (
        f"{spec.name} 格网定制计算完成：{stats.get('hours', 0)} 个时次 × "
        f"{stats.get('sites', 0)} 个格点，输出参数 " + "、".join(validated["parameters"]) + "。"
    )
    extra: dict[str, Any] = {
        "mode": MODE_GRID,
        "dataset": spec.key,
        "grid": {
            "n_lon": int(grid_meta["grid"]["n_lon"]),
            "n_lat": int(grid_meta["grid"]["n_lat"]),
            "points": int(grid_meta["grid"]["points"]),
        },
        "bounds": dict(grid_meta["bounds"]),
        "requested_bounds": dict(grid_meta["requested_bounds"]),
        "effective_bounds": dict(grid_meta["effective_bounds"]),
        "resolution": dict(grid_meta["resolution"]),
        "parameters": list(validated["parameters"]),
        "outputs": [results.relative_path(path) for path in files],
        "site_count": int(stats.get("sites", 0)),
        "sites_file": results.relative_path(site_file),
        "height_files": {
            "orthometric": results.relative_path(orthometric_file),
            "ellipsoidal": results.relative_path(ellipsoidal_file),
            "points": int(grid_meta["grid"]["points"]),
            "height_datum": dict(grid_meta["height_datum"]),
        },
        "engine_stats": stats,
    }
    result = results.build_result(
        result_type="custom_grid",
        path=output_dir,
        file_name=files[0].name,
        summary=summary,
        files=files,
        extra=extra,
    )
    results.write_metadata(task.id, task.type.value, task.name, result)
    logger.info("Custom grid calculation completed: %s (%d 格点)", task.id, extra["site_count"])
    return result


__all__ = [
    "CUSTOM_GRID_ELLIPSOIDAL_FILENAME",
    "CUSTOM_GRID_ORTHOMETRIC_FILENAME",
    "CUSTOM_GRID_SITES_FILENAME",
    "DATA_SUBDIR",
    "MODE_GRID",
    "STATION_NOT_IMPLEMENTED",
    "execute_custom",
    "submit_custom",
]
