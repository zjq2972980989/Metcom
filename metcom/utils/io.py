"""通用文件 IO：站点列表、反演结果、输出文件（严格复刻 MATLAB 格式）。

本模块集中管理三类格式契约，下游模块（retrieval/timeinterp/evaluation）
均以此为准：

1. 站点列表（read_site_file）
   四列空白分隔：``站点名 纬度 经度 高程``（高程为正高），对应原 GUI 的
   ``textscan('%s %f %f %f', 'Whitespace', ' \\t')``。

2. 反演结果文件（write_result_file / read_result_file）
   首行 12 列表头 + 数据行。MATLAB fprintf 格式：表头 ``%4s`` + 每列
   ``%20s``，数值 ``%4s`` + 每列 ``%20.8f``；NaN 打印为右对齐 ``NaN``。
   列名与顺序见 RESULT_HEADERS（saveToText.m 一致）。

3. .mat 结果（save_result_mat）
   struct 变量名 ``Result_<源名>``，字段与 saveToMat.m 一致
   （见 _MAT_FIELD_ORDER）。

4. 文件名契约（get_doy / parse_ztd_filename）
   ``ztd_<YYYYMMDDHH>_sit_<源名>[.txt|.dat]``；输出子目录为年积日
   ``年 + 三位天数``（闰年 2024-06-01 → 2024153）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

# 结果表头（与 saveToText.m/saveToDat.m 一致）
RESULT_HEADERS = (
    "name", "latitude", "longitude", "height(m)",
    "ZTD(m)", "ZHD(m)", "ZWD(m)", "PWV(m)",
    "Ps(hpa)", "Tm(K)", "vapor_pressure(hpa)", "WVD",
)

_ZTD_NAME_RE = re.compile(r"^ztd_(\d{10})_sit_([A-Za-z0-9_]+?)(?:\.(txt|dat))?$")
_TIMESTAMP_RE = re.compile(r"\d{10}")


@dataclass
class Site:
    """站点信息（对应站点 txt 的 name lat lon h 四列，h 为正高）。"""

    name: str
    lat: float
    lon: float
    height: float


def read_site_file(path: Path | str) -> list[Site]:
    """读取站点列表文件（对应 MATLAB textscan('%s %f %f %f')）。

    空行跳过；字段不足 4 个或数值解析失败的行跳过并警告
    （原 MATLAB 会直接报错，此处放宽为警告以便批量处理）。
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"站点文件不存在: {path}")

    sites: list[Site] = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            tokens = line.split()
            if not tokens:
                continue
            if len(tokens) < 4:
                print(f"警告: {path.name} 第 {lineno} 行字段不足 4 个，已跳过: {line.strip()!r}")
                continue
            try:
                lat, lon, h = (float(t) for t in tokens[1:4])
            except ValueError:
                print(f"警告: {path.name} 第 {lineno} 行数值解析失败，已跳过: {line.strip()!r}")
                continue
            sites.append(Site(name=tokens[0], lat=lat, lon=lon, height=h))
    if not sites:
        raise ValueError(f"站点文件中未解析到有效站点: {path}")
    return sites


def get_doy(filename: str) -> tuple[str, str]:
    """从文件名提取时间信息（对应 ERA5.mlapp 的 getDOY）。

    Returns
    -------
    (year_doy, timestamp):
        year_doy 形如 '2024152'（用作输出子目录名），
        timestamp 形如 '2024060112'（用于输出文件名）。
    """
    m = _TIMESTAMP_RE.search(filename)
    if m is None:
        raise ValueError(f"文件名中未找到10位时间戳 (YYYYMMDDHH): {filename}")
    ts = m.group(0)
    year, month, day = int(ts[:4]), int(ts[4:6]), int(ts[6:8])
    # 年积日（与 MATLAB day(dt,'dayofyear') 一致）
    doy = (np.datetime64(f"{year:04d}-{month:02d}-{day:02d}")
           - np.datetime64(f"{year:04d}-01-01")) / np.timedelta64(1, "D") + 1
    return f"{year:d}{int(doy):03d}", ts


def parse_ztd_filename(filename: str) -> tuple[str, str, str]:
    """解析 ztd_YYYYMMDDHH_sit_SOURCE[.txt|.dat] 文件名。

    Returns
    -------
    (timestamp, source, timestamp) —— 与 04/05 模块使用的字段。
    """
    m = _ZTD_NAME_RE.match(Path(filename).name)
    if m is None:
        raise ValueError(f"文件名不符合 ztd_YYYYMMDDHH_sit_SOURCE 约定: {filename}")
    return m.group(1), m.group(2), m.group(1)


# 结果文件列规格：首列站名宽 4，其余 11 列宽 20、8 位小数（统一由 fmt 模块渲染）
RESULT_FMT_SPECS = [(4, None)] + [(20, 8)] * 11


def _fmt_header_row() -> str:
    from metcom.utils.fmt import fmt_fixed_width
    return fmt_fixed_width(list(RESULT_HEADERS), RESULT_FMT_SPECS)


def write_result_file(sites: Iterable[Site],
                      values: dict[str, list[float]],
                      output_file: Path | str) -> Path:
    """写反演结果文件（saveToText/saveToDat 的统一实现）。

    values 的键为 RESULT_HEADERS 的数值列名（除 name 外），
    每列为与 sites 等长的数值列表。
    """
    from metcom.utils.fmt import fmt_fixed_width

    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    sites = list(sites)
    n = len(sites)
    for key in ("latitude", "longitude", "height(m)", "ZTD(m)", "ZHD(m)", "ZWD(m)",
                "PWV(m)", "Ps(hpa)", "Tm(K)", "vapor_pressure(hpa)", "WVD"):
        if len(values.get(key, [])) != n:
            raise ValueError(f"列 '{key}' 长度({len(values.get(key, []))})与站点数({n})不一致")

    lines = [_fmt_header_row()]
    for i, s in enumerate(sites):
        cells = [s.name] + [values[key][i] for key in (
            "latitude", "longitude", "height(m)", "ZTD(m)", "ZHD(m)", "ZWD(m)",
            "PWV(m)", "Ps(hpa)", "Tm(K)", "vapor_pressure(hpa)", "WVD")]
        lines.append(fmt_fixed_width(cells, RESULT_FMT_SPECS))

    with open(output_file, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return output_file


# 反演结果 dict 键 → 结果文件表头列名
_RESULT_KEY_TO_COLUMN = {
    "lat": "latitude", "lon": "longitude", "h": "height(m)",
    "ZTD": "ZTD(m)", "ZHD": "ZHD(m)", "ZWD": "ZWD(m)", "PWV": "PWV(m)",
    "Ps": "Ps(hpa)", "Tm": "Tm(K)", "eE": "vapor_pressure(hpa)", "WVD": "WVD",
}

# mat struct 字段名 → 反演结果 dict 键（与 saveToMat.m 字段一一对应）
_MAT_FIELD_ORDER = (
    ("name", "name"),
    ("latitude", "lat"),
    ("longitude", "lon"),
    ("height_m", "h"),
    ("ZTD_m", "ZTD"),
    ("ZHD_m", "ZHD"),
    ("ZWD_m", "ZWD"),
    ("PWV_m", "PWV"),
    ("Ps_hpa", "Ps"),
    ("Tm_K", "Tm"),
    ("vapor_pressure_hpa", "eE"),
    ("WVD", "WVD"),
)


def save_result_mat(sites: Iterable[Site],
                    values: dict[str, list[float]],
                    output_file: Path | str,
                    struct_name: str) -> Path:
    """写 .mat 结果文件（saveToMat.m 等价实现，struct 字段名与原版一致）。

    values 以结果表头列名为键（同 write_result_file）。
    struct_name 形如 'Result_era5' / 'Result_cra40'。
    """
    from scipy.io import savemat  # 延迟导入，减小常驻依赖

    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    sites = list(sites)
    n = len(sites)
    dtype = [(field, "O") for field, _ in _MAT_FIELD_ORDER]
    arr = np.empty((1, n), dtype=dtype)
    for i, s in enumerate(sites):
        for field, key in _MAT_FIELD_ORDER:
            if field == "name":
                arr[0, i][field] = str(s.name)
            else:
                arr[0, i][field] = float(values[_RESULT_KEY_TO_COLUMN[key]][i])

    savemat(str(output_file), {struct_name: arr})
    return output_file


def read_result_file(path: Path | str) -> tuple[list[str], np.ndarray]:
    """读取反演结果文件（首行空白分隔表头 + 数值数据行）。

    Returns
    -------
    (columns, data):
        columns 为表头列名列表；data 形状 (n_sites, n_cols)，
        第 0 列为 NaN 占位（对应站名列），其余为 float。
    """
    path = Path(path)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        header_line = f.readline()
    columns = header_line.split()
    rows = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        next(f)
        for line in f:
            tokens = line.split()
            if not tokens:
                continue
            row = [np.nan]  # name 列占位
            for t in tokens[1:]:
                try:
                    row.append(float(t))
                except ValueError:
                    row.append(np.nan)
            rows.append(row)
    if not rows:
        return columns, np.empty((0, len(columns)))
    return columns, np.asarray(rows, dtype=float)
