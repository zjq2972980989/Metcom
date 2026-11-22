"""标准产品业务服务（阶段 4）。

路由层只调用这里；本模块负责把 ``product/paths.py`` 的结果整理成
"只描述真实存在数据"的响应结构。产品由 metcom_product 读取，Web 不重算。

约定：

* 只有磁盘上存在、且出现在对应 index.json 里的文件才会出现在响应里；
* 演示数据（web/data/products/demo）与正式产品（web/data/products/standard）
  永远分开标注 ``is_demo``，界面必须据此提示，演示数据不得当正式产品展示；
* 产品仓库为空时返回空列表，不报错、不造数据。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
import tempfile
import zipfile

from config import get_settings
from metcom_product.standard import SOURCES
from product import paths


def product_roots() -> list[Path]:
    """当前生效的标准产品仓库根目录（正式在前，演示在后）。"""
    return list(get_settings().standard_product_roots)


def _roots_for(include_demo: bool) -> list[Path]:
    roots = product_roots()
    if include_demo:
        return roots
    return [root for root in roots if not paths.is_demo_root(root)]


def _file_entry(root: Path, rel: str) -> dict[str, Any]:
    """把索引相对路径转成响应里的文件条目（大小取自磁盘真实文件）。"""
    path = paths.resolve_rel(root, rel)
    name = Path(rel).name
    suffix = name.rsplit(".", 1)[-1]
    hour = int(suffix[1:]) if suffix[:1].lower() == "h" and suffix[1:].isdigit() else -1
    return {
        "path": rel,
        "filename": name,
        "hour": hour,
        "size_bytes": path.stat().st_size if path.is_file() else 0,
    }


def _entry(record: dict[str, Any]) -> dict[str, Any]:
    """仓库记录 -> 响应条目（去掉内部字段 _root）。"""
    root = Path(record["_root"])
    return {
        "source": record["source"],
        "region": record["region"],
        "resolution": record["resolution"],
        "date": record["date"],
        "hours": record["hours"],
        "file_count": record["file_count"],
        "is_demo": bool(record["is_demo"]),
        "files": [_file_entry(root, rel) for rel in record["files"]],
    }


def list_products(
    source: str | None = None,
    date: str | None = None,
    region: str | None = None,
    resolution: str | None = None,
    include_demo: bool = True,
) -> list[dict[str, Any]]:
    """产品目录（可按数据源 / 区域 / 分辨率 / 日期过滤）。"""
    records = paths.query_products(
        _roots_for(include_demo),
        source=source, region=region, resolution=resolution, date=date,
    )
    return [_entry(record) for record in records]


def product_detail(
    source: str,
    region: str,
    resolution: str,
    date: str,
    include_demo: bool = True,
) -> dict[str, Any] | None:
    """某个数据源某天的详情（含每个时次的文件）。"""
    records = paths.query_products(
        _roots_for(include_demo),
        source=source, region=region, resolution=resolution, date=date,
    )
    return _entry(records[0]) if records else None


def list_sources(include_demo: bool = True) -> list[dict[str, Any]]:
    """数据源清单：只列出真实存在产品的数据源及其实际区域/分辨率/日期/时次。"""
    catalog: dict[str, dict[str, Any]] = {}
    for record in list_products(include_demo=include_demo):
        bucket = catalog.setdefault(record["source"], {
            "regions": set(),
            "resolutions": set(),
            "dates": set(),
            "hours": set(),
            "product_count": 0,
            "is_demo": True,
        })
        bucket["regions"].add(record["region"])
        bucket["resolutions"].add(record["resolution"])
        bucket["dates"].add(record["date"])
        bucket["hours"].update(record["hours"])
        bucket["product_count"] += 1
        bucket["is_demo"] = bucket["is_demo"] and record["is_demo"]

    result: list[dict[str, Any]] = []
    for name, meta in SOURCES.items():
        bucket = catalog.get(name)
        if not bucket:
            continue
        result.append({
            "source": name,
            "dir_name": meta["dir_name"],
            "time_resolution": meta["time_resolution"],
            "regions": sorted(bucket["regions"]),
            "resolutions": sorted(bucket["resolutions"]),
            "dates": sorted(bucket["dates"]),
            "hours": sorted(bucket["hours"]),
            "product_count": bucket["product_count"],
            "is_demo": bucket["is_demo"],
        })
    return result


def preview_data(
    source: str,
    region: str,
    resolution: str,
    date: str,
    hour: int,
    include_demo: bool = True,
    limit: int | None = paths.DEFAULT_PREVIEW_ROWS,
) -> dict[str, Any]:
    """读取某个时次的产品（Header + 数据行）；找不到时抛 FileNotFoundError。"""
    path, record = paths.resolve_product_file(
        _roots_for(include_demo), source=source, region=region,
        resolution=resolution, date=date, hour=hour,
    )
    payload = paths.read_data(path, limit=limit)
    return {
        "source": record["source"],
        "region": region,
        "resolution": resolution,
        "date": date,
        "hour": int(hour),
        "is_demo": bool(record["is_demo"]),
        "filename": path.name,
        **payload,
    }


def download_path(
    source: str,
    region: str,
    resolution: str,
    date: str,
    hour: int,
    include_demo: bool = True,
) -> Path:
    """定位产品文件供下载；找不到时抛 FileNotFoundError。"""
    path, _record = paths.resolve_product_file(
        _roots_for(include_demo), source=source, region=region,
        resolution=resolution, date=date, hour=hour,
    )
    return path


def archive_filename(source: str, region: str, resolution: str, date: str) -> str:
    """按现有产品命名规则生成日期级 ZIP 文件名。"""
    return f"MetCom_{source}_{region}_{resolution}_{date}.zip"


def archive_path(
    source: str,
    region: str,
    resolution: str,
    date: str,
    include_demo: bool = True,
) -> Path:
    """把某数据源 / 区域 / 分辨率 / 日期的全部时次原样打包。

    ZIP 只作为传输容器：其中保持仓库原有的相对目录和 ``MetCom_*.Hxx``
    文件名，不新增或改写任何产品文件；产品元数据仍保留在每个文件 Header 中。
    """
    records = paths.query_products(
        _roots_for(include_demo), source=source, region=region,
        resolution=resolution, date=date,
    )
    if not records:
        raise FileNotFoundError(
            f"未找到产品: {source}/{region}/{resolution}/{date}",
        )

    record = records[0]
    root = Path(record["_root"]).resolve()
    entries: list[tuple[str, Path]] = []
    digest = hashlib.sha256(str(root).encode("utf-8"))
    total_bytes = 0
    for rel in record["files"]:
        path = paths.resolve_rel(root, rel).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            continue
        stat = path.stat()
        arcname = Path(str(rel).replace("\\", "/")).as_posix()
        entries.append((arcname, path))
        total_bytes += stat.st_size
        digest.update(f"{arcname}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode("utf-8"))

    if not entries:
        raise FileNotFoundError(
            f"未找到产品: {source}/{region}/{resolution}/{date}",
        )

    settings = get_settings()
    if total_bytes > settings.product_zip_max_mb * 1024 * 1024:
        raise ValueError(
            f"产品数据超过 {settings.product_zip_max_mb} MB，暂不支持打包下载。"
        )

    archive_dir = settings.cache_dir / "product_archives"
    stem = Path(archive_filename(source, region, resolution, date)).stem
    archive = archive_dir / f"{stem}-{digest.hexdigest()[:16]}.zip"
    if archive.is_file():
        return archive

    archive_dir.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b", prefix=".metcom-product-", suffix=".zip.tmp",
            dir=archive_dir, delete=False,
        ) as handle:
            temporary = Path(handle.name)
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED,
            compresslevel=6, allowZip64=True,
        ) as bundle:
            for arcname, path in entries:
                bundle.write(path, arcname=arcname)
        os.replace(temporary, archive)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)
    return archive


def index_updated() -> dict[str, str | None]:
    """各仓库根索引的生成时间（前端"数据版本"提示用）。"""
    result: dict[str, str | None] = {}
    for root, demo in paths.list_roots(product_roots()):
        try:
            payload = json.loads((root / "index.json").read_text(encoding="utf-8"))
            updated = payload.get("updated")
        except (OSError, ValueError):
            updated = None
        result["demo" if demo else "standard"] = updated
    return result
