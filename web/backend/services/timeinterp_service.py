"""时间插值业务服务（在线计算 · 时间插值）。

调用链与在线计算完全同构，只把"再分析原始资料"换成"平台自己已完成的反演结果"::

    POST /api/timeinterp  →  timeinterp_service.submit_timeinterp  →  TaskService
    TaskRunner            →  timeinterp_service.execute_timeinterp
                          →  metcom.timeinterp.runner.run_timeinterp

科学计算 100% 复用现有 ``metcom.timeinterp``；本模块只做参数转换、目录落位与
结果校验，绝不复制或重写插值算法。

一个任务的全部落盘都限定在该任务自己的目录内，**绝不写回源任务目录**::

    data/tasks/results/<task_id>/input/<年积日>/ztd_<时次>_sit_<源>.<ext>   输入副本
    data/tasks/results/<task_id>/data/<年积日>/ztd_<时次>_sit_<源>.<ext>    插值结果
    data/tasks/results/<task_id>/metadata.json                             结果元信息

为什么要先复制输入：原生 ``interpolate_one_day`` 跨天时会把次日 00 时文件
**临时复制进当天文件夹、处理完再删除**（见 ``metcom/timeinterp/runner.py``）。
直接对源任务目录运行，等于让"插值"顺带改动另一个任务的结果目录，因此这里
一律先把输入复制到本任务的 ``input/`` 下再交给引擎——科学算法一行不改，
差异只体现在目录边界上。

阶段划分：创建任务前该拒绝的（任务不存在 / 未成功 / 非参数计算 / 结果不可插值 /
参数缺列 / 时间间隔不支持）一律 422 且不创建任务；运行期才暴露的问题
（引擎不可用、插值报错、输出缺失或为空）→ 任务 FAILED。

原生算法的两条硬约束（源码事实，Web 侧只做翻译、不做绕行）：

1. 输出列宽写死：``_TIMEINTERP_SPECS`` 为 4 列站点信息 + 11 列数值，表头由
   ``base_headers[:4] + variables`` 拼出，因此插值变量必须**恰好 8 个**
   （见 ``required_parameters()``）。所以本阶段不提供"只插值部分参数"的选项。
2. 目标时刻由 ``target_interval`` 与当天实际存在的时次共同决定，没有"任意时刻"
   输入；时间范围之外需要外推时，由原生 not-a-knot 样条按既有规则处理。
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Sequence

from config import get_settings
from engine import adapter
from models.schemas import (TaskResponse, TaskStatus, TaskType, TimeInterpInputInfo,
                            TimeInterpInputListResponse, TimeInterpRequest)
from services import results
from services.errors import InvalidRequestError, TaskExecutionError
from services.task_service import TaskNotFoundError, get_task_service

logger = logging.getLogger("metcom.web.tasks")

ProgressCallback = Callable[[int, str], None]

# 原生文件名契约：ztd_YYYYMMDDHH_sit_<源名>.<扩展名>（metcom/timeinterp/runner.py）
_FILENAME_RE = re.compile(r"^ztd_(\d{8})(\d{2})_sit_([^.]+)\.([^.]+)$")
# 年积日文件夹名：YYYYDDD
_DOY_RE = re.compile(r"^\d{7}$")

# 原生 Reader 只认定宽文本；MAT 结构体不在其读取范围内
SUPPORTED_INPUT_FORMATS: tuple[str, ...] = ("txt", "dat")

# 目标时间间隔（小时）：只开放能整除 24 的取值。
# 原生的目标时刻网格是 ``np.arange(0, 24 + interval, interval)``，非 24 的因子
# 会生成 25 时这类越界时刻（例如 5 小时 → 0/5/10/15/20/25），因此这里先把
# 取值范围收到整除 24 的集合内，而不是把问题留给运行期。
TARGET_INTERVALS: tuple[int, ...] = (1, 2, 3, 4, 6, 8, 12)
DEFAULT_TARGET_INTERVAL = 1

# 输入副本 / 结果子目录（结果子目录与在线计算保持一致，便于前端复用预览）
INPUT_SUBDIR = "input"
DATA_SUBDIR = "data"

# Web 参数键 → 原生结果列名（与 engine.adapter.PARAM_COLUMNS 同源）
_COLUMN_TO_PARAM = {column: key for key, column in adapter.PARAM_COLUMNS.items()}


@lru_cache(maxsize=1)
def required_parameters() -> tuple[str, ...]:
    """原生时间插值固定会插值的 8 个参数（顺序 = 结果文件列顺序）。

    唯一事实来源是引擎的 ``metcom.timeinterp.runner.DEFAULT_VARIABLES``：
    这里只做"列名 → Web 参数键"的翻译，不另造一份清单。原生输出表头固定
    12 列，只有恰好 8 个插值变量时列宽才对得上，因此这个清单不是可选项。
    """
    from metcom.timeinterp.runner import DEFAULT_VARIABLES

    unmapped = [column for column in DEFAULT_VARIABLES if column not in _COLUMN_TO_PARAM]
    if unmapped:
        raise InvalidRequestError(
            "原生插值变量与平台参数映射不一致：" + "、".join(unmapped) + "。")
    return tuple(_COLUMN_TO_PARAM[column] for column in DEFAULT_VARIABLES)


def required_columns() -> tuple[str, ...]:
    """原生插值变量名（引擎列名），与 ``required_parameters()`` 一一对应。"""
    return tuple(adapter.PARAM_COLUMNS[key] for key in required_parameters())


def _source_name(key: str) -> str:
    """数据源对外显示名；取不到时原样返回 key（不编造名字）。"""
    spec = adapter.source_spec(key)
    return getattr(spec, "name", "") or str(key or "")


# --------------------------------------------------------------------------- #
# 输入任务扫描（只读，不做任何计算）
# --------------------------------------------------------------------------- #
def _read_header_columns(path: Path) -> list[str]:
    """读取结果文件首行表头（原生 ``_read_gnss_file`` 用同一套空白切分）。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            line = handle.readline()
    except OSError:
        return []
    return line.split() if line else []


def _result_files(task: TaskResponse) -> list[Path]:
    """从任务结果元信息里解析出**数据目录范围内**的结果文件。

    只认任务记录里登记的相对路径（``services.results`` 统一负责越界判定），
    不接受任何前端传入的路径。
    """
    result = dict(task.result or {})
    candidates: list[Path] = []
    listed = result.get("files")
    if isinstance(listed, list) and listed:
        for item in listed:
            if not isinstance(item, str) or not item:
                continue
            try:
                path = results.resolve_path(item)
            except ValueError:
                logger.warning("Skip out-of-scope result file: %s", item)
                continue
            if path.is_file():
                candidates.append(path)
        return candidates

    base = result.get("file_path")
    if isinstance(base, str) and base:
        try:
            directory = results.resolve_path(base)
        except ValueError:
            return []
        if directory.is_dir():
            candidates.extend(sorted(p for p in directory.rglob("*") if p.is_file()))
    return candidates


def _scan_input(task: TaskResponse) -> dict[str, Any]:
    """把结果文件整理成"年积日 → 时次 → 文件"，并汇总真实存在的元信息。"""
    folders: dict[str, dict[int, Path]] = {}
    reference: Path | None = None
    skipped: list[str] = []

    for path in _result_files(task):
        match = _FILENAME_RE.match(path.name)
        doy = path.parent.name
        if match is None or not _DOY_RE.match(doy):
            continue
        _, hour_text, _, extension = match.groups()
        if extension.lower() not in SUPPORTED_INPUT_FORMATS:
            skipped.append(path.name)
            continue
        folders.setdefault(doy, {})[int(hour_text)] = path
        if reference is None or path.name < reference.name:
            reference = path

    out: dict[str, Any] = {
        "folders": folders,
        "files": [p for hours in folders.values() for p in hours.values()],
        "reference": reference,
        "ext": reference.suffix.lstrip(".").lower() if reference else "",
        "columns": _read_header_columns(reference) if reference else [],
        "unsupported": sorted(set(skipped)),
    }
    out["parameters"] = [key for key, column in adapter.PARAM_COLUMNS.items()
                         if column in out["columns"]]
    hours_per_day = [len(hours) for hours in folders.values()]
    out["hour_count"] = sum(hours_per_day)
    out["min_hours_per_day"] = min(hours_per_day) if hours_per_day else 0
    out["max_hours_per_day"] = max(hours_per_day) if hours_per_day else 0
    out["time_labels"] = _time_labels(folders)
    return out


def _time_labels(folders: dict[str, dict[int, Path]]) -> list[str]:
    """按真实文件名还原每个时次的日历时间（升序、去重）。"""
    labels: set[str] = set()
    for hours in folders.values():
        for path in hours.values():
            match = _FILENAME_RE.match(path.name)
            if match is None:
                continue
            date_part, hour_text = match.group(1), match.group(2)
            try:
                moment = datetime.strptime(f"{date_part}{int(hour_text):02d}", "%Y%m%d%H")
            except ValueError:
                continue
            labels.add(moment.isoformat())
    return sorted(labels)


def _missing_columns(scan: dict[str, Any]) -> list[str]:
    """源结果里缺失的插值参数（Web 参数键），顺序与原生变量顺序一致。"""
    present = set(scan["columns"])
    return [key for key, column in zip(required_parameters(), required_columns())
            if column not in present]


def _input_hint(scan: dict[str, Any]) -> str:
    """给用户一句能看懂的可插值性结论。"""
    if not scan["folders"]:
        if scan["unsupported"]:
            formats = sorted({Path(name).suffix.lstrip(".").upper()
                              for name in scan["unsupported"]})
            return ("源结果文件为 " + "、".join(formats)
                    + " 格式，时间插值当前只支持定宽文本（TXT / DAT）结果。")
        return "源结果中没有可识别的时次文件，无法插值。"
    if scan["max_hours_per_day"] < 2:
        return "源结果每天只有一个时次，原生插值算法至少需要 2 个时次，无法插值。"
    missing = _missing_columns(scan)
    if missing:
        return ("源结果未包含 " + "、".join(missing)
                + " 参数；原生时间插值固定输出 8 个参数列，缺列会变成整列 NaN，"
                "因此本平台拒绝这种输入，请改用包含全部 8 个参数的反演结果。")
    return ""


def describe_input(task: TaskResponse) -> TimeInterpInputInfo:
    """把一个已完成任务描述成插值输入候选（只读扫描，不做计算）。"""
    scan = _scan_input(task)
    reason = _input_hint(scan)
    dataset = str((task.result or {}).get("dataset")
                  or (task.params or {}).get("dataset") or "")
    return TimeInterpInputInfo(
        task_id=task.id,
        name=task.name,
        dataset=dataset,
        dataset_name=_source_name(dataset),
        created_at=task.created_at,
        result_format=scan["ext"],
        interpolatable=not reason,
        reason=reason,
        parameters=list(required_parameters()),
        missing_columns=_missing_columns(scan),
        columns=list(scan["columns"]),
        doy_folders=sorted(scan["folders"]),
        time_labels=scan["time_labels"],
        site_count=_site_count(scan),
        hour_count=scan["hour_count"],
        min_hours_per_day=scan["min_hours_per_day"],
        max_hours_per_day=scan["max_hours_per_day"],
    )


def _site_count(scan: dict[str, Any]) -> int:
    """源结果的站点数：取第一个文件的数据行数（只统计，不解析数值）。"""
    reference = scan.get("reference")
    if reference is None:
        return 0
    try:
        with open(reference, "r", encoding="utf-8", errors="replace") as handle:
            handle.readline()
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def list_inputs() -> TimeInterpInputListResponse:
    """列出可作为插值输入的已完成参数计算任务（最新在前）。"""
    service = get_task_service()
    tasks, _ = service.list_tasks(page=1, page_size=1000, type=TaskType.CALCULATION)
    items = [describe_input(task) for task in tasks if task.status == TaskStatus.SUCCESS]
    items.sort(key=lambda item: item.created_at, reverse=True)
    return TimeInterpInputListResponse(
        items=items,
        total=len(items),
        target_intervals=list(TARGET_INTERVALS),
        default_target_interval=DEFAULT_TARGET_INTERVAL,
    )


# --------------------------------------------------------------------------- #
# 请求校验（创建任务前，全部为 422）
# --------------------------------------------------------------------------- #
def validate_request(payload: TimeInterpRequest) -> dict[str, Any]:
    """校验插值请求，返回落盘用的规范化参数。

    创建任务与执行任务共用本函数，保证"提交时能过 = 执行时不因参数被拒"。
    """
    task_id = str(getattr(payload, "source_task_id", "") or "").strip()
    if not task_id:
        raise InvalidRequestError("请选择用于插值的已完成参数计算任务。")

    try:
        task = get_task_service().get_task(task_id)
    except TaskNotFoundError as exc:
        raise InvalidRequestError("未找到指定的输入任务，请重新选择。") from exc

    if task.type != TaskType.CALCULATION:
        raise InvalidRequestError("只有参数计算任务的结果可以用于时间插值。")
    if task.status != TaskStatus.SUCCESS:
        raise InvalidRequestError("该任务尚未成功完成，其结果不能用于时间插值。")

    scan = _scan_input(task)
    reason = _input_hint(scan)
    if reason:
        raise InvalidRequestError(reason)

    parameters = _resolve_parameters(getattr(payload, "parameters", None))
    interval = _resolve_interval(getattr(payload, "target_interval", None))
    labels = scan["time_labels"]
    dataset = str((task.result or {}).get("dataset")
                  or (task.params or {}).get("dataset") or "")

    return {
        "task_id": task.id,
        "name": task.name,
        "dataset": dataset,
        "dataset_name": _source_name(dataset),
        "parameters": parameters,
        "variables": [adapter.PARAM_COLUMNS[key] for key in parameters],
        "target_interval": interval,
        "input_format": scan["ext"],
        "input_hours": scan["hour_count"],
        "input_folders": sorted(scan["folders"]),
        "input_time_range": [labels[0], labels[-1]] if labels else [],
        "sites_count": _site_count(scan),
        "folders": scan["folders"],
    }


def _resolve_parameters(requested: Sequence[Any] | None) -> list[str]:
    """参数选择：原生固定 8 个参数，因此这里只校验"不能删列"。

    省略（None）→ 使用原生固定清单；显式传入 → 必须是同一集合，
    否则明确报错。返回顺序始终按原生变量顺序，保证结果列顺序稳定。
    """
    required = list(required_parameters())
    if requested is None:
        return required

    selected: list[str] = []
    for item in requested or []:
        key = item.value if hasattr(item, "value") else str(item)
        key = key.strip()
        if key not in adapter.PARAM_COLUMNS:
            raise InvalidRequestError(f"不支持的输出参数：{key}。")
        if key not in selected:
            selected.append(key)
    if not selected:
        raise InvalidRequestError("请选择需要插值的参数。")
    if set(selected) != set(required):
        raise InvalidRequestError(
            "原生时间插值的输出表头固定为 4 列站点信息 + 8 个参数，只插值部分参数会"
            "导致列宽不匹配，因此必须包含全部 8 个参数：" + "、".join(required) + "。"
        )
    return required


def _resolve_interval(value: Any) -> int:
    """目标时间间隔：省略取默认值，只接受能整除 24 的整点小时数。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_TARGET_INTERVAL
    if isinstance(value, bool):
        raise InvalidRequestError("目标时间间隔必须是小时数（整数）。")
    try:
        interval = int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidRequestError("目标时间间隔必须是小时数（整数）。") from exc
    if interval != float(value):
        raise InvalidRequestError("目标时间间隔必须是整点小时数。")
    if interval not in TARGET_INTERVALS:
        raise InvalidRequestError(
            "不支持的目标时间间隔：" + str(value) + " 小时；可选 "
            + " / ".join(str(item) for item in TARGET_INTERVALS) + " 小时。"
        )
    return interval


# --------------------------------------------------------------------------- #
# 提交 / 执行
# --------------------------------------------------------------------------- #
def submit_timeinterp(payload: TimeInterpRequest) -> TaskResponse:
    """校验并创建 TIMEINTERP 任务（真正的插值在后台由 TaskRunner 执行）。"""
    validated = validate_request(payload)
    params: dict[str, Any] = {
        "source_task_id": validated["task_id"],
        "dataset": validated["dataset"],
        "parameters": validated["parameters"],
        "variables": validated["variables"],
        "target_interval": validated["target_interval"],
        "input_format": validated["input_format"],
        "input_hours": validated["input_hours"],
        "input_doy_folders": validated["input_folders"],
        "input_time_range": validated["input_time_range"],
        "sites_count": validated["sites_count"],
    }
    return get_task_service().create_task(
        name=f"{validated['dataset_name']} 时间插值",
        type=TaskType.TIMEINTERP,
        params=params,
    )


def _stage_inputs(folders: dict[str, dict[int, Path]], input_dir: Path) -> list[Path]:
    """把源结果复制到本任务的 ``input/`` 下，返回副本路径列表。

    复制而不是就地运行，是为了让原生跨天逻辑（临时文件创建 / 删除）只发生在
    本任务的目录里，源任务目录保持字节级不变。
    """
    staged: list[Path] = []
    for doy, hours in folders.items():
        target_dir = input_dir / doy
        target_dir.mkdir(parents=True, exist_ok=True)
        for path in hours.values():
            target = target_dir / path.name
            shutil.copy2(path, target)
            staged.append(target)
    return staged


def execute_timeinterp(task: TaskResponse, progress: ProgressCallback) -> dict[str, Any]:
    """执行真实时间插值：metcom.timeinterp.runner.run_timeinterp。"""
    settings = get_settings()
    params = dict(task.params or {})
    try:
        payload = TimeInterpRequest(
            source_task_id=params.get("source_task_id") or "",
            parameters=params.get("parameters") or None,
            target_interval=params.get("target_interval"),
        )
        validated = validate_request(payload)
    except InvalidRequestError as exc:
        raise TaskExecutionError(str(exc)) from exc
    except Exception as exc:                        # noqa: BLE001 - 面向用户收敛
        raise TaskExecutionError("任务参数不完整，无法执行时间插值。") from exc

    task_dir = settings.results_dir / task.id
    input_dir = task_dir / INPUT_SUBDIR
    output_dir = task_dir / DATA_SUBDIR

    progress(10, "正在准备插值输入")
    try:
        staged = _stage_inputs(validated["folders"], input_dir)
    except OSError as exc:
        logger.error("Stage timeinterp input failed: %s — %s", task.id, exc)
        raise TaskExecutionError("插值输入文件准备失败，无法执行时间插值。") from exc
    logger.info("Time interp input staged: %s (%d files)", task.id, len(staged))

    try:
        from metcom.timeinterp.runner import run_timeinterp
    except Exception as exc:                        # noqa: BLE001
        raise TaskExecutionError("时间插值引擎不可用，无法执行时间插值。") from exc

    progress(35, "正在执行时间插值")
    logger.info("Time interp started: %s (interval=%d, %d 年积日)",
                task.id, validated["target_interval"], len(validated["folders"]))
    try:
        outcome = run_timeinterp({
            "input_dir": str(input_dir),
            "output_dir": str(output_dir),
            "target_interval": validated["target_interval"],
            "variables": validated["variables"],
        })
    except Exception:                               # noqa: BLE001 - 后台任务必须收敛所有异常
        logger.exception("Time interp failed: %s", task.id)
        raise TaskExecutionError("时间插值执行失败，请查看任务详情。") from None

    progress(85, "正在校验并整理结果")
    files = sorted(p for p in output_dir.rglob("*") if p.is_file()) if output_dir.is_dir() else []
    if not files:
        raise TaskExecutionError("时间插值未生成任何结果文件，任务未完成。")
    empty = [path for path in files if path.stat().st_size == 0]
    if empty:
        raise TaskExecutionError("时间插值结果文件为空，任务未完成。")

    labels = _time_labels(_group_output(files))
    interval = validated["target_interval"]
    summary = (
        f"{validated['dataset_name']} 结果时间插值完成：{len(validated['folders'])} 个年积日 × "
        f"{validated['sites_count']} 个站点，按 {interval} 小时间隔输出 "
        f"{len(files)} 个时次文件，输出参数 " + "、".join(validated["parameters"]) + "。"
    )
    extra: dict[str, Any] = {
        "dataset": validated["dataset"],
        "parameters": list(validated["parameters"]),
        "variables": list(validated["variables"]),
        "target_interval": interval,
        "output_hours": labels,
        "output_time_range": [labels[0], labels[-1]] if labels else [],
        "output_doy_folders": sorted({path.parent.name for path in files}),
        "source_task_id": validated["task_id"],
        "source_name": validated["name"],
        "input_format": validated["input_format"],
        "input_hours": validated["input_hours"],
        "input_doy_folders": list(validated["input_folders"]),
        "input_time_range": list(validated["input_time_range"]),
        "sites_count": validated["sites_count"],
        "missing_columns": [],
        "input_files": [results.relative_path(path) for path in staged],
        "engine_stats": {"output_files": int((outcome or {}).get("output_files") or len(files))},
    }
    result = results.build_result(
        result_type="timeinterp",
        path=output_dir,
        file_name=files[0].name,
        summary=summary,
        files=files,
        extra=extra,
    )
    results.write_metadata(task.id, task.type.value, task.name, result)
    logger.info("Time interp completed: %s (%d files)", task.id, len(files))
    return result


def _group_output(files: Sequence[Path]) -> dict[str, dict[int, Path]]:
    """把输出文件按"年积日 → 时次"重新分组，用于还原时次标签。"""
    grouped: dict[str, dict[int, Path]] = {}
    for path in files:
        match = _FILENAME_RE.match(path.name)
        doy = path.parent.name
        if match is None or not _DOY_RE.match(doy):
            continue
        grouped.setdefault(doy, {})[int(match.group(2))] = path
    return grouped


__all__ = [
    "DATA_SUBDIR",
    "DEFAULT_TARGET_INTERVAL",
    "INPUT_SUBDIR",
    "SUPPORTED_INPUT_FORMATS",
    "TARGET_INTERVALS",
    "describe_input",
    "execute_timeinterp",
    "list_inputs",
    "submit_timeinterp",
    "validate_request",
]
