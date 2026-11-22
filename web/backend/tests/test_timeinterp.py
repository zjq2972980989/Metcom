"""在线计算 · 时间插值接入的测试（POST /api/timeinterp → metcom.timeinterp）。

不联网、不做大规模计算：输入用**真实引擎写入器** ``write_result_file`` 造出的
合法结果文件，插值用**原生** ``metcom.timeinterp.runner.run_timeinterp`` 真跑，
因此断言的是真实算法行为，不是替身行为。

覆盖：输入校验（不存在 / 未成功 / 非参数计算 / MAT / 单时次 / 缺列 / 删列 / 非法间隔 /
越界文件）、多站多参数多时次、跨天扩展、与独立 CubicSpline 的数值一致性、
源结果目录字节级不被修改、任务生命周期与下载。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
from scipy.interpolate import CubicSpline

from config import get_settings
from metcom.utils.io import Site, write_result_file
from models.schemas import TaskStatus, TaskType, TimeInterpRequest
from services import timeinterp_service
from services.errors import InvalidRequestError
from services.task_service import get_task_service
from tasks.runner import run_task

# 真实数据里的年积日 → 日期映射（2025152 = 2025-06-01，2025153 = 2025-06-02）
DOY_A, DATE_A = "2025152", "20250601"
DOY_B, DATE_B = "2025153", "20250602"
HOURS_6H = (0, 6, 12, 18)

SITES = (
    Site(name="A001", lat=36.125, lon=115.0, height=51.5),
    Site(name="A002", lat=39.904, lon=116.407, height=43.5),
)

# 原生固定输出的 12 列（4 列站点信息 + 8 个参数），顺序即结果文件列顺序
EXPECTED_HEADER = ["name", "latitude", "longitude", "height(m)", "ZTD(m)", "ZHD(m)",
                   "ZWD(m)", "PWV(m)", "Ps(hpa)", "Tm(K)", "vapor_pressure(hpa)", "WVD"]
ZTD_COLUMN, ZHD_COLUMN = 4, 5


def _values(hour: int) -> dict[str, list[float]]:
    """每个时次一组真实感数值列（不同站、不同时刻取值不同）。"""
    out: dict[str, list[float]] = {
        "latitude": [site.lat for site in SITES],
        "longitude": [site.lon for site in SITES],
        "height(m)": [site.height for site in SITES],
    }
    for index, _site in enumerate(SITES):
        offset = 0.001 * index
        out.setdefault("ZTD(m)", []).append(2.40 + offset + 0.010 * hour)
        out.setdefault("ZHD(m)", []).append(2.30 + offset + 0.005 * hour)
        out.setdefault("ZWD(m)", []).append(0.10 + 0.005 * hour)
        out.setdefault("PWV(m)", []).append(18.0 + offset + 0.20 * hour)
        out.setdefault("Ps(hpa)", []).append(1000.0 + 0.50 * hour)
        out.setdefault("Tm(K)", []).append(275.0 + 0.10 * hour)
        out.setdefault("vapor_pressure(hpa)", []).append(12.0 + 0.05 * hour)
        out.setdefault("WVD", []).append(9.0 + 0.10 * hour)
    return out


def _write_day(folder: Path, date_part: str, hours, source: str, ext: str) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for hour in hours:
        path = folder / f"ztd_{date_part}{hour:02d}_sit_{source}.{ext}"
        write_result_file(SITES, _values(hour), path)
        written.append(path)
    return written


def _register_source_task(
    *,
    dataset: str = "merra2",
    days: dict[str, tuple[str, tuple[int, ...]]] | None = None,
    ext: str = "txt",
    status: TaskStatus = TaskStatus.SUCCESS,
    task_type: TaskType = TaskType.CALCULATION,
) -> tuple[str, Path]:
    """在隔离数据目录里造一个"已完成的反演结果任务"，返回 (task_id, 结果目录)。"""
    settings = get_settings()
    service = get_task_service()
    task = service.create_task(
        name=f"{dataset} 参数计算",
        type=task_type,
        params={"dataset": dataset, "parameters": ["ZTD"], "output_format": ext},
    )
    data_dir = settings.results_dir / task.id / "data"
    files: list[Path] = []
    for doy, (date_part, hours) in (days or {DOY_A: (DATE_A, HOURS_6H)}).items():
        files.extend(_write_day(data_dir / doy, date_part, hours, dataset, ext))

    if status == TaskStatus.SUCCESS:
        service.mark_success(task.id, {
            "dataset": dataset, "result_type": "station", "files": [str(p) for p in files],
        })
    elif status == TaskStatus.FAILED:
        service.mark_failed(task.id, "测试用失败任务")
    return task.id, data_dir


PARTIAL_COLUMNS = ("ZTD(m)", "Ps(hpa)", "vapor_pressure(hpa)")


def _register_partial_source_task(columns=PARTIAL_COLUMNS) -> tuple[str, Path]:
    """造一个"只反演了部分参数"的结果（与数据库里真实任务一致）。"""
    settings = get_settings()
    service = get_task_service()
    task = service.create_task(name="部分参数结果", type=TaskType.CALCULATION,
                              params={"dataset": "merra2"})
    folder = settings.results_dir / task.id / "data" / DOY_A
    folder.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    for hour in HOURS_6H:
        path = folder / f"ztd_{DATE_A}{hour:02d}_sit_merra2.txt"
        rows = ["name latitude longitude height(m) " + " ".join(columns)]
        for site in SITES:
            rows.append(f"{site.name} {site.lat} {site.lon} {site.height} "
                        + " ".join("1.0" for _ in columns))
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        files.append(path)
    service.mark_success(task.id, {"dataset": "merra2", "result_type": "station",
                                   "files": [str(p) for p in files]})
    return task.id, folder


def _manifest(root: Path) -> dict[str, tuple[int, int, str]]:
    """目录清单：相对路径 → (字节数, mtime_ns, sha256)，用于证明源结果未被改动。"""
    out: dict[str, tuple[int, int, str]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            stat = path.stat()
            out[path.relative_to(root).as_posix()] = (
                stat.st_size,
                stat.st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
    return out


def _read_rows(path: Path) -> tuple[list[str], list[list[str]]]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return lines[0].split(), [line.split() for line in lines[1:]]


def _expected(column_base: float, slope: float, index: int, hour: int) -> float:
    """独立 CubicSpline 复算（与原生 _interp_spline 同一算法、同一参数）。"""
    hours = np.asarray(HOURS_6H, dtype=float)
    values = np.asarray([column_base + 0.001 * index + slope * h for h in HOURS_6H])
    return float(CubicSpline(hours, values, bc_type="not-a-knot", extrapolate=True)(hour))

# --------------------------------------------------------------------------- #
# 原生契约一致性
# --------------------------------------------------------------------------- #
def test_required_parameters_follow_native_default_variables():
    from metcom.timeinterp.runner import DEFAULT_VARIABLES

    assert list(timeinterp_service.required_columns()) == list(DEFAULT_VARIABLES)
    assert len(timeinterp_service.required_parameters()) == 8


def test_native_writer_rejects_partial_variable_sets():
    """记录原生约束本身：列宽写死为 12，非 8 个变量会直接抛错。"""
    from metcom.timeinterp.runner import _TIMEINTERP_HEADER_SPECS
    from metcom.utils.fmt import fmt_fixed_width

    assert len(_TIMEINTERP_HEADER_SPECS) == 12
    with pytest.raises(ValueError):
        fmt_fixed_width(["name", "latitude", "longitude", "height(m)", "ZTD(m)"],
                        _TIMEINTERP_HEADER_SPECS, sep=" ")


# --------------------------------------------------------------------------- #
# 端到端：真实调用原生插值算法
# --------------------------------------------------------------------------- #
def test_single_day_end_to_end_matches_native_algorithm():
    source_id, source_dir = _register_source_task()
    before = _manifest(source_dir)

    task = timeinterp_service.submit_timeinterp(TimeInterpRequest(
        source_task_id=source_id, target_interval=1))
    assert task.type == TaskType.TIMEINTERP
    run_task(task.id)

    stored = get_task_service().get_task(task.id)
    assert stored.status == TaskStatus.SUCCESS, stored.error
    result = stored.result
    assert result["result_type"] == "timeinterp"
    assert result["sites_count"] == len(SITES)
    assert result["target_interval"] == 1

    # 单日 + 最后一天：目标时刻为 0..18，共 19 个文件
    files = sorted((get_settings().results_dir / task.id / "data").rglob("ztd_*.txt"))
    assert [path.name for path in files] == [
        f"ztd_{DATE_A}{hour:02d}_sit_merra2.txt" for hour in range(19)
    ]

    # 表头 = 原生固定的 12 列；数据行 = 2 个测站
    header, rows = _read_rows(files[0])
    assert header == EXPECTED_HEADER
    assert [row[0] for row in rows] == ["A001", "A002"]

    # 0 时结果 = 输入 0 时数值（样条在已知点上必然复原）
    for index, row in enumerate(rows):
        assert float(row[ZTD_COLUMN]) == pytest.approx(2.40 + 0.001 * index, abs=1e-6)

    # 逐时次、逐站、逐参数与独立 CubicSpline 比对（原生即 not-a-knot + 外推）
    for hour in range(19):
        _, rows_at = _read_rows(files[hour])
        for index in range(len(SITES)):
            assert float(rows_at[index][ZTD_COLUMN]) == pytest.approx(
                _expected(2.40, 0.010, index, hour), abs=1e-6)
            assert float(rows_at[index][ZHD_COLUMN]) == pytest.approx(
                _expected(2.30, 0.005, index, hour), abs=1e-6)

    # 源结果目录字节级未变：文件集合、大小、mtime、sha256 全部一致
    assert _manifest(source_dir) == before
    assert not list(source_dir.rglob("*24_sit_*"))


def test_cross_day_extension_writes_24h_from_next_day_zero():
    source_id, source_dir = _register_source_task(days={
        DOY_A: (DATE_A, HOURS_6H),
        DOY_B: (DATE_B, HOURS_6H),
    })
    before = _manifest(source_dir)

    task = timeinterp_service.submit_timeinterp(TimeInterpRequest(
        source_task_id=source_id, target_interval=3))
    run_task(task.id)
    assert get_task_service().get_task(task.id).status == TaskStatus.SUCCESS

    # 第一天被跨天扩展 → 0..24（9 个）；最后一天 → 0..18（7 个）
    data_dir = get_settings().results_dir / task.id / "data"
    day_a = sorted((data_dir / DOY_A).glob("*.txt"))
    day_b = sorted((data_dir / DOY_B).glob("*.txt"))
    assert [path.name for path in day_a] == [
        f"ztd_{DATE_A}{hour:02d}_sit_merra2.txt" for hour in range(0, 25, 3)]
    assert [path.name for path in day_b] == [
        f"ztd_{DATE_B}{hour:02d}_sit_merra2.txt" for hour in range(0, 19, 3)]

    # 当日 24 时 = 次日 00 时的真实数值
    _, last_a = _read_rows(day_a[-1])
    _, first_b = _read_rows(day_b[0])
    assert [row[ZTD_COLUMN] for row in last_a] == [row[ZTD_COLUMN] for row in first_b]

    # 跨天只发生在本任务目录：源目录仍无 24 时临时文件，且字节级未变
    assert _manifest(source_dir) == before
    assert not list(source_dir.rglob("*24_sit_*"))


def test_output_time_labels_and_metadata_recorded():
    source_id, _ = _register_source_task(days={
        DOY_A: (DATE_A, HOURS_6H),
        DOY_B: (DATE_B, HOURS_6H),
    })
    task = timeinterp_service.submit_timeinterp(TimeInterpRequest(
        source_task_id=source_id, target_interval=6))
    run_task(task.id)
    stored = get_task_service().get_task(task.id)
    result = stored.result

    assert stored.params["source_task_id"] == source_id
    assert stored.params["target_interval"] == 6
    assert result["output_time_range"] == ["2025-06-01T00:00:00", "2025-06-02T18:00:00"]
    assert result["output_hours"][0] == "2025-06-01T00:00:00"
    assert result["input_doy_folders"] == [DOY_A, DOY_B]
    assert result["input_hours"] == 8
    assert result["missing_columns"] == []
    # 插值参数固定为原生 8 个，顺序 = 结果文件列顺序
    assert result["parameters"] == ["ZTD", "ZHD", "ZWD", "PWV", "Ps", "Tm",
                                    "vapor_pressure", "WVD"]


def test_download_serves_the_generated_file():
    import io
    import zipfile

    from fastapi.testclient import TestClient

    from main import app

    source_id, _ = _register_source_task()
    client = TestClient(app)
    created = client.post("/api/timeinterp", json={
        "source_task_id": source_id, "target_interval": 1})
    assert created.status_code == 201
    task_id = created.json()["task_id"]

    detail = client.get(f"/api/tasks/{task_id}").json()
    assert detail["status"] == "SUCCESS", detail.get("error")
    response = client.get(f"/api/tasks/{task_id}/download")
    assert response.status_code == 200
    # 结果目录含 19 个时次文件 → 按既有任务下载机制打包成 zip
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = sorted(archive.namelist())
    assert len(names) == 19
    assert names[0].endswith(f"ztd_{DATE_A}00_sit_merra2.txt")
    assert names[-1].endswith(f"ztd_{DATE_A}18_sit_merra2.txt")


def test_dat_input_keeps_dat_output_format():
    """输出扩展名继承自输入（原生行为），Web 不改写、不重命名。"""
    source_id, _ = _register_source_task(ext="dat")
    task = timeinterp_service.submit_timeinterp(TimeInterpRequest(
        source_task_id=source_id, target_interval=1))
    run_task(task.id)

    stored = get_task_service().get_task(task.id)
    assert stored.status == TaskStatus.SUCCESS, stored.error
    assert stored.result["input_format"] == "dat"
    files = sorted((get_settings().results_dir / task.id / "data").rglob("*.dat"))
    assert len(files) == 19
    assert stored.result["file_count"] == 19
    assert not list((get_settings().results_dir / task.id / "data").rglob("*.txt"))


def test_inputs_endpoint_lists_successful_calculation_only():
    from fastapi.testclient import TestClient

    from main import app

    ok_id, _ = _register_source_task()
    _register_source_task(status=TaskStatus.FAILED)
    _register_source_task(status=TaskStatus.PENDING)
    _register_source_task(task_type=TaskType.DOWNLOAD)

    payload = TestClient(app).get("/api/timeinterp/inputs").json()
    assert payload["total"] == 1
    assert payload["target_intervals"] == [1, 2, 3, 4, 6, 8, 12]
    item = payload["items"][0]
    assert item["task_id"] == ok_id
    assert item["interpolatable"] is True
    assert item["missing_columns"] == []
    assert item["parameters"] == ["ZTD", "ZHD", "ZWD", "PWV", "Ps", "Tm",
                                 "vapor_pressure", "WVD"]
    assert item["hour_count"] == 4
    assert item["site_count"] == len(SITES)
    assert item["time_labels"] == [
        "2025-06-01T00:00:00", "2025-06-01T06:00:00",
        "2025-06-01T12:00:00", "2025-06-01T18:00:00",
    ]


def test_inputs_endpoint_explains_why_input_is_not_interpolatable():
    from fastapi.testclient import TestClient

    from main import app

    single_id, _ = _register_source_task(days={DOY_A: (DATE_A, (0,))})
    mat_id, _ = _register_source_task(ext="mat", days={DOY_A: (DATE_A, HOURS_6H)})
    partial_id, _ = _register_partial_source_task()

    items = {item["task_id"]: item
             for item in TestClient(app).get("/api/timeinterp/inputs").json()["items"]}
    assert items[single_id]["interpolatable"] is False
    assert "至少需要 2 个时次" in items[single_id]["reason"]
    assert items[mat_id]["interpolatable"] is False
    assert "TXT / DAT" in items[mat_id]["reason"]
    assert items[partial_id]["interpolatable"] is False
    assert "PWV" in items[partial_id]["reason"]
    assert items[partial_id]["missing_columns"] == ["ZHD", "ZWD", "PWV", "Tm", "WVD"]


# --------------------------------------------------------------------------- #
# 输入校验：创建任务前一律 422，且不创建任务
# --------------------------------------------------------------------------- #
def _submit_expect_invalid(**kwargs) -> str:
    payload = {"source_task_id": kwargs.pop("source_task_id", "missing")}
    payload.update(kwargs)
    with pytest.raises(InvalidRequestError) as excinfo:
        timeinterp_service.submit_timeinterp(TimeInterpRequest(**payload))
    return str(excinfo.value)


def test_rejects_unknown_source_task():
    assert "未找到" in _submit_expect_invalid(source_task_id="0" * 32)


def test_rejects_unfinished_and_failed_source_task():
    pending_id, _ = _register_source_task(status=TaskStatus.PENDING)
    failed_id, _ = _register_source_task(status=TaskStatus.FAILED)
    assert "尚未成功完成" in _submit_expect_invalid(source_task_id=pending_id)
    assert "尚未成功完成" in _submit_expect_invalid(source_task_id=failed_id)


def test_rejects_non_calculation_task():
    download_id, _ = _register_source_task(task_type=TaskType.DOWNLOAD)
    assert "参数计算任务" in _submit_expect_invalid(source_task_id=download_id)


def test_rejects_mat_result_as_input():
    mat_id, _ = _register_source_task(ext="mat", days={DOY_A: (DATE_A, HOURS_6H)})
    assert "TXT / DAT" in _submit_expect_invalid(source_task_id=mat_id)


def test_rejects_single_hour_per_day():
    single_id, _ = _register_source_task(days={DOY_A: (DATE_A, (0,))})
    assert "至少需要 2 个时次" in _submit_expect_invalid(source_task_id=single_id)


def test_rejects_source_missing_parameter_columns():
    """只反演了部分参数的结果不可插值——绝不生成整列 NaN 的结果。"""
    partial_id, _ = _register_partial_source_task()
    message = _submit_expect_invalid(source_task_id=partial_id)
    assert "未包含" in message and "PWV" in message and "ZHD" in message


def test_rejects_partial_parameter_selection():
    """原生输出列宽写死 12 列，因此不接受只插值部分参数。"""
    source_id, _ = _register_source_task()
    message = _submit_expect_invalid(source_task_id=source_id,
                                     parameters=["ZTD", "ZHD"])
    assert "必须包含全部 8 个参数" in message


def test_rejects_empty_parameter_list():
    source_id, _ = _register_source_task()
    assert "请选择需要插值的参数" in _submit_expect_invalid(
        source_task_id=source_id, parameters=[])


def test_accepts_full_parameter_set_in_any_order():
    source_id, _ = _register_source_task()
    task = timeinterp_service.submit_timeinterp(TimeInterpRequest(
        source_task_id=source_id, parameters=["WVD", "ZTD", "Ps", "Tm", "PWV",
                                              "ZWD", "ZHD", "vapor_pressure"]))
    # 无论传入顺序如何，落盘顺序始终是原生变量顺序
    assert get_task_service().get_task(task.id).params["parameters"] == [
        "ZTD", "ZHD", "ZWD", "PWV", "Ps", "Tm", "vapor_pressure", "WVD"]


def test_api_rejects_unknown_parameter_name():
    from fastapi.testclient import TestClient

    from main import app

    source_id, _ = _register_source_task()
    response = TestClient(app).post("/api/timeinterp", json={
        "source_task_id": source_id, "parameters": ["ZTD", "nope"], "target_interval": 1})
    assert response.status_code == 422
    assert isinstance(response.json().get("detail"), (str, list))


@pytest.mark.parametrize("interval", [0, 5, 7, 24, 48, -1])
def test_rejects_unsupported_target_interval(interval):
    source_id, _ = _register_source_task()
    assert "不支持的目标时间间隔" in _submit_expect_invalid(
        source_task_id=source_id, target_interval=interval)


@pytest.mark.parametrize("interval", [1, 2, 3, 4, 6, 8, 12])
def test_accepts_every_supported_target_interval(interval):
    source_id, _ = _register_source_task()
    task = timeinterp_service.submit_timeinterp(TimeInterpRequest(
        source_task_id=source_id, target_interval=interval))
    assert get_task_service().get_task(task.id).params["target_interval"] == interval


def test_rejects_result_file_outside_data_dir():
    """任务记录里指向数据目录之外的文件不得被读取（拒绝任意路径访问）。"""
    settings = get_settings()
    service = get_task_service()
    task = service.create_task(name="越界结果", type=TaskType.CALCULATION,
                              params={"dataset": "merra2"})
    outside = Path(settings.project_root) / "metcom" / "pyproject.toml"
    service.mark_success(task.id, {
        "dataset": "merra2",
        "result_type": "station",
        "files": [str(outside), "../../../etc/passwd"],
    })
    assert "没有可识别的时次文件" in _submit_expect_invalid(source_task_id=task.id)


def test_api_returns_422_without_creating_task():
    from fastapi.testclient import TestClient

    from main import app

    before = len(get_task_service().list_tasks(page=1, page_size=100)[0])
    response = TestClient(app).post("/api/timeinterp", json={
        "source_task_id": "0" * 32, "target_interval": 1})
    assert response.status_code == 422
    assert isinstance(response.json().get("detail"), str)
    assert len(get_task_service().list_tasks(page=1, page_size=100)[0]) == before


def test_engine_failure_marks_task_failed(monkeypatch):
    """运行期异常必须收敛成 FAILED + 可诊断信息，绝不误标 SUCCESS。"""
    import metcom.timeinterp.runner as runner

    source_id, _ = _register_source_task()

    def _boom(cfg):
        raise RuntimeError("simulated interpolate failure")

    monkeypatch.setattr(runner, "run_timeinterp", _boom)
    task = timeinterp_service.submit_timeinterp(TimeInterpRequest(
        source_task_id=source_id, target_interval=1))
    run_task(task.id)

    stored = get_task_service().get_task(task.id)
    assert stored.status == TaskStatus.FAILED
    assert stored.error and "时间插值执行失败" in stored.error
    assert not stored.result


def test_empty_output_is_treated_as_failure(monkeypatch):
    import metcom.timeinterp.runner as runner

    source_id, _ = _register_source_task()
    monkeypatch.setattr(runner, "run_timeinterp", lambda cfg: {"output_files": 0})
    task = timeinterp_service.submit_timeinterp(TimeInterpRequest(
        source_task_id=source_id, target_interval=1))
    run_task(task.id)

    stored = get_task_service().get_task(task.id)
    assert stored.status == TaskStatus.FAILED
    assert "未生成任何结果文件" in (stored.error or "")


def test_output_directory_is_isolated_from_source_task():
    source_id, source_dir = _register_source_task()
    task = timeinterp_service.submit_timeinterp(TimeInterpRequest(
        source_task_id=source_id, target_interval=1))
    run_task(task.id)

    settings = get_settings()
    assert (settings.results_dir / task.id / "data").is_dir()
    assert (settings.results_dir / task.id / timeinterp_service.INPUT_SUBDIR).is_dir()
    # 源任务目录既没有被写入插值结果，也没有残留跨天临时文件
    assert not list(source_dir.rglob("*24_sit_*"))
    assert len(list(source_dir.rglob("ztd_*.txt"))) == 4
