"""阶段一任务 B/C：CRA40 / CRA V1.5 / CMA-MESO 小时级筛选与覆盖完整性。

用合成 NC（命名与变量分组严格遵循各数据源约定）验证**读取路径实际处理的时次**：
单时刻、同日范围、跨日范围、缺失起始/中间/结束时次、不符合采样分辨率的请求、
变量文件组不完整的时次、日期级请求（单日/跨日/跨年）的覆盖完整性。合成文件只
落在 pytest 临时目录，不触碰正式原始数据、EGM2008 或历史结果。

同时给出 MERRA-2 的回归对照：它仍按日读取（小时边界不生效），保持原有语义。
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import netCDF4
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import metcom.retrieval.pipeline as pipeline
from metcom.retrieval.pipeline import RetrievalOptions, run_retrieval
from metcom.retrieval.readers import get_reader

DAY1 = "20240101"
DAY2 = "20240102"

# 数据源 → 变量标签/数据变量名、文件名模板、原生分辨率、采样步长（小时）
_SOURCES = {
    "cra40": {
        "tags": {"GPH": "gh", "RHU": "r", "SHU": "q", "TEM": "t"},
        "name": "CRA40_{tag}_{ts}_GLB_0P25_HOUR_V1_0_0.nc",
        "res": (0.25, 0.25),
        "step": 6,
    },
    "crav15": {
        "tags": {"HGT": "gh", "RH": "r", "SPFH": "q", "TMP": "t"},
        "name": "ART_ATM_GLB_0P10_6HOR_ANAL_{ts}_20240101000000_{tag}.nc",
        "res": (0.10, 0.10),
        "step": 1,
    },
    "cmameso": {
        "tags": {"GPH": "gh", "RHU": "r", "TEM": "t"},
        "name": "GRAPES3KMORIG_{tag}_100_{ts}_CHN_0_0.nc",
        "res": (0.03, 0.03),
        "step": 3,
    },
}

KEYS = tuple(_SOURCES)

LON = np.array([113.0, 113.25])
LAT = np.array([35.0, 35.25])
PLEV = np.array([1000.0, 500.0])


def _write_var(path: Path, var: str) -> None:
    """写单变量 NC（与真实转换产物同风格：变量名 gh/t/r/q + isobaricInhPa，纬度降序）。"""
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


def _write_group(root: Path, key: str, day: str, hour: int, *, omit: tuple[str, ...] = ()) -> None:
    """写一个时次的变量文件组；omit 中的变量故意缺失以构造不完整时次。"""
    spec = _SOURCES[key]
    folder = root / day
    folder.mkdir(parents=True, exist_ok=True)
    ts = f"{day}{hour:02d}"
    for tag, var in spec["tags"].items():
        if tag in omit:
            continue
        _write_var(folder / spec["name"].format(tag=tag, ts=ts), var)


def _fake_process_site(site, temp_data, spec, egm):
    del temp_data, spec, egm
    return {
        "lat": site.lat, "lon": site.lon, "h": site.height,
        "ZTD": 2.0, "ZHD": 1.8, "ZWD": 0.2, "PWV": 20.0,
        "Ps": 1000.0, "Tm": 280.0, "eE": 10.0, "WVD": 5.0,
    }


@pytest.fixture(autouse=True)
def _fast_engine(monkeypatch):
    """替换 EGM 加载与站点反演：本文件只验证时次/覆盖语义，不做真实物理计算。"""
    monkeypatch.setattr(pipeline, "load_egm_mat", lambda *a, **k: object())
    monkeypatch.setattr(pipeline, "_process_site", _fake_process_site)


def _data_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir(exist_ok=True)
    return root


def _site_file(tmp_path: Path) -> Path:
    path = tmp_path / "sites.txt"
    path.write_text("A000 35.125 113.125 50.0\n", encoding="utf-8")
    return path


def _run(tmp_path: Path, key: str, *, start: str, end: str, name: str):
    out = tmp_path / name
    stats = run_retrieval(RetrievalOptions(
        source_key=key,
        data_path=str(_data_root(tmp_path)),
        output_path=str(out),
        output_format="txt",
        site_file=str(_site_file(tmp_path)),
        start_time=start,
        end_time=end,
        spatial_resolution=_SOURCES[key]["res"],
        egm_file=str(tmp_path / "unused.mat"),
    ))
    files = sorted(p.name for p in out.rglob("ztd_*.txt"))
    return stats, files


# ------------------------------------------------------------ 单时刻 / 范围 --


@pytest.mark.parametrize("key", KEYS)
def test_single_hour_reads_only_that_hour(tmp_path, key):
    step = _SOURCES[key]["step"]
    for hour in (0, step, 2 * step):
        _write_group(_data_root(tmp_path), key, DAY1, hour)

    stats, files = _run(tmp_path, key, start=f"{DAY1}{step:02d}",
                        end=f"{DAY1}{step:02d}", name="single")

    assert stats["hours"] == 1
    assert files == [f"ztd_{DAY1}{step:02d}_sit_{key}.txt"]


@pytest.mark.parametrize("key", KEYS)
def test_same_day_range_is_inclusive(tmp_path, key):
    step = _SOURCES[key]["step"]
    _write_group(_data_root(tmp_path), key, DAY1, 0)
    _write_group(_data_root(tmp_path), key, DAY1, step)

    stats, files = _run(tmp_path, key, start=f"{DAY1}00",
                        end=f"{DAY1}{step:02d}", name="range")

    assert stats["hours"] == 2
    assert files == [f"ztd_{DAY1}00_sit_{key}.txt",
                     f"ztd_{DAY1}{step:02d}_sit_{key}.txt"]


@pytest.mark.parametrize("key", KEYS)
def test_cross_day_range_reads_both_days(tmp_path, key):
    last_hour = 24 - _SOURCES[key]["step"]      # 当日最后一个采样时刻
    _write_group(_data_root(tmp_path), key, DAY1, last_hour)
    _write_group(_data_root(tmp_path), key, DAY2, 0)

    stats, files = _run(tmp_path, key, start=f"{DAY1}{last_hour:02d}",
                        end=f"{DAY2}00", name="cross")

    assert stats["hours"] == 2
    assert files == [f"ztd_{DAY1}{last_hour:02d}_sit_{key}.txt",
                     f"ztd_{DAY2}00_sit_{key}.txt"]


# --------------------------------------------------- 缺失 / 不完整 / 非法请求 --


@pytest.mark.parametrize("key", KEYS)
def test_missing_start_hour_fails(tmp_path, key):
    step = _SOURCES[key]["step"]
    _write_group(_data_root(tmp_path), key, DAY1, step)

    with pytest.raises(FileNotFoundError) as excinfo:
        _run(tmp_path, key, start=f"{DAY1}00", end=f"{DAY1}{step:02d}", name="no-start")
    assert f"{DAY1}00" in str(excinfo.value)


@pytest.mark.parametrize("key", ["cra40", "cmameso"])
def test_missing_middle_hour_fails(tmp_path, key):
    step = _SOURCES[key]["step"]
    _write_group(_data_root(tmp_path), key, DAY1, 0)
    _write_group(_data_root(tmp_path), key, DAY1, 2 * step)

    with pytest.raises(FileNotFoundError) as excinfo:
        _run(tmp_path, key, start=f"{DAY1}00", end=f"{DAY1}{2 * step:02d}", name="no-mid")
    assert f"{DAY1}{step:02d}" in str(excinfo.value)


@pytest.mark.parametrize("key", KEYS)
def test_missing_end_hour_fails(tmp_path, key):
    step = _SOURCES[key]["step"]
    _write_group(_data_root(tmp_path), key, DAY1, 0)

    with pytest.raises(FileNotFoundError) as excinfo:
        _run(tmp_path, key, start=f"{DAY1}00", end=f"{DAY1}{step:02d}", name="no-end")
    assert f"{DAY1}{step:02d}" in str(excinfo.value)


@pytest.mark.parametrize("key", ["cra40", "cmameso"])
def test_unaligned_hour_request_fails_without_substitution(tmp_path, key):
    _write_group(_data_root(tmp_path), key, DAY1, 0)

    with pytest.raises(FileNotFoundError):
        _run(tmp_path, key, start=f"{DAY1}01", end=f"{DAY1}01", name="unaligned")


@pytest.mark.parametrize("key", ["cra40", "cmameso"])
def test_unaligned_request_diagnostic_mentions_sampling_rule(tmp_path, key):
    """采样网格之外的请求必须说明采样时刻，不得只归因于文件命名。"""
    step = _SOURCES[key]["step"]
    assert step > 1 and 1 % step != 0        # 1 时必然落在采样网格之外
    _write_group(_data_root(tmp_path), key, DAY1, 0)   # 数据存在（00 时），只是不在请求区间

    with pytest.raises(FileNotFoundError) as excinfo:
        _run(tmp_path, key, start=f"{DAY1}01", end=f"{DAY1}01", name="unaligned-diag")

    message = str(excinfo.value)
    assert f"{DAY1}01" in message
    assert "采样规则" in message


@pytest.mark.parametrize("key", KEYS)
def test_hour_request_does_not_fall_back_to_whole_day(tmp_path, key):
    """请求区间内没有数据时不得退化为处理当天其他时次（原缺陷的回归）。"""
    step = _SOURCES[key]["step"]
    _write_group(_data_root(tmp_path), key, DAY1, 0)
    _write_group(_data_root(tmp_path), key, DAY1, 2 * step)

    with pytest.raises(FileNotFoundError):
        _run(tmp_path, key, start=f"{DAY1}{step:02d}", end=f"{DAY1}{step:02d}", name="strict")


@pytest.mark.parametrize("key", KEYS)
def test_incomplete_group_is_not_silently_successful(tmp_path, key):
    spec = _SOURCES[key]
    missing_tag = sorted(spec["tags"])[-1]
    _write_group(_data_root(tmp_path), key, DAY1, 0, omit=(missing_tag,))

    with pytest.raises(FileNotFoundError) as excinfo:
        _run(tmp_path, key, start=f"{DAY1}00", end=f"{DAY1}00", name="incomplete")
    assert missing_tag in str(excinfo.value)


# ------------------------------------------------------------- 日期级兼容 --


@pytest.mark.parametrize("key", KEYS)
def test_date_only_request_requires_and_keeps_full_day(tmp_path, key):
    """日期级请求按数据源采样规则校验完整性：写满一天采样时刻应全部被处理。"""
    step = _SOURCES[key]["step"]
    hours = list(range(0, 24, step))
    for hour in hours:
        _write_group(_data_root(tmp_path), key, DAY1, hour)

    stats, files = _run(tmp_path, key, start=DAY1, end=DAY1, name="date-only")

    assert stats["hours"] == len(hours)
    assert files == [f"ztd_{DAY1}{hour:02d}_sit_{key}.txt" for hour in hours]


@pytest.mark.parametrize("key", KEYS)
def test_date_only_request_rejects_partial_day(tmp_path, key):
    """日期级请求只写到部分时次（旧行为会静默成功）时必须明确失败。"""
    step = _SOURCES[key]["step"]
    _write_group(_data_root(tmp_path), key, DAY1, 0)
    _write_group(_data_root(tmp_path), key, DAY1, step)

    with pytest.raises(FileNotFoundError):
        _run(tmp_path, key, start=DAY1, end=DAY1, name="date-partial")


@pytest.mark.parametrize("key", KEYS)
def test_date_only_request_rejects_missing_sampled_hour(tmp_path, key):
    """日期级请求缺少一个采样时刻时，错误信息要能指出该缺失时次。"""
    step = _SOURCES[key]["step"]
    hours = list(range(0, 24, step))
    missing_hour = hours[len(hours) // 2]
    for hour in hours:
        if hour != missing_hour:
            _write_group(_data_root(tmp_path), key, DAY1, hour)

    with pytest.raises(FileNotFoundError) as excinfo:
        _run(tmp_path, key, start=DAY1, end=DAY1, name="date-missing")
    assert f"{DAY1}{missing_hour:02d}" in str(excinfo.value)


@pytest.mark.parametrize("key", KEYS)
def test_date_only_request_rejects_missing_variable(tmp_path, key):
    """日期级请求某时次变量组不完整时，错误信息要能指出缺失变量。"""
    step = _SOURCES[key]["step"]
    missing_tag = sorted(_SOURCES[key]["tags"])[-1]
    for hour in range(0, 24, step):
        omit = (missing_tag,) if hour == 0 else ()
        _write_group(_data_root(tmp_path), key, DAY1, hour, omit=omit)

    with pytest.raises(FileNotFoundError) as excinfo:
        _run(tmp_path, key, start=DAY1, end=DAY1, name="date-var")
    assert missing_tag in str(excinfo.value)


@pytest.mark.parametrize("key", KEYS)
def test_date_only_request_without_data_fails(tmp_path, key):
    """日期级请求没有任何可用时次时，不得当作正常成功结果返回。"""
    _data_root(tmp_path)                       # 空数据目录
    with pytest.raises(FileNotFoundError):
        _run(tmp_path, key, start=DAY1, end=DAY1, name="date-empty")


@pytest.mark.parametrize("key", KEYS)
def test_date_only_request_cross_day(tmp_path, key):
    """跨日日期级请求：两天采样时次齐全 → 全部处理。"""
    step = _SOURCES[key]["step"]
    for day in (DAY1, DAY2):
        for hour in range(0, 24, step):
            _write_group(_data_root(tmp_path), key, day, hour)

    stats, _ = _run(tmp_path, key, start=DAY1, end=DAY2, name="date-crossday")
    assert stats["hours"] == 2 * (24 // step)


@pytest.mark.parametrize("key", KEYS)
def test_date_only_request_cross_year(tmp_path, key):
    """跨年日期级请求：两年边界日采样时次齐全 → 全部处理。"""
    step = _SOURCES[key]["step"]
    for day in ("20241231", "20250101"):
        for hour in range(0, 24, step):
            _write_group(_data_root(tmp_path), key, day, hour)

    stats, _ = _run(tmp_path, key, start="20241231", end="20250101",
                    name="date-crossyear")
    assert stats["hours"] == 2 * (24 // step)


@pytest.mark.parametrize("key", KEYS)
def test_date_level_expected_hours_use_sampling_grid(tmp_path, key):
    """日期级覆盖校验只把采样相位时刻当作必需（如 3h 源为 8 个而非 24 个）。"""
    from metcom.sources import SOURCES
    from metcom.retrieval.readers import grib_sources as gs

    step = _SOURCES[key]["step"]
    root = _data_root(tmp_path)
    for hour in range(0, 24, step):
        _write_group(root, key, DAY1, hour)

    folders = gs.resolve_scan_folders(root, date(2024, 1, 1), date(2024, 1, 1))
    result = gs.verify_hour_coverage(
        key, folders, start_time=DAY1, end_time=DAY1,
        time_resolution=SOURCES[key].time_resolution,
    )
    assert result["expected_time_count"] == 24 // step
    assert result["missing_time_count"] == 0
    assert result["incomplete_time_count"] == 0


# ------------------------------------------------------------ Reader 直测 --


@pytest.mark.parametrize("key", KEYS)
def test_reader_iter_hours_respects_bounds(tmp_path, key):
    step = _SOURCES[key]["step"]
    for hour in (0, step, 2 * step):
        _write_group(_data_root(tmp_path), key, DAY1, hour)

    reader = get_reader(key)
    entries = list(reader.iter_hours(
        _data_root(tmp_path), date(2024, 1, 1), date(2024, 1, 1),
        start_time=f"{DAY1}00", end_time=f"{DAY1}{step:02d}",
    ))

    assert [ts for ts, _ in entries] == [f"{DAY1}00", f"{DAY1}{step:02d}"]


# ------------------------------------------------------- MERRA-2 回归对照 --


def _write_merra2_day(path: Path) -> None:
    lon = np.array([112.0, 112.625])
    lat = np.array([35.0, 35.5])
    lev = np.array([1000.0, 500.0])
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("time", 4)
        ds.createDimension("lev", lev.size)
        ds.createDimension("lat", lat.size)
        ds.createDimension("lon", lon.size)
        for name, vals in (("time", np.arange(4)), ("lev", lev),
                           ("lat", lat), ("lon", lon)):
            v = ds.createVariable(name, "f8", (name,))
            v[:] = vals
        for name, base in (("H", 100.0), ("T", 280.0), ("QV", 0.003)):
            arr = np.full((4, lev.size, lat.size, lon.size), base, dtype="f4")
            v = ds.createVariable(name, "f4", ("time", "lev", "lat", "lon"))
            v[:] = arr


def test_merra2_hour_bounds_do_not_change_daily_reading(tmp_path):
    """MERRA-2 仍按日读取：即使请求 10 位小时边界，也处理当日的全部 4 个切片。"""
    root = _data_root(tmp_path)
    year = root / "2024"
    year.mkdir()
    _write_merra2_day(year / "MERRA2_400.inst6_3d_ana_Np.20240101.SUB.nc")

    out = tmp_path / "merra2"
    stats = run_retrieval(RetrievalOptions(
        source_key="merra2",
        data_path=str(root),
        output_path=str(out),
        output_format="txt",
        site_file=str(_site_file(tmp_path)),
        start_time=f"{DAY1}00",
        end_time=f"{DAY1}00",
        spatial_resolution=(0.5, 0.625),
        egm_file=str(tmp_path / "unused.mat"),
    ))

    assert stats["hours"] == 4
    assert sorted(p.name for p in out.rglob("ztd_*.txt")) == [
        f"ztd_{DAY1}00_sit_merra2.txt", f"ztd_{DAY1}06_sit_merra2.txt",
        f"ztd_{DAY1}12_sit_merra2.txt", f"ztd_{DAY1}18_sit_merra2.txt",
    ]
