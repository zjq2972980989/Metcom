"""在线计算 —— 参数计算接口（阶段 3）。

    POST /api/calculation → calculation_service.submit_calculation → TaskService
                          → TaskRunner → engine.adapter → metcom.retrieval

任务状态与结果统一走 /api/tasks/{task_id}，这里不另建一套查询接口。
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks

from models.schemas import CalculationRequest, TaskSubmittedResponse
from services.calculation_service import submit_calculation
from tasks.runner import run_task

router = APIRouter(tags=["在线计算"])


@router.post(
    "/calculation",
    response_model=TaskSubmittedResponse,
    status_code=201,
    summary="提交参数计算任务",
)
def create_calculation_task(
    payload: CalculationRequest,
    background: BackgroundTasks,
) -> TaskSubmittedResponse:
    """创建 CALCULATION 任务并交给任务中心执行；立即返回任务 ID 与 PENDING 状态。"""
    task = submit_calculation(payload)
    background.add_task(run_task, task.id)
    return TaskSubmittedResponse(task_id=task.id, status=task.status, name=task.name)
