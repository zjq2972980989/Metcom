"""Web 层与现有 Python MetCom 计算引擎之间的唯一适配边界。

调用链固定为::

    FastAPI router -> service -> engine.adapter -> metcom（现有引擎）

路由与 service 一律不直接 import metcom，这样引擎内部重构时 Web 层不用跟着改。

阶段一：能力探测（引擎可导入、五个数据源规格可读）。
阶段三：接入真实下载与反演调用，并对外报告每个数据源的**真实**可用能力。
"""

from __future__ import annotations

import importlib
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from config import BACKEND_DIR, PROJECT_ROOT, get_settings

logger = logging.getLogger("metcom.web.engine")

ENGINE_PACKAGE = "metcom"

# 数据源显示顺序（与平台导航、文档描述一致）。
SOURCE_ORDER = ("era5", "merra2", "cra40", "crav15", "cmameso")

SOURCE_DESCRIPTIONS: dict[str, str] = {
    "era5": "ECMWF 第五代再分析资料，全球覆盖，1 小时时间分辨率。",
    "merra2": "NASA GES DISC 压力层三维分析资料（inst6_3d_ana_Np），6 小时一次（00/06/12/18 UTC）。",
    "cra40": "中国第一代全球大气再分析资料 CRA-40。",
    "crav15": "中国全球大气再分析产品 CRA V1.5，0.10° 高空间分辨率。",
    "cmameso": "中国气象局 CMA-MESO 区域模式分析场，中国区域。",
}

# 八个输出参数（单位取自引擎结果文件表头）。
PARAMETERS: tuple[dict[str, str], ...] = (
    {"key": "PWV", "name": "大气可降水量", "unit": "m",
     "description": "整层水汽积分，引擎结果列 PWV(m)"},
    {"key": "WVD", "name": "水汽密度", "unit": "g/m³",
     "description": "逐层水汽密度，引擎结果列 WVD"},
    {"key": "Tm", "name": "大气加权平均温度", "unit": "K",
     "description": "逐层后缀积分比值，引擎结果列 Tm(K)"},
    {"key": "ZTD", "name": "对流层总延迟", "unit": "m",
     "description": "静力延迟 + 湿延迟，引擎结果列 ZTD(m)"},
    {"key": "ZHD", "name": "对流层静力延迟", "unit": "m",
     "description": "含 Saastamoinen 顶层修正，引擎结果列 ZHD(m)"},
    {"key": "ZWD", "name": "对流层湿延迟", "unit": "m",
     "description": "水汽贡献的延迟，引擎结果列 ZWD(m)"},
    {"key": "Ps", "name": "地表气压", "unit": "hPa",
     "description": "站点高度处的气压，引擎结果列 Ps(hpa)"},
    {"key": "vapor_pressure", "name": "水汽压", "unit": "hPa",
     "description": "站点高度处的水汽分压，引擎结果列 vapor_pressure(hpa)"},
)


@dataclass(frozen=True)
class EngineStatus:
    """引擎探测结果。"""

    available: bool
    version: str | None = None
    path: str | None = None
    detail: str = ""


@lru_cache(maxsize=1)
def probe_engine() -> EngineStatus:
    """探测计算引擎是否可用；任何异常都收敛为 available=False，不让 Web 层崩。"""
    try:
        module = importlib.import_module(ENGINE_PACKAGE)
    except Exception as exc:                       # noqa: BLE001 - 探测接口不做异常分类
        return EngineStatus(available=False, detail=f"导入 {ENGINE_PACKAGE} 失败：{exc}")

    file = getattr(module, "__file__", None)
    return EngineStatus(
        available=True,
        version=getattr(module, "__version__", None),
        path=str(file) if file else None,
        detail="计算引擎可导入",
    )


def engine_info() -> dict[str, Any]:
    """引擎信息（供 /api/engine 与关于平台页使用）。"""
    status = probe_engine()
    return {
        "available": status.available,
        "package": ENGINE_PACKAGE,
        "version": status.version,
        "path": status.path,
        "detail": status.detail,
    }


def list_sources() -> list[dict[str, Any]]:
    """五个数据源的对外描述（含真实可用能力）；引擎不可用时返回空列表。"""
    if not probe_engine().available:
        return []

    specs = _engine_specs()
    if not specs:
        return []

    capabilities = source_capabilities()
    ordered = [key for key in SOURCE_ORDER if key in specs]
    ordered += [key for key in specs if key not in SOURCE_ORDER]

    result: list[dict[str, Any]] = []
    for key in ordered:
        spec = specs[key]
        lat_res, lon_res = spec.spatial_resolution
        cap = capabilities.get(key, {})
        result.append({
            "key": spec.key,
            "name": spec.name,
            "time_resolution": spec.time_resolution,
            "spatial_resolution": f"{lat_res:g}° × {lon_res:g}°",
            "levels": spec.levels,
            "description": SOURCE_DESCRIPTIONS.get(key, ""),
            "available": bool(cap.get("available")),
            "calculate_available": bool(cap.get("calculate_available")),
            "download_available": bool(cap.get("download_available")),
            "downloader_available": bool(cap.get("downloader_available")),
            "note": str(cap.get("note", "")),
            "has_complete_data": bool(cap.get("has_complete_data")),
            "complete_time_count": int(cap.get("complete_time_count") or 0),
            "incomplete_time_count": int(cap.get("incomplete_time_count") or 0),
            "available_time_range": cap.get("available_time_range"),
            "diagnostics": list(cap.get("diagnostics") or []),
        })
    return result


def list_parameters() -> list[dict[str, str]]:
    """八个输出参数的对外描述（顺序固定：PWV/WVD/Tm/ZTD/ZHD/ZWD/Ps/vapor_pressure）。"""
    return [dict(item) for item in PARAMETERS]


# --------------------------------------------------------------------------- #
# 阶段 3：真实下载 / 反演接入
# --------------------------------------------------------------------------- #

# Web 参数键 → 引擎结果文件列名（任务中心与结果页都以此为准，不另造命名）
PARAM_COLUMNS: dict[str, str] = {
    "PWV": "PWV(m)",
    "WVD": "WVD",
    "Tm": "Tm(K)",
    "ZTD": "ZTD(m)",
    "ZHD": "ZHD(m)",
    "ZWD": "ZWD(m)",
    "Ps": "Ps(hpa)",
    "vapor_pressure": "vapor_pressure(hpa)",
}

# 每个结果文件都保留的站点信息列（属于站点基准，不能被取消）
BASE_COLUMNS: tuple[str, ...] = ("name", "latitude", "longitude", "height(m)")

# 结果文件格式：与引擎 ``metcom.utils.io`` 的真实写入器一一对应。
#   txt / dat → ``write_result_file``（同一实现：定宽文本；dat 是引擎原生扩展名）
#   mat       → ``save_result_mat``（struct 变量 ``Result_<源名>``，12 个字段）
OUTPUT_FORMATS: tuple[str, ...] = ("txt", "dat", "mat")
_TEXT_FORMATS: tuple[str, ...] = ("txt", "dat")

# 核心引擎已具备真实下载器的数据源（代码能力）：
#   era5   → metcom.downloader.runner（CDS API）
#   merra2 → metcom.downloader.merra2（NASA GES DISC / Earthdata Login）
DOWNLOADER_SOURCES: tuple[str, ...] = ("era5", "merra2")

# Web 在线下载（TaskService）已接入、可提交 POST /api/download 的源。
# 注意：能提交 != 当前机器能下载；是否真的具备下载条件由 download_available() 逐源判定
# （ERA5 需 CDS 凭据，MERRA-2 需 NASA Earthdata 凭据），缺凭据时提交会被明确拒绝。
DOWNLOADABLE_SOURCES: tuple[str, ...] = ("era5", "merra2")

_DATA_SUFFIXES = (".nc", ".grib", ".grb", ".grib2")
_DOWNLOAD_WORKERS = 2
_DOWNLOAD_MIN_MB = 220.0
_CAPABILITY_TTL_SECONDS = 10.0
_CAPABILITY_SCAN_LIMIT = 4000


@lru_cache(maxsize=1)
def download_options() -> dict[str, Any]:
    """已验证的 ERA5 pressure-level 下载配置（只读，直接取自下载器常量）。

    第一版只接受这套已经验证成功的配置：37 个气压层、4 个变量、NetCDF、不使用 grid。
    Web 层只做校验与转发，绝不修改 metcom.downloader 里的这些参数。
    """
    from metcom.downloader.runner import PRESSURE_LEVELS, VARIABLES

    return {
        "dataset": "reanalysis-era5-pressure-levels",
        "variables": tuple(VARIABLES),
        "pressure_levels": tuple(PRESSURE_LEVELS),
        "formats": ("nc",),
        "grid": None,  # 显式不使用 grid：保留 ERA5 全球 0.25° 原始网格
    }


@lru_cache(maxsize=1)
def merra2_download_options() -> dict[str, Any]:
    """已验证的 MERRA-2 下载配置（只读，直接取自 MERRA-2 下载器常量）。

    与 ERA5 完全独立：不涉及 CDS、气压层清单、变量清单、grid，也没有 220MB 最小
    文件限制。MERRA-2 官方为一天一文件（含 00/06/12/18 UTC 四个时次），格式 nc4。
    """
    from metcom.downloader.merra2 import (
        MERRA2_PRODUCT,
        MERRA2_STREAM,
        MERRA2_VALID_HOURS,
    )

    return {
        "dataset": "M2I6NPANA",
        "product": MERRA2_PRODUCT,
        "stream": MERRA2_STREAM,
        "valid_hours": tuple(MERRA2_VALID_HOURS),
        "output_format": "nc4",
    }


class AdapterError(RuntimeError):
    """适配层错误。message 面向用户，可直接作为任务失败原因写入任务记录。"""


# ------------------------------------------------------------------ 能力探测 --


def _engine_specs() -> dict[str, Any]:
    """引擎数据源规格（唯一事实来源：metcom.sources.SOURCES）。"""
    try:
        return dict(importlib.import_module(f"{ENGINE_PACKAGE}.sources").SOURCES)
    except Exception:                              # noqa: BLE001
        return {}


def _has_engine_data(root: Path) -> bool:
    """有界扫描：目录下是否存在引擎可读的数据文件（nc / grib）。"""
    if not root.is_dir():
        return False
    seen = 0
    for _, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in filenames:
            seen += 1
            if name.lower().endswith(_DATA_SUFFIXES):
                return True
            if seen >= _CAPABILITY_SCAN_LIMIT:
                return False
    return False


def _cdsapirc_path() -> Path | None:
    """定位 CDS API 凭据文件；找不到说明 ERA5 下载尚未配置。"""
    settings = get_settings()
    candidates: list[Path] = []
    if settings.metcom_cdsapirc:
        candidates.append(Path(settings.metcom_cdsapirc))
    candidates += [BACKEND_DIR / ".cdsapirc", Path.home() / ".cdsapirc"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _merra2_credentials_available() -> bool:
    """MERRA-2 的 NASA Earthdata 凭据是否已配置（只返回布尔值，不暴露凭据）。"""
    try:
        from metcom.downloader.merra2 import earthdata_credentials_available

        return bool(earthdata_credentials_available())
    except Exception:                               # noqa: BLE001 - 探测失败视为未配置
        return False


def download_available(source_key: str) -> tuple[bool, str]:
    """该数据源在当前机器上是否真的可以下载；不可下载时返回面向用户的原因。

    与 ``source_capabilities()`` 的 ``downloader_available``（代码能力）严格区分：
    这里判定的是"当前机器是否已经具备下载条件"（凭据是否配置）。因此：

        MERRA-2 有下载器但无 Earthdata 凭据 → downloader_available=True、
        download_available=False，POST /api/download 必须被拒绝。

    只返回布尔值与提示文本，绝不返回或记录任何凭据内容。
    """
    key = str(source_key or "").strip().lower()
    if key not in DOWNLOADER_SOURCES:
        return False, f"暂未提供 {key or '该数据源'} 的下载器，无法在线下载。"
    if key == "era5":
        if _cdsapirc_path() is None:
            return False, "未配置 CDS API 凭据（.cdsapirc），无法下载 ERA5 资料。"
        return True, ""
    if key == "merra2":
        if not _merra2_credentials_available():
            return False, "MERRA-2 下载需要配置 NASA Earthdata 账号凭据，当前机器尚未配置。"
        return True, ""
    return False, "该数据源暂不支持在线下载。"


def validate_merra2_time(moment: datetime) -> datetime:
    """校验 MERRA-2 时次（00/06/12/18 UTC 整点）；失败时抛 AdapterError。"""
    from metcom.downloader.merra2 import Merra2DownloadError
    from metcom.downloader.merra2 import validate_merra2_time as _validate

    try:
        return _validate(moment)
    except Merra2DownloadError as exc:
        raise AdapterError(str(exc)) from exc


# 需要按数据源命名规则扫描的多变量 GRIB 数据源（能力探测 / 诊断用）。
_GRIB_SCAN_SOURCES: tuple[str, ...] = ("cra40", "crav15", "cmameso")


def _scan_grib_capability(key: str, data_root: Path) -> dict[str, Any]:
    """按 Reader 的文件命名规则轻量扫描一个 GRIB 数据源目录（不解码数据）。

    只根据**变量文件组是否完整**判定数据是否可处理：目录不存在、只有无关文件、
    命名不符、只有部分变量文件都判为不可计算，并给出可读诊断。扫描本身异常时
    作为诊断返回（不吞掉、也不判为可用）。绝不把不完整目录误判为可用。
    """
    from metcom.retrieval.readers.grib_sources import scan_source_root

    root = Path(data_root)
    if not root.is_dir():
        return {
            "has_complete_data": False,
            "complete_time_count": 0,
            "incomplete_time_count": 0,
            "available_time_range": None,
            "diagnostics": ["数据目录不存在"],
            "scan_error": False,
        }

    try:
        scan = scan_source_root(key, root)
    except Exception as exc:                        # noqa: BLE001 - 诊断而非可用
        return {
            "has_complete_data": False,
            "complete_time_count": 0,
            "incomplete_time_count": 0,
            "available_time_range": None,
            "diagnostics": [f"扫描数据目录失败：{exc}"],
            "scan_error": True,
        }

    complete = scan.complete_times
    incomplete = scan.incomplete_times
    time_range = scan.available_time_range
    diagnostics: list[str] = []
    if scan.matched_files == 0:
        diagnostics.append("目录中没有符合命名规则的数据文件")
    if incomplete:
        samples = []
        for ts in incomplete[:5]:
            missing = "/".join(scan.missing_tags(ts))
            samples.append(f"{ts}（缺少 {missing}）" if missing else ts)
        suffix = "" if len(incomplete) <= 5 else f" 等 {len(incomplete)} 个时次"
        diagnostics.append("存在变量文件组不完整的时次：" + "、".join(samples) + suffix)
    duplicates = scan.duplicate_times
    if duplicates:
        samples = []
        for ts in duplicates[:5]:
            samples.append(f"{ts}（{'；'.join(scan.duplicate_detail(ts))}）")
        suffix = "" if len(duplicates) <= 5 else f" 等 {len(duplicates)} 个时次"
        diagnostics.append("存在同一变量多个文件的时次（无法确定唯一文件）："
                           + "、".join(samples) + suffix)
    if scan.matched_files and not complete:
        diagnostics.append("没有变量文件组完整的时次，无法计算")
    if scan.truncated:
        diagnostics.append("数据文件过多，扫描已在安全上限处截断，统计可能不完整")

    return {
        "has_complete_data": bool(complete),
        "complete_time_count": len(complete),
        "incomplete_time_count": len(incomplete),
        "available_time_range": list(time_range) if time_range else None,
        "diagnostics": diagnostics,
        "scan_error": False,
    }


_capability_cache: tuple[float, dict[str, dict[str, Any]]] | None = None


def source_capabilities() -> dict[str, dict[str, Any]]:
    """每个数据源的真实可用能力（短 TTL 缓存，避免每次 /api/meta 都扫目录）。"""
    global _capability_cache
    now = time.monotonic()
    if _capability_cache is not None and now - _capability_cache[0] < _CAPABILITY_TTL_SECONDS:
        return _capability_cache[1]

    settings = get_settings()
    has_site = settings.engine_site_file.is_file()
    cdsapirc = _cdsapirc_path()
    merra2_creds = _merra2_credentials_available()

    capabilities: dict[str, dict[str, Any]] = {}
    for key, spec in _engine_specs().items():
        source_root = settings.engine_data_root / key
        has_data = _has_engine_data(source_root)
        has_egm = (settings.engine_egm_dir / spec.egm_file).is_file()
        if key in _GRIB_SCAN_SOURCES:
            scan_info = _scan_grib_capability(key, source_root)
        else:
            # ERA5 / MERRA-2 沿用既有语义：有任意可读数据文件即视为有数据，
            # 不改变这两个数据源的可用性判定（回归保护）。
            scan_info = {
                "has_complete_data": has_data,
                "complete_time_count": 0,
                "incomplete_time_count": 0,
                "available_time_range": None,
                "diagnostics": [],
                "scan_error": False,
            }
        has_complete_data = bool(scan_info["has_complete_data"])
        can_calculate = has_complete_data and has_egm and has_site
        has_downloader = key in DOWNLOADER_SOURCES

        if key == "era5":
            can_download = has_downloader and cdsapirc is not None
        elif key == "merra2":
            can_download = has_downloader and merra2_creds
        else:
            can_download = False

        notes: list[str] = []
        if not has_data:
            notes.append("本地暂无该数据源的原始资料")
        if key in _GRIB_SCAN_SOURCES and not has_complete_data:
            notes.extend(scan_info["diagnostics"])
        if not has_egm:
            notes.append("缺少 EGM2008 大地水准面改正文件")
        if not has_site:
            notes.append("缺少站点列表文件")
        if has_downloader and not can_download:
            if key == "era5":
                notes.append("未配置 CDS API 凭据")
            elif key == "merra2":
                notes.append("已具备 MERRA-2 下载器，但未配置 NASA Earthdata Login 凭据")

        capabilities[key] = {
            "available": can_calculate or can_download,
            "calculate_available": can_calculate,
            "download_available": can_download,
            "downloader_available": has_downloader,
            "note": "；".join(notes),
            "has_complete_data": has_complete_data,
            "complete_time_count": int(scan_info["complete_time_count"]),
            "incomplete_time_count": int(scan_info["incomplete_time_count"]),
            "available_time_range": scan_info["available_time_range"],
            "diagnostics": list(scan_info["diagnostics"]),
        }

    _capability_cache = (now, capabilities)
    return capabilities


# ------------------------------------------------------------------ 调用引擎 --


def engine_time(moment: datetime) -> str:
    """Web 时间 → 引擎时间串（YYYYMMDDHH）。"""
    return moment.strftime("%Y%m%d%H")


def _require_source(source_key: str) -> Any:
    spec = source_spec(source_key)
    if spec is None:
        raise AdapterError(f"不支持的数据源：{source_key}。")
    return spec


def source_spec(source_key: str) -> Any | None:
    """按 key 取引擎数据源规格；不存在时返回 None（供业务层做参数校验）。"""
    return _engine_specs().get(str(source_key or "").strip().lower())


def resolve_egm_path(spec: Any) -> Path:
    """Resolve the configured EGM path using the retrieval module's sole rule."""
    return get_settings().engine_egm_dir / spec.egm_file


def _engine_inputs(spec: Any, site_file: Path | str | None = None) -> tuple[Path, Path, Path]:
    """解析一次反演所需的三个引擎输入（数据目录 / EGM2008 文件 / 站点列表）。

    ``site_file`` 为 None 时使用平台默认站点文件（与历史行为一致）；
    传入路径时使用该文件（自定义测站场景由业务层写好后传入）。
    """
    settings = get_settings()
    data_path = settings.engine_data_root / spec.key
    egm_dir = settings.engine_egm_dir
    egm_file = resolve_egm_path(spec)
    site_path = Path(site_file) if site_file is not None else settings.engine_site_file

    if not data_path.is_dir():
        raise AdapterError(f"{spec.name} 暂无可用的原始资料，请先获取数据。")
    if not egm_dir.is_dir():
        raise AdapterError(
            f"{spec.name} 的 EGM2008 目录配置无效，预期目录：{egm_dir}。"
        )
    if not egm_file.is_file():
        raise AdapterError(
            f"{spec.name} 缺少 EGM2008 改正文件，预期路径：{egm_file}。"
        )
    if not site_path.is_file():
        raise AdapterError("缺少站点列表文件，无法反演。")
    return data_path, egm_file, site_path


def _project_parameters(path: Path, parameters: Sequence[str]) -> list[str]:
    """把引擎结果裁剪为用户选择的输出参数（站点信息列保留，数值原样保留）。"""
    from metcom.utils.fmt import fmt_fixed_width

    keep = list(BASE_COLUMNS) + [PARAM_COLUMNS[key] for key in parameters]
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise AdapterError("引擎输出为空，未生成结果。")

    header = lines[0].split()
    try:
        picked = [header.index(column) for column in keep]
    except ValueError as exc:
        raise AdapterError("引擎结果列与参数选择不一致，无法生成结果。") from exc

    if picked == list(range(len(header))):
        return [header[index] for index in picked]

    specs = [(4, None)] + [(20, None)] * (len(picked) - 1)
    projected = [fmt_fixed_width([header[index] for index in picked], specs)]
    for line in lines[1:]:
        cells = line.split()
        if len(cells) != len(header):
            raise AdapterError("引擎结果行格式异常，无法生成结果。")
        projected.append(fmt_fixed_width([cells[index] for index in picked], specs))

    path.write_text("\n".join(projected) + "\n", encoding="utf-8")
    return [header[index] for index in picked]


_MAT_COLUMN_TO_FIELD: dict[str, str] | None = None


def _mat_column_to_field() -> dict[str, str]:
    """结果列名 → 引擎 .mat 结构字段名（唯一事实来源：metcom.utils.io）。

    ``_MAT_FIELD_ORDER`` 的元素是 ``(mat 字段名, 引擎结果 dict 键)``；站名列
    ``name`` 是唯一不经过 ``_RESULT_KEY_TO_COLUMN`` 的字段，按同名列直接对应。
    """
    global _MAT_COLUMN_TO_FIELD
    if _MAT_COLUMN_TO_FIELD is None:
        from metcom.utils.io import _MAT_FIELD_ORDER, _RESULT_KEY_TO_COLUMN

        _MAT_COLUMN_TO_FIELD = {"name": "name"}
        _MAT_COLUMN_TO_FIELD.update({
            _RESULT_KEY_TO_COLUMN[key]: field
            for field, key in _MAT_FIELD_ORDER
            if key in _RESULT_KEY_TO_COLUMN
        })
    return _MAT_COLUMN_TO_FIELD


def _project_mat_parameters(path: Path, parameters: Sequence[str]) -> list[str]:
    """把 .mat 结果裁剪为用户选择的字段（纯输出裁剪，不重算任何数值）。

    只保留站点信息列与所选参数列；字段名与变量名沿用引擎 ``save_result_mat``
    的格式契约，未选中的字段直接不写入新结构（不是写入 NaN 占位）。
    """
    from scipy.io import loadmat, savemat

    keep = list(BASE_COLUMNS) + [PARAM_COLUMNS[key] for key in parameters]
    mapping = _mat_column_to_field()
    try:
        fields = [mapping[column] for column in keep]
    except KeyError as exc:
        raise AdapterError("引擎结果列与参数选择不一致，无法生成结果。") from exc

    try:
        payload = loadmat(str(path))
    except Exception as exc:                        # noqa: BLE001 - 统一收敛为引擎错误
        raise AdapterError("结果文件不是有效的 .mat 结果，无法生成结果。") from exc

    names = [key for key in payload if not key.startswith("__")]
    if len(names) != 1:
        raise AdapterError("结果文件结构异常，无法生成结果。")
    struct_name = names[0]
    source = payload[struct_name]
    present = getattr(getattr(source, "dtype", None), "names", None)
    if not present:
        raise AdapterError("结果文件结构异常，无法生成结果。")
    if any(field not in present for field in fields):
        raise AdapterError("引擎结果列与参数选择不一致，无法生成结果。")

    projected = np.empty(source.shape, dtype=[(field, "O") for field in fields])
    for field in fields:
        projected[field] = source[field]
    savemat(str(path), {struct_name: projected})
    return list(keep)


def run_calculation(
    *,
    source_key: str,
    start_time: datetime,
    end_time: datetime,
    output_dir: Path,
    parameters: Sequence[str],
    output_format: str | None = None,
    site_file: Path | str | None = None,
) -> dict[str, Any]:
    """真实反演：调用 metcom.retrieval.pipeline.run_retrieval。

    ``site_file`` 省略（None）时使用平台默认站点文件，保持既有调用行为 100% 不变。
    ``output_format`` 省略（None）时使用平台配置的默认格式；仅在引擎真实支持的
    格式内取值，未知格式直接拒绝，绝不静默替换成别的格式。
    """
    spec = _require_source(source_key)
    data_path, egm_file, resolved_site_file = _engine_inputs(spec, site_file)
    fmt = (output_format or get_settings().metcom_output_format).lstrip(".").lower()
    if fmt not in OUTPUT_FORMATS:
        raise AdapterError(
            "不支持的结果文件格式：" + str(output_format) + "；可选 "
            + " / ".join(OUTPUT_FORMATS) + "。"
        )

    unknown = [key for key in parameters if key not in PARAM_COLUMNS]
    if unknown:
        raise AdapterError("不支持的输出参数：" + "、".join(unknown) + "。")

    try:
        from metcom.retrieval.pipeline import RetrievalOptions, run_retrieval
    except Exception as exc:                        # noqa: BLE001
        raise AdapterError("计算引擎不可用，无法执行参数计算。") from exc

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    options = RetrievalOptions(
        source_key=spec.key,
        data_path=str(data_path),
        output_path=str(output_dir),
        output_format=fmt,
        site_file=str(resolved_site_file),
        start_time=engine_time(start_time),
        end_time=engine_time(end_time),
        spatial_resolution=(float(spec.spatial_resolution[0]),
                            float(spec.spatial_resolution[1])),
        time_resolution=spec.time_resolution,
        egm_file=str(egm_file),
    )

    logger.info("MetCom adapter called: retrieval %s %s..%s",
                spec.key, options.start_time, options.end_time)
    try:
        stats = run_retrieval(options)
    except FileNotFoundError as exc:
        raise AdapterError(str(exc)) from exc

    files = sorted(p for p in output_dir.rglob("*") if p.is_file())
    if not files:
        raise AdapterError(
            f"所选时间范围 {options.start_time}..{options.end_time} 内未找到可用的 "
            f"{spec.name} 原始资料，未生成结果。"
            f"请确认数据目录中已有对应时次的数据（{spec.file_pattern_doc}）。"
        )

    columns: list[str] = []
    if fmt in _TEXT_FORMATS:
        for path in files:
            columns = _project_parameters(path, parameters)
    elif fmt == "mat":
        for path in files:
            columns = _project_mat_parameters(path, parameters)

    return {
        "stats": dict(stats),
        "files": [str(p) for p in files],
        "output_dir": str(output_dir),
        "columns": columns,
    }


def run_download(
    *,
    source_key: str,
    start_time: datetime,
    end_time: datetime,
    output_dir: Path,
    output_format: str = "nc",
) -> dict[str, Any]:
    """真实下载：调用 metcom.downloader.runner.run_download。"""
    spec = _require_source(source_key)
    if spec.key not in DOWNLOADABLE_SOURCES:
        raise AdapterError(f"{spec.name} 暂不支持在线下载。")

    fmt = (output_format or "nc").lstrip(".").strip().lower()
    if fmt not in download_options()["formats"]:
        raise AdapterError(f"暂不支持的下载格式：{output_format}。")

    cdsapirc = _cdsapirc_path()
    if cdsapirc is None:
        raise AdapterError("未配置 CDS API 凭据（.cdsapirc），无法下载 ERA5 资料。")

    try:
        from metcom.downloader.runner import run_download as _run_download
    except Exception as exc:                        # noqa: BLE001
        raise AdapterError("数据下载模块不可用，无法执行下载。") from exc

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cfg = {
        "start_time": engine_time(start_time),
        "end_time": engine_time(end_time),
        "format": fmt,
        "output_dir": str(output_dir),
        "use_idm": False,
        "download_workers": _DOWNLOAD_WORKERS,
        "min_file_size_mb": _DOWNLOAD_MIN_MB,
        "request_retries": 3,
        "download_retries": 5,
        "cdsapirc_path": str(cdsapirc),
    }

    logger.info("MetCom adapter called: download %s %s..%s",
                spec.key, cfg["start_time"], cfg["end_time"])
    stats = _run_download(cfg, config_dir=PROJECT_ROOT / "configs")

    files = sorted(
        p for p in output_dir.rglob("*")
        if p.is_file() and p.name.lower().endswith(_DATA_SUFFIXES)
    )
    if not files:
        raise AdapterError(f"{spec.name} 数据下载失败，未获取到任何数据文件。")

    return {
        "stats": dict(stats),
        "files": [str(p) for p in files],
        "output_dir": str(output_dir),
    }


def run_merra2_download(
    *,
    start_time: datetime,
    end_time: datetime,
    output_dir: Path,
    force: bool = False,
) -> dict[str, Any]:
    """预留接口：真实下载 MERRA-2 inst6_3d_ana_Np（NASA GES DISC / Earthdata Login）。

    与 ERA5 完全独立，不经过 CDS，也不复用 ERA5 downloader。文件按年写入
    ``output_dir/<yyyy>/MERRA2_400.inst6_3d_ana_Np.YYYYMMDD.nc4``。
    未配置 Earthdata 凭据时抛 AdapterError（message 面向用户），绝不伪装成功。
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        from metcom.downloader.merra2 import run_merra2_download as _run_merra2
    except Exception as exc:                        # noqa: BLE001
        raise AdapterError("MERRA-2 下载模块不可用，无法执行下载。") from exc

    logger.info("MetCom adapter called: merra2 download %s..%s", start_time, end_time)
    try:
        return _run_merra2(
            start_time=start_time,
            end_time=end_time,
            output_dir=output_dir,
            force=force,
        )
    except Exception as exc:                        # noqa: BLE001 - message 面向用户
        raise AdapterError(str(exc)) from exc
