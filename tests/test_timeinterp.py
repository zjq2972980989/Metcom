"""P4 时间插值测试：以 benchmarks/output_Merra2 为输入的回归验证。"""

import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.interpolate import CubicSpline

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from metcom.timeinterp.runner import interpolate_one_day, run_timeinterp

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCH_MERRA2 = PROJECT_ROOT / "benchmarks" / "output_Merra2"


def test_timeinterp_full(tmp_path):
    doys = sorted(d.name for d in BENCH_MERRA2.glob("20*") if d.is_dir())
    assert len(doys) >= 2, "基准数据不足两天"
    total = run_timeinterp({
        "input_dir": str(BENCH_MERRA2),
        "output_dir": str(tmp_path / "out"),
        "target_interval": 1,
    })["output_files"]
    assert total > 0

    first, last = doys[0], doys[-1]
    # 第一天：00-18 共 4 个时次 + 次日 00 扩展为 24 → 目标 0..24 共 25 个文件
    out_first = sorted((tmp_path / "out" / first).glob("*.txt"))
    assert len(out_first) == 25, [p.name for p in out_first]
    assert (tmp_path / "out" / first / "ztd_2024010124_sit_merra2.txt").is_file()
    # 最后一天：无 24 时扩展 → 目标 0..18 共 19 个文件
    out_last = sorted((tmp_path / "out" / last).glob("*.txt"))
    assert len(out_last) == 19
    # 临时 24 时文件已被清理（输入目录不变）
    assert not (BENCH_MERRA2 / first / "ztd_2024010124_sit_merra2.txt").exists()

    # 内容校验：0 时文件与输入 0 时文件逐站逐列一致（样条过节点）
    src0 = _read_table(BENCH_MERRA2 / first / "ztd_2024010100_sit_merra2.txt")
    dst0 = _read_table(tmp_path / "out" / first / "ztd_2024010100_sit_merra2.txt")
    assert len(dst0) == len(src0)
    for r_src, r_dst in zip(src0, dst0):
        assert r_src[0] == r_dst[0]  # 站名
        assert np.allclose(np.array(r_dst[1:], dtype=float),
                           np.array(r_src[1:], dtype=float), rtol=1e-6, atol=1e-9)

    # 跨天边界: 24 时文件与次日 00 时文件数值一致
    ext24 = _read_table(tmp_path / "out" / first / "ztd_2024010124_sit_merra2.txt")
    nxt00 = _read_table(BENCH_MERRA2 / doys[1] / f"ztd_{_date_of(doys[1])}00_sit_merra2.txt")
    assert len(ext24) == len(nxt00)
    for r_a, r_b in zip(ext24, nxt00):
        assert np.allclose(np.array(r_a[4:], dtype=float),
                           np.array(r_b[4:], dtype=float), rtol=1e-6, atol=1e-9)

    # 样条正确性抽查：用 00/06/12/18 + 次日00(=24时扩展源) 的 ZTD 独立做
    # not-a-knot 样条，对比输出 03 时数值
    hours = np.array([0.0, 6.0, 12.0, 18.0, 24.0])
    next_first = sorted((BENCH_MERRA2 / doys[1]).glob("ztd_*00_sit_*.txt"))[0]
    tables = {}
    for h in hours:
        name = f"ztd_20240101{int(h):02d}_sit_merra2.txt"
        tables[h] = _read_table(tmp_path / "out" / first / name)
    src = {int(h): _read_table(BENCH_MERRA2 / first /
                               f"ztd_20240101{int(h):02d}_sit_merra2.txt")
           for h in (0, 6, 12, 18)}
    src_next0 = _read_table(next_first)
    r0 = tables[0.0]
    out3 = _read_table(tmp_path / "out" / first / "ztd_2024010103_sit_merra2.txt")
    step = max(1, len(r0) // 5)
    for si in range(0, len(r0), step):  # 抽查若干站
        ztd = [float(src[int(h)][si][4]) for h in (0, 6, 12, 18)]
        ztd.append(float(src_next0[si][4]))  # 24 时 = 次日 00 时
        spline = CubicSpline(hours, np.array(ztd, dtype=float), bc_type="not-a-knot")
        got = float(out3[si][4])
        assert got == pytest.approx(float(spline(3.0)), rel=1e-8), (
            f"站 {r0[si][0]} 3 时样条不一致")


def _date_of(doy: str) -> str:
    import datetime as dt
    y, d = int(doy[:4]), int(doy[4:])
    date = dt.date(y, 1, 1) + dt.timedelta(days=d - 1)
    return date.strftime("%Y%m%d")


def _read_table(path: Path) -> list[list]:
    rows = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if i == 0:
            continue
        tokens = line.split()
        if tokens:
            rows.append(tokens)
    return rows


def test_interpolate_one_day_insufficient(tmp_path):
    # 单时次文件夹应跳过
    d = tmp_path / "2024001"
    d.mkdir()
    src = BENCH_MERRA2 / sorted(x.name for x in BENCH_MERRA2.glob("20*"))[0]
    first = sorted(src.glob("ztd_*_sit_*.txt"))[0]
    (d / first.name).write_text(first.read_text(encoding="utf-8"), encoding="utf-8")
    n = interpolate_one_day(d, tmp_path / "out" / "2024001", 1,
                            ["ZTD(m)"], is_last_day=True)
    assert n == 0
