"""配置文件加载模块。

配置文件命名规范：``metcom_<模块名称>.yaml``，统一放 ``configs/`` 目录。
优先级：命令行参数 > 配置文件 > 代码内置默认值（默认值取自原 MATLAB App 初始值）。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


def load_config(config_path: Optional[Path | str], module_name: str) -> Dict[str, Any]:
    """加载 metcom_<模块名称>.yaml 配置文件。

    Parameters
    ----------
    config_path:
        配置文件路径；为 None 时尝试在工作目录 ``configs/`` 下按规范名查找。
    module_name:
        模块名称（如 ``era5`` / ``gridgen`` / ``timeinterp``），用于规范名查找与报错提示。
    """
    if config_path is None:
        default = Path("configs") / f"metcom_{module_name}.yaml"
        if default.is_file():
            config_path = default
        else:
            raise FileNotFoundError(
                f"未指定配置文件且默认位置不存在: {default}，"
                f"请用 --config 指定 metcom_{module_name}.yaml"
            )
    config_path = Path(config_path)
    if not config_path.is_file():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError(f"配置文件格式错误（根节点须为键值映射）: {config_path}")
    return cfg


def apply_overrides(cfg: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """将命令行参数覆盖到配置字典上（仅覆盖用户显式提供的非 None 值）。"""
    cfg = copy.deepcopy(cfg)
    for key, value in overrides.items():
        if value is not None:
            cfg[key] = value
    return cfg


def require(cfg: Dict[str, Any], key: str, ctx: str = "") -> Any:
    """取出必填配置项，缺失时给出明确报错。"""
    if key not in cfg or cfg[key] is None:
        where = f"（{ctx}）" if ctx else ""
        raise KeyError(f"配置缺少必填项 '{key}'{where}")
    return cfg[key]


def parse_yyyymmdd(value: str | int) -> tuple[int, int, int]:
    """解析 YYYYMMDD 或 YYYYMMDDHH 形式的时间串（取前 8 位日期部分）。"""
    s = str(value).strip()
    if len(s) < 8 or not s[:8].isdigit():
        raise ValueError(f"时间格式须为 YYYYMMDD 或 YYYYMMDDHH: {value!r}")
    return int(s[:4]), int(s[4:6]), int(s[6:8])


def parse_yyyymmddhh(value: str | int) -> tuple[int, int, int, int]:
    """解析 YYYYMMDDHH 形式的时间串（10 位，用于反演/插值时间范围）。"""
    s = str(value).strip()
    if len(s) != 10 or not s.isdigit():
        raise ValueError(f"时间格式须为 10 位 YYYYMMDDHH: {value!r}")
    y, m, d = parse_yyyymmdd(s[:8])
    return y, m, d, int(s[8:10])
