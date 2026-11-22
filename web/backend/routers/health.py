"""系统接口：健康检查、元信息、计算引擎状态。

阶段一唯一返回真实数据的接口组。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from config import get_settings
from engine.adapter import engine_info, list_parameters, list_sources
from models.schemas import CustomMode, EngineInfo, HealthResponse, MetaResponse, TaskStatus, TaskType

router = APIRouter(tags=["系统"])


@router.get("/health", response_model=HealthResponse, summary="服务健康检查")
def health() -> HealthResponse:
    """前端与运维用的存活探针。"""
    settings = get_settings()
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        version=settings.version,
        phase=settings.phase,
        time=datetime.now(timezone.utc),
    )


@router.get("/meta", response_model=MetaResponse, summary="元信息（数据源 / 参数 / 任务枚举）")
def meta() -> MetaResponse:
    """前端启动时拉一次：下拉框选项、参数勾选框都来自这里，不在前端写死。"""
    return MetaResponse(
        sources=list_sources(),
        parameters=list_parameters(),
        task_status=list(TaskStatus),
        task_types=list(TaskType),
        custom_modes=list(CustomMode),
    )


@router.get("/engine", response_model=EngineInfo, summary="计算引擎状态")
def engine() -> EngineInfo:
    """返回现有 Python MetCom 引擎的可用性与版本，供"关于平台"页显示。"""
    return EngineInfo(**engine_info())