"""任务记录存储层（阶段 2）。

V1.0 不引入数据库：任务记录以 JSON 文件保存在 ``web/data/tasks/tasks.json``。

并发说明
--------
uvicorn 把同步 endpoint 放到线程池里执行，同一进程内确实会出现并发"读 → 改 → 写"。
因此本模块用一把进程内的 ``threading.RLock`` 串行化整个读改写过程，落盘时先写
``tasks.json.tmp`` 再 ``os.replace`` 原子替换，避免出现写坏的 tasks.json。

这覆盖 V1.0 的单进程部署形态。将来改成多 worker / 多机部署时，把本模块换成真正的
存储（数据库或带跨进程锁的实现）即可，TaskService 与路由器都不需要改。

不做的事：不存原始数据。GRIB / NetCDF / TXT 内容一律不进任务记录，只存业务参数与结果元信息。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("metcom.web.tasks")


class TaskStoreError(RuntimeError):
    """任务记录文件不可用（损坏 / 无法读 / 无法写）。"""


class TaskStore:
    """tasks.json 的唯一读写出口。"""

    def __init__(self, file_path: Path, results_dir: Path) -> None:
        self._file = Path(file_path)
        self._results_dir = Path(results_dir)
        self._lock = threading.RLock()

    @property
    def file_path(self) -> Path:
        return self._file

    @property
    def results_dir(self) -> Path:
        return self._results_dir

    # ------------------------------------------------------------ 对外接口 --
    def list_tasks(self) -> list[dict[str, Any]]:
        """返回全部任务记录（文件不存在时返回空列表，并自动建目录）。"""
        with self._lock:
            return self._read_unlocked()

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """按 ID 取任务；返回值是本次解析出的独立副本，可以安全修改。"""
        with self._lock:
            for task in self._read_unlocked():
                if task.get("id") == task_id:
                    return task
        return None

    def insert(self, task: dict[str, Any]) -> dict[str, Any]:
        """写入一个新任务（放在最前，保证列表默认最新在前）。"""
        with self._lock:
            tasks = self._read_unlocked()
            tasks.insert(0, task)
            self._write_unlocked(tasks)
        return task

    def update(self, task_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        """按 ID 合并字段并落盘；任务不存在时返回 None。"""
        with self._lock:
            tasks = self._read_unlocked()
            for task in tasks:
                if task.get("id") == task_id:
                    task.update(changes)
                    self._write_unlocked(tasks)
                    return task
        return None

    def update_if_status(
        self,
        task_id: str,
        *,
        expected_status: str,
        changes: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Atomically update only when the persisted status still matches."""
        with self._lock:
            tasks = self._read_unlocked()
            for task in tasks:
                if task.get("id") != task_id:
                    continue
                if task.get("status") != expected_status:
                    return None
                task.update(changes)
                self._write_unlocked(tasks)
                return task
        return None

    def delete(self, task_id: str) -> bool:
        """删除任务记录；返回是否真的删掉了。"""
        with self._lock:
            tasks = self._read_unlocked()
            remaining = [task for task in tasks if task.get("id") != task_id]
            if len(remaining) == len(tasks):
                return False
            self._write_unlocked(remaining)
            return True

    # ---------------------------------------------------------------- 内部 --
    def _read_unlocked(self) -> list[dict[str, Any]]:
        if not self._file.exists():
            return []

        try:
            raw = self._file.read_text(encoding="utf-8")
        except OSError as exc:
            raise TaskStoreError(f"任务记录文件读取失败：{exc}") from exc

        if not raw.strip():
            return []

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TaskStoreError(f"任务记录文件不是合法 JSON（{self._file}）：{exc}") from exc

        tasks = payload.get("tasks") if isinstance(payload, dict) else payload
        if not isinstance(tasks, list):
            raise TaskStoreError(f"任务记录文件结构不正确（{self._file}）：期望任务数组")
        return [task for task in tasks if isinstance(task, dict)]

    def _write_unlocked(self, tasks: list[dict[str, Any]]) -> None:
        self._ensure_dirs()
        payload = {"version": 1, "tasks": tasks}
        tmp = self._file.with_name(f"{self._file.name}.tmp")
        try:
            with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self._file)
        except OSError as exc:
            raise TaskStoreError(f"任务记录文件写入失败：{exc}") from exc

    def _ensure_dirs(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._results_dir.mkdir(parents=True, exist_ok=True)
