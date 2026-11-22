"""数据下载业务服务（阶段 3）。

调用链固定为::

    POST /api/download  →  download_service.submit_download  →  TaskService（创建 DOWNLOAD 任务）
    TaskRunner          →  download_service.execute_download →  engine.adapter →  metcom.downloader

路由层只做 HTTP 转发，不直接调用引擎。按数据源分流：

    era5   → adapter.run_download        → metcom.downloader.runner（CDS API）
    merra2 → adapter.run_merra2_download → metcom.downloader.merra2（NASA GES DISC）

真实文件落在该任务独立的结果目录 ``data/tasks/results/<task_id>/download/`` 下
（下载器再按日期/年份建子目录），用户无法指定任何路径。下载结束后必须通过结果校验
（存在 / 非空 / 在任务目录内 / 扩展名符合该数据源 / NetCDF 可打开），否则任务进入
FAILED —— 绝不把"接口返回成功"当成下载成功。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config import get_settings
from engine import adapter
from models.schemas import DownloadRequest, TaskResponse, TaskType
from services import results, validation
from services.errors import InvalidRequestError, TaskExecutionError
from services.task_service import get_task_service

logger = logging.getLogger("metcom.web.tasks")

ProgressCallback = Callable[[int, str], None]

# 每种输出格式允许的扩展名（ERA5 命名规则：era5_global_qrzt_<时次>.<ext>）
_EXPECTED_SUFFIXES: dict[str, tuple[str, ...]] = {
    "nc": (".nc",),
    "nc4": (".nc4",),
    "grib": (".grib", ".grb", ".grib2"),
}

# 各数据源的展示名（校验失败信息用），避免把 MERRA-2 写成 ERA5
_SOURCE_LABELS: dict[str, str] = {"era5": "ERA5", "merra2": "MERRA-2"}


def _source_label(source_key: str) -> str:
    return _SOURCE_LABELS.get(source_key, str(source_key or "数据"))


def _expected_suffixes(source_key: str, output_format: str) -> tuple[str, ...]:
    """该数据源下载结果允许的扩展名：MERRA-2 官方为 .nc4，ERA5 按输出格式。"""
    if source_key == "merra2":
        return (".nc4",)
    return _EXPECTED_SUFFIXES.get(output_format, (".nc",))


def submit_download(request: DownloadRequest) -> TaskResponse:
    """校验并创建 DOWNLOAD 任务（真正的下载在后台由 TaskRunner 执行）。"""
    spec = validation.require_source(request.dataset)
    if spec.key not in adapter.DOWNLOADABLE_SOURCES:
        raise InvalidRequestError(f"{spec.name} 暂不支持在线下载，请选择 ERA5 或 MERRA-2。")
    start, end = validation.parse_time_range(request.start_time, request.end_time)
    options = validation.resolve_download_options(request)

    params: dict[str, Any] = {
        "dataset": spec.key,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
    }
    # 只写入该数据源真实需要的参数：ERA5 写气压层/变量，MERRA-2 写产品/流号，
    # 绝不把 ERA5 的层与变量混进 MERRA-2 任务。
    for key in ("pressure_levels", "variables", "output_format", "product", "stream"):
        if key in options:
            params[key] = options[key]

    return get_task_service().create_task(
        name=f"{spec.name} 数据下载",
        type=TaskType.DOWNLOAD,
        params=params,
    )


def execute_download(task: TaskResponse, progress: ProgressCallback) -> dict[str, Any]:
    """执行真实下载：按数据源分流到对应的 adapter 下载入口。"""
    settings = get_settings()
    params = dict(task.params or {})
    spec = validation.require_source(params.get("dataset"))
    start, end = validation.parse_time_range(params.get("start_time"), params.get("end_time"))
    output_format = str(params.get("output_format") or "nc").strip().lower().lstrip(".")

    # 输出目录固定在任务自己的结果目录下，不接受前端传入的任何路径
    output_dir = settings.results_dir / task.id / "download"

    progress(15, "正在准备下载")
    logger.info("Download started: %s (%s → %s)", task.id, spec.key, output_dir)

    try:
        if spec.key == "merra2":
            # MERRA-2 走自己的 GES DISC 下载器，绝不经过 CDS，也不复用 ERA5 逻辑
            outcome = adapter.run_merra2_download(
                start_time=start,
                end_time=end,
                output_dir=output_dir,
            )
        else:
            outcome = adapter.run_download(
                source_key=spec.key,
                start_time=start,
                end_time=end,
                output_dir=output_dir,
                output_format=output_format,
            )
    except adapter.AdapterError as exc:
        logger.error("Download failed: %s — %s", task.id, exc)
        raise TaskExecutionError(str(exc)) from exc
    except Exception:                               # noqa: BLE001 - 后台任务必须收敛所有异常
        logger.exception("Download failed: %s", task.id)
        raise TaskExecutionError("数据下载失败，请查看任务详情。") from None

    progress(85, "正在校验下载结果")
    files = [Path(item) for item in outcome["files"]]
    stats = dict(outcome.get("stats") or {})
    _verify_download(task.id, spec.key, output_dir, files, output_format, stats)

    progress(95, "正在整理结果")
    summary = (
        f"{spec.name} 数据下载完成：新增 {stats.get('success', len(files))} 个时次，"
        f"跳过 {stats.get('skipped', 0)} 个，失败 {stats.get('failed', 0)} 个。"
    )
    result = results.build_result(
        result_type="dataset",
        path=output_dir,
        file_name=f"{spec.name} 数据文件",
        summary=summary,
        files=files,
        extra={
            "dataset": spec.key,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "output_format": output_format,
            "status": "SUCCESS",
            "engine_stats": stats,
        },
    )
    results.write_metadata(task.id, task.type.value, task.name, result)
    return result


def _verify_download(
    task_id: str,
    source_key: str,
    output_dir: Path,
    files: list[Path],
    output_format: str,
    stats: dict[str, Any],
) -> int:
    """真实校验下载结果；任何一项不满足都抛 TaskExecutionError（任务 FAILED）。

    检查顺序：下载器自报失败数 → 文件非空 → 文件在任务目录内 → 存在 → 大小 > 0
    → 扩展名符合该数据源（ERA5 .nc / MERRA-2 .nc4）→ NetCDF 最小结构检查。
    只按数据源检查扩展名与可打开性，不要求特定变量名、特定大小或 220MB 限制。
    返回总字节数供日志使用。
    """
    label = _source_label(source_key)
    failed = int(stats.get("failed") or 0)
    if failed > 0:
        raise TaskExecutionError(
            f"{label} 下载未全部完成：{failed} 个时次失败，任务判定为失败。"
        )
    if not files:
        raise TaskExecutionError(f"{label} 下载失败：未获取到任何数据文件。")

    suffixes = _expected_suffixes(source_key, output_format)
    root = output_dir.resolve()
    total_bytes = 0
    for path in files:
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise TaskExecutionError("下载文件不在任务结果目录内，已判定为失败。")
        if not resolved.is_file():
            raise TaskExecutionError(f"下载文件缺失：{resolved.name}")
        size = resolved.stat().st_size
        if size <= 0:
            raise TaskExecutionError(f"下载文件为空：{resolved.name}")
        if not resolved.name.lower().endswith(suffixes):
            raise TaskExecutionError(f"下载文件格式不符合预期：{resolved.name}")
        if suffixes[0].startswith(".nc"):
            _assert_openable_netcdf(resolved)
        total_bytes += size

    logger.info(
        "Download verified: %s — %d 个文件，共 %.1f MB",
        task_id, len(files), total_bytes / 1048576,
    )
    return total_bytes


def _assert_openable_netcdf(path: Path) -> None:
    """最小结构检查：只打开元数据，不把整个数据文件读进内存。"""
    try:
        import xarray as xr
    except Exception:                               # noqa: BLE001 - 退回 netCDF4
        xr = None

    try:
        if xr is not None:
            with xr.open_dataset(path) as dataset:
                if not dataset.data_vars:
                    raise TaskExecutionError(f"NetCDF 文件没有数据变量：{path.name}")
        else:
            import netCDF4

            with netCDF4.Dataset(path) as dataset:
                if not dataset.variables:
                    raise TaskExecutionError(f"NetCDF 文件没有数据变量：{path.name}")
    except TaskExecutionError:
        raise
    except Exception as exc:                        # noqa: BLE001 - 打不开即格式异常
        raise TaskExecutionError(
            f"NetCDF 文件无法打开（格式异常或已损坏）：{path.name}"
        ) from exc


__all__ = ["execute_download", "submit_download"]
