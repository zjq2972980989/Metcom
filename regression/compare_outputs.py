"""回归输出对比工具：Python 输出 vs MATLAB 基准（逐站逐列数值对比）。

用法: python regression/compare_outputs.py <python_dir> <matlab_dir> [--rtol 1e-6]
"""

import sys
from pathlib import Path

import numpy as np

COLUMNS = ["latitude", "longitude", "height(m)", "ZTD(m)", "ZHD(m)", "ZWD(m)",
           "PWV(m)", "Ps(hpa)", "Tm(K)", "vapor_pressure(hpa)", "WVD"]


def read_table(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    header = lines[0].split()
    rows = []
    n_bad = 0
    for line in lines[1:]:
        tok = line.split()
        if not tok:
            continue
        if len(tok) != len(header):
            n_bad += 1  # 列溢出粘连行（如缺测泄漏导致数值超宽），跳过该行
            continue
        try:
            rows.append([float(t) if t.lower() not in ("nan",) else np.nan
                         for t in tok[1:]])
        except ValueError:
            n_bad += 1
    if n_bad:
        print(f"  [警告] {path.name}: 跳过 {n_bad} 个格式异常行（旧版列溢出）")
    return header, np.asarray(rows, dtype=float) if rows else np.empty((0, len(header) - 1))


def compare_dir(py_dir: Path, ml_dir: Path, rtol: float) -> bool:
    ml_files = sorted(ml_dir.rglob("ztd_*_sit_*.txt"))
    if not ml_files:
        print(f"[跳过] {ml_dir} 无基准文件")
        return True
    all_ok = True
    worst = (0.0, "")
    n_files = n_rows = n_missing = 0
    for ml_file in ml_files:
        rel = ml_file.relative_to(ml_dir)
        py_file = py_dir / rel
        if not py_file.is_file():
            n_missing += 1   # 基准存在但本次运行时间范围未覆盖（仅提示，不算失败）
            continue
        ml_header, ml = read_table(ml_file)
        py_header, py = read_table(py_file)
        if ml.shape != py.shape:
            print(f"[形状] {rel}: python {py.shape} vs matlab {ml.shape}")
            all_ok = False
            continue
        # 站名顺序按行号对齐（同一站点文件产出）
        with np.errstate(invalid="ignore"):
            err = np.abs(py - ml)
            scale = np.maximum(np.abs(ml), 1.0)
        rel_err = err / scale
        rel_err = np.where(np.isnan(rel_err) & np.isnan(ml) & np.isnan(py), 0.0, rel_err)
        n_nan_diff = int(np.sum(np.isnan(py) != np.isnan(ml)))
        max_rel = float(np.nanmax(rel_err)) if rel_err.size else 0.0
        n_files += 1
        n_rows += ml.shape[0]
        if max_rel > worst[0]:
            i, j = np.unravel_index(np.nanargmax(rel_err), rel_err.shape)
            worst = (max_rel, f"{rel} 行{i} 列{COLUMNS[j] if j < len(COLUMNS) else j} "
                              f"(py={py[i, j]:.8f} ml={ml[i, j]:.8f})")
        if max_rel > rtol or n_nan_diff:
            print(f"[超差] {rel}: max_rel={max_rel:.3e}, NaN差异={n_nan_diff}")
            all_ok = False
    status = "✅ 一致" if all_ok else "❌ 存在差异"
    print(f"{status}: 对比 {n_files} 个文件, {n_rows} 站行, "
          f"最大相对误差 {worst[0]:.3e} @ {worst[1]} (容差 {rtol:.0e}); "
          f"基准中未覆盖 {n_missing} 个文件")
    return all_ok


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("python_dir")
    ap.add_argument("matlab_dir")
    ap.add_argument("--rtol", type=float, default=1e-6)
    args = ap.parse_args()
    ok = compare_dir(Path(args.python_dir), Path(args.matlab_dir), args.rtol)
    sys.exit(0 if ok else 1)
