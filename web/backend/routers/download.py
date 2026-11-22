"""在线计算 —— 数据下载接口（阶段 3）。

    POST /api/download → download_service.submit_download → TaskService → TaskRunner
                       → engine.adapter → metcom.downloader

路由只做 HTTP 转发与后台任务登记，不直接调用引擎、不读写任务文件。
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks

from models.schemas import DownloadRequest, TaskSubmittedResponse
from services.download_service import submit_download
from tasks.runner import run_task

router = APIRouter(tags=["在线计算"])


@router.post(
    "/download",
    response_model=TaskSubmittedResponse,
    status_code=201,
    summary="提交数据下载任务",
)
def create_download_task(
    payload: DownloadRequest,
    background: BackgroundTasks,
) -> TaskSubmittedResponse:
    """创建 DOWNLOAD 任务并交给任务中心执行；立即返回任务 ID 与 PENDING 状态。"""
    task = submit_download(payload)
    background.add_task(run_task, task.id)
    return TaskSubmittedResponse(task_id=task.id, status=task.status, name=task.name)
