"""时间维度插值（04_TimeInter / interpolate_gnss_data.m 的 Python 复刻）。

对低时间分辨率反演结果（如 MERRA2 6h）做时间插值至逐小时：
  - 文件名约定 ztd_YYYYMMDDHH_sit_SOURCE.{txt|dat}，按年积日文件夹组织；
  - 动态跨天扩展：当天无 24 时且非最后一天时，复制次日 00 时作为当日 24 时
    （临时文件，处理完删除）；
  - 插值变量有效点 <2 个 → 全 NaN；否则 not-a-knot 三次样条外推
    （MATLAB interp1 'spline','extrap'），异常回退线性外推；
  - 输出表头 %4s + %19s，数据 %4s + %19.8f，NaN 输出 'NaN'（MATLAB 行为）。
"""

from __future__ import annotations

import re
from pathlib import Path
from shutil import copy2

import numpy as np
from scipy.interpolate import CubicSpline

from metcom.utils.fmt import fmt_fixed_width

DEFAULT_VARIABLES = ["ZTD(m)", "ZHD(m)", "ZWD(m)", "PWV(m)", "Ps(hpa)",
                     "Tm(K)", "vapor_pressure(hpa)", "WVD"]

_FILENAME_RE = re.compile(r"^ztd_(\d{8})(\d{2})_sit_([^.]+)\.(.+)$")


def _interp_spline(x: np.ndarray, y: np.ndarray, xq: np.ndarray) -> np.ndarray:
    """interp1(x, y, xq, 'spline', 'extrap') + 线性回退。"""
    try:
        return CubicSpline(x, y, bc_type="not-a-knot", extrapolate=True)(xq)
    except Exception:  # noqa: BLE001 —— 对应原版 try/catch 线性回退
        w = np.isfinite(y)
        if w.sum() == 1:
            return np.full_like(xq, float(y[w][0]))
        return _interp_extrap(x[w], y[w], xq)


def _interp_extrap(x: np.ndarray, y: np.ndarray, xq: np.ndarray) -> np.ndarray:
    out = np.empty_like(xq, dtype=float)
    for i, q in enumerate(xq):
        if q <= x[0]:
            slope = (y[1] - y[0]) / (x[1] - x[0])
            out[i] = y[0] + slope * (q - x[0])
        elif q >= x[-1]:
            slope = (y[-1] - y[-2]) / (x[-1] - x[-2])
            out[i] = y[-1] + slope * (q - x[-1])
        else:
            out[i] = np.interp(q, x, y)
    return out


def _read_gnss_file(filepath: Path) -> tuple[list[list], dict[str, int], list[str]]:
    """read_gnss_file 复刻：表头 tokens + 数据（首列站名，其余 float）。"""
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        header_line = f.readline()
        if not header_line:
            raise ValueError(f"文件为空或无表头: {filepath}")
        header_tokens = header_line.split()
        col_map = {tok: i for i, tok in enumerate(header_tokens)}  # 0 基列号
        data: list[list] = []
        for line in f:
            tokens = line.split()
            if not tokens:
                continue
            row: list = [tokens[0]]
            for tok in tokens[1:]:
                try:
                    row.append(float(tok))
                except ValueError:
                    row.append(np.nan)
            while len(row) < len(header_tokens):  # 缺失列按 NaN 容错
                row.append(np.nan)
            data.append(row)
    return data, col_map, header_tokens


# 输出列规格：首列站名宽 4，其余 11 列宽 19、8 位小数（列间以空格分隔）
_TIMEINTERP_SPECS = [(4, None)] + [(19, 8)] * 11
_TIMEINTERP_HEADER_SPECS = [(4, None)] + [(19, None)] * 11


def interpolate_one_day(doy_path: Path, out_doy_path: Path,
                        target_interval: int,
                        variables: list[str],
                        is_last_day: bool) -> int:
    """处理单个年积日文件夹，返回写出的文件数。"""
    hour_files: dict[int, Path] = {}
    current_date = output_source = output_ext = None
    for pattern in ("ztd_*_sit_*.txt", "ztd_*_sit_*.dat"):
        for p in sorted(doy_path.glob(pattern)):
            m = _FILENAME_RE.match(p.name)
            if m is None:
                print(f"警告: 无法解析文件名格式: {p.name}，跳过")
                continue
            date_part, hour_str, source_name, file_ext = m.groups()
            if current_date is None:
                current_date, output_source, output_ext = date_part, source_name, file_ext
            hour_files[int(hour_str)] = p

    hour_list = sorted(hour_files)
    out_doy_path.mkdir(parents=True, exist_ok=True)
    temp_files: list[Path] = []

    # ---- 动态跨天边界处理 ----
    has_24 = 24 in hour_list
    if not is_last_day and not has_24:
        next_day_file = _find_next_day_zero_file(doy_path.parent, _next_doy(doy_path.name))
        if next_day_file is not None:
            new_name = f"ztd_{current_date}24_sit_{output_source}.{output_ext}"
            dest = doy_path / new_name
            copy2(next_day_file, dest)
            temp_files.append(dest)
            hour_files[24] = dest
            hour_list = sorted(hour_files)
            print(f"  已复制下一日0时数据到当前文件夹作为扩展数据: {new_name}")
        else:
            print("  警告: 下一日没有0时数据，无法扩展24时")

    if len(hour_list) < 2:
        print(f"  警告: 文件夹 {doy_path.name} 中有效数据点不足，跳过")
        _cleanup(temp_files)
        return 0

    print("  找到 %d 个时间点: %s" % (
        len(hour_list),
        " ".join("24(ext)" if h == 24 else f"{h:02d}" for h in hour_list)))

    # ---- 读取数据（第一个文件定义表头与站点基准）----
    all_data: list[list[list]] = []
    var_map: dict[str, int] = {}
    base_headers: list[str] = []
    station_info: list[list] = []
    for h in hour_list:
        data, col_map, headers = _read_gnss_file(hour_files[h])
        all_data.append(data)
        if not var_map:
            var_map = col_map
            base_headers = headers
            station_info = [row[:4] for row in data]

    num_stations = len(station_info)
    has_extended = 24 in hour_list
    original_hours = np.asarray(hour_list, dtype=float)
    target_hours = (np.arange(0, 24 + target_interval, target_interval, dtype=float)
                    if has_extended
                    else np.arange(0, max(hour_list) + 1, target_interval, dtype=float))

    print(f"  开始处理 {num_stations} 个站点的插值与数据写入...")

    n_out = 0
    missing = [v for v in variables if v not in var_map]
    for v in missing:
        print(f"警告: 变量 {v} 不在表头中，跳过")

    for si in range(num_stations):
        interpolated = np.full((len(target_hours), len(variables)), np.nan)
        for vi, var_name in enumerate(variables):
            if var_name not in var_map:
                continue
            col = var_map[var_name]
            vals = np.array([
                (all_data[hi][si][col]
                 if isinstance(all_data[hi][si][col], (int, float)) else np.nan)
                for hi in range(len(hour_list))
            ], dtype=float)
            vals[~np.isfinite(vals)] = np.nan
            w = ~np.isnan(vals)
            if w.sum() < 2:
                continue
            interpolated[:, vi] = _interp_spline(original_hours[w], vals[w], target_hours)

        for ti, target_hour in enumerate(target_hours):
            # 输出 0..24 时（原版已修复 <=23 丢弃 24 时的 bug）
            out_name = f"ztd_{current_date}{int(target_hour):02d}_sit_{output_source}.{output_ext}"
            out_path = out_doy_path / out_name
            if si == 0:
                headers = base_headers[:4] + list(variables)
                line = fmt_fixed_width(headers, _TIMEINTERP_HEADER_SPECS, sep=" ")
                with open(out_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(line + "\n")
                n_out += 1
            info = station_info[si]
            cells = [info[0], info[1], info[2], info[3], *interpolated[ti]]
            with open(out_path, "a", encoding="utf-8", newline="\n") as f:
                f.write(fmt_fixed_width(cells, _TIMEINTERP_SPECS, sep=" ") + "\n")

    _cleanup(temp_files)
    print(f"  文件夹 {doy_path.name} 处理完成")
    return n_out


def _next_doy(doy_name: str) -> str:
    import datetime as dt
    y, d = int(doy_name[:4]), int(doy_name[4:])
    date = dt.date(y, 1, 1) + dt.timedelta(days=d)  # 当天 + 1 天
    return f"{date.year}{date.timetuple().tm_yday:03d}"


def _find_next_day_zero_file(root: Path, next_doy: str) -> Path | None:
    next_path = root / next_doy
    if not next_path.is_dir():
        return None
    for pattern in ("ztd_*00_sit_*.txt", "ztd_*00_sit_*.dat"):
        found = sorted(next_path.glob(pattern))
        if found:
            return found[0]
    return None


def _cleanup(temp_files: list[Path]) -> None:
    for p in temp_files:
        if p.is_file():
            p.unlink()


def run_timeinterp(cfg: dict) -> dict:
    """运行入口（main.m 复刻，配置节 metcom_timeinterp.yaml）。"""
    input_dir = Path(cfg["input_dir"])
    output_dir = Path(cfg["output_dir"])
    target_interval = int(cfg.get("target_interval", 1))
    variables = list(cfg.get("variables") or DEFAULT_VARIABLES)
    # original_interval 在原 MATLAB 代码中仅作文档作用，不参与计算

    doy_folders = sorted([d for d in input_dir.glob("20*") if d.is_dir()],
                         key=lambda p: p.name)
    print(f"找到 {len(doy_folders)} 个年积日文件夹")
    total = 0
    for i, doy_path in enumerate(doy_folders):
        print(f"\n处理文件夹: {doy_path.name}")
        out_doy = output_dir / doy_path.name
        total += interpolate_one_day(doy_path, out_doy, target_interval,
                                     variables, is_last_day=(i == len(doy_folders) - 1))
    print("\n========== 处理完成 ==========")
    print(f"输出文件夹: {output_dir}")
    return {"output_files": total}
