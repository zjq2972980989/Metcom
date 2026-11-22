"""反演结果完整性（Ps / vapor_pressure / txt-dat-mat 输出格式）的离线测试。

不联网、不下载、不做大规模计算：用真实的小体量 ERA5 NC + 真实的引擎读写器
（``metcom.utils.io``）跑通 ``adapter.run_calculation`` → ``run_retrieval`` 全链路，
只把物理内核 ``pipeline._process_site`` 与 EGM 加载替换为固定值替身，
因此被测的是**输出与裁剪行为**，不是物理公式。

覆盖：
  参数  —— 6 个原有参数、Ps、vapor_pressure 可单独输出；数值与引擎一致；
           未选参数不混入；空 / 非法 / 重复参数的既有 API 规范。
  格式  —— txt / dat / mat 真实生成，扩展名、内容、字段名与数值一致；
           格式异常时任务 FAILED 而不是假成功。
  兼容  —— 默认 txt 流程、自定义测站、旧请求不带 sites、下载响应 MIME 不回归。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import netCDF4
import numpy as np
import pytest
from scipy.io import loadmat

import config
from engine import adapter
from metcom.retrieval import pipeline
from models.schemas import CalculationRequest, TaskStatus
from services import calculation_service, validation
from services.errors import InvalidRequestError
from services.task_service import get_task_service
from tasks.runner import run_task

START = datetime(2025, 6, 1, 0)
END = datetime(2025, 6, 1, 0)

# 引擎结果文件的完整 12 列（顺序与 metcom/utils/io.py RESULT_HEADERS 一致）
FULL_COLUMNS = [
    "name", "latitude", "longitude", "height(m)",
    "ZTD(m)", "ZHD(m)", "ZWD(m)", "PWV(m)",
    "Ps(hpa)", "Tm(K)", "vapor_pressure(hpa)", "WVD",
]
BASE_COLUMNS = ["name", "latitude", "longitude", "height(m)"]
SIX_PARAMETERS = ["PWV", "WVD", "Tm", "ZTD", "ZHD", "ZWD"]
SIX_PARAM_COLUMNS = BASE_COLUMNS + ["PWV(m)", "WVD", "Tm(K)", "ZTD(m)", "ZHD(m)", "ZWD(m)"]

# 内核替身返回值（键与 metcom/utils/io.py _RESULT_KEY_TO_COLUMN 的引擎键一致）
FAKE_RESULT = {
    "ZTD": 2.41546916, "ZHD": 2.35002730, "ZWD": 0.06544186,
    "PWV": 0.00969804, "Ps": 1029.46805713, "Tm": 266.51246143,
    "eE": 5.38274166, "WVD": 4.28738742,
}


# ------------------------------------------------------------------ 夹具 --


def _write_era5_nc(path: Path) -> None:
    lon = np.array([0.0, 0.25])
    lat = np.array([1.0, 0.75])
    lev = np.array([1000.0, 500.0])
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("longitude", lon.size)
        ds.createDimension("latitude", lat.size)
        ds.createDimension("pressure_level", lev.size)
        for name, values in (("longitude", lon), ("latitude", lat)):
            var = ds.createVariable(name, "f8", (name,))
            var[:] = values
        var = ds.createVariable("pressure_level", "f4", ("pressure_level",))
        var[:] = lev
        for name, base in (("z", 1000.0), ("t", 280.0), ("q", 0.003), ("r", 70.0)):
            arr = np.full((lon.size, lat.size, lev.size), base, dtype=float)
            var = ds.createVariable(
                name, "f4", ("longitude", "latitude", "pressure_level")
            )
            var[:] = arr.astype(np.float32)


def _fake_process_site(site, temp_data, spec, egm):
    del temp_data, spec, egm
    return {"lat": site.lat, "lon": site.lon, "h": site.height, **FAKE_RESULT}


@pytest.fixture
def engine_env(tmp_path, monkeypatch):
    """真实小体量 ERA5 NC + 真实引擎读写器；只替换物理内核与 EGM 加载。"""
    raw_root = tmp_path / "raw"
    day = raw_root / "era5" / "20250601"
    day.mkdir(parents=True)
    _write_era5_nc(day / "era5_global_qrzt_2025060100.nc")

    dep_root = tmp_path / "dependent"
    (dep_root / "egm2008").mkdir(parents=True)
    (dep_root / "egm2008" / "geoidheight_EGM2008_0250.mat").write_bytes(b"stub")

    site_file = tmp_path / "sites.txt"
    site_file.write_text("A000 0.875 0.125 10.0\n", encoding="utf-8")

    monkeypatch.setenv("METCOM_WEB_METCOM_DATA_ROOT", str(raw_root))
    monkeypatch.setenv("METCOM_WEB_METCOM_DEPENDENT_DIR", str(dep_root))
    monkeypatch.setenv("METCOM_WEB_METCOM_SITE_FILE", str(site_file))
    config.get_settings.cache_clear()
    monkeypatch.setattr(pipeline, "load_egm_mat", lambda *a, **k: object())
    monkeypatch.setattr(pipeline, "_process_site", _fake_process_site)
    yield {"raw": raw_root, "dep": dep_root, "site_file": site_file, "tmp": tmp_path}
    config.get_settings.cache_clear()


# ------------------------------------------------------------------ 工具 --


def _read_table(path: Path) -> tuple[list[str], list[list[str]]]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return lines[0].split(), [line.split() for line in lines[1:]]


def _run_adapter(env, *, parameters, output_format=None, out_name="out"):
    return adapter.run_calculation(
        source_key="era5",
        start_time=START,
        end_time=END,
        output_dir=env["tmp"] / out_name,
        parameters=parameters,
        output_format=output_format,
        site_file=env["site_file"],
    )


def _only_file(outcome) -> Path:
    files = [Path(item) for item in outcome["files"]]
    assert len(files) == 1
    return files[0]


def _submit(parameters, *, sites=None, output_format=None):
    return calculation_service.submit_calculation(
        CalculationRequest(
            dataset="era5",
            parameters=parameters,
            start_time=START,
            end_time=END,
            sites=sites,
            output_format=output_format,
        )
    )


def _no_write_run_retrieval(options):
    del options
    return {"hours": 0, "sites": 1, "outputs": 0}


# ------------------------------------------------------- A. 参数完整性 --


def test_six_existing_parameters_still_work(engine_env):
    outcome = _run_adapter(engine_env, parameters=SIX_PARAMETERS)
    columns, rows = _read_table(_only_file(outcome))

    assert columns == SIX_PARAM_COLUMNS
    assert outcome["columns"] == SIX_PARAM_COLUMNS
    assert len(rows) == 1
    assert rows[0][0] == "A000"


def test_ps_can_be_selected_alone(engine_env):
    outcome = _run_adapter(engine_env, parameters=["Ps"])
    columns, rows = _read_table(_only_file(outcome))

    assert columns == BASE_COLUMNS + ["Ps(hpa)"]
    assert float(rows[0][-1]) == pytest.approx(FAKE_RESULT["Ps"])


def test_vapor_pressure_can_be_selected_alone(engine_env):
    outcome = _run_adapter(engine_env, parameters=["vapor_pressure"])
    columns, rows = _read_table(_only_file(outcome))

    assert columns == BASE_COLUMNS + ["vapor_pressure(hpa)"]
    assert float(rows[0][-1]) == pytest.approx(FAKE_RESULT["eE"])


def test_all_eight_parameters_can_be_selected(engine_env):
    outcome = _run_adapter(
        engine_env, parameters=SIX_PARAMETERS + ["Ps", "vapor_pressure"]
    )
    columns, _ = _read_table(_only_file(outcome))

    assert set(columns) == set(FULL_COLUMNS)
    assert set(outcome["columns"]) == set(FULL_COLUMNS)


def test_new_parameters_match_engine_output_values(engine_env):
    """新增两列必须与引擎自己写出的完整结果逐位一致（Web 侧不重算）。"""
    baseline_dir = engine_env["tmp"] / "baseline"
    pipeline.run_retrieval(pipeline.RetrievalOptions(
        source_key="era5",
        data_path=str(engine_env["raw"] / "era5"),
        output_path=str(baseline_dir),
        output_format="txt",
        site_file=str(engine_env["site_file"]),
        start_time="2025060100",
        end_time="2025060100",
        spatial_resolution=(0.25, 0.25),
        egm_file=str(engine_env["tmp"] / "unused.mat"),
    ))
    baseline_file = next(baseline_dir.rglob("ztd_*.txt"))
    base_columns, base_rows = _read_table(baseline_file)
    assert base_columns == FULL_COLUMNS

    outcome = _run_adapter(
        engine_env, parameters=["Ps", "vapor_pressure"], out_name="projected"
    )
    columns, rows = _read_table(_only_file(outcome))

    for column in ("Ps(hpa)", "vapor_pressure(hpa)"):
        assert rows[0][columns.index(column)] == base_rows[0][base_columns.index(column)]


def test_unselected_parameters_are_not_written(engine_env):
    outcome = _run_adapter(engine_env, parameters=["ZTD", "Ps"])
    columns, _ = _read_table(_only_file(outcome))

    assert columns == BASE_COLUMNS + ["ZTD(m)", "Ps(hpa)"]
    for column in ("PWV(m)", "WVD", "Tm(K)", "ZHD(m)", "ZWD(m)", "vapor_pressure(hpa)"):
        assert column not in columns


def test_duplicate_parameters_are_deduplicated():
    assert validation.validate_parameters(["PWV", "PWV", "ZTD"]) == ["PWV", "ZTD"]
    assert validation.validate_parameters(["Ps", "Ps"]) == ["Ps"]


def test_unknown_parameter_rejected():
    with pytest.raises(InvalidRequestError):
        validation.validate_parameters(["PWX"])


def test_empty_parameter_list_rejected():
    with pytest.raises(InvalidRequestError):
        validation.validate_parameters([])


# --------------------------------------------------------- B. 输出格式 --


@pytest.mark.parametrize(
    "fmt,suffix", [("txt", ".txt"), ("dat", ".dat"), ("mat", ".mat")]
)
def test_each_format_produces_a_real_file(engine_env, fmt, suffix):
    outcome = _run_adapter(engine_env, parameters=["ZTD", "Ps"], output_format=fmt)
    path = _only_file(outcome)

    assert path.suffix == suffix
    assert path.stat().st_size > 0
    assert path.name == "ztd_2025060100_sit_era5" + suffix
    assert outcome["columns"] == BASE_COLUMNS + ["ZTD(m)", "Ps(hpa)"]


def test_dat_is_fixed_width_text_like_txt(engine_env):
    """DAT 是引擎原生的同一套定宽文本写入器，不是把 txt 改名而来。"""
    txt = _only_file(_run_adapter(
        engine_env, parameters=["ZTD", "Ps"], output_format="txt", out_name="f_txt"))
    dat = _only_file(_run_adapter(
        engine_env, parameters=["ZTD", "Ps"], output_format="dat", out_name="f_dat"))

    assert txt.suffix == ".txt" and dat.suffix == ".dat"
    assert _read_table(txt) == _read_table(dat)


def test_mat_is_a_real_struct_not_renamed_text(engine_env):
    path = _only_file(_run_adapter(
        engine_env, parameters=["ZTD", "Ps", "vapor_pressure"], output_format="mat"))

    assert path.read_bytes()[:6] == b"MATLAB"
    payload = loadmat(str(path))
    names = [key for key in payload if not key.startswith("__")]
    assert names == ["Result_era5"]
    assert payload["Result_era5"].dtype.names == (
        "name", "latitude", "longitude", "height_m",
        "ZTD_m", "Ps_hpa", "vapor_pressure_hpa",
    )


def test_mat_values_match_text_output(engine_env):
    text = _only_file(_run_adapter(
        engine_env, parameters=["ZTD", "Ps", "vapor_pressure"],
        output_format="txt", out_name="v_txt"))
    columns, rows = _read_table(text)

    mat = _only_file(_run_adapter(
        engine_env, parameters=["ZTD", "Ps", "vapor_pressure"],
        output_format="mat", out_name="v_mat"))
    record = loadmat(str(mat))["Result_era5"][0, 0]

    def value(field):
        # loadmat 会把结构体字段读成嵌套数组，统一取标量再比较
        return np.asarray(record[field]).ravel()[0]

    assert str(value("name")) == rows[0][columns.index("name")]
    for column, field in (("ZTD(m)", "ZTD_m"), ("Ps(hpa)", "Ps_hpa"),
                          ("vapor_pressure(hpa)", "vapor_pressure_hpa")):
        assert float(value(field)) == pytest.approx(
            float(rows[0][columns.index(column)])), column


def test_mat_field_mapping_covers_every_engine_column():
    """MAT 字段映射必须覆盖引擎结果文件的全部列，防止两侧格式契约漂移。"""
    mapping = adapter._mat_column_to_field()

    assert set(FULL_COLUMNS) <= set(mapping)
    for key in adapter.PARAM_COLUMNS:
        assert adapter.PARAM_COLUMNS[key] in mapping, key


@pytest.mark.parametrize("fmt", ["txt", "dat", "mat"])
def test_selection_is_consistent_across_formats(engine_env, fmt):
    """同一参数组合下，三种格式承载的列集合完全一致。"""
    path = _only_file(_run_adapter(
        engine_env, parameters=["vapor_pressure", "Ps"],
        output_format=fmt, out_name="c_" + fmt))

    if fmt == "mat":
        names = loadmat(str(path))["Result_era5"].dtype.names
        assert set(names) == {"name", "latitude", "longitude", "height_m",
                              "vapor_pressure_hpa", "Ps_hpa"}
    else:
        columns, _ = _read_table(path)
        assert columns == ["name", "latitude", "longitude", "height(m)",
                           "vapor_pressure(hpa)", "Ps(hpa)"]


def test_unsupported_format_is_rejected_before_task(engine_env):
    with pytest.raises(InvalidRequestError):
        _submit(["ZTD"], output_format="csv")

    items, total = get_task_service().list_tasks()
    assert total == 0


def test_default_format_is_txt_and_recorded(engine_env):
    task = _submit(SIX_PARAMETERS)
    assert task.params["output_format"] == "txt"
    run_task(task.id)

    finished = get_task_service().get_task(task.id)
    assert finished.status == TaskStatus.SUCCESS
    assert finished.result["output_format"] == "txt"
    assert finished.result["file_name"].endswith(".txt")


def test_format_failure_marks_task_failed(engine_env, monkeypatch):
    """引擎写不出合法 .mat 时任务必须 FAILED，且不得留下成功记录。"""
    def broken_run_retrieval(options):
        out_dir = Path(options.output_path) / "2025152"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / ("ztd_" + options.start_time + "_sit_era5.mat")).write_bytes(b"not-a-mat")
        return {"hours": 1, "sites": 1, "outputs": 1}

    monkeypatch.setattr(pipeline, "run_retrieval", broken_run_retrieval)

    task = _submit(["ZTD"], output_format="mat")
    run_task(task.id)

    finished = get_task_service().get_task(task.id)
    assert finished.status == TaskStatus.FAILED
    assert finished.error
    assert not (finished.result or {}).get("file_path")


# ------------------------------------------------------------ C. 兼容性 --


def test_legacy_request_without_format_keeps_txt(engine_env):
    task = calculation_service.submit_calculation(
        CalculationRequest(
            dataset="era5", parameters=SIX_PARAMETERS,
            start_time=START, end_time=END,
        )
    )
    run_task(task.id)

    finished = get_task_service().get_task(task.id)
    assert finished.status == TaskStatus.SUCCESS
    assert finished.result["columns"] == SIX_PARAM_COLUMNS
    assert "sites_file" not in finished.result


def test_custom_sites_with_new_parameters(engine_env):
    task = _submit(
        ["Ps", "vapor_pressure"],
        sites=[{"name": "MY01", "lat": 0.875, "lon": 0.125, "height": 10.0}],
        output_format="dat",
    )
    run_task(task.id)

    finished = get_task_service().get_task(task.id)
    assert finished.status == TaskStatus.SUCCESS
    assert finished.result["sites_count"] == 1
    assert finished.result["output_format"] == "dat"
    assert finished.result["columns"] == BASE_COLUMNS + [
        "Ps(hpa)", "vapor_pressure(hpa)"]

    produced = Path(config.get_settings().data_dir) / finished.result["files"][0]
    columns, rows = _read_table(produced)
    assert produced.suffix == ".dat"
    assert columns == BASE_COLUMNS + ["Ps(hpa)", "vapor_pressure(hpa)"]
    assert rows[0][0] == "MY01"


def test_api_accepts_new_parameters_and_format(engine_env, monkeypatch):
    from fastapi.testclient import TestClient

    from main import app

    monkeypatch.setattr(pipeline, "run_retrieval", _no_write_run_retrieval)
    client = TestClient(app)
    response = client.post("/api/calculation", json={
        "dataset": "era5",
        "parameters": ["PWV", "Ps", "vapor_pressure"],
        "start_time": "2025-06-01T00:00:00",
        "end_time": "2025-06-01T00:00:00",
        "output_format": "mat",
    })

    assert response.status_code == 201
    task_id = response.json()["task_id"]
    assert get_task_service().get_task(task_id).params["output_format"] == "mat"


def test_api_rejects_unknown_format(engine_env):
    from fastapi.testclient import TestClient

    from main import app

    client = TestClient(app)
    response = client.post("/api/calculation", json={
        "dataset": "era5",
        "parameters": ["ZTD"],
        "start_time": "2025-06-01T00:00:00",
        "end_time": "2025-06-01T00:00:00",
        "output_format": "exe",
    })

    assert response.status_code == 422
    assert isinstance(response.json().get("detail"), str)


def test_download_media_type_matches_format(engine_env):
    from fastapi.testclient import TestClient

    from main import app

    client = TestClient(app)
    expected = {
        "txt": "text/plain; charset=utf-8",
        "dat": "text/plain; charset=utf-8",
        "mat": "application/x-matlab-data",
    }
    for fmt, content_type in expected.items():
        task = _submit(["ZTD", "Ps"], output_format=fmt)
        run_task(task.id)
        response = client.get("/api/tasks/" + task.id + "/download")

        assert response.status_code == 200, fmt
        assert response.headers["content-type"] == content_type, fmt
        assert "." + fmt in response.headers.get("content-disposition", ""), fmt
