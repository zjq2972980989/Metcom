"""阶段 3：MERRA-2 正式接入 Web 下载链路（POST /api/download → TaskService）的离线测试。

不联网、不访问 GES DISC、不改任何凭据配置：用 monkeypatch 替换
``engine.adapter.run_merra2_download``，让它把真实可打开的 NetCDF-4 写进任务结果目录，
从而只验证 Web 侧的数据源分流、任务状态流转、目录落点与结果校验。所有文件都落在
pytest ``tmp_path`` 隔离出来的运行期数据目录里。

对应本轮要求的 13 项：era5 仍走 ERA5、merra2 有/无凭据、downloader 正确调用、
不误调 ERA5、.nc4 vs .nc 校验、SUCCESS/FAILED、目录隔离、非法时间预检、/api/meta
能力、前端按 capability 门控。
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

START = datetime(2025, 6, 1, 0)
END = datetime(2025, 6, 1, 6)
DAY_NAME = "MERRA2_400.inst6_3d_ana_Np.20250601.nc4"


# ------------------------------------------------------------------ 工具 --


def _write_netcdf(path: Path, *, engine: str | None = None) -> None:
    """写一个最小但真实可打开的 NetCDF（test 只验证校验逻辑，不涉及真实数据）。"""
    import xarray as xr

    path.parent.mkdir(parents=True, exist_ok=True)
    dataset = xr.Dataset(
        {"T": (("lev", "lat", "lon"), np.zeros((1, 2, 2), dtype="float32"))},
        coords={"lev": [1000.0], "lat": [0.0, 1.0], "lon": [0.0, 1.0]},
    )
    dataset.to_netcdf(path, engine=engine)


def _grant_credentials(monkeypatch) -> None:
    """模拟当前机器已配置 Earthdata 凭据（不涉及任何真实凭据内容）。"""
    monkeypatch.setattr(adapter, "_merra2_credentials_available", lambda: True)
    adapter._capability_cache = None


def _deny_credentials(monkeypatch) -> None:
    monkeypatch.setattr(adapter, "_merra2_credentials_available", lambda: False)
    adapter._capability_cache = None


def _fake_merra2(record: dict, *, suffix: str = ".nc4", fail: str | None = None):
    """替换 adapter.run_merra2_download：写真实文件并返回真实结构。"""

    def fake_run_merra2_download(*, start_time, end_time, output_dir, force=False):
        record["called"] = True
        record["output_dir"] = Path(output_dir)
        if fail is not None:
            raise adapter.AdapterError(fail)
        path = Path(output_dir) / "2025" / f"MERRA2_400.inst6_3d_ana_Np.20250601{suffix}"
        _write_netcdf(path, engine="netcdf4" if suffix == ".nc4" else None)
        return {
            "files": [str(path)],
            "stats": {
                "total": 1, "downloaded": 1, "skipped": 0, "success": 1,
                "failed": 0, "bytes": path.stat().st_size,
            },
            "failed": [],
        }

    return fake_run_merra2_download


def _fake_era5(record: dict, *, suffix: str = ".nc"):
    def fake_run_download(*, source_key, start_time, end_time, output_dir, output_format="nc"):
        record["called"] = True
        record["source_key"] = source_key
        record["output_format"] = output_format
        path = Path(output_dir) / "20250601" / f"era5_global_qrzt_2025060100{suffix}"
        _write_netcdf(path)
        return {
            "files": [str(path)],
            "stats": {"total": 1, "skipped": 0, "success": 1, "failed": 0},
        }

    return fake_run_download


def _submit_merra2():
    return download_service.submit_download(
        DownloadRequest(dataset="merra2", start_time=START, end_time=END)
    )


def _submit_era5():
    return download_service.submit_download(
        DownloadRequest(dataset="era5", start_time=START, end_time=END)
    )


# ------------------------------------------------- 1/5. ERA5 仍走 ERA5 --


def test_era5_request_still_uses_era5_downloader(monkeypatch):
    era5: dict = {}
    merra2: dict = {}
    monkeypatch.setattr(adapter, "run_download", _fake_era5(era5))
    monkeypatch.setattr(adapter, "run_merra2_download", _fake_merra2(merra2))

    task = _submit_era5()
    assert task.params["dataset"] == "era5"
    assert task.params["output_format"] == "nc"
    assert task.params["pressure_levels"] and task.params["variables"]
    assert "product" not in task.params  # 不把 MERRA-2 的产品字段写进 ERA5 任务

    run_task(task.id)
    finished = get_task_service().get_task(task.id)

    assert finished.status == TaskStatus.SUCCESS
    assert era5.get("called") is True
    assert merra2.get("called") is None  # 绝不误调 MERRA-2 下载器


# ------------------------------------------------- 2/3. 凭据门控 --


def test_merra2_submit_allowed_with_credentials(monkeypatch):
    _grant_credentials(monkeypatch)

    task = _submit_merra2()

    assert task.status == TaskStatus.PENDING
    assert task.type.value == "DOWNLOAD"
    assert task.params["dataset"] == "merra2"
    assert task.params["output_format"] == "nc4"
    assert task.params["product"] == "inst6_3d_ana_Np"
    assert "pressure_levels" not in task.params
    assert "variables" not in task.params


def test_merra2_submit_rejected_without_credentials(monkeypatch):
    _deny_credentials(monkeypatch)

    with pytest.raises(InvalidRequestError) as excinfo:
        _submit_merra2()

    assert "Earthdata" in str(excinfo.value)
    tasks, total = get_task_service().list_tasks()
    assert total == 0 and tasks == []  # 不创建任务


# ------------------------------------------------- 4/6. 正确调用 + nc4 校验 --


def test_merra2_uses_its_own_downloader_and_verifies_nc4(monkeypatch):
    _grant_credentials(monkeypatch)
    merra2: dict = {}
    era5: dict = {}
    monkeypatch.setattr(adapter, "run_merra2_download", _fake_merra2(merra2))
    monkeypatch.setattr(adapter, "run_download", _fake_era5(era5))

    task = _submit_merra2()
    run_task(task.id)
    finished = get_task_service().get_task(task.id)

    assert finished.status == TaskStatus.SUCCESS
    assert finished.progress == 100
    assert finished.error is None
    assert merra2.get("called") is True
    assert era5.get("called") is None  # MERRA-2 绝不走 ERA5/CDS 下载器

    from config import get_settings

    task_dir = get_settings().results_dir / task.id / "download"
    expected = task_dir / "2025" / DAY_NAME
    assert expected.is_file() and expected.stat().st_size > 0
    assert merra2["output_dir"] == task_dir
    assert finished.result["files"] == [
        f"tasks/results/{task.id}/download/2025/{DAY_NAME}"
    ]


def test_merra2_result_metadata_records_source(monkeypatch):
    import json

    _grant_credentials(monkeypatch)
    monkeypatch.setattr(adapter, "run_merra2_download", _fake_merra2({}))

    task = _submit_merra2()
    run_task(task.id)
    finished = get_task_service().get_task(task.id)
    assert finished.status == TaskStatus.SUCCESS
    assert finished.result["dataset"] == "merra2"
    assert finished.result["status"] == "SUCCESS"
    assert finished.result["output_format"] == "nc4"

    from config import get_settings

    metadata = json.loads(
        (get_settings().results_dir / task.id / "metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["task_id"] == task.id
    assert metadata["dataset"] == "merra2"   # 不把 MERRA-2 记成 ERA5
    assert metadata["status"] == "SUCCESS"
    assert metadata["files"] == [f"tasks/results/{task.id}/download/2025/{DAY_NAME}"]


def test_merra2_nc_suffix_is_rejected(monkeypatch):
    """MERRA-2 只接受官方 .nc4；给个 .nc 结果必须 FAILED。"""
    _grant_credentials(monkeypatch)
    monkeypatch.setattr(adapter, "run_merra2_download", _fake_merra2({}, suffix=".nc"))

    task = _submit_merra2()
    run_task(task.id)
    finished = get_task_service().get_task(task.id)

    assert finished.status == TaskStatus.FAILED
    assert "格式" in (finished.error or "")


def test_era5_nc4_suffix_is_rejected(monkeypatch):
    """ERA5 仍按 .nc 校验；.nc4 结果必须 FAILED。"""
    monkeypatch.setattr(adapter, "run_download", _fake_era5({}, suffix=".nc4"))

    task = _submit_era5()
    run_task(task.id)
    finished = get_task_service().get_task(task.id)

    assert finished.status == TaskStatus.FAILED
    assert "格式" in (finished.error or "")


# ------------------------------------------------- 8/9. 成功 / 失败 --


def test_merra2_failure_marks_task_failed(monkeypatch):
    _grant_credentials(monkeypatch)
    monkeypatch.setattr(
        adapter, "run_merra2_download",
        _fake_merra2({}, fail="MERRA-2 GES DISC 下载失败：Earthdata 认证被拒绝。"),
    )

    task = _submit_merra2()
    run_task(task.id)
    finished = get_task_service().get_task(task.id)

    assert finished.status == TaskStatus.FAILED
    assert "GES DISC" in (finished.error or "")
    assert finished.result == {}


# ------------------------------------------------- 10. 任务目录隔离 --


def test_merra2_files_stay_inside_own_task_dir(monkeypatch):
    _grant_credentials(monkeypatch)
    monkeypatch.setattr(adapter, "run_merra2_download", _fake_merra2({}))

    from config import get_settings

    first = _submit_merra2()
    run_task(first.id)
    second = _submit_merra2()
    run_task(second.id)

    first_dir = (get_settings().results_dir / first.id / "download").resolve()
    second_dir = (get_settings().results_dir / second.id / "download").resolve()
    assert first_dir != second_dir
    for path in first_dir.rglob("*.nc4"):
        assert path.resolve().is_relative_to(first_dir)
        assert not path.resolve().is_relative_to(second_dir)


# ------------------------------------------------- 11. 非法时间预检 --


@pytest.mark.parametrize("hour", [1, 3, 23])
def test_invalid_merra2_time_rejected_before_task_creation(monkeypatch, hour):
    _grant_credentials(monkeypatch)
    moment = datetime(2025, 6, 1, hour)

    with pytest.raises(InvalidRequestError):
        download_service.submit_download(
            DownloadRequest(dataset="merra2", start_time=moment, end_time=moment)
        )

    _, total = get_task_service().list_tasks()
    assert total == 0


def test_merra2_rejects_era5_only_fields(monkeypatch):
    _grant_credentials(monkeypatch)

    with pytest.raises(InvalidRequestError):
        download_service.submit_download(
            DownloadRequest(
                dataset="merra2", start_time=START, end_time=END,
                pressure_levels=["500"],
            )
        )


# ------------------------------------------------- 12/13. 能力与 API --


def test_meta_reports_merra2_code_ready_but_machine_not_ready(monkeypatch, tmp_path):
    _deny_credentials(monkeypatch)
    cdsapirc = tmp_path / ".cdsapirc"
    cdsapirc.write_text("url: https://example.invalid\n", encoding="utf-8")
    monkeypatch.setattr(adapter, "_cdsapirc_path", lambda: cdsapirc)

    from fastapi.testclient import TestClient

    from main import app

    sources = {
        item["key"]: item for item in TestClient(app).get("/api/meta").json()["sources"]
    }

    assert sources["era5"]["downloader_available"] is True
    assert sources["era5"]["download_available"] is True
    assert sources["merra2"]["downloader_available"] is True   # 代码能力
    assert sources["merra2"]["download_available"] is False    # 当前机器无凭据
    for key in ("cra40", "crav15", "cmameso"):
        assert sources[key]["downloader_available"] is False
        assert sources[key]["download_available"] is False


def test_download_api_rejects_merra2_without_credentials(monkeypatch):
    _deny_credentials(monkeypatch)

    from fastapi.testclient import TestClient

    from main import app

    response = TestClient(app).post(
        "/api/download",
        json={
            "dataset": "merra2",
            "start_time": "2025-06-01T00:00:00",
            "end_time": "2025-06-01T06:00:00",
        },
    )

    assert response.status_code == 422
    assert "Earthdata" in response.json()["detail"]


def test_download_api_merra2_endpoint_reaches_success(monkeypatch):
    _grant_credentials(monkeypatch)
    monkeypatch.setattr(adapter, "run_merra2_download", _fake_merra2({}))

    from fastapi.testclient import TestClient

    from main import app

    client = TestClient(app)
    response = client.post(
        "/api/download",
        json={
            "dataset": "merra2",
            "start_time": "2025-06-01T00:00:00",
            "end_time": "2025-06-01T06:00:00",
        },
    )
    assert response.status_code == 201
    submitted = response.json()
    assert submitted["status"] == "PENDING"

    detail = client.get(f"/api/tasks/{submitted['task_id']}").json()
    assert detail["status"] == "SUCCESS"
    assert detail["result"]["dataset"] == "merra2"


def test_frontend_gates_merra2_on_capability():
    """前端必须按后端 capability 门控，而不是写死"代码支持即可下载"。"""
    view = (
        Path(__file__).resolve().parents[2]
        / "frontend" / "src" / "views" / "DataDownload.vue"
    ).read_text(encoding="utf-8")

    assert "download_available" in view        # 标签与提交门控都读真实能力
    assert "submitDownload" in view
