"""标准产品仓库访问层（阶段 4）。

产品由同学交付的 ``metcom_product`` 包生成与解析，Web 只做四件事：
定位仓库根 -> 调 reader -> 组装响应 -> 下载文件。

本模块是 Web 侧唯一的路径事实来源：

* 所有相对路径都通过 :func:`resolve_rel` 归一化，索引里历史遗留的
  ``ERA5\\BTH\\...``（Windows 反斜杠）与 ``ERA5/BTH/...`` 都能正确解析，
  因此在 Windows / Linux 上行为一致。
* 只读取磁盘上真实存在、且出现在 ``index.json`` 里的文件，
  不猜测数据、不伪造时次。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional

from metcom_product.reader import read_product as reader_read_product
from metcom_product.standard import PARAMETERS, PRODUCT_VERSION, SOURCES, UNITS

# 交付包里的数据源代号是产品侧命名（ERA5 / MERRA2 / ...）。
# Web 侧其他接口用的小写 key 在这里做一次映射，避免前端出现两套名字。
SOURCE_ALIASES = {
    "era5": "ERA5",
    "merra2": "MERRA2",
    "merra-2": "MERRA2",
    "cra40": "CRA40",
    "crav15": "CRAV15",
    "crav1.5": "CRAV15",
    "cmameso": "CMAMESO",
    "cma-meso": "CMAMESO",
}

# 单次预览返回的数据行上限（数据是"1 格点 1 行"，整份文件可能上千行）。
DEFAULT_PREVIEW_ROWS = 200


def normalize_source(value: Optional[str]) -> Optional[str]:
    """把前端/其他接口的数据源写法统一成产品侧代号；空值原样返回。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.upper() in SOURCES:
        return text.upper()
    return SOURCE_ALIASES.get(text.lower(), text)


def is_demo_root(root: Path) -> bool:
    """判定仓库根是否为演示数据目录（目录结构约定：.../demo）。"""
    return "demo" in Path(root).parts


def normalize_rel(rel: Any) -> Path:
    """把索引里的相对路径（可能是反斜杠）转成当前平台的 Path。"""
    return Path(str(rel).replace("\\", "/"))


def load_index(root: Path) -> dict[str, Any]:
    """读取仓库根的 index.json；不存在或损坏时返回空索引。"""
    index_file = Path(root) / "index.json"
    empty: dict[str, Any] = {"version": PRODUCT_VERSION, "updated": None, "products": []}
    if not index_file.is_file():
        return empty
    try:
        payload = json.loads(index_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    if not isinstance(payload, dict):
        return empty
    payload.setdefault("products", [])
    return payload


def resolve_rel(root: Path, rel: Any) -> Path:
    """仓库根 + 索引相对路径 -> 磁盘绝对路径（跨平台）。"""
    return Path(root) / normalize_rel(rel)


def list_roots(roots: Iterable[Path]) -> list[tuple[Path, bool]]:
    """过滤出真实存在的仓库根，返回 [(路径, 是否演示数据)]，正式目录在前。"""
    seen: set[str] = set()
    result: list[tuple[Path, bool]] = []
    for raw in roots:
        root = Path(raw)
        key = str(root).lower()
        if key in seen or not root.is_dir():
            continue
        seen.add(key)
        result.append((root, is_demo_root(root)))
    return result


def _existing_files(root: Path, rel_items: Iterable[Any]) -> list[str]:
    """索引里的相对路径 -> 实际存在且文件名合法的产品文件（保序去重，正斜杠）。"""
    result: list[str] = []
    for item in rel_items:
        path = resolve_rel(root, item)
        if not path.is_file():
            continue
        text = str(item).replace("\\", "/")
        if text not in result:
            result.append(text)
    return result


def query_products(
    roots: Iterable[Path],
    source: Optional[str] = None,
    region: Optional[str] = None,
    resolution: Optional[str] = None,
    date: Optional[str] = None,
    hour: Optional[int] = None,
) -> list[dict[str, Any]]:
    """跨仓库查询产品记录；只返回文件真实存在的记录（演示数据带 is_demo）。"""
    wanted_source = normalize_source(source)
    result: list[dict[str, Any]] = []

    for root, demo in list_roots(roots):
        for item in load_index(root).get("products", []):
            if not isinstance(item, dict):
                continue
            if wanted_source and item.get("source") != wanted_source:
                continue
            if region and item.get("region") != region:
                continue
            if resolution and item.get("resolution") != resolution:
                continue
            if date and item.get("date") != date:
                continue

            hours = sorted(int(h) for h in item.get("hours", []))
            if hour is not None and hour not in hours:
                continue

            files = _existing_files(root, item.get("files", []))
            if not files:
                continue

            result.append({
                "_root": root,
                "source": item.get("source"),
                "region": item.get("region"),
                "resolution": item.get("resolution"),
                "date": item.get("date"),
                "hours": hours,
                "files": files,
                "file_count": len(files),
                "is_demo": demo,
            })

    result.sort(key=lambda row: (str(row["source"]), str(row["date"]), row["is_demo"]))
    return result


def _hour_suffix(hour: int) -> str:
    return f".h{int(hour):02d}"


def resolve_product_file(
    roots: Iterable[Path],
    source: Optional[str],
    region: str,
    resolution: str,
    date: str,
    hour: int,
) -> tuple[Path, dict[str, Any]]:
    """定位某个时次的产品文件。

    返回 ``(磁盘路径, 产品记录)``；找不到时抛 ``FileNotFoundError``。
    """
    records = query_products(
        roots, source=source, region=region, resolution=resolution, date=date, hour=hour,
    )
    if not records:
        raise FileNotFoundError(
            f"未找到产品: {source}/{region}/{resolution}/{date}/H{int(hour):02d}",
        )

    for root, _demo in list_roots(roots):
        for rel in records[0]["files"]:
            if not rel.lower().endswith(_hour_suffix(hour)):
                continue
            candidate = resolve_rel(root, rel)
            if candidate.is_file():
                return candidate, records[0]
    raise FileNotFoundError(f"索引里没有 H{int(hour):02d} 的文件")


def read_header(path: Path) -> dict[str, str]:
    """只读产品 Header（不解析数据区）。"""
    from metcom_product.reader import read_header as reader_read_header

    return reader_read_header(Path(path))


def read_data(path: Path, limit: Optional[int] = DEFAULT_PREVIEW_ROWS) -> dict[str, Any]:
    """读取产品数据区。

    ``limit`` 为 ``None`` 时返回全部行，否则只返回前 ``limit`` 行（预览用），
    同时给出真实总行数，前端据此显示"已展示 x / 共 y 行"。
    """
    product = reader_read_product(Path(path))
    rows = product["data"]
    total_rows = len(rows)
    if limit is not None and limit >= 0:
        rows = rows[:limit]
    return {
        "metadata": dict(product["metadata"]),
        "columns": list(product["columns"]),
        "data": rows,
        "total_rows": total_rows,
        "returned_rows": len(rows),
        "parameters": list(PARAMETERS),
        "units": dict(UNITS),
    }


__all__ = [
    "DEFAULT_PREVIEW_ROWS",
    "SOURCE_ALIASES",
    "is_demo_root",
    "list_roots",
    "load_index",
    "normalize_rel",
    "normalize_source",
    "query_products",
    "read_data",
    "read_header",
    "resolve_product_file",
    "resolve_rel",
]
