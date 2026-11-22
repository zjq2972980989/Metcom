"""评估计算（compareByTime_Grid.py / compareByGrid.py 的统一迁移）。

eval_mode:
  timeseries —— 每时刻全体站点平均 Bias/RMSE 时间序列（原 compareByTime_Grid）
  bystation  —— 每站点全时段 Bias/RMSE（原 compareByGrid）
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from metcom.evaluation.io import index_files, parse_time_input, read_dynamic_file
from metcom.utils.fmt import fmt_fixed_width

DEFAULT_VAR_CONFIG = {
    "ZTD(m)": (1000.0, "ZTD", "mm"),
    "PWV(m)": (1000.0, "PWV", "mm"),
    "Ps(hpa)": (1.0, "Ps", "hPa"),
    "Tm(K)": (1.0, "Tm", "K"),
}


def _prepare(cfg: dict):
    dir_a, dir_b = cfg["dir_a"], cfg["dir_b"]
    name_a = cfg.get("name_a", "A")
    name_b = cfg.get("name_b", "B")
    start_str, end_str = parse_time_input(cfg["time_input"])
    var_config = {**DEFAULT_VAR_CONFIG, **(cfg.get("var_config") or {})}
    eval_vars = list(cfg.get("eval_vars") or
                     [v for v in ("ZTD(m)", "PWV(m)") if v in var_config])
    if isinstance(eval_vars, list) and eval_vars and isinstance(eval_vars[0], dict):
        # 配置形如 [{key, column_a, column_b}]: 统一到 VAR_CONFIG 结构
        raise ValueError("eval_vars 配置格式应为变量原始列名列表")
    output_dir = Path(cfg["output_dir"])
    tag = cfg.get("output_tag") or f"{name_a}_vs_{name_b}"
    return (dir_a, dir_b, name_a, name_b, start_str, end_str,
            var_config, eval_vars, output_dir, tag)


def analyze_timeseries(cfg: dict) -> pd.DataFrame:
    """原 compareByTime_Grid.analyze_timeseries。"""
    (dir_a, dir_b, name_a, name_b, start_str, end_str,
     var_config, eval_vars, output_dir, tag) = _prepare(cfg)

    print("正在索引数据源文件...")
    map_a = index_files(dir_a)
    map_b = index_files(dir_b)
    print(f"  [{name_a}] 找到 {len(map_a)} 个时刻文件")
    print(f"  [{name_b}] 找到 {len(map_b)} 个时刻文件")

    common_times = sorted(t for t in set(map_a) & set(map_b)
                          if start_str <= t <= end_str)
    print(f"有效匹配时间点: {len(common_times)} 个 (范围: {start_str} - {end_str})")

    results = []
    for t_key in common_times:
        df_a = read_dynamic_file(map_a[t_key], var_config)
        df_b = read_dynamic_file(map_b[t_key], var_config)
        if df_a.empty or df_b.empty:
            continue
        out_names = [var_config[v][1] for v in eval_vars]
        merged = pd.merge(df_a[["SITE"] + out_names],
                          df_b[["SITE"] + out_names],
                          on="SITE", suffixes=(f"_{name_a}", f"_{name_b}"))
        if merged.empty:
            continue
        row = {"Time": t_key, "N": len(merged)}
        for var in eval_vars:
            _, dn, unit = var_config[var]
            diff = merged[f"{dn}_{name_a}"] - merged[f"{dn}_{name_b}"]
            row[f"{dn}_Bias({unit})"] = diff.mean()
            row[f"{dn}_RMSE({unit})"] = np.sqrt(np.mean(diff ** 2))
        results.append(row)

    if not results:
        raise ValueError("无有效匹配数据！")
    report = pd.DataFrame(results).sort_values("Time").reset_index(drop=True)

    output_dir.mkdir(parents=True, exist_ok=True)
    safe_time = str(cfg["time_input"]).replace(",", "_")
    csv_file = output_dir / f"{tag}_Timeseries_{safe_time}.csv"
    report.to_csv(csv_file, index=False, float_format="%.4f")

    txt_file = output_dir / f"{tag}_Timeseries_{safe_time}.txt"
    with open(txt_file, "w", encoding="utf-8") as f:
        f.write(f"处理时间范围: {start_str}-{end_str}\n")
        f.write(f"处理小时数: {len(report)}\n")
        for var in eval_vars:
            _, dn, u = var_config[var]
            f.write(f"  {dn}  Bias 均值: {report[f'{dn}_Bias({u})'].mean():.4f} {u}\n")
            f.write(f"  {dn}  RMSE 均值: {report[f'{dn}_RMSE({u})'].mean():.4f} {u}\n")
        f.write("\n")
        cols = report.columns.tolist()
        col_specs = [(15, None)] * len(cols)
        f.write(fmt_fixed_width(list(cols), col_specs, sep="  ") + "\n")
        for _, r in report.iterrows():
            cells = [r[c] for c in cols]
            row_specs = [(15, 4) if isinstance(v, float) else (15, None)
                         for v in cells]
            f.write(fmt_fixed_width(cells, row_specs, sep="  ", nan_text="nan")
                    + "\n")

    print(f"CSV 已保存: {csv_file.resolve()}")
    print(f"TXT 已保存: {txt_file.resolve()}")
    return report


def analyze_stations(cfg: dict) -> pd.DataFrame:
    """原 compareByGrid.analyze_stations。"""
    (dir_a, dir_b, name_a, name_b, start_str, end_str,
     var_config, eval_vars, output_dir, tag) = _prepare(cfg)

    map_a = index_files(dir_a)
    map_b = index_files(dir_b)
    common_times = sorted(t for t in set(map_a) & set(map_b)
                          if start_str <= t <= end_str)
    print(f"共找到 {len(common_times)} 个匹配时间点，开始读取数据...")

    all_a, all_b = [], []
    for t_key in common_times:
        df_a = read_dynamic_file(map_a[t_key], var_config)
        df_b = read_dynamic_file(map_b[t_key], var_config)
        if not df_a.empty:
            all_a.append(df_a)
        if not df_b.empty:
            all_b.append(df_b)

    if not all_a or not all_b:
        raise ValueError("无有效匹配数据！（数据源文件读取为空，请检查目录与格式）")
    df_all_a = pd.concat(all_a, ignore_index=True)
    df_all_b = pd.concat(all_b, ignore_index=True)

    merge_cols = ["TIME_KEY", "SITE"]
    out_names = [var_config[v][1] for v in eval_vars]
    merged = pd.merge(df_all_a[merge_cols + ["LAT", "LON", "ALT"] + out_names],
                      df_all_b[merge_cols + out_names],
                      on=merge_cols, suffixes=(f"_{name_a}", f"_{name_b}"))

    results = []
    tr_label = f"{start_str}-{end_str}"
    for site, grp in merged.groupby("SITE"):
        row = {"SITE": site,
               "LAT/deg": grp["LAT"].iloc[0], "LON/deg": grp["LON"].iloc[0],
               "ALT/m": grp["ALT"].iloc[0], "Time_Range": tr_label, "N": len(grp)}
        for var in eval_vars:
            _, dn, u = var_config[var]
            diff = grp[f"{dn}_{name_a}"] - grp[f"{dn}_{name_b}"]
            row[f"{dn}_RMSE({u})"] = np.sqrt(np.mean(diff ** 2))
            row[f"{dn}_Bias({u})"] = diff.mean()
        results.append(row)

    report = pd.DataFrame(results)
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_time = str(cfg["time_input"]).replace(",", "_")
    csv_file = output_dir / f"{tag}_ByStation_{safe_time}.csv"
    report.to_csv(csv_file, index=False, float_format="%.6f")

    txt_file = output_dir / f"{tag}_ByStation_{safe_time}.txt"
    with open(txt_file, "w", encoding="utf-8") as f:
        cols = report.columns.tolist()
        col_specs = [(15, None)] * len(cols)
        f.write(fmt_fixed_width(list(cols), col_specs, sep="  ") + "\n")
        for _, r in report.iterrows():
            cells = [r[c] for c in cols]
            row_specs = [(15, 6) if isinstance(v, float) else (15, None)
                         for v in cells]
            f.write(fmt_fixed_width(cells, row_specs, sep="  ", nan_text="nan")
                    + "\n")

    print(f"CSV 已保存: {csv_file.resolve()}")
    print(f"TXT 已保存: {txt_file.resolve()}")
    return report


def run_evaluation(cfg: dict) -> dict:
    """运行入口（配置节 metcom_evaluation.yaml）。"""
    mode = cfg.get("eval_mode", "timeseries")
    if mode == "timeseries":
        report = analyze_timeseries(cfg)
    elif mode == "bystation":
        report = analyze_stations(cfg)
    else:
        raise ValueError(f"未知评估模式: {mode}")
    return {"mode": mode, "rows": len(report)}
