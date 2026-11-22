# metcom_product/validator.py
"""
转换一致性校验。

在 converter 写入产品文件之前，比对"输入 TXT"和"输出内容"，
保证没有静默丢行、静默改数、NaN、列数错等问题。

校验失败 → 抛 ValidationError，converter 不写文件。
"""

from __future__ import annotations
import re
from typing import List


class ValidationError(Exception):
    """转换一致性校验失败"""
    pass


# ============================================================
# 主入口：校验转换前后的数据
# ============================================================
def validate_conversion(
    rows_in: List[dict],
    content_out: str,
    source: str,
    region: str,
    params: List[str],
    tol: float = 1e-4,
) -> dict:
    """
    校验转换一致性。

    参数：
        rows_in:     原始 TXT 解析出来的行（list of dict）
        content_out: 将要写入的标准产品内容（str，含 Header + 数据区）
        source:      数据源代号（仅用于报错信息）
        region:      区域代号（仅用于报错信息）
        params:      参数顺序，如 ["PWV","WVD","Tm","ZTD","ZHD","ZWD"]
        tol:         浮点比较容差

    返回：
        统计信息 dict

    抛异常：
        ValidationError: 任何一项校验不通过
    """
    # -------- 1. 解析输出内容 --------
    # Header 以 "!" 开头，全部跳过
    # 数据区从第一个非 ! 行开始，全部是数据
    # 注意：标准产品的 Header 里已经写明列名
    #      "! latitude longitude PWV WVD Tm ZTD ZHD ZWD"
    #      所以数据区没有单独的"列名行"
    data_lines = []
    for line in content_out.splitlines():
        if not line or line.startswith("!"):
            continue
        data_lines.append(line)

    if not data_lines:
        raise ValidationError(
            f"[{source}/{region}] 输出内容无数据区"
        )

    # 所有非 ! 行都是数据
    rows_out = [ln.split() for ln in data_lines]

    # 期望列数 = 2 (lat, lon) + len(params)
    expected_cols = 2 + len(params)
    if rows_out and len(rows_out[0]) != expected_cols:
        raise ValidationError(
            f"[{source}/{region}] 输出列数 {len(rows_out[0])} != 期望 {expected_cols}"
        )

    # -------- 2. 行数校验 --------
    n_in = len(rows_in)
    n_out = len(rows_out)
    if n_in != n_out:
        raise ValidationError(
            f"[{source}/{region}] 行数不一致: 输入 {n_in}, 输出 {n_out} "
            f"(可能丢了 {n_in - n_out} 行)"
        )

    # -------- 3. 每行列数校验 --------
    for i, r in enumerate(rows_out):
        if len(r) != expected_cols:
            raise ValidationError(
                f"[{source}/{region}] 第 {i+1} 行列数 {len(r)} != {expected_cols}: {r}"
            )

    # -------- 4. 数值解析 + NaN 校验 --------
    data = []
    for i, r in enumerate(rows_out):
        vals = []
        for x in r:
            try:
                v = float(x)
            except ValueError:
                raise ValidationError(
                    f"[{source}/{region}] 第 {i+1} 行有非数值: {x}"
                )
            if v != v:  # NaN
                raise ValidationError(
                    f"[{source}/{region}] 第 {i+1} 行有 NaN"
                )
            vals.append(v)
        data.append(vals)

    # -------- 5. 经纬度集合校验 --------
    set_in = set()
    for row in rows_in:
        try:
            lat = round(float(row["latitude"]), 4)
            lon = round(float(row["longitude"]), 4)
        except (KeyError, ValueError):
            continue
        set_in.add((lat, lon))

    set_out = set()
    for row in data:
        lat = round(row[0], 4)
        lon = round(row[1], 4)
        set_out.add((lat, lon))

    if set_in != set_out:
        only_in = set_in - set_out
        only_out = set_out - set_in
        msg = f"[{source}/{region}] 经纬度集合不一致: "
        if only_in:
            msg += f"输入独有 {len(only_in)} 个 (如 {list(only_in)[:2]}) "
        if only_out:
            msg += f"输出独有 {len(only_out)} 个 (如 {list(only_out)[:2]})"
        raise ValidationError(msg)

    # -------- 6. 六参数统计量校验 --------
    stats_in = _stats_in(rows_in, params)
    stats_out = _stats_out(data, params)

    for p in params:
        for metric in ("min", "max", "mean"):
            v_in = stats_in[p][metric]
            v_out = stats_out[p][metric]
            diff = abs(v_in - v_out)
            denom = max(abs(v_in), 1e-9)
            rel = diff / denom
            if rel > tol and diff > tol:
                raise ValidationError(
                    f"[{source}/{region}] 参数 {p} 的 {metric} 不一致: "
                    f"输入 {v_in:.6f}, 输出 {v_out:.6f}, 相对误差 {rel:.2e}"
                )

    return {
        "n_rows": n_in,
        "n_cols": expected_cols,
        "lat_min": min(r[0] for r in data),
        "lat_max": max(r[0] for r in data),
        "lon_min": min(r[1] for r in data),
        "lon_max": max(r[1] for r in data),
        "params": params,
    }


# ============================================================
# 内部工具
# ============================================================
def _stats_in(rows: List[dict], params: List[str]) -> dict:
    """
    从原始 TXT 行（list of dict，列名可能带单位）里提取六参数统计量。
    """
    # 建立 参数名 → 实际列名 的映射
    # 例如 "PWV" → "PWV(m)"
    col_map = {}
    if rows:
        for col in rows[0].keys():
            col_clean = re.sub(r"\(.*?\)", "", col).strip()
            if col_clean in params:
                col_map[col_clean] = col

    result = {}
    for p in params:
        col = col_map.get(p)
        if col is None:
            raise ValidationError(f"输入行里找不到参数 {p}")
        vals = []
        for row in rows:
            try:
                vals.append(float(row[col]))
            except (KeyError, ValueError):
                continue
        if not vals:
            raise ValidationError(f"参数 {p} 没有有效数值")
        result[p] = {
            "min": min(vals),
            "max": max(vals),
            "mean": sum(vals) / len(vals),
        }
    return result


def _stats_out(data: List[List[float]], params: List[str]) -> dict:
    """
    从输出数据区（8 列，前两列是 lat/lon）里提取六参数统计量。
    """
    result = {}
    for idx, p in enumerate(params):
        col = 2 + idx  # 前两列是 lat/lon
        vals = [row[col] for row in data]
        result[p] = {
            "min": min(vals),
            "max": max(vals),
            "mean": sum(vals) / len(vals),
        }
    return result