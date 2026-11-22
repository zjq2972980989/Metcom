"""CUSTOM 格网任务（Grid Step 4）：POST /api/custom → TaskService → TaskRunner → adapter。

真实 ERA5 反演只执行 **1 次**（module 级 fixture），供多个用例复用；其余用例
在 HTTP 层把后台 ``run_task`` 拦掉（只验证创建链路），或在运行期替换
``adapter.run_calculation`` 来验证"结果不合规必须 FAILED"，从而避免重复昂贵计算。

所有落盘都发生在 pytest 临时目录（conftest 的 ``METCOM_WEB_DATA_DIR``），
真实 ERA5 / EGM2008 / 默认站点文件只读引用。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config import PROJECT_ROOT, get_settings
from engine import adapter
from main import app
from models.schemas import CustomRequest, TaskStatus, TaskType
from services import custom_service, grid_service
from services.task_service import get_task_service
from tasks.runner import run_task

# ---------------------------------------------------------------- 真实资源 --

REAL_DATA_ROOT = PROJECT_ROOT / "web" / "data" / "raw"
REAL_DEPENDENT_DIR = PROJECT_ROOT / "web" / "data" / "dependent"
REAL_DEFAULT_SITES = REAL_DEPENDENT_DIR / "sites" / "sta_30sits.txt"

_ENV_KEYS = ("METCOM_WEB_METCOM_DATA_ROOT", "METCOM_WEB_METCOM_DEPENDENT_DIR")

EXPECTED_COLUMNS = ["name", "latitude", "longitude", "height(m)",
                    "PWV(m)", "WVD", "Tm(K)", "ZTD(m)", "ZHD(m)", "ZWD(m)"]
EXPECTED_NAMES = [f"A{index:03d}" for index in range(9)]      # Python gridgen 原生编号


@pytest.fixture(autouse=True)
def configured_grid_egm(monkeypatch):
    """Point Gridgen at the real read-only EGM directory for this test module."""
    monkeypatch.setenv("METCOM_WEB_METCOM_DEPENDENT_DIR", str(REAL_DEPENDENT_DIR))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _payload(**overrides):
    payload = {
        "mode": "GRID",
        "dataset": "era5",
        "parameters": ["PWV", "WVD", "Tm", "ZTD", "ZHD", "ZWD"],
        "start_time": "2025-06-01T00:00:00",
        "end_time": "2025-06-01T00:00:00",
        "lon_min": 115.0,
        "lon_max": 115.5,
        "lat_min": 37.0,
        "lat_max": 37.5,
    }
    payload.update(overrides)
    return payload


def _grid_request(**overrides) -> CustomRequest:
    return CustomRequest(**_payload(**overrides))


def _digest(path: Path) -> tuple[str, float]:
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime


@pytest.fixture
def pending_only(monkeypatch):
    """HTTP 层测试：拦掉后台执行，只验证"创建任务"这一段链路。"""
    from routers import custom as custom_router

    scheduled: list[str] = []
    monkeypatch.setattr(custom_router, "run_task", scheduled.append)
    return scheduled


# ------------------------------------------------- 1~5. 创建 / 拒绝（HTTP）--


def test_grid_request_creates_custom_task(pending_only):
    response = TestClient(app).post("/api/custom", json=_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["task_id"]

    task = get_task_service().get_task(body["task_id"])
    assert task.type == TaskType.CUSTOM
    assert task.name == "ERA5 格网定制计算"
    assert task.params["mode"] == "GRID"
    assert task.params["dataset"] == "era5"
    assert task.params["parameters"] == ["PWV", "WVD", "Tm", "ZTD", "ZHD", "ZWD"]
    assert (task.params["lon_min"], task.params["lon_max"]) == (115.0, 115.5)
    assert (task.params["lat_min"], task.params["lat_max"]) == (37.0, 37.5)
    # params 只保存业务参数，不落任何内部路径
    assert set(task.params) == {"mode", "dataset", "parameters", "start_time",
                                "end_time", "lon_min", "lon_max",
                                "lat_min", "lat_max"}
    assert pending_only == [body["task_id"]]        # 已交给后台执行


@pytest.mark.parametrize("mode", ["station", "STATION", "Station"])
def test_station_mode_returns_422(pending_only, mode):
    response = TestClient(app).post("/api/custom", json=_payload(mode=mode))
    assert response.status_code == 422
    assert response.json()["detail"] == "Custom station mode is not implemented yet"
    _, total = get_task_service().list_tasks()
    assert total == 0                                # 不创建任务


_INVALID_PAYLOADS = {
    "lon_reversed": {"lon_min": 115.5, "lon_max": 115.0},
    "lat_reversed": {"lat_min": 37.5, "lat_max": 37.0},
    "lon_out_of_range": {"lon_min": -200.0},
    "lat_out_of_range": {"lat_max": 91.0},
    "no_parameters": {"parameters": []},
    "unknown_parameter": {"parameters": ["BAD"]},
    "unknown_dataset": {"dataset": "not-a-source"},
    "time_reversed": {"start_time": "2025-06-02T00:00:00"},
}


@pytest.mark.parametrize("label", sorted(_INVALID_PAYLOADS))
def test_invalid_grid_request_returns_422(pending_only, label):
    response = TestClient(app).post("/api/custom", json=_payload(**_INVALID_PAYLOADS[label]))
    assert response.status_code == 422
    assert response.json()["detail"]
    _, total = get_task_service().list_tasks()
    assert total == 0


def test_out_of_dem_range_returns_422(pending_only):
    response = TestClient(app).post(
        "/api/custom", json=_payload(lon_min=112.0, lon_max=115.0))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "exceeds DEM coverage" in detail
    assert "113~120" in detail and "36~43" in detail
    _, total = get_task_service().list_tasks()
    assert total == 0


def test_oversized_grid_returns_422(pending_only):
    response = TestClient(app).post(
        "/api/custom", json=_payload(lon_min=-180.0, lon_max=180.0,
                                     lat_min=-90.0, lat_max=90.0))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "10000" in detail and "格点" in detail
    _, total = get_task_service().list_tasks()
    assert total == 0


# ------------------------------------------------------------ 6. 初始状态 --


def test_custom_task_initial_state():
    task = custom_service.submit_custom(_grid_request())
    assert task.type == TaskType.CUSTOM
    assert task.status == TaskStatus.PENDING
    assert task.progress == 0
    assert task.result == {}
    assert task.error is None
    assert task.params["mode"] == "GRID"


# ------------------------------------------- 7~13. 真实 Retrieval（只跑一次）--


@pytest.fixture(scope="module")
def real_grid_task(tmp_path_factory):
    """真实执行一次 CUSTOM 格网任务（submit → run_task → SUCCESS）。"""
    import config
    from config import get_settings
    from services import task_service as task_service_module

    work = tmp_path_factory.mktemp("custom-grid")
    # module 级 fixture 早于 function 级的 autouse 隔离 fixture 执行，
    # 因此这里必须自己把运行期数据目录指到临时目录，绝不触碰真实 web/data。
    saved_env = {key: os.environ.get(key)
                 for key in (*_ENV_KEYS, "METCOM_WEB_DATA_DIR")}
    os.environ["METCOM_WEB_DATA_DIR"] = str(work / "web-data")
    os.environ["METCOM_WEB_METCOM_DATA_ROOT"] = str(REAL_DATA_ROOT)
    os.environ["METCOM_WEB_METCOM_DEPENDENT_DIR"] = str(REAL_DEPENDENT_DIR)
    config.get_settings.cache_clear()
    task_service_module.get_task_service.cache_clear()

    digest_before = _digest(REAL_DEFAULT_SITES)
    try:
        task = custom_service.submit_custom(_grid_request())
        run_task(task.id)
        finished = get_task_service().get_task(task.id)
        task_dir = Path(get_settings().results_dir) / task.id
        data_dir = Path(get_settings().data_dir)
    finally:
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        config.get_settings.cache_clear()
        task_service_module.get_task_service.cache_clear()

    yield {
        "task_id": task.id,
        "finished": finished,
        "task_dir": task_dir,
        "data_dir": data_dir,
        "digest_before": digest_before,
    }


def test_custom_task_final_success(real_grid_task):
    finished = real_grid_task["finished"]
    assert finished.status == TaskStatus.SUCCESS
    assert finished.error is None
    assert finished.progress == 100
    assert finished.result["engine_stats"] == {"hours": 1, "sites": 9, "outputs": 1}


def test_custom_task_uses_task_specific_site_file(real_grid_task):
    task_dir = real_grid_task["task_dir"]
    site_file = task_dir / custom_service.CUSTOM_GRID_SITES_FILENAME
    assert site_file.is_file()
    assert site_file.parent == task_dir                    # 属于当前任务目录

    names = [line.split()[0] for line in site_file.read_text(encoding="utf-8").splitlines()]
    assert names == EXPECTED_NAMES                          # 9 个格点，A000 起
    assert real_grid_task["finished"].result["sites_file"].endswith(
        f"{real_grid_task['task_id']}/{custom_service.CUSTOM_GRID_SITES_FILENAME}")


def test_task_result_site_count(real_grid_task):
    result = real_grid_task["finished"].result
    assert result["site_count"] == 9
    assert result["site_count"] == result["engine_stats"]["sites"]


def test_task_result_metadata(real_grid_task):
    result = real_grid_task["finished"].result
    assert result["mode"] == "GRID"
    assert result["dataset"] == "era5"
    assert result["grid"] == {"n_lon": 3, "n_lat": 3, "points": 9}
    assert result["bounds"] == {"lon_min": 115.0, "lon_max": 115.5,
                                "lat_min": 37.0, "lat_max": 37.5}
    assert result["requested_bounds"] == result["bounds"]
    assert result["effective_bounds"] == result["bounds"]
    assert result["resolution"] == {"lat_step": 0.25, "lon_step": 0.25}
    assert result["parameters"] == ["PWV", "WVD", "Tm", "ZTD", "ZHD", "ZWD"]
    assert result["outputs"] and result["file_count"] == len(result["outputs"]) == 1


def test_outputs_exist_and_are_isolated(real_grid_task):
    result = real_grid_task["finished"].result
    task_id = real_grid_task["task_id"]
    data_dir = real_grid_task["data_dir"]

    for relative in result["outputs"]:
        assert not Path(relative).is_absolute()             # 对外只暴露相对路径
        path = data_dir / relative
        assert path.is_file() and path.stat().st_size > 0
        assert f"tasks/results/{task_id}/" in relative
        assert path.name == "ztd_2025060100_sit_era5.txt"
        assert path.parent.name == "2025152"


def test_result_has_six_parameters(real_grid_task):
    result = real_grid_task["finished"].result
    path = real_grid_task["data_dir"] / result["outputs"][0]
    lines = path.read_text(encoding="utf-8").splitlines()
    header = lines[0].split()
    assert header == EXPECTED_COLUMNS
    assert len(lines) == 10                                 # 表头 + 9 个格点

    rows = [dict(zip(header, line.split())) for line in lines[1:]]
    assert [row["name"] for row in rows] == EXPECTED_NAMES
    for row in rows:
        for column in ("PWV(m)", "WVD", "Tm(K)", "ZTD(m)", "ZHD(m)", "ZWD(m)"):
            assert float(row[column]) > 0.0
        # 引擎内部 ZTD(n) = ZHD + ZWD；TXT 8 位小数舍入下取 7 位必须严格相等
        ztd = float(row["ZTD(m)"]); zhd = float(row["ZHD(m)"]); zwd = float(row["ZWD(m)"])
        assert abs(round(ztd - zhd - zwd, 7)) < 1e-8


def test_task_directory_isolation(real_grid_task):
    task_dir = real_grid_task["task_dir"]
    assert task_dir.name == real_grid_task["task_id"]

    # 任务目录里只有该任务自己的站点文件与结果
    site_file = task_dir / custom_service.CUSTOM_GRID_SITES_FILENAME
    outputs = sorted(task_dir.rglob("ztd_*.txt"))
    assert site_file.is_relative_to(task_dir)
    assert len(outputs) == 1
    assert all(path.is_relative_to(task_dir) for path in outputs)
    assert (task_dir / "metadata.json").is_file()

    # 默认 30 站点文件未被读取/改写
    digest = _digest(REAL_DEFAULT_SITES)
    assert digest == real_grid_task["digest_before"]
    assert len(REAL_DEFAULT_SITES.read_text(encoding="utf-8").splitlines()) == 30


# --------------------------------------------- 14~17. 运行期失败必须 FAILED --


def _run_with(monkeypatch, task, fake_call):
    monkeypatch.setattr(adapter, "run_calculation", fake_call)
    run_task(task.id)
    return get_task_service().get_task(task.id)


def test_missing_dem_fails_task(monkeypatch, tmp_path):
    task = custom_service.submit_custom(_grid_request())
    monkeypatch.setattr(grid_service, "DEFAULT_DEM_PATH", tmp_path / "missing.nc")
    finished = _run_with(monkeypatch, task, lambda **kwargs: {})
    assert finished.status == TaskStatus.FAILED
    assert finished.error and "DEM" in finished.error


def test_empty_outputs_fails_task(monkeypatch):
    task = custom_service.submit_custom(_grid_request())
    finished = _run_with(monkeypatch, task, lambda **kwargs: {
        "stats": {"hours": 1, "sites": 9, "outputs": 0}, "files": [], "columns": [],
    })
    assert finished.status == TaskStatus.FAILED
    assert finished.error and "未生成任何结果文件" in finished.error


def test_site_count_mismatch_fails_task(monkeypatch):
    task = custom_service.submit_custom(_grid_request())

    def fake(**kwargs):
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "ztd_2025060100_sit_era5.txt"
        path.write_text("name\n", encoding="utf-8")
        return {"stats": {"hours": 1, "sites": 5, "outputs": 1},
                "files": [str(path)], "columns": []}

    finished = _run_with(monkeypatch, task, fake)
    assert finished.status == TaskStatus.FAILED
    assert finished.error and "不一致" in finished.error


def test_output_outside_task_dir_fails_task(monkeypatch, tmp_path):
    task = custom_service.submit_custom(_grid_request())
    outside = tmp_path / "outside.txt"
    outside.write_text("name\n", encoding="utf-8")

    finished = _run_with(monkeypatch, task, lambda **kwargs: {
        "stats": {"hours": 1, "sites": 9, "outputs": 1},
        "files": [str(outside)], "columns": [],
    })
    assert finished.status == TaskStatus.FAILED
    assert finished.error and "越出" in finished.error


# ------------------------------------- 18~25. 结果文件完整性（P0 回归） --


def _write_grid_result_file(
    task,
    *,
    filename: str = "ztd_2025060100_sit_era5.txt",
    columns: list[str] | None = None,
    names: list[str] | None = None,
    values: dict[str, object] | None = None,
) -> Path:
    """Write a syntactically valid Grid result row-by-row for validation tests."""
    columns = list(columns or EXPECTED_COLUMNS)
    names = list(EXPECTED_NAMES if names is None else names)
    values = dict(values or {})

    output_dir = get_settings().results_dir / task.id / custom_service.DATA_SUBDIR
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename

    lines = [" ".join(columns)]
    for name in names:
        cells = [name]
        cells.extend(str(values.get(column, 1.0)) for column in columns[1:])
        lines.append(" ".join(cells))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def _grid_outcome(*paths: Path, outputs: int | None = None) -> dict:
    return {
        "stats": {"hours": 1, "sites": 9,
                  "outputs": len(paths) if outputs is None else outputs},
        "files": [str(path) for path in paths],
        "columns": list(EXPECTED_COLUMNS),
    }


def test_complete_grid_result_passes_validation(monkeypatch):
    task = custom_service.submit_custom(_grid_request())
    path = _write_grid_result_file(task)
    recorded: dict = {}

    def fake(**kwargs):
        recorded["site_file"] = Path(kwargs["site_file"])
        return _grid_outcome(path)

    finished = _run_with(monkeypatch, task, fake)

    assert finished.status == TaskStatus.SUCCESS
    assert recorded["site_file"].name == custom_service.CUSTOM_GRID_ORTHOMETRIC_FILENAME
    assert finished.result["outputs"]


def test_header_only_result_fails_even_with_another_valid_file(monkeypatch):
    task = custom_service.submit_custom(_grid_request())
    valid = _write_grid_result_file(task, filename="a_valid.txt")
    header_only = _write_grid_result_file(
        task, filename="b_header_only.txt", names=[])

    finished = _run_with(
        monkeypatch, task,
        lambda **kwargs: _grid_outcome(valid, header_only),
    )

    assert finished.status == TaskStatus.FAILED
    assert finished.result == {}
    assert finished.error and "有效数据记录数为 0" in finished.error


def test_fewer_result_rows_than_expected_fails(monkeypatch):
    task = custom_service.submit_custom(_grid_request())
    path = _write_grid_result_file(task, names=EXPECTED_NAMES[:-1])

    finished = _run_with(monkeypatch, task, lambda **kwargs: _grid_outcome(path))

    assert finished.status == TaskStatus.FAILED
    assert finished.result == {}
    assert finished.error and "有效数据记录数为 8" in finished.error


def test_more_result_rows_than_expected_fails(monkeypatch):
    task = custom_service.submit_custom(_grid_request())
    path = _write_grid_result_file(
        task, names=EXPECTED_NAMES + ["A009"])

    finished = _run_with(monkeypatch, task, lambda **kwargs: _grid_outcome(path))

    assert finished.status == TaskStatus.FAILED
    assert finished.result == {}
    assert finished.error and "有效数据记录数为 10" in finished.error


def test_missing_required_parameter_column_fails(monkeypatch):
    task = custom_service.submit_custom(_grid_request())
    columns = [column for column in EXPECTED_COLUMNS if column != "WVD"]
    path = _write_grid_result_file(task, columns=columns)

    finished = _run_with(monkeypatch, task, lambda **kwargs: _grid_outcome(path))

    assert finished.status == TaskStatus.FAILED
    assert finished.result == {}
    assert finished.error and "缺少必需列" in finished.error
    assert "WVD" in finished.error


@pytest.mark.parametrize("invalid_value", ["NaN", "not-a-number"])
def test_invalid_or_unparseable_numeric_value_fails(monkeypatch, invalid_value):
    task = custom_service.submit_custom(_grid_request())
    path = _write_grid_result_file(task, values={"Tm(K)": invalid_value})

    finished = _run_with(monkeypatch, task, lambda **kwargs: _grid_outcome(path))

    assert finished.status == TaskStatus.FAILED
    assert finished.result == {}
    assert finished.error and "Tm(K)" in finished.error


@pytest.mark.parametrize("names", [
    EXPECTED_NAMES[:-1] + ["WRONG"],
    [EXPECTED_NAMES[1], EXPECTED_NAMES[0], *EXPECTED_NAMES[2:]],
])
def test_station_identity_or_order_mismatch_fails(monkeypatch, names):
    task = custom_service.submit_custom(_grid_request())
    path = _write_grid_result_file(task, names=names)

    finished = _run_with(monkeypatch, task, lambda **kwargs: _grid_outcome(path))

    assert finished.status == TaskStatus.FAILED
    assert finished.result == {}
    assert finished.error and "站点标识或顺序" in finished.error
