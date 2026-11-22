"""自定义测站接入的离线测试（POST /api/calculation → TaskService → adapter）。

不联网、不做真实反演：把 ``metcom.retrieval.pipeline.run_retrieval`` 换成可记录的
替身，但 adapter 的 ``_engine_inputs`` / ``RetrievalOptions`` 构造、站点文件写出、
结果列裁剪（``_project_parameters``）全部走真实代码；替身用真实引擎解析器
``metcom.utils.io.read_site_file`` 读取站点文件，并用真实写入器
``write_result_file`` 产出格式合法的结果，因此 ``engine_stats.sites`` 确实来自
被测站点文件本身，而不是硬编码。

覆盖：A 旧请求不带 sites / B 1 个自定义测站 / C 2 个自定义测站 /
D 结果不含默认 30 站 / E 非法纬度 / F 非法经度 / G 空列表 / H 非法名称 /
I 默认站点文件未被修改。
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import pytest

from models.schemas import CalculationRequest, TaskStatus
from services import calculation_service
from services.errors import InvalidRequestError
from services.task_service import get_task_service
from tasks.runner import run_task

START = datetime(2025, 6, 1, 0)
END = datetime(2025, 6, 1, 0)

# 真实的平台默认站点文件（只读引用，测试全程不得改动它）
REAL_DEFAULT_SITES = (
    Path(__file__).resolve().parents[2] / "data" / "dependent" / "sites" / "sta_30sits.txt"
)

_RESULT_COLUMNS = (
    "latitude", "longitude", "height(m)", "ZTD(m)", "ZHD(m)", "ZWD(m)",
    "PWV(m)", "Ps(hpa)", "Tm(K)", "vapor_pressure(hpa)", "WVD",
)


# ------------------------------------------------------------------ 夹具 --


@pytest.fixture
def engine_paths(tmp_path, monkeypatch):
    """把引擎的三个输入全部指向临时目录，避免触碰真实 web/data 与真实结果。"""
    import config

    raw_root = tmp_path / "raw"
    dep_root = tmp_path / "dependent"
    (raw_root / "era5").mkdir(parents=True, exist_ok=True)
    (dep_root / "egm2008").mkdir(parents=True, exist_ok=True)
    (dep_root / "egm2008" / "geoidheight_EGM2008_0250.mat").write_bytes(b"stub")

    monkeypatch.setenv("METCOM_WEB_METCOM_DATA_ROOT", str(raw_root))
    monkeypatch.setenv("METCOM_WEB_METCOM_DEPENDENT_DIR", str(dep_root))
    monkeypatch.setenv("METCOM_WEB_METCOM_SITE_FILE", str(REAL_DEFAULT_SITES))
    config.get_settings.cache_clear()
    yield {"raw": raw_root, "dep": dep_root}
    config.get_settings.cache_clear()


def _install_fake_engine(monkeypatch, record: dict) -> None:
    """替换 run_retrieval：记录入参并用真实 IO 函数产出真实格式结果。"""
    from metcom.utils.io import read_site_file, write_result_file
    import metcom.retrieval.pipeline as pipeline

    def fake_run_retrieval(options):
        record["site_file"] = Path(options.site_file)
        record["output_path"] = Path(options.output_path)
        sites = read_site_file(options.site_file)          # 真实引擎解析器
        record["sites"] = [site.name for site in sites]

        out_dir = Path(options.output_path) / "2025152"
        out_dir.mkdir(parents=True, exist_ok=True)
        values = {column: [1.0] * len(sites) for column in _RESULT_COLUMNS}
        out_file = out_dir / f"ztd_{options.start_time}_sit_{options.source_key}.txt"
        write_result_file(sites, values, out_file)         # 真实引擎写入器
        return {"hours": 1, "sites": len(sites), "outputs": 1}

    monkeypatch.setattr(pipeline, "run_retrieval", fake_run_retrieval)


def _submit(sites=None):
    return calculation_service.submit_calculation(
        CalculationRequest(
            dataset="era5",
            parameters=["PWV", "WVD", "Tm", "ZTD", "ZHD", "ZWD"],
            start_time=START,
            end_time=END,
            sites=sites,
        )
    )


def _digest(path: Path) -> tuple[str, float]:
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime


def _task_dir(task_id: str) -> Path:
    """任务专属结果目录（results/<task_id>/）。"""
    from config import get_settings

    return get_settings().results_dir / task_id


# ------------------------------------------------- A. 旧请求不带 sites --


def test_a_request_without_sites_uses_default_file(engine_paths, monkeypatch):
    record: dict = {}
    _install_fake_engine(monkeypatch, record)

    task = _submit()                       # sites 省略 → 与历史请求完全一致
    assert "sites" not in (task.params or {})
    run_task(task.id)

    finished = get_task_service().get_task(task.id)
    assert finished.status == TaskStatus.SUCCESS
    assert record["site_file"] == REAL_DEFAULT_SITES
    assert len(record["sites"]) == 30      # 默认 30 站
    assert "sites_file" not in (finished.result or {})
    assert finished.result["engine_stats"]["sites"] == 30


# --------------------------------------------- B. 1 个自定义测站 --


def test_b_single_custom_site_writes_custom_sites_file(engine_paths, monkeypatch):
    record: dict = {}
    _install_fake_engine(monkeypatch, record)

    task = _submit([{"name": "MY01", "lat": 39.9, "lon": 116.4, "height": 43.5}])
    run_task(task.id)

    finished = get_task_service().get_task(task.id)
    assert finished.status == TaskStatus.SUCCESS

    custom = _task_dir(task.id) / "custom_sites.txt"
    assert custom.is_file()
    assert custom == record["site_file"]
    assert record["sites"] == ["MY01"]
    assert finished.result["sites_count"] == 1
    assert finished.result["sites_file"] == f"tasks/results/{task.id}/custom_sites.txt"


# --------------------------------------------- C. 2 个自定义测站 --


def test_c_two_custom_sites_engine_stats_and_lines(engine_paths, monkeypatch):
    record: dict = {}
    _install_fake_engine(monkeypatch, record)

    task = _submit([
        {"name": "MY01", "lat": 39.9, "lon": 116.4, "height": 43.5},
        {"name": "MY02", "lat": 31.2, "lon": 121.5, "height": 4.0},
    ])
    run_task(task.id)

    finished = get_task_service().get_task(task.id)
    assert finished.status == TaskStatus.SUCCESS
    assert finished.result["engine_stats"]["sites"] == 2
    assert finished.result["sites_count"] == 2

    custom = _task_dir(task.id) / "custom_sites.txt"
    lines = [line for line in custom.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 2
    assert lines[0].split()[0] == "MY01"
    assert lines[1].split()[0] == "MY02"


# ------------------------------------ D. 结果不含默认 30 站 --


def test_d_custom_result_excludes_default_stations(engine_paths, monkeypatch):
    record: dict = {}
    _install_fake_engine(monkeypatch, record)

    default_names = {
        line.split()[0]
        for line in REAL_DEFAULT_SITES.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    assert len(default_names) == 30

    task = _submit([
        {"name": "CU01", "lat": 39.9, "lon": 116.4, "height": 43.5},
        {"name": "CU02", "lat": 31.2, "lon": 121.5, "height": 4.0},
    ])
    run_task(task.id)

    custom = _task_dir(task.id) / "custom_sites.txt"
    custom_names = {line.split()[0] for line in custom.read_text(encoding="utf-8").splitlines() if line.strip()}
    assert custom_names == {"CU01", "CU02"}
    assert not (custom_names & default_names)
    assert set(record["sites"]) == {"CU01", "CU02"}

    result_file = _task_dir(task.id) / "data" / "2025152" / "ztd_2025060100_sit_era5.txt"
    body = result_file.read_text(encoding="utf-8")
    assert "name" in body.splitlines()[0]
    for name in default_names:
        assert name not in body


# -------------------------------------------- E/F/G/H. 非法输入 --


_INVALID_SITE_CASES = [
    ("lat", [{"name": "S1", "lat": 91.0, "lon": 100.0, "height": 10.0}]),
    ("lat", [{"name": "S1", "lat": -90.5, "lon": 100.0, "height": 10.0}]),
    ("lon", [{"name": "S1", "lat": 30.0, "lon": -200.0, "height": 10.0}]),
    ("lon", [{"name": "S1", "lat": 30.0, "lon": 180.5, "height": 10.0}]),
    ("empty", []),
    ("name", [{"name": "   ", "lat": 30.0, "lon": 100.0, "height": 10.0}]),
    ("name", [{"name": "A B", "lat": 30.0, "lon": 100.0, "height": 10.0}]),
]


@pytest.mark.parametrize("label,sites", _INVALID_SITE_CASES)
def test_e_f_g_h_invalid_sites_rejected_before_task(engine_paths, label, sites):
    with pytest.raises(InvalidRequestError):
        _submit(sites)
    items, total = get_task_service().list_tasks()
    assert total == 0                       # 校验失败不创建任务


@pytest.mark.parametrize("label,sites", _INVALID_SITE_CASES)
def test_e_f_g_h_api_returns_422(engine_paths, label, sites):
    from fastapi.testclient import TestClient

    from main import app

    payload = {
        "dataset": "era5",
        "parameters": ["PWV", "ZTD"],
        "start_time": "2025-06-01T00:00:00",
        "end_time": "2025-06-01T00:00:00",
        "sites": sites,
    }
    client = TestClient(app)
    response = client.post("/api/calculation", json=payload)
    assert response.status_code == 422
    detail = response.json().get("detail")
    assert isinstance(detail, str) and detail


# -------------------------------- I. 默认站点文件未被修改 --


def test_i_default_site_file_untouched(engine_paths, monkeypatch):
    before = _digest(REAL_DEFAULT_SITES)

    record: dict = {}
    _install_fake_engine(monkeypatch, record)

    # 先跑一次默认请求，再跑一次自定义请求，两者都不应改动默认文件
    default_task = _submit()
    run_task(default_task.id)
    custom_task = _submit([{"name": "ZZ01", "lat": 10.0, "lon": 20.0, "height": 5.0}])
    run_task(custom_task.id)

    assert _digest(REAL_DEFAULT_SITES) == before


# --------------------- 附加：真实 HTTP 链路（含后台执行） ---------------------


def test_api_old_request_without_sites_keeps_default(engine_paths, monkeypatch):
    """旧 HTTP 请求体（不含 sites）经真实路由后仍走平台默认站点文件。"""
    from fastapi.testclient import TestClient

    from main import app

    record: dict = {}
    _install_fake_engine(monkeypatch, record)

    payload = {
        "dataset": "era5",
        "parameters": ["PWV", "ZTD"],
        "start_time": "2025-06-01T00:00:00",
        "end_time": "2025-06-01T00:00:00",
    }
    response = TestClient(app).post("/api/calculation", json=payload)
    assert response.status_code == 201
    task_id = response.json()["task_id"]

    finished = get_task_service().get_task(task_id)
    assert finished.status == TaskStatus.SUCCESS
    assert "sites" not in (finished.params or {})
    assert record["site_file"] == REAL_DEFAULT_SITES
    assert len(record["sites"]) == 30
    assert not (_task_dir(task_id) / "custom_sites.txt").exists()


def test_api_request_with_sites_writes_task_custom_file(engine_paths, monkeypatch):
    """新 HTTP 请求体携带 sites：真实路由 + 后台执行后生成任务专属站点文件。"""
    from fastapi.testclient import TestClient

    from main import app

    record: dict = {}
    _install_fake_engine(monkeypatch, record)

    payload = {
        "dataset": "era5",
        "parameters": ["PWV", "ZTD"],
        "start_time": "2025-06-01T00:00:00",
        "end_time": "2025-06-01T00:00:00",
        "sites": [{"name": "API1", "lat": 39.9, "lon": 116.4, "height": 43.5}],
    }
    response = TestClient(app).post("/api/calculation", json=payload)
    assert response.status_code == 201
    task_id = response.json()["task_id"]

    finished = get_task_service().get_task(task_id)
    assert finished.status == TaskStatus.SUCCESS
    assert finished.params["sites"] == [
        {"name": "API1", "lat": 39.9, "lon": 116.4, "height": 43.5}
    ]
    custom = _task_dir(task_id) / "custom_sites.txt"
    assert custom.is_file()
    assert custom == record["site_file"]
    assert record["sites"] == ["API1"]
    assert finished.result["sites_count"] == 1
