"""阶段二回归：CRA40/CRA V1.5 年/变量布局与 CMA-MESO 日目录布局的扫描兼容。

只做**纯文件名/目录扫描**（stub 文件），不读取或解码任何数据；所有落盘都在
pytest 临时目录。Reader 的时次迭代通过替换 ``read_time_group`` 避免解码。
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from metcom.retrieval.readers import grib_sources as gs  # noqa: E402

# 变量子目录名 → 变量标签（与真实目录布局一致）
CRA40_TAGS = {"gph": "GPH", "rhu": "RHU", "shu": "SHU", "tem": "TEM"}


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def _cra40_year_layout(root: Path, year: int, ts_list) -> None:
    """年/变量布局：<root>/<yyyy>/<var>/CRA40_<TAG>_<ts>_GLB_0P25_HOUR_V1_0_0.nc"""
    for var, tag in CRA40_TAGS.items():
        for ts in ts_list:
            _touch(root / str(year) / var
                   / f"CRA40_{tag}_{ts}_GLB_0P25_HOUR_V1_0_0.nc")


def _cra40_day_layout(root: Path, day: str, hours) -> None:
    """日目录布局：<root>/<yyyyMMdd>/CRA40_<TAG>_<yyyyMMddHH>_...nc"""
    for _, tag in CRA40_TAGS.items():
        for hh in hours:
            _touch(root / day / f"CRA40_{tag}_{day}{hh:02d}_GLB_0P25_HOUR_V1_0_0.nc")


def _cmameso_day_layout(root: Path, day: str, hours) -> None:
    for tag in ("GPH", "RHU", "TEM"):
        for hh in hours:
            _touch(root / day / f"GRAPES3KMORIG_{tag}_100_{day}{hh:02d}_CHN_0_0.grib2")


@pytest.fixture
def fake_read(monkeypatch):
    """替换 read_time_group，避免解码真实数据（本文件只验证扫描/过滤语义）。"""
    def _fake(cfg, file_map, levels=None):
        return {"key": cfg.key, "tags": sorted(file_map)}

    monkeypatch.setattr(gs, "read_time_group", _fake)
    return _fake


# ------------------------------------------------ resolve_scan_folders --


def test_year_layout_folders_resolved(tmp_path):
    root = tmp_path / "cra40"
    _cra40_year_layout(root, 2024, ["2024010100"])
    folders = gs.resolve_scan_folders(root, date(2024, 1, 1), date(2024, 1, 1))
    assert sorted(p.name for p in folders) == ["2024"]


def test_day_layout_folders_resolved_unchanged(tmp_path):
    root = tmp_path / "cmameso"
    _cmameso_day_layout(root, "20250601", [0, 3])
    folders = gs.resolve_scan_folders(root, date(2025, 6, 1), date(2025, 6, 1))
    assert [p.name for p in folders] == ["20250601"]


def test_empty_root_resolves_to_no_folders(tmp_path):
    root = tmp_path / "cra40"
    root.mkdir()
    assert gs.resolve_scan_folders(root, date(2024, 1, 1), date(2024, 1, 1)) == []


def test_missing_root_raises_not_a_directory(tmp_path):
    with pytest.raises(NotADirectoryError):
        gs.resolve_scan_folders(tmp_path / "nope", date(2024, 1, 1), date(2024, 1, 1))


# ------------------------------------------------------- 两种布局扫描 --


def test_year_variable_layout_groups_complete_times(tmp_path):
    root = tmp_path / "cra40"
    _cra40_year_layout(root, 2024, ["2024010100", "2024010106"])
    folders = gs.resolve_scan_folders(root, date(2024, 1, 1), date(2024, 1, 1))
    scan = gs.scan_day_folders("cra40", folders)
    assert scan.complete_times == ("2024010100", "2024010106")
    assert scan.incomplete_times == ()
    assert scan.matched_files == 8


def test_cmameso_day_layout_groups_complete_times(tmp_path):
    root = tmp_path / "cmameso"
    _cmameso_day_layout(root, "20250601", [0, 3, 6])
    folders = gs.resolve_scan_folders(root, date(2025, 6, 1), date(2025, 6, 1))
    scan = gs.scan_day_folders("cmameso", folders)
    assert scan.complete_times == ("2025060100", "2025060103", "2025060106")


def test_no_double_counting_when_folders_overlap(tmp_path):
    root = tmp_path / "cra40"
    _cra40_year_layout(root, 2024, ["2024010100"])
    year_dir = root / "2024"
    scan = gs.scan_day_folders("cra40", [year_dir, year_dir / "gph", year_dir])
    assert scan.matched_files == 4          # 而非 4×3
    assert scan.complete_times == ("2024010100",)


def test_day_and_year_layouts_coexist_without_double_counting(tmp_path):
    root = tmp_path / "cra40"
    _cra40_year_layout(root, 2024, ["2024010100"])
    _cra40_day_layout(root, "20240102", [0])
    folders = gs.resolve_scan_folders(root, date(2024, 1, 1), date(2024, 1, 2))
    assert sorted(p.name for p in folders) == ["2024", "20240102"]
    scan = gs.scan_day_folders("cra40", folders)
    assert scan.complete_times == ("2024010100", "2024010200")
    assert scan.matched_files == 8


# -------------------------------------------------------- Reader 迭代 --


def test_iter_hours_reads_year_layout_across_days(tmp_path, fake_read):
    root = tmp_path / "cra40"
    _cra40_year_layout(root, 2024, ["2024010100", "2024010106", "2024010200"])
    reader = gs.GribSourceReader("cra40")
    entries = [ts for ts, _ in reader.iter_hours(root, date(2024, 1, 1), date(2024, 1, 2))]
    assert entries == ["2024010100", "2024010106", "2024010200"]


def test_iter_hours_reads_year_layout_across_years(tmp_path, fake_read):
    root = tmp_path / "cra40"
    _cra40_year_layout(root, 2024, ["2024123106"])
    _cra40_year_layout(root, 2025, ["2025010100"])
    reader = gs.GribSourceReader("cra40")
    entries = [ts for ts, _ in reader.iter_hours(root, date(2024, 12, 31), date(2025, 1, 1))]
    assert entries == ["2024123106", "2025010100"]


def test_iter_hours_respects_hour_bounds_in_year_layout(tmp_path, fake_read):
    root = tmp_path / "cra40"
    _cra40_year_layout(root, 2024, ["2024010100", "2024010106", "2024010112"])
    reader = gs.GribSourceReader("cra40")
    entries = [ts for ts, _ in reader.iter_hours(
        root, date(2024, 1, 1), date(2024, 1, 1),
        start_time="2024010100", end_time="2024010106")]
    assert entries == ["2024010100", "2024010106"]


def test_iter_hours_empty_root_yields_nothing(tmp_path, fake_read):
    root = tmp_path / "cra40"
    root.mkdir()
    reader = gs.GribSourceReader("cra40")
    assert list(reader.iter_hours(root, date(2024, 1, 1), date(2024, 1, 1))) == []


# --------------------------------------------- 覆盖校验在年布局下可用 --


def test_verify_hour_coverage_accepts_year_layout(tmp_path):
    root = tmp_path / "cra40"
    _cra40_year_layout(root, 2024, ["2024010100", "2024010106"])
    folders = gs.resolve_scan_folders(root, date(2024, 1, 1), date(2024, 1, 1))
    result = gs.verify_hour_coverage(
        "cra40", folders,
        start_time="2024010100", end_time="2024010106", time_resolution="6h")
    assert result["complete_time_count"] == 2
    assert result["missing_time_count"] == 0


def test_verify_hour_coverage_rejects_missing_in_year_layout(tmp_path):
    root = tmp_path / "cra40"
    _cra40_year_layout(root, 2024, ["2024010100"])
    folders = gs.resolve_scan_folders(root, date(2024, 1, 1), date(2024, 1, 1))
    with pytest.raises(FileNotFoundError) as excinfo:
        gs.verify_hour_coverage(
            "cra40", folders,
            start_time="2024010100", end_time="2024010106", time_resolution="6h")
    assert "2024010106" in str(excinfo.value)
