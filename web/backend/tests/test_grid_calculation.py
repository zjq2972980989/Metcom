"""Grid site_file → 真实 adapter.run_calculation → MetCom Retrieval（Grid Step 3）。

本文件是**真实计算验收**：

- 不使用 mock adapter / mock retrieval / fake result / 复制旧结果；
- 通过现有 Web Adapter（``engine.adapter.run_calculation``）进入
  ``metcom.retrieval.pipeline.run_retrieval``，结果为引擎真实写出；
- 真实 ERA5 反演只执行**一次**（module 级 fixture），其余用例复用该次结果，
  避免重复昂贵计算。

真实 ERA5 与 EGM2008 都是只读引用（``web/data`` 不写入）；输出目录与
Grid site_file 都放在 pytest 临时目录，计算结束即丢弃。

唯一的一处运行时插桩：把一个**透传记录器**临时挂在 ``run_retrieval`` 上，
只记录 ``options.site_file`` 后立刻调用真实函数（不是替身实现），
用于证明 adapter 确实把 Grid site_file 传进了引擎。
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime
from pathlib import Path

import pytest

from config import PROJECT_ROOT
from engine import adapter
from metcom.utils.io import read_site_file
from services import grid_service

# ---------------------------------------------------------------- 真实资源 --

REAL_DATA_ROOT = PROJECT_ROOT / "web" / "data" / "raw"
REAL_DEPENDENT_DIR = PROJECT_ROOT / "web" / "data" / "dependent"
REAL_DEFAULT_SITES = REAL_DEPENDENT_DIR / "sites" / "sta_30sits.txt"
ERA5_NC = REAL_DATA_ROOT / "era5" / "20250601" / "era5_global_qrzt_2025060100.nc"

_ENV_KEYS = ("METCOM_WEB_METCOM_DATA_ROOT", "METCOM_WEB_METCOM_DEPENDENT_DIR")

# ---------------------------------------------------------------- 计算参数 --

DATASET = "era5"
PARAMETERS = ["PWV", "WVD", "Tm", "ZTD", "ZHD", "ZWD"]
START = datetime(2025, 6, 1, 0)
END = datetime(2025, 6, 1, 0)

LON_MIN, LON_MAX = 115.0, 115.5
LAT_MIN, LAT_MAX = 37.0, 37.5
EXPECTED_LONS = (115.0, 115.25, 115.5)
EXPECTED_LATS = (37.0, 37.25, 37.5)

EXPECTED_COLUMNS = ["name", "latitude", "longitude", "height(m)",
                    "PWV(m)", "WVD", "Tm(K)", "ZTD(m)", "ZHD(m)", "ZWD(m)"]

# TXT 结果三者各自按 8 位小数独立舍入，文本层偏差上界 = 3 × 5e-9。
ZTD_ROUNDING_BOUND = 1.5e-8


def _digest(path: Path) -> tuple[str, float]:
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime


def _build_grid_site_file(path: Path) -> dict:
    return grid_service.build_grid_site_file(
        dataset=DATASET, lon_min=LON_MIN, lon_max=LON_MAX,
        lat_min=LAT_MIN, lat_max=LAT_MAX, output_path=path)


def _result_rows(outcome: dict) -> tuple[list[str], list[dict]]:
    """读取引擎真实写出的结果文件，返回 (表头列名, 每行 dict)。"""
    files = [Path(item) for item in outcome["files"]]
    assert len(files) == 1, files
    lines = files[0].read_text(encoding="utf-8").splitlines()
    header = lines[0].split()
    rows = []
    for line in lines[1:]:
        cells = line.split()
        assert len(cells) == len(header)
        row = {}
        for column, cell in zip(header, cells):
            row[column] = cell if column == "name" else float(cell)
        rows.append(row)
    return header, rows


# ------------------------------------------------------- 1~4. site_file 检查 --


def test_grid_site_file_has_expected_9_points(tmp_path):
    site_path = tmp_path / "grid_sites.txt"
    meta = _build_grid_site_file(site_path)

    assert meta["grid"] == {"n_lon": 3, "n_lat": 3, "points": 9}
    assert site_path.is_file()

    lines = site_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 9
    names = [line.split()[0] for line in lines]
    assert names == [f"G{index:06d}" for index in range(1, 10)]


def test_grid_site_file_readable_by_metcom(tmp_path):
    site_path = tmp_path / "grid_sites.txt"
    _build_grid_site_file(site_path)

    sites = read_site_file(site_path)          # 真实引擎站点解析器
    assert len(sites) == 9
    assert [site.name for site in sites] == [f"G{index:06d}" for index in range(1, 10)]
    assert {site.lat for site in sites} == set(EXPECTED_LATS)
    assert {site.lon for site in sites} == set(EXPECTED_LONS)


def test_grid_coordinates_order(tmp_path):
    site_path = tmp_path / "grid_sites.txt"
    _build_grid_site_file(site_path)
    sites = read_site_file(site_path)

    # lat 外层、lon 内层：每 3 个点换一个纬度，纬度内经度递增
    assert [site.lat for site in sites] == [lat for lat in EXPECTED_LATS for _ in EXPECTED_LONS]
    assert [site.lon for site in sites] == [lon for _ in EXPECTED_LATS for lon in EXPECTED_LONS]
    assert (sites[0].lat, sites[0].lon) == (37.0, 115.0)
    assert (sites[-1].lat, sites[-1].lon) == (37.5, 115.5)


def test_grid_heights_are_finite(tmp_path):
    site_path = tmp_path / "grid_sites.txt"
    _build_grid_site_file(site_path)
    heights = [site.height for site in read_site_file(site_path)]

    assert len(heights) == 9
    assert all(height == height for height in heights)          # 非 NaN
    assert all(abs(height) != float("inf") for height in heights)
    assert all(height > 0.0 for height in heights)              # DEM 正交高程为正


# ------------------------------------------- 5~8. 真实 Retrieval（只跑一次）--


@pytest.fixture(scope="module")
def grid_calculation(tmp_path_factory):
    """真实执行一次 Grid → adapter → run_retrieval，供多个用例共享结果。"""
    import config
    import metcom.retrieval.pipeline as pipeline_module

    work = tmp_path_factory.mktemp("grid-calculation")
    site_path = work / "grid_sites.txt"          # 必须落在 output_dir 之外
    meta = _build_grid_site_file(site_path)
    output_dir = work / "out"

    saved_env = {key: os.environ.get(key) for key in _ENV_KEYS}
    os.environ["METCOM_WEB_METCOM_DATA_ROOT"] = str(REAL_DATA_ROOT)
    os.environ["METCOM_WEB_METCOM_DEPENDENT_DIR"] = str(REAL_DEPENDENT_DIR)
    config.get_settings.cache_clear()

    recorded: dict = {}
    real_run_retrieval = pipeline_module.run_retrieval

    def _recording_run_retrieval(options):
        """透传记录器：只记 site_file，计算仍由真实 run_retrieval 完成。"""
        recorded["site_file"] = str(options.site_file)
        recorded["output_path"] = str(options.output_path)
        return real_run_retrieval(options)

    digest_before = _digest(REAL_DEFAULT_SITES)
    pipeline_module.run_retrieval = _recording_run_retrieval
    try:
        outcome = adapter.run_calculation(
            source_key=DATASET,
            start_time=START,
            end_time=END,
            output_dir=output_dir,
            parameters=PARAMETERS,
            site_file=site_path,
        )
    finally:
        pipeline_module.run_retrieval = real_run_retrieval
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        config.get_settings.cache_clear()

    yield {
        "site_file": site_path,
        "meta": meta,
        "outcome": outcome,
        "recorded": recorded,
        "digest_before": digest_before,
    }


def test_grid_calculation_real_era5(grid_calculation):
    outcome = grid_calculation["outcome"]
    header, rows = _result_rows(outcome)

    assert outcome["stats"]["hours"] == 1
    assert outcome["stats"]["sites"] == 9
    assert outcome["stats"]["outputs"] == 1

    files = [Path(item) for item in outcome["files"]]
    assert len(files) == 1 and files[0].is_file() and files[0].stat().st_size > 0
    assert files[0].name == "ztd_2025060100_sit_era5.txt"
    assert files[0].parent.name == "2025152"          # 年积日目录（2025-06-01）

    assert len(rows) == 9
    assert [row["name"] for row in rows] == [f"G{index:06d}" for index in range(1, 10)]
    assert header == EXPECTED_COLUMNS

    # 结果不是 mock：格点高程与 Grid site_file（DEM 最近邻）逐一一致
    sites = {site.name: site for site in read_site_file(grid_calculation["site_file"])}
    for row in rows:
        site = sites[row["name"]]
        assert row["latitude"] == pytest.approx(site.lat)
        assert row["longitude"] == pytest.approx(site.lon)
        assert row["height(m)"] == pytest.approx(site.height, abs=1e-3)


def test_grid_calculation_uses_custom_site_file(grid_calculation):
    site_file = str(grid_calculation["site_file"])

    # adapter 传给引擎的就是 Grid site_file，而不是平台默认站点文件
    assert grid_calculation["recorded"]["site_file"] == site_file
    assert Path(grid_calculation["recorded"]["site_file"]) != REAL_DEFAULT_SITES

    _, rows = _result_rows(grid_calculation["outcome"])
    assert {row["name"] for row in rows} == {f"G{index:06d}" for index in range(1, 10)}

    # 结果里不允许出现默认 30 个站点的名字
    default_names = {site.name for site in read_site_file(REAL_DEFAULT_SITES)}
    assert len(default_names) == 30
    assert not (default_names & {row["name"] for row in rows})

    # 真实计算过程没有改动默认站点文件
    before = grid_calculation["digest_before"]
    assert _digest(REAL_DEFAULT_SITES) == before


def test_grid_calculation_has_six_parameters(grid_calculation):
    header, rows = _result_rows(grid_calculation["outcome"])
    columns = ["PWV(m)", "WVD", "Tm(K)", "ZTD(m)", "ZHD(m)", "ZWD(m)"]
    assert header == EXPECTED_COLUMNS

    for column in columns:
        values = [row[column] for row in rows]
        assert len(values) == 9
        assert all(value == value and abs(value) != float("inf") for value in values)
        assert all(value > 0.0 for value in values)

    # 真实反演：同一时刻不同格点的 PWV / WVD 必须不同（排除常量假结果）
    assert len({row["PWV(m)"] for row in rows}) > 1
    assert len({row["WVD"] for row in rows}) > 1


def test_grid_ztd_identity(grid_calculation):
    """引擎 ZTD(n) = ZHD + ZWD（site_retrieval.py: n_hell = zwd_hell + zhd_hell）。

    TXT 三者各自按 8 位小数独立舍入，文本层偏差上界 ZTD_ROUNDING_BOUND。
    在 7 位小数（低于格式舍入噪声）下，9 个格点必须严格满足 < 1e-8。
    """
    _, rows = _result_rows(grid_calculation["outcome"])
    assert len(rows) == 9

    raw = [abs(row["ZTD(m)"] - row["ZHD(m)"] - row["ZWD(m)"]) for row in rows]
    assert max(raw) < ZTD_ROUNDING_BOUND

    # 去掉 8 位小数舍入噪声（取到 7 位）后偏差必须为 0，即严格满足 < 1e-8
    for row in rows:
        deviation = round(row["ZTD(m)"] - row["ZHD(m)"] - row["ZWD(m)"], 7)
        assert abs(deviation) < 1e-8
        assert f"{row['ZTD(m)']:.7f}" == f"{row['ZHD(m)'] + row['ZWD(m)']:.7f}"
