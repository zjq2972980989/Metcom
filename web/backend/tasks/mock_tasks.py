"""模拟任务处理函数（阶段 2 引入，阶段 3 起只保留定制产品）。

数据下载与参数计算已在阶段 3 换成真实引擎实现（见 ``tasks/handlers.py``），
本模块只剩 CUSTOM：它负责把"任务生命周期"跑通，不接触任何 MetCom 计算代码，
等阶段 5 接入真实的定制产品后再替换。
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from config import get_settings
from models.schemas import TaskResponse, TaskType
from services.errors import TaskExecutionError
from services.results import relative_path


ProgressCallback = Callable[[int, str], None]
TaskHandler = Callable[[TaskResponse, ProgressCallback], dict[str, Any]]

# 模拟进度节点（百分比, 状态说明）；与文档第九节的 20/40/60/80/100 一致。
_STEP_LISTS: dict[TaskType, tuple[tuple[int, str], ...]] = {
    TaskType.DOWNLOAD: (
        (20, "正在准备下载清单"),
        (40, "正在获取再分析资料"),
        (60, "正在校验数据文件"),
        (80, "正在写入本地目录"),
        (100, "处理完成"),
    ),
    TaskType.CALCULATION: (
        (20, "正在读取再分析资料"),
        (40, "正在计算大气参数"),
        (60, "正在反演对流层延迟"),
        (80, "正在整理结果数据"),
        (100, "处理完成"),
    ),
    TaskType.CUSTOM: (
        (20, "正在读取再分析资料"),
        (40, "正在生成计算区域"),
        (60, "正在计算定制产品"),
        (80, "正在整理结果数据"),
        (100, "处理完成"),
    ),
}


def _result_type(task: TaskResponse) -> str:
    """结果形态：下载是文件，参数计算是格网，定制产品随计算模式而定。"""
    if task.type == TaskType.DOWNLOAD:
        return "file"
    if task.type == TaskType.CUSTOM and str(task.params.get("mode")) == "station":
        return "station"
    return "grid"


def _write_result(task: TaskResponse) -> dict[str, Any]:
    """写一份模拟结果文件到 data/tasks/results/，返回 result 元信息。

    阶段三真实结果落盘后，返回结构（file_path / file_name / result_type）保持不变。
    """
    settings = get_settings()
    settings.results_dir.mkdir(parents=True, exist_ok=True)

    result_type = _result_type(task)
    payload: dict[str, Any] = {
        "mock": True,
        "task_id": task.id,
        "task_type": task.type.value,
        "result_type": result_type,
        "note": "阶段二模拟结果：未执行真实 MetCom 计算，仅用于验证任务链路。",
    }
    if task.params.get("dataset"):
        payload["dataset"] = task.params["dataset"]
    if task.params.get("parameters"):
        payload["parameters"] = task.params["parameters"]

    file_name = f"{task.id}.json"
    file_path = settings.results_dir / file_name
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    result: dict[str, Any] = {
        "file_path": relative_path(file_path),
        "file_name": file_name,
        "result_type": result_type,
        "summary": payload["note"],
    }
    if payload.get("dataset"):
        result["dataset"] = payload["dataset"]
    if payload.get("parameters"):
        result["parameters"] = payload["parameters"]
    return result


def _execute(task: TaskResponse, progress: ProgressCallback) -> dict[str, Any]:
    """公共模拟流程：逐步上报进度 → 产出结果元信息。"""
    delay = get_settings().task_mock_step_seconds

    # 开发测试用：params.simulate_failure 让任务在中途失败，验证 FAILED 链路。
    if task.params.get("simulate_failure"):
        progress(20, "正在处理")
        time.sleep(delay)
        raise TaskExecutionError("任务执行失败（开发测试：已设置 simulate_failure）")

    for percent, message in _STEP_LISTS[task.type]:
        progress(percent, message)
        if percent < 100:
            time.sleep(delay)

    return _write_result(task)


def download_task(task: TaskResponse, progress: ProgressCallback) -> dict[str, Any]:
    """数据下载任务（阶段 2 为模拟实现）。"""
    return _execute(task, progress)


def calculation_task(task: TaskResponse, progress: ProgressCallback) -> dict[str, Any]:
    """参数计算任务（阶段 2 为模拟实现）。"""
    return _execute(task, progress)


def custom_task(task: TaskResponse, progress: ProgressCallback) -> dict[str, Any]:
    """定制产品任务（阶段 2 为模拟实现）。"""
    return _execute(task, progress)


HANDLERS: dict[TaskType, TaskHandler] = {
    TaskType.DOWNLOAD: download_task,
    TaskType.CALCULATION: calculation_task,
    TaskType.CUSTOM: custom_task,
}
