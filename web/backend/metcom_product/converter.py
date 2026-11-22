# metcom_product/converter.py
"""
MetCom Standard Product —— 转换器

把 METCOM 原始 TXT 转成标准产品。
"""

from __future__ import annotations
import re
from pathlib import Path

from metcom_product.standard import (
    SOURCES, REGIONS, PARAMETERS,
    make_filename, make_header, make_epoch_string,
    get_product_dir, update_index,
    STANDARD_ROOT, DEMO_ROOT,
    BENCHMARKS_ROOT,
    parse_resolution,
)


# ============================================================
# 1. 读 METCOM 原始 TXT
# ============================================================
def load_metcom_txt(txt_path):
    """
    读 METCOM 的 TXT 文件。

    返回：
        rows: list of dict，每个 dict 是 {列名: 值}
        columns: list，列名顺序
    """
    txt_path = Path(txt_path)
    if not txt_path.is_file():
        raise FileNotFoundError(f"TXT 文件不存在: {txt_path}")

    lines = txt_path.read_text(encoding="utf-8").strip().splitlines()
    if len(lines) < 2:
        raise ValueError(f"TXT 文件内容太少: {txt_path}")

    header_line = lines[0].strip()
    columns = header_line.split()

    rows = []
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        values = line.split()
        if len(values) != len(columns):
            raise ValueError(
                f"TXT 第 {len(rows) + 2} 行列数 {len(values)} != "
                f"表头列数 {len(columns)}: {txt_path}"
            )
        row = {}
        for col, val in zip(columns, values):
            row[col] = val
        rows.append(row)

    return rows, columns


# ============================================================
# 2. 从文件名提取时间
# ============================================================
def parse_metcom_filename(txt_path):
    """
    从 METCOM 文件名提取信息。

    文件名格式：ztd_YYYYMMDDHH_sit_{source}.txt
    返回：
        date: "20240101"
        hour: 0
        source: "ERA5"
    """
    name = Path(txt_path).stem
    parts = name.split("_")
    if len(parts) < 4:
        raise ValueError(f"文件名格式错误: {txt_path}")

    yyyymmddhh = parts[1]
    date = yyyymmddhh[:8]
    hour = int(yyyymmddhh[8:10])

    src_lower = parts[3]
    src_map = {
        "era5":    "ERA5",
        "merra2":  "MERRA2",
        "cra40":   "CRA40",
        "crav15":  "CRAV15",
        "cmameso": "CMAMESO",
    }
    source = src_map.get(src_lower, src_lower.upper())

    return date, hour, source


# ============================================================
# 3. 生成标准产品内容
# ============================================================
def build_product_content(rows, source, region, resolution,
                          date, hour, lon_range, lat_range):
    """
    生成标准产品的完整文本（Header + 数据区）。
    """
    epoch = make_epoch_string(date, hour)
    header = make_header(source, region, resolution,
                         epoch, lon_range, lat_range)

    data_lines = []
    for row in rows:
        lat = float(row["latitude"])
        lon = float(row["longitude"])

        vals = []
        for p in PARAMETERS:
            val = None
            for col in row:
                col_clean = re.sub(r"\(.*?\)", "", col)
                if col_clean == p:
                    val = float(row[col])
                    break
            if val is None:
                raise KeyError(f"找不到参数 {p} 在行 {row}")
            vals.append(val)

        # 经纬度保留 3 位小数，兼容 0.125° 网格
        line = f"{lat:.3f} {lon:.3f} " + " ".join(
            [f"{v:.6f}" for v in vals]
        )
        data_lines.append(line)

    return header + "\n" + "\n".join(data_lines) + "\n"


# ============================================================
# 4. 转换一个 TXT
# ============================================================
def convert_one_txt(txt_path, source=None, region="BTH",
                    resolution="0p125", output_root=None,
                    update_idx=True):
    """
    转换一个 METCOM TXT 成标准产品。

    参数：
        txt_path:   原始 TXT 路径
        source:     数据源代号（None = 自动从文件名推断）
        region:     区域代号（如 BTH）
        resolution: 分辨率字符串（如 0p125）
        output_root: 输出根目录（默认用 DEMO_ROOT）
        update_idx: 是否更新索引

    返回：
        生成的标准产品文件路径
    """
    txt_path = Path(txt_path)

    # 从文件名提取
    date, hour, src_from_name = parse_metcom_filename(txt_path)
    if source is None:
        source = src_from_name

    # 读 TXT
    rows, columns = load_metcom_txt(txt_path)
    if not rows:
        raise ValueError(f"TXT 无数据: {txt_path}")

    # 区域范围
    if region in REGIONS:
        lon_range = REGIONS[region]["lon_range"]
        lat_range = REGIONS[region]["lat_range"]
    else:
        lats = [float(r["latitude"]) for r in rows]
        lons = [float(r["longitude"]) for r in rows]
        lon_range = (int(min(lons)), int(max(lons)))
        lat_range = (int(min(lats)), int(max(lats)))

    # 生成内容
    content = build_product_content(
        rows, source, region, parse_resolution(resolution),
        date, hour, lon_range, lat_range,
    )

    # 校验：转换前后必须一致
    from metcom_product.validator import validate_conversion, ValidationError
    try:
        validate_conversion(
            rows_in=rows,
            content_out=content,
            source=source,
            region=region,
            params=PARAMETERS,
        )
    except ValidationError as e:
        raise RuntimeError(f"转换校验失败: {e}")

    # 输出路径
    if output_root is None:
        output_root = DEMO_ROOT

    out_dir = get_product_dir(source, region, resolution,
                              date, root=output_root)
    out_dir.mkdir(parents=True, exist_ok=True)

    filename = make_filename(source, region, resolution, date, hour)
    out_file = out_dir / filename

    # 写入
    out_file.write_text(content, encoding="utf-8")

    # 更新索引
    if update_idx:
        rel_path = str(out_file.relative_to(output_root))
        update_index(source, region, resolution,
                     date, hour, rel_path,
                     index_file=str(Path(output_root) / "index.json"))

    return out_file


# ============================================================
# 5. 批量转换一个目录
# ============================================================
def convert_batch(txt_dir, source=None, region="BTH",
                  resolution="0p125", output_root=None):
    """
    批量转换一个目录下的所有 TXT。
    """
    txt_dir = Path(txt_dir)
    if not txt_dir.is_dir():
        raise NotADirectoryError(f"目录不存在: {txt_dir}")

    txt_files = sorted(txt_dir.glob("ztd_*.txt"))
    if not txt_files:
        raise FileNotFoundError(f"目录里没有 ztd_*.txt: {txt_dir}")

    results = []
    for txt in txt_files:
        try:
            out = convert_one_txt(
                txt, source=source, region=region,
                resolution=resolution, output_root=output_root,
            )
            results.append(out)
            print(f"  转换完成: {out.name}")
        except Exception as e:
            print(f"  转换失败 {txt.name}: {e}")

    return results


# ============================================================
# 6. 一键转 BTH Demo（5 种数据源）
# ============================================================
def convert_bth_demo():
    """
    把 benchmarks 里的 5 种数据源，全部转成 BTH Demo 标准产品。
    """
    if not Path(BENCHMARKS_ROOT).is_dir():
        raise FileNotFoundError(f"benchmarks 目录不存在: {BENCHMARKS_ROOT}")

    print("=" * 60)
    print("开始转换 BTH Demo")
    print("=" * 60)

    total = 0
    for source, info in SOURCES.items():
        src_dir = Path(BENCHMARKS_ROOT) / info["dir_name"]
        if not src_dir.is_dir():
            print(f"[跳过] {source}: 目录不存在 {src_dir}")
            continue

        for date_dir in sorted(src_dir.iterdir()):
            if not date_dir.is_dir():
                continue
            print(f"\n[{source}] {date_dir.name}")
            try:
                results = convert_batch(
                    date_dir,
                    source=source,
                    region="BTH",
                    resolution="0p125",
                    output_root=DEMO_ROOT,
                )
                total += len(results)
            except Exception as e:
                print(f"  失败: {e}")

    print("\n" + "=" * 60)
    print(f"总转换: {total} 个文件")
    print("=" * 60)

    return total


if __name__ == "__main__":
    convert_bth_demo()
