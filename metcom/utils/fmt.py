"""固定宽度文本输出的统一格式化工具。

全软件所有文本类输出（反演结果、格点坐标、时间插值、评估报表）共用本模块，
遵循同一原则：

  - 每列固定宽度、右对齐；
  - 数值列按指定小数位格式化（precision=None 时按字符串右对齐）；
  - 非有限值（NaN/Inf）与 None 输出为右对齐的占位文本（默认 'NaN'）；
  - 内容超过列宽时按需增长（printf 行为），不截断。

列规格为 (width, precision) 二元组；列间分隔符由 sep 指定（通常为空串或
单个空格——定宽列自身已含对齐空位，多数输出无需额外分隔）。
"""

from __future__ import annotations

import math

__all__ = ["fmt_fixed_width"]


def fmt_fixed_width(cells, specs, sep: str = "", nan_text: str = "NaN") -> str:
    """按列规格生成一行固定宽、右对齐文本。

    Parameters
    ----------
    cells:
        单元格值列表。str 按字符串右对齐；数值按列规格格式化；
        None / NaN / Inf 输出 nan_text 右对齐。
    specs:
        每列 (width, precision) 二元组；precision=None 表示文本/整数列
        （按 str(value) 右对齐）。
    sep:
        相邻列之间的分隔符（如 " " 或 "  "；定宽列通常无需分隔）。
    nan_text:
        非有限值占位文本（默认 'NaN'）。

    Returns
    -------
    str: 拼接后的一整行（不含换行符）。

    Raises
    ------
    ValueError:
        单元格数与列规格数不一致。
    """
    cells = list(cells)
    specs = list(specs)
    if len(cells) != len(specs):
        raise ValueError(f"单元格数 {len(cells)} 与列规格数 {len(specs)} 不一致")

    parts: list[str] = []
    for i, (value, (width, precision)) in enumerate(zip(cells, specs)):
        if i > 0 and sep:
            parts.append(sep)
        if value is None:
            text = nan_text
        elif isinstance(value, str):
            text = value
        else:
            v = float(value)
            text = (f"{v:.{precision}f}" if precision is not None
                    else str(value))
            if not math.isfinite(v):
                text = nan_text
        parts.append(text.rjust(width))
    return "".join(parts)
