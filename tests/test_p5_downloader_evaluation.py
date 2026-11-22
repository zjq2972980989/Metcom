"""P5 测试：下载器工具函数、评估（A vs A 自比）与绘图（真实基准数据）。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from metcom.downloader.runner import (generate_time_list, get_filepath,
                                      parse_cdsapirc, parse_date)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCH = PROJECT_ROOT / "benchmarks"
RESULTS = BENCH / "Results"
OUT_RESULTS = RESULTS / "output_results"

# ---------------------------------------------------------------- 下载器 --
def test_parse_date_and_time_list():
    start = parse_date("2024010300")
    end = parse_date("2024010312")
    times = generate_time_list(start, end)
    assert len(times) == 13
    assert times[0] == start and times[-1] == end
    with pytest.raises(ValueError):
        parse_date("2024010")  # 位数不足


def test_parse_cdsapirc_template():
    tpl = (PROJECT_ROOT.parent / "METCOM_ver3.1" / "02_Downloader"
           / "Downloader_ERA5" / "CDSAPI" / ".cdsapirc_test")
    if not tpl.is_file():
        pytest.skip("原软件包密钥模板不在本机")
    cfg = parse_cdsapirc(tpl)
    assert "url" in cfg and "key" in cfg
    assert cfg["url"].startswith("https://")


def test_get_filepath():
    dt = parse_date("2024010312")
    p = get_filepath(dt, "out", "nc")
    assert p.replace("\\", "/") == "out/20240103/era5_global_qrzt_2024010312.nc"
    assert get_filepath(dt, "out", "grib").endswith(".grib")


# ---------------------------------------------------------------- 评估 --
def test_evaluation_self_comparison(tmp_path):
    """A = B = ERA5 输出自比：RMSE 应为 0，管线与输出格式正确。"""
    from metcom.evaluation.compare import analyze_stations, analyze_timeseries

    era5_dir = str(BENCH / "output_ERA5")
    base = {
        "dir_a": era5_dir,
        "dir_b": era5_dir,
        "name_a": "ERA5_MET",
        "name_b": "ERA5_SELF",
        "time_input": "2024010100,2024010223",
        "eval_vars": ["ZTD(m)", "PWV(m)"],
        "output_dir": str(tmp_path / "eval"),
    }
    ts = analyze_timeseries(base)
    assert len(ts) == 48  # 两天逐小时
    rmse_col = [c for c in ts.columns if "ZTD_RMSE" in c][0]
    assert (ts[rmse_col].abs() < 1e-9).all(), "自比 RMSE 应为 0"

    bs = analyze_stations(base)
    ztd_rmse = [c for c in bs.columns if "ZTD_RMSE" in c][0]
    assert (bs[ztd_rmse].abs() < 1e-9).all()
    # 输出文件存在且列结构与 MATLAB 基准一致
    csv_files = list((tmp_path / "eval").glob("*.csv"))
    assert len(csv_files) == 2
    bench_csv = OUT_RESULTS / "ERA5_MET_vs_ERA5_GMET_ByStation_2024010100_2024010223.csv"
    if bench_csv.is_file():
        import pandas as pd
        cols_bench = list(pd.read_csv(bench_csv, nrows=1).columns)
        cols_got = list(pd.read_csv(
            tmp_path / "eval" /
            "ERA5_MET_vs_ERA5_SELF_ByStation_2024010100_2024010223.csv",
            nrows=1).columns)
        assert cols_got == cols_bench, (cols_got, cols_bench)


# ---------------------------------------------------------------- 绘图 --
def test_plot_sequence_from_benchmark(tmp_path):
    """用 MATLAB 生成的真实 Timeseries txt 绘图。"""
    pytest.importorskip("matplotlib")
    txt = OUT_RESULTS / "MERRA2_vs_ERA5_GMET_Timeseries_2024010100_2024010223.txt"
    if not txt.is_file():
        pytest.skip("基准评估 txt 不在本机")
    from metcom.evaluation.plots import run_plot
    result = run_plot({
        "plot_type": "sequence",
        "txt_file": str(txt),
        "output_dir": str(tmp_path / "plots"),
        "name_a": "MERRA2",
        "name_b": "ERA5_GMET",
        "region": "BTH",
        "plot_vars": ["ZTD_RMSE(mm)", "PWV_RMSE(mm)"],
    })
    assert len(result["files"]) == 1
    assert Path(result["files"][0]).stat().st_size > 10000


def test_plot_heatmap_rmse_from_benchmark(tmp_path):
    """用真实 ByStation txt 绘制 RMSE 热力图（需 cartopy/geopandas）。"""
    pytest.importorskip("cartopy")
    pytest.importorskip("geopandas")
    txt = OUT_RESULTS / "MERRA2_vs_ERA5_GMET_ByStation_2024010100_2024010223.txt"
    shp = (PROJECT_ROOT.parent / "METCOM_ver3.1" / "05_AnaEvaluationPlot"
           / "ChinaAdminDivisonSHP" / "District" / "district.shp")
    if not txt.is_file() or not shp.is_file():
        pytest.skip("基准数据或 shapefile 不在本机")
    from metcom.evaluation.plots import run_plot
    result = run_plot({
        "plot_type": "heatmap_rmse",
        "txt_file": str(txt),
        "output_dir": str(tmp_path / "plots"),
        "region": "BTH",
        "china_shp": str(shp),
        "grid_resolution": 80,
        "plot_vars": ["ZTD_RMSE(mm)"],
    })
    assert len(result["files"]) == 1
    assert Path(result["files"][0]).stat().st_size > 10000


def test_plot_heatmap_value_from_benchmark(tmp_path):
    """用 03 反演结果单时刻文件绘制数值热力图。"""
    pytest.importorskip("cartopy")
    pytest.importorskip("geopandas")
    txt = BENCH / "output_ERA5" / "2024001" / "ztd_2024010100_sit_era5.txt"
    shp = (PROJECT_ROOT.parent / "METCOM_ver3.1" / "05_AnaEvaluationPlot"
           / "ChinaAdminDivisonSHP" / "District" / "district.shp")
    if not txt.is_file() or not shp.is_file():
        pytest.skip("基准数据或 shapefile 不在本机")
    from metcom.evaluation.plots import run_plot
    result = run_plot({
        "plot_type": "heatmap_value",
        "txt_file": str(txt),
        "output_dir": str(tmp_path / "plots"),
        "region": "BTH",
        "china_shp": str(shp),
        "grid_resolution": 80,
        "plot_vars": ["ZTD(m)", "PWV(m)"],
        "var_convert": {"ZTD(m)": 1000.0, "PWV(m)": 1000.0},
    })
    assert len(result["files"]) == 2
