"""任务执行分发（阶段 3）。

runner.py 只认这一张表，任务中心与状态流转不因接入真实引擎而改变：

DOWNLOAD     → services.download_service.execute_download    → MetCom Downloader
    CALCULATION  → services.calculation_service.execute_calculation → MetCom Retrieval
    CUSTOM       → services.custom_service.execute_custom     → MetCom Retrieval（格网）
    TIMEINTERP   → services.timeinterp_service.execute_timeinterp → MetCom TimeInterp
"""

from __future__ import annotations

from models.schemas import TaskType
from services.calculation_service import execute_calculation
from services.custom_service import execute_custom
from services.download_service import execute_download
from services.errors import TaskExecutionError
from services.timeinterp_service import execute_timeinterp

HANDLERS = {
    TaskType.DOWNLOAD: execute_download,
    TaskType.CALCULATION: execute_calculation,
    TaskType.CUSTOM: execute_custom,
    TaskType.TIMEINTERP: execute_timeinterp,
}

__all__ = ["HANDLERS", "TaskExecutionError"]
