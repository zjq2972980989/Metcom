# metcom_product/reader.py
"""
MetCom Standard Product —— 读取器

FastAPI 只调用本模块，不自己解析 TXT。

对外接口：
    list_sources(root)                → 列出所有数据源及其时间分辨率
    list_products(root)               → 列出所有产品记录
    query_products(...)               → 按条件查询产品
    read_header(path)                 → 只读 Header
    read_product(path)                → 读完整产品（dict）
    read_product_dataframe(path)      → 读完整产品（DataFrame）
    read_product_by_id(...)           → 通过 (source, region, res, date, hour) 读
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Optional, Union

from metcom_product.standard import (
    PRODUCT_VERSION, PARAMETERS, UNITS,
    SOURCES, REGIONS,
    STANDARD_ROOT, DEMO_ROOT, INDEX_FILE,
    parse_filename,
)


# ============================================================
# 1. 列数据源
# ============================================================
def list_sources(root: Optional[Union[str, Path]] = None) -> list:
    """
    列出所有数据源及时间分辨率信息（供前端构建时间轴）。

    返回：
        [
            {"name": "ERA5",    "dir_name": "output_ERA5",
             "time_resolution": "1h", "hours": [0,1,...,23]},
            ...
        ]
    """
    if root is None:
        root = STANDARD_ROOT
    index = _load_index(root)
    result = []
    for name, info in SOURCES.items():
        hours = _hours_from_index(index, name)
        result.append({
            "name": name,
            "dir_name": info["dir_name"],
            "time_resolution": info["time_resolution"],
            "hours": hours,
            "hours_per_day": len(hours),
        })
    return result


# ============================================================
# 2. 列出产品
# ============================================================
def list_products(root: Optional[Union[str, Path]] = None) -> list:
    """列出索引里所有产品记录。"""
    if root is None:
        root = STANDARD_ROOT
    index = _load_index(root)
    return index.get("products", [])


# ============================================================
# 3. 按条件查询
# ============================================================
def query_products(source: Optional[str] = None,
                   region: Optional[str] = None,
                   resolution: Optional[str] = None,
                   date: Optional[str] = None,
                   hour: Optional[int] = None,
                   root: Optional[Union[str, Path]] = None) -> list:
    """
    按条件查询产品。

    date: '20240101'（8 位纯数字）
    hour: 0 / 6 / 12 / 18 / ...
    """
    if root is None:
        root = STANDARD_ROOT
    products = list_products(root)
    out = []
    for item in products:
        if source     and item["source"]     != source:     continue
        if region     and item["region"]     != region:     continue
        if resolution and item["resolution"] != resolution: continue
        if date       and item["date"]       != date:       continue
        if hour is not None and hour not in item["hours"]:  continue
        out.append(item)
    return out


# ============================================================
# 4. 读 Header
# ============================================================
def read_header(path: Union[str, Path]) -> dict:
    """
    只读 Header，返回 dict。
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"产品文件不存在: {path}")

    meta = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.startswith("!"):
                break
            body = line.lstrip("!").strip()
            if not body:
                continue
            if ":" in body:
                k, v = body.split(":", 1)
                meta[k.strip()] = v.strip()
    return meta


# ============================================================
# 5. 读完整产品（dict 形式）
# ============================================================
def read_product(path: Union[str, Path]) -> dict:
    """
    读完整产品，返回 dict。

    返回：
        {
            "metadata": {...},   # Header
            "columns": ["latitude","longitude","PWV","WVD","Tm","ZTD","ZHD","ZWD"],
            "data": [[...], [...]],
            "shape": (n_rows, 8),
        }
    """
    path = Path(path)
    metadata = read_header(path)

    # 标准产品：列名已经在 Header 里
    # Header 最后一行是 "! latitude longitude PWV WVD Tm ZTD ZHD ZWD"
    # 数据区从第一个非 ! 行开始，全部是数据
    data_lines = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("!"):
                continue
            data_lines.append(line)

    if not data_lines:
        raise ValueError(f"产品文件无数据区: {path}")

    # 列名从 Header 的 Parameters 字段取
    params = metadata.get("Parameters", "PWV WVD Tm ZTD ZHD ZWD")
    columns = ["latitude", "longitude"] + params.split()

    # 所有非 ! 行都是数据
    rows = [ln.split() for ln in data_lines]

    # 校验列数
    for i, r in enumerate(rows):
        if len(r) != len(columns):
            raise ValueError(
                f"第 {i+1} 行列数 {len(r)} != 期望 {len(columns)}: {r}"
            )

    # 转 float
    data = []
    for r in rows:
        data.append([float(x) for x in r])

    return {
        "metadata": metadata,
        "columns": columns,
        "data": data,
        "shape": (len(data), len(columns)),
    }


# ============================================================
# 6. 读完整产品（DataFrame 形式）
# ============================================================
def read_product_dataframe(path: Union[str, Path]):
    """
    读完整产品，返回 (metadata, DataFrame)。
    需要 pandas；未安装则抛 ImportError。
    """
    try:
        import pandas as pd
    except ImportError as e:
        raise ImportError("需要安装 pandas: pip install pandas") from e

    result = read_product(path)
    df = pd.DataFrame(result["data"], columns=result["columns"])
    return result["metadata"], df


# ============================================================
# 7. 通过 ID 读
# ============================================================
def read_product_by_id(source: str,
                       region: str,
                       resolution: str,
                       date: str,
                       hour: int,
                       root: Optional[Union[str, Path]] = None,
                       as_dataframe: bool = False):
    """
    通过 (source, region, resolution, date, hour) 读取产品。

    date: '20240101'
    hour: 0 / 6 / ...
    """
    if root is None:
        root = STANDARD_ROOT
    products = query_products(source, region, resolution, date, hour, root)
    if not products:
        raise FileNotFoundError(
            f"未找到产品: {source}/{region}/{resolution}/{date}/H{hour:02d}"
        )
    item = products[0]

    target_rel = None
    for rel in item["files"]:
        meta = parse_filename(Path(rel).name)
        if meta["hour"] == hour:
            target_rel = rel
            break
    if target_rel is None:
        raise FileNotFoundError(f"索引里没有 H{hour:02d} 的文件")

    path = Path(root) / target_rel
    if as_dataframe:
        return read_product_dataframe(path)
    return read_product(path)


# ============================================================
# 内部工具
# ============================================================
def _load_index(root: Union[str, Path]) -> dict:
    """加载 root 下的 index.json；不存在则返回空索引。"""
    p = Path(root) / "index.json"
    if not p.exists():
        return {"version": PRODUCT_VERSION, "updated": None, "products": []}
    return json.loads(p.read_text(encoding="utf-8"))


def _hours_from_index(index: dict, source: str) -> list:
    """从索引里反推某数据源实际有的小时。"""
    all_hours = set()
    for item in index.get("products", []):
        if item["source"] == source:
            all_hours.update(item["hours"])
    return sorted(all_hours)