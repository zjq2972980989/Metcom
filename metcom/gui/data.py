"""METCOM 图形化数据层：演示基准数据与用户运行结果的统一读取接口。

界面上的曲线、地图、指标表、雷达图都只跟这一层打交道，页面代码不必关心
数据落在磁盘哪个角落。两类数据来源共用同一套文件格式（同一批模块产出）：

* ``origin = "demo"`` —— 随仓库提供的基准结果，用来"打开即出图"；
* ``origin = "user"`` —— 用户自己跑 01~05 流程产生的结果。

扫描时先用户、后基准：某个数据源在哪一侧先命中，就用哪一侧的数据。

关于 MAE
--------
评估模块的输出表只给 RMSE 与 Bias。对零均值高斯残差有恒等式

    MAE = sqrt(2 / pi) * sigma,   sigma = sqrt(RMSE ** 2 - Bias ** 2)

据此给出与 RMSE/Bias 自洽的 MAE 估计值，界面上标注成 ``MAE*``，避免与
实测 MAE 混淆。

关于统计口径
------------
雷达图等图形上的每个维度都有明确出处：能从产出里量出来的就用实测值，量不
出来的才回退到数据源规格，两者用 :class:`SourceStats` 的 ``*_basis`` 字段
区分，界面据此加注，避免把规格值当实测值横向比较。

* ``output`` —— 实测：读本源的原始产出（时刻取自文件名、站点取自站列表）；
* ``eval``   —— 实测：原始产出缺失时，退用评估结果表的对应列；
* ``derived``—— 派生：由 RMSE 与 Bias 推算，例如相关性 ``1 - |Bias| / RMSE``；
* ``spec``   —— 规格：``metcom.sources.SOURCES`` 声明的模式参数。五源都插值
  到同一套 GNSS 站网，站网间距实测五源相同，因此"模式网格分辨率"这类参数
  无法从产出中反推，只能标成规格值。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

SOURCE_ORDER: tuple[str, ...] = ("era5", "cmameso", "cra40", "crav15", "merra2")

SOURCE_LABELS: dict[str, str] = {
    "era5": "ERA5",
    "cmameso": "CMA-MESO",
    "cra40": "CRA40",
    "crav15": "CRA V1.5",
    "merra2": "MERRA2",
}

# 评估输出文件名前缀（NAME_A），例如 CRAV15_vs_ERA5_GMET_ByStation_*.csv
_METRIC_STEM: dict[str, str] = {
    "era5": "ERA5_MET",
    "cmameso": "CMAMESO",
    "cra40": "CRA40",
    "crav15": "CRAV15",
    "merra2": "MERRA2",
}

# 反演输出文件名后缀，例如 ztd_2024010100_sit_crav15.txt
_SERIES_TAG: dict[str, str] = {
    "era5": "era5",
    "cmameso": "cmameso",
    "cra40": "cra40",
    "crav15": "crav15",
    "merra2": "merra2",
}

# 宽松别名表：目录名 / 名称字段经过 _norm 归一后都能落到标准 key
_ALIASES: dict[str, str] = {
    "era5": "era5", "era5met": "era5", "era5gmet": "era5", "era5g": "era5",
    "cmameso": "cmameso", "cma": "cmameso", "cmeso": "cmameso",
    "grapes3km": "cmameso", "cmameso3km": "cmameso",
    "cra40": "cra40", "cra": "cra40",
    "crav15": "crav15", "crav1": "crav15", "cra15": "crav15",
    "merra2": "merra2", "merra": "merra2", "merra2np": "merra2",
}

GAUSS_MAE = math.sqrt(2.0 / math.pi)

_TIME_RE = re.compile(r"(\d{10})")


# ------------------------------------------------------------------ 小工具 --
def _norm(text: str) -> str:
    """把名称归一成小写字母数字串（"CRA V1.5" -> "crav15"）。"""
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def normalize_source(value: str | None) -> str | None:
    """名称 -> 标准数据源 key；无法识别时返回 None。"""
    if not value:
        return None
    key = _norm(value)
    if key in _ALIASES:
        return _ALIASES[key]
    for alias, target in _ALIASES.items():
        if key.startswith(alias) or alias.startswith(key):
            return target
    return None


def source_label(key: str) -> str:
    """数据源中文/英文显示名。"""
    return SOURCE_LABELS.get(normalize_source(key) or "", str(key))


@dataclass(frozen=True)
class SourceInfo:
    """数据源规格（层数 / 分辨率 / 湿度模式），雷达图与图例要用。"""

    key: str
    label: str
    levels: int
    spatial: float
    temporal: float
    humidity: str = "none"
    grib2: bool = False


_SPEC_FALLBACK: dict[str, SourceInfo] = {
    "era5": SourceInfo("era5", "ERA5", 37, 0.25, 1.0),
    "cmameso": SourceInfo("cmameso", "CMA-MESO", 21, 0.03, 3.0, "rh_to_q", True),
    "cra40": SourceInfo("cra40", "CRA40", 37, 0.25, 6.0, "none", True),
    "crav15": SourceInfo("crav15", "CRA V1.5", 37, 0.10, 1.0, "none", True),
    "merra2": SourceInfo("merra2", "MERRA2", 42, 0.50, 6.0, "q_to_rh"),
}


def source_info(key: str) -> SourceInfo:
    """优先取业务模块里的真实规格，取不到则退回内置副本。"""
    std = normalize_source(key) or "era5"
    try:
        from metcom.sources import SOURCES

        spec = SOURCES[std]
        temporal = float(str(spec.time_resolution).rstrip("hH") or 1.0)
        return SourceInfo(
            std, spec.name, int(spec.levels), float(spec.spatial_resolution[0]),
            temporal, spec.humidity_mode, bool(spec.needs_grib2_convert))
    except Exception:
        return _SPEC_FALLBACK[std]


# 统计口径各项的出处标记（见模块文档「关于统计口径」）
BASIS_OUTPUT = "output"
BASIS_EVAL = "eval"
BASIS_SPEC = "spec"

BASIS_TEXT: dict[str, str] = {
    BASIS_OUTPUT: "实测",
    BASIS_EVAL: "评估表",
    BASIS_SPEC: "规格值",
}


@dataclass(frozen=True)
class SourceStats:
    """单个数据源的实测统计口径（定义见模块文档「关于统计口径」）。

    实测项取自本数据源实际产出的时刻与站点；取不到实测值时回退到数据源
    规格，并在 :attr:`steps_basis` / :attr:`interval_basis` 里标注出处。
    """

    key: str
    label: str
    origin: str
    steps: int                 # 时次数：实际产出的时刻个数
    steps_basis: str
    interval_hours: float      # 采样间隔：相邻产出时刻间隔的中位数，小时
    interval_basis: str
    span_hours: float          # 时间跨度：首末产出时刻之差，小时
    coverage: float            # 时间覆盖率：时次数 / 跨度内理论时次数，0~1
    sites: int                 # 有效站点数：实际有输出的站点个数
    network: int               # 站网规模：各源有效站点数的最大值
    grid_deg: float            # 模式网格：数据源规格声明的分辨率，度
    window: str
    first: str = ""            # 首个产出时刻 YYYYMMDDHH
    last: str = ""             # 末个产出时刻 YYYYMMDDHH

    @property
    def site_ratio(self) -> float:
        """站点覆盖率 0~1（有效站点 / 站网规模）。"""
        return self.sites / self.network if self.network else 0.0

    def interval_text(self) -> str:
        """采样间隔的出处角标：取自原始产出时为空串，回退项标注出处。"""
        if self.interval_basis == BASIS_OUTPUT:
            return ""
        label = BASIS_TEXT.get(self.interval_basis, self.interval_basis)
        return f"（{label}）"


def _iter_dirs(base: Path, depth: int) -> list[Path]:
    """受限深度的目录枚举（避免在数据盘上无限递归）。"""
    if not base.is_dir():
        return []
    found = [base]
    if depth <= 0:
        return found
    try:
        children = sorted(p for p in base.iterdir() if p.is_dir())
    except OSError:
        return found
    for child in children:
        found.extend(_iter_dirs(child, depth - 1))
    return found


def _grep(base: Path, pattern: str, depth: int = 2) -> list[Path]:
    """在 base 下（含 depth 层子目录）找文件。"""
    hits: list[Path] = []
    for folder in _iter_dirs(base, depth):
        try:
            hits.extend(sorted(folder.glob(pattern)))
        except OSError:
            continue
    return hits


def _window_from_name(name: str) -> str:
    """从文件名里抽出 "YYYYMMDDHH-YYYYMMDDHH" 时间窗。"""
    stamps = _TIME_RE.findall(name)
    if len(stamps) >= 2:
        return f"{stamps[-2]}-{stamps[-1]}"
    return stamps[-1] if stamps else ""


def _pretty_window(window: str) -> str:
    """2024010100-2024010223 -> 2024-01-01 00时 → 01-02 23时。"""
    parts = window.split("-")
    if len(parts) != 2:
        return window
    try:
        a = pd.to_datetime(parts[0], format="%Y%m%d%H")
        b = pd.to_datetime(parts[1], format="%Y%m%d%H")
    except Exception:
        return window
    if a.date() == b.date():
        return f"{a:%Y-%m-%d %H}时 → {b:%H}时"
    return f"{a:%Y-%m-%d %H}时 → {b:%m-%d %H}时"


# -------------------------------------------------------------- 数据源对象 --
class SourceData:
    """单个数据源的两套数据：指标表（评估）与原始时序（反演）。"""

    def __init__(self, key: str, origin: str, root: Path,
                 metric_files: list[Path], series_files: list[Path]) -> None:
        self.key = key
        self.label = source_label(key)
        self.info = source_info(key)
        self.origin = origin
        self.root = root
        self.metric_files = list(metric_files)
        self.series_files = list(series_files)
        self.window = ""
        self.refresh_window()
        self._stations: pd.DataFrame | None = None
        self._times: pd.DataFrame | None = None
        self._series: pd.DataFrame | None = None      # 长表: time, site, ZTD, PWV
        self._geo: pd.DataFrame | None = None
        self._site_count: int | None = None

    # -- 基本情况 --
    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<SourceData {self.key} {self.origin} 指标{len(self.metric_files)} 时序{len(self.series_files)}>"

    def has_metrics(self) -> bool:
        return bool(self.metric_files)

    def has_series(self) -> bool:
        return bool(self.series_files)

    def refresh_window(self) -> None:
        """重算时间窗（文件增补后必须调用）。

        指标表文件名里带完整范围；只有原始时序时，取全部时序文件的首尾
        时刻。旧实现只认最后一个文件名，于是"只有原始反演结果"的用户目录
        会被显示成一个时刻。
        """
        if self.metric_files:
            self.window = _window_from_name(sorted(p.name for p in self.metric_files)[-1])
            return
        stamps = sorted({s for path in self.series_files
                         for s in _TIME_RE.findall(path.name)})
        if len(stamps) >= 2:
            self.window = f"{stamps[0]}-{stamps[-1]}"
        else:
            self.window = stamps[-1] if stamps else ""

    def window_text(self) -> str:
        return _pretty_window(self.window)

    # -- 指标表 --
    def stations(self) -> pd.DataFrame:
        """逐站指标表（列为 site/lat/lon/alt/n/ztd_rmse/...)。"""
        if self._stations is None:
            frame = pd.DataFrame()
            pick = self._pick(self.metric_files, "ByStation")
            if pick is not None:
                frame = _read_station_csv(pick)
                frame["window"] = self.window
            self._stations = frame
        return self._stations

    def times(self) -> pd.DataFrame:
        """逐时次指标表（列为 time/n/ztd_rmse/...)。"""
        if self._times is None:
            frame = pd.DataFrame()
            pick = self._pick(self.metric_files, "Timeseries")
            if pick is not None:
                frame = _read_timeseries_csv(pick)
            self._times = frame
        return self._times

    @staticmethod
    def _pick(files: list[Path], kind: str) -> Path | None:
        hits = [p for p in files if kind.lower() in p.name.lower()]
        if not hits:
            return None
        return max(hits, key=lambda p: (p.stat().st_mtime, len(p.name)))

    # -- 原始时序 --
    def load_series(self) -> bool:
        """读入本数据源全部时刻的重力场反演结果（只读一次，之后走缓存）。"""
        if self._series is not None:
            return not self._series.empty
        if not self.series_files:
            self._series = pd.DataFrame()
            return False
        frames: list[pd.DataFrame] = []
        for path in self.series_files:
            stamp = _TIME_RE.search(path.name)
            if stamp is None:
                continue
            try:
                raw = pd.read_csv(path, sep=r"\s+", usecols=["name", "ZTD(m)", "PWV(m)"])
            except Exception:
                continue
            frame = pd.DataFrame({
                "time": pd.to_datetime(stamp.group(1), format="%Y%m%d%H"),
                "site": raw["name"].astype(str),
                "ztd": pd.to_numeric(raw["ZTD(m)"], errors="coerce") * 1000.0,
                "pwv": pd.to_numeric(raw["PWV(m)"], errors="coerce") * 1000.0,
            })
            frames.append(frame)
        if not frames:
            self._series = pd.DataFrame()
            return False
        long = pd.concat(frames, ignore_index=True)
        long.sort_values("time", inplace=True, kind="stable")
        self._series = long
        return True

    def series_frame(self) -> pd.DataFrame:
        """长表（time / site / ztd / pwv，单位 mm）。"""
        self.load_series()
        return self._series if self._series is not None else pd.DataFrame()

    def times_index(self) -> np.ndarray:
        frame = self.series_frame()
        if frame.empty:
            return np.array([], dtype="datetime64[ns]")
        return np.sort(frame["time"].unique())

    def sites(self) -> tuple[str, ...]:
        """站点名单：优先后端原始数据，其次指标表。"""
        frame = self.series_frame()
        if not frame.empty:
            return tuple(pd.unique(frame["site"]))
        stations = self.stations()
        if not stations.empty:
            return tuple(stations["site"])
        return ()

    def geo(self) -> pd.DataFrame:
        """站点坐标（site / lat / lon / alt）。"""
        if self._geo is None:
            stations = self.stations()
            if not stations.empty:
                self._geo = stations[["site", "lat", "lon", "alt"]].drop_duplicates("site")
            else:
                self._geo = _geo_from_series(self.series_files)
        return self._geo

    def series(self, site: str, var: str = "ztd") -> tuple[np.ndarray, np.ndarray]:
        """单站点时序：返回 ``(时刻数组, 数值数组 mm)``。"""
        frame = self.series_frame()
        if frame.empty:
            return np.array([], dtype="datetime64[ns]"), np.array([])
        column = "pwv" if str(var).lower().startswith("p") else "ztd"
        part = frame[frame["site"] == site].sort_values("time")
        if part.empty:
            return np.array([], dtype="datetime64[ns]"), np.array([])
        values = pd.to_numeric(part[column], errors="coerce")
        return part["time"].to_numpy(), values.to_numpy(dtype=float)

    def snapshot(self, var: str = "ztd", when=None) -> pd.DataFrame:
        """某一时刻全部站点的数值（数值热力图层用）。"""
        frame = self.series_frame()
        if frame.empty:
            return pd.DataFrame()
        column = "pwv" if str(var).lower().startswith("p") else "ztd"
        stamps = np.sort(frame["time"].unique())
        target = stamps[-1] if when is None else np.datetime64(
            pd.Timestamp(when).to_datetime64())
        part = frame[frame["time"] == target]
        return part.rename(columns={column: "value"})[["site", "value"]]

    # -- 概览统计 --
    def summary(self) -> dict:
        """一行式统计：站点数、RMSE 中位数、时次数。"""
        stations = self.stations()
        times = self.times()
        return {
            "key": self.key,
            "label": self.label,
            "origin": self.origin,
            "window": self.window,
            "sites": int(stations.shape[0]) if not stations.empty else len(self.sites()),
            "steps": int(times.shape[0]),
            "ztd_rmse": float(stations["ztd_rmse"].median())
            if not stations.empty else float("nan"),
            "pwv_rmse": float(stations["pwv_rmse"].median())
            if not stations.empty else float("nan"),
            "ztd_bias": float(stations["ztd_bias"].median())
            if not stations.empty else float("nan"),
        }

    # ---------------------------------------------------------- 实测口径 --
    def output_stamps(self) -> pd.DatetimeIndex:
        """实际产出的时刻：只解析文件名，不读文件内容。

        没有原始时序文件时退而取评估表的时刻列，两者都没有则返回空索引。
        """
        stamps: list[pd.Timestamp] = []
        for path in self.series_files:
            hit = _TIME_RE.search(path.name)
            if hit is None:
                continue
            stamp = pd.to_datetime(hit.group(1), format="%Y%m%d%H", errors="coerce")
            if stamp is not pd.NaT:
                stamps.append(stamp)
        if stamps:
            return pd.DatetimeIndex(sorted(set(stamps)))
        times = self.times()
        if not times.empty and "time" in times.columns:
            return pd.DatetimeIndex(sorted(pd.unique(times["time"].dropna())))
        return pd.DatetimeIndex([])

    def sample_interval_hours(self) -> float | None:
        """实测采样间隔（h）：相邻产出时刻间隔的中位数。"""
        stamps = self.output_stamps()
        if len(stamps) < 2:
            return None
        gaps = np.diff(stamps.to_numpy()).astype("timedelta64[s]").astype(float)
        gaps = gaps[np.isfinite(gaps) & (gaps > 0.0)] / 3600.0
        return float(np.median(gaps)) if gaps.size else None

    def span_hours(self) -> float | None:
        """实测时间跨度（h）：首末产出时刻之差。"""
        stamps = self.output_stamps()
        if len(stamps) < 2:
            return None
        return float((stamps[-1] - stamps[0]).total_seconds() / 3600.0)

    def site_count(self) -> int:
        """实测有效站点数：本数据源实际有输出的站点个数。"""
        if self._site_count is None:
            self._site_count = len(self.sites())
        return self._site_count

    def stats(self, network: int = 0) -> SourceStats:
        """把本数据源的实测口径整理成 :class:`SourceStats`。

        优先用原始产出（``output``），其次评估表（``eval``），都没有才回退到
        数据源规格（``spec``），回退项在 ``*_basis`` 里标注，界面据此加注。
        """
        stamps = self.output_stamps()
        # 时刻可能来自原始产出，也可能来自评估表，出处要如实标出来。
        if self.series_files:
            basis = BASIS_OUTPUT
        elif len(stamps):
            basis = BASIS_EVAL
        else:
            basis = BASIS_SPEC
        interval = self.sample_interval_hours()
        if interval is None:
            interval, interval_basis = float(self.info.temporal), BASIS_SPEC
        else:
            interval_basis = basis
        steps = int(len(stamps))
        if steps:
            steps_basis = basis
        else:
            steps = int(self.times().shape[0])
            steps_basis = BASIS_EVAL if steps else BASIS_SPEC
        span = self.span_hours() or 0.0
        expected = span / interval + 1.0 if span > 0.0 and interval > 0.0 else 0.0
        coverage = min(1.0, steps / expected) if expected > 0 else (1.0 if steps else 0.0)
        sites = self.site_count()
        return SourceStats(
            key=self.key, label=self.label, origin=self.origin,
            steps=steps, steps_basis=steps_basis,
            interval_hours=float(interval), interval_basis=interval_basis,
            span_hours=span, coverage=float(coverage),
            sites=sites, network=int(network) or sites,
            grid_deg=float(self.info.spatial), window=self.window,
            first=stamps[0].strftime("%Y%m%d%H") if steps else "",
            last=stamps[-1].strftime("%Y%m%d%H") if steps else "")


# ------------------------------------------------------------- 表格读取 --
_STATION_COLUMNS = {
    "site": "site", "lat/deg": "lat", "lat": "lat", "lon/deg": "lon", "lon": "lon",
    "alt/m": "alt", "alt": "alt", "time_range": "time_range", "n": "n",
    "ztd_rmse(mm)": "ztd_rmse", "ztd_bias(mm)": "ztd_bias",
    "pwv_rmse(mm)": "pwv_rmse", "pwv_bias(mm)": "pwv_bias",
}

_TIME_COLUMNS = {
    "time": "time", "n": "n", "ztd_bias(mm)": "ztd_bias",
    "ztd_rmse(mm)": "ztd_rmse", "pwv_bias(mm)": "pwv_bias",
    "pwv_rmse(mm)": "pwv_rmse",
}


def _rename(frame: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    renamed = {}
    for column in frame.columns:
        target = mapping.get(str(column).strip().lower())
        renamed[column] = target or str(column).strip().lower()
    return frame.rename(columns=renamed)


def _numeric(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    for column in columns:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _read_station_csv(path: Path) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path)
    except Exception:
        return pd.DataFrame()
    frame = _rename(frame, _STATION_COLUMNS)
    frame = _numeric(frame, ("lat", "lon", "alt", "n", "ztd_rmse", "ztd_bias",
                             "pwv_rmse", "pwv_bias"))
    if "site" in frame.columns:
        frame["site"] = frame["site"].astype(str)
    return frame


def _read_timeseries_csv(path: Path) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path)
    except Exception:
        return pd.DataFrame()
    frame = _rename(frame, _TIME_COLUMNS)
    frame = _numeric(frame, ("n", "ztd_rmse", "ztd_bias", "pwv_rmse", "pwv_bias"))
    if "time" in frame.columns:
        frame["time"] = pd.to_datetime(frame["time"].astype(str),
                                       format="%Y%m%d%H", errors="coerce")
        frame.sort_values("time", inplace=True, kind="stable")
    return frame


def _geo_from_series(files: list[Path]) -> pd.DataFrame:
    """没有指标表时，从反演结果文件里取站点坐标。"""
    if not files:
        return pd.DataFrame(columns=["site", "lat", "lon", "alt"])
    try:
        raw = pd.read_csv(files[0], sep=r"\s+",
                          usecols=["name", "latitude", "longitude", "height(m)"])
    except Exception:
        return pd.DataFrame(columns=["site", "lat", "lon", "alt"])
    return pd.DataFrame({
        "site": raw["name"].astype(str),
        "lat": pd.to_numeric(raw["latitude"], errors="coerce"),
        "lon": pd.to_numeric(raw["longitude"], errors="coerce"),
        "alt": pd.to_numeric(raw["height(m)"], errors="coerce"),
    })


# ------------------------------------------------------------------ 数据集 --
class Dataset:
    """整个可视化数据层：扫描项目里的结果目录，统一对外提供各源数据。

    扫描顺序为"用户结果 -> 基准数据"，前者命中即用，因此用户跑完任意一步
    流程后重新 :meth:`reload` 就能切换成自己的结果。
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.bench_dir = self.root / "benchmarks"
        self._sources: dict[str, SourceData] = {}
        self._origin = "demo"
        self._network: int | None = None
        self.reload()

    # ---------------------------------------------------------- 目录扫描 --
    def _user_roots(self) -> list[Path]:
        """用户结果目录：项目根下的 output*/Results*，外加配置里声明的输出目录。"""
        roots: list[Path] = []
        try:
            children = sorted(self.root.iterdir())
        except OSError:
            children = []
        for child in children:
            low = child.name.lower()
            if child.is_dir() and (low.startswith("output") or low.startswith("result")):
                roots.append(child)
        for name in ("metcom_evaluation.yaml", "metcom_plot.yaml"):
            path = self.root / "configs" / name
            if not path.is_file():
                continue
            try:
                import yaml

                declared = (yaml.safe_load(path.read_text(encoding="utf-8")) or {})
                target = declared.get("output_dir")
                if target:
                    candidate = (path.parent / str(target)).resolve()
                    if candidate.is_dir() and candidate not in roots:
                        roots.append(candidate)
            except Exception:
                continue
        return roots

    @staticmethod
    def _series_files(base: Path, key: str, depth: int) -> list[Path]:
        pattern = f"ztd_*_sit_{_SERIES_TAG[key]}.txt"
        folders: list[Path] = []
        if base.name.lower().startswith("output") and \
                normalize_source(base.name[6:]) == key:
            folders.append(base)
        for folder in _iter_dirs(base, depth):
            name = folder.name.lower()
            if name.startswith("output") and normalize_source(folder.name[6:]) == key:
                folders.append(folder)
        hits: list[Path] = []
        for folder in folders:
            hits.extend(_grep(folder, pattern, 2))
        return sorted(set(hits))

    def _scan(self, base: Path, depth: int) -> dict[str, tuple[list[Path], list[Path]]]:
        """在某个根目录下按数据源搜集指标表与原始时序。"""
        out: dict[str, tuple[list[Path], list[Path]]] = {}
        if not base.is_dir():
            return out
        for key in SOURCE_ORDER:
            metrics = [p for p in _grep(base, f"{_METRIC_STEM[key]}_vs_*.csv", depth)
                       if "bystation" in p.name.lower() or "timeseries" in p.name.lower()]
            out[key] = (metrics, self._series_files(base, key, depth))
        return out

    def reload(self) -> None:
        """重新扫描全部数据源（用户结果优先）。"""
        found: dict[str, SourceData] = {}
        demo_scan: dict[str, tuple[list[Path], list[Path]]] = {}
        if self.bench_dir.is_dir():
            demo_scan = self._scan(self.bench_dir, 2)

        for base in self._user_roots():
            for key, (metrics, series) in self._scan(base, 2).items():
                if not (metrics or series):
                    continue
                existing = found.get(key)
                if existing is None:
                    found[key] = SourceData(key, "user", base, metrics, series)
                    continue
                if existing.origin != "user":
                    continue
                # 同一来源的结果可能分散在多个用户目录（如 output_ERA5 与
                # Results/output_results），此处按缺口互补，避免只认第一个目录
                if not existing.has_metrics() and metrics:
                    existing.metric_files = metrics
                if not existing.has_series() and series:
                    existing.series_files = series
        if demo_scan:
            for key, (metrics, series) in demo_scan.items():
                if key in found:
                    if not found[key].has_metrics() and metrics:
                        found[key].metric_files = metrics
                    if not found[key].has_series() and series:
                        found[key].series_files = series
                    continue
                if metrics or series:
                    found[key] = SourceData(key, "demo", self.bench_dir, metrics, series)

        self._sources = {k: found[k] for k in SOURCE_ORDER if k in found}
        # 补齐文件后统一重算时间窗（用户目录与演示数据可能互相借了文件）
        for source in self._sources.values():
            source.refresh_window()
        self._origin = ("user" if any(s.origin == "user" for s in self._sources.values())
                        else "demo")
        self._network = None

    # ------------------------------------------------------------ 对外 --
    def origin(self) -> str:
        return self._origin

    def origin_text(self) -> str:
        return "我的结果" if self._origin == "user" else "演示数据"

    def keys(self) -> tuple[str, ...]:
        return tuple(self._sources)

    def has(self, key: str) -> bool:
        std = normalize_source(key)
        return bool(std and std in self._sources)

    def get(self, key: str) -> SourceData | None:
        std = normalize_source(key)
        return self._sources.get(std) if std else None

    def label(self, key: str) -> str:
        return SOURCE_LABELS.get(normalize_source(key) or "", str(key))

    def metrics_sources(self) -> tuple[str, ...]:
        return tuple(k for k, s in self._sources.items() if s.has_metrics())

    def series_sources(self) -> tuple[str, ...]:
        return tuple(k for k, s in self._sources.items() if s.has_series())

    def sites(self) -> tuple[str, ...]:
        """站点总名单（取站点数最多的那个源）。"""
        best: tuple[str, ...] = ()
        for source in self._sources.values():
            names = source.sites()
            if len(names) > len(best):
                best = names
        return best

    def geo(self) -> pd.DataFrame:
        """站点坐标表（优先指标表，其次反演结果文件）。"""
        for source in self._sources.values():
            frame = source.geo()
            if not frame.empty:
                return frame
        return pd.DataFrame(columns=["site", "lat", "lon", "alt"])

    # ------------------------------------------------------------ 统计口径 --
    def network_size(self) -> int:
        """站网规模：各数据源实测有效站点数的最大值。"""
        if self._network is None:
            self._network = max((s.site_count() for s in self._sources.values()),
                                default=0)
        return self._network

    def stats(self, key: str) -> SourceStats | None:
        """某个数据源的实测统计口径（见模块文档「关于统计口径」）。"""
        source = self.get(key)
        return source.stats(self.network_size()) if source is not None else None

    def stats_all(self) -> dict[str, SourceStats]:
        """全部数据源的实测统计口径。"""
        network = self.network_size()
        return {key: source.stats(network) for key, source in self._sources.items()}

    def windows(self) -> dict[str, str]:
        return {k: s.window for k, s in self._sources.items()}

    def all_times(self, keys=None) -> np.ndarray:
        """给出若干数据源共有的时刻并集（已去重排序）。"""
        chunks = []
        for key in (keys or self.keys()):
            source = self.get(key)
            if source is None:
                continue
            stamps = source.times_index()
            if len(stamps):
                chunks.append(stamps)
        if not chunks:
            return np.array([], dtype="datetime64[ns]")
        return np.unique(np.concatenate(chunks))

    def time_bounds(self, keys=None):
        stamps = self.all_times(keys)
        if not len(stamps):
            return None
        return pd.Timestamp(stamps[0]), pd.Timestamp(stamps[-1])

    def common_window(self, keys=None):
        """找一个"覆盖数据源最多"的时间窗。

        五源的时间覆盖并不一致（例如 CMA-MESO 只有 2025 年 6 月），直接把
        全部画在一起会让横轴跨越大半年；这里挑出让尽可能多的源同框的区间，
        界面默认用它，用户仍可手动放宽。

        评分口径：``同框源数 - 3 × (窗口长度 / 全部数据跨度)``；加时长惩罚是
        为了让"包含全部源、却横跨一年半"的窗口输给"四源重合的那两天"。
        """
        spans = []
        for key in (keys or self.keys()):
            source = self.get(key)
            if source is None:
                continue
            stamps = source.times_index()
            if len(stamps):
                spans.append((pd.Timestamp(stamps[0]), pd.Timestamp(stamps[-1])))
        if not spans:
            return None
        low = min(s[0] for s in spans)
        high = max(s[1] for s in spans)
        total = max(1.0, (high - low).total_seconds())
        best_score: float | None = None
        best_span = (low, high)
        for start, _ in spans:
            for _, end in spans:
                if end < start:
                    continue
                inside = sum(1 for s in spans if s[0] >= start and s[1] <= end)
                score = inside - 3.0 * (end - start).total_seconds() / total
                if best_score is None or score > best_score:
                    best_score, best_span = score, (start, end)
        return best_span

    def overview(self) -> dict:
        """首页仪表盘用的汇总指标。"""
        summaries = [s.summary() for s in self._sources.values()]
        rmse = [s["ztd_rmse"] for s in summaries if s["ztd_rmse"] == s["ztd_rmse"]]
        return {
            "origin": self._origin,
            "origin_text": self.origin_text(),
            "sources": len(self._sources),
            "sites": len(self.sites()),
            "steps": max([s["steps"] for s in summaries], default=0),
            "ztd_rmse": min(rmse) if rmse else float("nan"),
            "summaries": summaries,
        }

    def summary_line(self) -> str:
        parts = [f"{self.label(k)} {s.summary()['sites']}站"
                 for k, s in self._sources.items()]
        return " · ".join(parts)


# ------------------------------------------------------------ 单例访问 --
_CACHE: dict[str, Dataset] = {}


def dataset(root: Path) -> Dataset:
    """按项目根目录缓存的数据集实例。"""
    key = str(Path(root).resolve())
    if key not in _CACHE:
        _CACHE[key] = Dataset(Path(root))
    return _CACHE[key]


def refresh(root: Path) -> Dataset:
    """业务任务结束后调用：重新扫描，返回最新数据集。"""
    item = dataset(root)
    item.reload()
    return item


__all__ = [
    "SOURCE_ORDER", "SOURCE_LABELS", "GAUSS_MAE", "SourceInfo", "SourceData",
    "SourceStats", "Dataset", "dataset", "refresh", "normalize_source",
    "source_label", "source_info", "BASIS_TEXT", "BASIS_OUTPUT", "BASIS_EVAL",
    "BASIS_SPEC",
]
