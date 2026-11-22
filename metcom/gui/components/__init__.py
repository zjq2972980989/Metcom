"""可复用 UI 控件库。

  cards.py    卡片、统计块、英雄区、键值行
  inputs.py   表单行、路径选择器、标准输入控件工厂
  console.py  带等级着色的日志控制台
  stepper.py  自绘五步工作流进度条
  sidebar.py  侧边导航
"""

from __future__ import annotations

from metcom.gui.components.cards import (Card, HeroBanner, KeyValueRow,
                                        MetricCard, chip, section_label,
                                        separator)
from metcom.gui.components.console import LogConsole, classify
from metcom.gui.components.inputs import (FormRow, PathField, checkbox, combo,
                                         dspin, line_edit, spin)
from metcom.gui.components.sidebar import Sidebar
from metcom.gui.components.stepper import WorkflowStepper

__all__ = [
    "Card", "MetricCard", "HeroBanner", "KeyValueRow", "chip", "section_label",
    "separator", "LogConsole", "classify", "FormRow", "PathField", "line_edit",
    "combo", "spin", "dspin", "checkbox", "Sidebar", "WorkflowStepper",
]
