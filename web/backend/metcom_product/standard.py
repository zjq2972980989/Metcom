# metcom_product/standard.py
"""
MetCom Standard Product v1.0 —— 格式定义

独立包，支持 5 种数据源：
    ERA5 / MERRA2 / CRA40 / CRAV15 / CMAMESO
"""

from __future__ import annotations
import json
import re
from datetime import datetime, timezone
from pathlib import Path

# ============================================================
# 产品版本
# ============================================================
PRODUCT_VERSION = "1.0"
PRODUCT_TYPE = "STANDARD"

# ============================================================
# 六个核心参数
# ============================================================
PARAMETERS = ["PWV", "WVD", "Tm", "ZTD", "ZHD", "ZWD"]

UNITS = {
    "PWV": "m",
    "WVD": "g/m^3",
    "Tm":  "K",
    "ZTD": "m",
    "ZHD": "m",
    "ZWD": "m",
}

# ============================================================
# 5 种数据源
# ============================================================
SOURCES = {
    "ERA5":    {"dir_name": "output_ERA5",    "time_resolution": "1h"},
    "MERRA2":  {"dir_name": "output_Merra2",  "time_resolution": "6h"},
    "CRA40":   {"dir_name": "output_CRA40",   "time_resolution": "6h"},
    "CRAV15":  {"dir_name": "output_CRAV15",  "time_resolution": "1h"},
    "CMAMESO": {"dir_name": "output_CMAMESO", "time_resolution": "3h"},
}

# ============================================================
# 区域
# ============================================================
REGIONS = {
    "China": {
        "lon_range": (73, 135),
        "lat_range": (18, 54),
    },
    "BTH": {
        "lon_range": (113, 120),
        "lat_range": (36, 43),
    },
}

# ============================================================
# 产品根目录
# ============================================================
PRODUCT_ROOT = "products"
STANDARD_ROOT = f"{PRODUCT_ROOT}/standard"
DEMO_ROOT = f"{PRODUCT_ROOT}/demo"
INDEX_FILE = f"{STANDARD_ROOT}/index.json"

# METCOM 项目路径（读取原始 TXT 用）
METCOM_ROOT = r"C:\Users\32992\Desktop\METCOM zhang"
BENCHMARKS_ROOT = f"{METCOM_ROOT}/benchmarks"


# ============================================================
# 文件命名
# ============================================================
def parse_resolution(value: str) -> tuple[float, float]:
    """把产品分辨率标识解析为 Header 使用的两个方向间距。

    支持 ``0p125``、``1x1`` 和 ``0.125 x 0.125`` 三种既有写法；
    无法识别时明确报错，避免 Header 静默写入错误的分辨率。
    """
    text = str(value).strip().lower().replace("×", "x")
    if not text:
        raise ValueError("分辨率不能为空")

    if "x" in text:
        parts = [part.strip() for part in text.split("x")]
        if len(parts) != 2:
            raise ValueError(f"分辨率格式错误: {value}")
    else:
        parts = [text, text]

    try:
        lon_res, lat_res = (
            float(re.sub(r"^([0-9]+)p([0-9]+)$", r"\1.\2", part))
            for part in parts
        )
    except ValueError as exc:
        raise ValueError(f"分辨率格式错误: {value}") from exc
    if lon_res <= 0 or lat_res <= 0:
        raise ValueError(f"分辨率必须为正数: {value}")
    return lon_res, lat_res


def make_filename(source: str, region: str, resolution: str,
                  date: str, hour: int) -> str:
    """
    生成标准产品文件名。

    Example:
        make_filename("ERA5", "China", "1x1", "20250101", 0)
        → "MetCom_ERA5_China_1x1_20250101.H00"
    """
    return f"MetCom_{source}_{region}_{resolution}_{date}.H{hour:02d}"


def parse_filename(filename: str) -> dict:
    """
    从文件名解析元数据。

    Example:
        parse_filename("MetCom_ERA5_China_1x1_20250101.H00")
        → {"source": "ERA5", "region": "China",
           "resolution": "1x1", "date": "20250101", "hour": 0}
    """
    stem = Path(filename).stem    # "MetCom_ERA5_China_1x1_20250101"
    parts = stem.split("_")       # ["MetCom", "ERA5", "China", "1x1", "20250101"]

    if len(parts) != 5 or parts[0] != "MetCom":
        raise ValueError(f"文件名格式错误: {filename}")

    # parts[4] 需要再拆出日期
    # 但 .H00 已经被 Path.stem 去掉了吗？不会！
    # Path("MetCom_..._20250101.H00").stem 仍然是 "MetCom_..._20250101.H00"
    # 因为 .H00 不是"后缀"，只有最后一个点之后才算后缀
    # 所以要手动处理：
    date_str = parts[4]
    if "." in date_str:
        date_str = date_str.split(".")[0]

    # 从完整的 filename 里提取 H 小时
    # 在 filename 里找 ".H"
    h_part = None
    for p in Path(filename).name.split("."):
        if p.startswith("H"):
            h_part = p
            break

    if h_part is None:
        raise ValueError(f"文件名中找不到小时标记: {filename}")

    return {
        "source": parts[1],
        "region": parts[2],
        "resolution": parts[3],
        "date": date_str,
        "hour": int(h_part[1:]),
    }


# ============================================================
# 目录
# ============================================================
def compute_doy(date: str) -> str:
    """'2025-01-01' 或 '20250101' → '2025001'"""
    date = date.replace("-", "")
    dt = datetime.strptime(date, "%Y%m%d")
    doy = dt.timetuple().tm_yday
    return f"{dt.year}{doy:03d}"


def get_product_dir(source: str, region: str, resolution: str,
                    date: str, root: str = None) -> Path:
    """返回某天产品的目录。"""
    if root is None:
        root = STANDARD_ROOT
    date = date.replace("-", "")
    year = date[:4]
    doy = compute_doy(date)
    return Path(root) / source / region / resolution / year / doy


# ============================================================
# Header
# ============================================================
def make_header(source: str, region: str, resolution: tuple,
                epoch: str, lon_range: tuple, lat_range: tuple) -> str:
    """生成标准产品 Header（14 行）"""
    res_str = f"{resolution[0]:g} x {resolution[1]:g}"
    units_str = " ".join([f"{k}[{v}]" for k, v in UNITS.items()])
    params_str = " ".join(PARAMETERS)

    lines = [
        "! MetCom Standard Tropospheric Product",
        f"! Version: {PRODUCT_VERSION}",
        f"! Product_Type: {PRODUCT_TYPE}",
        f"! Source: {source}",
        f"! Region: {region}",
        f"! Resolution: {res_str} degree",
        f"! Epoch: {epoch}",
        "! Time_System: UTC",
        f"! Longitude_Range: {lon_range[0]} {lon_range[1]}",
        f"! Latitude_Range: {lat_range[0]} {lat_range[1]}",
        f"! Parameters: {params_str}",
        f"! Units: {units_str}",
        "!",
        f"! latitude longitude {params_str}",
    ]
    return "\n".join(lines)


def make_epoch_string(date: str, hour: int) -> str:
    """'20250101', 0 → '2025-01-01 00:00 UTC'"""
    date = date.replace("-", "")
    dt = datetime.strptime(date, "%Y%m%d")
    return f"{dt.year}-{dt.month:02d}-{dt.day:02d} {hour:02d}:00 UTC"


# ============================================================
# 索引
# ============================================================
def load_index(index_file: str = None) -> dict:
    """读索引，不存在则返回空索引"""
    if index_file is None:
        index_file = INDEX_FILE
    p = Path(index_file)
    if not p.exists():
        return {"version": PRODUCT_VERSION, "updated": None, "products": []}
    return json.loads(p.read_text(encoding="utf-8"))


def save_index(index: dict, index_file: str = None):
    """保存索引"""
    if index_file is None:
        index_file = INDEX_FILE
    p = Path(index_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(index, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def update_index(source: str, region: str, resolution: str,
                 date: str, hour: int, rel_path: str,
                 index_file: str = None):
    """新增/更新一条产品记录（幂等）"""
    index = load_index(index_file)

    key = (source, region, resolution, date)
    found = None
    for item in index["products"]:
        if (item["source"], item["region"],
            item["resolution"], item["date"]) == key:
            found = item
            break

    if found is None:
        found = {
            "source": source,
            "region": region,
            "resolution": resolution,
            "date": date,
            "hours": [],
            "files": [],
        }
        index["products"].append(found)

    if hour not in found["hours"]:
        found["hours"].append(hour)
        found["hours"].sort()
    if rel_path not in found["files"]:
        found["files"].append(rel_path)
        found["files"].sort()

    index["updated"] = datetime.now(timezone.utc).isoformat()

    save_index(index, index_file)
    return index
