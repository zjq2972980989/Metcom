"""工作台首页：KPI、五步流水线、数据来源、站点分布与近期运行。

这一页只做"进门第一眼"：所有数字都来自 :mod:`metcom.gui.data` 与
:class:`metcom.gui.state.AppState`，不额外读盘，切回来时秒开。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget)

from metcom.gui import charts, charts_map, data, icons, theme
from metcom.gui.components.cards import (Card, KeyValueRow, MetricCard,
                                         section_label)
from metcom.gui.components.stepper import WorkflowStepper
from metcom.gui.pages.base import BasePage
from metcom.gui.state import STEP_TITLES, WORKFLOW

PIPELINE = ("gridgen", "download", "retrieve", "timeinterp", "evaluation")


class HomePage(BasePage):
    """五步流水线总览。"""

    page_key = "home"
    module_key = ""
    page_title = "工作台"
    page_subtitle = "五步流水线总览 · 数据来源 · 站点分布 · 近期运行记录"
    page_icon = "home"

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        self._ds = None
        self._queue: list[str] = []
        super().__init__(ctx, parent)

    # ------------------------------------------------------------ 构建 --
    def build(self) -> None:
        self._ds = data.dataset(self.ctx.root)

        run = self.run_button("一键跑完五步")
        run.clicked.connect(self._run_pipeline)
        self.add_action(run)
        reset = self.button("重置进度", "refresh", "ghost", "把五步状态清回未运行")
        reset.clicked.connect(self._reset)
        self.add_action(reset)

        self.body().addLayout(self._build_kpis())
        self.body().addLayout(self._build_middle())
        self.body().addLayout(self._build_bottom())

        self.ctx.state.workflow_changed.connect(self._refresh)
        self.ctx.state.history_changed.connect(self._refresh)
        self._refresh()

    def _build_kpis(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(14)
        self._cards = {
            "sites": MetricCard("测站数量", "--", "站", "grid"),
            "sources": MetricCard("数据源", "--", "个", "layers"),
            "rmse": MetricCard("最近 ZTD RMSE", "--", "mm", "target"),
            "steps": MetricCard("已完成步骤", "0/5", "", "check-circle"),
        }
        for card in self._cards.values():
            row.addWidget(card, 1)
        return row

    def _build_middle(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(14)

        card = Card("五步反演流水线", "按 01 → 05 顺序执行；任一步失败会停在该步",
                    "pulse")
        self._ring_holder = QHBoxLayout()
        self._ring = charts.ProgressRing()
        self._ring_holder.addWidget(self._ring, 0, Qt.AlignmentFlag.AlignLeft)
        info = QVBoxLayout()
        info.setSpacing(6)
        self._step_label = QLabel("")
        self._step_label.setObjectName("CardDesc")
        self._step_label.setWordWrap(True)
        info.addWidget(self._step_label)
        self._step_rows: list[KeyValueRow] = []
        for index, (key, title) in enumerate(zip(WORKFLOW, STEP_TITLES)):
            entry = KeyValueRow(f"{index + 1:02d} · {title}", "未运行")
            self._step_rows.append(entry)
            info.addWidget(entry)
        info.addStretch(1)
        self._ring_holder.addLayout(info, 1)
        card.add_layout(self._ring_holder)
        self._stepper = WorkflowStepper(STEP_TITLES)
        card.add_widget(self._stepper)
        row.addWidget(card, 3)

        origin = Card("数据来源", "启动时自动判定：优先使用自己的反演结果", "database")
        self._origin_chip = QLabel(self._ds.origin_text())
        self._origin_chip.setObjectName("OriginChip")
        origin_row = QHBoxLayout()
        origin_row.addWidget(self._origin_chip)
        origin_row.addStretch(1)
        origin.add_layout(origin_row)
        self._source_rows: list[KeyValueRow] = []
        for key in data.SOURCE_ORDER:
            entry = KeyValueRow(data.source_label(key), "—")
            self._source_rows.append(entry)
            origin.add_widget(entry)
        origin.add_widget(section_label("扫描目录"))
        self._path_label = QLabel("")
        self._path_label.setObjectName("Hint")
        self._path_label.setWordWrap(True)
        origin.add_widget(self._path_label)
        row.addWidget(origin, 2)
        return row

    def _build_bottom(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(14)

        card = Card("站点空间分布", "全国 1443 个测站，按测站高程着色", "map")
        if charts_map.available():
            self._map = charts_map.MapCanvas(compact=True)
            self._map.setMinimumHeight(280)
            card.add_widget(self._map, 1)
        else:
            self._map = None
            card.add_widget(QLabel(charts_map.unavailable_reason() or "地图不可用"))
        row.addWidget(card, 3)

        history = Card("近期运行记录", "最近 6 条，含耗时与结果摘要", "list")
        self._history_box = QVBoxLayout()
        self._history_box.setSpacing(6)
        history.add_layout(self._history_box)
        row.addWidget(history, 2)
        return row

    # ------------------------------------------------------------ 刷新 --
    def _refresh(self) -> None:
        overview = self._ds.overview()
        self._cards["sites"].set_value(str(overview.get("sites", "--")), "站")
        self._cards["sources"].set_value(str(overview.get("sources", "--")), "个")
        self._cards["rmse"].set_value(_fmt(overview.get("ztd_rmse")), "mm")
        done = self.ctx.state.done_count()
        self._cards["steps"].set_value(f"{done}/5", "")

        states = self.ctx.state.states_for_stepper()
        self._stepper.set_states(states)
        self._ring.set_value(done, 5, "流水线完成度")
        labels = {"done": "已完成", "running": "运行中", "failed": "失败",
                  "skipped": "已跳过", "idle": "未运行"}
        for index, key in enumerate(WORKFLOW):
            state = self.ctx.state.step(key)
            self._step_rows[index].set_value(labels.get(state, state))
            self._step_rows[index].set_state(
                {"done": "ok", "running": "warn", "failed": "bad"}.get(state, "none"))
        self._step_label.setText(
            f"已完成 {done} / 5 步；"
            + ("全部完成，可进入可视化工作台查看结果"
               if done == 5 else "尚未完成的步骤按顺序继续即可"))

        self._origin_chip.setText(self._ds.origin_text())
        self._origin_chip.setProperty("demo",
                                      "true" if self._ds.origin() == "demo" else "false")
        self._origin_chip.style().unpolish(self._origin_chip)
        self._origin_chip.style().polish(self._origin_chip)
        self.set_status(self._ds.origin_text(),
                        "warn" if self._ds.origin() == "demo" else "ok")
        for entry, key in zip(self._source_rows, data.SOURCE_ORDER):
            source = self._ds.get(key)
            if source is None:
                entry.set_value("未找到结果")
                entry.set_state("bad")
                continue
            summary = source.summary()
            entry.set_value(f"{summary.get('steps', 0)} 时次 · "
                            f"ZTD RMSE {_fmt(summary.get('ztd_rmse'))} mm")
            entry.set_state("ok")
        self._path_label.setText(str(self._ds.get(data.SOURCE_ORDER[0]).root)
                                 if self._ds.get(data.SOURCE_ORDER[0]) else "—")
        self._refresh_history()

    def _refresh_history(self) -> None:
        while self._history_box.count():
            item = self._history_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        records = self.ctx.state.history[:6]
        if not records:
            empty = QLabel("还没有运行记录")
            empty.setObjectName("Hint")
            self._history_box.addWidget(empty)
            return
        for record in records:
            badge = "成功" if record["ok"] else "失败"
            row = KeyValueRow(f"{record['time']} · {record['badge']} "
                              f"{record['name']}",
                              f"{badge} {record['seconds']:.1f}s")
            row.set_state("ok" if record["ok"] else "bad")
            row.setToolTip(record["summary"])
            self._history_box.addWidget(row)
        self._history_box.addStretch(1)

    # ------------------------------------------------------------ 动作 --
    def _reset(self) -> None:
        self.ctx.state.reset_steps()
        self.ctx.log("五步进度已重置")

    def _run_pipeline(self) -> None:
        if self.ctx.controller.is_busy():
            self.ctx.log("警告: 已有任务在运行，请等待完成或先取消")
            return
        self._queue = list(PIPELINE)
        self.ctx.log("一键流水线开始：01 → 05")
        self._advance()

    def _advance(self) -> None:
        if not self._queue:
            self._toast("五步流水线全部完成")
            return
        key = self._queue.pop(0)
        cfg = self.ctx.state.config(key) or self.ctx.load_config(key)
        started = self.ctx.run_module(
            key, cfg, on_done=lambda _result: QTimer.singleShot(280, self._advance),
            on_fail=lambda message, _detail: self._abort(key, message))
        if not started:
            self._queue = []

    def _abort(self, key: str, message: str) -> None:
        self._queue = []
        self._toast(f"流水线在「{key}」中断：{message}")

    def _toast(self, text: str) -> None:
        self.ctx.log(text)
        self.set_status(text, "warn")

    # ------------------------------------------------------------ 主题 --
    def on_show(self) -> None:
        self._refresh()

    def refresh_theme(self) -> None:
        super().refresh_theme()
        if getattr(self, "_map", None) is not None:
            self._map.refresh_theme()
        if getattr(self, "_ring", None) is not None:
            self._ring.refresh_theme()
        if getattr(self, "_ds", None) is not None:
            self._refresh()


def _fmt(value) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "--"


__all__ = ["HomePage"]
