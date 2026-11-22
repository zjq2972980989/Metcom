"""任务中心接口（阶段 2）。

V1.0 不上 Celery/Redis/消息队列：创建任务后由 FastAPI ``BackgroundTasks`` 在进程内后台执行，
任务记录与结果落在 ``web/data/tasks/``。路由本身只做校验与转发，业务在 ``services/task_service.py``。
"""

from __future__ import annotations

import math
import zipfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse

from config import get_settings
from models.schemas import (
    TaskCreate,
    TaskCreatedResponse,
    TaskListResponse,
    TaskResponse,
    TaskResultResponse,
    TaskStatus,
    TaskType,
)
from services.results import resolve_path
from services.task_service import TaskNotFoundError, get_task_service
from tasks.runner import run_task

router = APIRouter(prefix="/tasks", tags=["任务中心"])

# 结果文件的显式 MIME 映射：不依赖扩展名猜测（.dat / .mat 会被猜成 text/plain）。
_RESULT_MEDIA_TYPES = {
    ".txt": "text/plain; charset=utf-8",
    ".dat": "text/plain; charset=utf-8",
    ".mat": "application/x-matlab-data",
    ".nc": "application/x-netcdf",
    ".nc4": "application/x-netcdf",
    ".zip": "application/zip",
}


def _media_type_for(path: Path) -> str:
    """按实际文件扩展名给出 MIME；未知扩展名走通用二进制。"""
    return _RESULT_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _service():
    return get_task_service()


@router.post("", response_model=TaskCreatedResponse, status_code=201, summary="创建任务")
def create_task(payload: TaskCreate, background: BackgroundTasks) -> TaskCreatedResponse:
    """创建任务并提交后台执行；返回 PENDING 状态与任务 ID。"""
    task = _service().create_task(name=payload.name, type=payload.type, params=payload.params)
    background.add_task(run_task, task.id)
    return TaskCreatedResponse(id=task.id, status=task.status)


@router.get("", response_model=TaskListResponse, summary="任务列表")
def list_tasks(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    type: TaskType | None = Query(None, description="按任务类型筛选；不传则返回全部"),
) -> TaskListResponse:
    """任务历史，最新在前。"""
    items, total = _service().list_tasks(page=page, page_size=page_size, type=type)
    return TaskListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total else 0,
    )


@router.get("/{task_id}", response_model=TaskResponse, summary="任务详情 / 状态")
def get_task(task_id: str) -> TaskResponse:
    """单个任务的完整信息（轮询状态也用这个接口，不额外开 /status）。"""
    try:
        return _service().get_task(task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc


@router.get("/{task_id}/result", response_model=TaskResultResponse, summary="任务结果")
def get_task_result(task_id: str) -> TaskResultResponse:
    """任务结果：未完成返回明确状态，成功返回 result，失败返回 error。"""
    try:
        return _service().get_result(task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc


@router.get("/{task_id}/download", summary="任务结果下载")
def download_task_result(task_id: str):
    """下载任务结果：只有 SUCCESS 且结果文件确实存在时才提供下载。"""
    try:
        result = _service().get_result(task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc

    if result.status != TaskStatus.SUCCESS or not result.ready:
        raise HTTPException(status_code=409, detail="任务尚未完成，结果文件暂不可用。")

    relative = str((result.result or {}).get("file_path") or "")
    if not relative:
        raise HTTPException(status_code=409, detail="结果文件暂不可用。")

    try:
        target = resolve_path(relative)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="结果文件暂不可用。") from exc

    if target.is_file():
        return FileResponse(target, filename=target.name,
                            media_type=_media_type_for(target))
    if not target.is_dir():
        raise HTTPException(status_code=409, detail="结果文件暂不可用。")

    entries = sorted(item for item in target.rglob("*") if item.is_file())
    if not entries:
        raise HTTPException(status_code=409, detail="结果文件暂不可用。")
    if len(entries) == 1:
        return FileResponse(entries[0], filename=entries[0].name,
                            media_type=_media_type_for(entries[0]))

    archive = _ensure_archive(task_id, target, entries)
    return FileResponse(archive, filename=f"{task_id}-result.zip", media_type="application/zip")


def _ensure_archive(task_id: str, directory: Path, entries: list[Path]) -> Path:
    """把目录结果打成 zip（已存在且不旧于内容时直接复用）。"""
    settings = get_settings()
    archive = settings.results_dir / task_id / "result.zip"
    newest = max(item.stat().st_mtime for item in entries)

    if archive.is_file() and archive.stat().st_mtime >= newest:
        return archive
    if archive.is_file():
        archive.unlink()

    total = sum(item.stat().st_size for item in entries)
    if total > settings.result_zip_max_mb * 1024 * 1024:
        raise HTTPException(status_code=409, detail="结果数据过大，暂不支持打包下载。")

    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for item in entries:
            if item == archive:
                continue
            bundle.write(item, item.relative_to(directory).as_posix())
    return archive
