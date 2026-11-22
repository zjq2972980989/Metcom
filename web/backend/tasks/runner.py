"""任务生命周期执行器（阶段 2）。

统一入口是 ``run_task(task_id)``：由 FastAPI ``BackgroundTasks`` 在响应返回后调用。
本模块只做状态流转与异常收敛，具体业务全部交给 ``tasks.handlers.HANDLERS``：

    Task Runner
        ↓
    具体 Task Handler

后台线程里的异常如果外抛就没人接收，因此这里必须把所有异常收敛成 FAILED 状态 + 简洁错误信息
（完整 traceback 只写日志，不返回给用户）。
"""

from __future__ import annotations

import logging

from models.schemas import TaskType
from services.task_service import get_task_service
from services.task_store import TaskStoreError
from tasks.handlers import HANDLERS, TaskExecutionError

logger = logging.getLogger("metcom.web.tasks")


def run_task(task_id: str) -> None:
    """Atomically claim a PENDING task, then execute it once."""
    service = get_task_service()

    try:
        task = service.claim_pending(task_id)
    except TaskStoreError as exc:
        logger.error("Task claim failed, skipped: %s - %s", task_id, exc)
        return

    if task is None:
        logger.info("Task not claimed because it is not PENDING, skipped: %s", task_id)
        return

    logger.info("Task started: %s (%s)", task_id, task.type.value)

    handler = HANDLERS.get(task.type)

    def report(percent: int, message: str) -> None:
        service.update_progress(task_id, percent, message)

    try:
        if handler is None:
            raise TaskExecutionError(f"暂不支持的任务类型：{task.type.value}")
        result = handler(task, report)
    except TaskExecutionError as exc:
        logger.error("Task failed: %s — %s", task_id, exc)
        service.mark_failed(task_id, str(exc))
    except Exception:  # noqa: BLE001 - 后台任务必须收敛所有异常
        logger.exception("Task failed: %s", task_id)
        service.mark_failed(task_id, "任务执行失败，请查看任务详情。")
    else:
        service.mark_success(task_id, result)
        logger.info("Task completed: %s", task_id)
