"""业务适配层：把界面上的配置字典翻译成 01~05 模块的调用。

界面层不直接 import xarray / matplotlib 等重依赖——它们在 :func:`build_task`
内部按需导入，保证软件冷启动只加载 PySide6，秒开。
"""

from __future__ import annotations

import importlib
import importlib.util
import platform
import sys
from pathlib import Path

MODULE_ORDER = ("gridgen", "download", "retrieve", "timeinterp", "evaluation")


# --------------------------------------------------------------- 反演参数 --
def retrieval_options_from_cfg(cfg: dict):
    """把配置字典转成 ``RetrievalOptions``（与 metcom.cli 的构造逻辑一致）。"""
    from metcom.retrieval.pipeline import RetrievalOptions
    from metcom.sources import SOURCES

    source = str(cfg.get("source") or "era5")
    spec = SOURCES[source]
    sr = cfg.get("spatial_resolution") or list(spec.spatial_resolution)
    return RetrievalOptions(
        source_key=source,
        data_path=str(cfg["data_path"]),
        output_path=str(cfg["output_path"]),
        output_format=str(cfg.get("output_format", "txt")).lstrip(".").lower(),
        site_file=str(cfg.get("site_file") or ""),
        start_time=str(cfg["start_time"]),
        end_time=str(cfg["end_time"]),
        spatial_resolution=(float(sr[0]), float(sr[1])),
        time_resolution=str(cfg.get("time_resolution") or spec.time_resolution),
        parallel=bool(cfg.get("parallel", False)),
        n_jobs=cfg.get("n_jobs"),
        egm_file=cfg.get("egm_file") or None,
        grib2=cfg.get("grib2") or {},
    )


# --------------------------------------------------------------- 任务构造 --
def build_task(key: str, cfg: dict):
    """返回 ``(可调用对象, 位置参数元组)``；重依赖在函数内部才导入。"""
    if key == "gridgen":
        from metcom.gridgen import run_gridgen
        return run_gridgen, (cfg,)
    if key == "download":
        from metcom.downloader.runner import run_download
        return run_download, (cfg,)
    if key == "retrieve":
        from metcom.retrieval.pipeline import run_retrieval
        return run_retrieval, (retrieval_options_from_cfg(cfg),)
    if key == "timeinterp":
        from metcom.timeinterp.runner import run_timeinterp
        return run_timeinterp, (cfg,)
    if key == "evaluation":
        from metcom.evaluation.runner import run_evaluation
        return run_evaluation, (cfg,)
    if key == "plot":
        from metcom.evaluation.runner import run_plot
        return run_plot, (cfg,)
    raise KeyError(f"未知模块: {key}")


# ----------------------------------------------------------------- 校验 --
def _needs(problems: list[str], cfg: dict, keys: list[str], where: str) -> None:
    for k in keys:
        value = cfg.get(k)
        if value in (None, "", []):
            problems.append(f"错误: {where} 缺少必填项 {k}")


def _path_problem(problems: list[str], value, label: str, kind: str = "dir") -> None:
    if not value:
        return
    path = Path(str(value))
    if kind == "dir":
        if not path.is_dir():
            problems.append(f"警告: {label} 目录不存在（{path}）")
    elif not path.is_file():
        problems.append(f"警告: {label} 文件不存在（{path}）")


def validate(key: str, cfg: dict) -> list[str]:
    """运行前的轻量校验；返回问题清单（空表示可以运行）。

    只做"看得见"的检查（必填项、路径是否存在），不替代模块内部的严格校验。
    """
    problems: list[str] = []

    if key == "gridgen":
        dem = cfg.get("dem") or {}
        stations = cfg.get("stations") or {}
        if not dem and not stations:
            problems.append("错误: 未配置任何步骤（dem / stations 均为空）")
        if dem:
            _needs(problems, dem, ["dem_path", "city_shp", "output_dir"], "步骤 1A")
            _path_problem(problems, dem.get("dem_path"), "DEM 全球地形", "file")
            _path_problem(problems, dem.get("city_shp"), "行政区划 shapefile", "file")
            if not (dem.get("regions") or []):
                problems.append("警告: 区域表为空，步骤 1A 将不会输出任何 NC")
        if stations:
            _needs(problems, stations, ["dem_file", "output_prefix", "output_dir"],
                   "步骤 1B")
            _path_problem(problems, stations.get("dem_file"), "DEM 输入", "file")

    elif key == "download":
        _needs(problems, cfg, ["cdsapirc_path", "start_time", "end_time",
                               "output_dir"], "下载")
        _path_problem(problems, cfg.get("cdsapirc_path"), "CDS API 密钥", "file")
        if bool(cfg.get("use_idm")) and not Path(str(cfg.get("idm_path", ""))).is_file():
            problems.append("错误: 勾选了 IDM 加速，但 IDM 可执行文件路径无效")

    elif key == "retrieve":
        _needs(problems, cfg, ["data_path", "output_path", "site_file",
                               "start_time", "end_time"], "反演")
        _path_problem(problems, cfg.get("data_path"), "再分析数据根目录", "dir")
        _path_problem(problems, cfg.get("site_file"), "站点列表", "file")
        egm = cfg.get("egm_file")
        if egm:
            _path_problem(problems, egm, "EGM2008", "file")
        else:
            from metcom.sources import SOURCES
            spec = SOURCES[str(cfg.get("source") or "era5")]
            if find_egm(spec.key) is None:
                problems.append(
                    f"警告: 未定位到 {spec.name} 的 EGM2008 文件 {spec.egm_file}，"
                    "请在参数中显式指定 egm_file")

    elif key == "timeinterp":
        _needs(problems, cfg, ["input_dir", "output_dir"], "时间插值")
        _path_problem(problems, cfg.get("input_dir"), "反演结果输入", "dir")

    elif key == "evaluation":
        _needs(problems, cfg, ["dir_a", "dir_b", "time_input", "output_dir"], "评估")
        _path_problem(problems, cfg.get("dir_a"), "数据源 A", "dir")
        _path_problem(problems, cfg.get("dir_b"), "数据源 B", "dir")
        if "," not in str(cfg.get("time_input", "")):
            problems.append("警告: 时间范围应为 \"起始,结束\"（如 2024010100,2024010123）")
        if not (cfg.get("eval_vars") or []):
            problems.append("错误: 未勾选任何评估变量")

    elif key == "plot":
        _needs(problems, cfg, ["txt_file", "output_dir"], "绘图")
        _path_problem(problems, cfg.get("txt_file"), "输入数据文件", "file")
        if not (cfg.get("plot_vars") or []):
            problems.append("警告: 未勾选任何绘图变量")

    return problems


# --------------------------------------------------------------- 环境自检 --
_RUNTIME_DEPS = [
    ("numpy", "数值计算"),
    ("pandas", "表格处理"),
    ("scipy", "样条插值"),
    ("xarray", "NetCDF 读取"),
    ("netCDF4", "NetCDF4 后端"),
    ("h5py", "HDF5 支持"),
    ("yaml", "配置解析"),
    ("joblib", "站点级并行"),
    ("cdsapi", "ERA5 下载"),
]

_OPTIONAL_DEPS = [
    ("matplotlib", "绘图内核"),
    ("cartopy", "地图投影"),
    ("geopandas", "行政区划"),
    ("shapely", "几何运算"),
    ("cfgrib", "GRIB2 直读"),
]


def find_egm(source_key: str) -> Path | None:
    """定位某数据源的 EGM2008 文件（复用反演模块的搜索规则）。"""
    try:
        from metcom.retrieval.pipeline import _default_egm_path
        from metcom.sources import SOURCES

        return _default_egm_path(SOURCES[source_key])
    except Exception:
        return None


def _dep_state(name: str) -> tuple[str, str]:
    """返回 (版本或说明, 状态)；状态取 ok / warn / bad。"""
    try:
        if importlib.util.find_spec(name) is None:
            return "未安装", "bad"
    except (ImportError, ValueError):
        return "未安装", "bad"
    try:
        module = importlib.import_module(name)
        version = getattr(module, "__version__", "")
        return (f"已安装  {version}".rstrip(), "ok")
    except Exception as exc:
        return f"导入失败（{type(exc).__name__}）", "warn"


def environment_report(root: Path, configs_dir: Path) -> list[dict]:
    """环境自检结果（环境与设置页展示）。"""
    from metcom import __version__

    rows: list[dict] = []

    rows.append({"section": "运行环境"})
    rows.append({"key": "Python", "value": sys.version.split()[0],
                 "state": "ok" if sys.version_info >= (3, 9) else "bad"})
    rows.append({"key": "解释器", "value": sys.executable, "state": "normal",
                 "mono": True})
    rows.append({"key": "操作系统",
                 "value": f"{platform.system()} {platform.release()} "
                          f"({platform.machine()})", "state": "normal"})
    _pyside = _dep_state("PySide6")
    rows.append({"key": "PySide6", "value": _pyside[0], "state": _pyside[1]})
    rows.append({"key": "METCOM 版本", "value": f"v{__version__}（Python 迁移版）",
                 "state": "normal"})

    rows.append({"section": "工程路径"})
    rows.append({"key": "项目根目录", "value": str(root), "state": "normal",
                 "mono": True})
    rows.append({"key": "配置目录", "value": str(configs_dir),
                 "state": "ok" if configs_dir.is_dir() else "bad", "mono": True})

    rows.append({"section": "核心依赖"})
    for name, desc in _RUNTIME_DEPS:
        value, state = _dep_state(name)
        rows.append({"key": f"{name}", "value": f"{desc} · {value}", "state": state})

    rows.append({"section": "绘图依赖（05 模块可选）"})
    for name, desc in _OPTIONAL_DEPS:
        value, state = _dep_state(name)
        rows.append({"key": f"{name}", "value": f"{desc} · {value}", "state": state})

    rows.append({"section": "界面字体"})
    from metcom.gui import theme as _theme

    fonts = _theme.font_report()
    if not fonts["checked"]:
        rows.append({"key": "字体检测", "value": "未创建 QApplication，本次跳过",
                     "state": "warn"})
    else:
        rows.append({"key": "界面字体", "value": fonts["ui"], "state": "ok"})
        rows.append({"key": "等宽字体（日志）", "value": fonts["mono"], "state": "ok"})
        rows.append({"key": "中文字形",
                     "value": fonts["cjk"] or "未安装中文字体，界面可能显示方框",
                     "state": "ok" if fonts["cjk"] else "warn"})
        rows.append({"key": "系统字体总数", "value": f"{fonts['count']} 个",
                     "state": "normal"})

    rows.append({"section": "地图底图"})
    basemap = root / "metcom" / "gui" / "assets" / "naturalearth"
    layers = sorted(p.name for p in basemap.rglob("*.shp")) if basemap.is_dir() else []
    rows.append({"key": "底图目录", "value": str(basemap),
                 "state": "ok" if basemap.is_dir() else "bad", "mono": True})
    rows.append({"key": "矢量图层",
                 "value": (f"Natural Earth 50m · {len(layers)} 个图层（离线可用）"
                           if layers else "未找到底图矢量文件"),
                 "state": "ok" if layers else "bad"})
    mask = basemap / "_land_mask.pkl"
    rows.append({"key": "陆地面缓存",
                 "value": str(mask) if mask.is_file()
                 else "尚未生成（首次绘图时自动建立）",
                 "state": "ok" if mask.is_file() else "warn", "mono": mask.is_file()})

    rows.append({"section": "数据源与密钥"})
    from metcom.sources import SOURCES

    rows.append({"key": "数据源清单",
                 "value": " · ".join(spec.name for spec in SOURCES.values())
                          + f"（共 {len(SOURCES)} 个）",
                 "state": "ok"})
    for key, spec in SOURCES.items():
        found = find_egm(key)
        rows.append({
            "key": f"EGM2008 · {spec.name}",
            "value": str(found) if found else f"未找到（{spec.egm_file}）",
            "state": "ok" if found else "warn",
            "mono": bool(found),
        })

    eccodes_dir = root / "metcom" / "libs" / "eccodes-jpeg"
    rows.append({"key": "内嵌 eccodes", "value": str(eccodes_dir),
                 "state": "ok" if eccodes_dir.is_dir() else "warn", "mono": True})

    cds = configs_dir.parent / "CDSAPI" / ".cdsapirc_test"
    rows.append({"key": "CDS API 密钥模板",
                 "value": str(cds) if cds.is_file() else "未随仓库提供（需自备）",
                 "state": "ok" if cds.is_file() else "warn"})

    site_file = root / "benchmarks" / "bth_0125_orthometric.txt"
    rows.append({"key": "示例站点列表",
                 "value": str(site_file) if site_file.is_file() else "未找到",
                 "state": "ok" if site_file.is_file() else "warn", "mono": True})

    return rows
