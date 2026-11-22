"""CMA-MESO / CRA40 / CRA V1.5 读取器（原始 GRIB2 直接读取或预转换 NC）。

对应 MATLAB: METCOM_CMAMESO / METCOM_CRA40 / METCOM_CRAV15 三个模块的
readSingleData.m、find_matching_files.m 与各自 GUI 主循环。

约定（与 MATLAB 一致）：
  - 每时次每变量一个文件；变量名 gh/t/r/q，气压坐标 isobaricInhPa（hPa）；
  - CMAMESO 无比湿文件（函数内由 RH 换算 q）；CRA40/CRAV15 四变量齐全；
  - 纬度翻转（降序→升序）、经度 0~360 映射 [-180,180) 后升序排序；
  - MATLAB 按四列表排序序号配对文件，Python 改按时间戳配对（对齐时等价，
    长度不齐时更稳健）。

相对 MATLAB 流程的改进（经真实 SOMEDATA 数据验证）：
  - 原始 .grib2 文件用 cfgrib 直接读取（filter typeOfLevel=isobaricInhPa，
    等价于原 GribToNcConverter_cfgrib.exe 的转换行为），无需 40GB 级中间 NC；
  - 预转换的 .nc 文件同样支持（走 netCDF4 路径）。

阶段一（Web 三源接入）新增的**纯文件名**能力（不解码任何数据）：
  - ``scan_source_root`` / ``scan_files`` / ``scan_day_folders``：复用本模块
    既有的文件命名规则与变量分组做轻量扫描，供 Web 能力探测与覆盖校验复用，
    避免在 Web 层另写一套不一致的识别规则；
  - ``verify_hour_coverage``：小时级请求的覆盖完整性校验（预期时次、缺失时次、
    变量组不完整时次），不完整时抛出可读异常，调用方不会把部分时次当成功；
  - ``iter_hours(..., start_time=, end_time=)``：10 位 YYYYMMDDHH 边界时仅处理
    闭区间内的时次；仅传日期（或都不传）时保持原有按日行为不变。
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator

import netCDF4
import numpy as np

from metcom.config import parse_yyyymmddhh
from metcom.retrieval.readers.common import (
    apply_lonlat_convention, check_latlon_order, check_level_order,
    read_coord, read_field,
)
from metcom.retrieval.readers.era5 import _hour_bounds
from metcom.retrieval.site_retrieval import TempData

_GRIB_EXTS = (".grib2", ".grb2", ".grb", ".grib")
_GRIB_FILTER = {"typeOfLevel": "isobaricInhPa"}
# indexpath='' 禁用 .idx 索引文件（避免过滤条件变化时复用陈旧索引的副作用）
_GRIB_BACKEND = {"filter_by_keys": _GRIB_FILTER, "indexpath": ""}


@dataclass(frozen=True)
class _GribSourceCfg:
    key: str
    pattern: str              # 文件名正则（不含扩展名，带命名组 tag/ts）
    var_shortnames: dict      # 变量标签 -> 数据变量名（gh/t/r/q）
    n_vars: int               # 每时次文件数（3 或 4）


_CFG = {
    "cmameso": _GribSourceCfg(
        key="cmameso",
        pattern=r"GRAPES3KMORIG_(?P<tag>GPH|RHU|TEM)_100_(?P<ts>\d{10})_CHN_0_0",
        var_shortnames={"GPH": "gh", "RHU": "r", "TEM": "t"},
        n_vars=3,
    ),
    "cra40": _GribSourceCfg(
        key="cra40",
        pattern=r"CRA40_(?P<tag>GPH|RHU|SHU|TEM)_(?P<ts>\d{10})_GLB_0P25_HOUR_V1_0_0",
        var_shortnames={"GPH": "gh", "RHU": "r", "SHU": "q", "TEM": "t"},
        n_vars=4,
    ),
    "crav15": _GribSourceCfg(
        key="crav15",
        # 版本段（14 位 YYYYMMDDHHMMSS）可选：真实样例文件无该段
        # （ART_ATM_GLB_0P10_6HOR_ANAL_2024010100_HGT.nc）；历史命名可能带该段。
        # 时刻仍严格为 10 位 YYYYMMDDHH，变量标签白名单不变。
        pattern=r"ART_ATM_GLB_0P10_6HOR_ANAL_(?P<ts>\d{10})"
                r"(?:_(?P<ver>\d{14}))?_(?P<tag>HGT|RH|SPFH|TMP)",
        var_shortnames={"HGT": "gh", "RH": "r", "SPFH": "q", "TMP": "t"},
        n_vars=4,
    ),
}

_FILENAME_RE = {k: re.compile(v.pattern + r"(?:\.nc|\.grib2|\.grb2|\.grb|\.grib)$")
                for k, v in _CFG.items()}

# 能力探测的有界扫描上限：目录失控时不把整个磁盘读一遍。
_SCAN_FILE_LIMIT = 20000


@dataclass(frozen=True)
class SourceTimeScan:
    """按时次分组的轻量扫描结果（只按文件名识别，不解码任何数据）。"""

    groups: dict[str, dict[str, Path]]   # 时次 → {变量标签: 文件路径}
    n_vars: int                          # 每个完整时次需要的变量文件数
    var_tags: tuple[str, ...]            # 该数据源的全部变量标签
    matched_files: int = 0               # 命中命名规则的文件数
    unrelated_files: int = 0             # 未命中命名规则的文件数
    truncated: bool = False              # 扫描是否因上限被截断
    # 同一时次同一变量出现多个文件：时次 → {变量标签: (全部同名文件,)}。
    # 命名规则不定义文件格式优先级，无法安全择一，故此类时次不参与完整时次判定。
    duplicates: dict[str, dict[str, tuple[Path, ...]]] = field(default_factory=dict)

    @property
    def complete_times(self) -> tuple[str, ...]:
        """变量文件组完整且无重复歧义的时次（升序）。"""
        return tuple(ts for ts, fm in sorted(self.groups.items())
                     if len(fm) == self.n_vars and ts not in self.duplicates)

    @property
    def incomplete_times(self) -> tuple[str, ...]:
        """存在文件但变量文件组不完整的时次（升序）。"""
        return tuple(ts for ts, fm in sorted(self.groups.items()) if len(fm) < self.n_vars)

    @property
    def duplicate_times(self) -> tuple[str, ...]:
        """同一时次同一变量存在多个文件、无法安全择一的时次（升序）。"""
        return tuple(sorted(self.duplicates))

    def duplicate_detail(self, ts: str) -> tuple[str, ...]:
        """某时次重复变量的可读说明（变量标签 + 涉及的文件名）。"""
        return tuple(
            f"{tag}（{'、'.join(p.name for p in paths)}）"
            for tag, paths in sorted(self.duplicates.get(ts, {}).items())
        )

    @property
    def available_time_range(self) -> tuple[str, str] | None:
        """可处理时次的时间范围 (最早, 最晚)；没有完整时次时为 None。"""
        times = self.complete_times
        return (times[0], times[-1]) if times else None

    def missing_tags(self, ts: str) -> tuple[str, ...]:
        """某时次缺失的变量标签。"""
        present = set(self.groups.get(ts, {}))
        return tuple(sorted(set(self.var_tags) - present))


def scan_cfg_files(cfg: _GribSourceCfg, files) -> SourceTimeScan:
    """按数据源命名规则对给定文件列表分组（命名规则与 Reader 完全同源）。

    同一时次同一变量命中多个文件时**不静默覆盖**：全部记录到 ``duplicates``，
    该时次不计入 ``complete_times``，由调用方给出可诊断提示（命名规则不定义
    .nc / .grib2 的优先级，无法安全择一）。
    """
    regex = _FILENAME_RE[cfg.key]
    groups: dict[str, dict[str, Path]] = defaultdict(dict)
    seen: dict[str, dict[str, list[Path]]] = defaultdict(lambda: defaultdict(list))
    matched = unrelated = 0
    for item in files:
        path = Path(item)
        m = regex.match(path.name)
        if m is None:
            unrelated += 1
            continue
        matched += 1
        ts, tag = m.group("ts"), m.group("tag")
        seen[ts][tag].append(path)
        groups[ts].setdefault(tag, path)
    duplicates = {
        ts: {tag: tuple(paths) for tag, paths in tags.items() if len(paths) > 1}
        for ts, tags in seen.items()
    }
    duplicates = {ts: tags for ts, tags in duplicates.items() if tags}
    return SourceTimeScan(
        groups={key: dict(value) for key, value in groups.items()},
        n_vars=cfg.n_vars,
        var_tags=tuple(cfg.var_shortnames),
        matched_files=matched,
        unrelated_files=unrelated,
        duplicates=duplicates,
    )


def scan_files(key: str, files) -> SourceTimeScan:
    """按数据源 key 分组文件列表（能力探测 / 覆盖校验共用入口）。"""
    return scan_cfg_files(_CFG[str(key).strip().lower()], files)


def scan_day_folders(key: str, day_folders) -> SourceTimeScan:
    """扫描若干目录内的全部候选文件（**递归**、按真实路径去重，只按文件名识别）。

    递归是为了兼容两种真实布局：

    - 日目录布局：``<root>/<yyyyMMdd>/…``（CMA-MESO 现状，行为不变）；
    - 年/变量布局：``<root>/<yyyy>/<变量子目录>/…``（CRA40 / CRA V1.5 真实样例）。

    同一文件即使被多个目录分支覆盖也只计一次。
    """
    return scan_files(key, collect_source_files(day_folders))


def _iter_folder_files(folder: Path) -> Iterator[Path]:
    """递归列出目录下的候选文件（不区分数据源，调用方再按命名规则筛选）。"""
    for p in folder.rglob("*"):
        if p.is_file():
            yield p


def collect_source_files(folders) -> list[Path]:
    """收集若干目录下的候选文件：递归、按真实路径去重，避免多分支重复计数。"""
    seen: set[Path] = set()
    files: list[Path] = []
    for folder in folders:
        folder = Path(folder)
        if not folder.is_dir():
            continue
        for path in sorted(_iter_folder_files(folder), key=lambda x: str(x)):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            files.append(path)
    return files


def resolve_scan_folders(base_folder, start_date, end_date) -> list[Path]:
    """请求日期范围内需要扫描的目录，兼容两种真实布局且不重复计数。

    - 日目录布局：``<root>/<yyyyMMdd>/``（CMA-MESO 现状，保持不变）；
    - 年/变量布局：``<root>/<yyyy>/<变量子目录>/``（CRA40 / CRA V1.5 真实样例）。

    年目录由 :func:`collect_source_files` 递归展开，故一次即可覆盖其下全部变量
    子目录；返回列表按真实路径去重，日/年两个分支不会重复计入同一文件。
    根目录不存在时抛 ``NotADirectoryError``（保持既有语义）。
    """
    from metcom.retrieval.pipeline import filter_day_folders

    base = Path(base_folder)
    if not base.is_dir():
        raise NotADirectoryError(f"数据根目录不存在: {base}")

    folders: list[Path] = list(filter_day_folders(base, start_date, end_date))
    known = {p.resolve() for p in folders}
    for year in range(start_date.year, end_date.year + 1):
        year_dir = base / str(year)
        if year_dir.is_dir() and year_dir.resolve() not in known:
            folders.append(year_dir)
            known.add(year_dir.resolve())
    return folders


def scan_source_root(key: str, base_folder, *,
                     file_limit: int = _SCAN_FILE_LIMIT) -> SourceTimeScan:
    """有界地递归扫描数据源根目录，返回按时次分组的扫描结果。

    数据目录不存在或为空时返回空的扫描结果（不抛异常），由调用方决定诊断文案。
    """
    cfg = _CFG[str(key).strip().lower()]
    base = Path(base_folder)
    if not base.is_dir():
        return SourceTimeScan(groups={}, n_vars=cfg.n_vars,
                              var_tags=tuple(cfg.var_shortnames))

    files: list[Path] = []
    truncated = False
    for root, dirnames, filenames in os.walk(base):
        dirnames.sort()
        for name in sorted(filenames):
            files.append(Path(root) / name)
            if len(files) >= file_limit:
                truncated = True
                break
        if truncated:
            break
    return replace(scan_cfg_files(cfg, files), truncated=truncated)


_RESOLUTION_RE = re.compile(r"^\s*(\d+)\s*h", re.IGNORECASE)


def _resolution_step_hours(time_resolution: str) -> int:
    """把 ``"6h"`` 形式的时间分辨率解析为小时步长（无法解析时按 1h）。"""
    m = _RESOLUTION_RE.match(str(time_resolution or ""))
    if m is None:
        return 1
    step = int(m.group(1))
    return step if step > 0 else 1


def expected_hours(time_resolution: str, start_time: str, end_time: str) -> list[str]:
    """请求闭区间内符合数据源时间分辨率的有效时次（YYYYMMDDHH，升序）。

    采样时刻定义为 UTC 整日 0 时起每 ``time_resolution`` 小时；步长取自
    ``metcom.sources.SourceSpec.time_resolution``，与 Web 能力探测对外展示一致。
    """
    y0, m0, d0, h0 = parse_yyyymmddhh(start_time)
    y1, m1, d1, h1 = parse_yyyymmddhh(end_time)
    step = _resolution_step_hours(time_resolution)
    current = datetime(y0, m0, d0, h0)
    end = datetime(y1, m1, d1, h1)
    hours: list[str] = []
    while current <= end:
        if current.hour % step == 0:
            hours.append(current.strftime("%Y%m%d%H"))
        current += timedelta(hours=1)
    return hours


def _coverage_span(start_time, end_time) -> tuple[str, str] | None:
    """把请求边界规范化为覆盖校验用的 10 位 (start, end)。

    - 同为 10 位 YYYYMMDDHH：原样返回（小时级请求，语义不变）；
    - 同为 8 位 YYYYMMDD：扩为整段 [起始 00 时, 结束 23 时]，使日期级请求也按
      数据源采样规则校验请求范围内应有的时次与变量完整性；
    - 两者皆缺省：返回 None（不做校验，保持旧调用语义）；
    - 混合或非法：交由 ``_hour_bounds`` 抛 ``ValueError``（旧语义不变）。
    """
    start_text = "" if start_time is None else str(start_time).strip()
    end_text = "" if end_time is None else str(end_time).strip()
    if (len(start_text) == 8 and len(end_text) == 8
            and start_text.isdigit() and end_text.isdigit()):
        return f"{start_text}00", f"{end_text}23"
    return _hour_bounds(start_time, end_time)


def verify_hour_coverage(key: str, day_folders, *, start_time, end_time,
                         time_resolution: str) -> dict[str, int]:
    """请求覆盖完整性校验（只扫描文件名，不解码数据）。

    生效范围：

    - 10 位 YYYYMMDDHH：按数据源分辨率校验闭区间内应有的时次（原语义）；
    - 8 位 YYYYMMDD：扩为整段日期（00..23 时）后按同一采样规则校验，使日期级
      请求不会把"只处理到部分时次"当作成功；
    - 缺省边界：返回空字典，保持旧调用语义。

    不满足完整性要求时抛 ``FileNotFoundError``（异常类型与返回结构不变），消息
    包含缺失/不完整/重复的时次与缺失变量，供 Web 任务层写入失败原因——绝不把
    部分时次当完整成功。
    """
    cfg = _CFG[str(key).strip().lower()]
    span = _coverage_span(start_time, end_time)
    if span is None:
        return {}
    start_ts, end_ts = span
    expected = expected_hours(time_resolution, start_ts, end_ts)
    scan = scan_day_folders(key, day_folders)

    def in_range(ts: str) -> bool:
        return start_ts <= ts <= end_ts

    present = {ts for ts in scan.groups if in_range(ts)}
    complete = sorted(ts for ts in scan.complete_times if in_range(ts))
    considered = set(expected) if expected else present
    incomplete = sorted(ts for ts in considered
                        if ts in scan.groups and len(scan.groups[ts]) < cfg.n_vars)
    missing = sorted(set(expected) - present)

    problems: list[str] = []
    for ts in incomplete:
        problems.append(
            f"时次 {ts} 变量文件不完整（缺少 {'/'.join(scan.missing_tags(ts))}）"
        )
    for ts in sorted(ts for ts in scan.duplicate_times if ts in considered):
        problems.append(
            f"时次 {ts} 同一变量存在多个文件（{'；'.join(scan.duplicate_detail(ts))}），"
            f"无法确定唯一文件"
        )
    if missing:
        shown = missing[:8]
        extra = "" if len(missing) <= 8 else f" 等 {len(missing)} 个时次"
        problems.append("缺少预期时次 " + "、".join(shown) + extra)
    if not expected and not complete and not problems:
        # expected 为空 ⇒ 请求闭区间内不含任何符合数据源采样规则的时刻（“不符合
        # 采样规则的请求”）。此时不能把原因写成“没有符合命名规则的文件”：文件可能
        # 存在、只是时刻落在采样网格之外，否则会把排查方向引向文件命名。
        step = _resolution_step_hours(time_resolution)
        sample_hours = "/".join(f"{hour:02d}" for hour in range(0, 24, step))
        problems.append(
            f"请求时段 {start_ts}..{end_ts} 内没有任何符合命名规则的 {key} 数据文件，"
            f"且该区间不含符合采样规则的时次（UTC 每日 {sample_hours} 时）"
        )
    if problems:
        raise FileNotFoundError(
            f"{key} 在 {start_ts}..{end_ts} 的数据覆盖不完整："
            + "；".join(problems)
            + f"。数据源时间分辨率为 {time_resolution}，请补齐原始数据后重试。"
        )

    return {
        "expected_time_count": len(expected),
        "complete_time_count": len(complete),
        "incomplete_time_count": len(incomplete),
        "missing_time_count": len(missing),
    }


def _read_var_grib2(path: Path, var_name: str):
    """cfgrib 直接读取 GRIB2 单变量（等压面过滤）→ (lon, lat, lev) + 坐标。"""
    from metcom.retrieval.eccodes_backend import configure, jpeg_decode_hint
    configure()  # 必须在 eccodes/cfgrib 首次导入前注入 vendor 解码库

    import xarray as xr

    try:
        ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs=_GRIB_BACKEND)
    except Exception as e:  # noqa: BLE001 —— JPEG 缺解码器时给出明确指引
        if "Functionality" in str(e) or "JPEG" in str(e):
            raise RuntimeError(jpeg_decode_hint(e)) from e
        raise
    try:
        if var_name not in ds:
            raise KeyError(f"{path.name} 中未找到变量 '{var_name}'（实际: {list(ds.data_vars)}）")
        arr = np.asarray(ds[var_name].values, dtype=np.float64)   # (lev, lat, lon)
        pre = np.asarray(ds["isobaricInhPa"].values, dtype=np.float64).ravel()
        lon = np.asarray(ds["longitude"].values, dtype=np.float64).ravel()
        lat = np.asarray(ds["latitude"].values, dtype=np.float64).ravel()
    finally:
        ds.close()
    # cfgrib 维序 (lev, lat, lon) → (lon, lat, lev)
    arr = np.ascontiguousarray(np.transpose(arr, (2, 1, 0)))
    return arr, pre, lon, lat


def _read_var_nc(path: Path, var_name: str):
    """读取 NC 单变量 → (lon, lat, lev) + 坐标。

    优先 netCDF4；文件为非标准 netCDF 数据模型的 HDF5（如原版
    GribToNcConverter_cfgrib.exe / h5netcdf 的输出，MATLAB ncread 可读）
    时回退 xarray。
    """
    try:
        with netCDF4.Dataset(path, "r") as ds:
            arr = read_field(ds, var_name)                       # (lon, lat, lev)
            pre = read_coord(ds, "isobaricInhPa", "isobaric", "pressure_level", "lev")
            lon = read_coord(ds, "longitude", "lon")
            lat = read_coord(ds, "latitude", "lat")
        return arr, pre, lon, lat
    except OSError:
        return _read_var_nc_xarray(path, var_name)


def _read_var_nc_xarray(path: Path, var_name: str):
    """xarray（h5netcdf）读取回退，输出约定同 netCDF4 路径。"""
    import xarray as xr

    ds = xr.open_dataset(path)
    try:
        if var_name not in ds:
            raise KeyError(f"{path.name} 中未找到变量 '{var_name}'（实际: {list(ds.data_vars)}）")
        da = ds[var_name]
        dims = da.dims
        # 找 lon/lat/lev 维位置，重排为 (lon, lat, lev)
        from metcom.retrieval.readers.common import classify_dims
        order = classify_dims(dims)
        arr = np.asarray(da.values, dtype=np.float64)
        if order is None:
            raise ValueError(f"{path.name} 中 {var_name} 维度无法识别: {dims}")
        lon_ax, lat_ax, lev_ax = order
        arr = np.transpose(arr, (lon_ax, lat_ax, lev_ax))
        pre = np.asarray(ds["isobaricInhPa"].values, dtype=np.float64).ravel()
        lon = np.asarray(ds["longitude"].values, dtype=np.float64).ravel()
        lat = np.asarray(ds["latitude"].values, dtype=np.float64).ravel()
    finally:
        ds.close()
    return arr, pre, lon, lat


def read_time_group(cfg: _GribSourceCfg, file_map: dict[str, Path],
                    levels: int | None = None) -> TempData:
    """读取一个时次的多变量文件组（对应 MATLAB readSingleData 的多文件 cat）。

    levels: 统一截取的气压层数（MATLAB 固定取前 N 层，如 CRA40 为 37）；
    各变量文件的层数可不同（CRA40: GPH/TEM 47 层、RHU 41 层、SHU 37 层），
    以 GPH/HGT（位势高，MATLAB filelist{1}）的气压坐标为参考。
    """
    ref_tag = "GPH" if "GPH" in file_map else ("HGT" if "HGT" in file_map
                                               else sorted(file_map)[0])
    fields: dict[str, np.ndarray] = {}
    pre_check: dict[str, np.ndarray] = {}
    pre_ref = lon_g = lat_g = None
    ref_name = ""

    for tag in [ref_tag] + sorted(set(file_map) - {ref_tag}):
        path = file_map[tag]
        var_name = cfg.var_shortnames[tag]
        if path.suffix.lower() in _GRIB_EXTS:
            arr, pre_i, lon_i, lat_i = _read_var_grib2(path, var_name)
        else:
            arr, pre_i, lon_i, lat_i = _read_var_nc(path, var_name)

        if pre_ref is None:
            pre_ref, lon_g, lat_g, ref_name = pre_i, lon_i, lat_i, path.name
        else:
            if not (np.allclose(lon_i, lon_g) and np.allclose(lat_i, lat_g)):
                raise ValueError(f"{path.name} 经纬度坐标与 {ref_name} 不一致")
        pre_check[var_name] = pre_i
        fields[var_name] = arr

    expected = set(cfg.var_shortnames.values())
    if set(fields) != expected:
        raise ValueError(f"文件组不完整: 缺少 {expected - set(fields)}")

    # 统一截取前 n_lev 层（MATLAB 语义：各变量取前 N 层）。真实数据中各变量
    # 层表可能不是位势高层表的前缀（如 CRA40 RHU=41 层在 30→10 hPa 间跳层），
    # 仅需前 n_lev 层一致即可——与 MATLAB count=[1,1,N] 行为一致。
    n_lev = pre_ref.size if levels is None else min(levels, pre_ref.size)
    for var_name, pre_i in pre_check.items():
        if pre_i.size < n_lev:
            raise ValueError(f"变量 {var_name} 仅 {pre_i.size} 层，少于 {n_lev} 层")
        if not np.allclose(pre_i[:n_lev], pre_ref[:n_lev]):
            raise ValueError(f"变量 {var_name} 前 {n_lev} 层气压与 {ref_name} 不一致")
    fields = {k: v[:, :, :n_lev] for k, v in fields.items()}
    pre = pre_ref[:n_lev]

    if fields["gh"].shape != (lon_g.size, lat_g.size, pre.size):
        raise ValueError(
            f"数据维度 {fields['gh'].shape} 与坐标 (lon={lon_g.size}, "
            f"lat={lat_g.size}, lev={pre.size}) 不一致: {ref_name}"
        )

    fields, lon_g, lat_g = apply_lonlat_convention(fields, lon_g, lat_g)
    check_level_order(fields["gh"], "gh", ref_name)
    check_latlon_order(lon_g, lat_g, ref_name)

    return TempData(
        geo=fields["gh"], tem=fields["t"],
        sh=fields.get("q"), rh=fields.get("r"),
        pre=pre, lon=lon_g, lat=lat_g,
    )


def group_complete_times(cfg: _GribSourceCfg, files) -> dict[str, dict[str, Path]]:
    """按时间戳分组文件并剔除不完整/歧义时次（与 Reader 读取规则同源）。

    文件可来自任意目录（由调用方决定如何收集），因此同时适配"日目录"与
    "年/变量子目录"两种布局。
    """
    scan = scan_cfg_files(cfg, files)
    # 剔除不完整时次（MATLAB 按索引配对会错位/越界，这里显式跳过并警告）
    complete: dict[str, dict[str, Path]] = {}
    for ts, file_map in sorted(scan.groups.items()):
        if ts in scan.duplicates:
            print(f"警告: 时次 {ts} 同一变量存在多个文件（{'；'.join(scan.duplicate_detail(ts))}），"
                  f"命名规则不定义文件优先级，已跳过")
            continue
        if len(file_map) == cfg.n_vars:
            complete[ts] = dict(file_map)
        else:
            print(f"警告: 时次 {ts} 文件组不完整({len(file_map)}/{cfg.n_vars})，已跳过")
    return complete


def _group_by_timestamp(cfg: _GribSourceCfg, day_folder: Path) -> dict[str, dict[str, Path]]:
    """按时间戳分组单个目录（含子目录）内的各变量文件（NC 与 GRIB2 皆可）。"""
    return group_complete_times(cfg, collect_source_files([day_folder]))


def _date_bound(value, suffix: str) -> str:
    """把日期边界（``date`` 或 ``YYYYMMDD``）规范化为 10 位 ``YYYYMMDD`` + 时。

    年目录布局会把整年目录纳入扫描，必须用请求日期范围在内存中裁剪时次，否则
    日期级/子区间请求会误处理请求日期之外的时次。
    """
    if isinstance(value, date):
        return value.strftime("%Y%m%d") + suffix
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return text + suffix
    raise ValueError(f"日期边界格式无效: {value!r}")


def _make_iter_hours(cfg: _GribSourceCfg):
    from metcom.sources import SOURCES
    levels = SOURCES[cfg.key].levels

    def iter_hours(base_folder, start_date, end_date,
                   start_time=None, end_time=None) -> Iterator[tuple[str, TempData]]:
        # 10 位 YYYYMMDDHH 边界 → 只处理闭区间内的时次。
        bounds = _hour_bounds(start_time, end_time)
        # 年目录布局会扫描整个年份目录，故先按请求日期范围裁剪时次，避免日期级/
        # 子区间请求处理请求日期之外的时次。
        date_lo = _date_bound(start_date, "00")
        date_hi = _date_bound(end_date, "23")
        folders = resolve_scan_folders(base_folder, start_date, end_date)
        groups = group_complete_times(cfg, collect_source_files(folders))
        for ts, file_map in groups.items():
            if not (date_lo <= ts <= date_hi):
                continue
            if bounds is not None and not (bounds[0] <= ts <= bounds[1]):
                continue
            yield ts, read_time_group(cfg, file_map, levels)
    return iter_hours


class GribSourceReader:
    """CMAMESO/CRA40/CRAV15 读取器（GRIB2 直读或预转换 NC）。"""

    def __init__(self, key: str):
        self.key = key
        self.cfg = _CFG[key]

    def iter_hours(self, base_folder, start_date, end_date,
                   start_time=None, end_time=None):
        return _make_iter_hours(self.cfg)(
            base_folder, start_date, end_date,
            start_time=start_time, end_time=end_time,
        )
