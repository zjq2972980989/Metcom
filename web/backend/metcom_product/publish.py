# metcom_product/publish.py
"""
一键上新：把 benchmarks 下的新 TXT 发布到产品仓库。

增量模式：已经存在的产品跳过，只转新的。

用法（在 CMD 里跑）：

    # 单个目录
    py -3.14 -m metcom_product.publish ^
        --txt-dir "benchmarks/output_ERA5/2024001" ^
        --source ERA5 ^
        --region BTH ^
        --resolution 0p125 ^
        --root products/demo

    # 整个 benchmarks（自动识别数据源，增量）
    py -3.14 -m metcom_product.publish ^
        --benchmarks "C:\\Users\\32992\\Desktop\\METCOM zhang\\benchmarks" ^
        --region BTH ^
        --resolution 0p125 ^
        --root products/demo

    # 加 --dry-run 只列不转
    # 加 --force 强制覆盖已存在产品
"""

from __future__ import annotations
import argparse
import sys
from pathlib import Path

from metcom_product.standard import (
    SOURCES, DEMO_ROOT, BENCHMARKS_ROOT,
    get_product_dir, make_filename,
)
from metcom_product.converter import (
    convert_one_txt, parse_metcom_filename,
)
from metcom_product.rebuild_index import rebuild_index


# ============================================================
# 辅助：检查某产品是否已存在
# ============================================================
def _product_exists(root, source, region, resolution, date, hour):
    """检查 root 下是否已有 (source, region, resolution, date, hour) 的产品文件。"""
    out_dir = get_product_dir(source, region, resolution, date, root=root)
    filename = make_filename(source, region, resolution, date, hour)
    return (out_dir / filename).is_file()


# ============================================================
# 单个目录上新
# ============================================================
def publish_dir(txt_dir, source, region, resolution, root,
                dry_run=False, force=False):
    """
    把一个目录下的所有 TXT 转成标准产品，放进 root。
    已存在且未加 --force 的跳过。
    """
    txt_dir = Path(txt_dir)
    root = Path(root)

    if not txt_dir.is_dir():
        raise NotADirectoryError(f"TXT 目录不存在: {txt_dir}")

    txt_files = sorted(txt_dir.glob("ztd_*.txt"))
    if not txt_files:
        print(f"[publish] 目录下没有 ztd_*.txt: {txt_dir}")
        return 0, 0, 0, []

    print(f"[publish] 目录: {txt_dir}")
    print(f"[publish] 数据源: {source} / 区域: {region} / "
          f"分辨率: {resolution}")
    print(f"[publish] 找到 {len(txt_files)} 个 TXT")
    print("-" * 60)

    if dry_run:
        for f in txt_files:
            try:
                date, hour, _ = parse_metcom_filename(f)
            except Exception:
                print(f"  [skip] {f.name}: 文件名无法解析")
                continue
            exists = _product_exists(root, source, region, resolution,
                                     date, hour)
            tag = "[已存在]" if exists else "[新]"
            print(f"  {tag} {f.name}")
        return 0, 0, 0, []

    ok, skip, fail = 0, 0, 0
    produced = []

    for f in txt_files:
        try:
            date, hour, _ = parse_metcom_filename(f)
        except Exception as e:
            print(f"  [fail] {f.name}: 解析文件名失败: {e}")
            fail += 1
            continue

        if not force:
            if _product_exists(root, source, region, resolution, date, hour):
                skip += 1
                continue

        try:
            out_file = convert_one_txt(
                f,
                source=source,
                region=region,
                resolution=resolution,
                output_root=str(root),
                update_idx=False,
            )
            produced.append(out_file)
            ok += 1
            print(f"  [ok]  {f.name}")
            print(f"        → {out_file.relative_to(root)}")
        except Exception as e:
            fail += 1
            print(f"  [fail] {f.name}: {e}")

    print("-" * 60)
    print(f"[publish] 新增 {ok} 个，跳过 {skip} 个，失败 {fail} 个")

    return ok, skip, fail, produced


# ============================================================
# 整个 benchmarks 上新（自动识别数据源，增量）
# ============================================================
def publish_benchmarks(benchmarks_root, region, resolution, root,
                       dry_run=False, force=False):
    """
    扫描 benchmarks 下所有 output_* 目录，自动识别数据源并转换。
    增量：已存在的产品跳过。
    """
    benchmarks_root = Path(benchmarks_root)
    if not benchmarks_root.is_dir():
        raise NotADirectoryError(f"benchmarks 不存在: {benchmarks_root}")

    total_ok, total_skip, total_fail = 0, 0, 0

    for source, info in SOURCES.items():
        src_dir = benchmarks_root / info["dir_name"]
        if not src_dir.is_dir():
            print(f"[跳过] {source}: 目录不存在 {src_dir}")
            continue

        for date_dir in sorted(src_dir.iterdir()):
            if not date_dir.is_dir():
                continue

            print()
            print("=" * 60)
            print(f"[{source}] {date_dir.name}")
            print("=" * 60)

            ok, skip, fail, _ = publish_dir(
                txt_dir=date_dir,
                source=source,
                region=region,
                resolution=resolution,
                root=root,
                dry_run=dry_run,
                force=force,
            )
            total_ok += ok
            total_skip += skip
            total_fail += fail

    print()
    print("=" * 60)
    print(f"[publish_all] 新增 {total_ok} 个，"
          f"跳过 {total_skip} 个，失败 {total_fail} 个")

    # 全部转完后，统一重建一次索引
    if total_ok > 0:
        print()
        print("[publish_all] 统一重建索引...")
        rebuild_index(root)
    else:
        print("[publish_all] 无新增，不重建索引")

    print("=" * 60)

    return total_ok, total_skip, total_fail


# ============================================================
# CLI
# ============================================================
def main():
    ap = argparse.ArgumentParser("publish")
    ap.add_argument("--txt-dir", default=None,
                    help="单目录模式：含 ztd_*.txt 的目录")
    ap.add_argument("--source", default=None,
                    choices=list(SOURCES.keys()),
                    help="单目录模式：数据源代号")
    ap.add_argument("--benchmarks", default=None,
                    help="整个 benchmarks 模式：benchmarks 根目录")
    ap.add_argument("--region", default="BTH")
    ap.add_argument("--resolution", default="0p125")
    ap.add_argument("--root", default=str(DEMO_ROOT))
    ap.add_argument("--dry-run", action="store_true",
                    help="只列不转")
    ap.add_argument("--force", action="store_true",
                    help="强制覆盖已存在产品（默认跳过）")
    args = ap.parse_args()

    if args.benchmarks:
        publish_benchmarks(
            benchmarks_root=args.benchmarks,
            region=args.region,
            resolution=args.resolution,
            root=args.root,
            dry_run=args.dry_run,
            force=args.force,
        )
    elif args.txt_dir and args.source:
        publish_dir(
            txt_dir=args.txt_dir,
            source=args.source,
            region=args.region,
            resolution=args.resolution,
            root=args.root,
            dry_run=args.dry_run,
            force=args.force,
        )
    else:
        print("用法：")
        print()
        print("  单目录模式：")
        print('    py -3.14 -m metcom_product.publish --txt-dir "<目录>" '
              '--source ERA5 --region BTH --resolution 0p125 '
              '--root products/demo')
        print()
        print("  整个 benchmarks 模式：")
        print('    py -3.14 -m metcom_product.publish '
              '--benchmarks "<benchmarks 根目录>" '
              '--region BTH --resolution 0p125 '
              '--root products/demo')
        sys.exit(1)


if __name__ == "__main__":
    main()