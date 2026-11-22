"""IO 与文件格式测试（对照 MATLAB 基准输出）。"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from metcom.utils.io import (
    RESULT_HEADERS, get_doy, read_result_file, read_site_file,
    write_result_file,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCH = PROJECT_ROOT / "benchmarks"
ERA5_BENCH = BENCH / "output_ERA5" / "2024001" / "ztd_2024010100_sit_era5.txt"
SITE_FILE = BENCH / "bth_0125_orthometric.txt"


def test_get_doy():
    # 2024 为闰年：6 月 1 日 = 31+29+31+30+31+1 = 153
    assert get_doy("era5_global_qrzt_2024060112.nc") == ("2024153", "2024060112")
    # 2025 非闰年：6 月 1 日 = 152（与基准输出目录 2025152 一致）
    assert get_doy("ztd_2025060100_sit_cmameso.txt") == ("2025152", "2025060100")
    assert get_doy("ztd_2024010100_sit_era5.txt") == ("2024001", "2024010100")
    # 闰年 12-31 → 366
    assert get_doy("x_2024123123.nc") == ("2024366", "2024123123")


def test_read_site_file_benchmark():
    sites = read_site_file(SITE_FILE)
    assert len(sites) == 1443
    assert sites[0].name == "A000"
    assert sites[0].lat == pytest.approx(36.125)
    assert sites[0].lon == pytest.approx(115.0)
    assert sites[0].height == pytest.approx(51.5)


def test_result_file_roundtrip_matches_matlab_bytes():
    """用基准文件自身数值回写，应逐行一致（验证 %4s/%20.8f 格式复刻）。"""
    assert ERA5_BENCH.is_file(), "基准文件缺失"
    original_lines = ERA5_BENCH.read_text(encoding="utf-8").splitlines()

    # 解析基准文件
    columns, data = read_result_file(ERA5_BENCH)
    assert columns[0] == "name"
    assert columns == list(RESULT_HEADERS)

    sites = []
    for line in original_lines[1:]:
        tokens = line.split()
        sites.append(type("S", (), {"name": tokens[0]})())
    values = {
        col: [None if np.isnan(data[i, j]) else float(data[i, j])
              for i in range(data.shape[0])]
        for j, col in enumerate(columns[1:], start=1)
    }

    out = PROJECT_ROOT / "tests" / "_tmp_roundtrip.txt"
    try:
        write_result_file(sites, values, out)
        rewritten = out.read_text(encoding="utf-8").splitlines()
        # 逐行比对（含表头）
        assert rewritten == original_lines
    finally:
        out.unlink(missing_ok=True)


def test_nan_formatting():
    """NaN 应输出右对齐 'NaN'（统一 fmt 模块的既定行为）。"""
    from metcom.utils.fmt import fmt_fixed_width
    assert fmt_fixed_width([float("nan")], [(20, 8)]) == f"{'NaN':>20s}"
    assert fmt_fixed_width([1.5], [(20, 8)]) == f"{1.5:20.8f}"
