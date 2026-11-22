"""环境与设置：依赖自检、工程路径、界面偏好、图表引擎状态。

自检结果直接来自 :func:`metcom.gui.tasks.environment_report`，与运行时
实际 import 的模块完全一致——这里显示"已安装"，运行时就一定能导入。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMessageBox,
                               QWidget)

from metcom.gui import charts, data, icons, theme
from metcom.gui.components.cards import Card, KeyValueRow, section_label
from metcom.gui.components.inputs import FormRow, combo, hint, line_edit
from metcom.gui.pages.base import BasePage
from metcom.gui.state import MODULES
from metcom.gui.tasks import environment_report


class SettingsPage(BasePage):
    """环境自检与界面偏好。"""

    page_key = "settings"
    module_key = ""
    page_title = "环境与设置"
    page_subtitle = "依赖自检 · 工程路径 · 界面偏好 · 图表引擎"
    page_icon = "sliders"

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        self._rows: list[KeyValueRow] = []
        super().__init__(ctx, parent)

    # ------------------------------------------------------------ 构建 --
    def build(self) -> None:
        rescan = self.button("重新自检", "refresh", "ghost")
        rescan.clicked.connect(self._rescan)
        self.add_action(rescan)
        export = self.button("导出应用图标", "save", "ghost",
                             "把矢量徽标导出成 .ico（打包用）")
        export.clicked.connect(self._export_icon)
        self.add_action(export)

        left, right = self.columns(3, 2)
        left.addWidget(self._build_report_card())
        right.addWidget(self._build_preference_card())
        right.addWidget(self._build_engine_card())
        right.addStretch(1)
        self._rescan()

    def _build_report_card(self) -> QWidget:
        card = Card("环境自检", "启动时也会跑一遍，缺失项只警告不阻断", "check-circle")
        self._box = card.body()
        return card

    def _build_preference_card(self) -> QWidget:
        card = Card("界面偏好", "主题会立即生效并记住上次选择", "palette")
        self._theme_combo = combo(["深色（默认）", "浅色"], 0)
        self._theme_combo.setCurrentIndex(1 if theme.mode() == "light" else 0)
        self._theme_combo.currentIndexChanged.connect(self._on_theme)
        card.add_widget(FormRow("主题", self._theme_combo))
        self._source_combo = combo([data.source_label(k) for k in data.SOURCE_ORDER],
                                   data.source_label("era5"))
        card.add_widget(FormRow("默认数据源", self._source_combo))
        apply = self.button("写入 03 反演配置", "play", "ghost")
        apply.clicked.connect(self._apply_source)
        card.add_widget(apply)
        cache = _cache_dir()
        self._cache_edit = line_edit(str(cache) if cache else "（不可用）")
        self._cache_edit.setReadOnly(True)
        card.add_widget(FormRow("图形缓存", self._cache_edit,
                                "界面自绘素材（勾选框、下拉箭头）按主题缓存于此"))
        row = QHBoxLayout()
        row.setSpacing(8)
        open_button = self.button("打开目录", "folder", "ghost")
        open_button.clicked.connect(self._open_cache)
        row.addWidget(open_button)
        clear_button = self.button("清空缓存", "close", "danger")
        clear_button.clicked.connect(self._clear_cache)
        row.addWidget(clear_button)
        row.addStretch(1)
        card.add_layout(row)
        return card

    def _build_engine_card(self) -> QWidget:
        card = Card("图表引擎", "曲线与地图两套引擎共用同一套配色", "sparkles")
        self._legend = charts.LegendBar()
        self._legend.set_items([(data.source_label(key), icons.source_color(key))
                                for key in data.SOURCE_ORDER])
        card.add_widget(section_label("五源专属色"))
        card.add_widget(self._legend)
        card.add_widget(section_label("引擎状态"))
        for name, getter in (("pyqtgraph（曲线）", _pyqtgraph_state),
                             ("matplotlib + cartopy（地图）", _cartopy_state)):
            row = KeyValueRow(name, "—")
            value, state = getter()
            row.set_value(value)
            row.set_state(state)
            self._rows.append(row)
            card.add_widget(row)
        card.add_widget(hint("地图底图为本地 Natural Earth 50m 数据，离线可用。"))
        return card

    # ------------------------------------------------------------ 数据 --
    def _rescan(self) -> None:
        while self._box.count():
            item = self._box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        records = environment_report(Path(self.ctx.root), Path(self.ctx.root) / "configs")
        counts = {"ok": 0, "warn": 0, "bad": 0}
        for record in records:
            if "section" in record:
                self._box.addWidget(section_label(record["section"]))
                continue
            row = KeyValueRow(record["key"], record["value"],
                              mono=bool(record.get("mono")))
            row.set_state(record.get("state", "normal"))
            counts[record.get("state")] = counts.get(record.get("state"), 0) + 1
            self._box.addWidget(row)
        self._box.addStretch(1)
        state = "ok" if not counts.get("bad") else "bad"
        if counts.get("warn") and state == "ok":
            state = "warn"
        self.set_status(f"{counts.get('ok', 0)} 项正常 · {counts.get('warn', 0)} 项提示 · "
                        f"{counts.get('bad', 0)} 项缺失", state)
        self.ctx.log("环境自检完成")

    def _on_theme(self, index: int) -> None:
        target = "light" if index == 1 else "dark"
        if target == theme.mode():
            return
        theme.set_mode(target, QApplication.instance())
        window = self.window()
        if hasattr(window, "refresh_theme"):
            window.refresh_theme()
        else:
            self.refresh_theme()

    def _apply_source(self) -> None:
        keys = list(data.SOURCE_ORDER)
        key = keys[min(max(0, self._source_combo.currentIndex()), len(keys) - 1)]
        cfg = self.ctx.load_config("retrieve") or {}
        cfg["source"] = key
        path = self.ctx.store.save(self.ctx.config_name("retrieve"), cfg)
        self.ctx.log(f"默认数据源已设为 {data.source_label(key)}：{path}")
        self.set_status(f"默认数据源：{data.source_label(key)}", "accent")

    def _open_cache(self) -> None:
        cache = _cache_dir()
        if cache is None or not cache.exists():
            self.ctx.log("提示: 图形缓存目录尚未生成")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(cache)))

    def _clear_cache(self) -> None:
        cache = _cache_dir()
        if cache is None or not cache.exists():
            self.ctx.log("提示: 图形缓存目录尚未生成")
            return
        answer = QMessageBox.question(
            self, "清空图形缓存",
            f"将删除界面自绘素材缓存：\n{cache}\n\n删除后会自动重新生成。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = 0
        for item in cache.glob("*.png"):
            try:
                item.unlink()
                removed += 1
            except OSError:
                continue
        self.ctx.log(f"已清空图形缓存（{removed} 个文件），下次启动自动重建")
        self.set_status(f"已清空 {removed} 个缓存文件", "warn")

    def _export_icon(self) -> None:
        target = icons.export_app_icon()
        self.ctx.log(f"应用图标已导出：{target}")
        self.set_status("应用图标已导出", "ok")

    def refresh_theme(self) -> None:
        super().refresh_theme()
        if getattr(self, "_legend", None) is not None:
            self._legend.set_items([(data.source_label(key),
                                     icons.source_color(key))
                                    for key in data.SOURCE_ORDER])
        if getattr(self, "_rows", None):
            for row, getter in zip(self._rows, (_pyqtgraph_state, _cartopy_state)):
                value, state = getter()
                row.set_value(value)
                row.set_state(state)


def _cache_dir():
    getter = getattr(theme, "_cache_dir", None)
    return getter() if callable(getter) else None


def _pyqtgraph_state() -> tuple[str, str]:
    try:
        import pyqtgraph

        return f"已就绪  {getattr(pyqtgraph, '__version__', '')}".rstrip(), "ok"
    except Exception as exc:                       # pragma: no cover - 环境相关
        return f"不可用（{type(exc).__name__}）", "bad"


def _cartopy_state() -> tuple[str, str]:
    from metcom.gui import charts_map

    if charts_map.available():
        return "已就绪（本地底图已缓存）", "ok"
    return charts_map.unavailable_reason() or "不可用", "warn"


__all__ = ["SettingsPage"]
