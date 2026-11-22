"""阶段一任务 C：三源覆盖完整性不得被静默判成功（标准测站 + 定制格网两条路径）。

真实链路（TaskService → runner → calculation/custom service → engine.adapter →
metcom.retrieval.pipeline）全部保留，只替换引擎内部两处昂贵/外部依赖：
``pipeline.load_egm_mat``（避免依赖真实 EGM2008）与 ``pipeline._process_site``
（避免真实物理反演）；覆盖判定用的是真实 pipeline + 真实 Reader 扫描逻辑。

合成数据与落盘全部在 pytest 临时目录，不触碰正式数据 / EGM2008 / 历史结果。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import netCDF4
import numpy as np
import pytest

import config
import metcom.retrieval.pipeline as pipeline
from models.schemas import CalculationRequest, CustomRequest, TaskStatus
from services import calculation_service, custom_service
from services.task_service import get_task_service
from tasks.runner import run_task

DAY = "20240101"
START = datetime(2024, 1, 1, 0)
END = datetime(2024, 1, 1, 6)

LON = np.array([113.0, 113.25])
LAT = np.array([35.0, 35.25])
PLEV = np.array([1000.0, 500.0])

_TAGS = {"GPH": "gh", "RHU": "r", "SHU": "q", "TEM": "t"}


def _write_var(path: Path, var: str) -> None:
    n_lev = PLEV.size
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("longitude", LON.size)
        ds.createDimension("latitude", LAT.size)
        ds.createDimension("isobaricInhPa", n_lev)
        v = ds.createVariable("longitude", "f8", ("longitude",))
        v[:] = LON
        v = ds.createVariable("latitude", "f8", ("latitude",))
        v[:] = LAT[::-1]
        v = ds.createVariable("isobaricInhPa", "f4", ("isobaricInhPa",))
        v[:] = PLEV
        geo_h = np.linspace(-100.0, 20000.0, n_lev)
        arr = np.empty((LON.size, LAT.size, n_lev))
        for k in range(n_lev):
            base = {"gh": geo_h[k], "t": 285.0 - 5.5 * k,
                    "r": 70.0 - 5.0 * k, "q": max(0.004 - 1e-4 * k, 1e-6)}[var]
            arr[:, :, k] = base
        v = ds.createVariable(var, "f4", ("longitude", "latitude", "isobaricInhPa"))
        v[:] = arr.astype(np.float32)


def _raw_root(tmp_path) -> Path:
    return tmp_path / "raw"


def _write_group(root: Path, ts: str, *, omit: tuple[str, ...] = ()) -> None:
    folder = root / "cra40" / DAY
    folder.mkdir(parents=True, exist_ok=True)
    for tag, var in _TAGS.items():
        if tag in omit:
            continue
        _write_var(folder / f"CRA40_{tag}_{ts}_GLB_0P25_HOUR_V1_0_0.nc", var)


def _fake_process_site(site, temp_data, spec, egm):
    del temp_data, spec, egm
    return {
        "lat": site.lat, "lon": site.lon, "h": site.height,
        "ZTD": 2.0, "ZHD": 1.8, "ZWD": 0.2, "PWV": 20.0,
        "Ps": 1000.0, "Tm": 280.0, "eE": 10.0, "WVD": 5.0,
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    raw = _raw_root(tmp_path)
    dep = tmp_path / "dependent"
    (dep / "egm2008").mkdir(parents=True)
    (dep / "egm2008" / "geoidheight_EGM2008_0250.mat").write_bytes(b"egm-stub")
    (dep / "sites").mkdir(parents=True)
    (dep / "sites" / "sta_30sits.txt").write_text(
        "A000 35.125 113.125 50.0\n", encoding="utf-8")

    monkeypatch.setenv("METCOM_WEB_METCOM_DATA_ROOT", str(raw))
    monkeypatch.setenv("METCOM_WEB_METCOM_DEPENDENT_DIR", str(dep))
    config.get_settings.cache_clear()
    monkeypatch.setattr(pipeline, "load_egm_mat", lambda *a, **k: object())
    monkeypatch.setattr(pipeline, "_process_site", _fake_process_site)
    yield {"raw": raw, "dep": dep, "tmp": tmp_path}
    config.get_settings.cache_clear()


def _submit_station():
    return calculation_service.submit_calculation(CalculationRequest(
        dataset="cra40",
        parameters=["ZTD", "PWV"],
        start_time=START,
        end_time=END,
    ))


# ----------------------------------------------------------- 标准测站路径 --


def test_station_success_when_coverage_complete(env):
    _write_group(env["raw"], f"{DAY}00")
    _write_group(env["raw"], f"{DAY}06")

    task = _submit_station()
    run_task(task.id)

    finished = get_task_service().get_task(task.id)
    assert finished.status == TaskStatus.SUCCESS
    assert finished.result["engine_stats"]["hours"] == 2
    assert (config.get_settings().results_dir / task.id / "metadata.json").is_file()


def test_station_fails_on_incomplete_variable_group(env):
    _write_group(env["raw"], f"{DAY}00")
    _write_group(env["raw"], f"{DAY}06", omit=("SHU",))

    task = _submit_station()
    run_task(task.id)

    finished = get_task_service().get_task(task.id)
    assert finished.status == TaskStatus.FAILED
    assert f"{DAY}06" in (finished.error or "")
    assert "SHU" in (finished.error or "")
    # 失败任务不得写出成功元信息
    assert not (config.get_settings().results_dir / task.id / "metadata.json").is_file()


def test_station_fails_on_missing_middle_hour(env):
    _write_group(env["raw"], f"{DAY}00")
    _write_group(env["raw"], f"{DAY}12")

    task = _submit_station()
    run_task(task.id)

    finished = get_task_service().get_task(task.id)
    assert finished.status == TaskStatus.FAILED
    assert f"{DAY}06" in (finished.error or "")


def test_station_fails_when_no_data_in_range(env):
    (env["raw"] / "cra40" / DAY).mkdir(parents=True)

    task = _submit_station()
    run_task(task.id)

    finished = get_task_service().get_task(task.id)
    assert finished.status == TaskStatus.FAILED
    assert f"{DAY}00" in (finished.error or "")


def test_station_fails_with_files_but_no_complete_group(env):
    folder = env["raw"] / "cra40" / DAY
    folder.mkdir(parents=True)
    _write_var(folder / f"CRA40_GPH_{DAY}00_GLB_0P25_HOUR_V1_0_0.nc", "gh")

    task = _submit_station()
    run_task(task.id)

    finished = get_task_service().get_task(task.id)
    assert finished.status == TaskStatus.FAILED
    assert finished.status != TaskStatus.SUCCESS


# ------------------------------------------------------------- 定制格网路径 --


def _stub_grid_files(monkeypatch):
    """替换格点高程生成：只写 N 个格点站点文件，不做 DEM/EGM 科学处理。"""

    def fake(*, dataset, lon_min, lon_max, lat_min, lat_max,
             orthometric_path, ellipsoidal_path):
        lons = np.arange(float(lon_min), float(lon_max) + 1e-9, 0.25)
        lats = np.arange(float(lat_min), float(lat_max) + 1e-9, 0.25)
        lines = []
        index = 0
        for lat in lats:
            for lon in lons:
                lines.append(f"A{index:03d}   {lat:12.6f} {lon:12.6f}   {50.0:8.3f}")
                index += 1
        text = "\n".join(lines) + "\n"
        Path(orthometric_path).parent.mkdir(parents=True, exist_ok=True)
        Path(orthometric_path).write_text(text, encoding="utf-8")
        Path(ellipsoidal_path).write_text(text, encoding="utf-8")
        return {
            "grid": {"n_lon": int(lons.size), "n_lat": int(lats.size), "points": index},
            "bounds": {"lon_min": float(lon_min), "lon_max": float(lon_max),
                       "lat_min": float(lat_min), "lat_max": float(lat_max)},
            "requested_bounds": {"lon_min": float(lon_min), "lon_max": float(lon_max),
                                 "lat_min": float(lat_min), "lat_max": float(lat_max)},
            "effective_bounds": {"lon_min": float(lon_min), "lon_max": float(lon_max),
                                 "lat_min": float(lat_min), "lat_max": float(lat_max)},
            "resolution": {"lat_step": 0.25, "lon_step": 0.25},
            "height_datum": {"orthometric": "stub", "ellipsoidal": "stub",
                             "geoid": "stub"},
        }

    monkeypatch.setattr(custom_service.grid_service, "build_grid_height_files", fake)


def _submit_custom():
    return custom_service.submit_custom(CustomRequest(
        mode="GRID",
        dataset="cra40",
        parameters=["ZTD"],
        start_time=datetime(2024, 1, 1, 0),
        end_time=datetime(2024, 1, 1, 0),
        lon_min=115.0, lon_max=115.25, lat_min=37.0, lat_max=37.25,
    ))


def test_custom_grid_success_when_coverage_complete(env, monkeypatch):
    _stub_grid_files(monkeypatch)
    _write_group(env["raw"], f"{DAY}00")

    task = _submit_custom()
    run_task(task.id)

    finished = get_task_service().get_task(task.id)
    assert finished.status == TaskStatus.SUCCESS
    assert finished.result["engine_stats"]["hours"] == 1
    assert finished.result["grid"]["points"] == 4


def test_custom_grid_fails_on_incomplete_variable_group(env, monkeypatch):
    _stub_grid_files(monkeypatch)
    _write_group(env["raw"], f"{DAY}00", omit=("SHU",))

    task = _submit_custom()
    run_task(task.id)

    finished = get_task_service().get_task(task.id)
    assert finished.status == TaskStatus.FAILED
    assert "SHU" in (finished.error or "")
    assert not (config.get_settings().results_dir / task.id / "metadata.json").is_file()
