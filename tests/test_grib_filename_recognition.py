"""阶段一回归：CRA V1.5 文件命名识别与重复文件处理。

真实样例（无版本段，取自只读验收报告）::

    ART_ATM_GLB_0P10_6HOR_ANAL_2024010100_HGT.nc

历史命名（带 14 位版本段）必须继续兼容::

    ART_ATM_GLB_0P10_6HOR_ANAL_2024010100_20240101000000_HGT.nc

本文件只做**纯文件名/目录扫描**，不读取或解码任何数据；需要落盘的用例只写入
pytest 临时目录，绝不触碰正式数据目录。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from metcom.retrieval.readers.grib_sources import (  # noqa: E402
    _CFG, _group_by_timestamp, scan_files,
)

CRAV_TAGS = ("HGT", "RH", "SPFH", "TMP")


def _crav_names(ts: str, *, version: str | None = None) -> list[str]:
    mid = f"{ts}_{version}_" if version else f"{ts}_"
    return [f"ART_ATM_GLB_0P10_6HOR_ANAL_{mid}{tag}.nc" for tag in CRAV_TAGS]


# ------------------------------------------------- CRA V1.5 命名识别 --


def test_report_real_sample_name_is_recognized() -> None:
    """只读验收报告中的真实文件名必须被识别（原缺陷回归）。"""
    scan = scan_files("crav15", ["ART_ATM_GLB_0P10_6HOR_ANAL_2024010100_HGT.nc"])
    assert scan.matched_files == 1
    assert scan.unrelated_files == 0
    assert scan.groups["2024010100"]["HGT"].name == (
        "ART_ATM_GLB_0P10_6HOR_ANAL_2024010100_HGT.nc"
    )


def test_crav15_without_version_segment_full_group() -> None:
    scan = scan_files("crav15", _crav_names("2024010100"))
    assert scan.matched_files == 4
    assert scan.unrelated_files == 0
    assert scan.complete_times == ("2024010100",)
    assert sorted(scan.groups["2024010100"]) == sorted(CRAV_TAGS)
    assert scan.duplicates == {}


def test_crav15_with_legacy_version_segment_full_group() -> None:
    scan = scan_files("crav15", _crav_names("2024010100", version="20240101000000"))
    assert scan.matched_files == 4
    assert scan.complete_times == ("2024010100",)
    assert sorted(scan.groups["2024010100"]) == sorted(CRAV_TAGS)


@pytest.mark.parametrize("name", [
    "ART_ATM_GLB_0P25_6HOR_ANAL_2024010100_HGT.nc",     # 其它分辨率产品：不放宽
    "ART_ATM_GLB_0P10_6HOR_ANAL_202401010_HGT.nc",      # 时刻 9 位
    "ART_ATM_GLB_0P10_6HOR_ANAL_20240101000_HGT.nc",    # 时刻 11 位
    "ART_ATM_GLB_0P10_6HOR_ANAL_2024010100_XYZ.nc",     # 未知变量标签
    "ART_ATM_GLB_0P10_6HOR_ANAL_2024010100_HGT.csv",    # 未知扩展名
    "ART_ATM_GLB_0P10_6HOR_FCT_2024010100_HGT.nc",      # 非 ANAL 产品
    "ART_ATM_GLB_0P10_6HOR_ANAL_2024010100_HGT",        # 缺扩展名
    "art_atm_glb_0p10_6hor_anal_2024010100_HGT.nc",     # 大小写不符
    "random_file.nc",
])
def test_crav15_unrelated_names_are_strictly_rejected(name: str) -> None:
    scan = scan_files("crav15", [name])
    assert scan.matched_files == 0
    assert scan.unrelated_files == 1
    assert scan.complete_times == ()


# ---------------------------------------------- 重复文件：不得静默覆盖 --


def test_duplicate_time_variable_is_recorded_not_overwritten() -> None:
    files: list[str] = []
    for tag in CRAV_TAGS:
        files.append(f"ART_ATM_GLB_0P10_6HOR_ANAL_2024010100_{tag}.nc")
        files.append(f"ART_ATM_GLB_0P10_6HOR_ANAL_2024010100_{tag}.grib2")
    scan = scan_files("crav15", files)
    assert scan.matched_files == 8
    assert scan.duplicate_times == ("2024010100",)
    assert set(scan.duplicates["2024010100"]) == set(CRAV_TAGS)
    # 歧义时次不计入完整时次（绝不静默择一）
    assert scan.complete_times == ()
    detail = " / ".join(scan.duplicate_detail("2024010100"))
    assert "HGT" in detail and ".nc" in detail and ".grib2" in detail


def test_group_by_timestamp_skips_ambiguous_time_with_warning(tmp_path, capsys) -> None:
    day = tmp_path / "20240101"
    day.mkdir()
    for tag in CRAV_TAGS:
        (day / f"ART_ATM_GLB_0P10_6HOR_ANAL_2024010100_{tag}.nc").write_text(
            "", encoding="utf-8")
    (day / "ART_ATM_GLB_0P10_6HOR_ANAL_2024010100_HGT.grib2").write_text(
        "", encoding="utf-8")

    grouped = _group_by_timestamp(_CFG["crav15"], day)
    assert grouped == {}
    captured = capsys.readouterr().out
    assert "2024010100" in captured
    assert "多个文件" in captured


def test_group_by_timestamp_still_reads_unambiguous_group(tmp_path) -> None:
    day = tmp_path / "20240101"
    day.mkdir()
    for tag in CRAV_TAGS:
        (day / f"ART_ATM_GLB_0P10_6HOR_ANAL_2024010100_{tag}.nc").write_text(
            "", encoding="utf-8")
    grouped = _group_by_timestamp(_CFG["crav15"], day)
    assert sorted(grouped) == ["2024010100"]
    assert sorted(grouped["2024010100"]) == sorted(CRAV_TAGS)


# ----------------------------- CRA40 / CMA-MESO 命名筛选条件不放宽 --


@pytest.mark.parametrize("name,ok", [
    ("CRA40_GPH_2024010100_GLB_0P25_HOUR_V1_0_0.nc", True),
    ("CRA40_SHU_2024010106_GLB_0P25_HOUR_V1_0_0.nc", True),
    ("CRA40_GPH_2024010100_GLB_0P50_HOUR_V1_0_0.nc", False),   # 非 0.25
    ("CRA40_GPH_2024010100_GLB_34KM_HOUR_V1_0_0.nc", False),   # 非 0.25
    ("CRA40_GPH_20240101_GLB_0P25_DAY_V1_0_0.nc", False),      # 日产品
    ("CRA40_GPH_202401_GLB_0P25_MONTH_V1_0_0.nc", False),      # 月产品
])
def test_cra40_product_filter_unchanged(name: str, ok: bool) -> None:
    scan = scan_files("cra40", [name])
    assert scan.matched_files == (1 if ok else 0)


@pytest.mark.parametrize("name,ok", [
    ("GRAPES3KMORIG_GPH_100_2025060100_CHN_0_0.grib2", True),
    ("GRAPES3KMORIG_RHU_100_2025060100_CHN_0_0.nc", True),
    ("GRAPES3KMORIG_GPH_100_2025060100_CHN_0_0.grib2.5b7b6.idx", False),  # 索引文件
    ("GRAPES3KMORIG_SHU_100_2025060100_CHN_0_0.grib2", False),            # 变量不符
])
def test_cmameso_product_filter_unchanged(name: str, ok: bool) -> None:
    scan = scan_files("cmameso", [name])
    assert scan.matched_files == (1 if ok else 0)
