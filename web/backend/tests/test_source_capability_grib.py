"""阶段一任务 A：CRA40 / CRA V1.5 / CMA-MESO 能力探测（合成目录，不触碰正式数据）。

验证能力探测严格按各数据源**真实文件命名规则**识别，并且只在"至少存在一个
变量文件组完整的时次"时才判为可计算；不完整目录绝不误判为可用。所有落盘都在
pytest 临时目录（conftest 的 METCOM_WEB_DATA_DIR + 本文件的 raw/dependent 覆盖）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import config
from engine import adapter
from main import app

# 数据源 → 变量标签、文件名模板、EGM2008 文件
_SOURCES = {
    "cra40": {
        "tags": ("GPH", "RHU", "SHU", "TEM"),
        "name": "CRA40_{tag}_{ts}_GLB_0P25_HOUR_V1_0_0.nc",
        "egm": "geoidheight_EGM2008_0250.mat",
    },
    "crav15": {
        "tags": ("HGT", "RH", "SPFH", "TMP"),
        "name": "ART_ATM_GLB_0P10_6HOR_ANAL_{ts}_20240101000000_{tag}.nc",
        "egm": "geoidheight_EGM2008_0100.mat",
    },
    "cmameso": {
        "tags": ("GPH", "RHU", "TEM"),
        "name": "GRAPES3KMORIG_{tag}_100_{ts}_CHN_0_0.nc",
        "egm": "geoidheight_EGM2008_0030.mat",
    },
}

KEYS = tuple(_SOURCES)
DAY = "20240101"


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """把引擎数据根 / 依赖目录指向临时目录，并清空能力探测缓存。"""
    raw = tmp_path / "raw"
    dep = tmp_path / "dependent"
    (dep / "egm2008").mkdir(parents=True)
    for spec in _SOURCES.values():
        (dep / "egm2008" / spec["egm"]).write_bytes(b"egm-stub")
    (dep / "sites").mkdir(parents=True)
    (dep / "sites" / "sta_30sits.txt").write_text("A000 35.0 113.0 50.0\n", encoding="utf-8")

    monkeypatch.setenv("METCOM_WEB_METCOM_DATA_ROOT", str(raw))
    monkeypatch.setenv("METCOM_WEB_METCOM_DEPENDENT_DIR", str(dep))
    config.get_settings.cache_clear()
    adapter._capability_cache = None
    yield {"raw": raw, "dep": dep}
    config.get_settings.cache_clear()
    adapter._capability_cache = None


def _day_dir(paths, key):
    folder = paths["raw"] / key / DAY
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _write_group(paths, key, ts, *, omit=()):
    spec = _SOURCES[key]
    folder = _day_dir(paths, key)
    for tag in spec["tags"]:
        if tag in omit:
            continue
        (folder / spec["name"].format(tag=tag, ts=ts)).write_text("stub")


def _cap(key):
    adapter._capability_cache = None
    return adapter.source_capabilities()[key]


# --------------------------------------------------------------- 目录级情形 --


@pytest.mark.parametrize("key", KEYS)
def test_missing_directory_is_not_available(paths, key):
    cap = _cap(key)
    assert cap["calculate_available"] is False
    assert cap["has_complete_data"] is False
    assert "数据目录不存在" in cap["diagnostics"]


@pytest.mark.parametrize("key", KEYS)
def test_empty_directory_is_not_available(paths, key):
    (paths["raw"] / key).mkdir(parents=True)
    cap = _cap(key)
    assert cap["calculate_available"] is False
    assert cap["complete_time_count"] == 0
    assert "目录中没有符合命名规则的数据文件" in cap["diagnostics"]


@pytest.mark.parametrize("key", KEYS)
def test_only_unrelated_files_is_not_available(paths, key):
    folder = _day_dir(paths, key)
    (folder / "README.txt").write_text("x")
    (folder / "random_output.nc").write_text("x")
    cap = _cap(key)
    assert cap["calculate_available"] is False
    assert cap["has_complete_data"] is False
    assert "目录中没有符合命名规则的数据文件" in cap["diagnostics"]


@pytest.mark.parametrize("key", KEYS)
def test_wrong_naming_extension_is_not_available(paths, key):
    """有相关扩展名但文件名不符合数据源规则 → 不算可用。"""
    folder = _day_dir(paths, key)
    (folder / f"{key}_gph_{DAY}00.nc").write_text("x")
    cap = _cap(key)
    assert cap["calculate_available"] is False
    assert "目录中没有符合命名规则的数据文件" in cap["diagnostics"]


# ----------------------------------------------------------- 变量组完整性 --


@pytest.mark.parametrize("key", KEYS)
def test_single_variable_file_is_not_available(paths, key):
    _write_group(paths, key, f"{DAY}00", omit=tuple(_SOURCES[key]["tags"][1:]))
    cap = _cap(key)
    assert cap["calculate_available"] is False
    assert cap["complete_time_count"] == 0
    assert cap["incomplete_time_count"] == 1


@pytest.mark.parametrize("key", KEYS)
def test_one_complete_time_is_available(paths, key):
    _write_group(paths, key, f"{DAY}00")
    cap = _cap(key)
    assert cap["calculate_available"] is True
    assert cap["has_complete_data"] is True
    assert cap["complete_time_count"] == 1
    assert cap["incomplete_time_count"] == 0
    assert cap["available_time_range"] == [f"{DAY}00", f"{DAY}00"]


@pytest.mark.parametrize("key", KEYS)
def test_multiple_complete_times_report_range(paths, key):
    _write_group(paths, key, f"{DAY}00")
    _write_group(paths, key, f"{DAY}06")
    _write_group(paths, key, f"{DAY}12")
    cap = _cap(key)
    assert cap["complete_time_count"] == 3
    assert cap["available_time_range"] == [f"{DAY}00", f"{DAY}12"]


@pytest.mark.parametrize("key", KEYS)
def test_mixed_complete_and_incomplete_reports_diagnostics(paths, key):
    _write_group(paths, key, f"{DAY}00")
    _write_group(paths, key, f"{DAY}06", omit=(_SOURCES[key]["tags"][-1],))
    cap = _cap(key)
    assert cap["calculate_available"] is True       # 至少一个完整时次
    assert cap["complete_time_count"] == 1
    assert cap["incomplete_time_count"] == 1
    assert any(f"{DAY}06" in item for item in cap["diagnostics"])


# --------------------------------------------------------------- 依赖缺失 --


@pytest.mark.parametrize("key", KEYS)
def test_complete_data_without_egm_is_not_available(paths, key):
    _write_group(paths, key, f"{DAY}00")
    (paths["dep"] / "egm2008" / _SOURCES[key]["egm"]).unlink()
    cap = _cap(key)
    assert cap["has_complete_data"] is True
    assert cap["calculate_available"] is False
    assert "EGM2008" in cap["note"]


@pytest.mark.parametrize("key", KEYS)
def test_complete_data_without_site_file_is_not_available(paths, key):
    _write_group(paths, key, f"{DAY}00")
    (paths["dep"] / "sites" / "sta_30sits.txt").unlink()
    cap = _cap(key)
    assert cap["has_complete_data"] is True
    assert cap["calculate_available"] is False
    assert "站点列表文件" in cap["note"]


# -------------------------------------------------------------- 异常 / API --


@pytest.mark.parametrize("key", KEYS)
def test_scan_exception_is_reported_not_available(paths, key, monkeypatch):
    _write_group(paths, key, f"{DAY}00")
    from metcom.retrieval.readers import grib_sources

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(grib_sources, "scan_source_root", _boom)
    cap = _cap(key)
    assert cap["calculate_available"] is False
    assert any("扫描数据目录失败" in item for item in cap["diagnostics"])


def test_era5_capability_semantics_unchanged(paths):
    """ERA5 沿用"有数据文件即可"的既有语义（回归保护）。"""
    folder = paths["raw"] / "era5" / DAY
    folder.mkdir(parents=True)
    (folder / f"era5_global_qrzt_{DAY}00.nc").write_text("x")
    cap = _cap("era5")
    assert cap["calculate_available"] is True
    assert cap["has_complete_data"] is True
    assert cap["diagnostics"] == []


def test_meta_exposes_grib_capability_fields(paths):
    _write_group(paths, "cra40", f"{DAY}00")
    adapter._capability_cache = None
    body = TestClient(app).get("/api/meta").json()
    cra40 = next(item for item in body["sources"] if item["key"] == "cra40")
    assert cra40["has_complete_data"] is True
    assert cra40["complete_time_count"] == 1
    assert cra40["incomplete_time_count"] == 0
    assert cra40["available_time_range"] == [f"{DAY}00", f"{DAY}00"]
    assert cra40["diagnostics"] == []


# ---------------------------------- CRA V1.5 真实命名 / 重复文件回归 --


def test_crav15_real_naming_without_version_segment_is_available(paths):
    """只读验收报告中的真实命名（无 14 位版本段）必须被能力探测识别。"""
    folder = paths["raw"] / "crav15" / DAY
    folder.mkdir(parents=True, exist_ok=True)
    for tag in _SOURCES["crav15"]["tags"]:
        (folder / f"ART_ATM_GLB_0P10_6HOR_ANAL_{DAY}00_{tag}.nc").write_text(
            "stub", encoding="utf-8")
    cap = _cap("crav15")
    assert cap["has_complete_data"] is True
    assert cap["complete_time_count"] == 1
    assert cap["available_time_range"] == [f"{DAY}00", f"{DAY}00"]


def test_duplicate_variable_files_are_not_silently_complete(paths):
    """同一时次同一变量存在多个文件时不得静默择一，应判为不可计算并给出诊断。"""
    folder = paths["raw"] / "crav15" / DAY
    folder.mkdir(parents=True, exist_ok=True)
    for tag in _SOURCES["crav15"]["tags"]:
        (folder / f"ART_ATM_GLB_0P10_6HOR_ANAL_{DAY}00_{tag}.nc").write_text(
            "stub", encoding="utf-8")
        (folder / f"ART_ATM_GLB_0P10_6HOR_ANAL_{DAY}00_{tag}.grib2").write_text(
            "stub", encoding="utf-8")
    cap = _cap("crav15")
    assert cap["has_complete_data"] is False
    assert cap["complete_time_count"] == 0
    assert any("多个文件" in item for item in cap["diagnostics"])
