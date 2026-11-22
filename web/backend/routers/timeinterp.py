"""在线计算 —— 时间插值接口。

    POST /api/timeinterp         →  timeinterp_service.submit_timeinterp → TaskService
                                 →  TaskRunner → metcom.timeinterp.runner
    GET  /api/timeinterp/inputs  →  timeinterp_service.list_inputs（只读扫描，不做计算）

输入是平台自己已完成的参数计算任务（按 task_id 选择），不接受任何前端路径。
任务状态与结果统一走 /api/tasks/{task_id}，这里不另建一套查询接口。
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks

from models.schemas import (TaskSubmittedResponse, TimeInterpInputListResponse,
                            TimeInterpRequest)
from services.timeinterp_service import list_inputs, submit_timeinterp
from tasks.runner import run_task

router = APIRouter(tags=["在线计算"])


@router.get(
    "/timeinterp/inputs",
    response_model=TimeInterpInputListResponse,
    summary="可用于时间插值的已完成任务",
)
def list_timeinterp_inputs() -> TimeInterpInputListResponse:
    """列出已成功的参数计算任务，并给出其真实的时次、站点数与可插值参数。"""
    return list_inputs()


@router.post(
    "/timeinterp",
    response_model=TaskSubmittedResponse,
    status_code=201,
    summary="提交时间插值任务",
)
def create_timeinterp_task(
    payload: TimeInterpRequest,
    background: BackgroundTasks,
) -> TaskSubmittedResponse:
    """创建 TIMEINTERP 任务并交给任务中心执行；立即返回任务 ID 与 PENDING 状态。"""
    task = submit_timeinterp(payload)
    background.add_task(run_task, task.id)
    return TaskSubmittedResponse(task_id=task.id, status=task.status, name=task.name)
