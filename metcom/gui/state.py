"""GUI 运行时状态：模块表、配置档案、工作流进度、运行历史。

这一层是界面与业务之间的"共享黑板"：页面从这里拿配置、往这里写状态；
首页与侧边栏监听信号刷新展示，互相不直接耦合。
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml
from PySide6.QtCore import QObject, Signal

from metcom.gui.tasks import build_task, validate
from metcom.gui.workers import TaskController


# --------------------------------------------------------------- 模块表 --
@dataclass(frozen=True)
class ModuleInfo:
    """一个业务模块的静态信息（界面上到处要用）。"""

    key: str
    badge: str
    name: str
    icon: str
    config: str
    desc: str


MODULES: dict[str, ModuleInfo] = {
    "gridgen": ModuleInfo(
        "gridgen", "01", "格点生成", "grid", "metcom_gridgen.yaml",
        "由 DEM 与行政区划生成格点清单，并把正高换算为大地高"),
    "download": ModuleInfo(
        "download", "02", "数据下载", "download", "metcom_downloader.yaml",
        "按时间范围从 CDS 拉取 ERA5 再分析资料，支持 IDM 多线程加速"),
    "retrieve": ModuleInfo(
        "retrieve", "03", "核心反演", "pulse", "metcom_era5.yaml",
        "逐时次、逐站点反演 ZTD / ZHD / ZWD / PWV —— 平台核心算法"),
    "timeinterp": ModuleInfo(
        "timeinterp", "04", "时间插值", "clock", "metcom_timeinterp.yaml",
        "把粗时间分辨率的反演结果插值到目标时间分辨率"),
    "evaluation": ModuleInfo(
        "evaluation", "05", "精度评估", "chart", "metcom_evaluation.yaml",
        "与参考数据逐时/逐站对比，给出 Bias 与 RMSE"),
    "plot": ModuleInfo(
        "plot", "05", "可视化绘图", "map", "metcom_plot.yaml",
        "RMSE 时间序列图与站点空间热力图"),
}

WORKFLOW = ("gridgen", "download", "retrieve", "timeinterp", "evaluation")

STEP_TITLES = ["格点生成", "数据下载", "核心反演", "时间插值", "精度评估"]

NAV_ITEMS: list[tuple] = [
    (None, "工作区"),
    ("home", "工作台", "home"),
    (None, "反演流水线"),
    ("gridgen", "01 · 格点生成", "grid"),
    ("download", "02 · 数据下载", "download"),
    ("retrieve", "03 · 核心反演", "pulse"),
    ("timeinterp", "04 · 时间插值", "clock"),
    ("evaluation", "05 · 精度评估", "chart"),
    ("visualize", "05 · 可视化绘图", "map"),
    (None, "系统"),
    ("settings", "环境与设置", "sliders"),
]


def summarize(result: Any) -> str:
    """把业务函数返回值整理成一行中文摘要。"""
    if result is None:
        return "完成"
    if isinstance(result, dict):
        if not result:
            return "完成（无统计信息）"
        parts = []
        for key, value in result.items():
            if isinstance(value, float):
                value = f"{value:.2f}"
            elif isinstance(value, (list, tuple)):
                value = f"{len(value)} 项"
            parts.append(f"{key}={value}")
        return " · ".join(parts)
    if isinstance(result, (list, tuple)):
        return f"完成，输出 {len(result)} 项"
    return str(result)


# ------------------------------------------------------------- 配置档案 --
class ConfigStore:
    """``configs/metcom_*.yaml`` 的读写与缓存。

    界面全程直接操作 dict（业务函数本来就收 dict），只有点"保存配置"时才落盘；
    注意：落盘会重写 YAML，文件里的中文注释不保留（值不变）。
    """

    def __init__(self, configs_dir: Path) -> None:
        self.dir = Path(configs_dir)
        self._cache: dict[str, dict] = {}

    def path(self, name: str) -> Path:
        return self.dir / name

    def exists(self, name: str) -> bool:
        return self.path(name).is_file()

    def load(self, name: str, force: bool = False) -> dict:
        if force or name not in self._cache:
            path = self.path(name)
            data: dict = {}
            if path.is_file():
                with open(path, "r", encoding="utf-8") as handle:
                    loaded = yaml.safe_load(handle)
                if isinstance(loaded, dict):
                    data = loaded
            self._cache[name] = data
        return copy.deepcopy(self._cache[name])

    def save(self, name: str, cfg: dict) -> Path:
        path = self.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(cfg, handle, allow_unicode=True, sort_keys=False,
                           default_flow_style=False)
        self._cache[name] = copy.deepcopy(cfg)
        return path

    def names(self) -> list[str]:
        return sorted(self._cache)


# ------------------------------------------------------------- 运行状态 --
class AppState(QObject):
    """工作流进度 + 运行历史（首页/侧栏据此刷新）。"""

    workflow_changed = Signal()
    history_changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._steps: dict[str, str] = {key: "idle" for key in WORKFLOW}
        self._dirty: set[str] = set()
        self.history: list[dict] = []
        self.configs: dict[str, dict] = {}

    # -- 工作流 --
    def steps(self) -> dict[str, str]:
        return dict(self._steps)

    def step(self, key: str) -> str:
        return self._steps.get(key, "idle")

    def set_step(self, key: str, value: str) -> None:
        if self._steps.get(key) != value:
            self._steps[key] = value
            self.workflow_changed.emit()

    def reset_steps(self) -> None:
        for key in WORKFLOW:
            self._steps[key] = "idle"
        self.workflow_changed.emit()

    def states_for_stepper(self) -> list[str]:
        """映射到步骤条的状态：idle / active / done。"""
        mapping = {"idle": "idle", "running": "active", "done": "done",
                   "failed": "idle", "skipped": "idle"}
        return [mapping.get(self._steps.get(k, "idle"), "idle") for k in WORKFLOW]

    def done_count(self) -> int:
        return sum(1 for k in WORKFLOW if self._steps.get(k) == "done")

    # -- 配置快照 --
    def note_config(self, key: str, cfg: dict) -> None:
        self.configs[key] = copy.deepcopy(cfg)
        self._dirty.add(key)

    def config(self, key: str) -> dict | None:
        return self.configs.get(key)

    # -- 历史 --
    def mark_run(self, key: str, ok: bool, summary: str, seconds: float) -> None:
        info = MODULES.get(key)
        self.history.insert(0, {
            "time": time.strftime("%H:%M:%S"),
            "module": key,
            "name": info.name if info else key,
            "badge": info.badge if info else "",
            "ok": ok,
            "summary": summary,
            "seconds": seconds,
        })
        del self.history[40:]
        self.history_changed.emit()


# ------------------------------------------------------------- 上下文 --
@dataclass
class AppContext:
    """页面共享的运行上下文。"""

    root: Path
    store: ConfigStore
    controller: TaskController
    state: AppState
    pages: dict = field(default_factory=dict)

    # -- 便捷 --
    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def log(self, text: str) -> None:
        self.controller.emit_log(text)

    def config_name(self, key: str) -> str:
        return MODULES[key].config

    def load_config(self, key: str, name: str | None = None) -> dict:
        return self.store.load(name or self.config_name(key))

    # -- 运行 --
    def run(self, key: str, title: str, fn: Callable, *args,
            on_done: Callable[[Any], None] | None = None,
            on_fail: Callable[[str, str], None] | None = None,
            mark_workflow: bool = True, **kwargs) -> bool:
        """提交一个任务，并在结束时更新工作流状态与历史记录。"""
        if self.controller.is_busy():
            self.controller.emit_log(
                f"警告: 任务「{self.controller.current_name()}」正在运行，"
                "请等待完成或先取消")
            return False

        if key in self.state._steps and mark_workflow:
            self.state.set_step(key, "running")
        started = time.time()

        def _done(result: Any) -> None:
            if mark_workflow:
                self.state.set_step(key, "done")
            self.state.mark_run(key, True, summarize(result), time.time() - started)
            if on_done is not None:
                on_done(result)

        def _fail(message: str, detail: str) -> None:
            if mark_workflow:
                self.state.set_step(key, "failed")
            self.state.mark_run(key, False, message, time.time() - started)
            if on_fail is not None:
                on_fail(message, detail)

        self.controller.emit_log(f"=== {title} ===")
        return self.controller.submit(title, fn, *args, on_done=_done, on_fail=_fail,
                                      **kwargs)

    def run_module(self, key: str, cfg: dict, title: str | None = None,
                   on_done: Callable[[Any], None] | None = None,
                   on_fail: Callable[[str, str], None] | None = None,
                   strict: bool = False) -> bool:
        """按模块 key 运行：先校验配置，再构造任务。"""
        info = MODULES[key]
        label = title or f"{info.badge} {info.name}"

        problems = validate(key, cfg)
        for problem in problems:
            self.controller.emit_log(problem)
        errors = [p for p in problems if p.startswith("错误:")]
        if errors and strict:
            self.controller.emit_log("运行已中止：请先修正上述错误")
            return False

        try:
            fn, args = build_task(key, cfg)
        except Exception as exc:
            self.controller.emit_log(f"错误: 无法构造任务（{exc}）")
            return False

        self.state.note_config(key, cfg)
        return self.run(key, label, fn, *args, on_done=on_done, on_fail=on_fail)
