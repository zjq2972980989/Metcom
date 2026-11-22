"""评估共享 IO：文件索引与结果读取（原两评估脚本的公共部分抽离）。

相对原版的修复（见 docs/porting_notes.md）：
  - read_dynamic_file 不再静默吞异常（打印警告后返回空表，调用方可感知）；
  - index_files 同一时刻出现不同数据源文件时给出警告（原版静默覆盖）。
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

_FILE_PATTERN = re.compile(r"^ztd_(\d{10})_sit_([a-zA-Z0-9_]+)(?:\.txt)?$")
_TIME_RE = re.compile(r"ztd_(\d{10})_sit_")


def index_files(root_dir: str | Path) -> dict[str, Path]:
    """索引目录下所有 ztd_* 文件（兼容 .txt 后缀与无后缀），键为 10 位时间。"""
    file_map: dict[str, Path] = {}
    root_path = Path(root_dir)
    if not root_path.is_dir():
        print(f"警告: 目录不存在: {root_path}")
        return file_map

    for doy_dir in sorted(root_path.iterdir()):
        if not doy_dir.is_dir() or not doy_dir.name.startswith("20"):
            continue
        for f in doy_dir.iterdir():
            if not f.is_file():
                continue
            m = _FILE_PATTERN.match(f.name)
            if m:
                time_key, source = m.group(1), m.group(2)
                prev = file_map.get(time_key)
                if prev is not None and _TIME_RE.search(prev.name).group(1) == time_key:
                    src_prev = _FILE_PATTERN.match(prev.name)
                    if src_prev and src_prev.group(2) != source:
                        print(f"警告: 时刻 {time_key} 存在不同数据源文件 "
                              f"({src_prev.group(2)} / {source})，保留后者")
                file_map[time_key] = f
    return file_map


def read_dynamic_file(file_path: Path, target_vars: dict[str, tuple]) -> pd.DataFrame:
    """读取单个结果文件（自动识别表头，统一列名并计算换算列）。

    target_vars: {'原始列名': (转换系数, 输出名, 单位)}
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            header = f.readline().strip().split()
        df = pd.read_csv(file_path, sep=r"\s+", skiprows=1, header=None,
                         names=header, engine="python")
    except Exception as e:  # noqa: BLE001 —— 显式告警（原版静默返回空表）
        print(f"警告: 读取失败 {file_path}: {e}")
        return pd.DataFrame()

    df.rename(columns={"name": "SITE", "latitude": "LAT",
                       "longitude": "LON", "height(m)": "ALT"}, inplace=True)
    m = _TIME_RE.search(Path(file_path).name)
    if m:
        df["TIME_KEY"] = m.group(1)

    for orig_name, (factor, out_name, _) in target_vars.items():
        df[out_name] = df[orig_name] * factor if orig_name in df.columns else np.nan
    return df


def parse_time_input(time_str: str) -> tuple[str, str]:
    parts = str(time_str).strip().split(",")
    start_str = parts[0].strip()
    end_str = parts[1].strip() if len(parts) > 1 else start_str
    return start_str, end_str
