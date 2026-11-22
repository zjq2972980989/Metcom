"""定制产品接口（阶段 5）：格网模式 / 站点模式统一入口。

    POST /api/custom → custom_service.submit_custom → TaskService（CUSTOM 任务）
                     → TaskRunner → engine.adapter → metcom.retrieval

V1.0 只实现 GRID 模式：STATION 在创建任务前返回 422
（``Custom station mode is not implemented yet``），不会创建任务。

路由层只做参数校验与转发，不 import metcom、不调用 adapter、不执行科学计算；
任务状态与结果统一走 /api/tasks/{task_id}，这里不另建一套查询接口。
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks

from models.schemas import CustomRequest, TaskSubmittedResponse
from routers.common import not_implemented
from services.custom_service import submit_custom
from tasks.runner import run_task

router = APIRouter(tags=["定制产品"])

PHASE = "阶段 5：定制产品"


@router.post(
    "/custom",
    response_model=TaskSubmittedResponse,
    status_code=201,
    summary="提交定制产品任务",
)
def create_custom_task(
    payload: CustomRequest,
    background: BackgroundTasks,
) -> TaskSubmittedResponse:
    """创建 CUSTOM（GRID）任务并交给任务中心执行；立即返回任务 ID 与 PENDING 状态。"""
    task = submit_custom(payload)
    background.add_task(run_task, task.id)
    return TaskSubmittedResponse(task_id=task.id, status=task.status, name=task.name)


@router.get("/custom/{task_id}", summary="查询定制产品任务")
def get_custom_task(task_id: str):
    raise not_implemented(PHASE, "定制产品任务状态查询")
