"""参数计算业务服务（阶段 3）。

调用链固定为::

    POST /api/calculation  →  calculation_service.submit_calculation  →  TaskService
    TaskRunner             →  calculation_service.execute_calculation →  engine.adapter
                           →  metcom.retrieval.pipeline.run_retrieval

输出参数（PWV/WVD/Tm/ZTD/ZHD/ZWD/Ps/vapor_pressure）由 Web 层裁剪，
结果文件格式（txt / dat / mat）也由 Web 层选择；两者都只作用于**引擎已经
写出的结果文件**，数值本身完全来自引擎输出，Web 侧不重算任何物理量。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from config import get_settings
from engine import adapter
from models.schemas import CalculationRequest, TaskResponse, TaskType
from services import results, validation
from services.errors import InvalidRequestError, TaskExecutionError
from services.task_service import get_task_service

logger = logging.getLogger("metcom.web.tasks")

ProgressCallback = Callable[[int, str], None]

# 自定义测站文件名（任务结束后保留在 results/<task_id>/ 下，作为结果的一部分）
CUSTOM_SITES_FILENAME = "custom_sites.txt"


def write_custom_sites_file(task_id: str, sites: list[dict[str, Any]]) -> Path:
    """把自定义测站写成该任务专属的站点文件，返回其路径。

    格式与平台默认站点文件一致：空白分隔的四列 ``name lat lon height``，
    定宽左/右对齐（``%4s %12.6f %12.6f %10.3f``），可被引擎
    ``metcom.utils.io.read_site_file`` 直接解析。绝不改动默认站点文件。
    """
    directory = get_settings().results_dir / task_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / CUSTOM_SITES_FILENAME
    lines = [
        "{:<4s} {:12.6f} {:12.6f} {:10.3f}".format(
            site["name"], site["lat"], site["lon"], site["height"]
        )
        for site in sites
    ]
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    return path


def submit_calculation(request: CalculationRequest) -> TaskResponse:
    """校验并创建 CALCULATION 任务（真正的反演在后台由 TaskRunner 执行）。

    ``request.sites`` 省略（None）时沿用平台默认站点文件，行为与历史请求一致；
    提供非空列表时先校验、再随任务参数落盘，由执行阶段生成任务专属站点文件。

    ``request.output_format`` 省略时使用平台默认格式（txt），任务记录里始终
    保存本次执行**真实**使用的格式。
    """
    spec = validation.require_source(request.dataset)
    parameters = validation.validate_parameters(request.parameters)
    start, end = validation.parse_time_range(request.start_time, request.end_time)
    sites = validation.validate_sites(request.sites)
    output_format = validation.validate_output_format(
        getattr(request, "output_format", None))

    params: dict[str, Any] = {
        "dataset": spec.key,
        "parameters": parameters,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "output_format": output_format,
    }
    if sites is not None:
        params["sites"] = sites

    return get_task_service().create_task(
        name=f"{spec.name} 参数计算",
        type=TaskType.CALCULATION,
        params=params,
    )


def execute_calculation(task: TaskResponse, progress: ProgressCallback) -> dict[str, Any]:
    """执行真实反演：adapter → metcom.retrieval.pipeline.run_retrieval。

    任务参数里带 ``sites`` 时，先写成该任务专属的站点文件再交给引擎；
    不带 ``sites`` 时传 None，adapter 使用平台默认站点文件（行为不变）。
    """
    settings = get_settings()
    params = dict(task.params or {})
    spec = validation.require_source(params.get("dataset"))
    parameters = validation.validate_parameters(params.get("parameters") or [])
    start, end = validation.parse_time_range(params.get("start_time"), params.get("end_time"))
    try:
        sites = validation.validate_sites(params.get("sites"))
    except InvalidRequestError as exc:
        raise TaskExecutionError(str(exc)) from exc
    try:
        output_format = validation.validate_output_format(params.get("output_format"))
    except InvalidRequestError as exc:
        raise TaskExecutionError(str(exc)) from exc

    # 结果目录由后台生成：results/<task_id>/data/年积日/ztd_<时次>_sit_<源>.txt
    output_dir = settings.results_dir / task.id / "data"

    site_file: Path | None = None
    if sites is not None:
        try:
            site_file = write_custom_sites_file(task.id, sites)
        except OSError as exc:
            logger.error("Write custom sites failed: %s — %s", task.id, exc)
            raise TaskExecutionError("自定义测站文件写入失败，无法执行计算。") from exc
        logger.info("Custom sites file: %s (%d sites)", site_file, len(sites))

    progress(15, "正在读取再分析资料")
    logger.info("Calculation started: %s (%s)", task.id, spec.key)

    try:
        outcome = adapter.run_calculation(
            source_key=spec.key,
            start_time=start,
            end_time=end,
            output_dir=output_dir,
            parameters=parameters,
            output_format=output_format,
            site_file=site_file,
        )
    except adapter.AdapterError as exc:
        logger.error("Calculation failed: %s — %s", task.id, exc)
        raise TaskExecutionError(str(exc)) from exc
    except Exception:                               # noqa: BLE001 - 后台任务必须收敛所有异常
        logger.exception("Calculation failed: %s", task.id)
        raise TaskExecutionError("参数计算失败，请查看任务详情。") from None

    progress(90, "正在整理结果数据")
    files = [Path(item) for item in outcome["files"]]
    stats = dict(outcome.get("stats") or {})

    summary = (
        f"{spec.name} 参数反演完成：{stats.get('hours', len(files))} 个时次 × "
        f"{stats.get('sites', 0)} 个站点，输出参数 " + "、".join(parameters) + "。"
    )
    extra: dict[str, Any] = {
        "dataset": spec.key,
        "parameters": parameters,
        "output_format": output_format,
        "columns": list(outcome.get("columns") or []),
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "engine_stats": stats,
    }
    if site_file is not None:
        # 任务专属站点文件保留在 results/<task_id>/ 下，并在结果元信息中登记
        extra["sites_file"] = results.relative_path(site_file)
        extra["sites_count"] = len(sites)

    result = results.build_result(
        result_type="station",
        path=output_dir,
        file_name=files[0].name if files else spec.name,
        summary=summary,
        files=files,
        extra=extra,
    )
    results.write_metadata(task.id, task.type.value, task.name, result)
    return result


__all__ = ["CUSTOM_SITES_FILENAME", "execute_calculation", "submit_calculation",
           "write_custom_sites_file"]
