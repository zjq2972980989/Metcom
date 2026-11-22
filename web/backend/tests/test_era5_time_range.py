"""ERA5 hour-range filtering regressions for retrieval and the Web adapter."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import netCDF4
import numpy as np
import pytest

import config
from engine import adapter
from metcom.retrieval import pipeline
from metcom.retrieval.pipeline import RetrievalOptions, run_retrieval
from metcom.retrieval.readers.era5 import iter_hours


def _write_era5_nc(path: Path) -> None:
    lon = np.array([0.0, 0.25])
    lat = np.array([1.0, 0.75])
    lev = np.array([1000.0, 500.0])
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("longitude", lon.size)
        ds.createDimension("latitude", lat.size)
        ds.createDimension("pressure_level", lev.size)

        v = ds.createVariable("longitude", "f8", ("longitude",))
        v[:] = lon
        v = ds.createVariable("latitude", "f8", ("latitude",))
        v[:] = lat
        v = ds.createVariable("pressure_level", "f4", ("pressure_level",))
        v[:] = lev

        for name, base in (("z", 1000.0), ("t", 280.0), ("q", 0.003), ("r", 70.0)):
            arr = np.full((lon.size, lat.size, lev.size), base, dtype=float)
            v = ds.createVariable(
                name, "f4", ("longitude", "latitude", "pressure_level")
            )
            v[:] = arr.astype(np.float32)


def _make_day(root: Path, day: str, hours: tuple[str, ...]) -> Path:
    folder = root / day
    folder.mkdir(parents=True, exist_ok=True)
    for hour in hours:
        _write_era5_nc(folder / f"era5_global_qrzt_{day}{hour}.nc")
    return folder


def _timestamps(entries) -> list[str]:
    return [timestamp for timestamp, _ in entries]


@pytest.fixture
def four_hour_day(tmp_path: Path) -> Path:
    _make_day(tmp_path, "20240101", ("00", "06", "12", "18"))
    return tmp_path


def test_single_hour_request_reads_only_that_hour(four_hour_day: Path) -> None:
    entries = list(iter_hours(
        four_hour_day,
        date(2024, 1, 1),
        date(2024, 1, 1),
        start_time="2024010100",
        end_time="2024010100",
    ))

    assert _timestamps(entries) == ["2024010100"]


def test_hour_interval_is_inclusive(four_hour_day: Path) -> None:
    entries = list(iter_hours(
        four_hour_day,
        date(2024, 1, 1),
        date(2024, 1, 1),
        start_time="2024010106",
        end_time="2024010112",
    ))

    assert _timestamps(entries) == ["2024010106", "2024010112"]


def test_missing_hour_is_skipped_without_expanding_range(tmp_path: Path) -> None:
    _make_day(tmp_path, "20240101", ("06", "12"))

    partial = list(iter_hours(
        tmp_path,
        date(2024, 1, 1),
        date(2024, 1, 1),
        start_time="2024010100",
        end_time="2024010118",
    ))
    assert _timestamps(partial) == ["2024010106", "2024010112"]

    no_match = list(iter_hours(
        tmp_path,
        date(2024, 1, 1),
        date(2024, 1, 1),
        start_time="2024010100",
        end_time="2024010100",
    ))
    assert no_match == []


def test_cross_day_request_filters_both_days(tmp_path: Path) -> None:
    _make_day(tmp_path, "20240101", ("18",))
    _make_day(tmp_path, "20240102", ("00", "06", "12"))

    entries = list(iter_hours(
        tmp_path,
        date(2024, 1, 1),
        date(2024, 1, 2),
        start_time="2024010118",
        end_time="2024010206",
    ))

    assert _timestamps(entries) == ["2024010118", "2024010200", "2024010206"]


def test_date_only_reader_calls_keep_day_behavior(four_hour_day: Path) -> None:
    legacy = list(iter_hours(
        four_hour_day,
        date(2024, 1, 1),
        date(2024, 1, 1),
    ))
    explicit_date = list(iter_hours(
        four_hour_day,
        date(2024, 1, 1),
        date(2024, 1, 1),
        start_time="20240101",
        end_time="20240101",
    ))

    assert _timestamps(legacy) == [
        "2024010100", "2024010106", "2024010112", "2024010118"
    ]
    assert _timestamps(explicit_date) == _timestamps(legacy)


def _fake_process_site(site, temp_data, spec, egm):
    del temp_data, spec, egm
    return {
        "lat": site.lat,
        "lon": site.lon,
        "h": site.height,
        "ZTD": 2.0,
        "ZHD": 1.8,
        "ZWD": 0.2,
        "PWV": 20.0,
        "Ps": 1000.0,
        "Tm": 280.0,
        "eE": 10.0,
        "WVD": 5.0,
    }


def _run_pipeline_with_virtual_era5(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    start_time: str,
    end_time: str,
    output_name: str,
) -> tuple[dict, Path]:
    site_file = tmp_path / "sites.txt"
    site_file.write_text("A000 0.875 0.125 10.0\n", encoding="utf-8")
    output_dir = tmp_path / output_name

    monkeypatch.setattr(pipeline, "load_egm_mat", lambda *args, **kwargs: object())
    monkeypatch.setattr(pipeline, "_process_site", _fake_process_site)

    stats = run_retrieval(RetrievalOptions(
        source_key="era5",
        data_path=str(tmp_path),
        output_path=str(output_dir),
        output_format="txt",
        site_file=str(site_file),
        start_time=start_time,
        end_time=end_time,
        spatial_resolution=(0.25, 0.25),
        egm_file=str(tmp_path / "unused.mat"),
    ))
    return stats, output_dir


def test_pipeline_passes_hour_bounds_to_era5_reader(
    four_hour_day: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stats, output_dir = _run_pipeline_with_virtual_era5(
        four_hour_day,
        monkeypatch,
        start_time="2024010106",
        end_time="2024010112",
        output_name="hour-range",
    )

    files = sorted(path.name for path in output_dir.rglob("ztd_*.txt"))
    assert stats == {"hours": 2, "sites": 1, "outputs": 2}
    assert files == [
        "ztd_2024010106_sit_era5.txt",
        "ztd_2024010112_sit_era5.txt",
    ]


def test_pipeline_date_only_input_keeps_all_day_behavior(
    four_hour_day: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stats, output_dir = _run_pipeline_with_virtual_era5(
        four_hour_day,
        monkeypatch,
        start_time="20240101",
        end_time="20240101",
        output_name="day-range",
    )

    files = sorted(path.name for path in output_dir.rglob("ztd_*.txt"))
    assert stats == {"hours": 4, "sites": 1, "outputs": 4}
    assert files == [
        "ztd_2024010100_sit_era5.txt",
        "ztd_2024010106_sit_era5.txt",
        "ztd_2024010112_sit_era5.txt",
        "ztd_2024010118_sit_era5.txt",
    ]


def test_adapter_fails_clearly_when_hour_range_has_no_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root = tmp_path / "raw"
    (raw_root / "era5" / "20250601").mkdir(parents=True)
    dependent = tmp_path / "dependent"
    (dependent / "egm2008").mkdir(parents=True)
    (dependent / "egm2008" / "geoidheight_EGM2008_0250.mat").write_bytes(b"dummy")
    site_file = tmp_path / "sites.txt"
    site_file.write_text("A000 37.0 115.0 10.0\n", encoding="utf-8")

    monkeypatch.setenv("METCOM_WEB_METCOM_DATA_ROOT", str(raw_root))
    monkeypatch.setenv("METCOM_WEB_METCOM_DEPENDENT_DIR", str(dependent))
    config.get_settings.cache_clear()
    monkeypatch.setattr(pipeline, "load_egm_mat", lambda *args, **kwargs: object())

    with pytest.raises(adapter.AdapterError) as excinfo:
        adapter.run_calculation(
            source_key="era5",
            start_time=datetime(2025, 6, 1, 0),
            end_time=datetime(2025, 6, 1, 0),
            output_dir=tmp_path / "out",
            parameters=["ZTD"],
            output_format="txt",
            site_file=site_file,
        )

    message = str(excinfo.value)
    assert "2025060100..2025060100" in message
    assert "未找到" in message


def test_pipeline_fails_clearly_when_hour_range_has_no_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "20240101").mkdir()
    site_file = tmp_path / "sites.txt"
    site_file.write_text("A000 0.875 0.125 10.0\n", encoding="utf-8")
    monkeypatch.setattr(pipeline, "load_egm_mat", lambda *args, **kwargs: object())

    options = RetrievalOptions(
        source_key="era5",
        data_path=str(tmp_path),
        output_path=str(tmp_path / "out"),
        output_format="txt",
        site_file=str(site_file),
        start_time="2024010100",
        end_time="2024010100",
        spatial_resolution=(0.25, 0.25),
        egm_file=str(tmp_path / "unused.mat"),
    )

    with pytest.raises(FileNotFoundError) as excinfo:
        run_retrieval(options)

    assert "2024010100..2024010100" in str(excinfo.value)
