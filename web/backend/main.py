"""MetCom Web 后端入口。

启动（在 web/backend 目录下）::

    python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload

或者直接用 ``web/start.bat``。接口文档：http://127.0.0.1:8000/api/docs

阶段 2 起，任务中心是唯一入口：任何异步任务都走 ``services/task_service.py``，
由 ``tasks/runner.py`` 在进程内后台执行（V1.0 不使用 Celery / Redis）。

阶段 3 起，在线计算（数据下载 / 参数计算）通过 ``engine/adapter.py`` 调用现有 Python
MetCom 引擎，结果落在 ``web/data/tasks/results/<task_id>/``。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from config import get_settings
from engine.adapter import probe_engine
from services.errors import InvalidRequestError
from services.task_service import get_task_service

from routers import calculation, custom, download, health, products, tasks, timeinterp

API_PREFIX = "/api"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("metcom.web")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时准备运行期目录并探测计算引擎。"""
    settings = get_settings()
    created = settings.ensure_dirs()
    status = probe_engine()

    logger.info("MetCom Web %s 启动（%s）", settings.version, settings.phase)
    logger.info("数据目录：%s%s", settings.data_dir, "（本次新建）" if created else "")
    logger.info("任务记录：%s", settings.tasks_file)
    if status.available:
        logger.info("计算引擎：metcom %s —— %s", status.version, status.path)
    else:
        logger.warning("计算引擎不可用：%s", status.detail)

    interrupted = get_task_service().reconcile_interrupted()
    if interrupted:
        logger.warning("%d 个任务因服务重启被标记为失败", interrupted)
    yield


def create_app() -> FastAPI:
    """装配 FastAPI 应用。"""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description=(
            "MetCom —— 基于多源气象再分析资料的对流层参数精细反演平台。\n\n"
            f"当前进度：{settings.phase}。"
            "任务中心已可用（创建 / 查询 / 结果），在线计算的数据下载与参数计算"
            "已接入现有 Python MetCom 引擎。"
        ),
        lifespan=lifespan,
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json",
    )

    # 开发期前端跑在 5173，需要 CORS；生产部署由反向代理解决。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.dev_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for module in (health, products, download, calculation, custom, timeinterp, tasks):
        app.include_router(module.router, prefix=API_PREFIX)

    @app.get("/", include_in_schema=False)
    def index() -> dict[str, str]:
        return {
            "service": settings.app_name,
            "version": settings.version,
            "phase": settings.phase,
            "docs": f"{API_PREFIX}/docs",
        }

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """错误体统一成 {detail, path}，前端只需要处理一种结构。"""
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "path": request.url.path},
        )

    @app.exception_handler(InvalidRequestError)
    async def invalid_request(request: Request, exc: InvalidRequestError) -> JSONResponse:
        """业务参数校验失败：返回 422 + 面向用户的提示，且不创建任务。"""
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": str(exc), "path": request.url.path},
        )

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未处理异常：%s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "服务器内部错误，请查看后端日志。", "path": request.url.path},
        )

    return app


app = create_app()
