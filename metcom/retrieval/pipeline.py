"""反演流水线 —— 各数据源 .mlapp GUI 处理回调的 Python 复刻。

流程：GRIB2 预转换（可选）→ 逐时次（reader.iter_hours）读数据 →
逐站点反演 → 按年积日写结果。站点文件在循环外解析一次（与新版 GUI 一致）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from metcom.config import parse_yyyymmdd
from metcom.retrieval.readers import get_reader
from metcom.sources import SOURCES, SourceSpec
from metcom.utils.egm2008 import load_egm_mat
from metcom.utils.io import (Site, _RESULT_KEY_TO_COLUMN, get_doy,
                             read_site_file, save_result_mat,
                             write_result_file)


@dataclass
class RetrievalOptions:
    """对应各 .mlapp 界面参数（Options_* struct）。"""

    source_key: str                      # era5/cmameso/cra40/crav15/merra2
    data_path: str                       # Data Path
    output_path: str                     # Output Path
    output_format: str = "txt"           # OutPut Type: txt | mat | dat
    site_file: str = ""                  # Stations List
    start_time: str = ""                 # Start time（YYYYMMDD 或 YYYYMMDDHH）
    end_time: str = ""                   # End time
    spatial_resolution: tuple[float, float] = (0.25, 0.25)
    time_resolution: str = "1h"
    parallel: bool = False               # Parallel pool
    n_jobs: int | None = None
    egm_file: str | None = None          # 为空时按数据源默认文件名自动定位
    grib2: dict = field(default_factory=dict)  # GRIB2 预转换选项（grib 类数据源）


def filter_day_folders(base_folder: Path, start_date: date, end_date: date) -> list[Path]:
    """扫描数据根目录下 yyyyMMdd 文件夹并按时间范围筛选（对应 GUI 日期扫描）。"""
    if not base_folder.is_dir():
        raise NotADirectoryError(f"数据根目录不存在: {base_folder}")

    valid: list[Path] = []
    for p in sorted(base_folder.iterdir(), key=lambda x: x.name):
        if not p.is_dir() or not p.name.isdigit() or len(p.name) != 8:
            continue
        try:
            y, m, d = parse_yyyymmdd(p.name)
        except ValueError:
            continue
        if start_date <= date(y, m, d) <= end_date:
            valid.append(p)
    return valid


def _result_columns(results: list[dict]) -> dict[str, list[float]]:
    """把站点反演结果整理为以表头列名为键的列字典（write/save 共用）。"""
    return {col: [r[key] for r in results]
            for key, col in _RESULT_KEY_TO_COLUMN.items()}


_MODULE_DIRS = {
    "era5": "METCOM_ERA5",
    "cmameso": "METCOM_CMAMESO",
    "cra40": "METCOM_CRA40",
    "crav15": "METCOM_CRAV15",
    "merra2": "METCOM_MERRA2",
}

# 支持小时级时间边界（YYYYMMDDHH）的读取器；MERRA-2 仍按日读取（6h 切片语义不变）。
_HOUR_FILTER_SOURCES = ("era5", "cra40", "crav15", "cmameso")
# 多变量分文件的 GRIB 数据源：小时级请求除过滤外还要校验覆盖完整性（任务 B/C）。
_GRIB_HOUR_SOURCES = ("cra40", "crav15", "cmameso")


def _default_egm_path(spec: SourceSpec) -> Path:
    """在原 MATLAB 软件包中定位默认 EGM2008 文件。"""
    pkg_root = Path(__file__).resolve().parents[2]      # .../METCOM_ver3.1_python
    candidates = [
        # 原软件包 dependentDataFile（与 ver3.1_python 同级时）
        pkg_root.parent / "METCOM_ver3.1" / "03_DataProcessing"
        / _MODULE_DIRS[spec.key] / "dependentDataFile" / spec.egm_file,
        # 项目内 benchmarks/dependentDataFile（用户自行拷贝时）
        pkg_root / "benchmarks" / "dependentDataFile" / spec.egm_file,
        # 工作目录相对路径
        Path("dependentDataFile") / spec.egm_file,
        Path(spec.egm_file),
    ]
    for c in candidates:
        if c.is_file():
            return c.resolve()
    raise FileNotFoundError(
        f"未找到 EGM2008 文件 {spec.egm_file}，请在配置中用 egm_file 指定绝对路径"
    )


def _process_site(site: Site, temp_data, spec: SourceSpec, egm) -> dict:
    """单站点反演（并行工作函数）。"""
    from metcom.retrieval.site_retrieval import fun_site

    return fun_site((site.lat, site.lon, site.height), site.name,
                    temp_data, spec, egm)


def run_retrieval(options: RetrievalOptions) -> dict:
    """执行反演主流程（各 .mlapp 的 *_Process 回调复刻）。

    处理顺序：
      1. 校验空间分辨率与数据源一致（等价于 GUI 下拉框仅允许合法值）；
      2. GRIB2 预转换（仅当 grib2.enabled，默认直读跳过）；
      3. 加载 EGM2008 改正文件（对应原 addpath dependentDataFile）；
      4. 预解析站点文件（循环外一次，与新版 GUI 一致）；
      5. 逐时次（reader.iter_hours）→ 逐站点反演（串行或 joblib 并行）→
         按年积日目录写出（saveToText/saveToMat/saveToDat）。

    日志行与原 GUI 的 LOG 面板格式对应（``=== <时次> Running: ...``）。

    Parameters
    ----------
    options:
        反演参数（对应原 GUI 界面字段，见 RetrievalOptions）。

    Returns
    -------
    dict: 处理统计，键为 ``hours``（处理时次数）、``sites``（站点数）、
        ``outputs``（写出文件数）。为 0 时会打印排查指引。
    """
    spec: SourceSpec = SOURCES[options.source_key]
    reader = get_reader(spec.key)

    # ---- 校验分辨率与数据源一致（GUI 下拉框仅允许合法值，此处等价校验）----
    if options.spatial_resolution != spec.spatial_resolution:
        raise ValueError(
            f"空间分辨率 {options.spatial_resolution} 与 {spec.name} 要求的 "
            f"{spec.spatial_resolution} 不一致"
        )

    # ---- GRIB2 预转换（替代原 grib2Trans / GribToNcConverter_cfgrib.exe）----
    data_path = Path(options.data_path)
    grib2 = options.grib2 or {}
    if spec.needs_grib2_convert and grib2.get("enabled"):
        from metcom.retrieval.grib2nc import convert_day_folder
        raw_path = Path(grib2.get("raw_path") or data_path)
        temp_dir = Path(grib2.get("temp_dir") or "tempTransData")
        y0, m0, d0 = parse_yyyymmdd(options.start_time)
        y1, m1, d1 = parse_yyyymmdd(options.end_time)
        print(f"GRIB2 预转换: {raw_path} -> {temp_dir}")
        for day_folder in filter_day_folders(raw_path, date(y0, m0, d0), date(y1, m1, d1)):
            convert_day_folder(day_folder, temp_dir / day_folder.name)
        data_path = temp_dir
        print(f"转换完成，使用 NC 数据目录: {data_path}")

    start_text = str(options.start_time).strip()
    end_text = str(options.end_time).strip()
    y0, m0, d0 = parse_yyyymmdd(start_text)
    y1, m1, d1 = parse_yyyymmdd(end_text)
    start_date, end_date = date(y0, m0, d0), date(y1, m1, d1)

    # ---- EGM2008 文件定位（原 MATLAB 通过 addpath dependentDataFile 实现）----
    if options.egm_file:
        egm_path = Path(options.egm_file)
    else:
        egm_path = _default_egm_path(spec)
    print(f"加载 EGM2008 高程改正文件: {egm_path}")
    egm = load_egm_mat(egm_path, expected_sr=spec.spatial_resolution)

    # ---- 预解析站点（循环外，一次）----
    sites = read_site_file(options.site_file)
    n_sites = len(sites)
    print(f"站点数量: {n_sites}")

    output_format = options.output_format.lstrip(".").lower()
    if output_format not in ("txt", "mat", "dat"):
        raise ValueError(f"不支持的输出格式: {options.output_format}")

    from joblib import Parallel, delayed

    stats = {"hours": 0, "sites": n_sites, "outputs": 0}
    if spec.key in _HOUR_FILTER_SOURCES:
        if spec.key in _GRIB_HOUR_SOURCES:
            # 阶段一任务 B/C + 阶段三：GRIB 数据源（CRA40 / CRA V1.5 / CMA-MESO）
            # 无论小时级（YYYYMMDDHH）还是日期级（YYYYMMDD）请求，都先按数据源自
            # 有时间分辨率校验覆盖完整性（只扫描文件名，不解码数据）；缺失/不完整
            # 时明确失败，绝不把部分时次当完整成功。ERA5/MERRA-2 不进入此分支，
            # 既有行为保持不变。
            from metcom.retrieval.readers.grib_sources import (
                resolve_scan_folders, verify_hour_coverage)
            verify_hour_coverage(
                spec.key,
                resolve_scan_folders(data_path, start_date, end_date),
                start_time=start_text, end_time=end_text,
                time_resolution=spec.time_resolution,
            )
        hour_iter = reader.iter_hours(
            data_path, start_date, end_date,
            start_time=start_text, end_time=end_text,
        )
    else:
        # MERRA-2：保持按日读取，不传小时边界（原有筛选语义不变）。
        hour_iter = reader.iter_hours(data_path, start_date, end_date)

    for ts, temp_data in hour_iter:
        stats["hours"] += 1
        print(f"=== {ts} Running:      -time: {time.strftime('%Y-%m-%d %H:%M:%S')}")

        if options.parallel and n_sites > 1:
            results = Parallel(n_jobs=options.n_jobs or -1)(
                delayed(_process_site)(s, temp_data, spec, egm) for s in sites
            )
        else:
            results = [_process_site(s, temp_data, spec, egm) for s in sites]

        year_doy, _ = get_doy(ts)   # ztd_<ts>_sit_<key>.<ext>，目录按年积日
        output_dir = Path(options.output_path) / year_doy
        values = _result_columns(results)
        if output_format == "mat":
            out_file = output_dir / f"ztd_{ts}_sit_{spec.key}.mat"
            save_result_mat(sites, values, out_file, f"Result_{spec.key}")
        else:
            out_file = output_dir / f"ztd_{ts}_sit_{spec.key}.{output_format}"
            write_result_file(sites, values, out_file)
        stats["outputs"] += 1

        del temp_data

    print("Program has been completed !!!")
    if stats["hours"] == 0:
        if spec.key == "era5" and len(start_text) == 10 and len(end_text) == 10:
            raise FileNotFoundError(
                f"所选时刻范围内未找到 {spec.name} 数据：{start_text}..{end_text}"
            )
        print(
            f"警告: 未处理任何时次！请检查 data_path 下的文件命名是否匹配 {spec.name} "
            f"约定（{spec.file_pattern_doc}）以及时间范围是否正确"
        )
    return stats
