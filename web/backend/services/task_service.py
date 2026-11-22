"""任务服务（阶段 2）—— 任务中心唯一的业务入口。

调用链固定为::

    router  →  TaskService  →  TaskStore  →  data/tasks/tasks.json

路由只做 HTTP 参数校验与转发，永不直接读写 JSON。阶段三的 download / calculation /
custom 三个业务 service 也统一通过本模块创建与推进任务，不各自保存任务文件。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from config import get_settings
from models.schemas import TaskResponse, TaskResultResponse, TaskStatus, TaskType
from services.results import sanitize_result
from services.task_store import TaskStore

logger = logging.getLogger("metcom.web.tasks")

# 状态 → 默认状态说明（message 字段）
STATUS_MESSAGES: dict[TaskStatus, str] = {
    TaskStatus.PENDING: "等待执行",
    TaskStatus.RUNNING: "正在处理",
    TaskStatus.SUCCESS: "处理完成",
    TaskStatus.FAILED: "任务失败",
}

ACTIVE_STATUSES = frozenset({TaskStatus.PENDING.value, TaskStatus.RUNNING.value})


class TaskNotFoundError(LookupError):
    """任务 ID 不存在。路由层据此返回 404。"""


def _now() -> str:
    """统一的时间戳格式：带时区的 ISO 8601，前端按本地时区显示。"""
    return datetime.now(timezone.utc).isoformat()


def _to_response(raw: dict[str, Any]) -> TaskResponse:
    """落盘记录 → 对外响应；顺带清洗历史记录里可能残留的绝对路径。"""
    if raw.get("result"):
        raw = {**raw, "result": sanitize_result(raw["result"])}
    return TaskResponse.model_validate(raw)


class TaskService:
    """任务的创建、查询与状态流转。"""

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    # ------------------------------------------------------------------ 创建 --
    def create_task(
        self,
        *,
        name: str,
        type: TaskType,
        params: dict[str, Any] | None = None,
    ) -> TaskResponse:
        task = {
            "id": uuid.uuid4().hex,
            "name": name.strip() or "未命名任务",
            "type": type.value,
            "status": TaskStatus.PENDING.value,
            "created_at": _now(),
            "started_at": None,
            "finished_at": None,
            "progress": 0,
            "message": STATUS_MESSAGES[TaskStatus.PENDING],
            "params": dict(params or {}),
            "result": {},
            "error": None,
        }
        self._store.insert(task)
        logger.info("Task created: %s (%s, %s)", task["id"], task["type"], task["name"])
        return TaskResponse.model_validate(task)

    # ------------------------------------------------------------------ 查询 --
    def list_tasks(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        type: TaskType | None = None,
    ) -> tuple[list[TaskResponse], int]:
        """返回 (当前页任务, 总数)；按创建时间倒序（最新在前）。"""
        tasks = self._store.list_tasks()
        if type is not None:
            tasks = [task for task in tasks if task.get("type") == type.value]

        tasks.sort(key=lambda task: task.get("created_at") or "", reverse=True)

        total = len(tasks)
        start = max(0, (page - 1) * page_size)
        window = tasks[start : start + page_size]
        return [_to_response(task) for task in window], total

    def get_task(self, task_id: str) -> TaskResponse:
        raw = self._store.get_task(task_id)
        if raw is None:
            raise TaskNotFoundError(task_id)
        return _to_response(raw)

    def get_result(self, task_id: str) -> TaskResultResponse:
        """获取任务结果：未完成 / 成功 / 失败三种情况都返回明确状态。"""
        task = self.get_task(task_id)

        if task.status == TaskStatus.SUCCESS:
            message = task.message or STATUS_MESSAGES[TaskStatus.SUCCESS]
        elif task.status == TaskStatus.FAILED:
            message = "任务执行失败，请查看任务详情。"
        else:
            message = "任务尚未完成，请稍后查询。"

        return TaskResultResponse(
            task_id=task.id,
            status=task.status,
            ready=task.status == TaskStatus.SUCCESS,
            message=message,
            result=task.result or None,
            error=task.error,
        )

    # -------------------------------------------------------------- 状态流转 --
    def claim_pending(self, task_id: str) -> TaskResponse | None:
        """Atomically claim a PENDING task and move it to RUNNING.

        ``None`` means the task is missing or is no longer PENDING. A storage
        failure is allowed to propagate so callers never continue as if a
        claim had succeeded.
        """
        raw = self._store.update_if_status(
            task_id,
            expected_status=TaskStatus.PENDING.value,
            changes={
                "status": TaskStatus.RUNNING.value,
                "started_at": _now(),
                "message": STATUS_MESSAGES[TaskStatus.RUNNING],
                "error": None,
            },
        )
        if raw is None:
            return None
        return _to_response(raw)

    def mark_running(self, task_id: str) -> TaskResponse:
        return self._patch(
            task_id,
            status=TaskStatus.RUNNING.value,
            started_at=_now(),
            message=STATUS_MESSAGES[TaskStatus.RUNNING],
            error=None,
        )

    def update_progress(self, task_id: str, progress: int, message: str = "") -> TaskResponse:
        """上报进度。已结束的任务忽略迟到的上报，进度只增不减。"""
        raw = self._require(task_id)
        if raw.get("status") not in ACTIVE_STATUSES:
            return _to_response(raw)

        current = int(raw.get("progress") or 0)
        target = max(current, min(100, max(0, int(progress))))
        changes: dict[str, Any] = {"progress": target}
        if message:
            changes["message"] = message
        return self._patch(task_id, **changes)

    def mark_success(
        self,
        task_id: str,
        result: dict[str, Any] | None = None,
        message: str = "",
    ) -> TaskResponse:
        return self._patch(
            task_id,
            status=TaskStatus.SUCCESS.value,
            finished_at=_now(),
            progress=100,
            message=message or STATUS_MESSAGES[TaskStatus.SUCCESS],
            result=dict(result or {}),
            error=None,
        )

    def mark_failed(self, task_id: str, error: str, message: str = "") -> TaskResponse:
        return self._patch(
            task_id,
            status=TaskStatus.FAILED.value,
            finished_at=_now(),
            message=message or STATUS_MESSAGES[TaskStatus.FAILED],
            error=error,
            result={},
        )

    def delete_task(self, task_id: str) -> None:
        """删除任务记录（阶段 2 不提供 API，留给后续的清理逻辑）。"""
        if not self._store.delete(task_id):
            raise TaskNotFoundError(task_id)

    def reconcile_interrupted(self) -> int:
        """启动时收敛上次进程遗留的未结束任务，返回处理条数。

        后台任务只活在本进程里：服务重启后残留在 PENDING / RUNNING 的记录不会再有人推进，
        会永远显示"等待中 / 处理中"。因此在应用启动时统一标记为 FAILED，
        让任务中心始终反映真实状态。返回条数供启动日志使用。
        """
        interrupted = [
            task for task in self._store.list_tasks() if task.get("status") in ACTIVE_STATUSES
        ]
        for task in interrupted:
            self._store.update(
                task["id"],
                {
                    "status": TaskStatus.FAILED.value,
                    "finished_at": _now(),
                    "message": STATUS_MESSAGES[TaskStatus.FAILED],
                    "error": "服务重启，任务已中断。",
                    "result": {},
                },
            )
        if interrupted:
            logger.warning("Task interrupted by restart: %d", len(interrupted))
        return len(interrupted)

    # ------------------------------------------------------------------ 内部 --
    def _require(self, task_id: str) -> dict[str, Any]:
        raw = self._store.get_task(task_id)
        if raw is None:
            raise TaskNotFoundError(task_id)
        return raw

    def _patch(self, task_id: str, **changes: Any) -> TaskResponse:
        raw = self._store.update(task_id, changes)
        if raw is None:
            raise TaskNotFoundError(task_id)
        return _to_response(raw)


@lru_cache(maxsize=1)
def get_task_service() -> TaskService:
    """进程内单例：路由器与后台 runner 共用同一个服务与同一把文件锁。"""
    settings = get_settings()
    return TaskService(TaskStore(settings.tasks_file, settings.results_dir))
