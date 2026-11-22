"""阶段 3：真实 ERA5 下载接入 TaskService 的测试。

不访问 CDS、不修改 .cdsapirc：用 monkeypatch 替换 ``engine.adapter.run_download``，
让它把真实的 NetCDF 写进任务结果目录，从而只验证 Web 侧的任务链、目录落点与结果校验；
失败用例则模拟 CDS 失败 / 空文件 / 损坏文件 / 部分时次失败，验证任务进入 FAILED。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from engine import adapter
from models.schemas import DownloadRequest, TaskStatus
from services import download_service
from services.errors import InvalidRequestError
from services.task_service import get_task_service
from tasks.runner import run_task

START = datetime(2024, 1, 3, 0)
END = datetime(2024, 1, 3, 1)


def _write_netcdf(path: Path) -> None:
    """写一个最小但真实可打开的 NetCDF（不依赖 CDS）。"""
    import xarray as xr

    path.parent.mkdir(parents=True, exist_ok=True)
    dataset = xr.Dataset(
        {"t": (("level", "latitude", "longitude"), np.zeros((1, 2, 2), dtype="float32"))},
        coords={"level": [1000.0], "latitude": [0.0, 1.0], "longitude": [0.0, 1.0]},
    )
    dataset.to_netcdf(path)


def _download_path(output_dir: Path, moment: datetime) -> Path:
    return output_dir / moment.strftime("%Y%m%d") / f"era5_global_qrzt_{moment:%Y%m%d%H}.nc"


def _fake_success(record: dict):
    """替换 adapter.run_download：真实写文件 + 返回真实结构。"""

    def fake_run_download(*, source_key, start_time, end_time, output_dir, output_format="nc"):
        output_dir = Path(output_dir)
        path = _download_path(output_dir, start_time)
        _write_netcdf(path)
        record["output_dir"] = output_dir
        record["source_key"] = source_key
        record["output_format"] = output_format
        return {
            "files": [str(path)],
            "stats": {"total": 1, "skipped": 0, "success": 1, "failed": 0},
            "output_dir": str(output_dir),
        }

    return fake_run_download


def _submit():
    return download_service.submit_download(
        DownloadRequest(dataset="era5", start_time=START, end_time=END)
    )


# ------------------------------------------------------------------ 语法/接口 --


def test_download_router_and_service_import():
    from routers import download as download_router

    paths = {route.path for route in download_router.router.routes}
    assert "/download" in paths


def test_download_options_are_the_verified_era5_config():
    options = adapter.download_options()
    assert options["formats"] == ("nc",)
    assert options["grid"] is None
    assert len(options["pressure_levels"]) == 37
    assert set(options["variables"]) == {
        "geopotential",
        "relative_humidity",
        "specific_humidity",
        "temperature",
    }


# ------------------------------------------------------------------ 任务创建 --


def test_submit_download_creates_pending_task():
    task = _submit()
    assert task.status == TaskStatus.PENDING
    assert task.type.value == "DOWNLOAD"
    assert task.progress == 0
    assert task.params["output_format"] == "nc"
    assert len(task.params["pressure_levels"]) == 37
    assert set(task.params["variables"]) == {
        "geopotential",
        "relative_humidity",
        "specific_humidity",
        "temperature",
    }


def test_unsupported_pressure_levels_rejected_before_task_creation():
    with pytest.raises(InvalidRequestError):
        download_service.submit_download(
            DownloadRequest(
                dataset="era5", start_time=START, end_time=END, pressure_levels=["500"]
            )
        )


# -------------------------------------------------------------- 成功 / 失败 --


def test_download_success_writes_verified_file(monkeypatch):
    record: dict = {}
    monkeypatch.setattr(adapter, "run_download", _fake_success(record))

    task = _submit()
    run_task(task.id)

    finished = get_task_service().get_task(task.id)
    assert finished.status == TaskStatus.SUCCESS
    assert finished.progress == 100
    assert finished.error is None

    from config import get_settings

    results_dir = get_settings().results_dir
    task_dir = results_dir / task.id / "download"
    expected = _download_path(task_dir, START)
    assert expected.is_file()
    assert expected.stat().st_size > 0
    assert record["output_dir"] == task_dir
    assert record["source_key"] == "era5"
    assert record["output_format"] == "nc"

    # 结果元信息只暴露相对路径，且指向任务自己的目录
    file_path = finished.result["file_path"]
    assert not Path(file_path).is_absolute()
    assert file_path == f"tasks/results/{task.id}/download"
    assert finished.result["files"] == [
        f"tasks/results/{task.id}/download/20240103/{expected.name}"
    ]


def test_download_failure_marks_task_failed(monkeypatch):
    def boom(**_kwargs):
        raise adapter.AdapterError("CDS 请求失败：ERA5 服务不可用。")

    monkeypatch.setattr(adapter, "run_download", boom)
    task = _submit()
    run_task(task.id)

    finished = get_task_service().get_task(task.id)
    assert finished.status == TaskStatus.FAILED
    assert "CDS" in (finished.error or "")
    assert finished.result == {}


def test_empty_download_file_marks_task_failed(monkeypatch):
    def fake_run_download(*, source_key, start_time, end_time, output_dir, output_format="nc"):
        path = _download_path(Path(output_dir), start_time)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        return {
            "files": [str(path)],
            "stats": {"total": 1, "skipped": 0, "success": 1, "failed": 0},
        }

    monkeypatch.setattr(adapter, "run_download", fake_run_download)
    task = _submit()
    run_task(task.id)

    finished = get_task_service().get_task(task.id)
    assert finished.status == TaskStatus.FAILED
    assert "空" in (finished.error or "")


def test_corrupt_netcdf_marks_task_failed(monkeypatch):
    def fake_run_download(*, source_key, start_time, end_time, output_dir, output_format="nc"):
        path = _download_path(Path(output_dir), start_time)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"this is definitely not a netcdf file")
        return {
            "files": [str(path)],
            "stats": {"total": 1, "skipped": 0, "success": 1, "failed": 0},
        }

    monkeypatch.setattr(adapter, "run_download", fake_run_download)
    task = _submit()
    run_task(task.id)

    finished = get_task_service().get_task(task.id)
    assert finished.status == TaskStatus.FAILED
    assert "NetCDF" in (finished.error or "")


def test_partial_download_failure_marks_task_failed(monkeypatch):
    def fake_run_download(*, source_key, start_time, end_time, output_dir, output_format="nc"):
        path = _download_path(Path(output_dir), start_time)
        _write_netcdf(path)
        return {
            "files": [str(path)],
            "stats": {"total": 2, "skipped": 0, "success": 1, "failed": 1},
        }

    monkeypatch.setattr(adapter, "run_download", fake_run_download)
    task = _submit()
    run_task(task.id)

    finished = get_task_service().get_task(task.id)
    assert finished.status == TaskStatus.FAILED


# ------------------------------------------------------------------ HTTP 接口 --


def test_download_api_endpoint_reaches_success(monkeypatch):
    from fastapi.testclient import TestClient

    from main import app

    monkeypatch.setattr(adapter, "run_download", _fake_success({}))
    client = TestClient(app)

    response = client.post(
        "/api/download",
        json={
            "dataset": "era5",
            "start_time": "2024-01-03T00:00:00",
            "end_time": "2024-01-03T01:00:00",
        },
    )
    assert response.status_code == 201
    submitted = response.json()
    assert submitted["status"] == "PENDING"

    detail = client.get(f"/api/tasks/{submitted['task_id']}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "SUCCESS"
