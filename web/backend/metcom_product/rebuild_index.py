# metcom_product/rebuild_index.py
"""
扫描产品仓库，重建 index.json。

用法（在 CMD 里跑）：
    py -3.14 -m metcom_product.rebuild_index --root products/demo
    py -3.14 -m metcom_product.rebuild_index --root products/standard

作用：
    - 扫描 root 下所有 MetCom_*.H* 文件
    - 从文件名解析出 source/region/resolution/date/hour
    - 生成 index.json
"""

from __future__ import annotations
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from metcom_product.standard import (
    PRODUCT_VERSION, PARAMETERS, UNITS, SOURCES,
    STANDARD_ROOT, DEMO_ROOT,
    parse_filename,
)


def rebuild_index(root):
    """
    扫描 root 下所有产品文件，重建 index.json。

    返回：生成的 index 字典
    """
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(f"目录不存在: {root}")

    # 用 (source, region, resolution, date) 作为唯一键
    products = {}

    for f in sorted(root.rglob("MetCom_*.H*")):
        try:
            meta = parse_filename(f.name)
        except (TypeError, ValueError) as exc:
            print(f"[跳过] 文件名无法解析: {f.name} ({exc})")
            continue

        key = (meta["source"], meta["region"],
               meta["resolution"], meta["date"])

        if key not in products:
            products[key] = {
                "source":     meta["source"],
                "region":     meta["region"],
                "resolution": meta["resolution"],
                "date":       meta["date"],
                "hours":      [],
                "files":      [],
            }

        entry = products[key]

        if meta["hour"] not in entry["hours"]:
            entry["hours"].append(meta["hour"])
            entry["hours"].sort()

        rel = str(f.relative_to(root))
        if rel not in entry["files"]:
            entry["files"].append(rel)
            entry["files"].sort()

    # 排序：先按 source，再按 date
    product_list = sorted(
        products.values(),
        key=lambda x: (x["source"], x["date"])
    )

    index = {
        "version": PRODUCT_VERSION,
        "updated": datetime.now(timezone.utc).isoformat(),
        "sources": {
            name: {
                "dir_name":        info["dir_name"],
                "time_resolution": info["time_resolution"],
            }
            for name, info in SOURCES.items()
        },
        "products": product_list,
    }

    # 写回
    out_file = root / "index.json"
    out_file.write_text(
        json.dumps(index, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"[rebuild] 扫描目录: {root}")
    print(f"[rebuild] 产品记录: {len(product_list)} 条")
    for p in product_list:
        print(f"  {p['source']} / {p['region']} / {p['resolution']} / "
              f"{p['date']} / {len(p['hours'])} 个时次")
    print(f"[rebuild] 索引已写入: {out_file}")

    return index


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(DEMO_ROOT),
                    help="产品仓库根目录（默认 products/demo）")
    args = ap.parse_args()

    rebuild_index(args.root)


if __name__ == "__main__":
    main()
