"""Web 后端测试公共配置（阶段 3）。

后端模块使用扁平导入（``from config import ...`` / ``from services import ...``），
因此这里把 ``web/backend`` 与仓库根加入 ``sys.path``。每个用例把运行期数据目录
指向 ``tmp_path``，清空配置与任务服务单例，保证测试不触碰真实 ``web/data``、
真实任务记录与真实 CDS 配置。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent.parent

for _path in (BACKEND_DIR, PROJECT_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


@pytest.fixture(autouse=True)
def isolated_web_data(tmp_path, monkeypatch):
    """每个用例独立的数据目录 + 清空单例缓存。"""
    monkeypatch.setenv("METCOM_WEB_DATA_DIR", str(tmp_path / "web-data"))

    import config
    from services import task_service

    config.get_settings.cache_clear()
    task_service.get_task_service.cache_clear()
    yield
    config.get_settings.cache_clear()
    task_service.get_task_service.cache_clear()
